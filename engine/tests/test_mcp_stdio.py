"""Launch the MCP server as a subprocess over stdio, the way the Claude desktop app does."""

import sys

import pytest
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.anyio


async def test_server_answers_over_stdio() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "race_engineer.mcp_server.server"]
    )
    async with Client(params) as client:
        names = [t.name for t in (await client.list_tools()).tools]
        assert {"show_sample_telemetry", "compare_laps", "list_sessions"} <= set(names)
        result = await client.call_tool("show_sample_telemetry", {})
        assert not result.is_error
