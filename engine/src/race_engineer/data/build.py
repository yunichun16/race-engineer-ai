"""Turn FastF1 sessions into the processed dataset (one Parquet file per session per table).

Per session: load it, build the reference line and corner positions, resample every lap onto
the distance grid, cut corner segments, attach lap context and weak labels, and write it all.
Finished sessions are skipped on re-runs, so an interrupted build just picks up where it
stopped.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import fastf1
import numpy as np
import pandas as pd
from fastf1.core import Session
from fastf1.exceptions import RateLimitExceededError
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

from race_engineer.config import (
    FASTF1_CACHE,
    GRID_STEP_M,
    POSITION_UNITS_PER_M,
    PROCESSED_DIR,
    QUALI_CODES,
)
from race_engineer.data import store
from race_engineer.data.catalog import SessionRef
from race_engineer.data.labels import incident_events, mark_clusters, track_limit_events
from race_engineer.features.context import attach_weather, classify_laps, gap_ahead_s
from race_engineer.features.grid import (
    GRID_CHANNELS,
    DriverTelemetry,
    LapQA,
    lap_to_grid,
    make_grid,
    mismatch_windows,
    position_fixes,
    to_seconds,
)
from race_engineer.features.reference import ReferenceLine
from race_engineer.features.segments import SEGMENT_CHANNELS, Corner, cut_segments, window_indices

log = logging.getLogger(__name__)

LAP_COLUMNS = {
    "Driver": "driver",
    "DriverNumber": "driver_number",
    "Team": "team",
    "LapNumber": "lap_number",
    "Compound": "compound",
    "TyreLife": "tyre_life",
    "FreshTyre": "fresh_tyre",
    "Stint": "stint",
    "TrackStatus": "track_status",
    "IsAccurate": "is_accurate",
    "IsPersonalBest": "is_personal_best",
    "Deleted": "deleted",
    "DeletedReason": "deleted_reason",
    "Position": "position",
    "SpeedI1": "speed_i1",
    "SpeedI2": "speed_i2",
    "SpeedFL": "speed_fl",
    "SpeedST": "speed_st",
}
SEGMENT_CONTEXT = [
    "driver",
    "driver_number",
    "team",
    "lap_number",
    "lap_class",
    "compound",
    "tyre_life",
    "stint",
    "gap_ahead_s",
    "track_temp",
    "air_temp",
    "rainfall",
    "deleted",
]


@dataclass
class SessionSummary:
    key: str
    n_laps: int
    n_grid_ok: int
    n_segments: int
    n_events: int
    seconds: float


def load_session(ref: SessionRef) -> Session:
    session = fastf1.get_session(ref.year, ref.round, ref.name)
    session.load(laps=True, telemetry=True, weather=True, messages=True)
    return session


@dataclass(frozen=True)
class ReferenceLap:
    driver: str
    driver_number: str
    lap_number: int
    start_s: float
    lap_time_s: float


# The reference line is only as good as its lap's position trace: where the feed stalls in a
# corner, the corner becomes a straight chord between two fixes (on a straight, a chord is
# harmless), and a glitch leaves a spike that every other lap then skips over. A chord barely
# changes distance (by L * theta^2 / 24), and a fixed sideways bias at one spot is removed by
# the per-corner normalisation, so the limit only needs to catch corners cut badly.
MAX_REFERENCE_CHORD_ERROR_M = 5.0
MAX_REFERENCE_FIX_GAP_M = 200.0
LONG_STEP_M = 40.0  # ordinary steps between fixes are 5-25 m


def chord_error_m(x_m: np.ndarray, y_m: np.ndarray) -> float:
    """How far the straight line across the worst gap between fixes may cut inside the path.

    For each step longer than LONG_STEP_M, the heading change from the step before it to the
    step after it gives the turn across the gap; a circular arc of length L turning by theta
    sits about L * theta / 8 outside its chord.
    """
    dx, dy = np.diff(x_m), np.diff(y_m)
    length, heading = np.hypot(dx, dy), np.arctan2(dy, dx)
    if len(length) < 3:
        return float("inf")
    turn = np.abs(np.angle(np.exp(1j * (heading[2:] - heading[:-2]))))
    inner = length[1:-1]
    sagitta = np.where(inner > LONG_STEP_M, inner * turn / 8, 0.0)
    return float(sagitta.max())


def build_reference(session: Session) -> tuple[ReferenceLine, ReferenceLap]:
    """Reference line from the fastest lap whose position trace is consistent and clean.

    Consistent: its length is within 4% of the distance the car drove (from speed). Clean: the
    path through the fixes agrees with the car's speed everywhere (see `mismatch_windows`), no
    gap between fixes cuts a corner by more than MAX_REFERENCE_CHORD_ERROR_M (see
    `chord_error_m`), and none is longer than MAX_REFERENCE_FIX_GAP_M. If no lap is clean, the
    consistent lap is used that has no gap over the limit if possible, then the fewest
    stretches disagreeing with speed, then the smallest chord error.
    """
    laps = session.laps
    candidates = laps[
        laps["LapTime"].notna() & laps["PitInTime"].isna() & laps["PitOutTime"].isna()
    ]
    best: tuple[tuple[bool, int, float], ReferenceLine, ReferenceLap] | None = None
    traces: dict[str, tuple[np.ndarray, ...]] = {}  # per driver: fixes, and distance driven
    for _, lap in candidates.sort_values("LapTime").iterrows():
        number = str(lap["DriverNumber"])
        if number not in session.pos_data or number not in session.car_data:
            continue
        if number not in traces:
            pos, car = session.pos_data[number], session.car_data[number]
            x_all, y_all = pos["X"].to_numpy(float), pos["Y"].to_numpy(float)
            fix = position_fixes(x_all, y_all)
            t_car = to_seconds(car["SessionTime"])
            v = car["Speed"].to_numpy(float) / 3.6
            driven_all = np.concatenate([[0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * np.diff(t_car))])
            traces[number] = (to_seconds(pos["SessionTime"])[fix], x_all[fix], y_all[fix])
            traces[number] += (t_car, driven_all)
        t_pos, x_all, y_all, t_car, driven_all = traces[number]
        t0 = lap["LapStartTime"].total_seconds()
        t1 = t0 + lap["LapTime"].total_seconds()
        sel = (t_pos > t0) & (t_pos < t1)
        if sel.sum() < 50:
            continue
        # Start the line exactly where the lap starts (the timing line), so grid distance 0 is
        # the line and grid time matches official lap times.
        t = np.concatenate([[t0], t_pos[sel]])
        x = np.concatenate([[np.interp(t0, t_pos, x_all)], x_all[sel]])
        y = np.concatenate([[np.interp(t0, t_pos, y_all)], y_all[sel]])
        step = np.hypot(np.diff(x), np.diff(y)) / POSITION_UNITS_PER_M
        path = np.concatenate([[0.0], np.cumsum(step)])
        first, last = mismatch_windows(t, path, np.interp(t, t_car, driven_all))
        stretches = int(np.sum(first[1:] > last[:-1])) + 1 if len(first) else 0
        # Fixes only: the interpolated start point can sit centimetres from the first fix.
        chord = chord_error_m(x[1:] / POSITION_UNITS_PER_M, y[1:] / POSITION_UNITS_PER_M)
        key = (bool(step.max() > MAX_REFERENCE_FIX_GAP_M), stretches, chord)
        if best is not None and key >= best[0]:
            continue
        reference = ReferenceLine.from_positions(x, y)
        driven = float(np.interp(t1, t_car, driven_all) - np.interp(t0, t_car, driven_all))
        if abs(reference.length_m - driven) / driven >= 0.04:
            continue
        ref_lap = ReferenceLap(str(lap["Driver"]), number, int(lap["LapNumber"]), t0, t1 - t0)
        long_gap, stretches, chord = key
        if not long_gap and stretches == 0 and chord <= MAX_REFERENCE_CHORD_ERROR_M:
            return reference, ref_lap
        best = (key, reference, ref_lap)
    if best is None:
        raise ValueError("no lap with usable position data for the reference line")
    (long_gap, stretches, chord), reference, ref_lap = best
    log.warning(
        "no lap with clean position data; reference from %s lap %d (%s; chords cut corners by "
        "up to %.1f m; %d stretches disagree with speed)",
        ref_lap.driver,
        ref_lap.lap_number,
        f"fixes over {MAX_REFERENCE_FIX_GAP_M:.0f} m apart" if long_gap else "no long gaps",
        chord,
        stretches,
    )
    return reference, ref_lap


def sibling_corners(
    ref: SessionRef, reference: ReferenceLine, root: Path = PROCESSED_DIR
) -> pd.DataFrame | None:
    """Turn positions from another processed session of the same weekend, re-projected onto this
    session's reference line (the turns are the same; only the reference lap differs)."""
    for path in sorted((root / "corners").glob(f"{ref.year}_{ref.round:02d}_*.parquet")):
        if path.stem == store.session_key(ref.year, ref.round, ref.code):
            continue
        other = pd.read_parquet(path, columns=["number", "letter", "x_m", "y_m", "angle"])
        apex = reference.project(
            other["x_m"].to_numpy() * 10, other["y_m"].to_numpy() * 10
        ).distance_m
        return other.assign(apex_m=apex)[["number", "letter", "apex_m", "x_m", "y_m", "angle"]]
    return None


def detect_corners(speed: np.ndarray, grid: np.ndarray, reference: ReferenceLine) -> pd.DataFrame:
    """Corners as the reference lap's speed minima, for circuits without official turn data.

    Used for new circuits (e.g. Madrid in 2026). Numbering follows the order around the lap, so
    it may differ from the official turn numbers.
    """
    smooth = gaussian_filter1d(speed.astype(float), 2.0)
    minima, _ = find_peaks(-smooth, prominence=20.0, distance=int(100 / GRID_STEP_M))
    apex = grid[minima]
    return pd.DataFrame(
        {
            "number": np.arange(1, len(apex) + 1),
            "letter": "",
            "apex_m": apex,
            "x_m": np.interp(apex, reference.s, reference.xy[:, 0]),
            "y_m": np.interp(apex, reference.s, reference.xy[:, 1]),
            "angle": np.nan,
        }
    )


def corners_on(session: Session, reference: ReferenceLine) -> pd.DataFrame:
    info = session.get_circuit_info()
    if info is None or info.corners is None or info.corners.empty:
        raise ValueError("circuit info (turn positions) unavailable")
    c = info.corners
    apex = reference.project(c["X"].to_numpy(float), c["Y"].to_numpy(float)).distance_m
    return pd.DataFrame(
        {
            "number": c["Number"].astype(int).to_numpy(),
            "letter": c["Letter"].fillna("").astype(str).to_numpy(),
            "apex_m": apex,
            "x_m": c["X"].to_numpy(float) / 10,
            "y_m": c["Y"].to_numpy(float) / 10,
            "angle": c["Angle"].to_numpy(float),
        }
    )


def lap_table(session: Session, quali: bool) -> pd.DataFrame:
    laps = session.laps.reset_index(drop=True)
    out = laps[[c for c in LAP_COLUMNS if c in laps]].rename(columns=LAP_COLUMNS)
    out["driver_number"] = out["driver_number"].astype(str)
    out["lap_number"] = out["lap_number"].astype(int)
    out["lap_time_s"] = to_seconds(laps["LapTime"])
    out["lap_start_s"] = to_seconds(laps["LapStartTime"])
    for i in (1, 2, 3):
        out[f"sector{i}_s"] = to_seconds(laps[f"Sector{i}Time"])
    out["pit_in"] = laps["PitInTime"].notna()
    out["pit_out"] = laps["PitOutTime"].notna()
    out["lap_class"] = classify_laps(laps, quali).to_numpy()
    out["gap_ahead_s"] = gap_ahead_s(laps).to_numpy()
    weather = attach_weather(laps, session.weather_data)
    out["air_temp"] = weather["AirTemp"].to_numpy(float)
    out["track_temp"] = weather["TrackTemp"].to_numpy(float)
    out["humidity"] = weather["Humidity"].to_numpy(float)
    out["rainfall"] = weather["Rainfall"].astype(float).to_numpy()
    out["wind_speed"] = weather["WindSpeed"].to_numpy(float)
    out["deleted"] = out["deleted"].fillna(False).astype(bool)
    out["deleted_reason"] = out["deleted_reason"].fillna("").astype(str).str.strip()
    return out


def process_session(ref: SessionRef) -> dict[str, Any]:
    """Everything for one session, as frames ready to write (arrays kept alongside)."""
    session = load_session(ref)
    quali = ref.code in QUALI_CODES
    reference, ref_lap = build_reference(session)
    grid = make_grid(reference.length_m, GRID_STEP_M)
    try:
        corners, corner_source = corners_on(session, reference), "official"
    except RateLimitExceededError:
        raise
    except Exception as exc:  # e.g. brand-new circuits, or broken position data
        sibling = sibling_corners(ref, reference)
        if sibling is not None:
            log.info("%s %s: turn positions from another session (%s)", ref.event, ref.code, exc)
            corners, corner_source = sibling, "official (other session)"
        else:
            log.info(
                "%s %s: no official turn positions (%s); detecting corners",
                ref.event,
                ref.code,
                exc,
            )
            tel = DriverTelemetry.from_fastf1(
                cast(pd.DataFrame, session.car_data[ref_lap.driver_number]),
                cast(pd.DataFrame, session.pos_data[ref_lap.driver_number]),
            )
            ref_grid = lap_to_grid(tel, ref_lap.start_s, ref_lap.lap_time_s, reference, grid)
            if "speed" not in ref_grid.channels:
                raise ValueError(f"cannot detect corners: {ref_grid.qa.reason}") from exc
            corners, corner_source = (
                detect_corners(ref_grid.channels["speed"], grid, reference),
                "detected",
            )
    laps = lap_table(session, quali)

    qa_rows: list[LapQA] = []
    grid_rows: list[tuple[int, dict[str, np.ndarray]]] = []  # (laps row index, channels)
    segment_rows: list[tuple[int, object]] = []
    skipped_windows = invalid_windows = 0
    corner_list = [
        Corner(int(n), str(letter), float(apex))
        for n, letter, apex in zip(
            corners["number"], corners["letter"], corners["apex_m"], strict=True
        )
    ]

    for number, driver_laps in laps.groupby("driver_number", sort=False):
        if number not in session.car_data or number not in session.pos_data:
            for _ in driver_laps.index:
                qa_rows.append(LapQA(0, np.nan, np.nan, np.nan, np.nan, False, "no telemetry"))
            continue
        tel = DriverTelemetry.from_fastf1(
            cast(pd.DataFrame, session.car_data[number]),
            cast(pd.DataFrame, session.pos_data[number]),
        )
        grids: dict[int, dict[str, np.ndarray]] = {}
        times: dict[int, float] = {}
        index_of: dict[int, int] = {}
        for idx, lap in driver_laps.iterrows():
            if np.isnan(lap["lap_time_s"]) or np.isnan(lap["lap_start_s"]):
                qa_rows.append(LapQA(0, np.nan, np.nan, np.nan, np.nan, False, "no lap time"))
                continue
            start, lap_time = float(lap["lap_start_s"]), float(lap["lap_time_s"])
            result = lap_to_grid(tel, start, lap_time, reference, grid)
            qa_rows.append(result.qa)
            if result.qa.ok:
                grids[int(lap["lap_number"])] = result.channels
                times[int(lap["lap_number"])] = float(lap["lap_time_s"])
                index_of[int(lap["lap_number"])] = int(idx)  # pyright: ignore[reportArgumentType]
                grid_rows.append((int(idx), result.channels))  # pyright: ignore[reportArgumentType]
        segments, skipped, invalid = cut_segments(grids, times, corner_list, GRID_STEP_M)
        skipped_windows += skipped
        invalid_windows += invalid
        segment_rows.extend((index_of[s.lap_number], s) for s in segments)

    # QA rows were appended in groupby order; line them back up with the laps table.
    order = [idx for _, g in laps.groupby("driver_number", sort=False) for idx in g.index]
    qa = pd.DataFrame([q.__dict__ for q in qa_rows], index=pd.Index(order)).reindex(laps.index)
    laps["grid_ok"] = qa["ok"].fillna(False).astype(bool)
    laps["grid_reason"] = qa["reason"].fillna("")
    laps["n_samples"] = qa["n_samples"].fillna(0).astype(int)
    laps["max_gap_m"] = qa["max_gap_m"]
    laps["p95_offset_m"] = qa["p95_abs_offset_m"]
    laps["backwards_m"] = qa["backwards_m"]
    laps["frozen_share"] = qa["frozen_share"]
    laps["max_fix_gap_m"] = qa["max_fix_gap_m"]
    laps["gap_share"] = qa["gap_share"]
    laps["sparse_share"] = qa["sparse_share"]
    laps["mismatch_share"] = qa["mismatch_share"]

    messages = session.race_control_messages
    events = mark_clusters(
        pd.concat(
            [
                track_limit_events(session.laps, messages, quali),
                incident_events(session.laps, messages),
            ],
            ignore_index=True,
        )
    )

    window = window_indices(0.0, GRID_STEP_M)
    corners["curvature"] = pd.Series(
        [
            reference.curvature_at(float(apex) + window * GRID_STEP_M).astype(np.float32)
            for apex in corners["apex_m"]
        ],
        index=corners.index,
        dtype=object,
    )
    track = {
        "length_m": reference.length_m,
        "x_m": np.interp(grid, reference.s, reference.xy[:, 0]).astype(np.float32),
        "y_m": np.interp(grid, reference.s, reference.xy[:, 1]).astype(np.float32),
        "curvature": reference.curvature_at(grid).astype(np.float32),
    }
    info = {
        "event": ref.event,
        "location": ref.location,
        "session_name": ref.name,
        "session_date": ref.date_utc,
        "track_length_m": reference.length_m,
        "grid_step_m": GRID_STEP_M,
        "n_grid": len(grid),
        "n_corners": len(corners),
        "reference_driver": ref_lap.driver,
        "reference_lap": ref_lap.lap_number,
        "corner_source": corner_source,
        "n_laps": len(laps),
        "n_grid_ok": int(laps["grid_ok"].sum()),
        "n_segments": len(segment_rows),
        "skipped_windows": skipped_windows,
        "invalid_windows": invalid_windows,
        "n_track_limit_events": int((events["kind"] == "track_limits").sum()),
        "n_incident_events": int((events["kind"] == "incident").sum()),
        "fastf1_version": fastf1.__version__,
    }
    return {
        "session": session,
        "info": info,
        "laps": laps,
        "grid_rows": grid_rows,
        "segment_rows": segment_rows,
        "events": events,
        "corners": corners,
        "track": track,
    }


def write_session(ref: SessionRef, out: dict, root: Path = PROCESSED_DIR) -> SessionSummary:
    key = store.session_key(ref.year, ref.round, ref.code)
    ids = {"year": ref.year, "round": ref.round, "session": ref.code}
    laps: pd.DataFrame = out["laps"].assign(**ids)
    laps["lap_id"] = key + "_" + laps["driver_number"] + "_" + laps["lap_number"].astype(str)

    grid_idx = [i for i, _ in out["grid_rows"]]
    grid_meta = laps.loc[
        grid_idx, ["year", "round", "session", "lap_id", "driver", "driver_number", "lap_number"]
    ]
    store.write_table(
        store.to_arrow(
            grid_meta,
            {c: store.list_column([g[c] for _, g in out["grid_rows"]]) for c in GRID_CHANNELS},
        ),
        "lap_grids",
        key,
        root,
    )

    seg_idx = [i for i, _ in out["segment_rows"]]
    segs = [s for _, s in out["segment_rows"]]
    seg_meta = laps.loc[
        seg_idx, ["year", "round", "session", "lap_id", *SEGMENT_CONTEXT]
    ].reset_index(drop=True)
    seg_meta["corner"] = [s.corner.number for s in segs]
    seg_meta["corner_letter"] = [s.corner.letter for s in segs]
    seg_meta["apex_m"] = [s.corner.apex_m for s in segs]
    seg_meta["start_m"] = [s.start_m for s in segs]
    seg_meta["stitched"] = [s.stitched for s in segs]
    seg_meta["segment_time_s"] = [s.segment_time_s for s in segs]
    seg_meta["segment_id"] = (
        seg_meta["lap_id"] + "_T" + seg_meta["corner"].astype(str) + seg_meta["corner_letter"]
    )
    size = len(window_indices(0.0, GRID_STEP_M))
    store.write_table(
        store.to_arrow(
            seg_meta,
            {
                c: store.fixed_list_column([s.channels[c] for s in segs], size)
                for c in SEGMENT_CHANNELS
            },
        ),
        "segments",
        key,
        root,
    )

    corners: pd.DataFrame = out["corners"].assign(**ids)
    curvature = store.fixed_list_column(list(corners.pop("curvature")), size)
    store.write_table(store.to_arrow(corners, {"curvature": curvature}), "corners", key, root)

    track = out["track"]
    store.write_table(
        store.to_arrow(
            pd.DataFrame([{**ids, "length_m": track["length_m"]}]),
            {c: store.list_column([track[c]]) for c in ("x_m", "y_m", "curvature")},
        ),
        "tracks",
        key,
        root,
    )
    store.write_table(store.to_arrow(out["events"].assign(**ids)), "events", key, root)
    store.write_table(store.to_arrow(laps), "laps", key, root)
    # Written last: its presence marks the session as complete.
    store.write_table(store.to_arrow(pd.DataFrame([{**ids, **out["info"]}])), "sessions", key, root)

    info = out["info"]
    return SessionSummary(
        key=key,
        n_laps=info["n_laps"],
        n_grid_ok=info["n_grid_ok"],
        n_segments=info["n_segments"],
        n_events=info["n_track_limit_events"] + info["n_incident_events"],
        seconds=0.0,
    )


def drop_session_cache(session: Session) -> None:
    folder = FASTF1_CACHE / session.api_path.strip("/").removeprefix("static/")
    if folder.is_dir() and FASTF1_CACHE in folder.parents:
        shutil.rmtree(folder)


RATE_LIMIT_WAIT_S = 600
# A downloaded session costs ~13 FastF1 API calls. Spacing downloads 110 s apart keeps the build
# near 430 calls/hour, under FastF1's 500/hour cap (which protects the upstream APIs).
DEFAULT_PACE_S = 110.0
CACHED_LOAD_S = 5.0  # sessions served from FastF1's cache load faster than this


def build(
    refs: list[SessionRef],
    *,
    force: bool = False,
    drop_cache: bool = False,
    pace_s: float = DEFAULT_PACE_S,
    root: Path = PROCESSED_DIR,
    offline: bool = False,
) -> None:
    """Process `refs` in turn. `offline` serves everything from FastF1's cache (expired entries
    included) and never touches the network, so it can run beside a download without using up
    the API limit."""
    FASTF1_CACHE.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(FASTF1_CACHE))
    fastf1.Cache.offline_mode(offline)
    manifest = root / "manifest.jsonl"
    root.mkdir(parents=True, exist_ok=True)
    for i, ref in enumerate(refs, 1):
        key = store.session_key(ref.year, ref.round, ref.code)
        if not force and store.table_path("sessions", key, root).exists():
            continue
        started = time.time()
        record = {"key": key, "event": ref.event, "session": ref.name}
        try:
            while True:
                try:
                    out = process_session(ref)
                    break
                except RateLimitExceededError:
                    log.warning(
                        "FastF1 rate limit reached; waiting %d min", RATE_LIMIT_WAIT_S // 60
                    )
                    time.sleep(RATE_LIMIT_WAIT_S)
                    started = time.time()
            load_s = time.time() - started
            summary = write_session(ref, out, root)
            summary.seconds = round(time.time() - started, 1)
            record |= {"status": "ok", **summary.__dict__}
            log.info(
                "[%d/%d] %s %s %s: %d laps, %d grids, %d segments, %d labels (%.0fs)",
                i,
                len(refs),
                key,
                ref.event,
                ref.name,
                summary.n_laps,
                summary.n_grid_ok,
                summary.n_segments,
                summary.n_events,
                summary.seconds,
            )
            if drop_cache:
                drop_session_cache(out["session"])
        except Exception as exc:  # one bad session shouldn't stop a multi-hour build
            record |= {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            log.warning("[%d/%d] %s %s %s failed: %s", i, len(refs), key, ref.event, ref.name, exc)
            load_s = time.time() - started
        with manifest.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        if load_s > CACHED_LOAD_S and i < len(refs):
            time.sleep(max(0.0, pace_s - (time.time() - started)))
