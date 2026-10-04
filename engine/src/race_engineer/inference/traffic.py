"""Traffic at a corner: the cars around the driver, who passed whom and how close they were.

Where each car was
------------------
Each lap's grid (`data/processed/lap_grids`) has the time since the lap started at every 5 m of
the track's reference line, and the laps table the session time the lap started, so a car was
at distance d of lap L at session time lap_start_s(L) + time_s(L)[d]. Laid end to end, a car's
laps give its total distance (metres from the start of its lap 1) against session time, which
runs across the start/finish line without a seam: a corner near the line reaches into the
previous or the next lap. A car has no position where it was not racing on the track line:

- in the pit lane: the start of an out-lap up to the pit exit and the end of an in-lap from the
  pit entry. On a lap with a grid that is the run at the pit speed limit (up to PIT_SPEED_KPH)
  that opens an out-lap or closes an in-lap, plus PIT_MARGIN_M for the exit and entry lanes,
  or further while the car is still more than PIT_OFFSET_M off the reference line next to it:
  some exit and entry lanes run beside the track for longer than that (at Yas Marina the exit
  lane rejoins about 850 m after the line, 18-28 m off it before), and a car there placed on
  the track line would be read as passing or being passed;
- at grid points marked not valid (a feed failure), except that a hole of up to MAX_HOLE_M
  between valid points is bridged, as the grid itself bridges gaps between position fixes;
- on a stretch of valid points that the clock shifts by seconds: where the time across a hole
  differs by more than SHIFT_S from what both the speed channel and the driver's usual lap
  imply, with the car at speed on both sides (it can't have stopped in between), and a later
  hole shifts it back, the stretch in between is out (`_unshift`; GAS lap 48 of the 2026
  Australian GP ran 2.0-2.7 s late from 3185 m to 3805 m). A step that nothing undoes is left
  (the shift could be before it as well), and so are pit laps;
- on laps without a grid (no lap time, a rejected lap) and after the car retired. A lap
  without a grid but with sector times is placed from them instead: the session's usual
  progress through each sector (the median over its laps of the same kind with a grid: clean,
  out-, in- or slow laps), stretched to the lap's own sector time. That covers most qualifying
  out-laps, whose lap time FastF1 leaves blank (sectors 2 and 3 only: the first holds the pit
  exit), in-laps rejected for ending off the reference line in the pit lane, and the few race
  laps without a grid. These positions are `approximate`. Placing the laps of 2022-2025 that
  have a grid from their sector times instead, the median point is 0.07 s out on clean race
  laps (90th percentile 0.23 s), but 0.27 s on qualifying in-laps (90th percentile 1.2 s) and
  0.38 s on qualifying out-laps (2.5 s): a slow lap's pace varies within a sector. An out-lap
  ends where the next lap starts (the line crossing, which that lap's telemetry confirms), not
  at FastF1's start plus lap time, which can be seconds out for an out-lap (6.4 s for HAD's
  first lap of 2025 Canadian GP qualifying). Placed on any lap but a clean racing lap (a pit,
  slow, neutralised or opening lap), a position is `rough`: checked against the raw position
  feed (FastF1's cache) at the apex, 4 of 5 such cars that the rules had holding a driver up
  were 1.2-6.7 s from where their sector times put them (the 6.7 s before the out-lap fix
  above; the others 1.2-2.1 s), far more than the gaps the traffic rules use. The
  explanations name such cars but don't count them as traffic.

The gap between two cars
------------------------
The gap at a point of the track is how much later the other car went past it: positive when
it was behind, negative when it was ahead, on the nearest lap. A car one lap ahead in the race
that is 1 s behind on the road has a gap of +1 s and `laps_ahead` 1 (in qualifying the lap
count means nothing). A car passed the driver when its gap went from positive to negative
between two neighbouring points where both cars had a position (through the pit lane isn't a
pass), and the driver passed it the other way round.

Two gaps say how close a car was before the corner: `gap_start_s`, at the first point where
both cars have a position, as long as that comes before the corner's own window (a car leaving
the pit lane or crossing a feed hole is measured where it has one again, not left out), and
`gap_onset_s`, the closest it came to the driver from the start of the extended window up to
where the driver's loss began (`onset_m`, from the comparison with their usual laps: see
explain.loss_onset). The traffic rules weigh the second: whether a car was already close when
the time started to go, not 450 m before the apex, where a car lapping the driver can still be
seconds back.

`surroundings` looks at an extended window around a corner: its own window (250 m before the
apex to 145 m after) plus EXTEND_BEFORE_M before it and EXTEND_AFTER_M after it (450 m before
the apex to 445 m after, sampled every STEP_M and at both ends). Far enough
back that the gaps at its start come before the loss the corner carries in began: 450 m before
the apex lies before the previous turn's apex for four turns in five (consecutive apexes are
220 m apart at the median and within 390 m three times in four), and a slow exit from the turn
before loses its time after that apex. Far enough on to catch a pass on the straight after a
slow exit: on the flagged corners of 2022-2025, 97% of the passes by a lapping car that was
already close at the start came by 445 m after the apex (with the window reaching 800 m after
it, 2% came in the next 155 m). See inference/explain.py for what the explanations make of it.

`chequered_lap` says which lap a driver took the chequered flag on: the one under way when the
winner finished, so a driver who retired (whose last lap ends before that) has none.

This module reads the processed tables only; `load_tracks` is None for a session without laps
or lap grids (the explanations then have no traffic to go on).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from race_engineer.config import GRID_STEP_M, PROCESSED_DIR, WINDOW_BEFORE_M
from race_engineer.data import store

# Where the car is not racing on the track line (see the module docstring).
PIT_SPEED_KPH = 90.0  # the pit lane limit is 80 km/h (60 at Monaco and Singapore), plus noise
PIT_MARGIN_M = 150.0  # the pit exit and entry lanes, beyond the limit lines
# Further off the reference line than this next to the pit lane: still in the exit or entry
# lane. On the track the smoothed position feed puts cars about 0.5 m off it (typical offsets).
PIT_OFFSET_M = 5.0
# Back on the line means staying within PIT_OFFSET_M of it for this far: the Yas Marina exit
# lane crosses under the track, within 5 m of the line for about 40 m on the way.
PIT_REJOIN_M = 100.0
# Without a grid lap to find it from, the pit entry is taken this far before the line.
PIT_ENTRY_FALLBACK_M = 400.0
MAX_HOLE_M = 120.0  # as features.grid.MAX_FIX_GAP_M
# A clock step across a hole this much bigger than the speed channel implies, with the car at
# SHIFT_MIN_KPH or more on both sides, shifts the stretch after it (see `_unshift`).
SHIFT_S = 0.5
SHIFT_MIN_KPH = 150.0
MIN_USUAL_LAPS = 3  # grid laps of a driver needed for their usual lap (`_unshift`)
USUAL_LAPS = 10  # a car's usual time through a corner: its clean laps nearest in lap number

# The extended window around a corner, beyond its own (see the module docstring).
WINDOW_AFTER_M = 145.0  # the segments' last point (features.handcrafted.DISTANCE)
EXTEND_BEFORE_M = 200.0
EXTEND_AFTER_M = 300.0
STEP_M = 10.0  # the extended window is sampled every STEP_M
APEX_PHASE_END_M = 30.0  # entry and apex end here (explain.PHASE_EDGES_M)
NEAR_S = 8.0  # cars never closer than this in the extended window aren't reported

CLEAN = ("push", "race")


@dataclass(frozen=True)
class Car:
    """One car's progress through the session."""

    distance: np.ndarray  # total distance (m from the start of its lap 1), increasing
    time: np.ndarray  # session time (s) at that distance; NaN where it has no position
    approximate: np.ndarray  # placed from sector times, not the telemetry
    # Placed from sector times on a lap that isn't a clean racing lap: too rough for the
    # traffic rules (see the module docstring). None: nowhere.
    rough: np.ndarray | None = None
    known_time: np.ndarray = field(init=False, repr=False)  # the finite times, for distance_at
    known_distance: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        ok = np.isfinite(self.time)
        object.__setattr__(self, "known_time", np.maximum.accumulate(self.time[ok]))
        object.__setattr__(self, "known_distance", self.distance[ok])

    def time_at(self, distance: np.ndarray) -> np.ndarray:
        """Session time at each total distance (NaN where the car has no position)."""
        return np.interp(distance, self.distance, self.time, left=math.nan, right=math.nan)

    def rough_at(self, distance: np.ndarray) -> np.ndarray:
        """Whether the position at each total distance is `rough` (next to a rough point)."""
        if self.rough is None:
            return np.zeros(len(distance), dtype=bool)
        return np.interp(distance, self.distance, self.rough.astype(float), left=0, right=0) > 0

    def distance_at(self, time: float) -> float:
        """Total distance at a session time, bridging the pit lane (for picking the lap)."""
        if not len(self.known_time):
            return math.nan
        return float(
            np.interp(time, self.known_time, self.known_distance, left=math.nan, right=math.nan)
        )


@dataclass(frozen=True)
class SessionTracks:
    """Every car's position over the session, and each lap's grid clock for `lap_deficit`."""

    key: str
    length_m: float
    cars: dict[str, Car]
    laps: pd.DataFrame  # the laps table, indexed by (driver, lap_number)
    grid_time: np.ndarray  # (laps with a grid, grid points) time since the lap started
    grid_row: dict[tuple[str, int], int]  # (driver, lap) -> row of grid_time
    # Each car's clean racing laps (lap numbers, sorted), for its usual time through a corner.
    clean_laps: dict[str, np.ndarray] = field(default_factory=dict)


@dataclass(frozen=True)
class Encounter:
    """Another car near the driver in the extended window around a corner."""

    driver: str
    laps_ahead: int  # laps the other car had done more than the driver (races only)
    lap_class: str  # the other car's lap class where it met the driver ("out", "in" for pit laps)
    # At the first point where both cars have a position, if that comes before the corner's
    # own window (NaN otherwise); where that was is `gap_start_m`.
    gap_start_s: float
    gap_window_s: float  # at the start of the corner's own window
    min_gap_s: float  # smallest |gap| inside the corner's own window
    # Closest the driver came behind it on entry and through the apex, when it was ahead all
    # the way from the window start through the apex (NaN otherwise).
    behind_s: float
    passed: str  # "by": it passed the driver; "of": the driver passed it; "" neither
    pass_m: float  # where the (first) pass was, metres from the apex (NaN: no pass)
    swapped_back: bool  # alongside, places swapped and swapped back in the window
    # Its own time through the corner's window (NaN: unknown); for a car that rejoined from the
    # pit exit lane inside the window ahead of the driver, estimated from the driver's.
    window_s: float
    approximate: bool  # its positions here come from sector times
    # Its positions here, or the driver's, are `rough` (sector times on a pit, slow or other
    # lap that isn't a clean racing lap): too rough for the traffic rules.
    rough: bool = False
    gap_start_m: float = math.nan  # where gap_start_s was measured, metres from the apex
    # The closest it came (smallest |gap|, with its sign) from the start of the extended window
    # up to where the driver's loss began (Surroundings.onset_m), and where that was. NaN
    # without an onset or without a position before it.
    gap_onset_s: float = math.nan
    gap_onset_m: float = math.nan
    # Its usual time through the corner's window: the median over its USUAL_LAPS clean racing
    # laps nearest in lap number (NaN with fewer than three; only for a car that passed).
    usual_window_s: float = math.nan
    # The gap at each sample point of the extended window (metres from the apex), for
    # `closest_before` (None: not kept).
    offsets: np.ndarray | None = field(default=None, compare=False, repr=False)
    gaps: np.ndarray | None = field(default=None, compare=False, repr=False)

    def closest_before(self, point_m: float) -> tuple[float, float]:
        """The smallest |gap| (with its sign) from the window start up to `point_m`, and where
        it was (as gap_onset_s for the onset; NaN, NaN when unknown)."""
        if self.offsets is None or self.gaps is None:
            return math.nan, math.nan
        return _closest_before(self.offsets, self.gaps, point_m)

    def gap_before_loss(self) -> tuple[float, float]:
        """How close it was when the driver's loss began, and where (metres from the apex):
        `gap_onset_s`, or without one `gap_start_s` (NaN, NaN when neither is known)."""
        if np.isfinite(self.gap_onset_s):
            return self.gap_onset_s, self.gap_onset_m
        return self.gap_start_s, self.gap_start_m

    def passed_before(self, onset_m: float) -> bool:
        """It passed the driver, or the driver passed it, at or before `onset_m`."""
        return bool(self.passed and np.isfinite(self.pass_m) and self.pass_m <= onset_m)


@dataclass(frozen=True)
class Surroundings:
    """The cars around one corner (see `surroundings`)."""

    encounters: list[Encounter]
    start_m: float  # where the extended window started, metres from the apex
    end_m: float  # and ended (both clipped to where the driver has a position)
    onset_m: float = math.nan  # where the driver's loss began, metres from the apex (NaN: unknown)


# ---------------------------------------------------------------------------------------------
# Building the tracks


def _list_matrix(column: pa.ChunkedArray, width: int) -> np.ndarray:
    """A list column whose rows all have `width` values, as an (n, width) array."""
    array = column.combine_chunks()
    values = array.flatten().to_numpy(zero_copy_only=False).astype(float)
    return values.reshape(-1, width) if len(array) else np.zeros((0, width))


def load_tracks(key: str, root: Path = PROCESSED_DIR) -> SessionTracks | None:
    """The session's tracks from its laps, lap grids and sessions tables (None when one of
    them is missing)."""
    paths = {t: store.table_path(t, key, root) for t in ("laps", "lap_grids", "sessions")}
    if not all(p.exists() for p in paths.values()):
        return None
    session = pd.read_parquet(paths["sessions"]).iloc[0]
    columns = [
        "driver", "lap_number", "lap_class", "lap_start_s", "lap_time_s", "sector1_s",
        "sector2_s", "sector3_s", "pit_in", "pit_out", "track_status", "session",
    ]  # fmt: skip
    laps = pd.read_parquet(paths["laps"], columns=columns)
    channels = ["time_s", "valid", "speed", "offset_m"]
    table = pq.read_table(paths["lap_grids"], columns=["driver", "lap_number", *channels])
    width = int(session["n_grid"])
    time_s, valid, speed, offset = (_list_matrix(table.column(c), width) for c in channels)
    grids = table.select(["driver", "lap_number"]).to_pandas()
    length = float(session["track_length_m"])
    return session_tracks(laps, grids, time_s, valid, speed, length, key, offset)


def pit_lane(
    speed: np.ndarray, lap_class: np.ndarray, offset: np.ndarray | None = None
) -> np.ndarray:
    """(n, grid) mask of the grid points in the pit lane: on out-laps the opening run at up to
    PIT_SPEED_KPH (the pit speed limit) and PIT_MARGIN_M after it, on in-laps the closing run
    and PIT_MARGIN_M before it. With the offsets from the reference line (m), the mask also
    reaches on from that run until the car is back within PIT_OFFSET_M of the line for at
    least PIT_REJOIN_M (the exit or entry lane beside the track). Other laps have none."""
    n, width = speed.shape
    margin = round(PIT_MARGIN_M / GRID_STEP_M)
    idx = np.broadcast_to(np.arange(width), (n, width))
    slow = speed <= PIT_SPEED_KPH
    # Out-laps: up to the first point above the limit (none: the lap starts on the track).
    first_fast = np.where((~slow).any(axis=1), (~slow).argmax(axis=1), width)
    exit_idx = np.where(slow[:, 0], first_fast + margin, -1)
    # In-laps: from the last point above the limit on.
    last_fast = width - 1 - np.where((~slow).any(axis=1), (~slow)[:, ::-1].argmax(axis=1), width)
    entry_idx = np.where(slow[:, -1], last_fast + 1 - margin, width)
    if offset is not None:
        on_line = np.abs(np.nan_to_num(offset, nan=0.0)) <= PIT_OFFSET_M
        stay = round(PIT_REJOIN_M / GRID_STEP_M)
        for i in np.flatnonzero((lap_class == "out") | (lap_class == "in")):
            # On the line to stay: a lane that crosses the reference line (under or over the
            # track) is only briefly within PIT_OFFSET_M of it.
            settled = np.zeros(width, dtype=bool)
            for start, end in _runs(on_line[i]):
                settled[start:end] = end - start >= stay
            if lap_class[i] == "out":  # until back on the line, from the end of the limit
                back = np.flatnonzero(settled[first_fast[i] :])
                rejoin = first_fast[i] + back[0] if len(back) else width
                exit_idx[i] = max(exit_idx[i], rejoin - 1)
            else:  # from where it left the line, back from the start of the closing run
                left = np.flatnonzero(settled[: last_fast[i] + 1])
                entry_idx[i] = min(entry_idx[i], (left[-1] if len(left) else -1) + 1)
    out = (lap_class == "out")[:, None] & (idx <= exit_idx[:, None])
    into = (lap_class == "in")[:, None] & (idx >= entry_idx[:, None])
    return out | into


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) of each run of True, end exclusive."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()
    return list(zip(starts, ends, strict=True))


def _unshift(
    time: np.ndarray, hole: np.ndarray, speed_kph: np.ndarray, usual: np.ndarray | None
) -> np.ndarray:
    """A lap's grid clock (time since the lap started) with the stretches the feed shifted set
    to NaN. The step across a hole is its clock time minus the time it should have taken, by
    the speed channel and by the driver's usual lap (`usual`, the median of their laps' grid
    clocks; None: unknown, nothing is cut). A hole where both say the clock jumped by more
    than SHIFT_S the same way, with the car at SHIFT_MIN_KPH or more on both sides (so it
    can't have stopped inside), opens a shift: a frozen speed channel in the hole fools the
    first, a real slow-down in it the second, but not both. The stretch is cut only up to a
    later hole that steps back by about as much against the usual lap, so that both ends of
    it are seen, and only when the stretch as a whole runs off the usual lap by more than
    SHIFT_S against the points either side (not the clock jittering at a hole's edges). A step
    that nothing undoes is left, because the shift could as well be before it (on 2022-2026
    race laps, cutting to the lap end where the lap time disagreed took out whole laps that
    ran as usual)."""
    width = len(time)
    if width < 2 or not hole.any() or usual is None or not np.isfinite(usual).all():
        return time
    mps = np.maximum(np.asarray(speed_kph, dtype=float), 1.0) / 3.6
    steps = []  # (start, end, step against the usual lap, opens a shift) of each inner hole
    for start, end in _runs(hole):
        if start == 0 or end >= width:
            continue
        took = float(time[end] - time[start - 1])
        by_speed = took - float(
            np.sum(GRID_STEP_M / ((mps[start - 1 : end] + mps[start : end + 1]) / 2))
        )
        by_usual = took - float(usual[end] - usual[start - 1])
        at_speed = min(speed_kph[start - 1], speed_kph[end]) >= SHIFT_MIN_KPH
        opens = at_speed and min(abs(by_speed), abs(by_usual)) > SHIFT_S
        steps.append((start, end, by_usual, opens and np.sign(by_speed) == np.sign(by_usual)))
    out = time.copy()
    off = time - usual  # how far behind the usual lap the clock runs
    for i, (start, _, step, opens) in enumerate(steps):
        if not opens:
            continue
        back = [
            (begin, end) for begin, end, other, _ in steps[i + 1 :]
            if np.sign(other) != np.sign(step) and abs(step + other) <= max(SHIFT_S, abs(step) / 2)
        ]  # fmt: skip
        if not back:
            continue
        # The stretch between the holes as a whole runs off the usual lap by the step, against
        # the points on either side: not just the clock jittering at a hole's edges.
        begin, end = back[0]
        inside = float(np.median(off[steps[i][1] : begin]))
        outside = (off[start - 1] + off[end]) / 2
        if np.sign(inside - outside) == np.sign(step) and abs(inside - outside) > SHIFT_S:
            out[start:end] = math.nan
    return out


def _bridge(time: np.ndarray, hole: np.ndarray, max_points: int) -> np.ndarray:
    """Fill runs of `hole` no longer than max_points by linear interpolation along the lap."""
    out = time.copy()
    for start, end in _runs(hole):
        if end - start <= max_points and start > 0 and end < len(time):
            out[start:end] = np.interp(
                np.arange(start, end), [start - 1, end], [time[start - 1], time[end]]
            )
        else:
            out[start:end] = math.nan
    return out


MIN_PROFILE_LAPS = 5  # laps of a kind with a grid needed for that kind's own progress profile


@dataclass(frozen=True)
class _Sectors:
    """Where the sectors end along the lap, and the session's usual progress through each."""

    bounds: np.ndarray  # grid indices [0, end of sector 1, end of sector 2, last grid point]
    # Share of its sector's time done at each grid point, by kind of lap (`_kind`): the median
    # over the session's laps of that kind with a grid. Slow laps have a pace of their own (an
    # out-lap warming its tyres, an in-lap cooling down): placed with their own kind's profile,
    # the median error on 2022-2025 qualifying in-laps drops from 0.45 s to 0.28 s.
    progress: dict[str, np.ndarray]
    # The last grid point is a few metres before the line: the share of the last sector's time
    # done there (the median over clean laps), so that the lap's end time lands on the line.
    last_share: float = 1.0


def _kind(pit_out: bool, pit_in: bool, lap_class: str) -> str:
    if pit_out:
        return "out"
    if pit_in or lap_class == "in":
        return "in"
    return "slow" if lap_class == "slow" else "clean"


def _sectors(laps: pd.DataFrame, rows: np.ndarray, time_s: np.ndarray) -> _Sectors | None:
    """From the laps with a grid (`rows` of `laps`, aligned with `time_s`) and their sector
    times; None without at least three clean ones."""
    width = time_s.shape[1]
    s1, s2 = laps["sector1_s"].to_numpy(float)[rows], laps["sector2_s"].to_numpy(float)[rows]
    classes = laps["lap_class"].astype(str).to_numpy()[rows]
    kinds = np.array(
        [
            _kind(bool(o), bool(i), c)
            for o, i, c in zip(
                laps["pit_out"].to_numpy()[rows],
                laps["pit_in"].to_numpy()[rows],
                classes,
                strict=True,
            )
        ]
    )
    clean = (kinds == "clean") & np.isin(classes, CLEAN) & np.isfinite(s1) & np.isfinite(s2)
    if clean.sum() < 3:
        return None
    grid = np.arange(width, dtype=float)
    ends = [
        np.median(
            [np.interp(t, time_s[i], grid) for i, t in zip(np.flatnonzero(clean), cut, strict=True)]
        )
        for cut in (s1[clean], (s1 + s2)[clean])
    ]
    bounds = np.array([0, round(ends[0]), round(ends[1]), width - 1])
    if not (0 < bounds[1] < bounds[2] < width - 1):
        return None
    progress = {}
    for kind in ("clean", "in", "out", "slow"):
        rows_of_kind = clean if kind == "clean" else kinds == kind
        if kind != "clean" and rows_of_kind.sum() < MIN_PROFILE_LAPS:
            continue
        times = time_s[rows_of_kind]
        share = np.zeros(width)
        for a, b in pairwise(bounds):
            span = times[:, b] - times[:, a]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                part = (times[:, a : b + 1] - times[:, [a]]) / span[:, None]
                share[a : b + 1] = np.nanmedian(part, axis=0)
        if np.isfinite(share).all():
            progress[kind] = share
    ends = laps["lap_time_s"].to_numpy(float)[rows][clean]
    b = bounds[2]
    last = time_s[clean][:, -1] - time_s[clean][:, b]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        last_share = float(np.nanmedian(last / (ends - time_s[clean][:, b])))
    last_share = last_share if 0.5 < last_share <= 1.0 else 1.0
    return _Sectors(bounds=bounds, progress=progress, last_share=last_share)


def _placed_from_sectors(lap: pd.Series, next_start: float, sectors: _Sectors) -> np.ndarray:
    """Session time at each grid point of a lap without a grid, from its sector times (NaN in
    the sectors whose start or end time is unknown). An out-lap's start is in the garage or
    the pit lane, so its first sector is unknown; a lap's end comes from its lap time or, for a
    lap without one, the next lap's start (unless it ended in the pit lane)."""
    s = [float(lap[f"sector{i}_s"]) for i in (1, 2, 3)]
    lap_start, lap_time = float(lap["lap_start_s"]), float(lap["lap_time_s"])
    start = math.nan if lap["pit_out"] else lap_start
    end = lap_start + lap_time if np.isfinite(lap_time) else math.nan
    # An out-lap's start and lap time are FastF1's, from the garage: the next lap's start is
    # where it crossed the line (see the module docstring).
    if lap["pit_out"] and not lap["pit_in"] and np.isfinite(next_start):
        end = next_start
    if not np.isfinite(end) and not lap["pit_in"]:
        end = next_start
    marks = [start, start + s[0], math.nan, end]
    marks[2] = marks[1] + s[1] if np.isfinite(marks[1] + s[1]) else end - s[2]
    if not np.isfinite(marks[1]):
        marks[1] = marks[2] - s[1]
    kind = _kind(bool(lap["pit_out"]), bool(lap["pit_in"]), str(lap["lap_class"]))
    progress = sectors.progress.get(kind, sectors.progress["clean"])
    out = np.full(len(progress), math.nan)
    for j, (a, b) in enumerate(zip(sectors.bounds[:-1], sectors.bounds[1:], strict=True)):
        if np.isfinite(marks[j]) and np.isfinite(marks[j + 1]) and marks[j + 1] > marks[j]:
            share = progress[a : b + 1].copy()
            share[-1] = 1.0  # the profile holds the next sector's start (0) at the boundary
            if j == 2:  # the lap ends at the line, a few metres after the last grid point
                share *= sectors.last_share
            out[a : b + 1] = marks[j] + share * (marks[j + 1] - marks[j])
    return out


def session_tracks(
    laps: pd.DataFrame,
    grids: pd.DataFrame,
    time_s: np.ndarray,
    valid: np.ndarray,
    speed: np.ndarray,
    length_m: float,
    key: str = "",
    offset: np.ndarray | None = None,
) -> SessionTracks:
    """Every car's track (see the module docstring). `grids` has the driver and lap_number of
    each row of the (laps with a grid, grid points) arrays; `offset` is their distance from the
    reference line (m), for the pit lanes beside the track (without it, speed alone)."""
    laps = laps.drop_duplicates(["driver", "lap_number"]).sort_values(["driver", "lap_number"])
    laps = laps.reset_index(drop=True)
    width = time_s.shape[1]
    d = np.arange(width) * GRID_STEP_M
    lap_key = pd.MultiIndex.from_frame(laps[["driver", "lap_number"]].astype({"lap_number": int}))
    grid_key = list(zip(grids["driver"].astype(str), grids["lap_number"].astype(int), strict=True))
    row_of_lap = lap_key.get_indexer(pd.MultiIndex.from_tuples(grid_key, names=lap_key.names))
    keep = row_of_lap >= 0  # grid rows whose lap is in the laps table
    grid_rows = np.flatnonzero(keep)
    lap_rows = row_of_lap[keep]
    classes = laps["lap_class"].astype(str).to_numpy()
    start = laps["lap_start_s"].to_numpy(float)

    # Session times on the grid laps, with the pit lane, shifted stretches and feed failures
    # taken out.
    offsets = None if offset is None else offset[grid_rows]
    lap_speed = speed[grid_rows]
    pits = pit_lane(lap_speed, classes[lap_rows], offsets)
    holes = valid[grid_rows] <= 0
    since_start = time_s[grid_rows].astype(float)
    lap_driver = laps["driver"].astype(str).to_numpy()[lap_rows]
    # Pit laps are slow in their holes for real (the pit entry), so they are left as they are.
    on_track = holes.any(axis=1) & ~np.isin(classes[lap_rows], ("in", "out"))
    usual = {}  # each driver's usual grid clock, for `_unshift`
    for driver in np.unique(lap_driver[on_track]):
        theirs = since_start[lap_driver == driver]
        usual[driver] = np.median(theirs, axis=0) if len(theirs) >= MIN_USUAL_LAPS else None
    for i in np.flatnonzero(on_track):
        since_start[i] = _unshift(since_start[i], holes[i], lap_speed[i], usual[lap_driver[i]])
    clock = start[lap_rows][:, None] + since_start
    max_hole = int(MAX_HOLE_M // GRID_STEP_M)
    for i in np.flatnonzero((holes & ~pits).any(axis=1)):
        clock[i] = _bridge(clock[i], holes[i] & ~pits[i], max_hole)
    clock[pits] = math.nan
    entry = _pit_entry(pits, classes[lap_rows], width)

    sectors = _sectors(laps, lap_rows, time_s[grid_rows])
    has_grid = np.zeros(len(laps), dtype=bool)
    has_grid[lap_rows] = True
    grid_of = dict(zip(lap_rows.tolist(), range(len(lap_rows)), strict=True))
    numbers = laps["lap_number"].to_numpy(int)
    names = laps["driver"].astype(str).to_numpy()
    pit_in = laps["pit_in"].fillna(False).to_numpy(bool)
    pit_out = laps["pit_out"].fillna(False).to_numpy(bool)
    racing = np.isin(classes, CLEAN) & ~pit_in & ~pit_out  # sector times place these well
    cars = {}
    for driver, part in laps.groupby("driver", sort=False):
        pieces_d, pieces_t, pieces_a, pieces_r = [], [], [], []
        rows = part.index.to_numpy()
        for j, row in enumerate(rows):
            lap = int(numbers[row])
            if has_grid[row]:
                t, approx = clock[grid_of[row]], False
            elif sectors is not None:
                nxt = rows[j + 1] if j + 1 < len(rows) else -1
                follows = nxt >= 0 and numbers[nxt] == lap + 1
                next_start = start[nxt] if follows else math.nan
                t, approx = _placed_from_sectors(laps.loc[row], next_start, sectors), True
                if pit_in[row] or classes[row] == "in":
                    t[entry:] = math.nan  # the pit lane
            else:
                continue
            if not np.isfinite(t).any():
                continue
            pieces_d.append((lap - 1) * length_m + d)
            pieces_t.append(t)
            pieces_a.append(np.full(width, approx))
            pieces_r.append(np.full(width, approx and not racing[row]))
        if not pieces_d:
            continue
        distance, time = np.concatenate(pieces_d), np.concatenate(pieces_t)
        approximate, rough = np.concatenate(pieces_a), np.concatenate(pieces_r)
        # A missing lap leaves a jump in distance: a NaN point in it stops interpolation across.
        jumps = np.flatnonzero(np.diff(distance) > 1.5 * GRID_STEP_M)
        distance = np.insert(distance, jumps + 1, distance[jumps] + GRID_STEP_M / 2)
        time = np.insert(time, jumps + 1, math.nan)
        approximate = np.insert(approximate, jumps + 1, False)
        rough = np.insert(rough, jumps + 1, False)
        cars[str(driver)] = Car(distance=distance, time=time, approximate=approximate, rough=rough)
    laps_indexed = laps.set_index(["driver", "lap_number"])
    clean_laps = {
        str(driver): np.sort(numbers[part.index.to_numpy()][racing[part.index.to_numpy()]])
        for driver, part in laps.groupby("driver", sort=False)
    }
    return SessionTracks(
        key=key,
        length_m=float(length_m),
        cars=cars,
        laps=laps_indexed,
        grid_time=time_s[grid_rows],
        grid_row={(str(names[r]), int(numbers[r])): i for i, r in enumerate(lap_rows)},
        clean_laps=clean_laps,
    )


def _pit_entry(pits: np.ndarray, classes: np.ndarray, width: int) -> int:
    """The grid index where the session's in-laps enter the pit lane (the median over in-laps
    with a grid), for placing in-laps without one."""
    into = pits[classes == "in"]
    entries = [int(np.argmax(m)) for m in into if m.any()]
    if entries:
        return int(np.median(entries))
    return max(width - int(PIT_ENTRY_FALLBACK_M / GRID_STEP_M), 0)


# ---------------------------------------------------------------------------------------------
# Around one corner


def window_offsets() -> np.ndarray:
    """The extended window's sample points, metres from the apex: every STEP_M from its start,
    and its end."""
    start, end = -(WINDOW_BEFORE_M + EXTEND_BEFORE_M), WINDOW_AFTER_M + EXTEND_AFTER_M
    return np.append(np.arange(start, end - STEP_M / 2, STEP_M), end)


def surroundings(
    tracks: SessionTracks, driver: str, lap: int, apex_m: float, onset_m: float = math.nan
) -> Surroundings | None:
    """The cars that came within NEAR_S of the driver in the extended window around a corner
    (None when the driver has no position through the corner's own window). `onset_m`, where
    the driver's loss began (metres from the apex), gives each car's `gap_onset_s`."""
    me = tracks.cars.get(driver)
    if me is None:
        return None
    offsets = window_offsets()
    apex = (lap - 1) * tracks.length_m + apex_m
    points = apex + offsets
    mine = me.time_at(points)
    window = (offsets >= -WINDOW_BEFORE_M) & (offsets <= WINDOW_AFTER_M)
    if not np.isfinite(mine[window]).all():
        return None
    # Clip the extension to where the driver has a position (e.g. no grid for the lap before);
    # a hole inside it stays a hole, so no pass is read across it.
    known = np.isfinite(mine)
    first = int(np.argmax(known))
    last = len(known) - 1 - int(np.argmax(known[::-1]))
    span = slice(first, last + 1)
    offsets, points, mine, window = offsets[span], points[span], mine[span], window[span]
    mine_rough = me.rough_at(points)
    t_apex = float(np.interp(0.0, offsets, mine))
    mine_window_s = float(np.diff(np.interp([-WINDOW_BEFORE_M, WINDOW_AFTER_M], offsets, mine))[0])
    entry = window & (offsets <= APEX_PHASE_END_M)
    found = []
    for other, car in tracks.cars.items():
        if other == driver:
            continue
        where = car.distance_at(t_apex)
        if not np.isfinite(where):
            continue
        ahead = round((where - apex) / tracks.length_m)
        theirs = car.time_at(points + ahead * tracks.length_m)
        gap = theirs - mine
        ok = np.isfinite(gap)
        if not ok.any() or np.nanmin(np.abs(gap)) > NEAR_S:
            continue
        found.append(
            _encounter(
                tracks,
                other,
                car,
                ahead,
                apex,
                offsets,
                gap,
                window,
                entry,
                mine_rough,
                onset_m,
                mine_window_s,
            )
        )
    return Surroundings(
        encounters=found,
        start_m=float(offsets[0]),
        end_m=float(offsets[-1]),
        onset_m=float(onset_m),
    )


def _closest_before(offsets: np.ndarray, gap: np.ndarray, onset_m: float) -> tuple[float, float]:
    """The smallest |gap| (with its sign) from the window start up to `onset_m`, the onset
    itself interpolated when both cars have a position on either side of it, and where it was;
    (NaN, NaN) when there is none."""
    if not np.isfinite(onset_m):
        return math.nan, math.nan
    ok = np.isfinite(gap)
    before = ok & (offsets < onset_m)
    where, value = list(offsets[before]), list(gap[before])
    i = int(np.searchsorted(offsets, onset_m))
    if 0 < i < len(offsets) and ok[i] and ok[i - 1]:
        where.append(onset_m)
        value.append(float(np.interp(onset_m, offsets[i - 1 : i + 1], gap[i - 1 : i + 1])))
    elif i < len(offsets) and offsets[i] == onset_m and ok[i]:
        where.append(onset_m)
        value.append(float(gap[i]))
    if not value:
        return math.nan, math.nan
    j = int(np.argmin(np.abs(value)))
    return float(value[j]), float(where[j])


def usual_window_s(
    tracks: SessionTracks, driver: str, car: Car, lap: int, apex_in_lap_m: float
) -> float:
    """A car's usual time through a corner's window (apex `apex_in_lap_m` along the lap): the
    median over its USUAL_LAPS clean racing laps nearest in lap number to `lap`, that lap left
    out (NaN with fewer than three)."""
    laps = tracks.clean_laps.get(driver, np.zeros(0, dtype=int))
    laps = laps[laps != lap]
    if len(laps) < 3:
        return math.nan
    nearest = laps[np.argsort(np.abs(laps - lap), kind="stable")[:USUAL_LAPS]]
    starts = (nearest - 1) * tracks.length_m + apex_in_lap_m
    times = car.time_at(np.concatenate([starts - WINDOW_BEFORE_M, starts + WINDOW_AFTER_M]))
    through = times[len(nearest) :] - times[: len(nearest)]
    through = through[np.isfinite(through)]
    return float(np.median(through)) if len(through) >= 3 else math.nan


def _encounter(
    tracks: SessionTracks,
    other: str,
    car: Car,
    ahead: int,
    apex: float,
    offsets: np.ndarray,
    gap: np.ndarray,
    window: np.ndarray,
    entry: np.ndarray,
    mine_rough: np.ndarray,
    onset_m: float = math.nan,
    mine_window_s: float = math.nan,
) -> Encounter:
    ok = np.isfinite(gap)
    # Passes: sign changes between neighbouring points where both cars had a position.
    both = ok[1:] & ok[:-1]
    sign = np.sign(gap)
    by = np.flatnonzero(both & (sign[:-1] > 0) & (sign[1:] <= 0))
    of = np.flatnonzero(both & (sign[:-1] < 0) & (sign[1:] >= 0))
    finite = gap[ok]
    passed, pass_m = "", math.nan
    if len(by) or len(of):
        if finite[0] > 0 > finite[-1] and len(by):
            passed = "by"
        elif finite[0] < 0 < finite[-1] and len(of):
            passed = "of"
        crossings = np.sort(np.concatenate([by, of]))
        i = int(crossings[0])
        share = gap[i] / (gap[i] - gap[i + 1]) if gap[i] != gap[i + 1] else 0.0
        pass_m = float(offsets[i] + share * (offsets[i + 1] - offsets[i]))
    in_window = gap[window & ok]
    lap_there = int((apex + ahead * tracks.length_m) // tracks.length_m) + 1
    lap = lap_info(tracks, other, lap_there)
    lap_class = ""
    if lap is not None:
        lap_class = "out" if lap["pit_out"] else "in" if lap["pit_in"] else str(lap["lap_class"])
    # Ahead of the driver all the way from the window start through the apex: it held them up.
    # A car leaving the pits counts from where it rejoins the track inside the window, if it
    # had no position before (it came out of the exit lane ahead, not across a feed hole).
    behind = -gap[entry & ok]
    complete = bool(ok[entry].all())
    rejoined = -1
    if not complete and lap_class == "out" and ok[entry].any():
        rejoined = int(np.flatnonzero(entry & ok)[0])
        complete = not ok[:rejoined].any() and bool(ok[rejoined:][entry[rejoined:]].all())
    held = len(behind) > 0 and bool((behind > 0).all()) and complete
    edges = apex + ahead * tracks.length_m + np.array([-WINDOW_BEFORE_M, WINDOW_AFTER_M])
    through = car.time_at(edges)
    window_s = float(through[1] - through[0])
    if held and rejoined >= 0 and not np.isfinite(window_s):
        # Out of the pit exit lane inside the window: as long as the driver took up to where
        # it rejoined, plus what it lost or gained on them from there to the window's end.
        end_gap = float(np.interp(WINDOW_AFTER_M, offsets, gap))
        window_s = mine_window_s + end_gap - float(gap[rejoined])
    points = apex + ahead * tracks.length_m + offsets
    used = np.interp(points, car.distance, car.approximate.astype(float), left=0, right=0) > 0
    rough = (car.rough_at(points) | mine_rough) & ok
    # The start gap: the first point with both positions, before the corner's own window.
    first = np.flatnonzero(ok & (offsets <= -WINDOW_BEFORE_M))
    start_s = float(gap[first[0]]) if len(first) else math.nan
    start_m = float(offsets[first[0]]) if len(first) else math.nan
    onset_s, onset_at = _closest_before(offsets, gap, onset_m)
    usual = math.nan
    if passed == "by":  # for whether a car that passed was slowed itself (explain._battling)
        apex_there = apex + ahead * tracks.length_m - (lap_there - 1) * tracks.length_m
        usual = usual_window_s(tracks, other, car, lap_there, apex_there)
    return Encounter(
        driver=other,
        laps_ahead=ahead,
        lap_class=lap_class,
        gap_start_s=start_s,
        gap_window_s=float(np.interp(-WINDOW_BEFORE_M, offsets, gap)),
        min_gap_s=float(np.abs(in_window).min()) if len(in_window) else math.nan,
        behind_s=float(behind.min()) if held else math.nan,
        passed=passed,
        pass_m=pass_m,
        swapped_back=bool((len(by) or len(of)) and not passed),
        window_s=window_s,
        approximate=bool(used[ok].any()),
        rough=bool(rough.any()),
        gap_start_m=start_m,
        gap_onset_s=onset_s,
        gap_onset_m=onset_at,
        usual_window_s=usual,
        offsets=offsets,
        gaps=gap,
    )


# ---------------------------------------------------------------------------------------------
# How slow the lap was before a corner


def lap_deficit(
    tracks: SessionTracks, driver: str, lap: int, distance_m: float
) -> tuple[float, float]:
    """How far behind their usual push lap the driver was at a distance along the lap: this
    lap's time there minus the median of their other push laps' (seconds), and that median
    (the usual time to get there). NaN without a grid for the lap or another push lap."""
    row = tracks.grid_row.get((driver, lap))
    if row is None or distance_m <= 0:
        return math.nan, math.nan
    others = [
        i
        for (drv, n), i in tracks.grid_row.items()
        if drv == driver and n != lap and _lap_class(tracks, drv, n) == "push"
    ]
    if not others:
        return math.nan, math.nan
    grid = np.arange(tracks.grid_time.shape[1]) * GRID_STEP_M
    here = float(np.interp(distance_m, grid, tracks.grid_time[row]))
    usual = float(np.median([np.interp(distance_m, grid, tracks.grid_time[i]) for i in others]))
    return here - usual, usual


def _lap_class(tracks: SessionTracks, driver: str, lap: int) -> str:
    info = lap_info(tracks, driver, lap)
    return "" if info is None else str(info["lap_class"])


def lap_info(tracks: SessionTracks, driver: str, lap: int) -> pd.Series | None:
    """The laps table's row for one lap (None when the table doesn't have it)."""
    try:
        return cast(pd.Series, tracks.laps.loc[(driver, lap)])
    except KeyError:
        return None


def chequered_lap(tracks: SessionTracks, driver: str) -> int | None:
    """The lap on which the driver took the chequered flag in a race: the one under way when
    the winner finished (the earliest end of the session's last lap number), so the driver's
    first line crossing after it. None when no lap of theirs spans that moment: they retired
    (their last lap ends before it) or that lap has no lap time."""
    laps = tracks.laps
    numbers = laps.index.get_level_values("lap_number").to_numpy()
    if not len(numbers):
        return None
    starts = laps["lap_start_s"].to_numpy(float)
    ends = starts + laps["lap_time_s"].to_numpy(float)
    last = ends[numbers == numbers.max()]
    if not np.isfinite(last).any():
        return None
    finish = float(np.nanmin(last))
    mine = laps.index.get_level_values("driver").to_numpy() == driver
    spans = mine & (starts < finish) & (ends >= finish - 1e-3)
    return int(numbers[spans][0]) if spans.any() else None
