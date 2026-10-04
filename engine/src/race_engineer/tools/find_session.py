"""Find which processed session the user means, and what can be analysed for it.

The other tools take an event, a year and a session (`sessions.resolve_session`). People name a
session more loosely ("the last race", "the race before last", "round 5 2024"), so the model
reads the question and fills in this tool's fields: `recent` counts back from the newest
processed session ("last race" -> recent=0, session="R"), `round` picks a weekend of a season,
and `event` takes only the name the user gave ("Monza", "Silverstone", "2025_16_Q"). It used to
take the question as one free-text query and pick names out of it with word rules, which read
ordinary words as places ("car" as Monte Carlo, the pronoun "us" as the United States) and gave
a wrong session without saying so.

`recent` counts back from the newest processed session, which every answer names with its
season, so the model can also count a relative year from it when it doesn't know today's date.
A miss is not an error result: a misspelt or ambiguous name gets candidates to call again with
or ask the user about, an event without a session in the season asked for gets its other
seasons' to tell the user about, and event text that isn't a name (a question, a driver) gets
none, only how to fill the fields. Fields that are missing or contradict each other (round with
recent, a session key with another season) are errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypedDict

import duckdb
from pydantic import Field

from race_engineer.config import PROCESSED_DIR, RESULTS_DIR
from race_engineer.inference import provenance
from race_engineer.tools.cache import memo_by_files
from race_engineer.tools.params import FIRST_SEASON, LAST_SEASON, NOT_BOOL, ToolParams
from race_engineer.tools.registry import DataPaths, ToolOutput, ToolSpec
from race_engineer.tools.sessions import (
    SESSION_KEY,
    YEAR,
    MissingSessionError,
    SessionInfo,
    ToolInputError,
    event_names,
    event_query,
    given_once,
    list_sessions,
    match_event,
    match_tier,
    matched_names,
    missing_session_error,
    no_round_error,
    no_season_error,
    normalize,
    practice_note,
    resolve_session,
    session_codes,
    session_like,
    several_events_error,
    split_event_text,
    suggest_sessions,
    unknown_words,
)

MAX_CANDIDATES = 5
MAX_UNKNOWN_SHOWN = 4  # words named in the "not an event name" answer
MAX_ROUND = 30
MAX_RECENT = 30
MISTAKES_FILE = "mistakes.parquet"  # tools.mistakes.MISTAKES_FILE
SCORES_FILE = "segment_scores.parquet"  # tools.mistakes.SCORES_FILE
STYLE_FILE = "style_corners.parquet"  # inference.style's corner table
KIND_NAMES = {"Q": "qualifying", "R": "race", "S": "sprint", "SQ": "sprint qualifying"}
KIND_NAMES["SS"] = KIND_NAMES["SQ"]
NOTHING_GIVEN = (
    "Give event, round or recent: e.g. recent=0 with session='R' for the last race, "
    "event='Monza' with year=2025 for Monza 2025, or round=5 with year=2024."
)
EVENT_IS_A_NAME = (
    "Put only a Grand Prix name, location, country or session key in event, spelt right if you "
    "recognise it, and the rest of the question in the other fields, then call find_session "
    "again: 'last race' is recent=0 with session='R', 'round 5 2024' is round=5 with year=2024, "
    "'Max at Monza' is event='Monza'."
)
# Countries that held more than one Grand Prix in a season, as event text names them, and the
# locations of those Grands Prix (the data has no country column): the answer for the country
# names the other weekends of the match's season there.
_USA = ("Austin", "Miami", "Miami Gardens", "Las Vegas")
COUNTRY_LOCATIONS = {
    "spain": ("Barcelona", "Madrid"),
    "italy": ("Monza", "Imola"),
    "usa": _USA,
    "us": _USA,
    "united states": _USA,
    "america": _USA,
}

FindEvent = Annotated[
    str | None,
    Field(
        max_length=100,
        description="Only the event the user named: a Grand Prix name, location or country, or "
        "a session key, e.g. 'Monza', 'Silverstone', 'Abu Dhabi', 'USA', '2025_16_Q'. Never the "
        "rest of the question (no drivers, 'last', 'race' or 'mistakes'). Spell a name you "
        "recognise correctly ('silverston' -> 'Silverstone') and give a circuit as its town "
        "or Grand Prix ('Circuit de Spa-Francorchamps' -> 'Spa'); a misspelt or partial name "
        "still gets candidates. Without year, the newest season with the event; without "
        "session, its qualifying.",
    ),
]
FindYear = Annotated[
    int | None,
    Field(
        ge=FIRST_SEASON,
        le=LAST_SEASON,
        description="Season, e.g. 2025. With event, that season's; with round, a round of that "
        "season (without year, of the newest processed season); with recent, counts back within "
        "that season ('last race of 2024' -> recent=0, year=2024, session='R'). Count a "
        "relative year from today's date when you know it ('last year's Monaco race', asked in "
        "2026 -> event='Monaco', year=2025, session='R'), else from the newest processed "
        "season, which every answer names.",
    ),
    NOT_BOOL,
]
FindSessionText = Annotated[
    str | None,
    Field(
        max_length=30,
        description="Session: 'Q' or 'qualifying', 'R' or 'race', 'S' or 'sprint', 'SQ' or "
        "'sprint qualifying'. Omitted, it is qualifying with event, the race with round, and "
        "every session kind with recent: give 'R' for 'last race', 'Q' for 'latest qualifying'.",
    ),
]
Round = Annotated[
    int | None,
    Field(
        ge=1,
        le=MAX_ROUND,
        description="Round of the season: 'round 5 2024' -> round=5, year=2024. Without year, "
        "of the newest processed season (with event, of the newest season with that event). "
        "The weekend's race unless session is given. Not with recent.",
    ),
    NOT_BOOL,
]
Recent = Annotated[
    int | None,
    Field(
        ge=0,
        le=MAX_RECENT,
        description="Sessions to count back from the most recent processed one, not from today: "
        "0 = the latest, 1 = the one before. 'last race' -> recent=0, session='R'; 'latest "
        "qualifying' -> recent=0, session='Q'; 'the race before last' -> recent=1, session='R'; "
        "'N races ago' -> recent=N-1 ('2 races ago' is the race before last). With session, "
        "counts only that kind (without it, every session); with event, only that event's "
        "sessions; with year, only that season's.",
    ),
    NOT_BOOL,
]


class FindSessionParams(ToolParams):
    event: FindEvent = None
    year: FindYear = None
    session: FindSessionText = None
    round: Round = None
    recent: Recent = None


class SessionRef(TypedDict):
    key: str  # e.g. "2025_16_Q"
    year: int
    round: int
    session_code: str  # Q, SQ, SS, S or R
    session_name: str  # e.g. "Qualifying"
    event: str
    location: str
    date: str  # YYYY-MM-DD
    scored: bool  # the M4 detectors' scores cover it (find_mistakes, explain_corner)
    style: bool  # its season is in the driving-style tables (compare_driving_styles)


class Coverage(TypedDict):
    first: str  # e.g. "2022 Bahrain Grand Prix (Q)"
    last: str  # e.g. "2026 Azerbaijan Grand Prix (R)"; recent counts back from it
    newest_season: int
    sessions: int


class FindSessionData(TypedDict):
    match: SessionRef | None
    candidates: list[SessionRef]  # best match first, when the name has no single match
    weekend: list[SessionRef]  # every processed session of the matched weekend
    coverage: Coverage


@dataclass(frozen=True)
class Found:
    """How the fields were read: the session, or the candidates and why there's no match."""

    match: SessionInfo | None
    candidates: list[SessionInfo]
    how: str  # "key", "recent", "round" or "name"
    problem: str = ""  # the lookup's message when there's no match
    # The event text isn't a name (a question, a driver, only a season or session): no
    # candidates, which would be guesses ("car" -> Carlo); the answer says what event takes.
    not_a_name: bool = False
    # A newer weekend held where the match was under another event name, when no season was
    # given ("Barcelona Spain" is the 2025 Spanish GP; the 2026 Barcelona GP was there too).
    newer_here: SessionInfo | None = None
    # The other weekends of the match's season in the country the event text names ("Spain"
    # with 2026 is the Spanish GP in Madrid; the Barcelona GP was in Spain too).
    same_country: tuple[SessionInfo, ...] = ()
    # The season asked for, when every candidate is of another one ("Monza" with 2021): the
    # answer says to tell the user or ask, not to call again with another season.
    other_season: int | None = None
    # The event named one weekend but it had no session of the kind asked for (a Monza
    # sprint): the candidates are only that event's weekends that had one, and the answer says
    # to tell the user or ask, never to answer for another kind of session.
    missing_kind: bool = False


@dataclass(frozen=True)
class SessionSearch:
    found: Found
    summary: str  # plain language, for the model
    data: FindSessionData


# ---------------------------------------------------------------------------------------------
# What the M4 results cover


@memo_by_files(
    lambda a: [a["results"] / MISTAKES_FILE, a["results"] / SCORES_FILE],
    results=lambda a: a["results"],
    maxsize=2,
)
def scored_sessions(results: Path = RESULTS_DIR) -> tuple[frozenset[tuple[int, int, str]], str]:
    """The (year, round, session) the detectors' scores cover, and "" when the results are
    current, else why find_mistakes can't use them (then nothing counts as scored)."""
    scores = results / SCORES_FILE
    try:
        provenance.check_current(results / MISTAKES_FILE, "explain", results)
        if not scores.exists() or provenance.parquet_run(scores) is not None:
            provenance.check_current(scores, "score", results)
    except provenance.StaleResultsError as exc:
        return frozenset(), str(exc)
    query = f"select distinct year, round, session from read_parquet('{scores.as_posix()}')"
    rows = _fetch(query)
    return frozenset((int(y), int(r), str(s)) for y, r, s in rows), ""


@memo_by_files(lambda a: a["results"] / STYLE_FILE, results=lambda a: a["results"], maxsize=2)
def style_seasons(results: Path = RESULTS_DIR) -> frozenset[int]:
    """The seasons in the driving-style tables (none when they are missing or out of date)."""
    path = results / STYLE_FILE
    try:
        provenance.check_current(path, "style", results)
    except provenance.StaleResultsError:
        return frozenset()
    query = f"select distinct year from read_parquet('{path.as_posix()}')"
    return frozenset(int(y) for (y,) in _fetch(query))


def _fetch(query: str) -> list[tuple]:
    """Rows of a DuckDB query, on a connection of its own (tools run in worker threads)."""
    con = duckdb.connect()
    try:
        return con.sql(query).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------
# Reading the fields


def check_fields(
    event: str | None,
    year: int | None,
    session: str | None,
    round_: int | None,
    recent: int | None,
) -> None:
    """Raise ToolInputError, in one line, for fields that are missing or contradict each other:
    none of event, round and recent; round with recent; a session key with round or recent;
    an unknown session; a season or session in the event text that differs from year or
    session ("2025_16_Q" with year=2024)."""
    if event is None and round_ is None and recent is None:
        raise ToolInputError(NOTHING_GIVEN)
    if round_ is not None and recent is not None:
        raise ToolInputError(
            "Give round or recent, not both: round picks a weekend of a season, recent counts "
            "back from the newest processed session."
        )
    if session is not None:
        session_codes(session)
    if event is None:
        return
    if SESSION_KEY.fullmatch(event) and (round_ is not None or recent is not None):
        raise ToolInputError(
            f"{event!r} is a session key, which names one session: give it without round or recent."
        )
    given_once(event, year, session)


def _of_weekend(weekend: list[SessionInfo], session: str | None) -> SessionInfo:
    """The weekend's session of the kind asked for or, with none asked for, its race (else its
    last session)."""
    if session is None:
        return ([s for s in weekend if s.code == "R"] or weekend)[-1]
    wanted = [s for s in weekend if s.code in session_codes(session)]
    if not wanted:
        raise missing_session_error(weekend, session)
    return wanted[-1]


def _by_round(
    round_: int,
    event: str | None,
    year: int | None,
    session: str | None,
    sessions: list[SessionInfo],
    root: Path,
) -> SessionInfo:
    """The weekend of round `round_`, in `year` or else the newest processed season (with an
    event, the newest season of that event), and in it the session asked for or the race."""
    if event is None:
        season = year if year is not None else sessions[-1].year
        weekend = [s for s in sessions if (s.year, s.round) == (season, round_)]
        if not weekend:
            raise no_round_error(season, round_, sessions)
        return _of_weekend(weekend, session)
    found = match_event(event, year, session, root)
    season = found.year if found.year is not None else found.sessions[-1].year
    in_season = [s for s in found.sessions if s.year == season]
    weekend = [s for s in in_season if s.round == round_]
    if not weekend:
        rounds = ", ".join(str(r) for r in sorted({s.round for s in in_season}))
        raise ToolInputError(
            f"No processed session of {event!r} in {season} round {round_}: "
            f"{'; '.join(event_names(in_season))} was round {rounds}."
        )
    return _of_weekend(weekend, found.session)


def _by_recent(
    recent: int,
    event: str | None,
    year: int | None,
    session: str | None,
    sessions: list[SessionInfo],
    root: Path,
) -> SessionInfo:
    """The session `recent` places back from the newest processed one (0 = the newest), among
    sessions of the kind asked for (else every session), of the event (else every event) and
    of the season (else every season)."""
    if event is None:
        pool = [s for s in sessions if year is None or s.year == year]
        if not pool:
            raise no_season_error(year, sessions)
    else:
        found = match_event(event, year, session, root)
        pool, year, session = found.sessions, found.year, found.session
        for season in sorted({s.year for s in pool}, reverse=True):
            weekends = {s.round: s for s in pool if s.year == season}
            if len(weekends) > 1:  # "austr": the Australian and the Austrian Grands Prix
                raise several_events_error(event, list(weekends.values()))
    codes = session_codes(session) if session is not None else None
    kind = [s for s in pool if codes is None or s.code in codes]
    what = f"{KIND_NAMES[codes[0]]} ({'/'.join(codes)}) " if codes else ""
    seasons = [s for s in sessions if year is None or s.year == year]
    # "of the Spanish Grand Prix (Madrid)" for "Madrid": the Spanish GP was in Barcelona before.
    where = f" of the {' / '.join(matched_names(pool, seasons))}" if event else ""
    scope = where + (f" in {year}" if year is not None else "")
    if not kind and event is not None and pool and session is not None:
        # The event is plain, the kind of session isn't there: say so (MissingSessionError),
        # never offer the event's qualifying instead.
        last = pool[-1]
        weekend = [s for s in pool if (s.year, s.round) == (last.year, last.round)]
        raise MissingSessionError(f"No processed {what}session{scope}.", weekend, session)
    if not kind:
        raise ToolInputError(f"No processed {what}session{scope}.")
    if len(kind) <= recent:
        raise ToolInputError(
            f"Only {len(kind)} processed {what}session(s){scope}: recent={recent} goes back "
            f"too far (0 is the latest, {len(kind) - 1} the earliest)."
        )
    return kind[-1 - recent]


def _event_words(event: str) -> tuple[list[str], int | None, str | None]:
    """The words of event text to look for in names (aliases applied), and the season and the
    session phrase the text names. Never raises."""
    try:
        words, text_year, named = split_event_text(event)
    except ToolInputError:  # more than one season in the text
        words, text_year, named = normalize(YEAR.sub(" ", event)), None, None
    return event_query(words), text_year, named


def _candidates(
    event: str, year: int | None, session: str | None, sessions: list[SessionInfo]
) -> list[SessionInfo]:
    """Sessions an event name might mean when it has no single match, best match first: for
    each event whose name matches, its newest weekend (in the season asked for, if any match
    then), newest first; or for the names closest to a misspelling ("monzza"), the closest
    first, each one's weekend in the season asked for (only those, if any ran then), else its
    newest; and in that weekend the session asked for, else qualifying, else the last. Never
    raises."""
    if SESSION_KEY.fullmatch(event):
        return []
    query, text_year, named = _event_words(event)
    year = year if year is not None else text_year
    try:
        codes = session_codes(session or named) if session or named else ()
    except ToolInputError:
        codes = ()

    def pick(weekend: list[SessionInfo]) -> SessionInfo:
        wanted = [s for s in weekend if s.code in codes]
        quali = [s for s in weekend if s.code == "Q"]  # resolve_session's default
        return (wanted or quali or weekend)[-1]

    def weekend_of(near: SessionInfo) -> list[SessionInfo]:
        """The near event's weekend in `year` if it ran then, else its newest (`near`'s)."""
        same = [s for s in sessions if near.event == s.event or near.location == s.location]
        in_year = [s for s in same if s.year == year]
        newest = in_year[-1] if in_year else near
        return [s for s in sessions if (s.year, s.round) == (newest.year, newest.round)]

    if not query:
        return []
    tiers = {id(s): match_tier(query, s) for s in sessions}
    found = [t for t in tiers.values() if t is not None]
    if not found:
        near = suggest_sessions(query, sessions)  # the newest session of each event, best first
        closest = [pick(weekend_of(n)) for n in near]
        return [s for s in closest if s.year == year] or closest
    matched = [s for s in sessions if tiers[id(s)] == min(found)]
    matched = [s for s in matched if s.year == year] or matched
    picks: dict[str, SessionInfo] = {}  # the newest weekend of each event
    for s in reversed(matched):
        if s.event not in picks:
            picks[s.event] = pick([w for w in matched if (w.year, w.round) == (s.year, s.round)])
    return list(picks.values())[:MAX_CANDIDATES]


def lookup(
    event: str | None = None,
    year: int | None = None,
    session: str | None = None,
    round_: int | None = None,
    recent: int | None = None,
    root: Path = PROCESSED_DIR,
) -> Found:
    """The session the fields mean: a round (`round_`), else a count back (`recent`), else the
    event by name or key (resolve_session: the newest season with it, qualifying unless
    `session` says otherwise). Raises ToolInputError for fields that are missing or contradict
    each other (check_fields); fields that fit no single session give the lookup's message and
    candidates instead, or no candidates when the event text isn't a name."""
    event = (event or "").strip() or None
    check_fields(event, year, session, round_, recent)
    sessions = list_sessions(root=root)
    how = "round" if round_ is not None else "recent" if recent is not None else "name"
    try:
        if round_ is not None:
            match = _by_round(round_, event, year, session, sessions, root)
        elif recent is not None:
            match = _by_recent(recent, event, year, session, sessions, root)
        else:
            assert event is not None  # check_fields: event, round or recent
            how = "key" if SESSION_KEY.fullmatch(event) else how
            match = resolve_session(event, year, session, root)
    except MissingSessionError as exc:
        return Found(
            None, _with_kind(exc.weekend, exc.session, sessions), how, str(exc), missing_kind=True
        )
    except ToolInputError as exc:
        if event is None:
            return Found(None, [], how, str(exc))
        if SESSION_KEY.fullmatch(event) is None:
            words = _event_words(event)[0]
            unknown = unknown_words(words, sessions)
            if unknown:  # a question or a driver
                return Found(None, [], how, _not_a_name(event, unknown), not_a_name=True)
            if not words:  # only a season or session ("race", "2021")
                return Found(None, [], how, str(exc), not_a_name=True)
        candidates = _candidates(event, year, session, sessions)
        asked = year if year is not None else _event_words(event)[1]
        other = asked if candidates and all(c.year != asked for c in candidates) else None
        return Found(None, candidates, how, str(exc), other_season=other)
    return Found(
        match,
        [],
        how,
        newer_here=_newer_here(match, event, year, how, sessions),
        same_country=_same_country(match, event, sessions),
    )


def _with_kind(
    weekend: list[SessionInfo], session: str, sessions: list[SessionInfo]
) -> list[SessionInfo]:
    """The newest weekends of the same event (or at the same location) that had a session of
    the kind asked for, one session each, newest first: "Miami" with 2022 and the sprint -> the
    Miami sprints of 2026, 2025 and 2024."""
    codes = session_codes(session)
    first = weekend[0]
    picks: dict[tuple[int, int], SessionInfo] = {}
    for s in reversed(sessions):
        same = s.event == first.event or s.location == first.location
        if same and s.code in codes and (s.year, s.round) != (first.year, first.round):
            picks.setdefault((s.year, s.round), s)
    return list(picks.values())[:MAX_CANDIDATES]


def _not_a_name(event: str, unknown: list[str]) -> str:
    """Why event text isn't a name: the words of it no name has or is close to ("'max' and 'at'
    are ..."), and which of them look like a misspelt session word ("sprnt")."""
    shown = [repr(w) for w in unknown[:MAX_UNKNOWN_SHOWN]]
    if len(unknown) > MAX_UNKNOWN_SHOWN:
        shown.append(f"{len(unknown) - MAX_UNKNOWN_SHOWN} more")
    listed = f"{', '.join(shown[:-1])} and {shown[-1]}" if len(shown) > 1 else shown[0]
    verb = "is" if len(unknown) == 1 else "are"
    sessions = [f"{w!r} looks like {like!r}" for w in unknown if (like := session_like(w))]
    session_hint = f" ({'; '.join(sessions)}: the session goes in session)" if sessions else ""
    return (
        f"{event!r} is not an event name: {listed} {verb} in no Grand Prix, location or country "
        f"name, nor close to one{session_hint}.{practice_note(' '.join(unknown))}"
    )


def _newer_here(
    match: SessionInfo, event: str | None, year: int | None, how: str, sessions: list[SessionInfo]
) -> SessionInfo | None:
    """The newest weekend held at the match's location under another event name, after the
    match, when the season came from no field or event text but from the newest season the
    name matched ("Barcelona Spain" -> the 2025 Spanish GP, while the 2026 Barcelona GP was
    also held in Barcelona). None otherwise."""
    if event is None or how == "key" or year is not None or given_once(event)[0] is not None:
        return None
    later = [
        s
        for s in sessions
        if s.location == match.location
        and s.event != match.event
        and (s.year, s.round) > (match.year, match.round)
    ]
    return later[-1] if later else None


def _same_country(
    match: SessionInfo, event: str | None, sessions: list[SessionInfo]
) -> tuple[SessionInfo, ...]:
    """The other weekends of the match's season in the country the event text names, when it is
    one with several Grands Prix (COUNTRY_LOCATIONS), a session of each: "Spain" with 2026 ->
    the Barcelona GP, besides the Spanish GP in Madrid. Empty otherwise."""
    if event is None or SESSION_KEY.fullmatch(event):
        return ()
    try:
        places = COUNTRY_LOCATIONS.get(split_event_text(event)[0], ())
    except ToolInputError:  # more than one season in the text
        return ()
    weekends = {
        s.round: s
        for s in sessions
        if s.year == match.year and s.round != match.round and s.location in places
    }
    return tuple(weekends.values())


# ---------------------------------------------------------------------------------------------
# The tool


def _ref(
    s: SessionInfo, scored: frozenset[tuple[int, int, str]], style: frozenset[int]
) -> SessionRef:
    return SessionRef(
        key=s.key,
        year=s.year,
        round=s.round,
        session_code=s.code,
        session_name=s.name,
        event=s.event,
        location=s.location,
        date=s.date,
        scored=(s.year, s.round, s.code) in scored,
        style=s.year in style,
    )


def find_session(
    event: str | None = None,
    year: int | None = None,
    session: str | None = None,
    round_: int | None = None,
    recent: int | None = None,
    root: Path = PROCESSED_DIR,
    results: Path = RESULTS_DIR,
) -> SessionSearch:
    """Find the session the fields mean (see lookup), with its weekend, what the M4 results
    cover, and the dataset's first and last session and newest season."""
    found = lookup(event, year, session, round_, recent, root)
    sessions = list_sessions(root=root)
    scored, problem = scored_sessions(results)
    style = style_seasons(results)

    def ref(s: SessionInfo) -> SessionRef:
        return _ref(s, scored, style)

    first, last = sessions[0], sessions[-1]
    coverage = Coverage(
        first=f"{first.year} {first.event} ({first.code})",
        last=f"{last.year} {last.event} ({last.code})",
        newest_season=last.year,
        sessions=len(sessions),
    )
    match = found.match
    weekend = [s for s in sessions if match and (s.year, s.round) == (match.year, match.round)]
    data = FindSessionData(
        match=ref(match) if match else None,
        candidates=[ref(s) for s in found.candidates],
        weekend=[ref(s) for s in weekend],
        coverage=coverage,
    )
    summary = _summary(found, weekend, data, last, problem)
    return SessionSearch(found, summary, data)


def _summary(
    found: Found,
    weekend: list[SessionInfo],
    data: FindSessionData,
    last: SessionInfo,
    problem: str,
) -> str:
    newest = f"{last.year} {last.event}, {last.name} ({last.date})"
    match = found.match
    if match is None:
        lines = [f"No single session matches: {found.problem}"]
        if found.not_a_name:
            lines.append(EVENT_IS_A_NAME)
        ask = ""
        options = "; ".join(
            f"{s.year} {s.event}, {s.name} ({s.code}, {s.date})" for s in found.candidates
        )
        if found.missing_kind:
            if found.candidates:
                lines.append(f"Weekends of that event that had one: {options}.")
            these = " (or which of these weekends)" if found.candidates else ""
            ask = (
                f"Tell the user there was no such session, or ask which session{these} they "
                "mean; don't answer for another kind of session or another weekend unless they "
                "agree. "
            )
        elif found.candidates and found.other_season is not None:
            lines.append(f"In other seasons: {options}.")
            ask = (
                f"None is in {found.other_season}, the season asked for: tell the user the data "
                "has no such session then, or ask whether another season will do; don't answer "
                "for another season unless they agree. "
            )
        elif found.candidates:
            lines.append(f"Candidates, best match first: {options}.")
            ask = (
                "If one is plainly the event the user named, call again with its event and year; "
                "otherwise ask the user which they mean. "
            )
        lines.append(
            f"{ask}The data covers {data['coverage']['first']} to the {newest}, so "
            f"{last.year} is the newest processed season; list_sessions lists every session."
        )
        return "\n".join(lines)
    ref = data["match"]
    assert ref is not None
    others = ", ".join(f"{s.name} ({s.code})" for s in weekend)
    if problem:
        why = problem.rstrip(".")
        scores = f"Mistake scores: unavailable, the M4 results need rebuilding ({why})"
    else:
        scores = f"Mistake scores: {'yes' if ref['scored'] else 'no (not in the last scoring run)'}"
    style = "yes" if ref["style"] else "no"
    lines = [
        f"{match.year} {match.event}, {match.name} ({match.code}), {match.date}, "
        f"{match.location}: round {match.round}. That weekend: {others}.",
        f"{scores}. Driving-style comparisons for {match.year}: {style}.",
        f"Use event={match.event!r}, year={match.year}, session={match.code!r} in other tools.",
    ]
    later = found.newer_here
    if later is not None:
        lines.append(
            f"The {later.year} {later.event} was also held at {match.location}: "
            f"event={later.event!r}, year={later.year}."
        )
    lines += [
        f"The {s.year} {s.event} ({s.location}) was also held in that country: "
        f"event={s.event!r}, year={s.year}."
        for s in found.same_country
    ]
    lines.append(
        f"The data ends with the {newest}, so {last.year} is the newest processed season; "
        "recent counts back from that session."
    )
    return "\n".join(lines)


def _run(p: FindSessionParams, paths: DataPaths) -> ToolOutput:
    found = find_session(
        p.event, p.year, p.session, p.round, p.recent, paths.processed, paths.results
    )
    return ToolOutput(found.summary, dict(found.data))


FIND_SESSION = ToolSpec(
    name="find_session",
    title="Find a session",
    description=(
        "Find which processed F1 session the user means and what can be analysed for it. "
        "Read the question and fill in the fields: give at least one of event, round or "
        "recent, and never put the rest of the question in event. Examples: 'last race' -> "
        "recent=0, session='R'; 'latest qualifying' -> recent=0, session='Q'; 'the race before "
        "last' -> recent=1, session='R'; 'Monza 2025 quali' -> event='Monza', year=2025, "
        "session='Q'; 'the Baku sprint' -> event='Baku', session='S'; 'round 5 2024' -> "
        "round=5, year=2024; 'last year's Monaco race' -> event='Monaco', year=<last year>, "
        "session='R'. recent counts every session kind unless session is given, so 'last race' "
        "needs session='R'. recent counts back from the newest processed session, which every "
        "answer names with its season; count a relative year from today's date when you know "
        "it, else from that season. Returns the session (season, round, Grand Prix, session "
        "code, date), the other sessions of that weekend and whether mistake scores exist for "
        "it, or up to five candidates when a name is misspelt or fits several events. Call it "
        "first when the session is unclear, then pass its event, year and session to the other "
        "tools."
    ),
    params=FindSessionParams,
    run=_run,
    warm=lambda paths: (scored_sessions(paths.results), style_seasons(paths.results)),
)
