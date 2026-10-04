"""The REST API on synthetic data: health, the tool catalog, one endpoint per tool, error mapping,
CORS, ETags, settings and the chat's startup modes (no network, no API key).

The MCP server mounted at /mcp is tested in tests/test_api_mcp_mount.py.
"""

import json
import logging
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from race_engineer.api import app as app_module
from race_engineer.api.app import create_app
from race_engineer.api.settings import Settings
from race_engineer.inference import provenance
from race_engineer.tools.definitions import TOOLS
from race_engineer.tools.params import ListSessionsParams
from race_engineer.tools.registry import PAYLOAD_MAX_BYTES, DataPaths, ToolOutput, ToolSpec
from race_engineer.tools.sessions import ResultsUnavailableError
from race_engineer.tools.synthetic_style import write_style_results

# Arguments that every tool answers on the synthetic dataset (tests/test_tools_mistakes.py, plus
# the synthetic style tables). A tool added to TOOLS needs an entry here
# (test_every_tool_has_an_example).
EXAMPLES: dict[str, dict[str, str | int]] = {
    "find_session": {"event": "sample"},
    "list_sessions": {},
    "get_race_summary": {"event": "sample"},
    "find_mistakes": {"event": "sample", "limit": 2},
    "explain_corner": {"event": "sample", "driver": "AAA", "lap": 10, "corner": 3},
    "compare_laps": {"event": "sample", "driver_a": "AAA", "driver_b": "BBB"},
    "compare_driving_styles": {"driver_a": "AAA", "driver_b": "BBB"},
}
# Tools whose data may pass the payload budget (registry.PAYLOAD_MAX_BYTES): compare_laps' two
# full-lap traces predate it.
OVER_BUDGET = {"compare_laps"}

LOCAL = "http://localhost:3000"


def settings_for(root: Path, results: Path, **changes) -> Settings:
    """Settings reading synthetic data, chat off, /mcp reachable from the test client."""
    values = {"chat": "off", "mcp_host_check": False} | changes
    return Settings(processed=root, results=results, **values)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    from test_tools_mistakes import RUN_ID, mistakes_dataset

    root, results = mistakes_dataset(tmp_path_factory.mktemp("api"))
    # The style tables, recorded as built from the same scoring run (the manifest keeps it), so
    # the data is complete, health is ok and compare_driving_styles answers.
    write_style_results(results, RUN_ID)
    assert provenance.scoring_run(results) == RUN_ID
    return root, results


@pytest.fixture(scope="module")
def client(dataset: tuple[Path, Path]) -> Iterator[TestClient]:
    with TestClient(create_app(settings_for(*dataset))) as client:
        yield client


def error(response) -> dict:
    return response.json()["error"]


# Health


def test_health_ok(client: TestClient, dataset: tuple[Path, Path]) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["problems"] == []
    data = body["data"]
    assert data["sessions"] == 2 and data["latest"] == "2026 Sample Grand Prix Race"
    _, results = dataset
    assert data["results_current"] and data["results_run"] == provenance.scoring_run(results)
    assert data["mistakes"] == len(pd.read_parquet(results / "mistakes.parquet"))
    assert data["style"] and not data["circuits"]
    assert body["chat"]["mode"] == "off"
    assert body["mcp"] == {"mounted": True, "path": "/mcp", "tools": len(TOOLS) + 1}
    assert {"catalog", "results"} <= set(body["warm_s"])


def test_health_degraded_without_data(tmp_path: Path) -> None:
    root, results = tmp_path / "processed", tmp_path / "results"
    with TestClient(create_app(settings_for(root, results))) as client:
        response = client.get("/api/health")
    assert response.status_code == 200  # always: the reasons are in the body
    body = response.json()
    assert body["status"] == "degraded"
    assert body["data"]["sessions"] == 0 and body["data"]["latest"] is None
    assert not body["data"]["results_current"] and body["data"]["mistakes"] is None
    problems = " | ".join(body["problems"])
    assert "no processed sessions" in problems
    assert "results stale: No current scoring run in the results folder" in problems
    assert "style tables not built" in problems
    assert str(tmp_path) not in response.text  # no absolute paths


def test_health_answers_when_a_session_file_cant_be_read(tmp_path: Path) -> None:
    # A file half-written by `make data` neither stops startup nor breaks the health check.
    sessions = tmp_path / "processed" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "2026_01_R.parquet").write_bytes(b"not parquet")
    with TestClient(create_app(settings_for(tmp_path / "processed", tmp_path / "results"))) as c:
        response = c.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded" and body["data"]["sessions"] == 0
    assert any(p.startswith("processed sessions can't be read") for p in body["problems"])
    assert str(tmp_path) not in response.text


def test_health_reports_stale_results(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    from test_tools_mistakes import copy_results

    root, results = dataset
    older = copy_results(results, tmp_path / "results", run_id="2026-01-01T00:00:00+00:00/old")
    with TestClient(create_app(settings_for(root, older))) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "degraded" and not body["data"]["results_current"]
    assert any("mistakes.parquet was built from another scoring run" in p for p in body["problems"])
    assert str(tmp_path) not in str(body)


# The tools


def test_tool_catalog(client: TestClient) -> None:
    catalog = client.get("/api/tools").json()
    assert [t["name"] for t in catalog] == [s.name for s in TOOLS]  # the chat's fixed order
    for info, spec in zip(catalog, TOOLS, strict=True):
        assert info["title"] == spec.title and info["description"] == spec.description
        assert info["input_schema"] == spec.input_schema()
        if spec.chart:
            assert info["chart"] == {"bundle": spec.chart, "resource_uri": spec.resource_uri}
        else:
            assert info["chart"] is None


def test_every_tool_has_an_example() -> None:
    assert {s.name for s in TOOLS} <= set(EXAMPLES)


@pytest.mark.parametrize("spec", TOOLS, ids=lambda s: s.name)
def test_every_tool_answers(client: TestClient, spec: ToolSpec) -> None:
    response = client.get(f"/api/tools/{spec.name}", params=EXAMPLES[spec.name])
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"tool", "summary", "data", "chart"}
    assert body["tool"] == spec.name and body["summary"]
    if spec.chart:
        assert body["chart"]["bundle"] == spec.chart
        assert body["chart"]["resource_uri"] == f"ui://race-engineer/{spec.chart}.html"
        assert body["data"]
    else:
        assert body["chart"] is None
    if spec.name not in OVER_BUDGET:
        size = len(json.dumps(body["data"], separators=(",", ":"), allow_nan=False))
        assert size < PAYLOAD_MAX_BYTES
    assert response.headers["etag"].startswith('"')
    assert response.headers["cache-control"] == "private, max-age=60"


def test_tool_results(client: TestClient) -> None:
    found = client.get("/api/tools/find_mistakes", params=EXAMPLES["find_mistakes"]).json()
    assert "1. AAA [track limits / off track (race control)" in found["summary"]
    assert len(found["data"]["mistakes"]) == 2 and found["data"]["track"]["outline"]
    corner = client.get("/api/tools/explain_corner", params=EXAMPLES["explain_corner"]).json()
    assert corner["data"]["type"] == "early_braking"
    assert len(corner["data"]["lap"]["speed"]) == 80
    assert corner["data"]["map"]["window"] and corner["data"]["replay"]["me"]
    session = client.get("/api/tools/find_session", params=EXAMPLES["find_session"]).json()
    assert session["data"]["match"]["key"] == "2026_01_Q" and session["data"]["match"]["scored"]
    race = client.get("/api/tools/get_race_summary", params=EXAMPLES["get_race_summary"]).json()
    assert race["data"]["session_code"] == "R" and race["summary"].startswith("2026 Sample")
    styles = client.get(
        "/api/tools/compare_driving_styles", params=EXAMPLES["compare_driving_styles"]
    ).json()
    assert styles["data"]["driver_a"] == "AAA" and styles["data"]["teammates"]
    # A turn given as text works as over MCP.
    text = client.get(
        "/api/tools/explain_corner", params=EXAMPLES["explain_corner"] | {"corner": "T3"}
    )
    assert text.status_code == 200 and text.json()["data"]["type"] == "early_braking"


def test_a_matching_etag_gets_304(client: TestClient) -> None:
    params = EXAMPLES["find_mistakes"]
    first = client.get("/api/tools/find_mistakes", params=params)
    tag = first.headers["etag"]
    again = client.get("/api/tools/find_mistakes", params=params, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.headers["etag"] == tag and not again.content
    weak = client.get(
        "/api/tools/find_mistakes", params=params, headers={"If-None-Match": f"W/{tag}"}
    )
    assert weak.status_code == 304
    other = client.get(
        "/api/tools/find_mistakes", params=params | {"limit": 3}, headers={"If-None-Match": tag}
    )
    assert other.status_code == 200 and other.headers["etag"] != tag


def test_the_etag_follows_the_data(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    root, results = dataset
    copy = tmp_path / "results"
    shutil.copytree(results, copy)
    params = EXAMPLES["find_mistakes"]
    with TestClient(create_app(settings_for(root, copy))) as client:
        before = client.get("/api/tools/find_mistakes", params=params).headers["etag"]
        assert client.get("/api/tools/find_mistakes", params=params).headers["etag"] == before
        rebuilt = copy / "mistakes.parquet"  # as `make m4` would leave it: same run, new file
        mtime = rebuilt.stat().st_mtime_ns + 1_000_000_000
        os.utime(rebuilt, ns=(mtime, mtime))
        after = client.get("/api/tools/find_mistakes", params=params)
    assert after.status_code == 200 and after.headers["etag"] != before


# Errors


def test_invalid_request(client: TestClient) -> None:
    params = EXAMPLES["explain_corner"] | {"lap": 0, "zzz": 1}
    response = client.get("/api/tools/explain_corner", params=params)
    assert response.status_code == 422
    body = error(response)
    assert body["code"] == "invalid_request" and body["tool"] == "explain_corner"
    # Worded as the MCP server and the chat word it (registry.describe_errors).
    assert body["message"] == (
        "Invalid request: lap: must be at least 1; unknown parameter(s) zzz (parameters: "
        "event, driver, lap, corner, year, session)."
    )
    missing = error(client.get("/api/tools/explain_corner", params={"event": "sample"}))
    assert missing["code"] == "invalid_request"
    assert missing["message"] == (
        "Invalid request: driver: required; lap: required; corner: required."
    )


def test_a_parameter_called_query_is_named(client: TestClient) -> None:
    """FastAPI puts "query" (where the value came from) before each parameter's name; only that
    is dropped, so find_session's old free-text `query` argument is named like any other."""
    stale = {"query": "last race", "recent": -1}
    body = error(client.get("/api/tools/find_session", params=stale))
    assert body["message"] == (
        "Invalid request: recent: must be at least 0; unknown parameter(s) query (parameters: "
        "event, year, session, round, recent)."
    )
    flag = error(client.get("/api/tools/find_session", params={"recent": "true"}))
    assert flag["message"] == "Invalid request: recent: must be a whole number."


def test_invalid_input(client: TestClient) -> None:
    response = client.get("/api/tools/find_mistakes", params={"event": "sample", "driver": "ZZZ"})
    assert response.status_code == 422
    body = error(response)
    assert body["code"] == "invalid_input" and body["tool"] == "find_mistakes"
    assert body["message"].startswith("No driver 'ZZZ' in the 2026 Sample Grand Prix Qualifying.")
    nowhere = error(
        client.get(
            "/api/tools/compare_laps", params=EXAMPLES["compare_laps"] | {"event": "nowhere"}
        )
    )
    assert nowhere["code"] == "invalid_input" and "No processed event" in nowhere["message"]


def test_unknown_tool(client: TestClient) -> None:
    response = client.get("/api/tools/nope")
    assert response.status_code == 404
    body = error(response)
    assert body["code"] == "unknown_tool" and body["tool"] == "nope"
    assert all(spec.name in body["message"] for spec in TOOLS)


def test_other_http_errors_have_the_same_shape(client: TestClient) -> None:
    assert error(client.get("/api/nothing")) == {
        "code": "not_found",
        "message": "Not Found",
        "tool": None,
    }
    wrong_method = client.post("/api/tools/find_mistakes")
    assert wrong_method.status_code == 405 and error(wrong_method)["code"] == "method_not_allowed"


def failing_spec(exc: Exception) -> ToolSpec:
    def run(p: ListSessionsParams, paths: DataPaths) -> ToolOutput:
        raise exc

    return ToolSpec("failing", "Failing", "Fails.", ListSessionsParams, run)


@pytest.mark.parametrize("error_class", [provenance.StaleResultsError, ResultsUnavailableError])
def test_results_unavailable_is_503(
    dataset: tuple[Path, Path], error_class: type[Exception]
) -> None:
    root, results = dataset
    exc = error_class(f"mistakes.parquet not found in {results}: run `make m4`.")
    app = create_app(settings_for(root, results), specs=[failing_spec(exc)])
    with TestClient(app) as client:
        response = client.get("/api/tools/failing")
    assert response.status_code == 503
    assert error(response) == {
        "code": "results_unavailable",
        "message": "mistakes.parquet not found in data/results: run `make m4`.",
        "tool": "failing",
    }


def test_stale_results_are_503(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    from test_tools_mistakes import copy_results

    root, results = dataset
    older = copy_results(results, tmp_path / "results", run_id="2026-01-01T00:00:00+00:00/old")
    with TestClient(create_app(settings_for(root, older))) as client:
        response = client.get("/api/tools/find_mistakes", params={"event": "sample"})
    assert error(response)["code"] == "results_unavailable"
    assert response.status_code == 503


def test_stale_results_message(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    from test_tools_mistakes import copy_results

    root, results = dataset
    older = copy_results(results, tmp_path / "results", run_id="2026-01-01T00:00:00+00:00/old")
    with TestClient(create_app(settings_for(root, older))) as client:
        response = client.get("/api/tools/find_mistakes", params={"event": "sample"})
        corner = client.get("/api/tools/explain_corner", params=EXAMPLES["explain_corner"])
    assert response.status_code == 503
    message = error(response)["message"]
    assert "mistakes.parquet was built from another scoring run" in message
    assert "`race-engineer-infer explain`" in message
    # explain_corner still answers from the processed session, without a verdict.
    assert corner.status_code == 200 and corner.json()["data"]["unscored"]


def test_an_unexpected_failure_is_a_generic_500(
    dataset: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings_for(*dataset), specs=[failing_spec(RuntimeError("the secret"))])
    with (
        caplog.at_level(logging.ERROR, logger="race_engineer.api.errors"),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/api/tools/failing", headers={"Origin": LOCAL})
    assert response.status_code == 500
    body = error(response)
    assert body["code"] == "internal_error" and body["tool"] == "failing"
    assert "the secret" not in response.text
    request_id = body["message"].split("request ")[1].rstrip(").")
    assert request_id in caplog.text and "the secret" in caplog.text
    # The browser on the dev server can read it: the 500 carries the CORS headers.
    assert response.headers["access-control-allow-origin"] == LOCAL


# CORS and docs


def test_cors_allows_the_dev_server(client: TestClient) -> None:
    preflight = {"Origin": LOCAL, "Access-Control-Request-Method": "GET"}
    allowed = client.options("/api/tools/list_sessions", headers=preflight)
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == LOCAL
    chat = {"Origin": LOCAL, "Access-Control-Request-Method": "POST"}
    chat |= {"Access-Control-Request-Headers": "content-type"}
    assert client.options("/api/chat", headers=chat).status_code == 200
    simple = client.get("/api/health", headers={"Origin": LOCAL})
    assert simple.headers["access-control-allow-origin"] == LOCAL


def test_cors_refuses_other_origins(client: TestClient) -> None:
    preflight = {"Origin": "https://example.com", "Access-Control-Request-Method": "GET"}
    refused = client.options("/api/tools/list_sessions", headers=preflight)
    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers
    simple = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in simple.headers


def test_docs_list_every_route(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    expected = {f"/api/tools/{s.name}" for s in TOOLS} | {"/api/tools", "/api/health", "/api/chat"}
    assert expected <= paths
    assert "/api/tools/{name}" not in paths and "/mcp" not in paths
    assert client.get("/docs").status_code == 200
    explain = client.get("/openapi.json").json()["paths"]["/api/tools/explain_corner"]["get"]
    expected_order = [*EXAMPLES["explain_corner"], "year", "session"]
    assert [p["name"] for p in explain["parameters"]] == expected_order


# Settings


def test_settings_defaults() -> None:
    settings = Settings.from_env({})
    assert settings.chat == "auto" and settings.chat_model == "claude-sonnet-5-5"
    assert settings.cors_origins == (LOCAL, "http://127.0.0.1:3000")
    assert settings.mount_mcp and settings.tool_concurrency == 2
    assert settings.chat_max_turns == 8 and settings.chat_max_iterations == 6
    assert settings.chat_fallbacks and settings.chat_eager
    assert len(settings.chat_secret) == 32 and "chat_secret" not in repr(settings)
    assert Settings.from_env({}).chat_secret != settings.chat_secret  # random per process


def test_settings_from_env() -> None:
    env = {
        "RACE_ENGINEER_CORS_ORIGINS": "https://a.example/, https://b.example",
        "RACE_ENGINEER_MOUNT_MCP": "0",
        "RACE_ENGINEER_TOOL_CONCURRENCY": "3",
        "RACE_ENGINEER_CHAT_FAKE": "1",
        "RACE_ENGINEER_CHAT_SECRET": "s3cret",
        "RACE_ENGINEER_CHAT_MAX_TURNS": "2",
        "RACE_ENGINEER_HOST": "0.0.0.0",
    }
    settings = Settings.from_env(env)
    assert settings.cors_origins == ("https://a.example", "https://b.example")
    assert not settings.mount_mcp and settings.tool_concurrency == 3
    assert settings.chat == "fake" and settings.chat_secret == b"s3cret"
    assert settings.chat_max_turns == 2 and settings.host == "0.0.0.0"


def test_a_third_party_base_url_turns_off_api_only_features() -> None:
    gateway = {"ANTHROPIC_BASE_URL": "https://gateway.example/v1?token=x"}
    settings = Settings.from_env(gateway)
    assert settings.base_url_host == "gateway.example" and settings.third_party_base_url
    assert not settings.chat_fallbacks and not settings.chat_eager
    forced = Settings.from_env(gateway | {"RACE_ENGINEER_CHAT_FALLBACKS": "1"})
    assert forced.chat_fallbacks and not forced.chat_eager
    direct = Settings.from_env({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"})
    assert direct.chat_fallbacks and not direct.third_party_base_url


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RACE_ENGINEER_CHAT", "maybe"),
        ("RACE_ENGINEER_MOUNT_MCP", "sometimes"),
        ("RACE_ENGINEER_TOOL_CONCURRENCY", "0"),
        ("RACE_ENGINEER_CHAT_CONCURRENCY", "four"),
    ],
)
def test_bad_settings_name_the_variable(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        Settings.from_env({name: value})


# The chat's startup modes (the chat itself is tested in tests/test_chat_*.py)


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No Anthropic key, token, profile or federation in the environment."""
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID",
        "ANTHROPIC_IDENTITY_TOKEN",
        "ANTHROPIC_IDENTITY_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ANTHROPIC_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.config/anthropic profile either


def chat_health(dataset: tuple[Path, Path], **kwargs) -> dict:
    settings = settings_for(*dataset, chat=kwargs.pop("chat", "auto"))
    with TestClient(create_app(settings, **kwargs)) as client:
        return client.get("/api/health").json()


@pytest.mark.usefixtures("no_credentials")
def test_chat_is_off_without_credentials(dataset: tuple[Path, Path]) -> None:
    body = chat_health(dataset)
    assert body["chat"] == {
        "mode": "off",
        "model": "claude-sonnet-5-5",
        "base_url_host": "api.anthropic.com",
        "available": False,
        "reason": "off",
    }
    assert body["status"] == "ok"  # a chat that isn't configured isn't a fault


@pytest.mark.usefixtures("no_credentials")
def test_chat_is_on_with_a_key(
    dataset: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    with caplog.at_level(logging.INFO, logger="race_engineer.api.app"):
        body = chat_health(dataset)
    assert body["chat"]["mode"] == "anthropic" and body["status"] == "ok"
    assert "sk-ant-test" not in caplog.text  # credentials are never logged
    assert "chat: anthropic, model claude-sonnet-5-5, via api.anthropic.com" in caplog.text


@pytest.mark.usefixtures("no_credentials")
def test_a_gateway_is_warned_about(
    dataset: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    with caplog.at_level(logging.WARNING, logger="race_engineer.api.app"):
        body = chat_health(dataset)
    assert body["chat"]["base_url_host"] == "gateway.example"
    assert "requests go to gateway.example, not the Claude API" in caplog.text


@pytest.mark.usefixtures("no_credentials")
def test_a_profile_that_cant_be_read_is_reported(
    dataset: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_PROFILE", "missing")  # asked for, but not on disk
    body = chat_health(dataset)
    assert body["chat"]["mode"] == "off" and body["status"] == "degraded"
    assert body["problems"] == ["chat failed to start (CredentialsError); see the server log"]


def test_a_client_passed_in_is_used(dataset: tuple[Path, Path]) -> None:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key="test", base_url="http://scripted.invalid")
    body = chat_health(dataset, chat="fake", anthropic_client=client)
    assert body["chat"] == {
        "mode": "fake",
        "model": "scripted",
        "base_url_host": "scripted.invalid",
        "available": True,
        "reason": None,
    }


def test_the_fake_chat_answers_through_the_app(dataset: tuple[Path, Path]) -> None:
    # The lifespan builds the scripted client itself (RACE_ENGINEER_CHAT=fake, `make api-fake`)
    # and the chat route answers with it.
    with TestClient(create_app(settings_for(*dataset, chat="fake"))) as client:
        assert client.get("/api/health").json()["chat"]["mode"] == "fake"
        question = {"message": "Where did AAA make mistakes in Sample 2026 qualifying?"}
        response = client.post("/api/chat", json=question)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: tool_call" in response.text and "event: done" in response.text


def test_a_chat_that_fails_to_start_is_reported(
    dataset: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(settings: Settings) -> None:
        raise RuntimeError("no luck")

    monkeypatch.setattr(app_module, "_build_client", broken)
    body = chat_health(dataset)
    assert body["chat"]["mode"] == "off" and body["status"] == "degraded"
    assert body["problems"] == ["chat failed to start (RuntimeError); see the server log"]
