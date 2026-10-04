"""Compare two laps on the distance grid: where one driver gains time on the other, and how.

Both laps are already resampled onto the session's 5 m grid, so the time gap at any point is a
subtraction. The summary is written for the model to relay; the chart payload (speeds, pedals,
running gap, corners) is what the compare-laps MCP App draws.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.ndimage import gaussian_filter1d, median_filter

from race_engineer.config import PROCESSED_DIR
from race_engineer.data import store
from race_engineer.tools.sessions import SessionInfo, ToolInputError, resolve_session

MAX_POINTS = 1200  # chart payload cap; Spa on the 5 m grid is ~1,400 points
DRIFT_SIGMA_M = 100.0  # see lap_time_trace
DRIFT_TAPER_M = 200.0
SMOOTH_M = 10.0  # Gaussian sigma for the plotted gap
APEX_WINDOW_M = 50.0  # "apex speed" = slowest point within this distance of the turn marker
TOP_CORNERS = 3
MIN_CORNER_S = 0.01  # turns that moved the gap by less than this aren't worth a summary line
FLAT_THROTTLE = 90.0  # % throttle held all the way to the apex, with no braking, counts as flat
# A release of the brake this short doesn't end a braking zone if the car goes on to lose
# at least this share of its speed (not a dab of the brakes for the second half of a chicane).
BRAKE_GAP_M = 30.0
BRAKE_GAP_SLOWING = 0.1
TOP_SPEED_MARGIN = 0.9  # see top_speed
NOTE_MIN_FROZEN_M = 20.0  # shorter frozen stretches aren't listed in the notes
# See stale_speed.
CLOCK_WINDOW_M = 40.0
STALE_MIN_POINTS = 6
STALE_RATIO = 0.35
MAX_KPH = 400.0  # a clock implying more than this is itself glitching (position data catching up)

# How a driver approached a turn: braked (brake_m says where), flat (no braking and full
# throttle to the apex), none (no braking of its own, e.g. the second half of a chicane or a
# lift), unknown (the car data was frozen where it would tell).
Braking = Literal["braked", "flat", "none", "unknown"]
LAP_CLASS_TEXT = {
    "push": "a push lap",
    "race": "a green-flag lap",
    "start": "the opening lap",
    "slow": "a slow lap (a preparation or cool-down lap)",
    "yellow": "under yellow flags",
    "neutralised": "under a safety car, virtual safety car or red flag",
    "in": "an in lap",
    "out": "an out lap",
    # An abandoned or crashed lap can still have telemetry for its first corners.
    "no_time": "a lap without a lap time",
}

LAP_COLUMNS = [
    "lap_id",
    "driver",
    "driver_number",
    "team",
    "lap_number",
    "lap_time_s",
    "lap_class",
    "compound",
    "deleted",
    "grid_ok",
    "grid_reason",
    "sector1_s",
    "sector2_s",
    "sector3_s",
]
GRID_COLUMNS = ["time_s", "speed", "throttle", "brake", "valid"]


@dataclass(frozen=True)
class Lap:
    lap_id: str
    driver: str
    driver_number: str
    team: str
    lap_number: int
    lap_time_s: float
    lap_class: str
    compound: str
    deleted: bool
    sectors_s: tuple[float, float, float]  # NaN where timing is missing
    default: bool  # picked by default (not asked for by number)
    clean: bool  # a push lap in qualifying or a green-flag lap in a race, time not deleted


@dataclass(frozen=True)
class CornerDelta:
    label: str  # "T4" or "T9a"
    number: int
    letter: str
    apex_m: float
    start_m: float  # the stretch credited to this turn runs from the midpoint with the
    end_m: float  # previous apex to the midpoint with the next one
    delta_s: float  # time B lost to A over the stretch (negative: B gained)
    apex_speed: tuple[float | None, float | None]  # km/h (A, B); None if the data was frozen
    brake_m: tuple[float | None, float | None]  # where braking for the turn began, if it did
    braking: tuple[Braking, Braking]


@dataclass(frozen=True)
class LapComparison:
    session: SessionInfo
    lap_a: Lap
    lap_b: Lap
    gap_s: float  # lap time of B minus lap time of A
    corners: list[CornerDelta]
    notes: list[str]
    summary: str  # plain language, for the model
    chart: dict[str, Any]  # JSON-ready payload for the MCP App


def format_lap_time(seconds: float) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:06.3f}" if minutes else f"{rest:.3f}"


def lap_time_trace(
    time_s: np.ndarray, speed_kph: np.ndarray, lap_time_s: float, length_m: float, step_m: float
) -> np.ndarray:
    """Elapsed time at each grid point: 0 at the line, the official lap time at the finish.

    Two sources, each good at a different scale. Integrating speed gives smooth local detail
    (a braking point 10 m later shows up) but drifts by a few tenths over a lap; the grid's
    own `time_s` doesn't drift but jitters by about 0.1 s between neighbouring points. So:
    integrate speed, scale it to the official lap time, and add back the slow part of the
    clock's disagreement, tapered to zero at the line where both ends are exact. Against
    official sector times (800 lap pairs, 6 sessions) this puts the gap within 0.033 s
    (median) versus 0.039 s for raw `time_s`, with a fifth of the point-to-point jitter.
    """
    clock = np.asarray(time_s, dtype=float)
    mps = np.asarray(speed_kph, dtype=float) / 3.6
    d = np.arange(len(clock)) * step_m
    dt = step_m / np.maximum((mps[1:] + mps[:-1]) / 2, 1.0)
    frozen = np.isnan(dt)  # stale car data (speed nulled): trust the clock there
    dt[frozen] = np.diff(clock)[frozen]
    t = np.concatenate([[0.0], np.cumsum(dt)])
    # The grid stops short of the line; cover the rest at the pace of the last grid step.
    t *= lap_time_s / (t[-1] + (length_m - d[-1]) * dt[-1] / step_m)
    drift = gaussian_filter1d(clock - t, DRIFT_SIGMA_M / step_m, mode="nearest")
    taper = np.clip(np.minimum(d, length_m - d) / DRIFT_TAPER_M, 0.0, 1.0)
    return t + drift * taper


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(first, stop) index pairs of each run of True, `stop` exclusive."""
    edges = np.diff(np.concatenate([[0], np.asarray(mask, dtype=np.int8), [0]]))
    return list(
        zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True)
    )


def stale_speed(time_s: np.ndarray, speed_kph: np.ndarray, step_m: float) -> np.ndarray:
    """Grid points where the speed channel is stuck on a stale value that the clock contradicts.

    The pipeline marks the car feed frozen only when speed, RPM, throttle and gear all repeat,
    but speed alone sometimes sticks (2026 China sprint: 66 km/h held for 350 m out of the
    hairpin), which moves the integrated gap by a second. A stale value is the same speed at
    many consecutive grid points. That is also normal on a top-speed plateau, so a run only
    counts when the clock (position data, independent of the speed channel) disagrees by more
    than 35% both across the run and locally at most of its points. The local check alone
    misfires at braking points, and a clock implying an impossible speed is itself the glitch.
    A stale run is widened while the clock still disagrees, to take in the interpolated ramp
    onto the stuck value.
    """
    speed = np.asarray(speed_kph, dtype=float)
    clock = np.asarray(time_s, dtype=float)
    n = len(speed)
    k = max(1, round(CLOCK_WINDOW_M / 2 / step_m))
    i = np.arange(n)
    lo, hi = np.clip(i - k, 0, n - 1), np.clip(i + k, 0, n - 1)
    elapsed = np.maximum(clock[hi] - clock[lo], 1e-3)
    clock_kph = median_filter(3.6 * (hi - lo) * step_m / elapsed, size=2 * k + 1, mode="nearest")
    with np.errstate(invalid="ignore"):
        mismatch = (np.abs(speed / clock_kph - 1) > STALE_RATIO) & (clock_kph <= MAX_KPH)
    stale = np.zeros(n, dtype=bool)
    for first, stop in _runs(np.diff(speed) == 0):  # grid points first..stop share one speed
        if stop + 1 - first < STALE_MIN_POINTS:
            continue
        run_kph = 3.6 * (stop - first) * step_m / max(clock[stop] - clock[first], 1e-3)
        agrees = abs(speed[first] / run_kph - 1) <= STALE_RATIO or run_kph > MAX_KPH
        if agrees or mismatch[first : stop + 1].mean() <= 0.5:
            continue
        while first > 0 and mismatch[first - 1]:
            first -= 1
        while stop < n - 1 and mismatch[stop + 1]:
            stop += 1
        stale[first : stop + 1] = True
    return stale


def sample_indices(n: int, max_points: int = MAX_POINTS) -> np.ndarray:
    """Every k-th grid point (plus the last), keeping at most `max_points`."""
    stride = max(1, math.ceil(n / (max_points - 1)))
    idx = np.arange(0, n, stride)
    return idx if idx[-1] == n - 1 else np.append(idx, n - 1)


def _rounded(values: np.ndarray, decimals: int) -> list[float | None]:
    """JSON-ready list with None where the value is missing (NaN)."""
    return [None if math.isnan(v) else round(float(v), decimals) for v in values]


def _optional(value: float) -> float | None:
    return None if math.isnan(value) else float(value)


def top_speed(speed_kph: np.ndarray) -> float | None:
    """The lap's top speed, or None when a frozen stretch could hide a higher one: the car
    was already within 10% of the recorded top speed going into or coming out of it."""
    live = ~np.isnan(speed_kph)
    if not live.any():
        return None
    peak = float(np.nanmax(speed_kph))
    for first, stop in _runs(~live):
        edges = [j for j in (first - 1, stop) if 0 <= j < len(speed_kph)]
        if any(speed_kph[j] >= TOP_SPEED_MARGIN * peak for j in edges):
            return None
    return peak


def _load_laps(session: SessionInfo, root: Path) -> pd.DataFrame:
    laps = pd.read_parquet(store.table_path("laps", session.key, root), columns=LAP_COLUMNS)
    laps["team"] = laps["team"].fillna("").astype(str)
    laps["compound"] = laps["compound"].fillna("").astype(str)
    laps["deleted"] = laps["deleted"].fillna(False).astype(bool)
    laps["grid_ok"] = laps["grid_ok"].fillna(False).astype(bool)
    return laps


def _driver_laps(laps: pd.DataFrame, text: str, session: SessionInfo) -> pd.DataFrame:
    """One driver's laps, from a three-letter code (any case) or a car number."""
    code = text.strip().lstrip("#").upper()
    mine = laps[(laps["driver"].str.upper() == code) | (laps["driver_number"] == code)]
    if mine.empty:
        roster = laps.drop_duplicates("driver").sort_values("driver")
        names = ", ".join(f"{r.driver} ({r.team})" for r in roster.itertuples())
        raise ToolInputError(
            f"No driver {text!r} in the {session.label}. Drivers: {names}. "
            "Use the three-letter code."
        )
    return mine


def _fastest(laps: pd.DataFrame) -> dict:
    return laps.sort_values("lap_time_s", kind="stable").to_dict("records")[0]


def _laps_with_telemetry(driver: str, usable: pd.DataFrame) -> str:
    if usable.empty:
        return f"{driver} has no lap with usable telemetry in this session."
    best = _fastest(usable)
    numbers = ", ".join(str(n) for n in sorted(usable["lap_number"]))
    return (
        f"{driver}'s laps with telemetry: {numbers} "
        f"(fastest: lap {best['lap_number']}, {format_lap_time(best['lap_time_s'])})."
    )


def _pick_lap(
    mine: pd.DataFrame, lap_number: int | None, session: SessionInfo, notes: list[str]
) -> Lap:
    """The requested lap, or by default the fastest clean lap: a push lap in qualifying, a
    green-flag lap in a race, with telemetry and a lap time that wasn't deleted."""
    driver = str(mine["driver"].iloc[0])
    usable = mine[mine["grid_ok"] & mine["lap_time_s"].notna()]
    wanted = "push" if session.is_quali else "race"
    kind = "push" if session.is_quali else "green-flag"
    if lap_number is not None:
        rows = mine[mine["lap_number"] == lap_number]
        if rows.empty:
            raise ToolInputError(
                f"{driver} has no lap {lap_number} in the {session.label}. "
                + _laps_with_telemetry(driver, usable)
            )
        row = rows.to_dict("records")[0]
        if not (row["grid_ok"] and np.isfinite(row["lap_time_s"])):
            reason = row["grid_reason"] or "no lap time"
            raise ToolInputError(
                f"{driver} lap {lap_number} has no usable telemetry ({reason}). "
                + _laps_with_telemetry(driver, usable)
            )
        if row["lap_class"] != wanted:
            what = LAP_CLASS_TEXT.get(str(row["lap_class"]), f"classed {row['lap_class']}")
            notes.append(f"{driver} lap {lap_number} was {what}, not a representative {kind} lap.")
        if row["deleted"]:
            notes.append(f"{driver} lap {lap_number} had its lap time deleted (track limits).")
    else:
        clean = usable[(usable["lap_class"] == wanted) & ~usable["deleted"]]
        if clean.empty:
            if usable.empty:
                raise ToolInputError(
                    f"{driver} has no timed lap with usable telemetry in the {session.label}."
                )
            clean = usable
        row = _fastest(clean)
        if clean is usable:
            deleted = ", lap time deleted" if row["deleted"] else ""
            notes.append(
                f"{driver} has no clean {kind} lap with telemetry, so their fastest lap with "
                f"telemetry is used (lap {row['lap_number']}, classed {row['lap_class']}"
                f"{deleted})."
            )
    return Lap(
        lap_id=str(row["lap_id"]),
        driver=driver,
        driver_number=str(row["driver_number"]),
        team=str(row["team"]),
        lap_number=int(row["lap_number"]),
        lap_time_s=float(row["lap_time_s"]),
        lap_class=str(row["lap_class"]),
        compound=str(row["compound"]),
        deleted=bool(row["deleted"]),
        sectors_s=(float(row["sector1_s"]), float(row["sector2_s"]), float(row["sector3_s"])),
        default=lap_number is None,
        clean=row["lap_class"] == wanted and not row["deleted"],
    )


def _load_grids(session: SessionInfo, laps: list[Lap], root: Path) -> list[dict[str, np.ndarray]]:
    """Each lap's grid channels, NaN where the car data was frozen (speed also where it was
    stuck on a stale value; see stale_speed)."""
    ids = [lap.lap_id for lap in laps]
    table = pq.read_table(
        store.table_path("lap_grids", session.key, root),
        columns=["lap_id", *GRID_COLUMNS],
        filters=[("lap_id", "in", ids)],
    )
    row_of = {lap_id: i for i, lap_id in enumerate(table.column("lap_id").to_pylist())}
    grids = []
    for lap_id in ids:
        if lap_id not in row_of:  # laps says grid_ok but the grid row is missing
            raise ToolInputError(f"No telemetry grid stored for lap {lap_id}.")
        grid = {
            c: np.asarray(table.column(c)[row_of[lap_id]].values, dtype=float) for c in GRID_COLUMNS
        }
        # Checked before the frozen stretches are blanked, so a stale run is seen whole.
        stale = stale_speed(grid["time_s"], grid["speed"], session.grid_step_m)
        frozen = grid["valid"] < 0.5  # the car feed repeated stale values here
        for c in ("speed", "throttle", "brake"):
            grid[c][frozen] = np.nan
        grid["speed"][stale] = np.nan
        grids.append(grid)
    return grids


def _load_corners(session: SessionInfo, root: Path) -> pd.DataFrame:
    path = store.table_path("corners", session.key, root)
    if not path.exists():
        return pd.DataFrame({"number": [], "letter": [], "apex_m": []})
    corners = pd.read_parquet(path, columns=["number", "letter", "apex_m"])
    corners["letter"] = corners["letter"].fillna("").astype(str)
    return corners.sort_values("apex_m").reset_index(drop=True)


def _zone_onsets(
    on: np.ndarray,
    speed: np.ndarray,
    distance_m: np.ndarray,
    apex: np.ndarray,
    bounds: np.ndarray,
) -> list[float | None]:
    """Where braking for each turn began: the start of the first braking zone (runs of `on`,
    merged across brief releases) that belongs to it. A zone belongs to the turn whose stretch
    contains its end, where the car stops braking, so a zone carried through a chicane counts
    once; a zone that only began after that turn's apex (braking early for the next) moves on."""
    zones: list[tuple[int, int]] = []  # (first, last) index
    for first, stop in _runs(on):
        brief = zones and distance_m[first] - distance_m[zones[-1][1]] <= BRAKE_GAP_M
        if brief and speed[stop - 1] < (1 - BRAKE_GAP_SLOWING) * speed[zones[-1][1]]:
            zones[-1] = (zones[-1][0], stop - 1)
        else:
            zones.append((first, stop - 1))
    onsets: list[float | None] = [None] * len(apex)
    for first, last in zones:
        turn = min(int(np.searchsorted(bounds, distance_m[last], side="right")) - 1, len(apex) - 1)
        if distance_m[first] > apex[turn]:
            turn += 1
        if turn < len(apex) and onsets[turn] is None:
            onsets[turn] = float(distance_m[first])
    return onsets


def braking_points(
    grid: dict[str, np.ndarray], distance_m: np.ndarray, apex: np.ndarray, bounds: np.ndarray
) -> list[tuple[Braking, float | None]]:
    """How the driver approached each turn, and where braking began if they braked.

    Frozen car data (NaN brake) could hide braking, so each turn is worked out twice, reading
    the frozen stretches as no braking and as braking. The turn is unknown if the two answers
    differ or the data was frozen anywhere between the previous apex and this one.
    """
    brake, throttle, speed = grid["brake"], grid["throttle"], grid["speed"]
    frozen = np.isnan(brake)
    as_off = _zone_onsets(brake >= 0.5, speed, distance_m, apex, bounds)
    as_on = _zone_onsets((brake >= 0.5) | frozen, speed, distance_m, apex, bounds)
    points: list[tuple[Braking, float | None]] = []
    for i, x in enumerate(apex):
        approach = (distance_m >= (apex[i - 1] if i else 0.0)) & (distance_m <= x)
        to_apex = (distance_m >= bounds[i]) & (distance_m <= x)
        if as_off[i] != as_on[i] or frozen[approach].any():
            points.append(("unknown", None))
        elif as_off[i] is not None:
            points.append(("braked", as_off[i]))
        elif to_apex.any() and (throttle[to_apex] >= FLAT_THROTTLE).all():
            points.append(("flat", None))
        else:
            points.append(("none", None))
    return points


def corner_deltas(
    corners: pd.DataFrame,
    distance_m: np.ndarray,
    delta_s: np.ndarray,
    grids: list[dict[str, np.ndarray]],
    length_m: float,
    gap_s: float,
) -> list[CornerDelta]:
    """How much of the gap each turn accounts for, splitting the lap at the midpoints between
    consecutive apexes (so the braking zone before a turn and its exit both count for it)."""
    if corners.empty:
        return []
    apex = np.clip(corners["apex_m"].to_numpy(float), 0.0, length_m)
    bounds = np.concatenate([[0.0], (apex[:-1] + apex[1:]) / 2, [length_m]])
    # The gap is known exactly at the finish line (the official lap times).
    at = np.interp(bounds, np.append(distance_m, length_m), np.append(delta_s, gap_s))

    def apex_speed(grid: dict[str, np.ndarray], where: float) -> float | None:
        near = grid["speed"][np.abs(distance_m - where) <= APEX_WINDOW_M]
        # Partly frozen: the slowest point may be in the part that wasn't recorded.
        return None if not len(near) or np.isnan(near).any() else float(near.min())

    a, b = grids
    points = [braking_points(g, distance_m, apex, bounds) for g in grids]
    return [
        CornerDelta(
            label=f"T{number}{letter}",
            number=int(number),
            letter=str(letter),
            apex_m=float(x),
            start_m=float(bounds[i]),
            end_m=float(bounds[i + 1]),
            delta_s=float(at[i + 1] - at[i]),
            apex_speed=(apex_speed(a, x), apex_speed(b, x)),
            brake_m=(points[0][i][1], points[1][i][1]),
            braking=(points[0][i][0], points[1][i][0]),
        )
        for i, (number, letter, x) in enumerate(
            zip(corners["number"], corners["letter"], apex, strict=True)
        )
    ]


def _near(corners: list[CornerDelta], d: float) -> str:
    for c in corners:
        if c.start_m <= d <= c.end_m:
            return f"around {c.label}, {d / 1000:.2f} km"
    return f"at {d / 1000:.2f} km"


def _speed(value: float | None) -> str:
    return "not recorded" if value is None else f"{value:.0f} km/h"


def frozen_note(laps: tuple[Lap, Lap], grids: list[dict[str, np.ndarray]], step_m: float) -> str:
    """Where each driver's car data was frozen, so the model doesn't read gaps in the braking
    and speed claims as facts ("" if nothing worth listing)."""
    parts = []
    for lap, grid in zip(laps, grids, strict=True):
        frozen = np.isnan(grid["speed"]) | np.isnan(grid["brake"])
        stretches = [
            (first * step_m, (stop - 1) * step_m)
            for first, stop in _runs(frozen)
            if (stop - first) * step_m >= NOTE_MIN_FROZEN_M
        ]
        if not stretches:
            continue
        longest = sorted(sorted(stretches, key=lambda s: s[0] - s[1])[:3])
        text = ", ".join(f"{lo / 1000:.2f} to {hi / 1000:.2f} km" for lo, hi in longest)
        if (more := len(stretches) - len(longest)) > 0:
            text += f" and {more} shorter stretch{'es' if more > 1 else ''}"
        parts.append(f"{lap.driver} {text}")
    if not parts:
        return ""
    return (
        f"Car data was frozen (not recorded) for {'; '.join(parts)}. Speeds and braking points "
        "that depend on it are left out, and the running gap there comes from lap timing."
    )


def _braking_text(c: CornerDelta, a: Lap, b: Lap) -> str:
    (state_a, state_b), (brake_a, brake_b) = c.braking, c.brake_m
    if brake_a is not None and brake_b is not None:
        if abs(brake_b - brake_a) < 5:
            return ""
        later = b.driver if brake_b > brake_a else a.driver
        return f"{later} braked {abs(brake_b - brake_a):.0f} m later; "
    if {state_a, state_b} == {"braked", "flat"}:
        return f"only {a.driver if state_a == 'braked' else b.driver} braked; "
    unknown = [lap.driver for lap, s in zip((a, b), c.braking, strict=True) if s == "unknown"]
    if unknown:
        return f"braking point not recorded for {' and '.join(unknown)}; "
    return ""  # e.g. one driver braked and the other only lifted or was still slow


def build_summary(
    session: SessionInfo,
    a: Lap,
    b: Lap,
    gap_s: float,
    corners: list[CornerDelta],
    distance_m: np.ndarray,
    delta_s: np.ndarray,
    top_speeds: tuple[float | None, float | None],
    notes: list[str],
) -> str:
    def lap_line(lap: Lap) -> str:
        which = ""
        if lap.default:
            which = " (fastest clean lap)" if lap.clean else " (fastest lap with telemetry)"
        tyre = f" on {lap.compound}" if lap.compound else ""
        return (
            f"{lap.driver} ({lap.team}) lap {lap.lap_number}{which}{tyre}: "
            f"{format_lap_time(lap.lap_time_s)}"
        )

    lines = [
        f"{session.label} ({session.location}, {session.track_length_m / 1000:.2f} km lap).",
        f"{lap_line(a)}; {lap_line(b)}.",
    ]
    if abs(gap_s) < 0.0005:
        lines.append("The two laps were identical to the thousandth.")
    else:
        faster = a.driver if gap_s > 0 else b.driver
        lines.append(f"{faster} was {abs(gap_s):.3f} s faster over the lap.")

    if not any(np.isnan(a.sectors_s + b.sectors_s)):
        parts = []
        for i, (sa, sb) in enumerate(zip(a.sectors_s, b.sectors_s, strict=True), start=1):
            if abs(sb - sa) < 0.0005:
                parts.append(f"S{i} level")
            else:
                parts.append(f"S{i} {a.driver if sb > sa else b.driver} {abs(sb - sa):.3f} s")
        lines.append("Official sector gaps (who was faster): " + ", ".join(parts) + ".")

    notable = [c for c in corners if abs(c.delta_s) >= MIN_CORNER_S]
    ranked = sorted(notable, key=lambda c: abs(c.delta_s), reverse=True)[:TOP_CORNERS]
    if ranked:
        lines.append(
            "Where the time went (each turn counts from halfway after the previous turn to "
            "halfway to the next):"
        )
        for c in ranked:
            gainer = a.driver if c.delta_s > 0 else b.driver
            lines.append(
                f"- {c.label} ({c.apex_m / 1000:.2f} km): {gainer} gained {abs(c.delta_s):.3f} s; "
                f"{_braking_text(c, a, b)}apex speed {a.driver} {_speed(c.apex_speed[0])}, "
                f"{b.driver} {_speed(c.apex_speed[1])}."
            )

    leads = []
    i_a, i_b = int(np.argmax(delta_s)), int(np.argmin(delta_s))
    if delta_s[i_a] > 0.0005:
        where = _near(corners, float(distance_m[i_a]))
        leads.append(f"{a.driver} by {delta_s[i_a]:.3f} s ({where})")
    if delta_s[i_b] < -0.0005:
        where = _near(corners, float(distance_m[i_b]))
        leads.append(f"{b.driver} by {-delta_s[i_b]:.3f} s ({where})")
    if leads:
        lines.append("Biggest lead during the lap: " + "; ".join(leads) + ".")
    if any(v is not None for v in top_speeds):
        lines.append(
            f"Top speed: {a.driver} {_speed(top_speeds[0])}, {b.driver} {_speed(top_speeds[1])}."
        )
    lines.extend(notes)
    lines.append(
        "The interactive chart shows both speed traces, the running gap, throttle and braking "
        "along the lap, with corner markers."
    )
    return "\n".join(lines)


def build_chart(
    session: SessionInfo,
    laps: tuple[Lap, Lap],
    grids: list[dict[str, np.ndarray]],
    distance_m: np.ndarray,
    delta_s: np.ndarray,
    gap_s: float,
    corners: list[CornerDelta],
    notes: list[str],
) -> dict[str, Any]:
    """The MCP App payload: downsampled traces (None where the car data was frozen)."""
    idx = sample_indices(len(distance_m))
    smooth = gaussian_filter1d(delta_s, SMOOTH_M / session.grid_step_m, mode="nearest")

    def driver(lap: Lap, grid: dict[str, np.ndarray]) -> dict[str, Any]:
        return {
            "code": lap.driver,
            "number": lap.driver_number,
            "team": lap.team,
            "lap_number": lap.lap_number,
            "lap_time_s": round(lap.lap_time_s, 3),
            "lap_class": lap.lap_class,
            "compound": lap.compound,
            "deleted": lap.deleted,
            "sectors_s": [_optional(s) for s in lap.sectors_s],
            "speed": _rounded(grid["speed"][idx], 1),
            "throttle": _rounded(grid["throttle"][idx], 0),
            # Braking can be shorter than the sampling stride: keep the strongest in each step.
            "brake": _rounded(np.fmax.reduceat(grid["brake"], idx), 2),
        }

    a, b = laps
    return {
        "title": f"{a.driver} vs {b.driver}",
        "event": session.event,
        "location": session.location,
        "year": session.year,
        "round": session.round,
        "session": session.name,
        "session_code": session.code,
        "date": session.date,
        "track_length_m": round(session.track_length_m, 1),
        "gap_s": round(gap_s, 3),
        "distance_m": _rounded(distance_m[idx], 1),
        "delta_s": _rounded(smooth[idx], 3),
        "drivers": [driver(a, grids[0]), driver(b, grids[1])],
        "corners": [
            {
                "label": c.label,
                "number": c.number,
                "letter": c.letter,
                "apex_m": round(c.apex_m, 1),
                "delta_s": round(c.delta_s, 3),
                "apex_speed": [None if v is None else round(v, 1) for v in c.apex_speed],
                "brake_m": [None if v is None else round(v, 1) for v in c.brake_m],
                "braking": list(c.braking),
            }
            for c in corners
        ],
        "notes": notes,
    }


def compare_laps(
    event: str,
    driver_a: str,
    driver_b: str,
    year: int | None = None,
    session: str | None = None,
    lap_a: int | None = None,
    lap_b: int | None = None,
    root: Path = PROCESSED_DIR,
) -> LapComparison:
    """Compare two laps of one session (qualifying unless `session` or the event text names
    another): by default each driver's fastest clean lap.

    The gap is always driver B relative to driver A: positive means B was slower (A ahead).
    Raises ToolInputError with a helpful message when the session, a driver or a lap can't be
    found.
    """
    info = resolve_session(event, year, session, root)
    laps = _load_laps(info, root)
    notes: list[str] = []
    first = _pick_lap(_driver_laps(laps, driver_a, info), lap_a, info, notes)
    second = _pick_lap(_driver_laps(laps, driver_b, info), lap_b, info, notes)
    if first.lap_id == second.lap_id:
        raise ToolInputError(
            f"Both sides are {first.driver} lap {first.lap_number}. Pick two drivers, or give "
            "lap_a and lap_b to compare two laps of the same driver."
        )

    grids = _load_grids(info, [first, second], root)
    n = min(len(g["time_s"]) for g in grids)
    grids = [{c: v[:n] for c, v in g.items()} for g in grids]
    step, length = info.grid_step_m, info.track_length_m
    distance = np.arange(n) * step
    times = [
        lap_time_trace(g["time_s"], g["speed"], lap.lap_time_s, length, step)
        for g, lap in zip(grids, (first, second), strict=True)
    ]
    delta = times[1] - times[0]
    gap = second.lap_time_s - first.lap_time_s

    corners = corner_deltas(_load_corners(info, root), distance, delta, grids, length, gap)
    top_speeds = (top_speed(grids[0]["speed"]), top_speed(grids[1]["speed"]))
    if note := frozen_note((first, second), grids, step):
        notes.append(note)
    summary = build_summary(info, first, second, gap, corners, distance, delta, top_speeds, notes)
    chart = build_chart(info, (first, second), grids, distance, delta, gap, corners, notes)
    return LapComparison(
        session=info,
        lap_a=first,
        lap_b=second,
        gap_s=gap,
        corners=corners,
        notes=notes,
        summary=summary,
        chart=chart,
    )
