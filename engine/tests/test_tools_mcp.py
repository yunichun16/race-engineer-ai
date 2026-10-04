"""The analysis tools over MCP: the five chart tools render in their MCP Apps (get_race_summary,
find_mistakes, explain_corner, compare_laps, compare_driving_styles), find_session and
list_sessions are text.

The tools come from the shared registry (tools.definitions.TOOLS); tests/test_tools_registry.py
checks that their schemas match the specs.
"""

import json
import logging
from pathlib import Path

import pytest
from mcp import Client
from mcp.client import advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID

from race_engineer.mcp_server import server
from race_engineer.mcp_server.server import COMPARE_LAPS_UI_URI, UI_BUNDLES, create_server, ui_uri
from race_engineer.tools.definitions import LIST_SESSIONS, TOOLS
from race_engineer.tools.params import ListSessionsParams
from race_engineer.tools.registry import PAYLOAD_MAX_BYTES, DataPaths, ToolOutput, ToolSpec
from race_engineer.tools.synthetic import (
    sample_comparison_session,
    sample_race_session,
    write_session,
)
from race_engineer.tools.synthetic_style import write_style_results

APPS_SUPPORT = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})
CHART_TOOLS = {
    "get_race_summary": "race-summary",
    "find_mistakes": "find-mistakes",
    "explain_corner": "explain-corner",
    "compare_laps": "compare-laps",
    "compare_driving_styles": "compare-styles",
}
TEXT_TOOLS = {"find_session", "list_sessions"}

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("processed")
    write_session(sample_comparison_session(), root)
    return root


def text(result) -> str:
    return getattr(result.content[0], "text", "")


def size(data: dict) -> int:
    """The chart data's size as the server sends it: compact JSON."""
    return len(json.dumps(data, separators=(",", ":"), allow_nan=False))


async def test_every_tool_is_listed_with_its_ui(root: Path) -> None:
    async with Client(create_server(root), extensions=[APPS_SUPPORT]) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"show_sample_telemetry", *CHART_TOOLS, *TEXT_TOOLS}
        assert {s.name for s in TOOLS} == set(CHART_TOOLS) | TEXT_TOOLS
        for name, bundle in CHART_TOOLS.items():
            meta = tools[name].meta
            assert meta is not None and meta["ui"]["resourceUri"] == ui_uri(bundle), name
        for name in TEXT_TOOLS:
            assert tools[name].meta is None, name  # text only
        for spec in TOOLS:
            annotations = tools[spec.name].annotations
            assert annotations is not None and annotations.read_only_hint, spec.name
        meta = tools["show_sample_telemetry"].meta
        assert meta is not None and meta["ui"]["resourceUri"] == ui_uri("telemetry")
        assert tools["compare_laps"].meta == {"ui": {"resourceUri": COMPARE_LAPS_UI_URI}}
        schema = tools["compare_laps"].input_schema
        assert set(schema["required"]) == {"event", "driver_a", "driver_b"}
        assert "melbourne" in schema["properties"]["event"]["description"]

        resource = (await client.read_resource(COMPARE_LAPS_UI_URI)).contents[0]
        assert resource.mime_type == APP_MIME_TYPE
        assert "<html" in getattr(resource, "text", "").lower()


async def test_each_chart_resource_serves_its_own_bundle(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The built bundles are gitignored, so stand in a marked page per name.
    monkeypatch.setattr(server, "load_ui", lambda name: f"<!doctype html><html>{name}</html>")
    assert set(UI_BUNDLES) == {"telemetry", *CHART_TOOLS.values()}  # the 6 bundles
    async with Client(create_server(root), extensions=[APPS_SUPPORT]) as client:
        listed = {r.uri for r in (await client.list_resources()).resources}
        assert listed == {ui_uri(name) for name in UI_BUNDLES}
        for name in UI_BUNDLES:
            resource = (await client.read_resource(ui_uri(name))).contents[0]
            assert getattr(resource, "text", "") == f"<!doctype html><html>{name}</html>"


async def test_the_instructions_name_every_tool(root: Path) -> None:
    async with Client(create_server(root)) as client:
        instructions = client.instructions or ""
    assert all(spec.name in instructions for spec in TOOLS)
    assert "Call find_session first when the session is unclear" in instructions


async def test_compare_laps_returns_summary_and_chart_data(root: Path) -> None:
    async with Client(create_server(root), extensions=[APPS_SUPPORT]) as client:
        args = {"event": "sample", "driver_a": "AAA", "driver_b": "bbb"}
        result = await client.call_tool("compare_laps", args)
        assert not result.is_error
        assert "AAA was 0.114 s faster" in text(result)
        data = result.structured_content
        assert data is not None
        assert [d["code"] for d in data["drivers"]] == ["AAA", "BBB"]
        assert len(data["distance_m"]) == len(data["delta_s"]) == len(data["drivers"][1]["speed"])
        assert [c["label"] for c in data["corners"]][:2] == ["T1", "T2"]


async def test_list_sessions(root: Path) -> None:
    async with Client(create_server(root)) as client:
        result = await client.call_tool("list_sessions", {})
        assert not result.is_error
        assert "Round 1 Sample Grand Prix (Synthetic Circuit): Qualifying (Q" in text(result)
        other_year = await client.call_tool("list_sessions", {"year": 2024})
        assert "Processed seasons: 2026" in text(other_year)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"driver_a": "AAA", "driver_b": "ZZZ"}, "No driver 'ZZZ'"),
        ({"driver_a": "AAA", "driver_b": "BBB", "session": "race"}, "no processed 'race'"),
        ({"driver_a": "AAA", "driver_b": "BBB", "event": "nowhere"}, "No processed event"),
        ({"driver_a": "AAA", "driver_b": "BBB", "event": "sample 2025", "year": 2026}, "says 2025"),
    ],
)
async def test_bad_input_gives_an_error_result(root: Path, args: dict, message: str) -> None:
    async with Client(create_server(root), extensions=[APPS_SUPPORT]) as client:
        result = await client.call_tool("compare_laps", {"event": "sample"} | args)
        assert result.is_error
        assert message in text(result)
        assert result.structured_content is None


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        (
            "compare_laps",
            {"event": "sample", "driver_a": "AAA", "driver_b": "BBB", "lap_a": 0},
            "Invalid arguments for compare_laps: lap_a: must be at least 1.",
        ),
        # An argument the tool doesn't have is refused, not dropped (REST and the chat agree).
        (
            "compare_laps",
            {"event": "sample", "driver_a": "AAA", "driver_b": "BBB", "lap": 5},
            "Invalid arguments for compare_laps: unknown parameter(s) lap (parameters: event, "
            "driver_a, driver_b, year, session, lap_a, lap_b).",
        ),
        (
            "list_sessions",
            {"year": 2026, "event": "sample"},
            "Invalid arguments for list_sessions: unknown parameter(s) event (parameters: year).",
        ),
        ("list_sessions", {"year": "soon"}, "Invalid arguments for list_sessions: year: must be"),
    ],
)
async def test_invalid_arguments_get_the_registry_message(
    root: Path, tool: str, args: dict, message: str
) -> None:
    async with Client(create_server(root)) as client:
        result = await client.call_tool(tool, args)
        assert result.is_error and result.structured_content is None
        assert text(result).startswith(message)
        assert "pydantic" not in text(result)


async def test_server_serves_the_specs_it_is_given(root: Path) -> None:
    async with Client(create_server(root, specs=[LIST_SESSIONS])) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {"show_sample_telemetry", "list_sessions"}


async def test_a_spec_with_a_chart_is_bound_to_its_bundle(root: Path) -> None:
    def run(p: ListSessionsParams, paths: DataPaths) -> ToolOutput:
        return ToolOutput(f"{p.year} in {paths.processed.name}", {"year": p.year})

    spec = ToolSpec("charted", "Charted", "A chart tool.", ListSessionsParams, run, chart="test")
    async with Client(create_server(root, specs=[spec]), extensions=[APPS_SUPPORT]) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "charted")
        assert tool.meta is not None and tool.meta["ui"]["resourceUri"] == ui_uri("test")
        assert tool.annotations is not None and tool.annotations.read_only_hint
        resource = (await client.read_resource(ui_uri("test"))).contents[0]
        assert "Chart bundle not built" in getattr(resource, "text", "")  # no such bundle
        result = await client.call_tool("charted", {"year": 2026})
        assert text(result) == f"2026 in {root.name}"
        assert result.structured_content == {"year": 2026}


async def test_an_unexpected_failure_is_a_generic_error_result(
    root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(p: ListSessionsParams, paths: DataPaths) -> ToolOutput:
        raise RuntimeError("the secret detail")

    spec = ToolSpec("boom", "Boom", "Fails.", ListSessionsParams, boom)
    with caplog.at_level(logging.ERROR, logger="race_engineer.mcp_server.server"):
        async with Client(create_server(root, specs=[spec])) as client:
            result = await client.call_tool("boom", {})
    assert result.is_error
    assert text(result) == "Internal error in boom; the server log has details."
    assert "the secret detail" in caplog.text


async def test_missing_dataset_is_an_error_result(tmp_path: Path) -> None:
    async with Client(create_server(tmp_path)) as client:
        result = await client.call_tool("list_sessions", {})
        assert result.is_error
        assert "No processed sessions" in text(result)


# find_mistakes and explain_corner: text for the model plus the same as structured data.


@pytest.fixture(scope="module")
def mistakes(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    from test_tools_mistakes import mistakes_dataset

    return mistakes_dataset(tmp_path_factory.mktemp("mistakes"))


async def test_mistake_tools_are_listed(mistakes: tuple[Path, Path]) -> None:
    async with Client(create_server(*mistakes), extensions=[APPS_SUPPORT]) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert {"find_mistakes", "explain_corner"} <= set(tools)
        meta = tools["find_mistakes"].meta
        assert meta == {"ui": {"resourceUri": "ui://race-engineer/find-mistakes.html"}}
        meta = tools["explain_corner"].meta
        assert meta == {"ui": {"resourceUri": "ui://race-engineer/explain-corner.html"}}
        annotations = tools["find_mistakes"].annotations
        assert annotations is not None and annotations.read_only_hint
        assert set(tools["find_mistakes"].input_schema["required"]) == {"event"}
        schema = tools["explain_corner"].input_schema
        assert set(schema["required"]) == {"event", "driver", "lap", "corner"}
        # The model is told every type it can see, and which laps are never candidates.
        description = tools["find_mistakes"].description or ""
        assert "unclear: slower than usual, but no single feature explains how" in description
        assert "opening laps, in and out laps and laps under yellow flags" in description
        # Traffic is left out, and the model is told what counts as traffic.
        assert "being lapped and letting a faster car by, racing another car" in description
        assert "cars around" in (tools["explain_corner"].description or "")


async def test_find_mistakes_tool(mistakes: tuple[Path, Path]) -> None:
    async with Client(create_server(*mistakes)) as client:
        result = await client.call_tool("find_mistakes", {"event": "sample", "limit": 2})
        assert not result.is_error
        assert "1. AAA [track limits / off track (race control)" in text(result)
        assert "Laps scored: 1-10 of 10" in text(result)
        data = result.structured_content
        assert data is not None and len(data["mistakes"]) == 2
        assert data["mistakes"][0]["lap_number"] == 9 and data["mistakes"][0]["turn"] == "3"
        assert data["coverage"]["scored_driver_laps"] == 14
        # The chart's map, and where each listed turn's apex is on it.
        track = data["track"]
        assert track is not None and len(track["outline"]) > 2
        assert {"width", "height", "turns", "corners", "start", "rotation_source"} <= set(track)
        assert data["mistakes"][0]["apex_m"] == 3000.0
        assert size(data) < PAYLOAD_MAX_BYTES


async def test_find_mistakes_tool_driver_with_nothing_scored(mistakes: tuple[Path, Path]) -> None:
    async with Client(create_server(*mistakes)) as client:
        result = await client.call_tool("find_mistakes", {"event": "sample", "driver": "CCC"})
        assert not result.is_error
        assert "CCC: no scored corners." in text(result)
        assert "can't say whether CCC made a mistake" in text(result)
        data = result.structured_content
        assert data is not None and data["scored"] == 0 and data["note"]


async def test_mistake_tools_with_stale_results(
    mistakes: tuple[Path, Path], tmp_path: Path
) -> None:
    from test_tools_mistakes import copy_results

    root, results = mistakes
    older = copy_results(results, tmp_path / "results", run_id="2026-01-01T00:00:00+00:00/old")
    async with Client(create_server(root, older)) as client:
        found = await client.call_tool("find_mistakes", {"event": "sample"})
        assert found.is_error and found.structured_content is None
        assert "mistakes.parquet was built from another scoring run" in text(found)
        assert "`race-engineer-infer explain`" in text(found)
        # explain_corner still answers from the processed session, without a verdict.
        args = {"event": "sample", "driver": "AAA", "lap": 10, "corner": 3}
        corner = await client.call_tool("explain_corner", args)
        assert not corner.is_error
        assert "No detector verdict for this corner. The M4 results can't be used" in text(corner)
        data = corner.structured_content
        assert data is not None and data["flagged"] is None and data["unscored"]


@pytest.mark.parametrize("corner", [3, "T3"])
async def test_explain_corner_tool(mistakes: tuple[Path, Path], corner: int | str) -> None:
    async with Client(create_server(*mistakes)) as client:
        args = {"event": "sample", "driver": "aaa", "lap": 10, "corner": corner}
        result = await client.call_tool("explain_corner", args)
        assert not result.is_error
        assert "Lap 10, turn 3: " in text(result) and "braked 50 m earlier" in text(result)
        data = result.structured_content
        assert data is not None and data["type"] == "early_braking"
        assert len(data["lap"]["speed"]) == len(data["usual"]["speed"]) == 80
        # The usual's middle half, the line, the ghost lap, the map and the replay.
        assert len(data["usual_lo"]["speed"]) == len(data["usual_hi"]["throttle"]) == 80
        assert len(data["offset"]["lap"]) == 80 and data["apex_m"] == 3000.0
        assert data["ghost"]["driver"] == "AAA" and data["map"]["window"]
        assert data["replay"]["ghost_label"] == f"usual: lap {data['ghost']['lap']}"
        corner_map, replay = data["map"], data["replay"]
        assert len(corner_map["window"]) == 80 and {"x", "y"} == set(corner_map["apex"])
        assert len(corner_map["outline"]) > 2 and corner_map["cars"] == []
        assert len(replay["me"]) == len(replay["dist"]) == len(replay["ghost"]) > 0
        assert replay["dt"] > 0 and len(replay["box"]) == 4
        assert size(data) < PAYLOAD_MAX_BYTES


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("find_mistakes", {"event": "sample", "driver": "ZZZ"}, "No driver 'ZZZ'"),
        ("explain_corner", {"event": "sample", "driver": "AAA", "lap": 2, "corner": 99}, "No turn"),
        ("explain_corner", {"event": "sample", "driver": "AAA", "lap": 99, "corner": 3}, "lap 99"),
    ],
)
async def test_mistake_tools_errors(
    mistakes: tuple[Path, Path], tool: str, args: dict, message: str
) -> None:
    async with Client(create_server(*mistakes)) as client:
        result = await client.call_tool(tool, args)
        assert result.is_error
        assert message in text(result)
        assert result.structured_content is None


# The tools added in M5 on their own synthetic data: a race weekend and the style tables.


@pytest.fixture(scope="module")
def race(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("race")
    root, results = base / "processed", base / "results"
    write_session(sample_race_session(), root)
    write_style_results(results)
    return root, results


async def test_find_session_tool(race: tuple[Path, Path]) -> None:
    async with Client(create_server(*race), extensions=[APPS_SUPPORT]) as client:
        result = await client.call_tool("find_session", {"recent": 0, "session": "R"})
        assert not result.is_error
        assert "2026 Sample Grand Prix, Race (R)" in text(result)
        data = result.structured_content
        assert data is not None and data["match"]["key"] == "2026_01_R"
        assert {"match", "candidates", "weekend", "coverage"} <= set(data)
        assert size(data) < PAYLOAD_MAX_BYTES


async def test_race_summary_tool(race: tuple[Path, Path]) -> None:
    async with Client(create_server(*race), extensions=[APPS_SUPPORT]) as client:
        result = await client.call_tool("get_race_summary", {"event": "sample", "driver": "AAA"})
        assert not result.is_error
        assert "Won by AAA" in text(result)
        data = result.structured_content
        assert data is not None and data["focus"] == "AAA" and data["session_code"] == "R"
        assert [d["code"] for d in data["drivers"]][:2] == ["AAA", "BBB"]
        assert [n["kind"] for n in data["neutralisations"]] == ["SC", "VSC"]
        assert size(data) < PAYLOAD_MAX_BYTES
        # Qualifying isn't a race: the error comes back as an error result.
        quali = await client.call_tool("get_race_summary", {"event": "sample", "session": "Q"})
        assert quali.is_error and quali.structured_content is None
        assert "covers races and sprints" in text(quali)


async def test_compare_driving_styles_tool(race: tuple[Path, Path]) -> None:
    async with Client(create_server(*race), extensions=[APPS_SUPPORT]) as client:
        args = {"driver_a": "AAA", "driver_b": "BBB"}
        result = await client.call_tool("compare_driving_styles", args)
        assert not result.is_error
        assert "AAA and BBB" in text(result)
        data = result.structured_content
        assert data is not None and data["teammates"] and data["map"] is not None
        assert {"metrics", "table", "by_event", "embedding", "notes"} <= set(data)
        assert size(data) < PAYLOAD_MAX_BYTES
        unknown = await client.call_tool("compare_driving_styles", args | {"driver_b": "XYZ"})
        assert unknown.is_error and "XYZ" in text(unknown)
