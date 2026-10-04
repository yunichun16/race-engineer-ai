"""GET /api/catalog/sessions, /api/catalog/drivers and /api/catalog/styles: what the data holds,
for the website's pickers.

These are read-only routes for the site, not tools. They aren't ToolSpecs, so the MCP server, the
chat's tool list and its prompt version (and with it the signatures of the histories browsers
hold) stay as they are. They list what exists, so a picker never offers a session, a driver or a
pair the tools would refuse:

- sessions: every processed session, grouped by season and race weekend (both newest first; a
  weekend's sessions in the order they ran), with whether the M4 scores cover it
  (find_mistakes) and whether its season is in the driving-style tables (compare_driving_styles).
- drivers: the drivers of one session, named as the tools name it (event, year, session), with
  their team and how many laps they have in the data.
- styles: one season's drivers and teammate pairs in the driving-style tables (default: the
  newest season), each pair once, with the events they shared a car.

Errors are the tools' (api/errors.py): an unknown event or season is 422 `invalid_input`, an
unknown query key 422 `invalid_request`, and data the server must rebuild (no processed
sessions, style tables missing or from another scoring run) 503 `results_unavailable`, since no
change to the request would help. Each answer carries an ETag made from the package version,
the route, its arguments and the data's version, known before any work is done: a request with
a matching If-None-Match gets 304.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from itertools import groupby
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import anyio.to_thread
import pandas as pd
from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from race_engineer import __version__
from race_engineer.api.models import ErrorBody
from race_engineer.api.routes.tools import matches
from race_engineer.data import store
from race_engineer.tools import styles
from race_engineer.tools.cache import memo_by_files
from race_engineer.tools.find_session import scored_sessions, style_seasons
from race_engineer.tools.params import (
    FIRST_SEASON,
    LAST_SEASON,
    NOT_BOOL,
    Event,
    SessionText,
    ToolParams,
    Year,
)
from race_engineer.tools.registry import DataPaths, data_version
from race_engineer.tools.sessions import (
    ResultsUnavailableError,
    SessionInfo,
    ToolInputError,
    list_sessions,
    resolve_session,
)

CATALOG_PATH = "/api/catalog/"
# Longer than the tools' 60 s: the catalog changes only when the data is rebuilt, and a browser
# that revalidates with If-None-Match gets a 304 then.
CACHE_CONTROL = "private, max-age=300"
STYLE_MISSING = "Driving-style data hasn't been built yet: run `uv run race-engineer-infer style`."

SessionCode = Literal["Q", "SQ", "SS", "S", "R"]

_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorBody, "description": text}
    for status, text in (
        (422, "Invalid arguments, or an event or season the data doesn't have (the message says)"),
        (503, "The data needs rebuilding (the message says how)"),
    )
}


# --- The query parameters ---------------------------------------------------------------------


class SessionsQuery(ToolParams):
    """No parameters: any query key is refused, as the tools refuse one they don't declare."""


class DriversQuery(ToolParams):
    """The session, given as every tool takes it (`sessions.resolve_session`)."""

    event: Event
    year: Year = None
    session: SessionText = None


class StylesQuery(ToolParams):
    year: Annotated[
        int | None,
        Field(
            ge=FIRST_SEASON,
            le=LAST_SEASON,
            description="Season, e.g. 2025. Omit for the newest season in the driving-style data.",
        ),
        NOT_BOOL,
    ] = None


# --- The answers ------------------------------------------------------------------------------


class CatalogSession(BaseModel):
    key: str = Field(examples=["2025_16_Q"])
    code: SessionCode
    name: str = Field(examples=["Qualifying"])  # FastF1's session name
    date: str = Field(examples=["2025-09-06"])  # UTC date, YYYY-MM-DD
    scored: bool  # the M4 detectors' scores cover it (find_mistakes, explain_corner's verdict)


class CatalogWeekend(BaseModel):
    round: int
    event: str = Field(examples=["Italian Grand Prix"])
    location: str = Field(examples=["Monza"])
    date: str  # the date of its last session
    sessions: list[CatalogSession]  # in the order they ran


class CatalogSeason(BaseModel):
    year: int
    style: bool  # the season is in the driving-style tables (compare_driving_styles)
    weekends: list[CatalogWeekend]  # newest first


class Coverage(BaseModel):
    first: str = Field(examples=["2022 Bahrain Grand Prix (Q)"])
    last: str = Field(examples=["2026 Azerbaijan Grand Prix (R)"])
    sessions: int


class SessionCatalog(BaseModel):
    """Every processed session, by season and race weekend, newest first."""

    coverage: Coverage
    # False when the M4 results are missing or from another scoring run: then no session is
    # `scored` and find_mistakes answers 503.
    results_current: bool
    seasons: list[CatalogSeason]  # newest first


class DriversSession(BaseModel):
    key: str
    year: int
    round: int
    event: str
    location: str
    session_code: SessionCode
    session_name: str
    date: str


class SessionDriver(BaseModel):
    driver: str = Field(examples=["LEC"])
    team: str = Field(examples=["Ferrari"])  # the team of most of their laps
    laps: int  # laps in the data, out and in laps included


class SessionDrivers(BaseModel):
    """The session the arguments name, and its drivers sorted by team, then code."""

    session: DriversSession
    drivers: list[SessionDriver]


class StyleDriver(BaseModel):
    driver: str
    team: str
    events: int  # events in the style data with this team


class StylePair(BaseModel):
    team: str
    a: str  # a < b
    b: str
    events: int  # events they shared the car
    # Whether each driver's style difference from the other held between the season's odd and
    # even rounds (style_embedding_pairs' `consistent`, p < 0.05), for a then b. The test is run
    # once per pair, so the two are the same; false too when the pair has fewer than two events
    # in either half, which can't be tested.
    consistent: tuple[bool, bool]


class StyleCatalog(BaseModel):
    """One season of the driving-style tables: its drivers (a mid-season move gives a driver a
    row per team) and its teammate pairs, each once."""

    years: list[int]  # newest first
    year: int
    drivers: list[StyleDriver]  # sorted by team, then code
    pairs: list[StylePair]  # sorted by team, then a, then b


# --- Building them ----------------------------------------------------------------------------


def _coverage_label(s: SessionInfo) -> str:
    """As find_session's coverage names a session: "2022 Bahrain Grand Prix (Q)"."""
    return f"{s.year} {s.event} ({s.code})"


def session_catalog(paths: DataPaths) -> SessionCatalog:
    """Every processed session under `paths`, with what the M4 results cover. 503 (not 422)
    without processed sessions: the request has nothing to change."""
    try:
        sessions = list_sessions(root=paths.processed)  # calendar order
    except ToolInputError as exc:
        raise ResultsUnavailableError(str(exc)) from None
    scored, problem = scored_sessions(paths.results)
    style = style_seasons(paths.results)

    def weekend(round_: int, held: list[SessionInfo]) -> CatalogWeekend:
        last = held[-1]
        listed = [
            CatalogSession(
                key=s.key,
                code=cast(SessionCode, s.code),
                name=s.name,
                date=s.date,
                scored=(s.year, s.round, s.code) in scored,
            )
            for s in held
        ]
        return CatalogWeekend(
            round=round_, event=last.event, location=last.location, date=last.date, sessions=listed
        )

    seasons = []
    for year, in_year in groupby(reversed(sessions), key=lambda s: s.year):
        weekends = [
            weekend(round_, list(reversed(list(held))))
            for round_, held in groupby(in_year, key=lambda s: s.round)
        ]
        seasons.append(CatalogSeason(year=year, style=year in style, weekends=weekends))
    coverage = Coverage(
        first=_coverage_label(sessions[0]),
        last=_coverage_label(sessions[-1]),
        sessions=len(sessions),
    )
    return SessionCatalog(coverage=coverage, results_current=not problem, seasons=seasons)


@memo_by_files(lambda a: a["path"], maxsize=32)
def _drivers(path: Path) -> tuple[tuple[str, str, int], ...]:
    """(driver, team, laps) from one session's laps file, sorted by team, then driver: the team
    of most of the driver's laps (alphabetical on a tie), the distinct lap numbers. Kept until
    the file is rebuilt."""
    laps = pd.read_parquet(path, columns=["driver", "team", "lap_number"])
    laps = laps[laps["driver"].notna() & (laps["driver"] != "")]
    counted = laps.dropna(subset=["lap_number"]).groupby("driver")["lap_number"].nunique()
    teams = (
        laps.dropna(subset=["team"])
        .groupby(["driver", "team"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values(["driver", "n", "team"], ascending=[True, False, True])
        .drop_duplicates("driver")
        .set_index("driver")["team"]
    )
    rows = [
        (str(driver), str(teams.get(driver, "")), int(counted.get(driver, 0)))
        for driver in laps["driver"].unique()
    ]
    return tuple(sorted(rows, key=lambda r: (r[1], r[0])))


def session_drivers(params: DriversQuery, paths: DataPaths) -> SessionDrivers:
    """The drivers of the session `params` name. 422 for an event, season or session the data
    doesn't have (the tools' messages, with suggestions)."""
    s = resolve_session(params.event, params.year, params.session, paths.processed)
    path = store.table_path("laps", s.key, paths.processed)
    if not path.exists():
        raise ResultsUnavailableError(
            f"The {s.label} has no laps table in the processed data: rebuild it (make data)."
        )
    session = DriversSession(
        key=s.key,
        year=s.year,
        round=s.round,
        event=s.event,
        location=s.location,
        session_code=cast(SessionCode, s.code),
        session_name=s.name,
        date=s.date,
    )
    drivers = [SessionDriver(driver=d, team=t, laps=n) for d, t, n in _drivers(path)]
    return SessionDrivers(session=session, drivers=drivers)


def style_catalog(params: StylesQuery, paths: DataPaths) -> StyleCatalog:
    """The drivers and teammate pairs of one style season (default: the newest). 422 for a
    season without style data, 503 when the tables are missing or from another scoring run."""
    seasons = styles.style_seasons(paths.results).seasons  # ResultsUnavailableError if missing
    if not seasons:
        raise ResultsUnavailableError(STYLE_MISSING)
    year = seasons[-1] if params.year is None else params.year
    if year not in seasons:
        listed = ", ".join(str(y) for y in seasons)
        raise ToolInputError(f"No style data for {year}. Seasons: {listed}.")
    embeddings = styles.load_table("embeddings", paths.results)
    pair_rows = styles.load_table("embedding_pairs", paths.results)
    if embeddings is None or pair_rows is None:
        raise ResultsUnavailableError(STYLE_MISSING)

    in_year = embeddings[embeddings["year"] == year]
    drivers = sorted(
        (
            StyleDriver(driver=str(r["driver"]), team=str(r["team"]), events=int(r["events"]))
            for r in in_year[["driver", "team", "events"]].to_dict("records")
        ),
        key=lambda d: (d.team, d.driver),
    )

    # Rows come both ways round (driver, teammate) and (teammate, driver): one pair each.
    consistent: dict[tuple[str, str, str], bool] = {}
    events: dict[tuple[str, str, str], int] = {}
    for r in pair_rows[pair_rows["year"] == year].to_dict("records"):
        team, driver, mate = str(r["team"]), str(r["driver"]), str(r["teammate"])
        consistent[(team, driver, mate)] = bool(r["consistent"])
        first, second = sorted((driver, mate))
        events[(team, first, second)] = int(r["events"])
    pairs = []
    for team, a, b in sorted(events):
        ab = consistent.get((team, a, b), consistent.get((team, b, a), False))
        ba = consistent.get((team, b, a), ab)
        pairs.append(
            StylePair(team=team, a=a, b=b, events=events[(team, a, b)], consistent=(ab, ba))
        )
    return StyleCatalog(
        years=sorted(seasons, reverse=True), year=year, drivers=drivers, pairs=pairs
    )


# --- The routes -------------------------------------------------------------------------------


def catalog_etag(route: str, arguments: dict[str, Any], paths: DataPaths) -> str:
    """A strong ETag for a catalog answer: the same in every process while the data, the route,
    its arguments and the package version are unchanged (as routes/tools.etag for a tool)."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    key = repr((__version__, route, canonical, data_version(paths)))
    return f'"{hashlib.sha1(key.encode()).hexdigest()}"'


async def _answer[Q: ToolParams, A: BaseModel](
    request: Request,
    response: Response,
    route: str,
    params: Q,
    build: Callable[[Q, DataPaths], A],
) -> A | Response:
    """`build`'s answer with its ETag, or 304 when If-None-Match names it. The work (file stats,
    and on a cold cache reading parquet files) runs in a worker thread."""
    paths: DataPaths = request.app.state.paths
    arguments = params.model_dump(mode="json")
    if_none_match = request.headers.get("if-none-match")

    def run() -> tuple[str, A | None]:
        tag = catalog_etag(route, arguments, paths)
        return tag, None if matches(if_none_match, tag) else build(params, paths)

    tag, body = await anyio.to_thread.run_sync(run)
    headers = {"ETag": tag, "Cache-Control": CACHE_CONTROL}
    if body is None:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return body


router = APIRouter(tags=["catalog"])


@router.get(
    f"{CATALOG_PATH}sessions",
    summary="List the processed sessions",
    response_model=SessionCatalog,
    responses=_ERRORS,
)
async def sessions(
    request: Request, response: Response, params: Annotated[SessionsQuery, Query()]
) -> Any:
    """Every processed session, by season and race weekend (newest first), with whether the M4
    scores cover it and whether its season has driving-style data."""
    return await _answer(request, response, "sessions", params, lambda _, p: session_catalog(p))


@router.get(
    f"{CATALOG_PATH}drivers",
    summary="List a session's drivers",
    response_model=SessionDrivers,
    responses=_ERRORS,
)
async def drivers(
    request: Request, response: Response, params: Annotated[DriversQuery, Query()]
) -> Any:
    """The session that event, year and session name (as the tools read them), and its drivers
    with their team and laps, sorted by team, then code."""
    return await _answer(request, response, "drivers", params, session_drivers)


@router.get(
    f"{CATALOG_PATH}styles",
    summary="List a season's driving-style pairs",
    response_model=StyleCatalog,
    responses=_ERRORS,
)
async def style_pairs(
    request: Request, response: Response, params: Annotated[StylesQuery, Query()]
) -> Any:
    """The seasons with driving-style data, and one season's drivers and teammate pairs (each
    pair once, with the events they shared the car)."""
    return await _answer(request, response, "styles", params, style_catalog)
