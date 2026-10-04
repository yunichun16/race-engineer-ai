"""The M0 spike: a tool whose result renders as a chart inside Claude (MCP Apps)."""

import pytest
from mcp import Client
from mcp.client import advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID

from race_engineer.mcp_server.server import TELEMETRY_UI_URI, create_server

APPS_SUPPORT = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})

pytestmark = pytest.mark.anyio


async def test_tool_is_bound_to_the_chart_resource() -> None:
    async with Client(create_server(), extensions=[APPS_SUPPORT]) as client:
        tools = (await client.list_tools()).tools
        tool = next(t for t in tools if t.name == "show_sample_telemetry")
        assert tool.meta is not None
        assert tool.meta["ui"]["resourceUri"] == TELEMETRY_UI_URI


async def test_chart_resource_is_served_as_an_mcp_app() -> None:
    async with Client(create_server(), extensions=[APPS_SUPPORT]) as client:
        result = await client.read_resource(TELEMETRY_UI_URI)
        content = result.contents[0]
        assert content.mime_type == APP_MIME_TYPE
        assert "<html" in getattr(content, "text", "").lower()


async def test_tool_returns_text_for_the_model_and_data_for_the_chart() -> None:
    async with Client(create_server(), extensions=[APPS_SUPPORT]) as client:
        result = await client.call_tool("show_sample_telemetry", {})
        assert not result.is_error
        assert "synthetic" in getattr(result.content[0], "text", "")
        data = result.structured_content
        assert data is not None
        n = len(data["distance_m"])
        assert n == len(data["speed_kph"]) == len(data["throttle_pct"]) == len(data["brake"])
        assert [c["number"] for c in data["corners"]] == list(range(1, 8))
