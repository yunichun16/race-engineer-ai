"""Made-up processed sessions in the real table schema, for tests and the chart fixture.

F1 data isn't redistributed, so the tool tests and the web dev host's compare-laps fixture use
small synthetic sessions written with the same tables and columns as the pipeline
(`data.build.write_session`): sessions, laps, lap_grids, corners, tracks and, when a session
lists any, events. The track is a circle of the session's length, driven anticlockwise from
(radius, 0); the cars' x/y, the corners and the track outline all lie on it.

`sample_race_session` is a short made-up race (safety car, VSC, pit stops, a lapped car and one
that stops) for the race summary tests and its chart fixture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from race_engineer.config import SESSION_CODES
from race_engineer.data import store
from race_engineer.features.grid import GRID_CHANNELS
from race_engineer.features.segments import window_indices
from race_engineer.mcp_server.sample_data import CORNERS, TRACK_LENGTH_M, build_sample_lap

GRID_STEP_M = 5.0
SESSION_NAMES = {code: name for name, code in SESSION_CODES.items()}


@dataclass(frozen=True)
class SyntheticLap:
    driver: str
    lap_number: int
    speed_kph: np.ndarray  # one value per grid point
    driver_number: str = "1"
    team: str = "Test Team"
    lap_class: str = "push"
    grid_ok: bool = True
    deleted: bool = False
    valid: np.ndarray | None = None  # 1 = live car data; all live when omitted
    # The speed channel as the car feed reported it, e.g. stuck on a stale value; the clock
    # and pedals follow speed_kph. Same as speed_kph when omitted.
    reported_speed_kph: np.ndarray | None = None
    start_s: float | None = None  # session time the lap started (120 s * lap number if omitted)
    # Race fields, as FastF1 gives them: position at the end of the lap (NaN when not given),
    # every track status code seen during the lap ("4": safety car), pit entry and exit (an
    # "in" or "out" lap class implies them), and the tyre stint and compound.
    position: float = math.nan
    track_status: str = "1"
    pit_in: bool = False
    pit_out: bool = False
    stint: float = 1.0
    compound: str = "SOFT"


@dataclass(frozen=True)
class SyntheticEvent:
    """A row of the events table: a track-limits violation or a race-control incident."""

    driver: str
    lap_number: int
    turn: int
    kind: str = "track_limits"  # or "incident"


@dataclass(frozen=True)
class SyntheticSession:
    year: int
    round: int
    code: str  # Q, SQ, SS, S or R
    event: str
    location: str
    date: str = "2026-03-07"
    track_length_m: float = 5000.0
    corners: tuple[tuple[int, str, float], ...] = ()  # (number, letter, apex_m)
    laps: tuple[SyntheticLap, ...] = ()
    events: tuple[SyntheticEvent, ...] = ()

    @property
    def key(self) -> str:
        return store.session_key(self.year, self.round, self.code)

    @property
    def n_grid(self) -> int:
        return len(grid_distance(self.track_length_m))


def grid_distance(length_m: float) -> np.ndarray:
    return np.arange(0.0, length_m - GRID_STEP_M / 2, GRID_STEP_M)  # as features.grid.make_grid


def circle_xy(distance_m: np.ndarray, length_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Positions (m) at these distances along the circular track: anticlockwise from (r, 0)."""
    theta = 2 * np.pi * np.asarray(distance_m, dtype=float) / length_m
    radius = length_m / (2 * np.pi)
    return radius * np.cos(theta), radius * np.sin(theta)


def lap_clock(speed_kph: np.ndarray, length_m: float) -> tuple[np.ndarray, float]:
    """Elapsed time at each grid point and the lap time, driving at `speed_kph`."""
    mps = np.asarray(speed_kph, dtype=float) / 3.6
    dt = GRID_STEP_M / ((mps[1:] + mps[:-1]) / 2)
    t = np.concatenate([[0.0], np.cumsum(dt)])
    tail_m = length_m - (len(t) - 1) * GRID_STEP_M
    return t, float(t[-1] + tail_m * dt[-1] / GRID_STEP_M)


def pedals(speed_kph: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Plausible throttle (%) and brake (0-1) for a speed trace: brake while slowing hard,
    lift while slowing gently, otherwise flat out."""
    smooth = np.convolve(np.pad(speed_kph, 3, mode="edge"), np.ones(7) / 7, mode="valid")
    change = np.diff(smooth, append=smooth[-1])  # per grid step, ignoring sensor-like noise
    brake = (change < -1.5).astype(float)
    throttle = np.where(brake > 0, 0.0, np.where(change < -0.6, 30.0, 100.0))
    return throttle, brake


def _lap_row(session: SyntheticSession, lap: SyntheticLap, lap_time: float) -> dict:
    t, _ = lap_clock(lap.speed_kph, session.track_length_m)
    d = grid_distance(session.track_length_m)
    thirds = np.interp([session.track_length_m / 3, 2 * session.track_length_m / 3], d, t)
    timed = lap.lap_class != "no_time"
    nan = float("nan")
    return {
        "driver": lap.driver,
        "driver_number": lap.driver_number,
        "team": lap.team,
        "lap_number": lap.lap_number,
        "compound": lap.compound,
        "tyre_life": 2.0,
        "fresh_tyre": True,
        "stint": lap.stint,
        "track_status": lap.track_status,
        "is_accurate": True,
        "is_personal_best": False,
        "deleted": lap.deleted,
        "deleted_reason": "TRACK LIMITS" if lap.deleted else "",
        "position": lap.position,
        "speed_i1": nan,
        "speed_i2": nan,
        "speed_fl": nan,
        "speed_st": nan,
        "lap_time_s": lap_time if timed else nan,
        "lap_start_s": 120.0 * lap.lap_number if lap.start_s is None else lap.start_s,
        "sector1_s": thirds[0] if timed else nan,
        "sector2_s": thirds[1] - thirds[0] if timed else nan,
        "sector3_s": lap_time - thirds[1] if timed else nan,
        "pit_in": lap.pit_in or lap.lap_class == "in",
        "pit_out": lap.pit_out or lap.lap_class == "out",
        "lap_class": lap.lap_class,
        "gap_ahead_s": nan,
        "air_temp": 20.0,
        "track_temp": 30.0,
        "humidity": 50.0,
        "rainfall": 0.0,
        "wind_speed": 1.0,
        "grid_ok": lap.grid_ok and timed,
        "grid_reason": "" if lap.grid_ok else "gap in samples",
        "n_samples": 300,
        "max_gap_m": 20.0,
        "p95_offset_m": 1.0,
        "backwards_m": 0.0,
        "frozen_share": 0.0 if lap.valid is None else float(1 - np.mean(lap.valid)),
        "max_fix_gap_m": 20.0,
        "gap_share": 0.0,
        "sparse_share": 0.0,
        "mismatch_share": 0.0,
        "year": session.year,
        "round": session.round,
        "session": session.code,
        "lap_id": f"{session.key}_{lap.driver_number}_{lap.lap_number}",
    }


def write_session(session: SyntheticSession, root: Path) -> None:
    """Write the session's tables under `root`, like a processed session."""
    ids = {"year": session.year, "round": session.round, "session": session.code}
    d = grid_distance(session.track_length_m)
    x, y = circle_xy(d, session.track_length_m)
    rows, grids = [], []
    for lap in session.laps:
        t, lap_time = lap_clock(lap.speed_kph, session.track_length_m)
        row = _lap_row(session, lap, lap_time)
        rows.append(row)
        if not row["grid_ok"]:
            continue
        throttle, brake = pedals(lap.speed_kph)
        channels = {
            "time_s": t,
            "speed": lap.speed_kph if lap.reported_speed_kph is None else lap.reported_speed_kph,
            "throttle": throttle,
            "brake": brake,
            "gear": np.full(len(d), 7.0),
            "rpm": np.full(len(d), 11000.0),
            "drs": np.zeros(len(d)),
            "x_m": x,
            "y_m": y,
            "offset_m": np.zeros(len(d)),
            "valid": np.ones(len(d)) if lap.valid is None else lap.valid,
        }
        grids.append((row, channels))

    if session.laps:
        laps = pd.DataFrame(rows)
        store.write_table(store.to_arrow(laps), "laps", session.key, root)
        meta = pd.DataFrame(
            [
                {**ids, **{k: row[k] for k in ("lap_id", "driver", "driver_number", "lap_number")}}
                for row, _ in grids
            ]
        )
        arrays = {c: store.list_column([np.asarray(g[c]) for _, g in grids]) for c in GRID_CHANNELS}
        store.write_table(store.to_arrow(meta, arrays), "lap_grids", session.key, root)

    if session.corners:
        size = len(window_indices(0.0, GRID_STEP_M))
        apex = np.array([a for _, _, a in session.corners], dtype=float)
        cx, cy = circle_xy(apex, session.track_length_m)
        # FastF1's angle points from the turn to its label: outwards, away from the centre.
        angle = np.degrees(2 * np.pi * apex / session.track_length_m)
        corners = pd.DataFrame(
            [
                {"number": n, "letter": letter, "apex_m": a, "x_m": px, "y_m": py}
                | {"angle": ang, **ids}
                for (n, letter, a), px, py, ang in zip(session.corners, cx, cy, angle, strict=True)
            ]
        )
        curvature = store.fixed_list_column([np.zeros(size)] * len(corners), size)
        store.write_table(
            store.to_arrow(corners, {"curvature": curvature}), "corners", session.key, root
        )

    # The track outline on the grid, as data.build writes it (positive curvature: turning left).
    track = {"x_m": x, "y_m": y, "curvature": np.full(len(d), 2 * np.pi / session.track_length_m)}
    store.write_table(
        store.to_arrow(
            pd.DataFrame([{**ids, "length_m": session.track_length_m}]),
            {c: store.list_column([v]) for c, v in track.items()},
        ),
        "tracks",
        session.key,
        root,
    )

    if session.events:
        store.write_table(store.to_arrow(_events(session)), "events", session.key, root)

    info = {
        **ids,
        "event": session.event,
        "location": session.location,
        "session_name": SESSION_NAMES[session.code],
        "session_date": pd.Timestamp(session.date, tz="UTC"),
        "track_length_m": session.track_length_m,
        "grid_step_m": GRID_STEP_M,
        "n_grid": session.n_grid,
        "n_corners": len(session.corners),
        "reference_driver": session.laps[0].driver if session.laps else "",
        "reference_lap": session.laps[0].lap_number if session.laps else 0,
        "corner_source": "synthetic",
        "n_laps": len(rows),
        "n_grid_ok": len(grids),
        "n_segments": 0,
        "skipped_windows": 0,
        "invalid_windows": 0,
        "n_track_limit_events": sum(e.kind == "track_limits" for e in session.events),
        "n_incident_events": sum(e.kind == "incident" for e in session.events),
        "fastf1_version": "synthetic",
    }
    store.write_table(store.to_arrow(pd.DataFrame([info])), "sessions", session.key, root)


def _events(session: SyntheticSession) -> pd.DataFrame:
    """The events table for the session's events, in data.labels' columns and wording."""
    laps = {(lap.driver, lap.lap_number): lap for lap in session.laps}
    numbers = {lap.driver: lap.driver_number for lap in session.laps}
    rows = []
    for e in session.events:
        lap = laps.get((e.driver, e.lap_number))
        if e.kind == "track_limits":
            source = "fastf1_deleted" if lap is not None and lap.deleted else "race_control"
            message = f"TRACK LIMITS AT TURN {e.turn} LAP {e.lap_number}"
        else:
            source = "race_control"
            message = f"TURN {e.turn} INCIDENT INVOLVING CAR {numbers[e.driver]} ({e.driver}) NOTED"
        same = sum(
            (o.lap_number, o.turn, o.kind) == (e.lap_number, e.turn, e.kind) for o in session.events
        )
        rows.append(
            {
                "driver_number": numbers[e.driver],
                "driver": e.driver,
                "lap_number": e.lap_number,
                "turn": e.turn,
                "kind": e.kind,
                "source": source,
                "message": message,
                "drivers_same_lap_turn": same,
                "year": session.year,
                "round": session.round,
                "session": session.code,
            }
        )
    return pd.DataFrame(rows)


def sample_comparison_session() -> SyntheticSession:
    """Two made-up drivers on the synthetic sample lap (`mcp_server.sample_data`), for the
    compare-laps chart fixture. BBB is quicker through some turns and slower through others,
    and has a short stretch of frozen car data, which the chart draws as a gap."""
    lap = build_sample_lap()
    d = np.asarray(lap.distance_m)
    speed_a = np.asarray(lap.speed_kph)
    # (centre m, relative speed change, width m) around the sample track's turns
    changes = [(600, 0.03, 50), (1300, -0.03, 80), (2600, -0.05, 60), (4100, -0.025, 90)]
    changes += [(3300, 0.02, 100), (4700, 0.025, 60)]
    factor = 1 + sum(a * np.exp(-0.5 * ((d - c) / w) ** 2) for c, a, w in changes)
    noise = np.random.default_rng(11).uniform(-0.6, 0.6, len(d))
    speed_b = np.round(speed_a * factor + noise, 1)
    valid_b = np.where((d >= 3440) & (d < 3500), 0.0, 1.0)
    return SyntheticSession(
        year=2026,
        round=1,
        code="Q",
        event="Sample Grand Prix",
        location="Synthetic Circuit",
        track_length_m=float(TRACK_LENGTH_M),
        corners=tuple((n, "", float(apex)) for n, apex, _ in CORNERS),
        laps=(
            SyntheticLap("AAA", 7, speed_a, driver_number="1", team="Sample Racing"),
            SyntheticLap("BBB", 9, speed_b, driver_number="2", team="Example GP", valid=valid_b),
        ),
    )


# The sample race. Every car starts lap 1 at the same session time, as in FastF1; behind the
# safety car and under the VSC every car laps in the same time, and a pit stop costs less then.
RACE_START_S = 3600.0
RACE_STATUS = {3: "12", 5: "4", 6: "4", 7: "4", 12: "6"}  # lap -> track status codes seen
NEUTRAL_LAP_S = {5: 118.0, 6: 118.0, 7: 118.0, 12: 104.0}
PIT_IN_S, PIT_OUT_S = 7.0, 13.0  # added to the in-lap and the out-lap under green
NEUTRAL_PIT_IN_S, NEUTRAL_PIT_OUT_S = 2.0, 6.0  # the same when the stop is made neutralised


@dataclass(frozen=True)
class _RaceCar:
    driver: str
    number: str
    team: str
    start_lap_s: float  # lap 1, from the standing start
    pace: dict[int, float]  # green-flag lap time from each of these laps on
    in_laps: tuple[int, ...]
    compounds: tuple[str, ...]  # one per stint
    laps: int  # rows in the laps table (the last has no time when the car stops on track)
    stops_on_track: bool = False
    exact: dict[int, float] | None = None  # lap times that differ from the pattern


def _race_lap_times(car: _RaceCar) -> list[float]:
    """The car's lap times: its pace on green laps, the field's under the safety car and the
    VSC, a pit stop's cost on the in- and out-lap, and NaN for a lap it never finished."""
    times = []
    for lap in range(1, car.laps + 1):
        pace = car.pace[max(k for k in car.pace if k <= lap)] if lap > 1 else car.start_lap_s
        t = NEUTRAL_LAP_S.get(lap, pace)
        if lap in car.in_laps:
            t += NEUTRAL_PIT_IN_S if lap in NEUTRAL_LAP_S else PIT_IN_S
        if lap - 1 in car.in_laps:
            t += NEUTRAL_PIT_OUT_S if lap - 1 in NEUTRAL_LAP_S else PIT_OUT_S
        t = (car.exact or {}).get(lap, t)
        times.append(math.nan if car.stops_on_track and lap == car.laps else t)
    return times


def _race_lap_class(lap: int, status: str, pit_in: bool, pit_out: bool, timed: bool) -> str:
    """The lap class features.context.classify_laps gives a race lap (no slow laps here)."""
    if not timed:
        return "no_time"
    if pit_in or pit_out:
        return "in" if pit_in else "out"
    if any(code in status for code in "4567"):
        return "neutralised"
    if "2" in status:
        return "yellow"
    return "start" if lap == 1 else "race"


def sample_race_session() -> SyntheticSession:
    """A made-up 20-lap race of 4 cars on the sample track, for the race summary tests and its
    chart fixture. A safety car on laps 5-7, a VSC on lap 12 and yellow flags on lap 3. BBB leads
    from the start and stops twice (laps 6 and 12, both neutralised, SOFT-MEDIUM-SOFT); AAA
    stops once (lap 9, SOFT-HARD) and passes BBB on track on lap 15 to win. CCC is lapped once.
    DDD stops on track on lap 13 after 12 laps. AAA's lap 19 is the quickest but deleted for
    track limits, so the fastest lap is his lap 18. Positions follow the line crossings."""
    cars = (
        _RaceCar("AAA", "1", "Sample Racing", 95.6, {2: 90.0, 13: 89.9}, (9,), ("SOFT", "HARD"),
                 laps=20, exact={18: 89.5, 19: 89.2}),
        _RaceCar("BBB", "2", "Example GP", 95.0, {2: 90.0, 13: 91.8}, (6, 12),
                 ("SOFT", "MEDIUM", "SOFT"), laps=20),
        _RaceCar("CCC", "3", "Example GP", 97.2, {2: 99.5}, (), ("HARD",), laps=19),
        _RaceCar("DDD", "4", "Sample Racing", 96.1, {2: 90.3}, (), ("MEDIUM",), laps=13,
                 stops_on_track=True),
    )  # fmt: skip
    deleted = {("AAA", 19)}
    times = {car.driver: _race_lap_times(car) for car in cars}
    starts = {
        d: RACE_START_S + np.concatenate([[0.0], np.cumsum(t[:-1])]) for d, t in times.items()
    }
    ends = {d: starts[d] + np.asarray(t) for d, t in times.items()}

    def position(driver: str, lap: int) -> float:
        """1 + the cars that completed this lap before this one (NaN for an unfinished lap)."""
        mine = ends[driver][lap - 1]
        if math.isnan(mine):
            return math.nan
        ahead = sum(len(e) >= lap and e[lap - 1] < mine for d, e in ends.items() if d != driver)
        return float(1 + ahead)

    laps = []
    length = float(TRACK_LENGTH_M)
    for car in cars:
        for lap in range(1, car.laps + 1):
            lap_time = times[car.driver][lap - 1]
            timed = not math.isnan(lap_time)
            status = RACE_STATUS.get(lap, "1")
            pit_in, pit_out = lap in car.in_laps, lap - 1 in car.in_laps
            stint = 1 + sum(k < lap for k in car.in_laps)
            kph = length / lap_time * 3.6 if timed else 200.0
            laps.append(
                SyntheticLap(
                    car.driver,
                    lap,
                    np.full(len(grid_distance(length)), kph),
                    driver_number=car.number,
                    team=car.team,
                    lap_class=_race_lap_class(lap, status, pit_in, pit_out, timed),
                    deleted=(car.driver, lap) in deleted,
                    start_s=float(starts[car.driver][lap - 1]),
                    position=position(car.driver, lap),
                    track_status=status,
                    pit_in=pit_in,
                    pit_out=pit_out,
                    stint=float(stint),
                    compound=car.compounds[stint - 1],
                )
            )
    return SyntheticSession(
        year=2026,
        round=1,
        code="R",
        event="Sample Grand Prix",
        location="Synthetic Circuit",
        date="2026-03-08",
        track_length_m=length,
        corners=tuple((n, "", float(apex)) for n, apex, _ in CORNERS),
        laps=tuple(laps),
        events=(
            SyntheticEvent("AAA", 19, 3),  # the deleted lap
            SyntheticEvent("BBB", 14, 5),  # noted by race control, lap time kept
            SyntheticEvent("DDD", 13, 4, kind="incident"),
        ),
    )
