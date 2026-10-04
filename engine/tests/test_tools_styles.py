"""compare_driving_styles on synthetic style results (tools/synthetic_style.py): the season picked
when none is given, teammates against drivers of different teams, the errors, stale results and
the chart payload, through the registry and over MCP."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mcp import Client
from mcp.client import advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID

from race_engineer.inference.provenance import MANIFEST, parquet_run, scoring_run
from race_engineer.inference.style import METRIC_NAMES, StyleComparison
from race_engineer.mcp_server.server import create_server
from race_engineer.tools import styles
from race_engineer.tools.registry import (
    PAYLOAD_MAX_BYTES,
    DataPaths,
    run_tool,
    ui_uri,
    warm,
    without_titles,
)
from race_engineer.tools.sessions import ResultsUnavailableError, ToolInputError
from race_engineer.tools.styles import (
    COMPARE_DRIVING_STYLES,
    StyleChart,
    compare_styles,
    short_event,
    style_chart,
)
from race_engineer.tools.synthetic import SyntheticLap, SyntheticSession, write_session
from race_engineer.tools.synthetic_style import SYNTHETIC_RUN, write_style_results

SESSION = SyntheticSession(2025, 1, "Q", "Lakeside Grand Prix", "Lakeside")

CHART_KEYS = set(StyleChart.__annotations__)
ROW_KEYS = {"kind", "corner_type", "metric", "diff", "lo", "hi", "p", "corners", "events", "clear"}
EMBEDDING_KEYS = {
    "cos_centroids",
    "cos_style",
    "season_cos_teammate",
    "season_cos_field",
    "style_consistency",
    "consistency_p",
    "consistency_null95",
}


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("results")
    write_style_results(root)
    return root


@pytest.fixture
def own_results(results: Path, tmp_path: Path) -> Path:
    """A copy of the synthetic results that a test may change."""
    return Path(shutil.copytree(results, tmp_path / "results"))


def cell(chart: StyleChart, kind: str, corner_type: str, metric: str) -> dict:
    found = [
        r
        for r in chart["table"]
        if (r["kind"], r["corner_type"], r["metric"]) == (kind, corner_type, metric)
    ]
    assert len(found) == 1
    return dict(found[0])


def test_synthetic_results_are_one_scoring_run(tmp_path: Path) -> None:
    (tmp_path / MANIFEST).write_text(json.dumps({"run_id": "old", "frame_key": "abc"}))
    paths = write_style_results(tmp_path, "run-7")
    assert {p.name for p in paths} == {
        f"style_{name}.parquet"
        for name in ("corners", "profiles", "events", "embeddings", "embedding_pairs", "axes")
    }
    assert {parquet_run(p) for p in paths} == {"run-7"} and scoring_run(tmp_path) == "run-7"
    assert json.loads((tmp_path / MANIFEST).read_text())["frame_key"] == "abc"  # kept


def test_default_season_is_the_latest_both_drove(results: Path) -> None:
    only = compare_styles("aaa", "BBB", root=results)
    assert only.comparison.year == 2025 and only.chart["driver_a"] == "AAA"
    assert only.summary.endswith(
        "Season 2025 is the latest in the driving-style data that both drove."
    )
    both = compare_styles("AAA", "CCC", root=results)
    assert both.comparison.year == 2025
    assert "(both also drove 2024: give year for those)" in both.summary
    assert compare_styles("AAA", "GGG", root=results).comparison.year == 2024  # GGG: 2024 only
    asked = compare_styles("AAA", "CCC", 2024, root=results)
    assert asked.comparison.year == 2024 and "Season 2024" not in asked.summary


def test_teammates_are_compared_in_their_shared_car(results: Path) -> None:
    result = compare_styles("AAA", "BBB", root=results)
    chart = result.chart
    assert chart["teammates"] and chart["teams"] == ("Red", "Red") and chart["events"] == 6
    assert not any("different cars" in note for note in chart["notes"])
    # AAA brakes 10 m later than BBB in slow corners, and only there.
    slow = cell(chart, "all", "slow", "brake_start_d")
    assert slow["diff"] == pytest.approx(10.0, abs=1.5) and slow["clear"]
    assert slow["lo"] < slow["diff"] < slow["hi"] and slow["events"] == 6
    assert abs(cell(chart, "all", "fast", "brake_start_d")["diff"]) < 1.5
    assert "AAA brakes later than BBB" in result.summary


def test_drivers_of_different_teams_mix_car_and_driver(results: Path) -> None:
    result = compare_styles("AAA", "CCC", 2025, root=results)
    chart = result.chart
    assert not chart["teammates"] and chart["teams"] == ("Red", "Blue")
    assert chart["notes"][0].startswith("They drove different cars")
    assert "different cars, so these differences mix car and driver" in result.summary
    # CCC carries 2 km/h more minimum speed than everyone else.
    v_min = cell(chart, "all", "all", "v_min")
    assert v_min["diff"] == pytest.approx(-2.0, abs=0.5) and v_min["clear"]


def test_a_short_partnership_has_no_intervals(results: Path) -> None:
    result = compare_styles("AAA", "GGG", root=results)  # GGG gave his seat to HHH after round 3
    chart = result.chart
    assert chart["year"] == 2024 and chart["teammates"] and chart["events"] == 3
    assert all(r["lo"] is None and r["hi"] is None and r["p"] is None for r in chart["table"])
    assert not any(r["clear"] for r in chart["table"])
    assert chart["notes"] == [
        "They were teammates at 3 of the 5 2024 events either of them drove; only those are "
        "compared."
    ]
    assert "3 events are too few for intervals" in result.summary
    json.dumps(chart, allow_nan=False)
    assert chart["map"] is not None
    roles = {p["driver"]: p["role"] for p in chart["map"]["points"]}
    assert roles == {"AAA": "a", "GGG": "b", "HHH": "other", "CCC": "other", "DDD": "other"}


@pytest.mark.parametrize(
    ("a", "b", "year", "message"),
    [
        ("AAA", "aaa", None, "Both sides are AAA. Pick two different drivers."),
        ("XYZ", "AAA", None, "No driver 'XYZ' in the 2025 style data. Drivers: AAA (Red), BBB"),
        # the roster of the other driver's latest season
        ("XYZ", "GGG", None, "No driver 'XYZ' in the 2024 style data. Drivers: AAA (Red), CCC"),
        ("44", "AAA", 2025, "Use the three-letter code."),
        ("GGG", "AAA", 2025, "Use the three-letter code. GGG is in the style data for 2024."),
        ("AAA", "BBB", 2024, "No driver 'BBB' in the 2024 style data."),
        ("AAA", "BBB", 2023, "No style data for 2023. Seasons: 2024, 2025."),
        (
            "GGG",
            "BBB",
            None,
            "GGG and BBB never drove in the same season of the style data: GGG in 2024, BBB "
            "in 2025.",
        ),
    ],
)
def test_requests_the_data_cant_answer(
    results: Path, a: str, b: str, year: int | None, message: str
) -> None:
    with pytest.raises(ToolInputError) as caught:
        compare_styles(a, b, year, root=results)
    assert message in str(caught.value)
    assert not isinstance(caught.value, ResultsUnavailableError)


def test_stale_or_missing_tables_are_unavailable(own_results: Path, tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "processed", own_results)
    args = {"driver_a": "AAA", "driver_b": "BBB"}
    assert run_tool(COMPARE_DRIVING_STYLES, args, paths).data is not None
    # A later scoring run: the tables describe other results than the tools' now.
    (own_results / MANIFEST).write_text(json.dumps({"run_id": "run-2"}))
    with pytest.raises(ResultsUnavailableError, match=r"out of date.*another scoring run"):
        run_tool(COMPARE_DRIVING_STYLES, args, paths)
    write_style_results(own_results, "run-2")  # `make m4` again
    assert run_tool(COMPARE_DRIVING_STYLES, args, paths).data is not None
    # Scored, but the style step never ran.
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / MANIFEST).write_text(json.dumps({"run_id": "run-2"}))
    with pytest.raises(ResultsUnavailableError, match="hasn't been built"):
        run_tool(COMPARE_DRIVING_STYLES, args, DataPaths(tmp_path / "processed", empty))


def test_tables_are_kept_until_rebuilt(own_results: Path) -> None:
    assert scoring_run(own_results) == SYNTHETIC_RUN
    first = styles.load_corners(own_results)
    assert styles.load_corners(own_results) is first
    assert styles.load_table("embeddings", own_results) is styles.load_table(
        "embeddings", own_results
    )
    write_style_results(own_results, "run-3")
    again = styles.load_corners(own_results)
    assert again is not first and again.equals(first)


def test_chart_payload(results: Path) -> None:
    chart = compare_styles("AAA", "BBB", root=results).chart
    assert set(chart) == CHART_KEYS
    text = json.dumps(chart, allow_nan=False, separators=(",", ":"))  # no NaN anywhere
    assert len(text) < PAYLOAD_MAX_BYTES
    points = chart["map"]["points"] if chart["map"] else []
    assert len(chart["table"]) + len(chart["by_event"]) + len(points) < 300
    assert [m["name"] for m in chart["metrics"]] == list(METRIC_NAMES)
    assert chart["metrics"][0]["more"] == "brakes later"
    # every session kind, corner type and metric, A minus B
    assert len(chart["table"]) == 3 * 4 * len(METRIC_NAMES)
    assert all(set(r) == ROW_KEYS for r in chart["table"])
    assert len(chart["by_event"]) == 6 * len(METRIC_NAMES)
    assert chart["by_event"][0]["event"] == "Lakeside"
    assert set(chart["embedding"]) <= EMBEDDING_KEYS and chart["embedding"]["cos_centroids"] > 0.8

    style_map = chart["map"]
    assert style_map is not None
    roles = {p["driver"]: p["role"] for p in style_map["points"]}
    assert roles == {"AAA": "a", "BBB": "b", "CCC": "other", "DDD": "other", "EEE": "other",
                     "FFF": "other"}  # fmt: skip
    assert {a["metric"] for a in style_map["axes"]} == set(METRIC_NAMES)
    assert style_map["axes"][0]["label"] == "braking point"
    for plane in ("car", "style"):
        shares = style_map["explained"][plane]
        assert shares is not None and 0 < shares[1] <= shares[0] and sum(shares) <= 1


def test_chart_without_embeddings_or_their_attrs(own_results: Path, results: Path) -> None:
    for name in ("embeddings", "embedding_pairs", "axes"):
        (own_results / f"style_{name}.parquet").unlink()
    result = compare_styles("AAA", "BBB", root=own_results)
    assert result.chart["map"] is None and result.chart["embedding"] == {}
    assert "Style embedding" not in result.summary

    embeddings = styles.load_table("embeddings", results)
    assert embeddings is not None
    bare = embeddings.copy()
    bare.attrs = {}  # written by an older pandas or pyarrow: no explained variance
    chart = style_chart(compare_styles("AAA", "BBB", root=results).comparison, bare, None)
    assert chart["map"] is not None and chart["map"]["axes"] == []
    assert chart["map"]["explained"] == {"car": None, "style": None}


def test_short_event() -> None:
    assert short_event("Emilia Romagna Grand Prix") == "Emilia Romagna"
    assert short_event("Pre-Season Testing") == "Pre-Season Testing"


# The 30 KiB budget on a season as long as the real ones get: 24 events, 22 drivers.

CALENDAR = (
    "Bahrain", "Saudi Arabian", "Australian", "Japanese", "Chinese", "Miami", "Emilia Romagna",
    "Monaco", "Canadian", "Spanish", "Austrian", "British", "Hungarian", "Belgian", "Dutch",
    "Italian", "Azerbaijan", "Singapore", "United States", "Mexico City", "São Paulo",
    "Las Vegas", "Qatar", "Abu Dhabi",
)  # fmt: skip
TEAMS = ("Red Bull Racing", "Ferrari", "Mercedes", "McLaren", "Aston Martin", "Alpine",
         "Williams", "Racing Bulls", "Kick Sauber", "Haas F1 Team", "Cadillac")  # fmt: skip


# Per metric: a difference, its interval and a by-event difference as wide as the widest real
# ones (2024 ALB and LEC, the largest real payload: signs, decimals, 7-character p-values).
WIDE = {
    "brake_start_d": (-15.3, -19.3, -11.2, -12.7),
    "decel_entry": (-1.09, -1.65, -0.53, -1.27),
    "v_min": (-13.0, -14.0, -12.0, -10.4),
    "throttle_pickup_d": (14.3, 11.8, 16.8, 21.5),
    "coast_m": (14.5, 12.0, 17.1, 18.2),
    "v_exit": (-12.5, -13.3, -11.8, -10.6),
    "segment_time_s": (0.099, 0.067, 0.131, 0.123),
}


def full_season(events: int = 24, drivers: int = 22) -> tuple[StyleComparison, pd.DataFrame]:
    """A comparison with every row the chart can have, numbers as wide as real ones (WIDE), the
    three longest real notes, every embedding number and a style map of `drivers` drivers plus
    two who changed team."""
    kinds, types = ("all", "quali", "race"), ("all", "slow", "medium", "fast")
    table = pd.DataFrame(
        [
            {"kind": k, "corner_type": t, "metric": m, "diff": WIDE[m][0], "lo": WIDE[m][1],
             "hi": WIDE[m][2], "p": 2.7e-11, "corners": 1110, "events": events, "clear": True}
            for k in kinds for t in types for m in METRIC_NAMES
        ]
    )  # fmt: skip
    by_event = pd.DataFrame(
        [
            {"round": r + 1, "event": f"{CALENDAR[r % len(CALENDAR)]} Grand Prix", "metric": m,
             "diff": WIDE[m][3], "corners": 21}
            for r in range(events) for m in METRIC_NAMES
        ]
    )  # fmt: skip
    notes = [
        "Note: they drove different cars, so these differences mix car and driver; only "
        "teammates isolate driving style.",
        "ALB wasn't back on full throttle within 145 m of the corner on 12% of laps at the "
        "corners that own their slowest point; those count as later than the window.",
        "In 2026 drivers lift and coast to manage energy, often on team instructions, so "
        "coasting and braking-point differences can be strategy rather than style.",
    ]
    embedding = dict.fromkeys(EMBEDDING_KEYS, -0.1234)
    comparison = StyleComparison(
        2026, "AAA", "BBB", (TEAMS[0], TEAMS[1]), False, events, 1110, table, by_event,
        embedding, notes, "summary",
    )  # fmt: skip
    codes = ["AAA", "BBB", *(f"D{i:02d}" for i in range(2, drivers)), "D02", "D03"]
    embeddings = pd.DataFrame(
        {"year": 2026, "driver": codes, "team": [TEAMS[i % len(TEAMS)] for i in range(len(codes))],
         "events": events, "corners": 500, "car_pc1": -1.23, "car_pc2": -2.34,
         "style_pc1": -3.45, "style_pc2": -4.56}
    )  # fmt: skip
    embeddings.attrs = {"car_explained": [0.5207, 0.2415], "style_explained": [0.3423, 0.2953]}
    return comparison, embeddings


def test_a_full_season_fits_the_budget() -> None:
    # Every number as wide as the widest real ones, three notes, 22 drivers and 24 events: a
    # little wider than any real pair (the largest of all 1,201 is 30,089 B and loses nothing),
    # so the per-event table may lose its first event or two, never more.
    comparison, embeddings = full_season()
    axes = pd.DataFrame(
        {"metric": list(METRIC_NAMES), "r_pc1": -0.123, "r_pc2": -0.456, "R": 0.789}
    )
    chart = style_chart(comparison, embeddings, axes)
    assert styles.payload_bytes(chart) <= PAYLOAD_MAX_BYTES
    assert chart["map"] is not None and len(chart["map"]["points"]) == 24
    assert len(chart["table"]) == 84
    rounds = sorted({r["round"] for r in chart["by_event"]})
    assert rounds == list(range(rounds[0], 25)) and rounds[0] <= 3
    if rounds[0] > 1:
        assert chart["notes"][-1].startswith(f"The by-event table starts at round {rounds[0]}")
    assert {r["event"] for r in chart["by_event"]} >= {"Abu Dhabi", "São Paulo", "Mexico City"}


def test_an_oversized_chart_drops_the_earliest_events() -> None:
    comparison, embeddings = full_season(events=10)
    chart = style_chart(comparison, embeddings)
    size = styles.payload_bytes(chart)
    trimmed = styles.within_budget(chart, size - 200)  # about 2 events' rows over
    rounds = sorted({r["round"] for r in trimmed["by_event"]})
    assert rounds == list(range(rounds[0], 11)) and 2 <= rounds[0] <= 4
    assert trimmed["notes"][-1] == (
        f"The by-event table starts at round {rounds[0]} to keep the chart small; the "
        "differences above use every event."
    )
    assert trimmed["table"] == chart["table"] and len(chart["by_event"]) == 70  # untouched
    assert styles.payload_bytes(trimmed) <= size - 200
    bare = styles.within_budget(chart, 1000)  # can't fit: the table goes, the rest stays
    assert bare["by_event"] == [] and bare["notes"][-1].startswith("The by-event table is left")


def test_spec_runs_through_the_registry(results: Path, tmp_path: Path) -> None:
    spec = COMPARE_DRIVING_STYLES
    assert (spec.name, spec.chart, spec.heavy) == (
        "compare_driving_styles",
        "compare-styles",
        False,
    )
    schema = spec.input_schema()
    assert list(schema["properties"]) == ["driver_a", "driver_b", "year"]
    assert schema["required"] == ["driver_a", "driver_b"]
    assert schema["additionalProperties"] is False
    paths = DataPaths(tmp_path, results)
    output = run_tool(spec, {"driver_a": "AAA", "driver_b": "BBB"}, paths)
    assert output.summary == compare_styles("AAA", "BBB", root=results).summary
    assert output.data is not None and output.data["year"] == 2025
    with pytest.raises(ToolInputError, match="driver_b: required"):
        run_tool(spec, {"driver_a": "AAA"}, paths)
    with pytest.raises(ToolInputError, match="year: must be at least 2018"):
        run_tool(spec, {"driver_a": "AAA", "driver_b": "BBB", "year": 1990}, paths)

    seconds = warm(paths, specs=[spec])
    assert "compare_driving_styles" in seconds
    before = styles.load_corners.cache_info().hits
    styles.load_corners(results)
    assert styles.load_corners.cache_info().hits == before + 1


def test_car_numbers_are_read_as_their_drivers(results: Path, tmp_path: Path) -> None:
    # As the session tools read them: the code of the driver who had the number that season.
    def lap(driver: str, number: str) -> SyntheticLap:
        return SyntheticLap(driver, 1, np.full(SESSION.n_grid, 200.0), driver_number=number)

    write_session(replace(SESSION, laps=(lap("AAA", "7"), lap("BBB", "8"))), tmp_path)
    paths = DataPaths(tmp_path, results)
    output = run_tool(COMPARE_DRIVING_STYLES, {"driver_a": "#7", "driver_b": "8"}, paths)
    assert output.summary == compare_styles("AAA", "BBB", root=results).summary
    assert styles.car_numbers(2025, tmp_path) == {"7": "AAA", "8": "BBB"}
    assert styles.car_numbers(2024, tmp_path) == {}  # no laps processed for that season
    with pytest.raises(ToolInputError, match="No driver '9' in the 2025 style data"):
        run_tool(COMPARE_DRIVING_STYLES, {"driver_a": "9", "driver_b": "BBB"}, paths)


@pytest.mark.anyio
async def test_over_mcp_with_its_chart(results: Path, tmp_path: Path) -> None:
    spec = COMPARE_DRIVING_STYLES
    apps = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})
    server = create_server(tmp_path, results, specs=[spec])
    async with Client(server, extensions=[apps]) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == spec.name)
        assert tool.meta is not None and tool.meta["ui"]["resourceUri"] == ui_uri("compare-styles")
        assert without_titles(tool.input_schema) == spec.input_schema()
        result = await client.call_tool(spec.name, {"driver_a": "AAA", "driver_b": "BBB"})
        assert not result.is_error
        assert getattr(result.content[0], "text", "").startswith("2025: AAA and BBB")
        data = result.structured_content
        assert data is not None and data["teams"] == ["Red", "Red"] and len(data["table"]) == 84
        failed = await client.call_tool(spec.name, {"driver_a": "AAA", "driver_b": "ZZZ"})
        assert failed.is_error and "No driver 'ZZZ'" in getattr(failed.content[0], "text", "")
