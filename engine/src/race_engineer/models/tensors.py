"""Model inputs built from the processed corner segments.

Each segment becomes:
- `signal` (5 x 80): speed, throttle, brake, gear and line offset, each expressed relative to
  the field at that corner in that session (pointwise z-scores). The model learns how laps
  *deviate* at a corner, not what each track looks like, which helps on unseen circuits.
- `context_channels` (2 x 80): always visible, never masked: the field's mean speed profile at
  the corner and the track curvature, so the model knows what kind of corner it is looking at.
- `context_cat` / `context_num`: session type, tyre compound, tyre age, lap progress, traffic
  and track temperature. Driver and team are deliberately not inputs.

Building the arrays reads every processed session, so `load_or_build_tensors` caches them under
data/features/tensors/, keyed by the exact set of segments and the processed files they come
from: a dataset that gained (or lost) a session, or a session reprocessed after a pipeline fix,
never reuses stale arrays. `export_bundle` writes the arrays plus the metadata needed
for splits, labels and probes to one directory, so a cloud GPU job can train and evaluate
without the processed Parquet tree.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from race_engineer.config import PROCESSED_DIR, QUALI_CODES
from race_engineer.data.store import fixed_list_to_numpy, session_key
from race_engineer.evaluation.data import FEATURES_DIR

log = logging.getLogger(__name__)

SIGNAL = ("speed", "throttle", "brake", "gear", "offset_m")
FLOOR = {"speed": 2.0, "throttle": 5.0, "brake": 0.1, "gear": 0.3, "offset_m": 0.3}
COMPOUNDS = ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET")  # anything else -> "other"
N_COMPOUNDS = len(COMPOUNDS) + 1
N_NUMERIC = 4

# Bump whenever build_tensors changes what it produces, so cached arrays are rebuilt.
TENSOR_VERSION = 1
TENSOR_CACHE_DIR = FEATURES_DIR / "tensors"
CACHE_KEEP = 2  # cached datasets kept on disk (each about 1.2 kB per segment)
ARRAYS = ("signal", "context_channels", "context_cat", "context_num")
# What training, splits, labels and the embedding probes need; bundles carry only these.
BUNDLE_COLUMNS = (
    "segment_id", "lap_id", "year", "round", "session", "event", "location", "driver", "team",
    "lap_number", "corner", "corner_letter", "lap_class", "label",
)  # fmt: skip


@dataclass
class TelemetryData:
    frame: pd.DataFrame  # one row per segment (metadata, labels), aligned with the arrays
    signal: np.ndarray  # (N, 5, 80) float16
    context_channels: np.ndarray  # (N, 2, 80) float16
    context_cat: np.ndarray  # (N, 2) int64: session type (0 quali, 1 race), compound
    context_num: np.ndarray  # (N, 4) float32: tyre age, lap progress, traffic, track temp

    def __len__(self) -> int:
        return len(self.frame)

    def subset(self, rows: np.ndarray) -> TelemetryData:
        """Some rows, already normalised against their whole session (unlike rebuilding)."""
        return TelemetryData(
            frame=self.frame.iloc[rows].reset_index(drop=True),
            signal=self.signal[rows],
            context_channels=self.context_channels[rows],
            context_cat=self.context_cat[rows],
            context_num=self.context_num[rows],
        )


def normalise_per_corner(
    x: np.ndarray, corner: np.ndarray, floor: float
) -> tuple[np.ndarray, np.ndarray]:
    """Pointwise z-scores within each corner, plus each row's corner mean profile."""
    z = np.empty_like(x, dtype=np.float32)
    mean_profile = np.empty_like(x, dtype=np.float32)
    for c in np.unique(corner):
        rows = corner == c
        mean = x[rows].mean(axis=0)
        std = np.maximum(x[rows].std(axis=0), floor)
        z[rows] = (x[rows] - mean) / std
        mean_profile[rows] = mean
    return z, mean_profile


def context_features(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    session_type = (~frame["session"].isin(list(QUALI_CODES))).astype(np.int64).to_numpy()
    compound = (
        frame["compound"]
        .map({c: i for i, c in enumerate(COMPOUNDS)})
        .fillna(len(COMPOUNDS))
        .astype(np.int64)
    ).to_numpy()
    laps_in_session = frame.groupby(["year", "round", "session"])["lap_number"].transform("max")
    numeric = np.column_stack(
        [
            np.log1p(frame["tyre_life"].fillna(0).clip(lower=0)) / 3,
            frame["lap_number"] / laps_in_session.clip(lower=1),
            frame["gap_ahead_s"].fillna(5).clip(0, 5) / 5,  # no car ahead = open track
            ((frame["track_temp"] - 35) / 10).fillna(0),
        ]
    ).astype(np.float32)
    return np.column_stack([session_type, compound]), numeric


def corner_key(number: object, letter: object) -> str:
    """A corner's id, like 7 or 7A. Numbers can arrive as floats (DuckDB widens the column
    when one session's file is empty), and a missing letter as None or NaN."""
    return f"{int(cast(float, number))}{letter if isinstance(letter, str) else ''}"


def build_tensors(frame: pd.DataFrame) -> TelemetryData:
    """Arrays for every row of `frame` (the evaluation frame), session by session.

    Signals are normalised against the rows passed in, so pass every eligible segment of a
    session (as the evaluation frame does), not a handful: with two laps, every z-score is +/-1.
    """
    frame = frame.reset_index(drop=True)
    n = len(frame)
    signal = np.zeros((n, len(SIGNAL), 80), np.float16)
    context = np.zeros((n, 2, 80), np.float16)
    groups = frame.groupby(["year", "round", "session"], sort=True).indices
    for group, rows in groups.items():
        year, round_, code = cast(tuple, group)
        key = session_key(int(year), int(round_), str(code))
        table = pq.read_table(
            PROCESSED_DIR / "segments" / f"{key}.parquet", columns=["segment_id", *SIGNAL]
        )
        position = pd.Series(
            np.arange(table.num_rows), index=table.column("segment_id").to_pylist()
        )
        part = frame.iloc[rows]
        take = position.loc[part["segment_id"]].to_numpy()
        corner = np.array(
            [corner_key(n, x) for n, x in zip(part["corner"], part["corner_letter"], strict=True)]
        )
        for i, channel in enumerate(SIGNAL):
            z, mean = normalise_per_corner(
                fixed_list_to_numpy(table.column(channel))[take], corner, FLOOR[channel]
            )
            signal[rows, i] = z
            if channel == "speed":
                context[rows, 0] = (mean - 200) / 60  # the corner's typical speed profile
        corners = pd.read_parquet(PROCESSED_DIR / "corners" / f"{key}.parquet")
        curvature = {
            corner_key(n, x): np.asarray(c)
            for n, x, c in zip(
                corners["number"], corners["letter"], corners["curvature"], strict=True
            )
        }
        context[rows, 1] = np.stack([curvature[c] for c in corner]) * 50
    cat, num = context_features(frame)
    return TelemetryData(
        frame=frame, signal=signal, context_channels=context, context_cat=cat, context_num=num
    )


def cache_key(frame: pd.DataFrame, sources: bool = False) -> str:
    """Stable hash of the set of segments (not their order) and TENSOR_VERSION.

    With `sources`, also the size and modification time of the processed files build_tensors
    reads: reprocessing a session keeps its segment ids, so the ids alone would keep serving
    the old arrays. Bundles leave the files out (a cloud machine has none to compare).
    """
    digest = hashlib.sha256(f"tensors-v{TENSOR_VERSION}\n".encode())
    digest.update("\n".join(sorted(frame["segment_id"].astype(str))).encode())
    if sources:
        digest.update(source_stamp(frame).encode())
    return digest.hexdigest()[:24]


def source_stamp(frame: pd.DataFrame) -> str:
    """Size and modification time of each session's processed segments and corners files."""
    lines = []
    columns = ["year", "round", "session"]
    sessions = frame[columns].drop_duplicates().sort_values(columns)
    for year, round_, code in sessions.itertuples(index=False):
        key = session_key(int(year), int(round_), str(code))
        for folder in ("segments", "corners"):
            path = PROCESSED_DIR / folder / f"{key}.parquet"
            try:
                stat = path.stat()
            except FileNotFoundError:
                lines.append(f"{folder}/{key} missing")
                continue
            lines.append(f"{folder}/{key} {stat.st_size} {stat.st_mtime_ns}")
    return "\n".join(lines)


def load_or_build_tensors(
    frame: pd.DataFrame, cache_dir: Path = TENSOR_CACHE_DIR, refresh: bool = False
) -> TelemetryData:
    """build_tensors(frame), from the cache when this exact set of segments was built before
    from the same processed files.

    The returned frame is the one passed in (fresh labels and metadata); cached arrays are
    reordered to match its rows. `refresh` rebuilds regardless.
    """
    frame = frame.reset_index(drop=True)
    key = cache_key(frame, sources=True)
    arrays_path, frame_path = cache_dir / f"{key}.npz", cache_dir / f"{key}.parquet"
    if not refresh and arrays_path.exists() and frame_path.exists():
        cached = pd.read_parquet(frame_path, columns=["segment_id"])["segment_id"]
        take = pd.Series(np.arange(len(cached)), index=cached.to_numpy())
        take = take.loc[frame["segment_id"].to_numpy()].to_numpy()
        same_order = bool(np.array_equal(take, np.arange(len(take))))
        with np.load(arrays_path) as stored:
            arrays = {n: stored[n] if same_order else stored[n][take] for n in ARRAYS}
        arrays_path.touch()  # recently used: pruned last
        log.info("tensors for %d segments loaded from %s", len(frame), arrays_path.name)
        return TelemetryData(frame=frame, **arrays)

    data = build_tensors(frame)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _write_parquet(data.frame, frame_path)
    _write_arrays(data, arrays_path)  # written last: a cache entry counts once this exists
    _prune(cache_dir, keep=CACHE_KEEP)
    log.info("tensors for %d segments built and cached as %s", len(frame), arrays_path.name)
    return data


def export_bundle(path: Path, data: TelemetryData | None = None, refresh: bool = False) -> Path:
    """Write a self-contained dataset directory: tensors.npz, frame.parquet, manifest.json.

    With no `data`, bundles the current evaluation dataset (computing features for any newly
    processed sessions first). Copy or tar the directory to a cloud machine and pass it to
    `race-engineer-train --bundle` or `race-engineer-experiments --bundle`. The manifest is
    removed first and written last, so an export that stops partway can't pass as complete.
    """
    if data is None:
        from race_engineer.evaluation.data import build_features, load_eval_frame

        build_features()
        data = load_or_build_tensors(load_eval_frame(), refresh=refresh)
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").unlink(missing_ok=True)
    columns = [c for c in BUNDLE_COLUMNS if c in data.frame.columns]
    _write_parquet(data.frame[columns], path / "frame.parquet")
    _write_arrays(data, path / "tensors.npz")
    manifest = {
        "tensor_version": TENSOR_VERSION,
        "cache_key": cache_key(data.frame),
        "segments": len(data),
        "sessions": int(data.frame.groupby(["year", "round", "session"]).ngroups),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def load_bundle(path: Path) -> TelemetryData:
    """An export_bundle() directory, once its files are known to belong together."""
    if not (path / "manifest.json").exists():
        raise ValueError(f"{path} has no manifest.json (an unfinished export?): export it again")
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest["tensor_version"] != TENSOR_VERSION:
        raise ValueError(
            f"{path} holds tensors v{manifest['tensor_version']}, this code builds "
            f"v{TENSOR_VERSION}: export the bundle again"
        )
    frame = pd.read_parquet(path / "frame.parquet")
    with np.load(path / "tensors.npz") as stored:
        arrays = {n: stored[n] for n in ARRAYS}
    # A frame from one export beside arrays from another would pair labels with the wrong rows.
    lengths = {len(a) for a in arrays.values()}
    if lengths != {len(frame)} or manifest["segments"] != len(frame):
        raise ValueError(
            f"{path} is inconsistent ({len(frame)} frame rows, arrays of {sorted(lengths)}, "
            f"manifest {manifest['segments']}): export the bundle again"
        )
    if cache_key(frame) != manifest["cache_key"]:
        raise ValueError(f"{path}: frame.parquet isn't the one exported: export the bundle again")
    return TelemetryData(frame=frame, **arrays)


def _write_arrays(data: TelemetryData, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:  # uncompressed: z-scores barely compress, and loading is faster
        np.savez(f, **{name: getattr(data, name) for name in ARRAYS})
    tmp.replace(path)


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


def _prune(cache_dir: Path, keep: int) -> None:
    """Keep the newest `keep` cached datasets; every new session makes a new one."""
    entries = sorted(cache_dir.glob("*.npz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in entries[keep:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".parquet").unlink(missing_ok=True)
