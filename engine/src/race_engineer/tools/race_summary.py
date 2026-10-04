"""Summarise a race or sprint from the timing data: the finishing order as timed, safety cars,
pit stops, tyre stints and the fastest lap, with a lap-by-lap position chart.

Built only from the processed laps table (one row per driver and lap: position at the end of
the lap, every track status seen during it, pit flags, tyre stint and compound, lap time and
start time) and the events table (track limits, race-control incidents). The starting grid,
penalties, the official classification and retirement reasons aren't stored, so the order is as
timed on track, places gained count from the end of lap 1, and the summary always says so.

FastF1 sometimes leaves the last lap without a time: a race finishing behind the safety car or
under the VSC, or a live feed that stopped before the last cars crossed the line. So a lap
counts as completed when it has a time, a position or a later lap, and a last lap with neither
still counts when the car was on it as the winner finished (`finishing_order`). The rule that
decides who took the chequered flag mirrors `inference.traffic.chequered_lap`, which can't be
shared while M4 owns that module; merge the two when M4 lands.

The summary is written for the model to relay; `data` is the race-summary chart's payload
(web/src/charts/race-summary.ts).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypedDict

import numpy as np
import pandas as pd
from pydantic import Field

from race_engineer.config import PROCESSED_DIR
from race_engineer.data import store
from race_engineer.tools.lap_comparison import format_lap_time
from race_engineer.tools.params import Event, ToolParams, Year
from race_engineer.tools.registry import DataPaths, ToolOutput, ToolSpec
from race_engineer.tools.sessions import (
    SessionInfo,
    ToolInputError,
    resolve_session,
    session_codes,
    split_event_text,
)

RACE_CODES = frozenset({"R", "S"})
NOT_A_RACE = (
    "get_race_summary covers races and sprints (R, S). For qualifying, use compare_laps or "
    "find_mistakes."
)
DATA_NOTE = (
    "Grid positions, penalties, the official classification and retirement reasons aren't in "
    "the processed data: the order is as timed on track and positions start at the end of lap 1."
)
COLUMNS = [
    "driver",
    "driver_number",
    "team",
    "lap_number",
    "position",
    "track_status",
    "pit_in",
    "pit_out",
    "stint",
    "compound",
    "lap_class",
    "lap_time_s",
    "lap_start_s",
    "deleted",
]

# FastF1 track status codes: 2 yellow, 4 safety car, 5 red flag, 6 VSC deployed, 7 VSC ending.
NEUTRAL_CODES = {"4": "SC", "5": "RED", "6": "VSC", "7": "VSC"}
KIND_ORDER = ("SC", "VSC", "RED")
KIND_TEXT = {"SC": "Safety car", "VSC": "VSC", "RED": "Red flag"}
# Laps that can hold the fastest lap. Yellow laps too: in 9 of the 133 processed races and
# sprints the fastest lap was set on a lap with a yellow flag somewhere on track.
FASTEST_CLASSES = ("race", "yellow")
UNKNOWN_COMPOUNDS = frozenset({"", "UNKNOWN", "TEST_UNKNOWN", "NAN", "NONE"})
COMPOUND_LETTERS = {"SOFT": "S", "MEDIUM": "M", "HARD": "H", "INTERMEDIATE": "I", "WET": "W"}
TOP_MOVERS = 3  # biggest gains and losses listed in the summary

Status = Literal["finished", "lapped", "stopped"]
Kind = Literal["SC", "VSC", "RED"]

RaceSession = Annotated[
    str | None,
    Field(max_length=30, description="Session: 'R' or 'race', 'S' or 'sprint'. Omit for the race."),
]
FocusDriver = Annotated[
    str | None,
    Field(
        max_length=24,
        description="Three-letter driver code, e.g. 'HAM', to add a paragraph on that driver's "
        "race. Omit for the whole field.",
    ),
]


class RaceSummaryParams(ToolParams):
    event: Event
    year: Year = None
    session: RaceSession = None
    driver: FocusDriver = None


# The chart payload, mirroring the RaceSummary interface in web/src/charts/race-summary.ts.
class StintData(TypedDict):
    compound: str
    from_lap: int
    to_lap: int


class DriverData(TypedDict):
    code: str
    team: str
    finish: int | None  # None for a car that stopped
    status: Status
    laps_completed: int
    laps_down: int
    gap_s: float | None  # to the winner, for cars on the winner's lap with both finishes timed
    start_pos: int | None  # at the end of lap 1
    positions: list[int | None]  # at the end of each lap, 1..laps
    pit_laps: list[int]
    stints: list[StintData]
    best_lap_s: float | None


class NeutralisationData(TypedDict):
    kind: Kind
    from_lap: int
    to_lap: int


class FastestLapData(TypedDict):
    driver: str
    lap: int
    time_s: float


class RaceSummaryData(TypedDict):
    event: str
    location: str
    year: int
    round: int
    session: str
    session_code: str  # "R" or "S"
    date: str
    laps: int
    focus: str | None
    drivers: list[DriverData]
    neutralisations: list[NeutralisationData]
    fastest_lap: FastestLapData | None
    notes: list[str]


@dataclass(frozen=True)
class Finish:
    """Where one car ended up, as timed."""

    driver: str
    team: str
    laps: int  # laps completed
    status: Status
    position: int | None  # finishing position as timed; None for a car that stopped
    gap_s: float | None  # to the winner: cars on the winner's lap, both finishes timed
    laps_down: int
    end_s: float  # session time the last completed lap ended (estimated when not timed)
    timed: bool  # end_s comes from the lap times, not an estimate
    untimed_finish: bool  # took the flag on a lap the data has no time or position for


@dataclass(frozen=True)
class RaceSummary:
    session: SessionInfo
    order: list[Finish]
    summary: str  # plain language, for the model
    data: RaceSummaryData  # JSON-ready payload for the chart


# ---------------------------------------------------------------------------------------------
# The rules, one function each


def lap_ends(laps: pd.DataFrame) -> np.ndarray:
    """When each lap ended (session seconds): its start plus its lap time or, without a lap
    time, the start of the same driver's next lap; NaN when neither is known. `laps` sorted by
    driver and lap number."""
    start = laps["lap_start_s"].to_numpy(float)
    ends = start + laps["lap_time_s"].to_numpy(float)
    driver = laps["driver"].to_numpy()
    number = laps["lap_number"].to_numpy()
    following = np.zeros(len(laps), dtype=bool)
    following[:-1] = (driver[1:] == driver[:-1]) & (number[1:] == number[:-1] + 1)
    next_start = np.append(start[1:], np.nan)
    return np.where(np.isnan(ends) & following, next_start, ends)


def typical_lap_s(laps: pd.DataFrame, lap: int) -> float:
    """A typical time for lap `lap` (the median of the field's times for it, else for the lap
    before), used to estimate when an untimed lap ended. 0 when no lap is timed."""
    times = laps["lap_time_s"]
    for number in (lap, lap - 1):
        found = times[laps["lap_number"] == number].dropna()
        if len(found):
            return float(found.median())
    timed = times.dropna()
    return float(timed.median()) if len(timed) else 0.0


@dataclass
class _Run:
    """One car's timing, before the order is known."""

    driver: str
    team: str
    laps: int = 0  # laps completed
    end_s: float = math.nan  # end of the last completed lap: NaN when not timed
    est_end_s: float = math.nan  # end_s, or an estimate from the lap's start and a typical lap
    position: float = math.nan  # at the end of the last completed lap (NaN when not given)
    pending: tuple[int, float] | None = None  # (lap, start) of a last lap not completed
    into_pits: bool = False  # that last lap ended in the pit lane, with no lap time
    untimed_finish: bool = False


def _runs(laps: pd.DataFrame) -> list[_Run]:
    laps = laps.sort_values(["driver", "lap_number"], kind="stable").reset_index(drop=True)
    ends = lap_ends(laps)
    runs = []
    for driver, rows in laps.groupby("driver", sort=False):
        idx = rows.index.to_numpy()
        number = rows["lap_number"].to_numpy(int)
        position = rows["position"].to_numpy(float)
        start = rows["lap_start_s"].to_numpy(float)
        done = np.isfinite(ends[idx]) | np.isfinite(position)
        # A last lap into the pit lane without a lap time is a retirement in the pits, even
        # where FastF1 gives it a position. (The official lap count includes it at some tracks,
        # where the pit lane crosses the timing line before the garages: 18 of 44 such laps.)
        into_pits = bool(rows["pit_in"].to_numpy(bool)[-1] and np.isnan(ends[idx][-1]))
        done[-1] &= not into_pits
        run = _Run(str(driver), _team(rows), into_pits=into_pits)
        if not done[-1]:
            run.pending = (int(number[-1]), float(start[-1]))
        if done.any():
            last = int(np.flatnonzero(done)[-1])
            run.laps, run.end_s, run.position = (
                int(number[last]),
                float(ends[idx][last]),
                float(position[last]),
            )
            run.est_end_s = (
                run.end_s
                if math.isfinite(run.end_s)
                else float(start[last]) + typical_lap_s(laps, run.laps)
            )
        runs.append(run)
    return runs


def _team(rows: pd.DataFrame) -> str:
    teams = rows["team"].dropna().astype(str)
    return str(teams.iloc[-1]) if len(teams) else ""


def _crossing_order(runs: list[_Run]) -> list[_Run]:
    """Most laps first; on the same lap count, in the order they crossed the line: by position
    at the end of that lap when every car of the group has one (the timing positions agree
    with the lap times wherever both exist), else by when the lap ended."""

    def end(r: _Run) -> float:
        return r.est_end_s if math.isfinite(r.est_end_s) else math.inf

    ordered: list[_Run] = []
    for laps in sorted({r.laps for r in runs}, reverse=True):
        group = [r for r in runs if r.laps == laps]
        if all(math.isfinite(r.position) for r in group):
            group.sort(key=lambda r: (r.position, end(r)))
        else:
            group.sort(key=end)
        ordered += group
    return ordered


def finishing_order(laps: pd.DataFrame) -> list[Finish]:
    """Every car, in finishing order as timed, then the cars that stopped.

    The winner is the first car to complete the most laps; it finished when that lap ended.
    A car finished if it completed the winner's number of laps, and is lapped if it completed
    fewer but crossed the line after the winner finished. Any other car stopped, cause unknown.

    A last lap with neither a time nor a position is usually a car stopping on track, but the
    live feed sometimes ends before the last cars cross the line (Monza 2023: nine cars). So it
    counts as a finish when the car started it before the winner finished, a typical lap would
    have ended after, and no crossing at all is timed after then (the feed had stopped). A last
    lap into the pit lane without a time never counts: the car retired in the pits (Miami 2022
    and Mexico City 2023 on the final lap). Gaps are given for cars on the winner's lap whose
    finish is timed.
    """
    runs = _runs(laps)
    if not runs:
        return []
    winner = _crossing_order(runs)[0]
    winner_end = winner.est_end_s
    crossings = (laps["lap_start_s"] + laps["lap_time_s"]).to_numpy(float)
    last_timed = float(np.nanmax(crossings)) if np.isfinite(crossings).any() else math.inf
    for r in runs:
        if r.pending is None or r.into_pits or not math.isfinite(winner_end):
            continue
        lap, start = r.pending
        expected = start + typical_lap_s(laps, lap)
        if start < winner_end <= expected and expected > last_timed:
            r.laps, r.end_s, r.est_end_s, r.position = lap, math.nan, expected, math.nan
            r.untimed_finish = True

    ordered = _crossing_order(runs)
    winner = ordered[0]
    most, winner_end = winner.laps, winner.est_end_s
    winner_timed = math.isfinite(winner.end_s)

    def status(r: _Run) -> Status:
        if r.laps == most:
            return "finished"
        if r.untimed_finish or (r.laps and r.est_end_s >= winner_end):
            return "lapped"
        return "stopped"

    statuses: dict[str, Status] = {r.driver: status(r) for r in ordered}
    running = [r for r in ordered if statuses[r.driver] != "stopped"]
    stopped = [r for r in ordered if statuses[r.driver] == "stopped"]
    finishes = []
    for i, r in enumerate(running + stopped):
        timed = math.isfinite(r.end_s)
        gap = None
        if r.laps == most and timed and winner_timed:
            gap = round(r.end_s - winner_end, 3)
        finishes.append(
            Finish(
                driver=r.driver,
                team=r.team,
                laps=r.laps,
                status=statuses[r.driver],
                position=i + 1 if i < len(running) else None,
                gap_s=gap,
                laps_down=most - r.laps,
                end_s=r.est_end_s,
                timed=timed,
                untimed_finish=r.untimed_finish,
            )
        )
    return finishes


def position_matrix(laps: pd.DataFrame, n_laps: int) -> dict[str, list[int | None]]:
    """Each driver's position at the end of laps 1..n_laps (None where FastF1 gives none: a
    lap not completed, or one of the ~0.1% of laps it leaves blank)."""
    drivers = list(dict.fromkeys(laps["driver"]))
    wide = laps.pivot_table(index="driver", columns="lap_number", values="position", aggfunc="min")
    wide = wide.reindex(index=drivers, columns=range(1, n_laps + 1))
    return {
        str(driver): [None if math.isnan(p) else int(p) for p in row]
        for driver, row in zip(wide.index, wide.to_numpy(float), strict=True)
    }


def leader_laps(laps: pd.DataFrame) -> pd.DataFrame:
    """The race leader's row for each lap number: the lowest position, or, on a lap where no
    car has one, the first car to start that lap."""
    ranked = laps.assign(_rank=laps["position"].fillna(np.inf))
    ranked = ranked.sort_values(["lap_number", "_rank", "lap_start_s"], kind="stable")
    return ranked.drop_duplicates("lap_number").drop(columns="_rank")


def _status_runs(laps: pd.DataFrame, codes: str) -> list[tuple[int, int]]:
    """Runs of consecutive leader laps whose track status includes any of `codes`."""
    lead = leader_laps(laps)
    status = lead["track_status"].fillna("").astype(str)
    hit = sorted(
        int(n) for n, s in zip(lead["lap_number"], status, strict=True) if set(s) & set(codes)
    )
    found: list[tuple[int, int]] = []
    for n in hit:
        if found and n == found[-1][1] + 1:
            found[-1] = (found[-1][0], n)
        else:
            found.append((n, n))
    return found


def neutralisations(laps: pd.DataFrame) -> list[NeutralisationData]:
    """Safety-car, VSC and red-flag periods as runs of the leader's laps (track status 4; 6 or
    7; 5). A lap holds every code seen during it, so a period counts from the lap it began."""
    found: list[NeutralisationData] = []
    for kind in KIND_ORDER:
        codes = "".join(c for c, k in NEUTRAL_CODES.items() if k == kind)
        found += [
            NeutralisationData(kind=kind, from_lap=a, to_lap=b)
            for a, b in _status_runs(laps, codes)
        ]
    return sorted(found, key=lambda n: (n["from_lap"], KIND_ORDER.index(n["kind"])))


def yellow_laps(laps: pd.DataFrame) -> int:
    """How many of the leader's laps saw a yellow flag somewhere (track status 2)."""
    return sum(b - a + 1 for a, b in _status_runs(laps, "2"))


def pit_stops(laps: pd.DataFrame) -> dict[str, list[int]]:
    """Each driver's in-laps: laps ending in the pit lane that the driver rejoined from. Not a
    driver's last lap (retiring into the pits isn't a stop) and not a lap with a red flag, when
    every car is sent to the pit lane and may change tyres there."""
    stops: dict[str, list[int]] = {}
    for driver, rows in laps.groupby("driver", sort=False):
        last = rows["lap_number"].max()
        red = rows["track_status"].fillna("").astype(str).str.contains("5")
        hit = rows["pit_in"].fillna(False).astype(bool) & (rows["lap_number"] < last) & ~red
        stops[str(driver)] = sorted(int(n) for n in rows["lap_number"].to_numpy()[hit.to_numpy()])
    return stops


def stints(laps: pd.DataFrame) -> dict[str, list[StintData]]:
    """Each driver's tyre stints (FastF1's stint number, which also changes when tyres are
    changed under a red flag): the most common compound and the first and last lap."""
    found: dict[str, list[StintData]] = {}
    for driver, rows in laps.sort_values(["driver", "lap_number"]).groupby("driver", sort=False):
        stint = rows["stint"].ffill().bfill().fillna(1.0)
        runs = (stint != stint.shift()).cumsum()
        found[str(driver)] = [
            StintData(
                compound=_compound(group["compound"]),
                from_lap=int(group["lap_number"].min()),
                to_lap=int(group["lap_number"].max()),
            )
            for _, group in rows.groupby(runs, sort=False)
        ]
    return found


def _compound(values: pd.Series) -> str:
    names = [str(v).upper() for v in values.dropna()]
    known = [n for n in names if n not in UNKNOWN_COMPOUNDS]
    return Counter(known).most_common(1)[0][0] if known else "UNKNOWN"


def _fastest_candidates(laps: pd.DataFrame) -> pd.DataFrame:
    ok = laps["lap_class"].isin(FASTEST_CLASSES) & ~laps["deleted"].fillna(False).astype(bool)
    return laps[ok & laps["lap_time_s"].notna()]


def fastest_lap(laps: pd.DataFrame) -> FastestLapData | None:
    """The quickest green or yellow-flag lap that wasn't deleted (pit, opening and neutralised
    laps can't be it, and FastF1 sometimes times them impossibly fast)."""
    candidates = _fastest_candidates(laps)
    if candidates.empty:
        return None
    best = candidates.sort_values(["lap_time_s", "lap_start_s"], kind="stable").iloc[0]
    return FastestLapData(
        driver=str(best["driver"]),
        lap=int(best["lap_number"]),
        time_s=round(float(best["lap_time_s"]), 3),
    )


def best_laps(laps: pd.DataFrame) -> dict[str, float]:
    """Each driver's best lap by the fastest-lap rule."""
    best = _fastest_candidates(laps).groupby("driver")["lap_time_s"].min()
    return {str(d): round(float(t), 3) for d, t in best.items()}


# ---------------------------------------------------------------------------------------------
# The tool


def _check_session(event: str, session: str | None) -> str | None:
    """The session to resolve: as given, as named in the event text, or the race. Qualifying
    sessions are refused before the lookup, so the error says what this tool is for."""
    _, _, named = split_event_text(event)
    for text in (session, named):
        if text is not None and not set(session_codes(text)) & RACE_CODES:
            raise ToolInputError(NOT_A_RACE)
    return session if session is not None or named else "R"


def _load_laps(info: SessionInfo, root: Path) -> pd.DataFrame:
    path = store.table_path("laps", info.key, root)
    if not path.exists():
        raise ToolInputError(f"No lap data stored for the {info.label}.")
    laps = pd.read_parquet(path, columns=COLUMNS)
    laps["driver"] = laps["driver"].astype(str)
    laps["driver_number"] = laps["driver_number"].astype(str)
    laps["team"] = laps["team"].fillna("").astype(str)
    laps["track_status"] = laps["track_status"].fillna("").astype(str)
    laps["deleted"] = laps["deleted"].fillna(False).astype(bool)
    laps["pit_in"] = laps["pit_in"].fillna(False).astype(bool)
    return laps.sort_values(["driver", "lap_number"], kind="stable").reset_index(drop=True)


def _load_events(info: SessionInfo, root: Path) -> pd.DataFrame:
    path = store.table_path("events", info.key, root)
    columns = ["driver", "lap_number", "turn", "kind"]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    return pd.read_parquet(path, columns=columns)


def _focus_driver(text: str, laps: pd.DataFrame, info: SessionInfo) -> str:
    """The driver code for a three-letter code (any case) or car number."""
    code = text.strip().lstrip("#").upper()
    hit = laps[(laps["driver"].str.upper() == code) | (laps["driver_number"] == code)]
    if hit.empty:
        roster = laps.drop_duplicates("driver").sort_values("driver")
        names = ", ".join(f"{r.driver} ({r.team})" for r in roster.itertuples())
        raise ToolInputError(
            f"No driver {text!r} in the {info.label}. Drivers: {names}. Use the three-letter code."
        )
    return str(hit["driver"].iloc[0])


def race_summary(
    event: str,
    year: int | None = None,
    session: str | None = None,
    driver: str | None = None,
    root: Path = PROCESSED_DIR,
) -> RaceSummary:
    """Summarise one race or sprint (the race when `session` is omitted), optionally with a
    paragraph on one driver."""
    info = resolve_session(event, year, _check_session(event, session), root)
    if info.code not in RACE_CODES:
        raise ToolInputError(NOT_A_RACE)
    laps = _load_laps(info, root)
    if laps.empty:
        raise ToolInputError(f"No laps stored for the {info.label}.")
    focus = _focus_driver(driver, laps, info) if driver and driver.strip() else None
    events = _load_events(info, root)

    order = finishing_order(laps)
    n_laps = order[0].laps
    if not n_laps:
        raise ToolInputError(f"No completed lap is stored for the {info.label}.")
    positions = position_matrix(laps, n_laps)
    stops = pit_stops(laps)
    tyres = stints(laps)
    best = best_laps(laps)
    race_laps = laps[laps["lap_number"] <= n_laps]  # not a last lap no car completed
    periods = neutralisations(race_laps)
    fastest = fastest_lap(laps)
    notes = [DATA_NOTE, *_timing_notes(order, laps)]

    drivers = [
        DriverData(
            code=f.driver,
            team=f.team,
            finish=f.position,
            status=f.status,
            laps_completed=f.laps,
            laps_down=f.laps_down,
            gap_s=f.gap_s,
            start_pos=positions[f.driver][0],
            positions=positions[f.driver],
            pit_laps=stops.get(f.driver, []),
            stints=tyres.get(f.driver, []),
            best_lap_s=best.get(f.driver),
        )
        for f in order
    ]
    data = RaceSummaryData(
        event=info.event,
        location=info.location,
        year=info.year,
        round=info.round,
        session=info.name,
        session_code=info.code,
        date=info.date,
        laps=n_laps,
        focus=focus,
        drivers=drivers,
        neutralisations=periods,
        fastest_lap=fastest,
        notes=notes,
    )
    parts = [
        _header(info, n_laps),
        _result_line(order),
        _order_line(order),
        _periods_line(periods, yellow_laps(race_laps)),
        _movers_line(drivers),
        _stops_line(drivers, any(p["kind"] == "RED" for p in periods)),
    ]
    if fastest is not None:
        parts.append(
            f"Fastest lap: {fastest['driver']} {format_lap_time(fastest['time_s'])} "
            f"(lap {fastest['lap']})."
        )
    if focus is not None:
        parts.append(_focus_paragraph(focus, drivers, laps, events, fastest))
    parts += notes[1:]
    parts.append(DATA_NOTE)
    summary = "\n".join(p for p in parts if p)
    return RaceSummary(info, order, summary, data)


def _timing_notes(order: list[Finish], laps: pd.DataFrame) -> list[str]:
    """What the timing data leaves out: last laps without a time, laps without a position."""
    notes = []
    running = [f for f in order if f.status != "stopped"]
    untimed = [f.driver for f in running if not f.timed]
    if untimed:
        cars = (
            "any car"
            if len(untimed) == len(running)
            else f"{_plural(len(untimed), 'car')} ({', '.join(untimed)})"
        )
        notes.append(
            f"The timing data has no time for the last lap of {cars}: their order comes from "
            "the timing positions or from the lap before, and no gap is given."
        )
    blank = int((laps["lap_time_s"].notna() & laps["position"].isna()).sum())
    if blank:
        notes.append(
            f"{_plural(blank, 'timed lap')} without a position in the timing data: the chart "
            "leaves a gap there."
        )
    return notes


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _header(info: SessionInfo, n_laps: int) -> str:
    return (
        f"{info.year} {info.event}, {info.name} ({info.code}), {info.date}, {info.location}: "
        f"{n_laps} laps."
    )


def _gap_text(laps_down: int, gap_s: float | None) -> str:
    """ "+1 lap", "+12.3 s", or "" when the gap isn't timed."""
    if laps_down:
        return f"+{_plural(laps_down, 'lap')}"
    return f"+{gap_s:.1f} s" if gap_s is not None else ""


def _result_line(order: list[Finish]) -> str:
    winner = order[0]
    line = f"Won by {winner.driver} ({winner.team})"
    second = order[1] if len(order) > 1 and order[1].status != "stopped" else None
    if second is not None and second.status == "finished" and second.gap_s is not None:
        line += f" by {second.gap_s:.3f} s from {second.driver}"
    elif second is not None:
        gap = _gap_text(second.laps_down, second.gap_s)
        line += f", ahead of {second.driver}" + (f" ({gap})" if gap else "")
    return line + ", as timed."


def _order_line(order: list[Finish]) -> str:
    running = [f for f in order if f.status != "stopped"]
    text = "; ".join(
        " ".join(
            [str(f.position), f.driver, *([_gap_text(f.laps_down, f.gap_s)] if i else [])]
        ).strip()
        for i, f in enumerate(running)
    )
    line = f"Order as timed: {text}."
    stopped = [f for f in order if f.status == "stopped"]
    if stopped:
        cars = ", ".join(
            f"{f.driver} after {_plural(f.laps, 'lap')}" if f.laps else f"{f.driver} on lap 1"
            for f in stopped
        )
        line += f" Stopped (cause not in the data): {cars}."
    return line


def _laps_text(a: int, b: int) -> str:
    return f"lap {a}" if a == b else f"laps {a}-{b}"


def _periods_line(periods: list[NeutralisationData], yellow: int) -> str:
    parts = []
    for kind in KIND_ORDER:
        mine = [_laps_text(p["from_lap"], p["to_lap"]) for p in periods if p["kind"] == kind]
        parts.append(f"{KIND_TEXT[kind]}: {', '.join(mine) if mine else 'none'}.")
    if yellow:
        parts.append(f"Yellow flags on {_plural(yellow, 'lap')}.")
    return " ".join(parts) + " (Laps are the leader's.)"


def _movers_line(drivers: list[DriverData]) -> str:
    moved = [
        (d["start_pos"] - d["finish"], d)
        for d in drivers
        if d["finish"] is not None and d["start_pos"] is not None
    ]
    gains = sorted((m for m in moved if m[0] > 0), key=lambda m: -m[0])[:TOP_MOVERS]
    losses = sorted((m for m in moved if m[0] < 0), key=lambda m: m[0])[:TOP_MOVERS]

    def text(found: list[tuple[int, DriverData]]) -> str:
        return ", ".join(
            f"{d['code']} {n:+d} (P{d['start_pos']} to P{d['finish']})" for n, d in found
        )

    parts = []
    if gains:
        parts.append(f"gained most: {text(gains)}")
    if losses:
        parts.append(f"lost most: {text(losses)}")
    if not parts:
        return ""
    return "From the end of lap 1 to the finish, " + "; ".join(parts) + "."


def _tyres(stints: list[StintData], full: bool = False) -> str:
    names = [s["compound"] for s in stints]
    if full:
        return " to ".join(names)
    return "-".join(COMPOUND_LETTERS.get(n, n.title()) for n in names)


def _stops_line(drivers: list[DriverData], red_flag: bool) -> str:
    parts = []
    for d in drivers:
        laps = d["pit_laps"]
        where = f"lap{'s' if len(laps) > 1 else ''} {', '.join(map(str, laps))}; " if laps else ""
        parts.append(f"{d['code']} {len(laps)} ({where}{_tyres(d['stints'])})")
    # Under a red flag every car waits in the pit lane and may change tyres there: a new stint
    # without a stop.
    red = " Tyre changes during a red flag aren't counted as stops." if red_flag else ""
    return (
        "Pit stops and tyres (S soft, M medium, H hard, I intermediate, W wet): "
        + ", ".join(parts)
        + "."
        + red
    )


def _focus_paragraph(
    code: str,
    drivers: list[DriverData],
    laps: pd.DataFrame,
    events: pd.DataFrame,
    fastest: FastestLapData | None,
) -> str:
    """One driver's race: start and finish, stops and tyres, best lap, laps neutralised, track
    limits and race-control incidents."""
    d = next(d for d in drivers if d["code"] == code)
    mine = laps[laps["driver"] == code]
    start = (
        f"P{d['start_pos']} at the end of lap 1" if d["start_pos"] else "no position after lap 1"
    )
    if d["status"] == "stopped":
        done = d["laps_completed"]
        last = d["positions"][done - 1] if done else None
        running = f", running P{last}" if last else ""
        result = (
            f"stopped after {_plural(done, 'lap')}{running}" if done else "stopped on lap 1"
        ) + " (cause not in the data)"
    else:
        gap = "the winner" if d["finish"] == 1 else _gap_text(d["laps_down"], d["gap_s"])
        result = f"finished P{d['finish']}" + (f" ({gap})" if gap else "")
        if d["start_pos"] and d["finish"]:
            moved = d["start_pos"] - d["finish"]
            gained = "gained" if moved > 0 else "lost"
            result += f", {_plural(abs(moved), 'place')} {gained}" if moved else ", same place"
    stops = d["pit_laps"]
    stop_text = (
        f"{_plural(len(stops), 'stop')} (lap{'s' if len(stops) > 1 else ''} "
        f"{', '.join(map(str, stops))})"
        if stops
        else "No pit stops"
    )
    lines = [
        f"{code} ({d['team']}): {start}, {result}. {stop_text}; tyres "
        f"{_tyres(d['stints'], full=True)}."
    ]
    if d["best_lap_s"] is not None:
        candidates = _fastest_candidates(mine).sort_values("lap_time_s", kind="stable")
        best = int(candidates["lap_number"].to_numpy()[0])
        is_fastest = fastest is not None and fastest["driver"] == code
        lines.append(
            f"Best lap {format_lap_time(d['best_lap_s'])} (lap {best})"
            + (", the fastest lap of the race." if is_fastest else ".")
        )
    neutral = int(mine["track_status"].str.contains("[467]").sum())
    if neutral:
        lines.append(f"{_plural(neutral, 'lap')} behind the safety car or under the VSC.")
    limits = events[(events["driver"] == code) & (events["kind"] == "track_limits")]
    deleted = int(mine["deleted"].sum())
    if len(limits) or deleted:
        removed = _plural(deleted, "lap time") if deleted else "no lap time"
        lines.append(
            f"Track limits: {_plural(max(len(limits), deleted), 'violation')} noted "
            f"({removed} deleted)."
        )
    incidents = events[(events["driver"] == code) & (events["kind"] == "incident")]
    if len(incidents):
        noted = incidents.drop_duplicates(["lap_number", "turn"])
        where = ", ".join(
            f"lap {lap} turn {turn}"
            for lap, turn in zip(noted["lap_number"].tolist(), noted["turn"].tolist(), strict=True)
        )
        lines.append(f"Race control noted incidents involving {code}: {where}.")
    return " ".join(lines)


def _run(p: RaceSummaryParams, paths: DataPaths) -> ToolOutput:
    found = race_summary(p.event, p.year, p.session, p.driver, paths.processed)
    return ToolOutput(found.summary, dict(found.data))


RACE_SUMMARY = ToolSpec(
    name="get_race_summary",
    title="Summarise a race",
    description=(
        "Summarise a race or sprint from the timing data: finishing order as timed on track "
        "(laps completed, gaps to the winner, lapped cars and cars that stopped), positions "
        "gained or lost from the end of lap 1, safety-car, virtual-safety-car and red-flag "
        "periods, pit stops and tyre stints, and the fastest lap, with a lap-by-lap position "
        "chart. Give driver to focus on one driver's race. Use it for 'who won the 2025 Monaco "
        "GP?', 'how did Hamilton's race go in Baku?' or for context before discussing mistakes "
        "in a race. Grid positions, penalties, the official classification and retirement "
        "reasons are not in the data, and the result says so."
    ),
    params=RaceSummaryParams,
    run=_run,
    chart="race-summary",
)
