"""The MCP server mounted in the API at /mcp, reached by an MCP client over streamable HTTP
through the app itself (httpx2.ASGITransport, no network): the same tools, chart bindings and
`ui://` resources as over stdio.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client import advertise
from mcp.client.streamable_http import streamable_http_client
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID

from race_engineer.api.app import MCP_PATH, create_app
from race_engineer.api.settings import Settings
from race_engineer.tools.definitions import TOOLS
from race_engineer.tools.registry import ui_uri

APPS_SUPPORT = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    from test_tools_mistakes import mistakes_dataset

    return mistakes_dataset(tmp_path_factory.mktemp("mcp-mount"))


def make_app(dataset: tuple[Path, Path], **changes) -> FastAPI:
    root, results = dataset
    values = {"chat": "off", "mcp_host_check": False} | changes
    return create_app(Settings(processed=root, results=results, **values))


@asynccontextmanager
async def mcp_client(app: FastAPI) -> AsyncIterator[Client]:
    """An MCP client talking to the app's /mcp, with the app's lifespan running."""
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as http,
        Client(
            streamable_http_client(f"http://testserver{MCP_PATH}", http_client=http),
            extensions=[APPS_SUPPORT],
        ) as client,
    ):
        yield client


async def test_mounted_server_lists_the_tools(dataset: tuple[Path, Path]) -> None:
    async with mcp_client(make_app(dataset)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"show_sample_telemetry", *(s.name for s in TOOLS)}
        for spec in TOOLS:
            meta = tools[spec.name].meta
            if spec.chart:
                assert meta is not None and meta["ui"]["resourceUri"] == ui_uri(spec.chart)
            else:
                assert meta is None
            assert tools[spec.name].description == spec.description

        resource = (await client.read_resource(ui_uri("compare-laps"))).contents[0]
        assert resource.mime_type == APP_MIME_TYPE
        assert "<html" in getattr(resource, "text", "").lower()  # the bundle, or its placeholder


async def test_mounted_server_calls_the_tools(dataset: tuple[Path, Path]) -> None:
    async with mcp_client(make_app(dataset)) as client:
        result = await client.call_tool("find_mistakes", {"event": "sample", "limit": 2})
        assert not result.is_error
        text = getattr(result.content[0], "text", "")
        assert "1. AAA [track limits / off track (race control)" in text
        data = result.structured_content
        assert data is not None and len(data["mistakes"]) == 2
        bad = await client.call_tool("find_mistakes", {"event": "sample", "driver": "ZZZ"})
        assert bad.is_error and bad.structured_content is None


async def test_the_lifespan_can_run_again(dataset: tuple[Path, Path]) -> None:
    # Each start builds a new session manager (one can run only once).
    app = make_app(dataset)
    for _ in range(2):
        async with mcp_client(app) as client:
            assert len((await client.list_tools()).tools) == len(TOOLS) + 1


def test_mcp_answers_503_before_startup(dataset: tuple[Path, Path]) -> None:
    client = TestClient(make_app(dataset))  # no `with`: the lifespan doesn't run
    response = client.post(MCP_PATH, json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"


def test_only_localhost_hosts_by_default(dataset: tuple[Path, Path]) -> None:
    # Bound to 127.0.0.1, the MCP SDK's DNS-rebinding protection refuses other Host headers.
    with TestClient(make_app(dataset, mcp_host_check=True)) as client:
        refused = client.post(MCP_PATH, json=INITIALIZE, headers=MCP_HEADERS)
        assert refused.status_code == 421
        local = MCP_HEADERS | {"Host": "127.0.0.1:8000"}
        allowed = client.post(MCP_PATH, json=INITIALIZE, headers=local)
        assert allowed.status_code == 200
        assert '"serverInfo"' in allowed.text and "race-engineer" in allowed.text
        foreign = local | {"Origin": "https://example.com"}
        assert client.post(MCP_PATH, json=INITIALIZE, headers=foreign).status_code == 403


def test_mcp_can_be_left_out(dataset: tuple[Path, Path]) -> None:
    with TestClient(make_app(dataset, mount_mcp=False)) as client:
        assert client.post(MCP_PATH, json=INITIALIZE, headers=MCP_HEADERS).status_code == 404
        mcp = client.get("/api/health").json()["mcp"]
        assert mcp["mounted"] is False


# Stateless /mcp (RACE_ENGINEER_MCP_STATELESS=1, the deployment): every request stands alone,
# with no session id, and is answered with one JSON response.


def rpc(method: str, params: dict | None = None, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def stateless_post(client: TestClient, body: dict) -> dict:
    headers = MCP_HEADERS | {"mcp-protocol-version": "2025-06-18"}
    response = client.post(MCP_PATH, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in response.headers
    return response.json()


def test_stateless_requests_each_stand_alone(dataset: tuple[Path, Path]) -> None:
    app = make_app(dataset, mcp_stateless=True, host="0.0.0.0")
    with TestClient(app) as client:
        response = client.post(MCP_PATH, json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 200 and "mcp-session-id" not in response.headers
        assert response.json()["result"]["serverInfo"]["name"] == "race-engineer"

        # No initialize first and no session: each request on its own.
        listed = stateless_post(client, rpc("tools/list"))["result"]["tools"]
        tools = {t["name"]: t for t in listed}
        assert len(tools) == 8 and set(tools) == {"show_sample_telemetry", *(s.name for s in TOOLS)}
        assert tools["find_mistakes"]["_meta"]["ui"]["resourceUri"] == ui_uri("find-mistakes")

        call = rpc(
            "tools/call", {"name": "find_mistakes", "arguments": {"event": "sample", "limit": 2}}
        )
        result = stateless_post(client, call)["result"]
        assert not result.get("isError") and len(result["structuredContent"]["mistakes"]) == 2

        read = rpc("resources/read", {"uri": ui_uri("find-mistakes")})
        contents = stateless_post(client, read)["result"]["contents"][0]
        assert contents["mimeType"] == APP_MIME_TYPE and "<html" in contents["text"].lower()

        health = client.get("/api/health").json()["mcp"]
        assert health == {"mounted": True, "path": MCP_PATH, "tools": 8}


async def test_a_stateless_client_works_too(dataset: tuple[Path, Path]) -> None:
    async with mcp_client(make_app(dataset, mcp_stateless=True)) as client:
        assert len((await client.list_tools()).tools) == len(TOOLS) + 1
        result = await client.call_tool("find_mistakes", {"event": "sample", "limit": 1})
        assert not result.is_error and result.structured_content is not None


def test_mcp_bodies_are_capped(dataset: tuple[Path, Path]) -> None:
    app = make_app(dataset, mcp_stateless=True, mcp_max_body=1024)
    with TestClient(app) as client:
        big = rpc("tools/list") | {"padding": "x" * 2048}
        assert client.post(MCP_PATH, json=big, headers=MCP_HEADERS).status_code == 413
