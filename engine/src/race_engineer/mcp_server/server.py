"""MCP server for Race Engineer AI.

Tools answer questions about the processed F1 dataset and, through MCP Apps, render their
results as interactive charts inside Claude. Each chart is a single-file HTML bundle built by
Vite from `web/src/mcp-app/<name>.html` into `ui/` (`make mcp-app`).

The analysis tools come from the shared registry (`tools.definitions.TOOLS`), the same specs the
REST API and the chat serve: each spec's description, input schema and validation are used as
they are, and its `run` answers with a summary for the model plus the chart data, sent as
`structuredContent`. A spec with a chart is bound to its `ui://` bundle; the others are text.

- find_session: which processed session the user means ("last race", "Monza 2025 quali"), the
  rest of that weekend and whether mistake scores exist (text plus data, no chart).
- list_sessions: which events and sessions are available (text only).
- get_race_summary: a race or sprint from the timing data, with a lap-by-lap position chart.
- find_mistakes: the biggest explained driver mistakes in a session, traffic left out, with the
  list and a track map.
- explain_corner: one corner on one lap against the driver's usual, with the traces, a map and
  a replay of the cars around.
- compare_laps: two drivers' laps side by side (speed, running gap, pedals, corners).
- compare_driving_styles: how two drivers take corners over a season, by corner type, with a
  style map.
- show_sample_telemetry: the M0 spike on a synthetic lap, kept to test the chart pipeline
  (MCP only, not a registry tool and not in the chat).

Run over stdio (Claude desktop app):   uv run race-engineer-mcp
Run over HTTP (remote hosts, testing):  uv run race-engineer-mcp --transport streamable-http
"""

from __future__ import annotations

import argparse
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any

from mcp.server.apps import Apps
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult, InputRequiredResult, TextContent, ToolAnnotations
from mcp_types import Tool as MCPTool
from pydantic import BaseModel, Field

from race_engineer import __version__
from race_engineer.config import PROCESSED_DIR, RESULTS_DIR
from race_engineer.mcp_server.sample_data import CORNERS, TRACK_LENGTH_M, build_sample_lap
from race_engineer.tools.definitions import COMPARE_LAPS, EXPLAIN_CORNER, FIND_MISTAKES, TOOLS
from race_engineer.tools.mistakes import DEFAULT_LIMIT
from race_engineer.tools.registry import (
    DataPaths,
    ToolOutput,
    ToolSpec,
    run_tool,
    run_tool_async,
    short_paths,
    ui_uri,
    validate,
)
from race_engineer.tools.sessions import ToolInputError

log = logging.getLogger(__name__)

TELEMETRY_UI_URI = ui_uri("telemetry")
COMPARE_LAPS_UI_URI = ui_uri("compare-laps")
# Chart bundles always served: name (web/src/mcp-app/<name>.html) -> resource title. The specs'
# own charts are added to these (`ui_bundles`).
BASE_UI_BUNDLES = {"telemetry": "Telemetry chart", "compare-laps": "Lap comparison chart"}

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

# What the host tells the model about the server as a whole; each tool's description says the rest.
INSTRUCTIONS = (
    "F1 telemetry analysis on processed FastF1 sessions (qualifying, sprint and race, 2022 "
    "onwards) and on a deep-learning model's mistake scores. Call find_session first when the "
    "session is unclear ('last race', 'Monza quali', 'the Baku sprint') or a tool can't find the "
    "event, then pass the event, year and session it returns to the other tools; list_sessions "
    "lists every processed session. get_race_summary summarises a race or sprint (finishing "
    "order as timed, safety cars, pit stops, stints); find_mistakes lists the biggest driver "
    "mistakes in a session and explain_corner explains one corner on one lap in detail; "
    "compare_laps compares two drivers' laps; compare_driving_styles compares how two drivers "
    "take corners over a season. Use three-letter driver codes (LEC, HAM, VER). "
    "get_race_summary, find_mistakes, explain_corner, compare_laps and compare_driving_styles "
    "show interactive charts inside the conversation."
)

# Shown when the chart bundle hasn't been built yet (`make mcp-app`).
_MISSING_UI_HTML = """<!doctype html>
<html><body style="font-family: system-ui; padding: 16px">
<p><strong>Chart bundle not built.</strong> Run <code>make mcp-app</code> in the repo, then
restart the MCP server.</p>
</body></html>
"""


def ui_bundles(specs: Sequence[ToolSpec]) -> dict[str, str]:
    """Every chart bundle to serve as a resource: the base ones plus each spec's chart."""
    extra = {s.chart: s.title for s in specs if s.chart and s.chart not in BASE_UI_BUNDLES}
    return BASE_UI_BUNDLES | extra


UI_BUNDLES = ui_bundles(TOOLS)


def load_ui(name: str) -> str:
    """The single-file chart bundle built by Vite from `web/src/mcp-app/<name>.html`."""
    bundle = files("race_engineer.mcp_server").joinpath("ui", f"{name}.html")
    return bundle.read_text(encoding="utf-8") if bundle.is_file() else _MISSING_UI_HTML


def text_result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


def output_result(output: ToolOutput) -> CallToolResult:
    """A tool's summary as the text content, its data as `structuredContent`."""
    return CallToolResult(
        content=[TextContent(type="text", text=output.summary)],
        structured_content=output.data,
    )


def spec_result(
    spec: ToolSpec, arguments: dict[str, Any], paths: DataPaths | None = None
) -> CallToolResult:
    """Run a registry tool synchronously as the server would: the result, or the tool's
    message as an error result."""
    try:
        return output_result(run_tool(spec, arguments, paths))
    except ToolInputError as exc:
        return text_result(str(exc), is_error=True)


def sample_telemetry_result() -> CallToolResult:
    """Text for the model plus the full trace for the chart."""
    lap = build_sample_lap()
    slowest = min(range(len(lap.speed_kph)), key=lap.speed_kph.__getitem__)
    braking_zones = sum(1 for i, b in enumerate(lap.brake) if b and not lap.brake[i - 1])
    summary = (
        f"Sample lap (synthetic test data, {TRACK_LENGTH_M / 1000:.1f} km, not a real lap): "
        f"top speed {max(lap.speed_kph):.0f} km/h, slowest point {lap.speed_kph[slowest]:.0f} km/h "
        f"at {lap.distance_m[slowest] / 1000:.2f} km, {braking_zones} braking zones. "
        "The interactive chart shows speed, throttle and braking along the lap."
    )
    return CallToolResult(
        content=[TextContent(type="text", text=summary)],
        structured_content={
            "title": "Sample lap (synthetic)",
            "distance_m": lap.distance_m,
            "speed_kph": lap.speed_kph,
            "throttle_pct": lap.throttle_pct,
            "brake": lap.brake,
            "gear": lap.gear,
            "corners": [{"number": n, "distance_m": d} for n, d, _ in CORNERS],
        },
    )


# Thin wrappers over the registry, kept for scripts/export_sample_result.py and the tests.


def compare_laps_result(
    event: str,
    driver_a: str,
    driver_b: str,
    year: int | None = None,
    session: str | None = None,
    lap_a: int | None = None,
    lap_b: int | None = None,
    root: Path = PROCESSED_DIR,
) -> CallToolResult:
    """The summary for the model, the chart payload for the app, or a helpful error."""
    arguments = {"event": event, "driver_a": driver_a, "driver_b": driver_b, "year": year}
    arguments |= {"session": session, "lap_a": lap_a, "lap_b": lap_b}
    return spec_result(COMPARE_LAPS, arguments, DataPaths(root, RESULTS_DIR))


def find_mistakes_result(
    event: str,
    driver: str | None = None,
    year: int | None = None,
    session: str | None = None,
    limit: int = DEFAULT_LIMIT,
    root: Path = PROCESSED_DIR,
    results: Path = RESULTS_DIR,
) -> CallToolResult:
    """The ranked mistakes for the model, the same as data, or a helpful error."""
    arguments = {"event": event, "driver": driver, "year": year, "session": session}
    return spec_result(FIND_MISTAKES, arguments | {"limit": limit}, DataPaths(root, results))


def explain_corner_result(
    event: str,
    driver: str,
    lap: int,
    corner: int | str,
    year: int | None = None,
    session: str | None = None,
    root: Path = PROCESSED_DIR,
    results: Path = RESULTS_DIR,
) -> CallToolResult:
    """The corner explained for the model, the traces as data, or a helpful error."""
    arguments = {"event": event, "driver": driver, "lap": lap, "corner": corner}
    arguments |= {"year": year, "session": session}
    return spec_result(EXPLAIN_CORNER, arguments, DataPaths(root, results))


def tool_signature(params: type[BaseModel]) -> inspect.Signature:
    """Keyword-only parameters, one per field of `params` in order, annotated
    `Annotated[type, constraints..., Field(description)]`, so the MCP SDK derives the same
    input schema as `params` (tests/test_tools_registry.py checks the two schemas agree).
    RegistryServer checks the arguments with `params` itself before the SDK does."""
    parameters = []
    for name, info in params.model_fields.items():
        description = Field(description=info.description)
        annotation = Annotated[(info.annotation, *info.metadata, description)]
        default = inspect.Parameter.empty if info.is_required() else info.default
        parameters.append(
            inspect.Parameter(
                name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation
            )
        )
    return inspect.Signature(parameters, return_annotation=CallToolResult)


def tool_function(spec: ToolSpec, paths: DataPaths) -> Callable[..., Awaitable[CallToolResult]]:
    """The MCP tool function for a spec: its parameters are the spec's Params fields
    (`tool_signature`), and it answers with the summary and the chart data, the tool's message
    as an error result, or a generic error (logged) when the tool fails unexpectedly."""

    async def call(**arguments: Any) -> CallToolResult:
        try:
            output = await run_tool_async(spec, arguments, paths)
        except ToolInputError as exc:
            return text_result(str(exc), is_error=True)
        except Exception:
            log.exception("tool %s failed with arguments %s", spec.name, arguments)
            return text_result(
                f"Internal error in {spec.name}; the server log has details.", is_error=True
            )
        return output_result(output)

    call.__name__ = call.__qualname__ = spec.name
    call.__doc__ = spec.description
    call.__signature__ = tool_signature(spec.params)  # pyright: ignore[reportFunctionMemberAccess]
    return call


class RegistryServer(MCPServer[Any]):
    """The MCP server, checking a registry tool's arguments with the tool's own `Params` model
    before the SDK sees them.

    The SDK validates against the generated signature (`tool_signature`), which has the same
    fields and limits but quietly drops arguments it doesn't know and words its errors for a
    developer (pydantic's text with a docs link). Checking first means MCP refuses the same
    inputs as the REST API and the chat, with the same readable line: "Invalid arguments for
    explain_corner: lap: must be at least 1." or "unknown parameter(s) drivers (parameters:
    ...)". The listed schemas say so too (`additionalProperties: false`).

    Error results lose the data folders' absolute paths (`registry.short_paths`), as the REST
    API's errors do: a remote connector must not learn the server's file system."""

    def __init__(
        self,
        *args: Any,
        specs: Sequence[ToolSpec] = (),
        paths: DataPaths | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.specs = {spec.name: spec for spec in specs}
        self.paths = paths

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        return [
            t.model_copy(update={"input_schema": t.input_schema | {"additionalProperties": False}})
            if t.name in self.specs
            else t
            for t in tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        spec = self.specs.get(name)
        if spec is not None:
            try:
                validate(spec, arguments)
            except ToolInputError as exc:
                # Field names only, as the SDK logs a refusal: the values are the caller's data.
                log.info("tool %s refused arguments %s", name, sorted(arguments))
                return text_result(str(exc), is_error=True)
        result = await super().call_tool(name, arguments, context)
        if isinstance(result, CallToolResult) and result.is_error and self.paths is not None:
            result = self.shortened(result, self.paths)
        return result

    @staticmethod
    def shortened(result: CallToolResult, paths: DataPaths) -> CallToolResult:
        """An error result with the data folders' paths shortened in its text."""
        content = [
            block.model_copy(update={"text": short_paths(block.text, paths)})
            if isinstance(block, TextContent)
            else block
            for block in result.content
        ]
        return result.model_copy(update={"content": content})


def create_server(
    root: Path = PROCESSED_DIR, results: Path = RESULTS_DIR, specs: Sequence[ToolSpec] = TOOLS
) -> RegistryServer:
    """The server, reading the processed dataset under `root` and the M4 results under
    `results` (tests pass synthetic ones), with the registry tools in `specs`."""
    paths = DataPaths(root, results)
    apps = Apps()

    @apps.tool(
        resource_uri=TELEMETRY_UI_URI,
        name="show_sample_telemetry",
        title="Show sample telemetry",
        description=(
            "Show an interactive speed/throttle/brake chart for a synthetic sample lap. "
            "Test tool for the chart pipeline; the data is not from a real race."
        ),
        annotations=READ_ONLY,
    )
    def show_sample_telemetry() -> CallToolResult:
        return sample_telemetry_result()

    for spec in specs:
        if spec.chart:
            apps.tool(
                resource_uri=ui_uri(spec.chart),
                name=spec.name,
                title=spec.title,
                description=spec.description,
                annotations=READ_ONLY,
            )(tool_function(spec, paths))

    for name, title in ui_bundles(specs).items():
        apps.add_html_resource(ui_uri(name), load_ui(name), title=title, prefers_border=True)

    server = RegistryServer(
        "race-engineer",
        title="Race Engineer AI",
        version=__version__,
        instructions=INSTRUCTIONS,
        extensions=[apps],
        specs=specs,
        paths=paths,
    )

    for spec in specs:
        if not spec.chart:
            server.add_tool(
                tool_function(spec, paths),
                name=spec.name,
                title=spec.title,
                description=spec.description,
                annotations=READ_ONLY,
            )
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Race Engineer AI MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP only (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP only (default 8765)")
    args = parser.parse_args()

    server = create_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
