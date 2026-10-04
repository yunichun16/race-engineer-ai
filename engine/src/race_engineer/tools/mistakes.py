"""Ask about driver mistakes: the biggest ones in a session, and one corner in detail.

find_mistakes reads the batch results (`data/results/mistakes.parquet`, written by
`inference.explain`): every corner the detectors flagged, with its type, time lost and a
plain-English explanation. explain_corner works live from the processed session, so it answers
for any corner on any lap, flagged or not, with the traces a chart needs.

Time lost is always against the driver's usual way through that turn in the same session (the
median of their other clean laps there, or of the field's when they have fewer than five), so
car pace doesn't count as a mistake. Neighbouring turns whose windows overlap on the same lap
are one moment seen twice; find_mistakes lists them once (`inference.explain.merge_overlapping`).
Nor does traffic: a corner lost to other cars (being lapped, racing for a place, held up by a
slower car) is counted, not listed, and explain_corner names the cars around
(`inference.traffic`, `inference.explain.traffic_finding`).

The detectors score only some laps (evaluation.data.ELIGIBLE_SQL: no opening, in or out laps,
nothing under yellow flags or a safety car), and only where the telemetry is usable, so "no
mistakes" can also mean "nothing scored". Both tools say which it is: find_mistakes gives the
laps it covers, says so when a driver has nothing scored, and refuses a session the scores
don't cover; explain_corner says why a corner has no detector verdict. Both use the batch
results only when they come from the current scoring run (`inference.provenance`), so they
never mix two runs' outputs: find_mistakes refuses stale results, and explain_corner, whose
comparison needs only the processed session, answers without the verdict.

Both tools' chart data carry a map (`tools.mistake_charts`): find_mistakes the session's, and
explain_corner the corner's, with the cars around when the driver reached the apex and the
corner as an animation. explain_corner reads the session's positions once
(`tools.replay.load_session_tracks`, cached) for both its traffic explanation and that replay,
so the two agree, and the replay's ghost is the reference lap whose time through the corner is
closest to the usual (the reference's median); the chart also has the reference laps' middle
half (25th to 75th percentile) of speed and throttle, and the lateral offset from the
reference line.
"""

from __future__ import annotations

import math
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from race_engineer.config import PROCESSED_DIR, RESULTS_DIR
from race_engineer.data import store
from race_engineer.evaluation.data import ELIGIBLE_SQL
from race_engineer.features.handcrafted import DISTANCE
from race_engineer.inference import provenance
from race_engineer.inference.explain import (
    ADVERSE,
    DEVIATIONS,
    PHASE_EDGES_M,
    PHASE_TEXT,
    PHASES,
    SAME_APEX_M,
    THRESHOLDS,
    TYPE_TEXT,
    CornerComparison,
    Explanation,
    SessionSegments,
    cars_around_text,
    explain,
    listed_flags,
    load_session_events,
    load_session_segments,
    traffic_explained,
    turn_label,
)
from race_engineer.inference.traffic import Encounter
from race_engineer.tools.cache import memo_by_files
from race_engineer.tools.lap_comparison import LAP_CLASS_TEXT, _rounded, sample_indices
from race_engineer.tools.mistake_charts import map_and_replay, session_track
from race_engineer.tools.replay import load_session_tracks
from race_engineer.tools.sessions import (
    ResultsUnavailableError,
    SessionInfo,
    ToolInputError,
    resolve_session,
)

MISTAKES_FILE = "mistakes.parquet"
SCORES_FILE = "segment_scores.parquet"
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
TOP_DEVIATIONS = 6  # listed in explain_corner's summary; the chart payload has them all
# When fewer than this share of the laps the detectors score have a scored corner, find_mistakes
# warns that telemetry is missing. For the whole field that is three sessions: the 2026 Monaco
# and Hungarian races (scores for laps 2-5 and 2-6 only, 6% and 8%) and 2026 Austria qualifying
# (47%).
COVERAGE_WARN = 0.5
MAX_RANGES = 5  # lap ranges spelled out in find_mistakes' coverage line; more are summarised
# Sessions whose rows of the batch results are kept in memory (`_session_scores`: about 7,000
# rows, a megabyte or two; `_load_mistakes`: about 70 rows).
SCORES_CACHE_SIZE = 8
MISTAKES_CACHE_SIZE = 16

# The laps the detectors score, in words (evaluation.data.ELIGIBLE_SQL is the rule itself).
SCORED_LAPS = (
    "push laps in qualifying and green-flag laps in races and sprints, slow ones included "
    "(never opening laps, in or out laps, or laps under yellow flags, a safety car or a red flag)"
)
RERUN = "run `race-engineer-infer score`, then the later steps (`make m4`)"
LAP_TEXT = LAP_CLASS_TEXT  # lap classes as explain_corner's header names them
# How find_mistakes counts a driver's laps by class when none of them was scored.
LAP_COUNT_TEXT = {
    "push": ("push lap", "push laps"),
    "race": ("green-flag lap", "green-flag laps"),
    "start": ("opening lap", "opening laps"),
    "slow": ("slow lap", "slow laps"),
    "yellow": ("lap under yellow flags", "laps under yellow flags"),
    "neutralised": ("lap under a safety car or red flag", "laps under a safety car or red flag"),
    "in": ("in lap", "in laps"),
    "out": ("out lap", "out laps"),
    "no_time": ("lap without a lap time", "laps without a lap time"),
}

# What each compared feature is called, and its unit, in explain_corner's answer.
FEATURE_LABELS = {
    "v_start": ("speed 250 m before the apex", "km/h"),
    "brake_start_d": ("braking point", "m from the apex"),
    "brake_len_m": ("distance braking", "m"),
    "decel_max": ("peak deceleration", "m/s²"),
    "v_min": ("slowest point", "km/h"),
    "throttle_pickup_d": ("back on full throttle", "m from the apex"),
    "throttle_dip": ("throttle lift after the pickup", "%"),
    "coast_m": ("coasting (off both pedals)", "m"),
    "v_exit": ("speed 145 m after the apex", "km/h"),
    "exit_accel": ("acceleration after the apex", "m/s²"),
    "offset_exit_max": ("widest off the usual line on exit", "m"),
    "segment_time_s": ("time through the corner", "s"),
}
RANKING_TEXT = (
    "ranked by time lost (only the part beyond the field's when the field was slow there too, "
    "and none of it when other cars explain it: being lapped, racing for a place or held up by "
    "cars already at the driver as the loss began, so such a flag ranks on the race-control "
    "bonus alone), plus 0.5 s when race control named the driver alone at that turn, discounted "
    "by up to half when the detectors found the corner only borderline unusual"
)


# ---------------------------------------------------------------------------------------------
# Shared helpers


def _laps(info: SessionInfo, root: Path) -> pd.DataFrame:
    """Every lap of the session in the laps table: driver, car number, team, lap and class."""
    path = store.table_path("laps", info.key, root)
    if not path.exists():
        raise ToolInputError(f"No lap data stored for the {info.label}.")
    columns = ["driver", "driver_number", "team", "lap_number", "lap_class", "session"]
    laps = pd.read_parquet(path, columns=columns)
    laps["team"] = laps["team"].fillna("").astype(str)
    return laps


def _roster(laps: pd.DataFrame) -> pd.DataFrame:
    """Drivers of the session: code, car number and team."""
    roster = laps[["driver", "driver_number", "team"]].drop_duplicates("driver")
    return roster.sort_values("driver").reset_index(drop=True)


def _driver(text: str, roster: pd.DataFrame, info: SessionInfo) -> pd.Series:
    """The roster row for a three-letter code (any case) or car number."""
    code = text.strip().lstrip("#").upper()
    hit = roster[(roster["driver"].str.upper() == code) | (roster["driver_number"] == code)]
    if hit.empty:
        names = ", ".join(f"{r.driver} ({r.team})" for r in roster.itertuples())
        raise ToolInputError(
            f"No driver {text!r} in the {info.label}. Drivers: {names}. Use the three-letter code."
        )
    return hit.iloc[0]


def _check_results(results: Path) -> None:
    """Raise ResultsUnavailableError (a ToolInputError), saying how to rebuild them, unless the
    batch results exist and come from the current scoring run (see inference.provenance)."""
    try:
        provenance.check_current(results / MISTAKES_FILE, "explain", results)
        # The scores are the scoring run's own output (the manifest stands for them); when the
        # file records the run it came from, that must be the current one too.
        scores = results / SCORES_FILE
        if not scores.exists() or provenance.parquet_run(scores) is not None:
            provenance.check_current(scores, "score", results)
    except provenance.StaleResultsError as exc:
        raise ResultsUnavailableError(f"The M4 results can't be used: {exc}") from None


def _results_dir(arguments: Mapping[str, Any]) -> Path:
    return arguments["results"]


@memo_by_files(
    lambda a: a["results"] / SCORES_FILE, results=_results_dir, maxsize=SCORES_CACHE_SIZE
)
def _session_scores(info: SessionInfo, results: Path) -> pd.DataFrame:
    """The detectors' scores for every scored corner of the session (empty when the scores
    don't cover it). Call _check_results first.

    The file has only two row groups, so a filtered read scans most of its 61 MB. DuckDB
    streams it, keeping the session's rows as it goes (pandas' filtered read decodes both row
    groups whole: on the 2023 Monaco GP 0.09 s and about 150 MB at peak, against 0.03 s and
    15 MB), and the result is cached until the file or the scoring run changes (tools.cache).
    Shared: read-only."""
    query = (
        "select segment_id, driver, lap_number, flagged, score_pct from read_parquet(?) "
        "where year = ? and round = ? and session = ?"
    )
    con = duckdb.connect()
    try:
        path = str(results / SCORES_FILE)
        return con.execute(query, [path, info.year, info.round, info.code]).df()
    finally:
        con.close()


def _eligible(frame: pd.DataFrame) -> np.ndarray:
    """Which rows (laps or corner segments, with `session` and `lap_class`) are on laps the
    detectors score: evaluation.data.ELIGIBLE_SQL, the rule the scoring run applies."""
    part = frame[["session", "lap_class"]].assign(i=np.arange(len(frame)))
    con = duckdb.connect()
    try:
        con.register("part", part)
        query = f"select coalesce({ELIGIBLE_SQL}, false) as ok from part order by i"
        ok = con.sql(query).fetchnumpy()["ok"]
    finally:
        con.close()
    return np.asarray(ok, dtype=bool)


def _scoreable(info: SessionInfo, root: Path) -> pd.DataFrame:
    """The session's corner segments (driver, lap) on laps the detectors score: what a scoring
    run covering the session has a score for."""
    path = store.table_path("segments", info.key, root)
    if not path.exists():
        return pd.DataFrame(columns=["driver", "lap_number"])
    seg = pd.read_parquet(path, columns=["driver", "lap_number", "session", "lap_class"])
    return seg.loc[_eligible(seg), ["driver", "lap_number"]]


def _not_covered(info: SessionInfo, root: Path) -> str:
    """Why the scores have no row for the session, which is covered only if it has something
    to score."""
    n = len(_scoreable(info, root))
    if not n:
        return (
            f"The detectors have nothing to score in the {info.label}: none of its corners with "
            f"usable telemetry is on a lap they score. They score only {SCORED_LAPS}."
        )
    return (
        f"The M4 scores don't cover the {info.label}, although it has {n:,} corners the "
        f"detectors score: it was processed after the last scoring run. To score it, {RERUN}."
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _lap_ranges(laps: list[int]) -> str:
    """Sorted lap numbers as ranges: "2-5, 7, 9-12" (the span when there are many gaps)."""
    if not laps:
        return "none"
    runs: list[list[int]] = []
    for n in laps:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    if len(runs) > MAX_RANGES:
        return f"{laps[0]}-{laps[-1]} (with gaps)"
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


# ---------------------------------------------------------------------------------------------
# find_mistakes


@dataclass(frozen=True)
class Mistake:
    driver: str
    team: str
    lap_number: int
    turn: str  # "7" or "9a"
    mistake_type: str
    secondary_type: str
    time_lost_s: float
    phase: str  # entry, apex, exit or "" (no time lost)
    score_pct: float  # detector score percentile within the session
    priority: float  # the ranking value (see inference.explain.priority)
    lap_class: str
    explanation: str
    also_turns: tuple[str, ...] = ()  # neighbouring turns flagged on the same lap (same moment)
    apex_m: float = math.nan  # where the turn's apex is along the lap (for the chart's map)

    def as_dict(self) -> dict[str, Any]:
        return {
            "driver": self.driver,
            "team": self.team,
            "lap_number": self.lap_number,
            "turn": self.turn,
            "also_turns": list(self.also_turns),
            "type": self.mistake_type,
            "type_text": TYPE_TEXT.get(self.mistake_type, self.mistake_type),
            "secondary_type": self.secondary_type,
            "time_lost_s": _shown_s(self.time_lost_s),
            "phase": self.phase,
            "score_pct": round(self.score_pct, 4),
            "lap_class": self.lap_class,
            "explanation": self.explanation,
            "apex_m": _optional(self.apex_m),
        }


@dataclass(frozen=True)
class Coverage:
    """Which laps in scope have a scored corner, against the laps the detectors score."""

    laps: tuple[int, ...]  # lap numbers with at least one scored corner
    scored: int  # driver-laps with at least one scored corner
    eligible: int  # driver-laps of the classes the detectors score, usable telemetry or not
    session_laps: int  # the session's last lap number

    @property
    def low(self) -> bool:
        """Telemetry is missing for much of what the detectors would score."""
        return self.eligible > 0 and self.scored < COVERAGE_WARN * self.eligible

    def text(self, driver: str | None) -> str:
        whose, unit = (f"{driver}'s", "laps") if driver else ("the", "driver-laps")
        line = (
            f"Laps scored: {_lap_ranges(list(self.laps))} of {self.session_laps}; "
            f"{self.scored:,} of {whose} {self.eligible:,} {unit} of the kinds the detectors "
            f"score, which are {SCORED_LAPS}."
        )
        if self.low:
            line += (
                f" Only {self.scored / self.eligible:.0%} of those have usable telemetry, so "
                "few flags here doesn't mean few mistakes."
            )
        return line

    def as_dict(self) -> dict[str, Any]:
        return {
            "laps": list(self.laps),
            "scored_driver_laps": self.scored,
            "eligible_driver_laps": self.eligible,
            "session_laps": self.session_laps,
            "low": self.low,
        }


@dataclass(frozen=True)
class MistakeList:
    session: SessionInfo
    driver: str | None
    mistakes: list[Mistake]
    flagged: int  # flagged corners in scope, listed or not
    listed: int  # distinct mistakes in scope (overlapping neighbouring turns counted once)
    scored: int  # corners scored in scope (0: a driver with nothing scored, see `note`)
    left_out: dict[str, int]  # flagged corners not listed, by type
    coverage: Coverage
    note: str  # why nothing was scored, for a driver with no scored corners ("" otherwise)
    summary: str
    data: dict[str, Any]


@memo_by_files(
    lambda a: a["results"] / MISTAKES_FILE, results=_results_dir, maxsize=MISTAKES_CACHE_SIZE
)
def _load_mistakes(info: SessionInfo, results: Path) -> pd.DataFrame:
    """The session's rows of mistakes.parquet. Call _check_results first. Cached until the
    file or the scoring run changes (tools.cache); shared: read-only."""
    return pd.read_parquet(
        results / MISTAKES_FILE,
        filters=[("year", "=", info.year), ("round", "=", info.round), ("session", "=", info.code)],
    )


def _mistake(row: dict) -> Mistake:
    apex_m = row.get("apex_m")  # missing from results written before the column
    return Mistake(
        driver=str(row["driver"]),
        team=str(row["team"] or ""),
        lap_number=int(row["lap_number"]),
        turn=turn_label(row["corner"], str(row["corner_letter"] or "")),
        mistake_type=str(row["mistake_type"]),
        secondary_type=str(row["secondary_type"] or ""),
        time_lost_s=float(row["time_lost_s"]),
        phase=str(row["phase"] or ""),
        score_pct=float(row["score_pct"]),
        priority=float(row["priority"]),
        lap_class=str(row["lap_class"]),
        explanation=str(row["explanation"]),
        also_turns=tuple(t for t in str(row["also_turns"] or "").split(", ") if t),
        apex_m=math.nan if apex_m is None else float(apex_m),
    )


def find_mistakes(
    event: str,
    driver: str | None = None,
    year: int | None = None,
    session: str | None = None,
    limit: int = DEFAULT_LIMIT,
    root: Path = PROCESSED_DIR,
    results: Path = RESULTS_DIR,
) -> MistakeList:
    """The biggest explained mistakes in one session (qualifying unless `session` or the event
    text names another), optionally for one driver.

    Only flagged corners (the top 1% most unusual of the session) that look like driving
    mistakes are listed, ranked by `priority`: time lost, discounted by up to half when the
    detectors found the corner only borderline unusual. Time lost is what a mistake costs; the
    discount keeps a slow corner the detectors barely noticed (often traffic) below an equally
    slow one they are sure about. Flags that aren't mistakes at that corner (not clearly slower
    than usual, part of an abandoned lap, energy management, data problems, traffic: being
    lapped, racing another car or held up by a slower one, and in qualifying a lap that wasn't
    a full push lap) are counted, not listed, and so are the driving flags of a neighbouring
    turn whose loss the same traffic covers. Flags at neighbouring turns of the same lap whose
    windows overlap are one mistake,
    listed once with the others named (never adding up their losses: the windows cover the
    same track): under the turn race control named when there is one, otherwise under the first
    turn whose loss wasn't mostly carried in from before it (explain.merge_overlapping).

    The answer says which laps were scored. Raises ToolInputError when the batch results are
    missing or from another scoring run, or don't cover the session; a driver with no scored
    corners gets an answer saying so and why, never "no mistakes".
    """
    info = resolve_session(event, year, session, root)
    laps = _laps(info, root)
    code = None
    if driver is not None and driver.strip():
        code = str(_driver(driver, _roster(laps), info)["driver"])
    limit = max(1, min(int(limit), MAX_LIMIT))
    _check_results(results)
    scores = _session_scores(info, results)
    if scores.empty:
        raise ToolInputError(_not_covered(info, root))
    flags = _load_mistakes(info, results)
    session_laps = int(laps["lap_number"].max())
    eligible = laps[_eligible(laps)]
    if code is not None:
        flags = flags[flags["driver"] == code]
        scores = scores[scores["driver"] == code]
        eligible = eligible[eligible["driver"] == code]
    coverage = Coverage(
        laps=tuple(sorted(int(n) for n in scores["lap_number"].unique())),
        scored=len(scores[["driver", "lap_number"]].drop_duplicates()),
        eligible=len(eligible[["driver", "lap_number"]].drop_duplicates()),
        session_laps=session_laps,
    )
    is_mistake = listed_flags(flags)
    distinct = is_mistake & (flags["merged_into"] == flags["segment_id"])
    ranked = flags[distinct].sort_values(["priority", "score_pct"], ascending=False)
    mistakes = [_mistake(r) for r in ranked.head(limit).to_dict("records")]
    left_out = {
        str(k): int(v) for k, v in _left_out_kinds(flags[~is_mistake]).value_counts().items()
    }
    scored = len(scores)
    n_listed = int(distinct.sum())
    note = ""
    if code is not None and not scored:
        note = _unscored_note(info, code, laps[laps["driver"] == code], coverage.eligible, root)
        summary = f"{info.label} ({info.location}), {code}: no scored corners.\n{note}"
    else:
        summary = mistakes_summary(
            info, code, mistakes, len(flags), n_listed, int(is_mistake.sum()), scored, coverage
        )
        summary += _left_out_text(left_out)
    data = {
        "event": info.event,
        "location": info.location,
        "year": info.year,
        "round": info.round,
        "session": info.name,
        "session_code": info.code,
        "driver": code,
        "ranking": RANKING_TEXT,
        "scored": scored,
        "flagged": len(flags),
        "distinct_mistakes": n_listed,
        "mistakes": [m.as_dict() for m in mistakes],
        "left_out": left_out,
        "coverage": coverage.as_dict(),
        "note": note,
        "track": session_track(info.key, root),
    }
    return MistakeList(
        session=info,
        driver=code,
        mistakes=mistakes,
        flagged=len(flags),
        listed=n_listed,
        scored=scored,
        left_out=left_out,
        coverage=coverage,
        note=note,
        summary=summary,
        data=data,
    )


def _left_out_kinds(flags: pd.DataFrame) -> pd.Series:
    """Why each flag find_mistakes leaves out isn't listed: its type, or "traffic_moment" for a
    driving flag that traffic at a neighbouring turn covers (explain.traffic_cover)."""
    if "covered_by" not in flags:
        return flags["mistake_type"]
    return flags["mistake_type"].where(flags["covered_by"].fillna("") == "", "traffic_moment")


def _unscored_note(
    info: SessionInfo, driver: str, laps: pd.DataFrame, eligible: int, root: Path
) -> str:
    """Why a driver of a scored session has no scored corner: none of their laps is one the
    detectors score, or those laps have no usable telemetry. `laps`: the driver's laps."""
    stale = int((_scoreable(info, root)["driver"] == driver).sum())
    if stale:  # the processed data has corners to score that the scores don't have
        raise ToolInputError(
            f"The M4 scores have none of {driver}'s {stale:,} corners on laps the detectors "
            f"score in the {info.label}: it was processed again after the last scoring run. "
            f"To score them, {RERUN}."
        )
    if eligible:
        why = (
            f"{driver} has {_plural(eligible, 'lap')} of those kinds, but no usable telemetry at "
            "any turn of them (missing or rejected data)"
        )
    else:
        why = f"none of {driver}'s laps is one of those"
    return (
        f"The detectors can't say whether {driver} made a mistake here: they score only "
        f"{SCORED_LAPS}, and {why}. {driver}'s laps in the data: {_lap_counts(laps)}. "
        "explain_corner still compares any lap and turn with telemetry against the usual."
    )


def _lap_counts(laps: pd.DataFrame) -> str:
    """Laps by class, in lap order: "1 out lap and 2 laps without a lap time (laps 1-3)"."""
    ordered = laps.sort_values("lap_number")
    classes = ordered["lap_class"].astype(str)
    counts = classes.value_counts(sort=False)
    parts = []
    for kind in dict.fromkeys(classes):
        n = int(counts[kind])
        one, many = LAP_COUNT_TEXT.get(kind, (f"lap classed {kind}", f"laps classed {kind}"))
        parts.append(f"{n} {one if n == 1 else many}")
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    numbers = sorted(int(n) for n in ordered["lap_number"].unique())
    return f"{text} (laps {_lap_ranges(numbers)})" if len(numbers) > 1 else text


def mistakes_summary(
    info: SessionInfo,
    driver: str | None,
    mistakes: list[Mistake],
    flagged: int,
    n_mistakes: int,
    n_flags: int,
    scored: int,
    coverage: Coverage,
) -> str:
    """`n_mistakes` distinct mistakes from `n_flags` flagged corners that look like mistakes
    (more when neighbouring turns of one lap were both flagged)."""
    who = f", {driver}" if driver else ""
    merged = (
        f" ({n_flags} flagged corners; neighbouring turns flagged on the same lap count once)"
        if n_flags > n_mistakes
        else ""
    )
    look = "looks like a driving mistake" if n_mistakes == 1 else "look like driving mistakes"
    lines = [
        f"{info.label} ({info.location}){who}: {flagged} of {scored:,} scored corners flagged "
        f"as unusual (the top 1% of the session); {n_mistakes} {look}{merged}.",
        coverage.text(driver),
    ]
    if not mistakes:
        lines.append("No flagged corner looks like a driving mistake.")
    else:
        shown = f"top {len(mistakes)}" if len(mistakes) < n_mistakes else "all"
        lines.append(f"Biggest mistakes ({shown}, {RANKING_TEXT}):")
        for i, m in enumerate(mistakes, start=1):
            who_ = "" if driver else f"{m.driver} "
            also = f", also {TYPE_TEXT[m.secondary_type]}" if m.secondary_type else ""
            nearby = ""
            if m.also_turns:
                turns = " and ".join(f"turn {t}" for t in m.also_turns)
                nearby = f" The same moment also shows at {turns}, whose windows overlap."
            lines.append(
                f"{i}. {who_}[{TYPE_TEXT.get(m.mistake_type, m.mistake_type)}{also}] "
                f"{m.explanation}{nearby}"
            )
    lines.append(
        "Time lost is against the driver's own usual laps at that turn (the field's when they "
        "had fewer than 5 other clean laps there), over 250 m before to 145 m after the apex. "
        "Call explain_corner with the lap and turn for the details and traces."
    )
    return "\n".join(lines)


# How the flags find_mistakes leaves out are counted in its summary.
LEFT_OUT_TEXT = {
    "no_time_lost": ("not clearly slower than usual", "not clearly slower than usual"),
    "no_reference": ("too few clean laps to compare", "too few clean laps to compare"),
    "slowdown": ("part of a slow-down (abandoned lap or something on track)", "slow-downs"),
    "data_problem": ("data problem", "data problems"),
    "lift_and_coast": ("lift and coast (energy or fuel management)", "lifts and coasts"),
    "slow_approach": ("slow approach (carried in)", "slow approaches (carried in)"),
    "lapped": ("being lapped (letting a faster car by)", "being lapped (letting faster cars by)"),
    "battle": ("fight with another car", "fights with other cars"),
    "impeded": ("held up by a slower car ahead", "held up by slower cars ahead"),
    "not_push_lap": (
        "not a full push lap (already slow before the corner)",
        "not full push laps (already slow before the corner)",
    ),
    "traffic_moment": (
        "part of traffic at a neighbouring turn",
        "part of traffic at a neighbouring turn",
    ),
}


def _left_out_text(left_out: dict[str, int]) -> str:
    if not left_out:
        return ""
    parts = []
    for kind, n in sorted(left_out.items(), key=lambda kv: -kv[1]):
        one, many = LEFT_OUT_TEXT.get(kind, (kind, kind))
        parts.append(f"{n} {one if n == 1 else many}")
    return "\nFlagged but not listed as mistakes: " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------------------------
# explain_corner


@dataclass(frozen=True)
class CornerReport:
    session: SessionInfo
    driver: str
    team: str
    lap_number: int
    turn: str
    explanation: Explanation
    flagged: bool | None  # None when the corner has no detector score (see `unscored`)
    score_pct: float | None
    unscored: str  # why it has no score, in plain English ("" when it has one)
    summary: str
    chart: dict[str, Any]


TURN_RE = re.compile(r"^\s*(?:t|turn)?\s*(\d+)\s*([a-z]?)\s*$", re.IGNORECASE)


def parse_turn(corner: int | str) -> tuple[int, str | None]:
    """(number, letter or None) from 7, "7", "T7", "turn 9a"."""
    if isinstance(corner, int):
        return corner, None
    m = TURN_RE.match(str(corner))
    if not m:
        raise ToolInputError(
            f"Can't read turn {corner!r}. Give a turn number like 4, 'T4' or '9a'."
        )
    return int(m.group(1)), (m.group(2).lower() or None)


def _pick_turn(frame: pd.DataFrame, corner: int | str, info: SessionInfo) -> tuple[int, str]:
    """The (number, letter) of the session's turn meant by `corner`; a bare number means the
    turn without a letter when there is one ("1" is T1, not T1A)."""
    number, letter = parse_turn(corner)
    turns = frame[["corner", "corner_letter", "apex_m"]].drop_duplicates(
        ["corner", "corner_letter"]
    )
    turns = turns.sort_values("apex_m")
    labels = [
        turn_label(c, lt) for c, lt in zip(turns["corner"], turns["corner_letter"], strict=True)
    ]
    here = turns[turns["corner"] == number]
    if letter is not None:
        here = here[here["corner_letter"].str.lower() == letter]
    elif (here["corner_letter"] == "").any():
        here = here[here["corner_letter"] == ""]
    if here.empty:
        raise ToolInputError(f"No turn {corner} in the {info.label}. Turns: {', '.join(labels)}.")
    if len(here) > 1:
        options = ", ".join(
            turn_label(c, lt) for c, lt in zip(here["corner"], here["corner_letter"], strict=True)
        )
        raise ToolInputError(f"Turn {number} has several parts in the {info.label}: {options}.")
    return int(here["corner"].iloc[0]), str(here["corner_letter"].iloc[0])


def _value(feature: str, value: float) -> str:
    unit = FEATURE_LABELS[feature][1]
    if not np.isfinite(value):
        return "none"
    digits = {"m/s²": 1, "s": 3}.get(unit, 1 if feature == "offset_exit_max" else 0)
    return f"{value:.{digits}f} {unit}"


def deviation_rows(exp: Explanation) -> list[dict[str, Any]]:
    """Every compared feature: this lap, usual, difference, z and whether it counts."""
    cmp = exp.comparison
    rows = []
    for f in DEVIATIONS:
        label, unit = FEATURE_LABELS[f]
        adverse = cmp.adverse(f)
        rows.append(
            {
                "feature": f,
                "label": label,
                "unit": unit,
                "value": _optional(cmp.values[f]),
                "usual": _optional(cmp.usual[f]),
                "delta": _optional(cmp.delta[f]),
                "z": _optional(cmp.z[f]),
                "adverse_z": _optional(adverse),
                "threshold": THRESHOLDS[f],
                "evidence": bool(np.isfinite(adverse) and adverse >= THRESHOLDS[f]),
                "higher_is_worse": ADVERSE[f] > 0,
            }
        )
    return rows


def _optional(value: float) -> float | None:
    return None if not np.isfinite(value) else round(float(value), 3)


def _shown_s(value: float) -> float | None:
    """A time lost as the summaries and the charts show it, to 0.01 s. Rounded once, here, so
    the pages' toFixed(2) and the text's two decimals agree: rounding 0.3051 to 0.305 first
    would draw 0.30 s next to an explanation saying 0.31 s."""
    return None if not np.isfinite(value) else round(float(value), 2)


def explain_corner(
    event: str,
    driver: str,
    lap: int,
    corner: int | str,
    year: int | None = None,
    session: str | None = None,
    root: Path = PROCESSED_DIR,
    results: Path = RESULTS_DIR,
) -> CornerReport:
    """One corner on one lap against the driver's usual way through it: time lost by phase,
    the feature deviations, the mistake type and explanation, and the traces for a chart.

    Raises ToolInputError with a helpful message when the session, driver, lap or turn can't
    be found. The comparison needs only the processed session, so it answers even when the
    batch results are missing or stale; then it says so (and how to rebuild them) instead of
    whether the detectors flagged the corner.
    """
    info = resolve_session(event, year, session, root)
    who = _driver(driver, _roster(_laps(info, root)), info)
    code = str(who["driver"])
    try:
        seg = load_session_segments(info.key, root)
    except FileNotFoundError:
        raise ToolInputError(f"No corner segments stored for the {info.label}.") from None
    frame = seg.frame
    number, letter = _pick_turn(frame, corner, info)
    turn = turn_label(number, letter)
    at_turn = (
        (frame["driver"] == code) & (frame["corner"] == number) & (frame["corner_letter"] == letter)
    )
    rows = np.flatnonzero(at_turn & (frame["lap_number"] == int(lap)))
    if not len(rows):
        laps = sorted(frame.loc[at_turn, "lap_number"].astype(int).unique())
        listed = ", ".join(str(n) for n in laps) if laps else "none"
        raise ToolInputError(
            f"{code} has no usable telemetry for lap {lap} at turn {turn} in the {info.label} "
            f"(missing or rejected data, or the lap wasn't driven). Laps with data at turn "
            f"{turn}: {listed}."
        )
    row = int(rows[0])
    # As the batch explains the flags (inference.explain.explain_flags), so both agree. The same
    # tracks (cached) place the cars on the chart's map and in its replay.
    loaded = load_session_tracks(info.key, root)
    tracks = None if loaded is None else loaded.tracks
    exp = explain(seg, row, load_session_events(info.key, root), tracks=tracks)
    target = frame.iloc[row]
    flagged, score_pct, unscored = _verdict(info, frame.iloc[[row]], root, results)
    merged = _merged_with(info, results, str(target["segment_id"])) if flagged else ""
    team = str(who["team"])
    summary = corner_summary(info, exp, target, team, flagged, score_pct, unscored, merged)
    chart = corner_chart(info, exp, seg, target, team, flagged, score_pct, unscored)
    chart["map"], chart["replay"] = map_and_replay(chart, info.key, root, loaded)
    return CornerReport(
        session=info,
        driver=code,
        team=team,
        lap_number=int(lap),
        turn=turn,
        explanation=exp,
        flagged=flagged,
        score_pct=score_pct,
        unscored=unscored,
        summary=summary,
        chart=chart,
    )


def _verdict(
    info: SessionInfo, segment: pd.DataFrame, root: Path, results: Path
) -> tuple[bool | None, float | None, str]:
    """(flagged, score percentile, why there is no score) for one corner segment (a one-row
    frame): a lap the detectors don't score, batch results missing or stale, a session the
    scores don't cover, or scores older than the processed data."""
    target = segment.iloc[0]
    if not _eligible(segment)[0]:
        why = f"didn't score this corner ({_lap_text(target)}): they score only {SCORED_LAPS}"
        return None, None, f"The detectors {why}."
    try:
        _check_results(results)
    except ToolInputError as exc:
        return None, None, f"No detector verdict for this corner. {exc}"
    scores = _session_scores(info, results)
    hit = scores[scores["segment_id"] == target["segment_id"]]
    if len(hit):
        return bool(hit["flagged"].iloc[0]), float(hit["score_pct"].iloc[0]), ""
    if scores.empty:
        return None, None, f"No detector verdict for this corner. {_not_covered(info, root)}"
    why = (
        "have no score for this corner, although it is on a lap they score: the session was "
        f"processed again after the last scoring run. To score it, {RERUN}"
    )
    return None, None, f"The detectors {why}."


def _merged_with(info: SessionInfo, results: Path, segment_id: str) -> str:
    """What find_mistakes does with this flag when a neighbouring turn of the same lap decides
    it, in a sentence ("" when it is listed on its own or not listed for its own type): listed
    under that turn, whose window overlaps, or left out as the same moment as traffic there
    (inference.explain.traffic_cover). Call _check_results first."""
    flags = _load_mistakes(info, results)
    by_id = {str(r["segment_id"]): r for r in flags.to_dict("records")}
    me = by_id.get(segment_id, {})

    def turn(other: str) -> str:
        return turn_label(int(by_id[other]["corner"]), str(by_id[other]["corner_letter"] or ""))

    cover = str(me.get("covered_by", "") or "")
    if cover in by_id:
        kind = TYPE_TEXT.get(str(by_id[cover]["mistake_type"]), "traffic")
        if me.get("mistake_type") in ("track_limits", "incident"):
            explained = dict(
                zip(flags["segment_id"].astype(str), traffic_explained(flags), strict=True)
            )
            ranked = (
                "ranked on that alone"
                if explained.get(segment_id, True)
                else "ranked on its full loss too, as those cars hadn't reached the driver when "
                "the loss began"
            )
            return (
                f"Turn {turn(cover)} on this lap was traffic ({kind}), and its window covers this "
                f"corner's: find_mistakes still lists it for race control, {ranked}."
            )
        return (
            f"Turn {turn(cover)} on this lap was traffic ({kind}), and its window covers this "
            "corner's loss: find_mistakes leaves it out as the same moment."
        )
    head = str(me.get("merged_into", ""))
    if head in ("", segment_id) or head not in by_id:
        return ""
    return (
        f"Turn {turn(head)} was flagged on this lap too and its window overlaps this one: "
        f"find_mistakes lists them as one mistake, under turn {turn(head)}."
    )


def _lap_text(target: pd.Series) -> str:
    return LAP_TEXT.get(str(target["lap_class"]), f"classed {target['lap_class']}")


def _lap_note(target: pd.Series) -> str:
    notes = [_lap_text(target)]
    if target["deleted"]:
        notes.append("lap time deleted")
    return ", ".join(notes)


def _type_text(kind: str, flagged: bool | None) -> str:
    """TYPE_TEXT, except that a corner the detectors didn't flag isn't called unusual."""
    if kind == "no_time_lost" and not flagged:
        return LEFT_OUT_TEXT["no_time_lost"][0]
    return TYPE_TEXT.get(kind, kind)


def corner_summary(
    info: SessionInfo,
    exp: Explanation,
    target: pd.Series,
    team: str,
    flagged: bool | None,
    score_pct: float | None,
    unscored: str = "",
    merged: str = "",
) -> str:
    """explain_corner's answer. `merged`: what find_mistakes does with the flag because of a
    neighbouring turn (_merged_with)."""
    cmp = exp.comparison
    turn = turn_label(target["corner"], target["corner_letter"])
    lines = [
        f"{info.label}, {target['driver']} ({team}) lap {int(target['lap_number'])}, turn "
        f"{turn} ({_lap_note(target)}).",
        exp.sentence,
    ]
    also = f"; also {TYPE_TEXT[exp.secondary_type]}" if exp.secondary_type else ""
    lines.append(f"Type: {_type_text(exp.mistake_type, flagged)}{also}.")
    if cmp.reference != "none":
        lines.extend(_comparison_lines(exp, target, turn))
    around = cars_around_text(exp.traffic, info.code in ("R", "S"))
    if around:
        lines.append(around)
    if flagged is None or score_pct is None:
        lines.append(unscored or "The detectors didn't score this corner.")
    elif flagged:
        lines.append(
            f"Flagged by the detectors: among the top 1% most unusual corners of the session "
            f"(percentile {100 * score_pct:.1f})."
        )
        if merged:
            lines.append(merged)
    else:
        lines.append(
            f"Not flagged by the detectors (percentile {100 * score_pct:.0f} in the session)."
        )
    if cmp.reference != "none":
        lines.append(
            "The chart data has this lap's speed, throttle and brake against the usual traces, "
            "and the running time lost through the corner."
        )
    return "\n".join(lines)


def _comparison_lines(exp: Explanation, target: pd.Series, turn: str) -> list[str]:
    """What the corner was compared with, the time lost by phase and the biggest deviations."""
    cmp = exp.comparison
    lines = []
    n = len(cmp.reference_rows)
    if cmp.reference == "driver":
        lines.append(f"Compared with {target['driver']}'s {n} other clean laps at turn {turn}.")
    else:
        own = "no other clean lap"
        if cmp.own_laps:
            own = f"only {_plural(cmp.own_laps, 'other clean lap')}"
        lines.append(
            f"Compared with the field's {n} clean laps at turn {turn} ({target['driver']} had "
            f"{own} there), so car pace counts towards the time lost."
        )
    usual_time = float(target["segment_time_s"]) - cmp.time_lost_s
    phases = ", ".join(
        f"{PHASE_TEXT[p]} {loss:+.2f} s" for p, loss in zip(PHASES, cmp.phase_loss_s, strict=True)
    )
    bar = THRESHOLDS["segment_time_s"] * cmp.scale["segment_time_s"]
    lines.append(
        f"Time from 250 m before to 145 m after the apex: {target['segment_time_s']:.3f} s "
        f"against {usual_time:.3f} s usual ({cmp.time_lost_s:+.3f} s; given the spread of the "
        f"reference laps, a loss stands out from {bar:.3f} s). By phase (entry to "
        f"{-PHASE_EDGES_M[0]:.0f} m before the apex, exit from {PHASE_EDGES_M[1]:.0f} m after): "
        f"{phases}."
    )
    rows = [r for r in deviation_rows(exp) if r["adverse_z"] is not None]
    rows.sort(key=lambda r: -abs(r["z"]))
    if rows:
        lines.append(
            "Biggest differences from usual (z: robust z-score against the reference; * = beyond "
            "the calibrated threshold, counted as evidence):"
        )
        for r in rows[:TOP_DEVIATIONS]:
            star = " *" if r["evidence"] else ""
            f = r["feature"]
            lines.append(
                f"- {r['label']}: {_value(f, cmp.values[f])} vs {_value(f, cmp.usual[f])} usual "
                f"(z {r['z']:+.1f}){star}"
            )
    if cmp.apex_moved:
        lines.append(
            f"The slowest point was more than {SAME_APEX_M:.0f} m from its usual place (at the "
            "neighbouring turn), so the braking point, throttle pickup and throttle lift are "
            "measured from the usual slowest point, on this lap and the reference laps alike."
        )
    return lines


def corner_chart(
    info: SessionInfo,
    exp: Explanation,
    seg: Any,
    target: pd.Series,
    team: str,
    flagged: bool | None,
    score_pct: float | None,
    unscored: str = "",
) -> dict[str, Any]:
    """JSON-ready payload for the explain-corner chart (80 points, so nothing is dropped),
    without its map and replay (tools.mistake_charts.map_and_replay adds them). Besides this
    lap's and the usual traces, the reference laps' middle half (`usual_lo` and `usual_hi`:
    their 25th and 75th percentiles at each point) of speed and throttle, the lateral offset
    from the reference line (`offset`, metres, positive to the left; the usual is the median of
    the reference laps), the reference lap standing for the usual (`ghost`, ghost_lap) and the
    apex's distance along the lap (`apex_m`); the band and the ghost are None without a
    reference."""
    cmp = exp.comparison
    idx = sample_indices(len(DISTANCE))
    row = cmp.row
    ref = cmp.reference_rows

    def traces(source: dict[str, np.ndarray]) -> dict[str, Any]:
        return {
            "speed": _rounded(source["speed"][idx], 1),
            "throttle": _rounded(source["throttle"][idx], 0),
            # Braking can be shorter than the sampling stride: keep the strongest in each step.
            "brake": _rounded(np.fmax.reduceat(source["brake"], idx), 2),
            "gear": _rounded(source["gear"][idx], 0),
        }

    def quartile(q: float) -> dict[str, Any] | None:
        """The reference laps' q-th percentile of speed and throttle at each point."""
        if not len(ref):
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # a point no reference lap has
            return {
                c: _rounded(np.nanpercentile(seg.traces[c][ref], q, axis=0)[idx], digits)
                for c, digits in (("speed", 1), ("throttle", 0))
            }

    edges = [float(DISTANCE[0]), *PHASE_EDGES_M, float(DISTANCE[-1])]
    turn = turn_label(target["corner"], target["corner_letter"])
    ghost = ghost_lap(seg, cmp)
    return {
        "title": f"{target['driver']} lap {int(target['lap_number'])}, T{turn}",
        "event": info.event,
        "location": info.location,
        "year": info.year,
        "round": info.round,
        "session": info.name,
        "session_code": info.code,
        "date": info.date,
        "driver": str(target["driver"]),
        "team": team,
        "lap_number": int(target["lap_number"]),
        "turn": turn,
        "lap_class": str(target["lap_class"]),
        "deleted": bool(target["deleted"]),
        "type": exp.mistake_type,
        "type_text": _type_text(exp.mistake_type, flagged),
        "secondary_type": exp.secondary_type,
        "explanation": exp.sentence,
        "time_lost_s": _shown_s(cmp.time_lost_s),
        "segment_time_s": round(float(target["segment_time_s"]), 3),
        "phases": [
            {"name": p, "start_m": edges[i], "end_m": edges[i + 1], "loss_s": _shown_s(loss)}
            for i, (p, loss) in enumerate(zip(PHASES, cmp.phase_loss_s, strict=True))
        ],
        "reference": {"kind": cmp.reference, "laps": len(cmp.reference_rows)},
        "ghost": None if ghost is None else {"driver": ghost[0], "lap": ghost[1]},
        "apex_m": _optional(float(target["apex_m"])),
        "distance_m": _rounded(DISTANCE[idx], 1),
        "lap": traces({c: seg.traces[c][row] for c in ("speed", "throttle", "brake", "gear")}),
        "usual": traces(cmp.usual_trace),
        "usual_lo": quartile(25),
        "usual_hi": quartile(75),
        "offset": {
            "lap": _rounded(seg.traces["offset_m"][row][idx], 1),
            "usual": _rounded(cmp.usual_trace["offset_m"][idx], 1),
        },
        "delta_s": _rounded(cmp.delta_trace_s[idx], 3),
        "deviations": deviation_rows(exp),
        "flagged": flagged,
        "score_pct": None if score_pct is None else round(score_pct, 4),
        "unscored": unscored,
        "data_issue": exp.data_issue,
        "race_control": exp.race_control,
        "context": exp.context,
        "traffic": {
            "type": exp.traffic.kind,
            "cars": list(exp.traffic.cars),
            "window_m": [_optional(exp.traffic.start_m), _optional(exp.traffic.end_m)],
            "loss_onset_m": _optional(exp.traffic.onset_m),
            "alongside_before_loss": list(exp.traffic.alongside),
            "around": [_encounter(e) for e in exp.traffic.encounters],
        },
    }


def ghost_lap(seg: SessionSegments, cmp: CornerComparison) -> tuple[str, int] | None:
    """(driver, lap) of the reference lap whose time through the corner is closest to the
    reference's median: a real lap standing for "the usual" in the replay (the field's laps
    can be another driver's). None without reference laps."""
    ref = cmp.reference_rows
    times = seg.frame["segment_time_s"].to_numpy(float)[ref]
    if not np.isfinite(times).any():
        return None
    pick = seg.frame.iloc[int(ref[int(np.nanargmin(np.abs(times - np.nanmedian(times))))])]
    return str(pick["driver"]), int(pick["lap_number"])


def _encounter(e: Encounter) -> dict[str, Any]:
    """One car near the corner, for the chart payload (gaps: + behind, - ahead)."""
    return {
        "driver": e.driver,
        "laps_ahead": e.laps_ahead,
        "lap_class": e.lap_class,
        "gap_start_s": _optional(e.gap_start_s),
        "gap_start_m": _optional(e.gap_start_m),
        "gap_before_loss_s": _optional(e.gap_onset_s),
        "gap_before_loss_m": _optional(e.gap_onset_m),
        "gap_window_start_s": _optional(e.gap_window_s),
        "closest_in_corner_s": _optional(e.min_gap_s),
        "passed": e.passed,
        "pass_m": _optional(e.pass_m),
        "approximate": e.approximate,
    }
