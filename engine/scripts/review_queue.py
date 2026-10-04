"""The M4 manual review: the top findings of the test year, to label by eye.

The detectors are checked against race-control labels, but most real mistakes carry no label,
so the plan's check is a human one: look at the top findings `find_mistakes` would show and
say, for each, whether it is a real driver mistake. The share that are is the human-checked
precision.

Which findings: the listed mistakes of the test year (2026), one per moment (`merged_into`),
in `find_mistakes`' ranking (`priority`). The tool answers per session, so the queue takes at
most MAX_PER_SESSION per session and one per driver per session; otherwise one wet race or one
driver's bad afternoon fills it.

2026 is the test year for the detectors (nothing about them was fitted or chosen on it), but
not fully for the explanation rules: a few 2026 flags were looked at by eye while the rules
were built and fixed, and some calibrations pooled 2022-2026. A first queue was also read by
Claude before any human answer (report/m4_review_preread_v1.csv); it showed that most top race
findings were cars being lapped, which led to the traffic rule in `inference/explain.py`. So
the laps known to have been looked at during development are DEVELOPMENT_LAPS plus that
queue's, and `score` reports the precision with and without them.

    uv run python scripts/review_queue.py build [--n 50]        # -> data/results/review/queue.json
    uv run python scripts/review_queue.py score labels.json     # -> report/m4_review.md

`build` also writes report/m4_review_queue.csv, the list itself. `score` reads the labels as
exported from the review page ({id: {verdict, note}}) plus, optionally, a second reader's
verdicts on the same findings (--second, same shape) for the agreement between the two.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from race_engineer.config import FASTF1_CACHE, GRID_STEP_M, REPORT_DIR, RESULTS_DIR
from race_engineer.data import store
from race_engineer.data.qa import markdown_table
from race_engineer.features.handcrafted import DISTANCE
from race_engineer.inference.explain import (
    NOT_MISTAKES,
    PHASE_EDGES_M,
    TYPE_TEXT,
    compare,
    load_session_segments,
    turn_label,
)
from race_engineer.inference.provenance import check_current

TEST_YEAR = 2026
MAX_PER_SESSION = 2
REVIEW_DIR = RESULTS_DIR / "review"
SESSION_NAMES = {"Q": "Qualifying", "R": "Race", "S": "Sprint", "SQ": "Sprint Qualifying"}
VERDICTS = ("real", "not", "unsure")
# 2026 laps looked at while the explanation rules were built or fixed (year_round_session_
# car_lap, the start of their segment ids): the data checks' examples, the known incidents,
# the cross-review's worked examples and the cases named in the two reviews of the traffic
# rule.
DEVELOPMENT_LAPS = frozenset({
    "2026_01_Q_43_2", "2026_01_R_11_31", "2026_01_R_55_47", "2026_02_R_43_16",
    "2026_04_Q_5_2", "2026_05_R_18_8", "2026_05_R_18_22", "2026_05_R_44_22", "2026_05_R_55_66",
    "2026_05_SQ_44_10", "2026_05_S_31_19", "2026_05_S_44_22", "2026_08_R_12_2",
    "2026_08_R_23_64", "2026_09_R_31_30", "2026_12_R_22_43", "2026_12_S_11_15",
    "2026_13_R_12_49", "2026_14_R_22_44",
    # The traffic reviews.
    "2026_03_R_12_53", "2026_04_R_11_12", "2026_04_S_12_7", "2026_05_S_6_6", "2026_06_Q_3_16",
    "2026_07_R_5_2", "2026_08_R_44_11", "2026_10_R_14_29", "2026_12_R_11_59",
    "2026_13_R_77_49", "2026_14_R_77_48",
})  # fmt: skip
PREREAD = REPORT_DIR / "m4_review_preread_v1.csv"


def development_laps() -> frozenset[str]:
    """DEVELOPMENT_LAPS plus the laps of the first queue Claude read (see the docstring)."""
    if not PREREAD.exists():
        return DEVELOPMENT_LAPS
    ids = pd.read_csv(PREREAD, usecols=["id"])["id"].astype(str)
    return DEVELOPMENT_LAPS | {i.rsplit("_", 1)[0] for i in ids}


def select(mistakes: pd.DataFrame, n: int) -> pd.DataFrame:
    """The review queue: see the module docstring."""
    listed = mistakes[
        (mistakes["year"] == TEST_YEAR)
        & ~mistakes["mistake_type"].isin(NOT_MISTAKES)
        & (mistakes["merged_into"] == mistakes["segment_id"])
    ].sort_values(["priority", "segment_id"], ascending=[False, True])
    picked, per_session, seen = [], {}, set()
    for i, row in listed.iterrows():
        session = (row["round"], row["session"])
        if per_session.get(session, 0) >= MAX_PER_SESSION or (*session, row["driver"]) in seen:
            continue
        picked.append(i)
        per_session[session] = per_session.get(session, 0) + 1
        seen.add((*session, row["driver"]))
        if len(picked) == n:
            break
    return listed.loc[picked].reset_index(drop=True)


def _round(values: np.ndarray, digits: int) -> list[float | None]:
    return [None if not np.isfinite(v) else round(float(v), digits) for v in values]


TRACK_STATUS = {
    "2": "yellow flag",
    "4": "safety car",
    "5": "red flag",
    "6": "VSC",
    "7": "VSC ending",
}
LAP_WINDOW = 5  # in races, the driver's usual lap time is the median of racing laps this close
# Scored laps are always green (yellow and neutralised laps aren't eligible), so the flags
# worth showing are on the laps around it: a safety car on the next lap explains a slow-down.
STATUS_LAPS = (-1, 1, 2)
# In qualifying, race control counts laps one higher than FastF1's lap numbers, so its
# "TRACK LIMITS AT TURN 7 LAP 3" is about lap 2; the lap count is dropped there.
RC_LAP = re.compile(r"\s+LAP\s+\d+\s*$")
DELETED_TURN = re.compile(r"TRACK LIMITS AT TURN (\d+)")


def race_control_text(text: str, is_race: bool) -> str:
    return text if is_race else RC_LAP.sub("", text)


def lap_context(key: str, driver: str, lap: int, is_race: bool) -> str:
    """This lap in plain words: its time against the driver's usual, pit stops nearby,
    positions, flags and the gap to the car ahead (the context a reviewer needs to tell a
    mistake from a problem, a slow-down or traffic)."""
    everyone = pd.read_parquet(store.table_path("laps", key))
    laps = everyone[everyone["driver"] == driver].set_index("lap_number").sort_index()
    if lap not in laps.index:
        return ""
    this = laps.loc[lap]
    parts = []
    if is_race:
        near = laps[(laps["lap_class"] == "race") & (abs(laps.index - lap) <= LAP_WINDOW)]
        near = near.drop(index=lap, errors="ignore")
        usual = float(near["lap_time_s"].median()) if len(near) else float("nan")
        usual_text = "their usual nearby"
    else:
        push = laps[(laps["lap_class"] == "push") & ~laps["deleted"].astype(bool)]
        usual = float(push["lap_time_s"].min()) if len(push) else float("nan")
        usual_text = "their best lap"
    lap_time = float(laps["lap_time_s"].get(lap, np.nan))
    if np.isfinite(lap_time) and np.isfinite(usual):
        parts.append(
            f"lap {lap_time:.1f} s, {lap_time - usual:+.1f} s against {usual_text} ({usual:.1f} s)"
        )
    stops = [(n, "in") for n in laps.index[laps["pit_in"].astype(bool)]]
    stops += [(n, "out") for n in laps.index[laps["pit_out"].astype(bool)]]
    pits = [f"{way} on lap {n}" for n, way in sorted(stops) if abs(n - lap) <= 2]
    if pits:
        parts.append("pit " + ", ".join(pits))
    if is_race:
        before = laps["position"].get(lap - 1, np.nan)
        after = laps["position"].get(lap, np.nan)
        if np.isfinite(before) and np.isfinite(after):
            parts.append(f"P{int(before)} to P{int(after)}")
        if lap == laps.index.max():
            parts.append(
                f"their last recorded lap (the session ran {int(everyone['lap_number'].max())})"
            )
        gap = this["gap_ahead_s"]
        if np.isfinite(gap):
            parts.append(f"{gap:.1f} s behind the car ahead at the line")
    status = laps["track_status"].astype(str)
    for offset in STATUS_LAPS:
        flags = [
            TRACK_STATUS[c] for c in sorted(set(status.get(lap + offset, ""))) if c in TRACK_STATUS
        ]
        if flags:
            parts.append(f"{', '.join(flags)} on lap {lap + offset}")
    if bool(this["deleted"]):
        reason = race_control_text(str(this["deleted_reason"]), is_race)
        parts.append(f"lap deleted ({reason})")
    return "; ".join(parts)


def deleted_at(key: str, driver: str, lap: int) -> str | None:
    """The turn the lap was deleted for (track limits), from the laps table; None if not."""
    laps = pd.read_parquet(
        store.table_path("laps", key), columns=["driver", "lap_number", "deleted", "deleted_reason"]
    )
    hit = laps[
        (laps["driver"] == driver) & (laps["lap_number"] == lap) & laps["deleted"].astype(bool)
    ]
    if hit.empty:
        return None
    turn = DELETED_TURN.search(str(hit["deleted_reason"].iloc[0]).upper())
    return turn.group(1) if turn else None


def _clock(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# The track map on each card (see track_map and nearby_cars).
MAP_SIZE = 300  # the map's viewBox, px
MAP_PAD = 22  # room for the turn numbers at the edges
MAP_STEP_M = 20.0  # the outline's resolution
LABEL_OFFSET_M = 70.0  # turn numbers sit this far from their marker, as on the TV graphics
NEARBY_S = 5.0  # cars within this many seconds of the corner at that moment are drawn
ROTATIONS = REVIEW_DIR / "rotations.json"
RACE_SESSIONS = ("R", "S")
# The corner replay on each card: from REPLAY_BEFORE_M before the apex to REPLAY_AFTER_M after
# it (a little more than the analysed window, so a pass around it shows), in frames of
# REPLAY_DT_S; other cars within REPLAY_NEAR_M of the car at some frame are drawn.
REPLAY_BEFORE_M = 400.0
REPLAY_AFTER_M = 300.0
REPLAY_DT_S = 0.2
REPLAY_MAX_S = 45.0
REPLAY_NEAR_M = 300.0
REPLAY_ZOOM_PAD = 18.0  # px around the replayed stretch in the zoomed view
# Qualifying parts are separated by breaks with nobody starting a lap (Q1-Q2 and Q2-Q3 are
# 7-8 minutes); longer than any quiet spell within a part, shorter than the breaks.
QUALI_BREAK_S = 300.0


def circuit_rotation(year: int, round_: int) -> float:
    """The angle FastF1 turns the circuit by to match the official track maps (as on TV),
    read once from the FastF1 cache and kept in ROTATIONS."""
    cache = json.loads(ROTATIONS.read_text()) if ROTATIONS.exists() else {}
    key = f"{year}_{round_:02d}"
    if key not in cache:
        import logging

        import fastf1

        logging.getLogger("fastf1").setLevel(logging.ERROR)
        fastf1.Cache.enable_cache(str(FASTF1_CACHE))
        fastf1.Cache.offline_mode(True)
        try:
            session = fastf1.get_session(year, round_, "R")  # the rotation is the circuit's
            session.load(laps=True, telemetry=False, weather=False, messages=False)
            info = session.get_circuit_info()
            cache[key] = float(info.rotation) if info is not None else 0.0
        except Exception:  # not in the cache: north up is still a usable map
            cache[key] = 0.0
        ROTATIONS.parent.mkdir(parents=True, exist_ok=True)
        ROTATIONS.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return cache[key]


class MapFrame:
    """Track coordinates (m) -> the map's pixels: rotated like the official map, north of
    the rotated frame up, scaled so the longer side is MAP_SIZE (`width` x `height`)."""

    def __init__(self, x: np.ndarray, y: np.ndarray, rotation_deg: float) -> None:
        a = math.radians(rotation_deg)
        self.cos, self.sin = math.cos(a), math.sin(a)
        rx, ry = self._rotate(x, y)
        self.x0, self.y1 = float(rx.min()), float(ry.max())
        span_x, span_y = float(rx.max()) - self.x0, self.y1 - float(ry.min())
        self.scale = (MAP_SIZE - 2 * MAP_PAD) / max(span_x, span_y)
        self.width = round(2 * MAP_PAD + span_x * self.scale)
        self.height = round(2 * MAP_PAD + span_y * self.scale)

    def _rotate(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return x * self.cos - y * self.sin, x * self.sin + y * self.cos

    def __call__(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        rx, ry = self._rotate(np.asarray(x, float), np.asarray(y, float))
        return (
            MAP_PAD + (rx - self.x0) * self.scale,
            MAP_PAD + (self.y1 - ry) * self.scale,
        )


def track_map(key: str, rotation_deg: float) -> tuple[dict, MapFrame, np.ndarray, np.ndarray]:
    """The session's track for the map: the outline (every MAP_STEP_M), the turn numbers
    placed as FastF1 places them, and the start/finish line. Also returns the frame and the
    reference line (x, y on the 5 m grid) for placing cars."""
    track = pd.read_parquet(store.table_path("tracks", key)).iloc[0]
    x, y = np.asarray(track["x_m"], float), np.asarray(track["y_m"], float)
    frame = MapFrame(x, y, rotation_deg)
    step = max(1, round(MAP_STEP_M / GRID_STEP_M))
    ox, oy = frame(x[::step], y[::step])
    corners = pd.read_parquet(store.table_path("corners", key))
    cx, cy = corners["x_m"].to_numpy(float), corners["y_m"].to_numpy(float)
    # FastF1 gives each turn number a direction to sit in (no direction: on the marker).
    angle = np.radians(corners["angle"].to_numpy(float))
    has = np.isfinite(angle)
    lx = np.where(has, cx + LABEL_OFFSET_M * np.cos(np.where(has, angle, 0)), cx)
    ly = np.where(has, cy + LABEL_OFFSET_M * np.sin(np.where(has, angle, 0)), cy)
    mx, my = frame(cx, cy)
    tx, ty = frame(lx, ly)
    labels = [
        turn_label(int(n), str(letter or ""))
        for n, letter in zip(corners["number"], corners["letter"], strict=True)
    ]
    turns = [
        {"label": label, "x": round(float(a), 1), "y": round(float(b), 1),
         "tx": round(float(c), 1), "ty": round(float(d), 1)}
        for label, a, b, c, d in zip(labels, mx, my, tx, ty, strict=True)
    ]  # fmt: skip
    return (
        {
            "outline": [
                [round(float(a), 1), round(float(b), 1)] for a, b in zip(ox, oy, strict=True)
            ],
            "step_m": step * GRID_STEP_M,
            "width": frame.width,
            "height": frame.height,
            "length_m": float(track["length_m"]),
            "turns": turns,
        },
        frame,
        x,
        y,
    )


def _grid_clock(key: str) -> pd.DataFrame:
    """Every lap's session time at each point of the 5 m grid (lap start + time into the lap),
    with its driver, lap number and lap class."""
    grids = pd.read_parquet(
        store.table_path("lap_grids", key), columns=["lap_id", "driver", "lap_number", "time_s"]
    )
    laps = pd.read_parquet(
        store.table_path("laps", key), columns=["lap_id", "lap_start_s", "lap_class"]
    )
    grids = grids.merge(laps, on="lap_id", how="inner")
    grids["clock"] = [
        start + np.asarray(t, float)
        for start, t in zip(grids["lap_start_s"], grids["time_s"], strict=True)
    ]
    return grids.drop(columns="time_s")


def apex_moment(clock: pd.DataFrame, row: pd.Series) -> float:
    """Session time when the finding's car was at the apex."""
    lap = clock[clock["lap_id"] == row["lap_id"]]
    if lap.empty:
        return math.nan
    at = lap["clock"].iloc[0]
    return float(at[min(round(float(row["apex_m"]) / GRID_STEP_M), len(at) - 1)])


def nearby_cars(
    clock: pd.DataFrame,
    row: pd.Series,
    t_apex: float,
    frame: MapFrame,
    x: np.ndarray,
    y: np.ndarray,
) -> list[dict]:
    """The other cars on the map at the moment the finding's car reached the apex: where
    they were, how far behind (+) or ahead (-) in time at that point of the track, and how
    many laps up (+) or down (-) on the finding's car they were."""
    if not np.isfinite(t_apex):
        return []
    n = len(x)
    apex_i = min(round(float(row["apex_m"]) / GRID_STEP_M), n - 1)
    me = float(row["lap_number"]) + apex_i / n
    out = []
    for driver, laps in clock[clock["driver"] != row["driver"]].groupby("driver"):
        best = None
        for lap in laps.itertuples():
            at = lap.clock
            ok = np.isfinite(at)
            if not ok.any():
                continue
            # When this lap of theirs passed the finding's apex.
            if ok[apex_i]:
                gap = float(at[apex_i]) - t_apex
                if abs(gap) <= NEARBY_S and (best is None or abs(gap) < abs(best[0])):
                    best = (gap, lap)
        if best is None:
            continue
        gap, lap = best
        note = f"{lap.lap_class}-lap" if lap.lap_class in ("in", "out") else ""
        # Where they were at t_apex: on this lap or the one next to it.
        where = None
        for other in laps.itertuples():
            at = other.clock
            ok = np.isfinite(at)
            if ok.sum() > 1 and at[ok][0] <= t_apex <= at[ok][-1]:
                where = (float(np.interp(t_apex, at[ok], np.flatnonzero(ok))), other.lap_number)
                break
        if where is None:
            continue
        i, lap_number = where
        k = min(round(i), n - 1)
        px, py = frame(x[k], y[k])
        # Laps up or down only mean something in a race (qualifying laps are separate runs).
        laps_up = round(float(lap_number) + i / n - me) if row["session"] in RACE_SESSIONS else 0
        out.append(
            {"driver": str(driver), "gap_s": round(gap, 1), "laps": int(laps_up), "note": note,
             "x": round(float(px), 1), "y": round(float(py), 1)}
        )  # fmt: skip
    return sorted(out, key=lambda c: c["gap_s"])


def driver_runs(clock: pd.DataFrame, n: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Each driver's whole session as one run: distance since the start of their lap 1 (in
    grid points, lap k's point i at (k - 1) * n + i) against session time, so a pass over the
    line or from one lap into the next is just further along the run."""
    runs = {}
    for driver, laps in clock.sort_values("lap_number").groupby("driver"):
        dist, time = [], []
        for lap_number, at in zip(laps["lap_number"], laps["clock"], strict=True):
            at = np.asarray(at, float)
            dist.append((int(lap_number) - 1) * n + np.arange(len(at)))
            time.append(at)
        d, t = np.concatenate(dist).astype(float), np.concatenate(time)
        ok = np.isfinite(t)
        d, t = d[ok], t[ok]
        keep = np.concatenate([[True], np.diff(t) > 0])  # strictly increasing in time
        runs[str(driver)] = (d[keep], t[keep])
    return runs


def _where(dist: np.ndarray, x: np.ndarray, y: np.ndarray, frame: MapFrame) -> np.ndarray:
    """Map pixels of run distances (grid points, any lap), between grid points by
    interpolation; NaN distances give NaN."""
    n = len(x)
    ok = np.isfinite(dist)
    i = np.where(ok, dist, 0) % n
    lo = np.floor(i).astype(int)
    hi = (lo + 1) % n
    w = i - lo
    px, py = frame(x[lo] * (1 - w) + x[hi] * w, y[lo] * (1 - w) + y[hi] * w)
    return np.where(ok[:, None], np.column_stack([px, py]), np.nan)


def _points(xy: np.ndarray) -> list[list[float] | None]:
    return [
        None if not np.isfinite(p).all() else [round(float(p[0]), 1), round(float(p[1]), 1)]
        for p in xy
    ]


def corner_replay(
    runs: dict[str, tuple[np.ndarray, np.ndarray]],
    row: pd.Series,
    ghost: pd.Series | None,
    frame: MapFrame,
    x: np.ndarray,
    y: np.ndarray,
) -> dict | None:
    """The corner as an animation: where the car, a ghost of the usual lap (starting level
    with it) and the cars around it were, every REPLAY_DT_S from REPLAY_BEFORE_M before the
    apex to REPLAY_AFTER_M after it, in map pixels; the car's distance from the apex per frame
    (for the charts' playhead); and the zoomed view's box."""
    n = len(x)
    step = GRID_STEP_M
    apex_i = float(row["apex_m"]) / step
    me = runs.get(str(row["driver"]))
    if me is None:
        return None
    d_me, t_me = me
    s_apex = (int(row["lap_number"]) - 1) * n + apex_i
    s0, s1 = s_apex - REPLAY_BEFORE_M / step, s_apex + REPLAY_AFTER_M / step
    if s0 < d_me[0] or s1 > d_me[-1]:
        return None
    t0, t1 = float(np.interp(s0, d_me, t_me)), float(np.interp(s1, d_me, t_me))
    taus = np.arange(0.0, min(t1 - t0, REPLAY_MAX_S) + 1e-9, REPLAY_DT_S)
    at = t0 + taus
    s_me = np.interp(at, t_me, d_me)
    out: dict = {
        "dt": REPLAY_DT_S,
        "me": _points(_where(s_me, x, y, frame)),
        "dist": [round((s - s_apex) * step, 1) for s in s_me],
    }
    if ghost is not None and str(ghost["driver"]) in runs:
        d_g, t_g = runs[str(ghost["driver"])]
        g_apex = (int(ghost["lap_number"]) - 1) * n + apex_i
        if d_g[0] <= g_apex - REPLAY_BEFORE_M / step and g_apex + REPLAY_AFTER_M / step <= d_g[-1]:
            g0 = float(np.interp(g_apex - REPLAY_BEFORE_M / step, d_g, t_g))
            s_g = np.interp(g0 + taus, t_g, d_g, right=np.nan)
            out["ghost"] = _points(_where(s_g, x, y, frame))
            out["ghost_label"] = f"{ghost['driver']} lap {int(ghost['lap_number'])}"
    cars = []
    for driver, (d_b, t_b) in runs.items():
        if driver == str(row["driver"]):
            continue
        s_b = np.interp(at, t_b, d_b, left=np.nan, right=np.nan)
        if not np.isfinite(s_b).any():
            continue
        # Track distance between them, either way round the lap.
        gap = ((s_b - s_me) % n + n / 2) % n - n / 2
        if not (np.abs(gap[np.isfinite(gap)]) * step <= REPLAY_NEAR_M).any():
            continue
        laps = 0
        if row["session"] in RACE_SESSIONS:
            mid = len(at) // 2
            if np.isfinite(s_b[mid]):
                laps = round((s_b[mid] - s_me[mid] - gap[mid]) / n)
        cars.append({"driver": driver, "laps": int(laps), "pts": _points(_where(s_b, x, y, frame))})
    out["cars"] = cars
    # The zoomed view: the replayed stretch with a margin, square-ish so turns aren't squashed.
    stretch = _where(np.linspace(s0, s1, 60), x, y, frame)
    lo, hi = np.nanmin(stretch, axis=0), np.nanmax(stretch, axis=0)
    side = max(float((hi - lo).max()) + 2 * REPLAY_ZOOM_PAD, 60.0)
    centre = (lo + hi) / 2
    out["box"] = [
        round(float(centre[0] - side / 2), 1),
        round(float(centre[1] - side / 2), 1),
        round(side, 1),
        round(side, 1),
    ]
    return out


def ghost_lap(seg, cmp) -> pd.Series | None:
    """The reference lap whose time through the corner is closest to the reference's median:
    a real lap standing for "the usual" in the replay."""
    ref = cmp.reference_rows
    if not len(ref):
        return None
    times = seg.frame["segment_time_s"].to_numpy(float)[ref]
    pick = ref[int(np.nanargmin(np.abs(times - np.nanmedian(times))))]
    return seg.frame.iloc[int(pick)]


def quali_part(laps: pd.DataFrame, t: float) -> str:
    """ "Q2, about 6:30 after its first car went out", from the breaks between the parts (see
    QUALI_BREAK_S); "" when the session doesn't split into three (a red flag, for one)."""
    starts = np.sort(laps["lap_start_s"].dropna().unique())
    if not len(starts) or not np.isfinite(t):
        return ""
    edges = [starts[0], *starts[1:][np.diff(starts) > QUALI_BREAK_S]]
    if len(edges) != 3:
        return ""
    part = int(np.searchsorted(edges, t, side="right"))
    return f"Q{part}, about {_clock(t - edges[part - 1])} after its first car went out"


def replay_hint(key: str, row: pd.Series, is_race: bool, clock: pd.DataFrame) -> str:
    """Where to find the corner in a session replay (e.g. F1 TV's onboard feeds). In races:
    the lap counter the broadcast shows (the leader's lap, not the driver's) and how long
    after it changes the car reaches the turn. A replay's own clock also counts the build-up
    before the start, so a time since lights out doesn't match it; the counter does. In
    qualifying: which part and how far into it, or else the time since the first car left
    the pits."""
    turn = turn_label(int(row["corner"]), str(row["corner_letter"]))
    where = f"turn {turn}, {row['driver']}'s onboard"
    t = apex_moment(clock, row)
    if not np.isfinite(t):
        return f"their lap {int(row['lap_number'])}, {where}"
    # Lap counts and starts from the laps table: some laps have no position grid.
    laps = pd.read_parquet(
        store.table_path("laps", key), columns=["driver", "lap_number", "lap_start_s"]
    )
    if is_race:
        leader = int(laps["lap_number"][laps["lap_start_s"] <= t].max())
        total = int(laps["lap_number"].max())
        # The counter changes when the leader starts the lap: the first start of that lap.
        changed = float(laps["lap_start_s"][laps["lap_number"] == leader].min())
        return (
            f"TV lap counter {leader}/{total}, {_clock(t - changed)} after it changes to "
            f"{leader} ({row['driver']} on their lap {int(row['lap_number'])}), {where}"
        )
    part = quali_part(laps, t)
    own = int(laps["lap_number"][laps["driver"] == row["driver"]].max())
    first = float(laps["lap_start_s"].min())
    when = part or (
        f"about {_clock(t - first)} after the first car went out" if np.isfinite(first) else ""
    )
    return "; ".join(
        x for x in (when, f"their lap {int(row['lap_number'])} of {own}, {where}") if x
    )


def session_rank(scores: pd.DataFrame, row: pd.Series) -> tuple[int, int]:
    """The flag's rank by combined score among its session's scored corners, and their count."""
    session = scores[
        (scores["year"] == row["year"])
        & (scores["round"] == row["round"])
        & (scores["session"] == row["session"])
    ]
    return int((session["score"] > row["score"]).sum()) + 1, len(session)


def finding(row: pd.Series, rank: int, scores: pd.DataFrame, tracks: dict) -> dict:
    """One queue entry: what the page shows, with this lap's traces against the usual. Adds
    the session's track map to `tracks` (shared by its findings)."""
    key = store.session_key(int(row["year"]), int(row["round"]), str(row["session"]))
    if key not in tracks:
        rotation = circuit_rotation(int(row["year"]), int(row["round"]))
        outline, frame, ref_x, ref_y = track_map(key, rotation)
        tracks[key] = {
            "data": outline,
            "frame": frame,
            "x": ref_x,
            "y": ref_y,
            "clock": _grid_clock(key),
        }
        tracks[key]["runs"] = driver_runs(tracks[key]["clock"], len(ref_x))
    frame, ref_x, ref_y = tracks[key]["frame"], tracks[key]["x"], tracks[key]["y"]
    clock = tracks[key]["clock"]
    seg = load_session_segments(key)
    at = np.flatnonzero(seg.frame["segment_id"].to_numpy() == row["segment_id"])
    cmp = compare(seg, int(at[0]))
    ref = cmp.reference_rows

    def channel(name: str, digits: int, band: bool = True) -> dict[str, list[float | None]]:
        values = seg.traces[name]
        out = {
            name: _round(values[cmp.row], digits),
            f"{name}_ref": _round(cmp.usual_trace[name], digits),
        }
        if band:
            lo, hi = np.percentile(values[ref], [25, 75], axis=0)
            out |= {f"{name}_lo": _round(lo, digits), f"{name}_hi": _round(hi, digits)}
        return out

    race_control = race_control_text(str(row["race_control"]), seg.is_race)
    in_session, of = session_rank(scores, row)
    turn = turn_label(int(row["corner"]), str(row["corner_letter"]))
    # The moment's turns: its own and the neighbouring ones merged into it. A deletion for
    # track limits at any of them belongs to this finding.
    turns = {turn, *(t.strip() for t in str(row["also_turns"]).split(",") if t.strip())}
    deleted_turn = deleted_at(key, str(row["driver"]), int(row["lap_number"]))
    return {
        "id": str(row["segment_id"]),
        "rank": rank,
        "year": int(row["year"]),
        "round": int(row["round"]),
        "event": str(row["event"]),
        "event_short": str(row["location"]),
        "session": str(row["session"]),
        "session_name": SESSION_NAMES.get(str(row["session"]), str(row["session"])),
        "driver": str(row["driver"]),
        "driver_name": str(row["driver"]),
        "team": str(row["team"]),
        "lap": int(row["lap_number"]),
        "corner_label": turn,
        "also_turns": str(row["also_turns"]),
        "mistake_type": str(row["mistake_type"]),
        "type_label": TYPE_TEXT.get(str(row["mistake_type"]), str(row["mistake_type"])),
        "secondary_label": TYPE_TEXT.get(str(row["secondary_type"]), "")
        if row["secondary_type"]
        else "",
        "track_limits": "TRACK LIMITS" in race_control.upper()
        or (deleted_turn is not None and deleted_turn in {re.sub(r"\D", "", t) for t in turns}),
        "race_control": race_control,
        "deleted": bool(row["deleted"]),
        "context": str(row["context"]),
        "explanation": str(row["explanation"]),
        "time_lost_s": round(float(row["time_lost_s"]), 3),
        "priority": round(float(row["priority"]), 3),
        "phase": str(row["phase"]),
        "loss_by_phase_s": _round(
            np.array([row["loss_entry_s"], row["loss_apex_s"], row["loss_exit_s"]], float), 3
        ),
        "dv_min": round(float(row["delta_v_min"]), 1) if np.isfinite(row["delta_v_min"]) else None,
        "dv_exit": round(float(row["delta_v_exit"]), 1)
        if np.isfinite(row["delta_v_exit"])
        else None,
        "score_pct": round(float(row["score_pct"]), 5),
        "session_rank": in_session,
        "session_corners": of,
        "lap_context": lap_context(key, str(row["driver"]), int(row["lap_number"]), seg.is_race),
        "replay": replay_hint(key, row, seg.is_race, clock),
        "map": {
            "track": key,
            "from_m": float(row["apex_m"]) + float(DISTANCE[0]),
            "to_m": float(row["apex_m"]) + float(DISTANCE[-1]),
            "apex_m": float(row["apex_m"]),
            "cars": nearby_cars(clock, row, apex_moment(clock, row), frame, ref_x, ref_y),
        },
        "anim": corner_replay(tracks[key]["runs"], row, ghost_lap(seg, cmp), frame, ref_x, ref_y),
        "ref_laps": int(row["n_reference"]),
        "ref_kind": str(row["reference"]),
        "inspected": str(row["segment_id"]).rsplit("_", 1)[0] in development_laps(),
        "phase_edges": list(PHASE_EDGES_M),
        "traces": {
            **channel("speed", 1),
            **channel("throttle", 0),
            **channel("brake", 2, band=False),
            "delta": _round(cmp.delta_trace_s, 3),
        },
    }


def build(n: int) -> Path:
    check_current(RESULTS_DIR / "mistakes.parquet", "explain")
    mistakes = pd.read_parquet(RESULTS_DIR / "mistakes.parquet")
    queue = select(mistakes, n)
    scores = pd.read_parquet(
        RESULTS_DIR / "segment_scores.parquet",
        columns=["year", "round", "session", "score"],
        filters=[("year", "=", TEST_YEAR)],
    )
    tracks: dict = {}
    findings = [
        finding(row, rank, scores, tracks)
        for rank, (_, row) in enumerate(queue.iterrows(), start=1)
    ]
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = REVIEW_DIR / "queue.json"
    data = {
        "distance_m": DISTANCE.tolist(),
        "tracks": {key: t["data"] for key, t in tracks.items()},
        "findings": findings,
    }
    out.write_text(json.dumps(data))
    columns = ["rank", "id", "event", "session", "driver", "team", "lap", "corner_label",
               "type_label", "time_lost_s", "priority", "track_limits", "ref_kind",
               "inspected"]  # fmt: skip
    pd.DataFrame(findings)[columns].to_csv(REPORT_DIR / "m4_review_queue.csv", index=False)
    sessions = queue.groupby(["round", "session"]).ngroups
    print(f"{len(findings)} findings from {sessions} sessions -> {out}")
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for k of n."""
    if n == 0:
        return math.nan, math.nan
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def cohen_kappa(a: list[str], b: list[str]) -> float:
    labels = sorted(set(a) | set(b))
    if len(a) == 0:
        return math.nan
    observed = np.mean([x == y for x, y in zip(a, b, strict=True)])
    expected = sum((a.count(c) / len(a)) * (b.count(c) / len(b)) for c in labels)
    return float((observed - expected) / (1 - expected)) if expected < 1 else math.nan


def _precision_row(name: str, verdicts: pd.Series) -> dict:
    real, wrong = int((verdicts == "real").sum()), int((verdicts == "not").sum())
    lo, hi = wilson(real, real + wrong)
    return {
        "findings": name,
        "reviewed": len(verdicts),
        "mistake": real,
        "not a mistake": wrong,
        "can't tell": int((verdicts == "unsure").sum()),
        "precision [95% CI]": (
            f"{real / (real + wrong):.0%} [{lo:.0%}, {hi:.0%}]" if real + wrong else "n/a"
        ),
    }


def _verdicts(raw: dict) -> dict[str, str]:
    """{id: verdict} from an export ({id: {verdict, note}}), keeping only real answers."""
    out = {}
    for key, value in raw.items():
        verdict = value.get("verdict") if isinstance(value, dict) else None
        if verdict in VERDICTS:
            out[str(key)] = verdict
    return out


def score(labels_path: Path, second_path: Path | None, out: Path) -> Path:
    queue = pd.DataFrame(json.loads((REVIEW_DIR / "queue.json").read_text())["findings"])
    raw = json.loads(labels_path.read_text())
    verdicts = _verdicts(raw)
    queue["verdict"] = queue["id"].map(verdicts)
    queue["note"] = queue["id"].map(
        lambda i: str((raw.get(i) or {}).get("note") or "") if isinstance(raw.get(i), dict) else ""
    )
    queue["video"] = queue["id"].map(
        lambda i: bool((raw.get(i) or {}).get("video")) if isinstance(raw.get(i), dict) else False
    )
    done = queue[queue["verdict"].isin(VERDICTS)]
    parts = [
        "# M4: human-checked precision of the top findings",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `scripts/review_queue.py score`._",
        f"The top {len(queue)} findings of {TEST_YEAR}, the test year, in `find_mistakes`' "
        f"ranking with at most {MAX_PER_SESSION} per session and one per driver per session "
        "(the list: `m4_review_queue.csv`), to be looked at by eye: this lap's speed, throttle "
        "and brake against the driver's usual (the field's when they had fewer than 5 other "
        "clean laps there), the running time lost, the lap around it and the explanation. The "
        "question is whether it is a real driver mistake (time lost to the driver's own "
        "driving at that corner), not whether the explanation is worded well. Some answers "
        'were also checked against a session replay ("checked on video"). Precision counts '
        '"can\'t tell" as neither.',
        f"**{len(done)} of {len(queue)} reviewed.**",
    ]
    if done.empty:
        out.write_text("\n\n".join(parts) + "\n")
        print(f"wrote {out} (no answers yet)")
        return out
    rows = [_precision_row("all", done["verdict"])]
    for top in (10, 25):
        rows.append(_precision_row(f"top {top}", done.loc[done["rank"] <= top, "verdict"]))
    rows.append(
        _precision_row(
            "not inspected while building the rules", done.loc[~done["inspected"], "verdict"]
        )
    )
    for kind, part in done.groupby("session"):
        rows.append(
            _precision_row(f"{SESSION_NAMES.get(str(kind), kind)} sessions", part["verdict"])
        )
    if done["video"].any():
        rows.append(_precision_row("checked on video", done.loc[done["video"], "verdict"]))
        rows.append(_precision_row("from the traces only", done.loc[~done["video"], "verdict"]))
    for kind, part in done.groupby("ref_kind"):
        name = "against the driver's usual" if kind == "driver" else f"against the {kind}"
        rows.append(_precision_row(name, part["verdict"]))
    by_type = [_precision_row(str(t), part["verdict"]) for t, part in done.groupby("type_label")]
    inspected = int(done["inspected"].sum())
    parts += [
        markdown_table(pd.DataFrame(rows)),
        f"{inspected} of the reviewed findings are on laps looked at while the explanation "
        "rules were built or fixed (`DEVELOPMENT_LAPS` and the first queue Claude read, "
        "`m4_review_preread_v1.csv`); the row without them is the cleaner estimate.",
        "By the explanation's mistake type:",
        markdown_table(pd.DataFrame(by_type)),
    ]
    if second_path is not None:
        second = _verdicts(json.loads(second_path.read_text()))
        both = done[done["id"].map(lambda i: i in second)]
        a = both["verdict"].tolist()
        b = [second[i] for i in both["id"]]
        if a:
            agree = float(np.mean([x == y for x, y in zip(a, b, strict=True)]))
            kappa = cohen_kappa(a, b)
            kappa_text = f"Cohen's kappa {kappa:.2f}" if np.isfinite(kappa) else "kappa undefined"
            parts.append(
                f"A second reader (Claude, shown only after each answer) agreed on {agree:.0%} "
                f"of {len(a)} findings ({kappa_text})."
            )
    notes = done[done["note"].fillna("").str.strip() != ""]
    if len(notes):
        table = notes[
            ["rank", "driver", "event", "session", "lap", "corner_label", "verdict", "note"]
        ]
        parts += ["Reviewer notes:", markdown_table(table)]
    out.write_text("\n\n".join(parts) + "\n")
    print(f"wrote {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="The M4 manual review queue.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--n", type=int, default=50)
    s = sub.add_parser("score")
    s.add_argument("labels", type=Path)
    s.add_argument("--second", type=Path, default=None)
    s.add_argument("--out", type=Path, default=REPORT_DIR / "m4_review.md")
    args = parser.parse_args()
    if args.command == "build":
        build(args.n)
    else:
        score(args.labels, args.second, args.out)


if __name__ == "__main__":
    main()
