"""Check every MCP tool and chart on real data, as an MCP Inspector run would (plan 10.2).

Connects to the MCP server, either started here over stdio (`race-engineer-mcp`, as the Claude
desktop app runs it) or running already over streamable HTTP (the API's /mcp, or
`race-engineer-mcp --transport streamable-http`), and:

1. lists the tools: every registry tool is there, each chart tool with its bundle in
   `_meta.ui.resourceUri` and find_session and list_sessions with none, and the server's
   instructions name every tool;
2. reads every `ui://` resource and fails on the "Chart bundle not built" placeholder
   (run `make mcp-app`);
3. calls each tool with the example questions' arguments (plan appendix B): the most recent
   race, the 2023 Monaco summary, Leclerc's mistakes in Monza 2025 qualifying, explain_corner
   on the top row of a find_mistakes result, Piastri and Norris in Abu Dhabi 2025, Hamilton's
   style against Leclerc's (and Albon's against Leclerc's in 2024, the largest style payload of
   all 1,201 real pairs), the biggest mistakes in the last race and the Baku safety cars.
   Each result must not be an error, its summary must be under the 16,000-character cap, its
   structuredContent must have the keys the chart reads, and the chart data must be within the
   30 KiB payload budget (registry.PAYLOAD_MAX_BYTES; compare_laps is exempt).

It prints a table of tool, ok, seconds, summary characters and payload size, then the
problems. The time targets (explain_corner under 2 s cold, every other call under 1 s) are
shown; with --strict a slow call is a problem too. Exits 1 on any problem. Reads the data
under RACE_ENGINEER_DATA (data/ by default) and writes nothing.

    uv run python scripts/mcp_smoke.py                                   # stdio
    uv run python scripts/mcp_smoke.py --url http://127.0.0.1:8000/mcp   # e.g. `make api`
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.client import advertise
from mcp.client.stdio import StdioServerParameters
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
from mcp_types import CallToolResult, Tool

from race_engineer.tools.definitions import TOOLS
from race_engineer.tools.registry import PAYLOAD_MAX_BYTES, SUMMARY_MAX, ui_uri

APPS_SUPPORT = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})
PLACEHOLDER = "Chart bundle not built"
OVER_BUDGET = {"compare_laps"}  # two full-lap traces, 37-63 KB; predates the budget
SLOW_S = {"explain_corner": 2.0}  # seconds; every other tool 1 s
# The structuredContent keys each tool's chart (or caller) reads; None: text only.
KEYS: dict[str, set[str] | None] = {
    "find_session": {"match", "candidates", "weekend", "coverage"},
    "list_sessions": None,
    "get_race_summary": {"event", "year", "session_code", "laps", "drivers", "neutralisations"},
    "find_mistakes": {"event", "year", "session_code", "mistakes", "coverage", "track"},
    "explain_corner": {"distance_m", "lap", "usual", "delta_s", "phases", "map", "replay"},
    "compare_laps": {"drivers", "distance_m", "delta_s", "corners"},
    "compare_driving_styles": {"driver_a", "driver_b", "year", "metrics", "table", "map"},
    "show_sample_telemetry": {"distance_m", "speed_kph", "throttle_pct", "brake"},
}
# Keys that must not be null on real data (the maps exist for every processed session).
NOT_NULL = {"find_mistakes": {"track"}, "explain_corner": {"map"}}


@dataclass
class Call:
    tool: str
    arguments: dict[str, Any]
    ok: bool
    seconds: float
    summary: int
    payload: int | None
    note: str = ""


@dataclass
class Report:
    calls: list[Call] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def fail(self, what: str) -> None:
        self.problems.append(what)
        print(f"   FAIL {what}")


def size(data: object) -> int:
    """Bytes of compact JSON, as the server sends the data."""
    return len(json.dumps(data, separators=(",", ":"), allow_nan=False))


def first_text(result: CallToolResult) -> str:
    return getattr(result.content[0], "text", "") if result.content else ""


async def call(
    client: Client, tool: str, arguments: dict[str, Any], report: Report, *, strict: bool
) -> dict[str, Any] | None:
    """Call one tool, check its result and record the row; the structuredContent."""
    start = time.perf_counter()
    result = await client.call_tool(tool, arguments)
    seconds = time.perf_counter() - start
    text = first_text(result)
    data = result.structured_content
    payload = size(data) if data is not None else None
    problems = []
    if result.is_error:
        problems.append(f"error result: {text[:300]}")
    if len(text) >= SUMMARY_MAX:
        problems.append(f"summary of {len(text):,} characters (cap {SUMMARY_MAX:,})")
    expected = KEYS.get(tool)
    if not result.is_error and expected is None and data is not None:
        problems.append("structuredContent on a text-only tool")
    if not result.is_error and expected is not None:
        if data is None:
            problems.append("no structuredContent")
        else:
            if missing := expected - set(data):
                problems.append(f"structuredContent lacks {sorted(missing)}")
            if empty := {k for k in NOT_NULL.get(tool, set()) if data.get(k) is None}:
                problems.append(f"null {sorted(empty)}")
    if payload is not None and payload > PAYLOAD_MAX_BYTES and tool not in OVER_BUDGET:
        problems.append(f"payload {payload:,} bytes, over the {PAYLOAD_MAX_BYTES:,}-byte budget")
    slow = seconds > SLOW_S.get(tool, 1.0)
    if slow and strict:
        problems.append(f"{seconds:.2f} s, over the {SLOW_S.get(tool, 1.0):.0f} s target")
    note = "slow" if slow else ""
    if tool == "explain_corner" and data is not None and data.get("replay") is None:
        note = (note + ", no replay").lstrip(", ")
    row = Call(tool, arguments, not problems, seconds, len(text), payload, note)
    report.calls.append(row)
    for problem in problems:
        report.fail(f"{tool}({json.dumps(arguments)}): {problem}")
    return None if result.is_error else data


def resource_uri(tool: Tool) -> str | None:
    """The chart bundle a listed tool names in `_meta.ui`, or None."""
    return ((tool.meta or {}).get("ui") or {}).get("resourceUri")


async def check_listing(client: Client, report: Report) -> None:
    """The tools with their `_meta.ui`, the instructions and every chart bundle."""
    tools = {t.name: t for t in (await client.list_tools()).tools}
    print(f"{len(tools)} tools: {', '.join(tools)}")
    for spec in TOOLS:
        tool = tools.get(spec.name)
        if tool is None:
            report.fail(f"tool {spec.name} not listed")
            continue
        if (uri := resource_uri(tool)) != spec.resource_uri:
            report.fail(f"{spec.name}: _meta.ui.resourceUri {uri!r}, not {spec.resource_uri!r}")
    telemetry = tools.get("show_sample_telemetry")
    if telemetry is None or resource_uri(telemetry) != ui_uri("telemetry"):
        report.fail("show_sample_telemetry not listed with its chart")
    instructions = client.instructions or ""
    if missing := [s.name for s in TOOLS if s.name not in instructions]:
        report.fail(f"the server instructions don't name {missing}")

    expected = {ui_uri("telemetry")} | {s.resource_uri for s in TOOLS if s.resource_uri}
    listed = {str(r.uri) for r in (await client.list_resources()).resources}
    if missing := expected - listed:
        report.fail(f"chart resources not listed: {sorted(missing)}")
    for uri in sorted(expected & listed):
        content = (await client.read_resource(uri)).contents[0]
        html = getattr(content, "text", "")
        built = PLACEHOLDER not in html and "<html" in html.lower()
        print(f"   {uri}: {len(html) / 1024:.0f} KB, {content.mime_type}")
        if not built:
            report.fail(f"{uri} is the placeholder: run `make mcp-app` and restart the server")
        if content.mime_type != APP_MIME_TYPE:
            report.fail(f"{uri}: MIME type {content.mime_type}, expected {APP_MIME_TYPE}")


def top_row(found: dict[str, Any] | None) -> dict[str, Any] | None:
    """explain_corner's arguments for the first row of a find_mistakes result."""
    if not found or not found.get("mistakes"):
        return None
    row = found["mistakes"][0]
    session = {"event": found["event"], "year": found["year"], "session": found["session_code"]}
    return session | {"driver": row["driver"], "lap": row["lap_number"], "corner": row["turn"]}


async def run(client: Client, *, strict: bool) -> Report:
    report = Report()
    await check_listing(client, report)

    def ask(tool: str, arguments: dict[str, Any]):
        return call(client, tool, arguments, report, strict=strict)

    print("\ncalls:")
    latest = await ask("find_session", {"recent": 0, "session": "R"})  # 1: the last race
    await ask("list_sessions", {"year": 2025})
    await ask("get_race_summary", {"event": "monaco", "year": 2023})  # 2
    leclerc = {"event": "monza", "driver": "LEC", "year": 2025, "session": "Q"}
    found = await ask("find_mistakes", leclerc)  # 3
    match = (latest or {}).get("match")
    last_race = None
    if match:  # 7: the biggest mistakes in the last race
        arguments = {"event": match["event"], "year": match["year"], "session": "R"}
        last_race = await ask("find_mistakes", arguments)
    else:
        report.fail("find_session found no last race")
    # 4: the corner where he lost the most (Leclerc may have no mistake listed: then the last
    # race's top row)
    corner = top_row(found) or top_row(last_race)
    if corner is None:
        report.fail("no find_mistakes row to explain")
    else:
        await ask("explain_corner", corner)
    pair = {"event": "abu dhabi", "driver_a": "PIA", "driver_b": "NOR", "year": 2025}
    await ask("compare_laps", pair)  # 5
    await ask("compare_driving_styles", {"driver_a": "HAM", "driver_b": "LEC"})  # 6
    # The largest style payload on real data: drivers of different teams, a 24-event season.
    await ask("compare_driving_styles", {"driver_a": "ALB", "driver_b": "LEC", "year": 2024})
    await ask("get_race_summary", {"event": "baku", "session": "R"})  # 8
    await ask("show_sample_telemetry", {})
    return report


def print_table(report: Report) -> None:
    print(f"\n{'tool':<24} {'ok':<4} {'seconds':>7} {'summary':>8} {'payload':>9}  arguments")
    for c in report.calls:
        payload = f"{c.payload / 1024:.1f} KB" if c.payload is not None else "-"
        arguments = json.dumps(c.arguments, ensure_ascii=False)
        note = f"  ({c.note})" if c.note else ""
        print(
            f"{c.tool:<24} {'ok' if c.ok else 'FAIL':<4} {c.seconds:>7.2f} {c.summary:>8,} "
            f"{payload:>9}  {arguments}{note}"
        )


def stdio_server() -> StdioServerParameters:
    """`race-engineer-mcp` from this environment, with this process's data settings (the SDK
    passes a child only a few safe variables)."""
    script = Path(sys.executable).with_name("race-engineer-mcp")
    env = {k: v for k, v in os.environ.items() if k.startswith("RACE_ENGINEER_")}
    if script.exists():
        return StdioServerParameters(command=str(script), env=env)
    module = ["-m", "race_engineer.mcp_server.server"]  # the same entry point
    return StdioServerParameters(command=sys.executable, args=module, env=env)


async def main_async(url: str | None, strict: bool) -> int:
    server = url or stdio_server()
    print(f"MCP server: {url or 'race-engineer-mcp over stdio'}")
    start = time.perf_counter()
    async with Client(server, extensions=[APPS_SUPPORT]) as client:
        print(f"connected in {time.perf_counter() - start:.1f} s")
        report = await run(client, strict=strict)
    print_table(report)
    print(f"\n{len(report.problems)} problem(s)")
    for problem in report.problems:
        print(f"  - {problem}")
    return 1 if report.problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--stdio", action="store_true", help="start race-engineer-mcp (default)")
    where.add_argument("--url", help="a running server, e.g. http://127.0.0.1:8000/mcp")
    parser.add_argument("--strict", action="store_true", help="a call over its time target fails")
    args = parser.parse_args()
    return anyio.run(main_async, args.url, args.strict)


if __name__ == "__main__":
    sys.exit(main())
