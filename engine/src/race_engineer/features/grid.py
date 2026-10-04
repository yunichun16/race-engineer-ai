"""Resample one lap of raw telemetry onto the session's fixed distance grid.

Raw car data arrives about 4 times a second, so the samples of two laps never line up.
After this step every lap is a set of arrays indexed by distance (every 5 m), which makes
laps, drivers and corners directly comparable.

Distance comes from two sources. Position fixes (X/Y matched to the reference line) say where
the car is; integrated speed says how far it went in between. The position feed often stalls,
repeating the last X/Y for a second or more (for most of the 2026 Hungarian GP race), so each
stretch between fixes is filled by the car's own speed trace rather than a straight line.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np
import pandas as pd

from race_engineer.config import POSITION_UNITS_PER_M
from race_engineer.features.reference import ReferenceLine

GRID_CHANNELS = (
    "time_s",  # seconds since the lap started
    "speed",  # km/h
    "throttle",  # 0-100 %
    "brake",  # 0-1 (share of the interval spent braking)
    "gear",
    "rpm",
    "drs",  # raw FastF1 DRS code (always 0 from 2026, when DRS was removed)
    "x_m",
    "y_m",
    "offset_m",  # signed distance from the reference line, positive = left
    "valid",  # 1 where car and position data are live and agree; 0 in a feed failure
)

# Stretches between two car-data samples, or two position fixes, further apart than this are
# marked invalid. In a car-data gap speed, throttle and brake are only interpolated; in a
# position gap distance is still right (integrated speed) but the line (offset_m) is a guess.
MAX_SAMPLE_GAP_M = 120.0
MAX_FIX_GAP_M = 120.0
# Over MISMATCH_WINDOW_S, the distance between position fixes and the distance from integrated
# speed normally agree within about 12%. Stretches where they differ by more than this factor
# are marked invalid: usually a speed channel stuck on one value while the car moves on (much of
# the 2026 Japanese GP race), sometimes a position glitch.
MISMATCH_WINDOW_S = 1.5
MAX_MISMATCH_RATIO = 1.4

# Laps failing these checks keep their row in the laps table but get no grid.
MAX_P95_OFFSET_M = 25.0
MAX_BACKWARDS_M = 60.0
MAX_FROZEN_SHARE = 0.3  # and likewise for the share of the lap in car-data or position gaps
MIN_SAMPLES = 40

# The live-timing car feed sometimes freezes: the same speed, RPM, throttle and gear repeat
# while the car keeps moving (e.g. most of the 2026 China sprint). Real telemetry never stays
# exactly identical on all four for this many samples (~1.5 s).
FREEZE_SAMPLES = 6


def frozen_samples(*channels: np.ndarray) -> np.ndarray:
    """Samples that repeat the previous one on every channel, in runs long enough to be stale."""
    n = len(channels[0])
    same = np.ones(n - 1, dtype=bool)
    for channel in channels:
        same &= np.diff(channel) == 0
    edges = np.diff(np.concatenate([[0], same.astype(np.int8), [0]]))
    stale = np.zeros(n, dtype=bool)
    for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
        if end - start >= FREEZE_SAMPLES - 1:
            stale[start + 1 : end + 1] = True  # the first sample of the run is still genuine
    return stale


def position_fixes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Position samples that are new fixes: the first of each run of identical X/Y.

    A stalled feed repeats the last fix; its later copies say nothing about where the car is.
    (A stationary car also repeats, and integrated speed handles that correctly: it stays put.)
    """
    fix = np.ones(len(x), dtype=bool)
    fix[1:] = (np.diff(x) != 0) | (np.diff(y) != 0)
    return fix


def to_seconds(td: Any) -> np.ndarray:
    """Timedeltas as float seconds, with NaN for missing values."""
    return pd.to_timedelta(pd.Series(td)).dt.total_seconds().to_numpy(dtype=float)


@dataclass(frozen=True)
class DriverTelemetry:
    """One driver's full-session car and position data as plain arrays."""

    t_car: np.ndarray
    speed: np.ndarray
    throttle: np.ndarray
    brake: np.ndarray
    gear: np.ndarray
    rpm: np.ndarray
    drs: np.ndarray
    t_pos: np.ndarray
    x: np.ndarray  # decimetres, as FastF1 reports them
    y: np.ndarray

    @classmethod
    def from_fastf1(cls, car: pd.DataFrame, pos: pd.DataFrame) -> DriverTelemetry:
        return cls(
            t_car=to_seconds(car["SessionTime"]),
            speed=car["Speed"].to_numpy(float),
            throttle=np.clip(car["Throttle"].to_numpy(float), 0, 100),
            brake=car["Brake"].to_numpy(float),
            gear=car["nGear"].to_numpy(float),
            rpm=car["RPM"].to_numpy(float),
            drs=car["DRS"].to_numpy(float),
            t_pos=to_seconds(pos["SessionTime"]),
            x=pos["X"].to_numpy(float),
            y=pos["Y"].to_numpy(float),
        )

    @cached_property
    def pos_fix(self) -> np.ndarray:
        """Which position samples are new fixes (see `position_fixes`)."""
        return position_fixes(self.x, self.y)


@dataclass(frozen=True)
class LapQA:
    n_samples: int
    max_gap_m: float
    p95_abs_offset_m: float
    backwards_m: float
    frozen_share: float  # share of the lap where the car-data feed was frozen
    ok: bool
    reason: str
    max_fix_gap_m: float = np.nan  # longest stretch between two position fixes
    gap_share: float = np.nan  # share of the lap in car-data gaps longer than MAX_SAMPLE_GAP_M
    sparse_share: float = np.nan  # share of the lap in position gaps longer than MAX_FIX_GAP_M
    mismatch_share: float = np.nan  # share where speed and position fixes disagree


@dataclass(frozen=True)
class LapGrid:
    channels: dict[str, np.ndarray]
    qa: LapQA


def make_grid(length_m: float, step_m: float) -> np.ndarray:
    return np.arange(0.0, length_m - step_m / 2, step_m)


def mismatch_windows(t: np.ndarray, s: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Windows of MISMATCH_WINDOW_S, as (first, last) sample indices, over which distance from
    positions (`s`) and distance driven (`d`, from speed) disagree by more than
    MAX_MISMATCH_RATIO. `t`, `s` and `d` are per position fix, in time order."""
    j = np.searchsorted(t, t + MISMATCH_WINDOW_S)
    i = np.flatnonzero(j < len(t))
    j = j[i]
    ds, dd = s[j] - s[i], d[j] - d[i]
    # Moving by either measure: a speed channel stuck near zero while the fixes move on counts.
    moving = np.maximum(ds, dd) > 20.0
    ratio = ds / np.maximum(dd, 1.0)
    bad = moving & ((ratio > MAX_MISMATCH_RATIO) | (ratio < 1 / MAX_MISMATCH_RATIO))
    return i[bad], j[bad]


def speed_mismatch(
    t_a: np.ndarray, s_a: np.ndarray, d_a: np.ndarray, grid_m: np.ndarray
) -> np.ndarray:
    """Grid points inside a mismatch window (see `mismatch_windows`)."""
    i, j = mismatch_windows(t_a, s_a, d_a)
    # Mark each bad window's stretch of the grid: +1 where it starts, -1 after it ends.
    marks = np.zeros(len(grid_m) + 1)
    np.add.at(marks, np.searchsorted(grid_m, s_a[i]), 1)
    np.add.at(marks, np.searchsorted(grid_m, s_a[j], side="right"), -1)
    return np.cumsum(marks)[:-1] > 0


def stretch_length(knots_m: np.ndarray, grid_m: np.ndarray) -> np.ndarray:
    """Length of the stretch between consecutive knots that each grid point falls in (infinite
    before the first knot and after the last)."""
    edges = np.concatenate([[-np.inf], knots_m, [np.inf]])
    j = np.searchsorted(knots_m, grid_m, side="right")
    return edges[j + 1] - edges[j]


def lap_to_grid(
    tel: DriverTelemetry,
    lap_start_s: float,
    lap_time_s: float,
    reference: ReferenceLine,
    grid_m: np.ndarray,
    margin_s: float = 1.5,
) -> LapGrid:
    """Resample the lap that starts at `lap_start_s` (session seconds) and lasts `lap_time_s`."""
    lo, hi = lap_start_s - margin_s, lap_start_s + lap_time_s + margin_s
    sel = (tel.t_car >= lo) & (tel.t_car <= hi)
    t = tel.t_car[sel]
    if len(t) < MIN_SAMPLES:
        return _failed(len(t), "too few samples")

    fsel = (tel.t_pos >= lo - 2) & (tel.t_pos <= hi + 2) & tel.pos_fix
    if fsel.sum() < MIN_SAMPLES // 2:
        return _failed(len(t), "no position data")
    t_fix, x_fix, y_fix = tel.t_pos[fsel], tel.x[fsel], tel.y[fsel]

    # Distance driven since the first car sample, from speed; extended at constant speed to
    # fixes just outside the car data.
    v = tel.speed[sel] / 3.6
    driven = np.concatenate([[0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * np.diff(t))])
    d_fix = np.interp(t_fix, t, driven)
    d_fix = np.where(t_fix < t[0], (t_fix - t[0]) * v[0], d_fix)
    d_fix = np.where(t_fix > t[-1], driven[-1] + (t_fix - t[-1]) * v[-1], d_fix)

    # Match the fixes to the line, using distance driven (scaled to the line's length) to pick
    # the right branch where the track crosses itself.
    d_start, d_end = np.interp([lap_start_s, lap_start_s + lap_time_s], t, driven)
    scale = reference.length_m / max(d_end - d_start, 1.0)
    proj = reference.project(x_fix, y_fix, expected_m=(d_fix - d_start) * scale)
    s_fix = proj.distance_m.copy()
    rel_fix = t_fix - lap_start_s
    half = reference.length_m / 2
    # The lap starts at the line: fixes just before it project near the end of the loop, and
    # fixes just after the finish project near its start. Shift both onto one axis.
    s_fix[(rel_fix < 0.25 * lap_time_s) & (s_fix > half)] -= reference.length_m
    s_fix[(rel_fix > 0.75 * lap_time_s) & (s_fix < half)] += reference.length_m
    backwards = float(np.clip(-np.diff(s_fix), 0, None).sum())

    # Anchors: fixes that move forward both along the line and in distance driven. This drops
    # position glitches that jump back, and jitter while the car stands still.
    ahead = np.concatenate([[True], s_fix[1:] > np.maximum.accumulate(s_fix)[:-1]])
    anchor = np.flatnonzero(ahead)
    anchor = anchor[np.concatenate([[True], np.diff(d_fix[anchor]) > 1e-3])]
    if len(anchor) < 2:
        return _failed(len(t), "no position data")
    s_a, d_a = s_fix[anchor], d_fix[anchor]

    # Every car sample's distance along the line: pinned at the fixes, stretched in between
    # by how far the car drove, and at driven distance beyond the first and last fix.
    s = np.interp(driven, d_a, s_a)
    s = np.where(driven < d_a[0], s_a[0] + driven - d_a[0], s)
    s = np.where(driven > d_a[-1], s_a[-1] + driven - d_a[-1], s)
    keep = np.concatenate([[True], np.diff(s) > 1e-6])  # drops samples while stationary
    s_u = s[keep]
    if s_u[0] > grid_m[0] or s_u[-1] < grid_m[-1]:
        return _failed(len(t), "lap not fully covered")

    gap = stretch_length(s_u, grid_m) > MAX_SAMPLE_GAP_M
    mismatch = speed_mismatch(t_fix[anchor], s_a, d_a, grid_m)
    fix_gap = stretch_length(s_a, grid_m)
    sparse = fix_gap > MAX_FIX_GAP_M
    finite = fix_gap[np.isfinite(fix_gap)]
    max_fix_gap = float(finite.max()) if len(finite) else np.nan

    in_lap = (s_a >= grid_m[0]) & (s_a <= grid_m[-1])
    offsets = proj.offset_m[anchor][in_lap] if in_lap.any() else proj.offset_m[anchor]
    max_gap = float(np.diff(s_u).max())
    p95_offset = float(np.percentile(np.abs(offsets), 95))
    reasons = []
    if p95_offset > MAX_P95_OFFSET_M:
        reasons.append("off the reference line")
    if backwards > MAX_BACKWARDS_M:
        reasons.append("position glitches")

    def linear(values: np.ndarray) -> np.ndarray:
        return np.interp(grid_m, s_u, values[keep]).astype(np.float32)

    step_idx = np.clip(np.searchsorted(s_u, grid_m, side="right") - 1, 0, len(s_u) - 1)

    def step(values: np.ndarray) -> np.ndarray:
        return values[keep][step_idx].astype(np.float32)

    def along(values: np.ndarray) -> np.ndarray:
        """Position-derived channels, interpolated between fixes by distance."""
        return np.interp(grid_m, s_a, values[anchor]).astype(np.float32)

    stale = frozen_samples(tel.speed[sel], tel.rpm[sel], tel.throttle[sel], tel.gear[sel])
    # Car data is live at a grid point only if both raw samples around it are live.
    live = np.interp(grid_m, s_u, (~stale[keep]).astype(float)) > 0.999
    frozen_share = float(1 - live.mean())
    gap_share = float(gap.mean())
    sparse_share = float(sparse.mean())
    mismatch_share = float(mismatch.mean())
    if frozen_share > MAX_FROZEN_SHARE:
        reasons.append("frozen car data")
    if gap_share > MAX_FROZEN_SHARE:
        reasons.append("gap in samples")
    if sparse_share > MAX_FROZEN_SHARE:
        reasons.append("sparse position data")
    if mismatch_share > MAX_FROZEN_SHARE:
        reasons.append("speed disagrees with position")

    rel = t - lap_start_s
    channels = {
        "time_s": linear(rel),
        "speed": linear(tel.speed[sel]),
        "throttle": linear(tel.throttle[sel]),
        "brake": linear(tel.brake[sel]),
        "gear": step(tel.gear[sel]),
        "rpm": linear(tel.rpm[sel]),
        "drs": step(tel.drs[sel]),
        "x_m": along(x_fix / POSITION_UNITS_PER_M),
        "y_m": along(y_fix / POSITION_UNITS_PER_M),
        "offset_m": along(proj.offset_m),
        "valid": (live & ~gap & ~sparse & ~mismatch).astype(np.float32),
    }
    qa = LapQA(
        n_samples=len(t),
        max_gap_m=max_gap,
        p95_abs_offset_m=p95_offset,
        backwards_m=backwards,
        frozen_share=frozen_share,
        ok=not reasons,
        reason="; ".join(reasons),
        max_fix_gap_m=max_fix_gap,
        gap_share=gap_share,
        sparse_share=sparse_share,
        mismatch_share=mismatch_share,
    )
    return LapGrid(channels=channels, qa=qa)


def _failed(n: int, reason: str) -> LapGrid:
    qa = LapQA(
        n_samples=n,
        max_gap_m=np.nan,
        p95_abs_offset_m=np.nan,
        backwards_m=np.nan,
        frozen_share=np.nan,
        ok=False,
        reason=reason,
    )
    return LapGrid(channels={}, qa=qa)
