"""compare_driving_styles: how two drivers take corners over a season, as a tool.

The M4 style step (`inference/style.py`) leaves each driver's medians per corner, session and
tyre (style_corners), the style embeddings and what the 2-D style map means under
data/results/, and its `compare_drivers` turns them into a comparison of two named drivers with
a summary already written for the model. This module adds what a tool needs around it: the
season when none is given (the latest both drove), car numbers read as the drivers who had them
(from the processed laps; the style tables have codes only), errors that say what the data has,
the tables kept in memory between calls and reloaded when `make m4` rebuilds them
(tools/cache.py), and the chart payload for the compare-styles bundle (`style_chart`, mirroring
the `StyleChart` interface in web/src/charts/compare-styles.ts), kept within the 30 KiB budget.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, cast

import duckdb
import numpy as np
import pandas as pd
from pydantic import Field

from race_engineer.config import PROCESSED_DIR, RESULTS_DIR
from race_engineer.data import store
from race_engineer.inference import style
from race_engineer.inference.style import METRIC_BY_NAME, METRICS, StyleComparison
from race_engineer.tools.cache import memo_by_files
from race_engineer.tools.params import FIRST_SEASON, LAST_SEASON, NOT_BOOL, ToolParams
from race_engineer.tools.registry import PAYLOAD_MAX_BYTES, DataPaths, ToolOutput, ToolSpec
from race_engineer.tools.sessions import ToolInputError

StyleKind = Literal["all", "quali", "race"]
CornerType = Literal["all", "slow", "medium", "fast"]
Role = Literal["a", "b", "other"]
MAP_COORDS = ["car_pc1", "car_pc2", "style_pc1", "style_pc2"]


class StyleParams(ToolParams):
    driver_a: Annotated[
        str,
        Field(
            min_length=1,
            max_length=24,
            description="First driver's three-letter code, e.g. 'HAM'. Differences are "
            "driver_a minus driver_b.",
        ),
    ]
    driver_b: Annotated[
        str,
        Field(
            min_length=1,
            max_length=24,
            description="Second driver's three-letter code, e.g. 'LEC'.",
        ),
    ]
    year: Annotated[
        int | None,
        Field(
            ge=FIRST_SEASON,
            le=LAST_SEASON,
            description="Season, e.g. 2025. Omit for the latest season both drivers drove.",
        ),
        NOT_BOOL,
    ] = None


# --- The chart payload (web/src/charts/compare-styles.ts) -----------------------------------


class StyleMetric(TypedDict):
    name: str
    label: str
    unit: str
    digits: int
    more: str  # what a positive difference means for driver_a, e.g. "brakes later"
    less: str


class StyleRow(TypedDict):
    """One difference, driver_a minus driver_b, with its 95% interval (None below 4 events)."""

    kind: StyleKind
    corner_type: CornerType
    metric: str
    diff: float
    lo: float | None
    hi: float | None
    p: float | None
    corners: int
    events: int
    clear: bool


class StyleEventRow(TypedDict):
    round: int
    event: str  # short form: "Australian" for the Australian Grand Prix
    metric: str
    diff: float
    corners: int


class StylePoint(TypedDict):
    driver: str
    team: str
    events: int
    car: tuple[float, float]  # the car plane: field-relative centroids
    style: tuple[float, float]  # the style plane: differences from the teammate
    role: Role


class StyleAxis(TypedDict):
    metric: str
    label: str
    r_pc1: float  # the arrow's direction on the style plane
    r_pc2: float
    R: float  # how much of the metric the plane holds (the arrow's length)


class StyleExplained(TypedDict):
    """Share of variance (0-1) each plane's two axes explain; None when not recorded."""

    car: tuple[float, float] | None
    style: tuple[float, float] | None


class StyleMap(TypedDict):
    points: list[StylePoint]
    axes: list[StyleAxis]
    explained: StyleExplained


class StyleChart(TypedDict):
    year: int
    driver_a: str
    driver_b: str
    teams: tuple[str, str]
    teammates: bool
    events: int
    corners: int
    metrics: list[StyleMetric]
    table: list[StyleRow]
    by_event: list[StyleEventRow]
    embedding: dict[str, float]  # StyleComparison.embedding without missing values
    map: StyleMap | None  # None without style embeddings for the season
    notes: list[str]


# --- The tables, kept between calls ----------------------------------------------------------


def _root(arguments: Mapping[str, Any]) -> Path:
    return arguments["root"]


@memo_by_files(lambda a: a["root"] / "style_corners.parquet", results=_root, maxsize=2)
def load_corners(root: Path = RESULTS_DIR) -> pd.DataFrame:
    """style_corners (118,664 rows, 0.16 s to read), until it is rebuilt or the scoring run
    changes. ResultsUnavailableError if it hasn't been built or is from another run."""
    return style.load_corners(root)


@memo_by_files(lambda a: a["root"] / f"style_{a['name']}.parquet", results=_root, maxsize=8)
def load_table(name: str, root: Path = RESULTS_DIR) -> pd.DataFrame | None:
    """style_<name>.parquet (embeddings, embedding_pairs, axes), or None if it hasn't been
    built, kept like load_corners. Cached frames are shared: don't change them."""
    return style.load_style_table(name, root)


@dataclass(frozen=True)
class StyleSeasons:
    """The seasons in the style data, and the seasons each driver code appears in."""

    seasons: tuple[int, ...]
    by_driver: Mapping[str, tuple[int, ...]]


@memo_by_files(lambda a: a["root"] / "style_corners.parquet", results=_root, maxsize=2)
def style_seasons(root: Path = RESULTS_DIR) -> StyleSeasons:
    present = load_corners(root)[["year", "driver"]].drop_duplicates()
    by_driver = present.groupby("driver")["year"].agg(lambda y: tuple(sorted(int(v) for v in y)))
    return StyleSeasons(
        seasons=tuple(sorted(int(y) for y in present["year"].unique())),
        by_driver={str(d): years for d, years in by_driver.items()},
    )


def warm_tables(paths: DataPaths) -> None:
    """Load every table a comparison reads, so the first request doesn't wait (API startup)."""
    style_seasons(paths.results)
    for name in ("embeddings", "embedding_pairs", "axes"):
        load_table(name, paths.results)


def car_numbers(year: int, processed: Path = PROCESSED_DIR) -> dict[str, str]:
    """Car number -> driver code in `year`'s processed sessions (the style tables have codes
    only), the latest holder when a number changed hands. Empty without laps for the season.
    One DuckDB query over two columns, about 10 ms."""
    pattern = store.table_path("laps", f"{year}_*", processed)
    if not any(pattern.parent.glob(pattern.name)):
        return {}
    query = (
        "select driver_number, arg_max(driver, filename) from "
        f"read_parquet('{pattern.as_posix()}', filename = true) group by 1"
    )
    con = duckdb.connect()  # one per call: tools run in worker threads
    try:
        rows = con.sql(query).fetchall()
    finally:
        con.close()
    return {str(number): str(code) for number, code in rows if number is not None and code}


def driver_code(text: str, year: int | None, seasons: tuple[int, ...], processed: Path) -> str:
    """A car number ("44", "#16") as the code of the driver who had it in `year`, or without a
    year in the newest style season that number was used (as the session tools read numbers);
    any other text unchanged."""
    number = text.strip().lstrip("#")
    if not number.isdigit():
        return text
    number = number.lstrip("0") or "0"
    for season in [year] if year is not None else reversed(seasons):
        if code := car_numbers(season, processed).get(number):
            return code
    return text


# --- The comparison --------------------------------------------------------------------------


def _years(years: tuple[int, ...] | list[int]) -> str:
    return ", ".join(str(y) for y in years)


def resolve_drivers(
    corners: pd.DataFrame,
    index: StyleSeasons,
    driver_a: str,
    driver_b: str,
    year: int | None,
) -> tuple[str, str, int, tuple[int, ...]]:
    """The two driver codes and the season to compare them in: `year`, or the latest season
    both drove, then also returning the other seasons both drove. ToolInputError, saying what
    the data has, for a season without style data, a code not in it (with the season's roster),
    the same driver twice or two drivers who never drove in the same season."""
    a, b = driver_a.strip().upper(), driver_b.strip().upper()
    if year is not None and year not in index.seasons:
        raise ToolInputError(f"No style data for {year}. Seasons: {_years(index.seasons)}.")
    for text, code, other in ((driver_a, a, b), (driver_b, b, a)):
        found = index.by_driver.get(code, ())
        known = year in found if year is not None else bool(found)
        if known:
            continue
        # The roster of the season asked for, else of the other driver's latest season.
        shown = year if year is not None else (index.by_driver.get(other) or index.seasons)[-1]
        try:
            style._driver_code(corners[corners["year"] == shown], text, shown)
        except ToolInputError as err:
            seen = f" {code} is in the style data for {_years(found)}." if found else ""
            raise ToolInputError(f"{err}{seen}") from None
    if a == b:
        raise ToolInputError(f"Both sides are {a}. Pick two different drivers.")
    if year is not None:
        return a, b, year, ()
    shared = sorted(set(index.by_driver[a]) & set(index.by_driver[b]))
    if not shared:
        raise ToolInputError(
            f"{a} and {b} never drove in the same season of the style data: {a} in "
            f"{_years(index.by_driver[a])}, {b} in {_years(index.by_driver[b])}. Compare each "
            "with a driver of their own seasons."
        )
    return a, b, shared[-1], tuple(shared[:-1])


@dataclass(frozen=True)
class StyleResult:
    comparison: StyleComparison
    chart: StyleChart
    summary: str  # the comparison's summary, plus how the season was picked


def compare_styles(
    driver_a: str, driver_b: str, year: int | None = None, root: Path = RESULTS_DIR
) -> StyleResult:
    """Compare two drivers' cornering in one season (A minus B) from the style tables under
    `root`: `inference.style.compare_drivers`, with the season picked when `year` is None and
    the chart payload. Raises ToolInputError (ResultsUnavailableError when the tables are
    missing or from another scoring run)."""
    corners = load_corners(root)
    a, b, season, others = resolve_drivers(corners, style_seasons(root), driver_a, driver_b, year)
    embeddings = load_table("embeddings", root)
    comparison = style.compare_drivers(
        a,
        b,
        season,
        corners=corners,
        embeddings=embeddings,
        embedding_pairs=load_table("embedding_pairs", root),
        root=root,
    )
    summary = comparison.summary
    if year is None:
        summary += f"\nSeason {season} is the latest in the driving-style data that both drove"
        summary += f" (both also drove {_years(others)}: give year for those)." if others else "."
    chart = style_chart(comparison, embeddings, load_table("axes", root))
    return StyleResult(comparison, chart, summary)


# --- The payload -----------------------------------------------------------------------------


def _num(value: Any, digits: int) -> float | None:
    """`value` rounded to `digits` decimals; None when it isn't a finite number (JSON has no
    NaN)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if np.isfinite(number) else None


def _sig(value: Any, figures: int = 2) -> float | None:
    """`value` to `figures` significant figures (p-values), None when not finite."""
    number = _num(value, 12)
    return None if number is None else float(f"{number:.{figures}g}")


def _pair(values: Any, digits: int) -> tuple[float, float] | None:
    """Two finite numbers, rounded, or None."""
    try:
        first, second = (_num(v, digits) for v in values)
    except (TypeError, ValueError):  # not two values
        return None
    return None if first is None or second is None else (first, second)


def _table_rows(table: pd.DataFrame) -> list[StyleRow]:
    """summarise_differences rows with any corners, rounded as the metric is shown."""
    rows: list[StyleRow] = []
    for r in table.to_dict("records"):
        metric = str(r["metric"])
        digits = METRIC_BY_NAME[metric].digits
        diff = _num(r["diff"], digits)
        if diff is None or not r["corners"]:
            continue
        rows.append(
            StyleRow(
                kind=cast(StyleKind, str(r["kind"])),
                corner_type=cast(CornerType, str(r["corner_type"])),
                metric=metric,
                diff=diff,
                lo=_num(r["lo"], digits),
                hi=_num(r["hi"], digits),
                p=_sig(r["p"]),
                corners=int(r["corners"]),
                events=int(r["events"]),
                clear=bool(r["clear"]),
            )
        )
    return rows


def short_event(name: str) -> str:
    """An event's label in the chart's by-event table, under the heading "Event": "Australian
    Grand Prix" -> "Australian" (168 rows in a 24-event season, so the 3 bytes of " GP" add up
    to 500)."""
    return name.removesuffix(" Grand Prix")


def _event_rows(by_event: pd.DataFrame) -> list[StyleEventRow]:
    """event_differences rows with any corners, rounded as the metric is shown, with short
    event names (a 24-event season has 168 rows, so every byte of a row counts)."""
    rows: list[StyleEventRow] = []
    for r in by_event.to_dict("records"):
        metric = str(r["metric"])
        diff = _num(r["diff"], METRIC_BY_NAME[metric].digits)
        if diff is None or not r["corners"]:
            continue
        rows.append(
            StyleEventRow(
                round=int(r["round"]),
                event=short_event(str(r["event"])),
                metric=metric,
                diff=diff,
                corners=int(r["corners"]),
            )
        )
    return rows


def _points(embeddings: pd.DataFrame, comparison: StyleComparison) -> list[StylePoint]:
    """The season's driver-seasons placed on both planes. A driver who changed team has a
    point per team; A's and B's are those of the compared teams (as compare_drivers picks)."""
    season = embeddings[embeddings["year"] == comparison.year]
    season = season[np.isfinite(season[MAP_COORDS].to_numpy(float)).all(axis=1)]
    roles: dict[Any, Role] = {}
    sides: tuple[tuple[Role, str, str], ...] = (
        ("a", comparison.driver_a, comparison.teams[0]),
        ("b", comparison.driver_b, comparison.teams[1]),
    )
    for role, driver, team in sides:
        mine = season[season["driver"] == driver]
        exact = mine[mine["team"] == team]
        pick = exact if not exact.empty else mine.sort_values("corners", ascending=False)
        if not pick.empty:
            roles[pick.index[0]] = role
    points: list[StylePoint] = []
    for index, r in zip(season.index, season.to_dict("records"), strict=True):
        points.append(
            StylePoint(
                driver=str(r["driver"]),
                team=str(r["team"]),
                events=int(r["events"]),
                car=(round(float(r["car_pc1"]), 2), round(float(r["car_pc2"]), 2)),
                style=(round(float(r["style_pc1"]), 2), round(float(r["style_pc2"]), 2)),
                role=roles.get(index, "other"),
            )
        )
    return points


def _axes(axes: pd.DataFrame | None) -> list[StyleAxis]:
    out: list[StyleAxis] = []
    for r in [] if axes is None else axes.to_dict("records"):
        metric = str(r["metric"])
        r1, r2, big_r = (_num(r[c], 3) for c in ("r_pc1", "r_pc2", "R"))
        if metric not in METRIC_BY_NAME or r1 is None or r2 is None or big_r is None:
            continue
        label = METRIC_BY_NAME[metric].label
        out.append(StyleAxis(metric=metric, label=label, r_pc1=r1, r_pc2=r2, R=big_r))
    return out


def style_map(
    comparison: StyleComparison, embeddings: pd.DataFrame | None, axes: pd.DataFrame | None
) -> StyleMap | None:
    """The style map of the comparison's season: every driver-season on the car and style
    planes, the metric arrows of the style plane and the share of variance each plane explains
    (from style_embeddings' pandas attrs, None if they weren't kept). None without embeddings
    for the season."""
    if embeddings is None or not set(MAP_COORDS) <= set(embeddings.columns):
        return None
    points = _points(embeddings, comparison)
    if not points:
        return None
    attrs = embeddings.attrs
    explained = StyleExplained(
        car=_pair(attrs.get("car_explained"), 4), style=_pair(attrs.get("style_explained"), 4)
    )
    return StyleMap(points=points, axes=_axes(axes), explained=explained)


def _note(text: str) -> str:
    """A summary's note as a chart note: "Note: they drove ..." -> "They drove ..."."""
    text = text.removeprefix("Note: ")
    return text[:1].upper() + text[1:]


def style_chart(
    comparison: StyleComparison,
    embeddings: pd.DataFrame | None = None,
    axes: pd.DataFrame | None = None,
) -> StyleChart:
    """The compare-styles chart's data: the differences by session kind and corner type and per
    event, the embedding numbers, the style map and the notes, with no NaN anywhere (missing
    values are None or left out). At most 84 differences, 7 per event and a point per driver:
    under 300 rows and, for a 24-event season, about 30.1 KB of JSON at most (all 1,201 real
    pairs), within the 30 KiB budget (registry.PAYLOAD_MAX_BYTES; see `within_budget`)."""
    metrics = [
        StyleMetric(
            name=m.name, label=m.label, unit=m.unit, digits=m.digits, more=m.more, less=m.less
        )
        for m in METRICS
    ]
    embedding = {
        key: rounded
        for key, value in comparison.embedding.items()
        if (rounded := _num(value, 4)) is not None
    }
    chart = StyleChart(
        year=comparison.year,
        driver_a=comparison.driver_a,
        driver_b=comparison.driver_b,
        teams=comparison.teams,
        teammates=comparison.teammates,
        events=comparison.events,
        corners=comparison.corners,
        metrics=metrics,
        table=_table_rows(comparison.table),
        by_event=_event_rows(comparison.by_event),
        embedding=embedding,
        map=style_map(comparison, embeddings, axes),
        notes=[_note(note) for note in comparison.notes],
    )
    return within_budget(chart)


def payload_bytes(chart: StyleChart) -> int:
    """The chart data's size as the servers send it: compact JSON."""
    return len(json.dumps(chart, separators=(",", ":"), allow_nan=False))


def within_budget(chart: StyleChart, max_bytes: int = PAYLOAD_MAX_BYTES) -> StyleChart:
    """The chart, kept within `max_bytes` of JSON. No real pair needs it today (the largest is
    about 30.1 KB, 2024 ALB and LEC), but a longer season, a bigger field or longer notes could
    pass the budget: then the by-event table drops its earliest rounds, one event at a time,
    and a note says so. The differences and the summary still use every event."""
    if payload_bytes(chart) <= max_bytes:
        return chart
    rows = chart["by_event"]
    rounds = sorted({r["round"] for r in rows})
    trimmed = chart.copy()
    for first in rounds[1:]:
        trimmed["by_event"] = [r for r in rows if r["round"] >= first]
        trimmed["notes"] = [
            *chart["notes"],
            f"The by-event table starts at round {first} to keep the chart small; the "
            "differences above use every event.",
        ]
        if payload_bytes(trimmed) <= max_bytes:
            return trimmed
    trimmed["by_event"] = []
    trimmed["notes"] = [
        *chart["notes"],
        "The by-event table is left out to keep the chart small; the differences above use "
        "every event.",
    ]
    return trimmed


# --- The tool --------------------------------------------------------------------------------


def _compare_driving_styles(p: StyleParams, paths: DataPaths) -> ToolOutput:
    seasons = style_seasons(paths.results).seasons
    a, b = (driver_code(d, p.year, seasons, paths.processed) for d in (p.driver_a, p.driver_b))
    result = compare_styles(a, b, p.year, paths.results)
    return ToolOutput(result.summary, cast(dict[str, Any], result.chart))


COMPARE_DRIVING_STYLES = ToolSpec(
    name="compare_driving_styles",
    title="Compare driving styles",
    description=(
        "Compare how two drivers take corners over a season, from every corner both drove in "
        "the same session on the same tyre compound: braking point, peak braking, minimum "
        "speed, full-throttle point, coasting, exit speed and time through the corner, as "
        "driver_a minus driver_b, split by slow, medium and fast corners and by qualifying and "
        "race, with 95% intervals, plus how similar their learned style embeddings are. "
        "Teammates are compared only where they shared a car, which isolates driving style; "
        "for drivers of different teams the differences mix car and driver, and the result "
        "says so. Shows the differences by corner type and a style map of the season's "
        "drivers. Use it for 'how does Hamilton's style differ from Leclerc's?' or 'who brakes "
        "later, Norris or Piastri?'. Omit year for the latest season both drove."
    ),
    params=StyleParams,
    run=_compare_driving_styles,
    chart="compare-styles",
    warm=warm_tables,
)
