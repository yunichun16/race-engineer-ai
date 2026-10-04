"""Made-up driving-style results in the real table schemas, for tests and the chart fixture.

The style tables come from the eval frame and the M4 embeddings, which CI doesn't have.
`write_style_results` makes a small eval-frame-like set of corner segments with known effects,
builds the tables from them with the code `race-engineer-infer style` runs (`build_style`) and
writes them as that step does, each recording the scoring run, plus a manifest naming the run.
So the tables have the real schemas by construction, and the effects come out where a test
expects them:

- 2025: three teams of two (Red AAA/BBB, Blue CCC/DDD, Green EEE/FFF) at six events. AAA brakes
  10 m later than teammate BBB in slow corners, CCC carries 2 km/h more minimum speed than DDD
  and EEE coasts 4 m more than FFF.
- 2024: two teams at five events: Red is AAA with GGG for rounds 1-3 and HHH for rounds 4-5,
  Blue is CCC/DDD. GGG drives only in 2024 and is back on full throttle 5 m earlier than AAA;
  their three events together are too few for intervals.

Every event has the same six corners (two slow, two medium, two fast), a qualifying session
(an out lap and three push laps on softs) and a race (six laps in free air, mediums then hards).
Five or more events in a season, and three teams, are what the style map needs: intervals take
at least 4 events (MIN_INTERVAL_EVENTS), the map's projection 5 (MIN_EVENTS) and its axes three
teammate pairs.
"""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from race_engineer.inference.provenance import MANIFEST
from race_engineer.inference.style import FRAME_COLUMNS, build_style, write_tables

SYNTHETIC_RUN = "synthetic/000000000000"
# Season -> rounds and line-ups (team -> its two drivers).
SEASONS: dict[int, tuple[range, dict[str, tuple[str, str]]]] = {
    2024: (range(1, 6), {"Red": ("AAA", "GGG"), "Blue": ("CCC", "DDD")}),
    2025: (range(1, 7), {"Red": ("AAA", "BBB"), "Blue": ("CCC", "DDD"), "Green": ("EEE", "FFF")}),
}
# (season, driver) -> (first round, the driver who has the seat from then on)
SUBSTITUTES = {(2024, "GGG"): (4, "HHH")}
EVENTS = ("Lakeside", "Harbour", "Desert", "Forest", "Mountain", "Riverside")
# corner, apex (m), minimum speed (km/h), slowest point relative to the apex (m), corner type
CORNERS = (
    (1, 400.0, 85.0, -5.0, "slow"),
    (2, 1300.0, 105.0, 0.0, "slow"),
    (3, 2200.0, 150.0, 5.0, "medium"),
    (4, 3000.0, 175.0, -10.0, "medium"),
    (5, 3900.0, 225.0, 10.0, "fast"),
    (6, 4700.0, 260.0, 0.0, "fast"),
)
SESSIONS = (("Q", 4), ("R", 6))  # session, laps
# driver -> metric -> (shift, corner types it applies to; empty = all)
EFFECTS: dict[str, dict[str, tuple[float, tuple[str, ...]]]] = {
    "AAA": {"brake_start_d": (10.0, ("slow",))},
    "CCC": {"v_min": (2.0, ())},
    "EEE": {"coast_m": (4.0, ())},
    "GGG": {"throttle_pickup_d": (-5.0, ())},
}
EMBED_DIM = 8


def lap_context(session: str, lap: int) -> dict[str, object]:
    if session == "Q":
        return {"lap_class": "out" if lap == 1 else "push", "compound": "SOFT"}
    return {"lap_class": "race", "compound": "MEDIUM" if lap <= 3 else "HARD"}


def driver_at(year: int, driver: str, round_: int) -> str:
    """Who drove `driver`'s seat in this round (SUBSTITUTES)."""
    first, substitute = SUBSTITUTES.get((year, driver), (0, driver))
    return substitute if first and round_ >= first else driver


def style_frame(seed: int = 0) -> pd.DataFrame:
    """Eval-frame-like corner segments (the columns `run_style` reads, with decel_entry) for
    every season, event, session, driver, lap and corner above."""
    rng = np.random.default_rng(seed)
    rows = []
    for year, (rounds, lineup) in SEASONS.items():
        for round_, (session, laps), (team, drivers) in product(rounds, SESSIONS, lineup.items()):
            for seat, lap, corner in product(drivers, range(1, laps + 1), CORNERS):
                driver = driver_at(year, seat, round_)
                number, apex, v_min, d_vmin, corner_type = corner
                values = {
                    "brake_start_d": -100.0 + rng.normal(0, 2),
                    "decel_entry": 35.0 + rng.normal(0, 0.5),
                    "v_min": v_min + rng.normal(0, 0.5),
                    "throttle_pickup_d": 40.0 + rng.normal(0, 3),
                    "coast_m": 15.0 + rng.normal(0, 2),
                    "v_exit": v_min + 60.0 + rng.normal(0, 1),
                    "segment_time_s": 4.0 + rng.normal(0, 0.02),
                }
                for metric, (shift, types) in EFFECTS.get(driver, {}).items():
                    if not types or corner_type in types:
                        values[metric] += shift
                rows.append(
                    {
                        "segment_id": f"{year}_{round_}_{session}_{driver}_{lap}_T{number}",
                        "year": year, "round": round_, "session": session,
                        "event": f"{EVENTS[round_ - 1]} Grand Prix", "driver": driver,
                        "team": team, "lap_number": lap, "corner": number, "corner_letter": "",
                        "apex_m": apex, "deleted": False, "gap_ahead_s": 3.0,
                        "d_vmin": d_vmin, "v_apex": v_min + 2.0,
                        **lap_context(session, lap), **values,
                    }
                )  # fmt: skip
    return pd.DataFrame(rows)[[*FRAME_COLUMNS, "decel_entry"]]


def style_embeddings(frame: pd.DataFrame, seed: int = 0) -> np.ndarray:
    """Segment embeddings with a strong team signature, a corner signature, a steady offset of
    each driver's own and noise, as M3 found the real ones: drivers group by team."""
    rng = np.random.default_rng(seed)
    corner = {c: rng.normal(0, 1, EMBED_DIM) for c in sorted(frame["corner"].unique())}
    team = {t: rng.normal(0, 2, EMBED_DIM) for t in sorted(frame["team"].unique())}
    driver = {d: rng.normal(0, 0.5, EMBED_DIM) for d in sorted(frame["driver"].unique())}
    x = np.stack(
        [
            corner[c] + team[t] + driver[d]
            for c, t, d in zip(frame["corner"], frame["team"], frame["driver"], strict=True)
        ]
    )
    return (x + rng.normal(0, 0.3, x.shape)).astype(np.float32)


def write_style_results(results: Path, run_id: str = SYNTHETIC_RUN) -> list[Path]:
    """Build the synthetic style tables and write them under `results` as `race-engineer-infer
    style` does, each recording `run_id`, and make `run_id` the current scoring run in
    manifest.json (any other keys of an existing manifest are kept). Returns the tables' paths."""
    frame = style_frame()
    ids = frame["segment_id"].to_numpy()
    tables = build_style(frame, ids, style_embeddings(frame), chance_reps=1, null_reps=500)
    paths = write_tables(tables, results, run_id)
    manifest_path = results / MANIFEST
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest_path.write_text(json.dumps({**manifest, "run_id": run_id}, indent=2))
    return paths
