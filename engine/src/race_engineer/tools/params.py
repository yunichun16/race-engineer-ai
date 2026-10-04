"""The tools' inputs as pydantic models, shared by the MCP server, the REST API and the chat.

Each tool has a `Params` model (unknown fields are refused) whose fields are built from the
`Annotated` aliases below, so a parameter means the same thing, carries the same description and
accepts the same values in every tool and every front end. The MCP tool signatures and the REST
query parameters are generated from these models (`tools.registry`), and field order is the MCP
parameter order.

The limits are generous, there to refuse nonsense early (a season in the 1950s, lap 500, a
paragraph as a driver code) with a clear message rather than to second-guess the tools, which
still check names and numbers against the data.

Every whole-number field refuses true and false (`NOT_BOOL`). pydantic reads true as 1, so
find_session's recent=true ("the most recent") would have meant the session before last, and
lap=true lap 1; the REST routes already refuse "true" as not a number.

The new tools' models (find_session, get_race_summary, compare_driving_styles) live in their own
modules and reuse these aliases, or these limits where a field means something of its own there
(find_session's year and session also scope its round and recent fields).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic_core import PydanticCustomError

from race_engineer.tools.mistakes import DEFAULT_LIMIT, MAX_LIMIT

FIRST_SEASON = 2018
LAST_SEASON = 2030


def _not_bool(value: object) -> object:
    """Refuse true and false where a whole number is expected, with the error a non-number
    gets ("must be a whole number"), instead of pydantic's reading of true as 1."""
    if isinstance(value, bool):
        raise PydanticCustomError("int_type", "Input should be a valid integer")
    return value


# Goes after the Field in every whole-number field's Annotated: before it, pydantic would check
# the Field's limits apart from the number, and fail on None.
NOT_BOOL = BeforeValidator(_not_bool)

Event = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        description="Grand Prix name, location or country, case-insensitive; partial names "
        "work. Examples: 'Australian Grand Prix', 'melbourne', 'monza'.",
    ),
]
Year = Annotated[
    int | None,
    Field(
        ge=FIRST_SEASON,
        le=LAST_SEASON,
        description="Season, e.g. 2026. Omit for the latest season with the event.",
    ),
    NOT_BOOL,
]
SessionText = Annotated[
    str | None,
    Field(
        max_length=30,
        description="Session: 'Q' or 'qualifying', 'R' or 'race', 'S' or 'sprint', 'SQ' or "
        "'sprint qualifying'. Omit for qualifying.",
    ),
]
# A code in any case or a car number ("1", "#44"), as the tools accept.
Driver = Annotated[
    str,
    Field(min_length=1, max_length=24, description="Three-letter driver code, e.g. 'LEC'."),
]
# No minimum length: an empty string means the same as leaving it out.
OptDriver = Annotated[
    str | None,
    Field(
        max_length=24,
        description="Three-letter driver code, e.g. 'COL'. Omit for the whole field.",
    ),
]
Lap = Annotated[int, Field(ge=1, le=100, description="Lap number, e.g. 2."), NOT_BOOL]
# A turn number, or the text form with its letter ("T9a"); the tools parse the text.
Corner = Annotated[
    Annotated[int, Field(ge=1, le=99), NOT_BOOL]
    | Annotated[str, Field(min_length=1, max_length=8)],
    Field(description="Turn number, e.g. 7, or with its letter, e.g. '9a' or 'T9a'."),
]
Limit = Annotated[
    int,
    Field(ge=1, le=MAX_LIMIT, description=f"How many mistakes to list, 1 to {MAX_LIMIT}."),
    NOT_BOOL,
]

# compare_laps names its two sides; the gap's sign depends on which is which.
DriverA = Annotated[
    str,
    Field(
        min_length=1,
        max_length=24,
        description="First driver's three-letter code, e.g. 'RUS', 'VER', 'LEC'.",
    ),
]
DriverB = Annotated[
    str,
    Field(
        min_length=1,
        max_length=24,
        description="Second driver's three-letter code, e.g. 'ANT'. Gaps are reported for "
        "driver_b relative to driver_a.",
    ),
]
LapA = Annotated[
    int | None,
    Field(ge=1, le=100, description="Lap number for driver_a. Omit for their fastest clean lap."),
    NOT_BOOL,
]
LapB = Annotated[
    int | None,
    Field(ge=1, le=100, description="Lap number for driver_b. Omit for their fastest clean lap."),
    NOT_BOOL,
]


class ToolParams(BaseModel):
    """Base of every tool's inputs: unknown fields are an error, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


class ListSessionsParams(ToolParams):
    year: Annotated[
        int | None,
        Field(
            ge=FIRST_SEASON,
            le=LAST_SEASON,
            description="Only this season, e.g. 2026. Omit for all.",
        ),
        NOT_BOOL,
    ] = None


class FindMistakesParams(ToolParams):
    event: Event
    driver: OptDriver = None
    year: Year = None
    session: SessionText = None
    limit: Limit = DEFAULT_LIMIT


class ExplainCornerParams(ToolParams):
    event: Event
    driver: Driver
    lap: Lap
    corner: Corner
    year: Year = None
    session: SessionText = None


class CompareLapsParams(ToolParams):
    event: Event
    driver_a: DriverA
    driver_b: DriverB
    year: Year = None
    session: SessionText = None
    lap_a: LapA = None
    lap_b: LapB = None
