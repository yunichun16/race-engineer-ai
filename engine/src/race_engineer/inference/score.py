"""M4 batch scoring: a mistake score and a style embedding for every eligible corner segment.

Two detectors came out of M3 with different strengths. The Telemetry Transformer's
reconstruction error after the apex (`mae_error_exit`) puts the most real mistakes at the top
of its list; Isolation Forest on the hand-crafted features ranks best overall. Here each is
turned into a percentile within its session (so a score means the same in a wet race and a dry
qualifying), and the way to combine them is chosen by average precision on the validation
events only (2025 under the temporal split), then reported on the test events (2026).

    uv run race-engineer-infer score [--checkpoint models/selected/mae.pt]

Features are computed first for any session processed (or reprocessed) since they last were,
so a new race is scored with the rest. Writes data/results/segment_scores.parquet (one row per
segment), data/results/embeddings.npz (the [CLS] embedding of every segment),
report/m4_scoring.md and, last, data/results/manifest.json. The manifest names the run
(inference/provenance.py): the later steps check they are built from it, and a run that stops
partway leaves none, so its files can't pass for a complete run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

from race_engineer.config import REPORT_DIR, RESULTS_DIR, repo_root
from race_engineer.data.qa import markdown_table
from race_engineer.evaluation.data import build_features, load_eval_frame
from race_engineer.evaluation.deep import (
    checkpoint_split,
    load_checkpoint,
    safe_evaluate,
    score_and_embed,
)
from race_engineer.inference.provenance import (
    MANIFEST,
    StaleResultsError,
    check_current,
    new_run_id,
    write_parquet,
)
from race_engineer.models.baselines import IsoForest
from race_engineer.models.tensors import TelemetryData, cache_key, load_or_build_tensors
from race_engineer.models.train import event_keys, pick_device

log = logging.getLogger(__name__)

SELECTED_CHECKPOINT = repo_root() / "models" / "selected" / "mae.pt"
TRANSFORMER_SCORE = "mae_error_exit"  # chosen on validation in M3
SESSION = ["year", "round", "session"]
# A segment is flagged when its combined score is in the top FLAG_SHARE of its session.
FLAG_SHARE = 0.01

SCORE_COLUMNS = ("transformer", "isolation_forest")
FRAME_COLUMNS = (
    "segment_id", "lap_id", "year", "round", "session", "event", "location", "driver",
    "driver_number", "team", "lap_number", "corner", "corner_letter", "lap_class", "compound",
    "tyre_life", "deleted", "label",
)  # fmt: skip


def session_percentile(frame: pd.DataFrame, score: np.ndarray) -> np.ndarray:
    """Each score's percentile within its session, in (0, 1]; higher = more unusual."""
    ranks = pd.Series(score, index=frame.index).groupby([frame[c] for c in SESSION]).rank(pct=True)
    return ranks.to_numpy()


def combinations(percentiles: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The candidate mistake scores: each detector alone, and two ways of combining them."""
    t, f = percentiles["transformer"], percentiles["isolation_forest"]
    return {
        "transformer": t,
        "isolation_forest": f,
        "mean": (t + f) / 2,  # both have to find it unusual
        "max": np.maximum(t, f),  # either one is enough
    }


@dataclass(frozen=True)
class Choice:
    name: str
    table: pd.DataFrame  # one row per candidate and split part


def choose(
    candidates: dict[str, np.ndarray],
    frame: pd.DataFrame,
    split: np.ndarray,
    bootstrap: int = 200,
) -> Choice:
    """Pick the candidate with the best average precision on labelled validation segments."""
    labelled = frame["label"].to_numpy() >= 0
    y = frame["label"].to_numpy() == 1
    events = event_keys(frame).to_numpy()
    rows = []
    for name, score in candidates.items():
        for part in ("val", "test"):
            mask = labelled & (split == part)
            n_boot = bootstrap if part == "test" else 0
            metrics = safe_evaluate(y[mask], score[mask], events[mask], n_boot)
            rows.append({"candidate": name, "part": part, **metrics})
    table = pd.DataFrame(rows)
    val = table[table["part"] == "val"].set_index("candidate")["avg_precision"]
    name = str(val.idxmax()) if val.notna().any() else "mean"
    return Choice(name=name, table=table)


def run_scoring(
    checkpoint: Path,
    data: TelemetryData | None = None,
    out_dir: Path = RESULTS_DIR,
    report: Path | None = REPORT_DIR / "m4_scoring.md",
    device: torch.device | None = None,
    bootstrap: int = 200,
) -> pd.DataFrame:
    """Score every segment of `data` (default: all eligible segments, after computing the
    features of any newly processed session) and write the results."""
    started = time.time()
    device = device or pick_device()
    checkpoint_sha256 = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    model, state = load_checkpoint(checkpoint, device)
    if data is None:
        log.info("features computed for %d new or reprocessed sessions", build_features())
        data = load_or_build_tensors(load_eval_frame())
    frame = data.frame
    # Which segments are scored, from which processed files (see check_scored_frame). Taken
    # before scoring: a session reprocessed while this runs must not pass for the one scored.
    frame_key = cache_key(frame, sources=True)
    split, scheme, notes = checkpoint_split(state, frame)
    split_values = split.to_numpy()

    log.info("scoring %d segments with %s", len(frame), checkpoint.name)
    scores, embeddings = score_and_embed(model, data, device)
    train = split_values == "train"
    log.info("fitting Isolation Forest on %d training segments", int(train.sum()))
    iforest = IsoForest().fit(frame, train).score(frame)
    raw = {"transformer": scores[TRANSFORMER_SCORE], "isolation_forest": iforest}
    percentiles = {name: session_percentile(frame, value) for name, value in raw.items()}
    candidates = combinations(percentiles)
    choice = choose(candidates, frame, split_values, bootstrap)
    combined = candidates[choice.name]
    log.info("combined score chosen on validation events: %s", choice.name)

    columns = [c for c in FRAME_COLUMNS if c in frame.columns]
    results = frame[columns].copy()
    results["split"] = split_values
    for name, value in raw.items():
        results[f"{name}_raw"] = value.astype(np.float32)
        results[f"{name}_pct"] = percentiles[name].astype(np.float32)
    results["score"] = combined.astype(np.float32)
    results["score_pct"] = session_percentile(frame, combined).astype(np.float32)
    results["flagged"] = results["score_pct"] > 1 - FLAG_SHARE

    created = datetime.now(UTC).isoformat(timespec="seconds")
    run_id = new_run_id(created, checkpoint_sha256)
    manifest = {
        "run_id": run_id,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": checkpoint_sha256,
        "model_cfg": state["model_cfg"],
        "split": scheme,
        "transformer_score": TRANSFORMER_SCORE,
        "combined": choice.name,
        "flag_share": FLAG_SHARE,
        "segments": len(results),
        "frame_key": frame_key,
        "flagged": int(results["flagged"].sum()),
        "created": created,
    }
    # The old run's manifest goes before its files are overwritten and the new one is written
    # last: until then there is no complete run, and the later steps refuse to build on these.
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST
    manifest_path.unlink(missing_ok=True)
    write_parquet(results, out_dir / "segment_scores.parquet", run_id)
    np.savez_compressed(
        out_dir / "embeddings.npz",
        segment_id=frame["segment_id"].to_numpy().astype(str),
        embedding=embeddings.astype(np.float16),
    )
    if report is not None:
        write_report(choice, manifest, results, notes, report)
    manifest["seconds"] = round(time.time() - started, 1)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    flagged = manifest["flagged"]
    log.info("wrote %s (run %s: %d segments, %d flagged)", out_dir, run_id, len(results), flagged)
    return results


def check_scored_frame(frame: pd.DataFrame, results: Path = RESULTS_DIR) -> None:
    """Raise StaleResultsError unless `frame` holds the segments the current scoring run
    scored, from the same processed files. A step that loads the eval frame itself (style,
    onnx) would otherwise describe other data than the scores when features were computed or a
    session reprocessed since `race-engineer-infer score`."""
    path = results / MANIFEST
    recorded = json.loads(path.read_text()).get("frame_key") if path.exists() else None
    if recorded is None:
        raise StaleResultsError(
            f"No current scoring run in {results}: run `race-engineer-infer score` (or `make m4`)."
        )
    if cache_key(frame, sources=True) != recorded:
        raise StaleResultsError(
            f"The eval frame ({len(frame):,} segments) is not the one the current scoring run "
            "scored: features were computed or a session reprocessed since. Run "
            "`race-engineer-infer score`, then the later steps (`make m4`)."
        )


def _fmt(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if np.isfinite(value) else "n/a"


def write_report(
    choice: Choice,
    manifest: dict[str, Any],
    results: pd.DataFrame,
    notes: list[str],
    path: Path,
) -> Path:
    table = choice.table
    rows = []
    for name in table["candidate"].unique():
        val = table[(table["candidate"] == name) & (table["part"] == "val")].iloc[0]
        test = table[(table["candidate"] == name) & (table["part"] == "test")].iloc[0]
        rows.append(
            {
                "candidate": f"**{name}**" if name == choice.name else name,
                "AP val": _fmt(val["avg_precision"]),
                "AP test [95% CI]": (
                    f"{_fmt(test['avg_precision'])} [{_fmt(test['avg_precision_lo'])}, "
                    f"{_fmt(test['avg_precision_hi'])}]"
                ),
                "AUROC val": _fmt(val["auroc"]),
                "AUROC test": _fmt(test["auroc"]),
                "P@50 test": _fmt(test["p@50"], 2),
                "recall@5% test": _fmt(test["recall@5%"], 2),
            }
        )
    flagged = results[results["flagged"]]
    labelled = flagged[flagged["label"] >= 0]
    per_session = results.groupby(SESSION)["flagged"].sum()
    parts = [
        "# M4: scoring every corner",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `race-engineer-infer score` from "
        f"`{Path(manifest['checkpoint']).name}` ({manifest['split']} split)._",
        *notes,
        "Every eligible corner segment gets two scores, each turned into a percentile within its "
        f"session: the Telemetry Transformer's reconstruction error after the apex "
        f"(`{manifest['transformer_score']}`, chosen in M3) and Isolation Forest on the "
        "hand-crafted features (the best baseline). `mean` needs both to find a corner unusual, "
        "`max` needs either. The combination is **chosen by average precision on the "
        "validation events only** and reported on the test events. AP base rate is about 0.001.",
        markdown_table(pd.DataFrame(rows)),
        f"**Chosen:** `{choice.name}`. A corner is flagged when its combined score is in the top "
        f"{manifest['flag_share']:.0%} of its session: {int(results['flagged'].sum()):,} flags "
        f"over {len(per_session)} sessions (median {int(per_session.median())} per session). "
        f"Of the flags on labelled segments, {int((labelled['label'] == 1).sum())} of "
        f"{len(labelled):,} are labelled track-limits mistakes; most real mistakes carry no "
        "label, so the manual review of the top findings is the real check.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(parts) + "\n")
    return path


def load_scores(root: Path = RESULTS_DIR) -> pd.DataFrame:
    """segment_scores.parquet, if it is the current scoring run's (else StaleResultsError)."""
    path = root / "segment_scores.parquet"
    check_current(path, "score", root)
    return pd.read_parquet(path)


def load_embeddings(root: Path = RESULTS_DIR) -> tuple[np.ndarray, np.ndarray]:
    """(segment ids, embeddings as float32)."""
    with np.load(root / "embeddings.npz") as f:
        return f["segment_id"], f["embedding"].astype(np.float32)
