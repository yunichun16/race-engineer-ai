"""The site's catalog routes (api/routes/catalog.py) on synthetic data: the processed sessions,
a session's drivers and a style season's pairs, their errors, ETags and CORS, and that they are
not tools (the MCP tool list and the chat's prompt version don't change).

The data is the REST tests' (tests/test_api_tools.py): the 2026 Sample Grand Prix (qualifying
and race, both scored) and the synthetic style tables (2024 and 2025), plus two 2025 weekends
written here, neither scored: the Lakeside Grand Prix (round 1, qualifying and race) and the
Harbour Grand Prix (round 3, a sprint weekend: sprint qualifying, sprint, qualifying, race).
"""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import anyio
import numpy as np
import pytest
from fastapi.testclient import TestClient

from race_engineer.api.app import create_app
from race_engineer.api.chat.fake import ScriptedClaude, scripted_client
from race_engineer.api.chat.service import ChatConfig, ChatService, service_for
from race_engineer.api.settings import Settings
from race_engineer.inference import provenance
from race_engineer.tools.definitions import TOOLS
from race_engineer.tools.synthetic import (
    SyntheticLap,
    SyntheticSession,
    grid_distance,
    write_session,
)
from race_engineer.tools.synthetic_style import write_style_results

LOCAL = "http://localhost:3000"
SESSIONS, DRIVERS, STYLES = "/api/catalog/sessions", "/api/catalog/drivers", "/api/catalog/styles"


def settings_for(root: Path, results: Path, **changes) -> Settings:
    values = {"chat": "off", "mcp_host_check": False} | changes
    return Settings(processed=root, results=results, **values)


def laps(driver: str, team: str, count: int, first: int = 1) -> tuple[SyntheticLap, ...]:
    speed = np.full(len(grid_distance(5000.0)), 180.0)
    return tuple(SyntheticLap(driver, n, speed, team=team) for n in range(first, first + count))


def weekends_2025() -> list[SyntheticSession]:
    """Two unscored 2025 weekends. The sprint weekend's files sort Q, R, S, SQ by name; by date
    the sessions ran SQ, S, Q, R. In its sprint EEE has two laps for Green and one for Blue."""
    lakeside = [("Q", "2025-03-15 15:00"), ("R", "2025-03-16 15:00")]
    harbour = [
        ("SQ", "2025-04-18 15:30"),
        ("S", "2025-04-19 11:00"),
        ("Q", "2025-04-19 15:00"),
        ("R", "2025-04-20 15:00"),
    ]
    field = (*laps("AAA", "Red", 3), *laps("BBB", "Red", 2), *laps("CCC", "Blue", 1))
    sessions = [
        SyntheticSession(2025, 1, code, "Lakeside Grand Prix", "Lakeside", date, laps=field)
        for code, date in lakeside
    ]
    for code, date in harbour:
        drivers = field
        if code == "S":
            drivers += (*laps("EEE", "Green", 2), *laps("EEE", "Blue", 1, first=3))
        sessions.append(
            SyntheticSession(2025, 3, code, "Harbour Grand Prix", "Harbour", date, laps=drivers)
        )
    return sessions


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    from test_tools_mistakes import RUN_ID, mistakes_dataset

    root, results = mistakes_dataset(tmp_path_factory.mktemp("catalog"))
    write_style_results(results, RUN_ID)
    for session in weekends_2025():
        write_session(session, root)
    return root, results


@pytest.fixture(scope="module")
def client(dataset: tuple[Path, Path]) -> Iterator[TestClient]:
    with TestClient(create_app(settings_for(*dataset))) as client:
        yield client


def error(response) -> dict:
    return response.json()["error"]


# Sessions


def test_the_session_catalog(client: TestClient) -> None:
    response = client.get(SESSIONS)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"coverage", "results_current", "seasons"}
    assert body["coverage"] == {
        "first": "2025 Lakeside Grand Prix (Q)",
        "last": "2026 Sample Grand Prix (R)",
        "sessions": 8,
    }
    assert body["results_current"]
    # Seasons and weekends newest first; style tables cover 2024 and 2025, not 2026.
    assert [(s["year"], s["style"]) for s in body["seasons"]] == [(2026, False), (2025, True)]
    sample = body["seasons"][0]["weekends"]
    assert [{k: w[k] for k in ("round", "event", "location", "date")} for w in sample] == [
        {"round": 1, "event": "Sample Grand Prix", "location": "Synthetic Circuit",
         "date": "2026-03-07"},
    ]  # fmt: skip
    assert sample[0]["sessions"] == [
        {"key": "2026_01_Q", "code": "Q", "name": "Qualifying", "date": "2026-03-07",
         "scored": True},
        {"key": "2026_01_R", "code": "R", "name": "Race", "date": "2026-03-07", "scored": True},
    ]  # fmt: skip
    harbour, lakeside = body["seasons"][1]["weekends"]
    assert (harbour["round"], harbour["event"], lakeside["round"]) == (3, "Harbour Grand Prix", 1)
    # A weekend's sessions in the order they ran; its date is its last session's.
    assert [s["code"] for s in harbour["sessions"]] == ["SQ", "S", "Q", "R"]
    assert [s["date"] for s in harbour["sessions"]] == [
        "2025-04-18", "2025-04-19", "2025-04-19", "2025-04-20"
    ]  # fmt: skip
    assert harbour["date"] == "2025-04-20" and lakeside["date"] == "2025-03-16"
    assert harbour["sessions"][0] == {
        "key": "2025_03_SQ",
        "code": "SQ",
        "name": "Sprint Qualifying",
        "date": "2025-04-18",
        "scored": False,  # processed after the scoring run
    }
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.headers["etag"].startswith('"')


def test_stale_results_score_nothing(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    from test_tools_mistakes import copy_results

    root, results = dataset
    older = copy_results(results, tmp_path / "results", run_id="2026-01-01T00:00:00+00:00/old")
    with TestClient(create_app(settings_for(root, older))) as client:
        body = client.get(SESSIONS).json()
    assert not body["results_current"]
    every = [s for season in body["seasons"] for w in season["weekends"] for s in w["sessions"]]
    assert len(every) == 8 and not any(s["scored"] for s in every)
    assert not any(season["style"] for season in body["seasons"])  # no style tables in the copy


def test_sessions_without_results(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    root, _ = dataset
    with TestClient(create_app(settings_for(root, tmp_path / "results"))) as client:
        response = client.get(SESSIONS)
    assert response.status_code == 200  # the sessions are there; nothing is scored
    body = response.json()
    assert not body["results_current"] and body["coverage"]["sessions"] == 8
    assert not any(
        s["scored"] for season in body["seasons"] for w in season["weekends"] for s in w["sessions"]
    )


def test_no_processed_sessions_is_503(tmp_path: Path) -> None:
    root, results = tmp_path / "processed", tmp_path / "results"
    with TestClient(create_app(settings_for(root, results))) as client:
        response = client.get(SESSIONS)
    assert response.status_code == 503
    body = error(response)
    assert body["code"] == "results_unavailable" and body["tool"] is None
    assert "make data" in body["message"] and str(tmp_path) not in response.text


# Drivers


def test_a_sessions_drivers(client: TestClient) -> None:
    response = client.get(DRIVERS, params={"event": "sample", "year": 2026, "session": "Q"})
    assert response.status_code == 200
    body = response.json()
    assert body["session"] == {
        "key": "2026_01_Q",
        "year": 2026,
        "round": 1,
        "event": "Sample Grand Prix",
        "location": "Synthetic Circuit",
        "session_code": "Q",
        "session_name": "Qualifying",
        "date": "2026-03-07",
    }
    # Sorted by team, then code; every lap counts, out laps included.
    assert body["drivers"] == [
        {"driver": "AAA", "team": "Team A", "laps": 10},
        {"driver": "BBB", "team": "Team B", "laps": 4},
        {"driver": "CCC", "team": "Team C", "laps": 2},
        {"driver": "DDD", "team": "Team D", "laps": 2},
    ]
    assert response.headers["cache-control"] == "private, max-age=300"


def test_drivers_read_the_session_as_the_tools_do(client: TestClient) -> None:
    # No session: qualifying, as every tool defaults. A session key works too.
    default = client.get(DRIVERS, params={"event": "Sample Grand Prix"}).json()
    assert default["session"]["key"] == "2026_01_Q"
    race = client.get(DRIVERS, params={"event": "2026_01_R"}).json()
    assert race["session"]["session_code"] == "R"
    assert [d["driver"] for d in race["drivers"]] == ["AAA", "BBB"]
    # Sorted by team: Blue's CCC, Green's EEE (the team of most of its laps), Red's AAA, BBB.
    sprint = client.get(DRIVERS, params={"event": "harbour", "session": "sprint"}).json()
    assert sprint["session"]["key"] == "2025_03_S"
    assert sprint["drivers"] == [
        {"driver": "CCC", "team": "Blue", "laps": 1},
        {"driver": "EEE", "team": "Green", "laps": 3},
        {"driver": "AAA", "team": "Red", "laps": 3},
        {"driver": "BBB", "team": "Red", "laps": 2},
    ]


def test_drivers_errors(client: TestClient) -> None:
    unknown = client.get(DRIVERS, params={"event": "nowhere"})
    assert unknown.status_code == 422
    body = error(unknown)
    assert body["code"] == "invalid_input" and body["tool"] is None
    assert body["message"].startswith("No processed event matches 'nowhere'.")
    no_sprint = error(client.get(DRIVERS, params={"event": "lakeside", "session": "S"}))
    assert (
        no_sprint["code"] == "invalid_input"
        and "has no processed 'S' session" in no_sprint["message"]
    )
    extra = client.get(DRIVERS, params={"event": "sample", "zzz": 1})
    assert extra.status_code == 422 and error(extra)["code"] == "invalid_request"
    assert "zzz" in error(extra)["message"]
    missing = client.get(DRIVERS)
    assert missing.status_code == 422 and error(missing)["code"] == "invalid_request"
    assert error(client.get(DRIVERS, params={"event": "sample", "year": 1999}))["code"] == (
        "invalid_request"
    )


def test_a_session_without_laps_is_503(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    # Listed in the sessions table but its laps file is gone: the data needs rebuilding, so 503
    # (as missing results are), not 422.
    root, results = dataset
    copy = tmp_path / "processed"
    shutil.copytree(root, copy)
    (copy / "laps" / "2025_01_R.parquet").unlink()
    with TestClient(create_app(settings_for(copy, results))) as client:
        response = client.get(DRIVERS, params={"event": "lakeside", "session": "R"})
        assert client.get(DRIVERS, params={"event": "lakeside"}).status_code == 200
    assert response.status_code == 503
    body = error(response)
    assert body["code"] == "results_unavailable" and "make data" in body["message"]
    assert "2025 Lakeside Grand Prix Race" in body["message"] and str(tmp_path) not in response.text


def test_unknown_keys_are_refused_everywhere(client: TestClient) -> None:
    for path in (SESSIONS, STYLES):
        response = client.get(path, params={"zzz": 1})
        assert response.status_code == 422 and error(response)["code"] == "invalid_request"


# Styles


def test_the_style_catalog(client: TestClient) -> None:
    response = client.get(STYLES)
    assert response.status_code == 200
    body = response.json()
    assert body["years"] == [2025, 2024] and body["year"] == 2025  # the newest by default
    assert body["drivers"] == [
        {"driver": "CCC", "team": "Blue", "events": 6},
        {"driver": "DDD", "team": "Blue", "events": 6},
        {"driver": "EEE", "team": "Green", "events": 6},
        {"driver": "FFF", "team": "Green", "events": 6},
        {"driver": "AAA", "team": "Red", "events": 6},
        {"driver": "BBB", "team": "Red", "events": 6},
    ]
    # Each pair once (the table has both ways round), a < b, sorted by team.
    pairs = [(p["team"], p["a"], p["b"], p["events"]) for p in body["pairs"]]
    assert pairs == [
        ("Blue", "CCC", "DDD", 6),
        ("Green", "EEE", "FFF", 6),
        ("Red", "AAA", "BBB", 6),
    ]
    for pair in body["pairs"]:
        first, second = pair["consistent"]
        assert isinstance(first, bool) and first == second
    assert response.headers["cache-control"] == "private, max-age=300"


def test_a_mid_season_swap_gives_two_pairs(client: TestClient) -> None:
    body = client.get(STYLES, params={"year": 2024}).json()
    assert body["year"] == 2024
    pairs = [(p["team"], p["a"], p["b"], p["events"]) for p in body["pairs"]]
    assert pairs == [("Blue", "CCC", "DDD", 5), ("Red", "AAA", "GGG", 3), ("Red", "AAA", "HHH", 2)]
    # Three and two events are too few to split into odd and even rounds: not consistent.
    assert body["pairs"][2]["consistent"] == [False, False]
    assert [(d["driver"], d["events"]) for d in body["drivers"] if d["team"] == "Red"] == [
        ("AAA", 5), ("GGG", 3), ("HHH", 2)
    ]  # fmt: skip


def test_an_unknown_style_season_is_422(client: TestClient) -> None:
    response = client.get(STYLES, params={"year": 2023})
    assert response.status_code == 422
    assert error(response) == {
        "code": "invalid_input",
        "message": "No style data for 2023. Seasons: 2024, 2025.",
        "tool": None,
    }
    assert error(client.get(STYLES, params={"year": "true"}))["code"] == "invalid_request"


def test_missing_or_stale_style_tables_are_503(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    from test_tools_mistakes import copy_results

    root, results = dataset
    missing = copy_results(results, tmp_path / "missing")  # scores and mistakes only
    stale = tmp_path / "stale"
    shutil.copytree(results, stale)
    manifest = stale / provenance.MANIFEST
    manifest.write_text(manifest.read_text().replace('"run_id": "', '"run_id": "other/'))
    for folder, words in ((missing, "hasn't been built"), (stale, "out of date")):
        with TestClient(create_app(settings_for(root, folder))) as client:
            response = client.get(STYLES)
        assert response.status_code == 503, folder
        body = error(response)
        assert body["code"] == "results_unavailable" and words in body["message"]
        assert str(tmp_path) not in response.text


# Caching and CORS


@pytest.mark.parametrize(
    ("path", "params", "other"),
    [
        (SESSIONS, {}, None),
        (DRIVERS, {"event": "sample"}, {"event": "sample", "session": "R"}),
        (STYLES, {}, {"year": 2024}),
    ],
)
def test_a_matching_etag_gets_304(
    client: TestClient, path: str, params: dict, other: dict | None
) -> None:
    first = client.get(path, params=params)
    tag = first.headers["etag"]
    assert client.get(path, params=params).headers["etag"] == tag  # the same every time
    again = client.get(path, params=params, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.headers["etag"] == tag and not again.content
    assert again.headers["cache-control"] == "private, max-age=300"
    weak = client.get(path, params=params, headers={"If-None-Match": f"W/{tag}"})
    assert weak.status_code == 304
    if other is not None:
        changed = client.get(path, params=other, headers={"If-None-Match": tag})
        assert changed.status_code == 200 and changed.headers["etag"] != tag


def test_the_etag_follows_the_data(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    root, results = dataset
    copy = tmp_path / "results"
    shutil.copytree(results, copy)
    with TestClient(create_app(settings_for(root, copy))) as client:
        before = client.get(SESSIONS).headers["etag"]
        rebuilt = copy / "mistakes.parquet"  # as `make m4` would leave it: same run, new file
        mtime = rebuilt.stat().st_mtime_ns + 1_000_000_000
        os.utime(rebuilt, ns=(mtime, mtime))
        after = client.get(SESSIONS, headers={"If-None-Match": before})
    assert after.status_code == 200 and after.headers["etag"] != before


@pytest.mark.parametrize("path", [SESSIONS, f"{DRIVERS}?event=sample", STYLES])
def test_the_dev_server_can_read_the_catalog(client: TestClient, path: str) -> None:
    response = client.get(path, headers={"Origin": LOCAL})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == LOCAL
    refused = client.get(path, headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in refused.headers
    failed = client.get(DRIVERS, params={"event": "nowhere"}, headers={"Origin": LOCAL})
    assert failed.headers["access-control-allow-origin"] == LOCAL  # errors are readable too


# Not tools


def test_the_catalog_routes_are_not_tools(dataset: tuple[Path, Path]) -> None:
    """The catalog adds routes, not specs: the REST tool list, the MCP server's tools and the
    chat's tool definitions and prompt version are those of TOOLS alone."""
    settings = settings_for(*dataset, chat="fake")
    app = create_app(settings)
    assert tuple(app.state.specs) == TOOLS
    mcp = [t.name for t in anyio.run(app.state.server.list_tools)]
    assert sorted(mcp) == sorted(["show_sample_telemetry", *(s.name for s in TOOLS)])
    with TestClient(app) as client:
        listed = [t["name"] for t in client.get("/api/tools").json()]
        paths = set(client.get("/openapi.json").json()["paths"])
        chat = service_for(app.state)
        assert chat is not None and chat.mode == "fake"
    assert listed == [s.name for s in TOOLS]
    assert {SESSIONS, DRIVERS, STYLES} <= paths
    assert [d["name"] for d in chat.tool_definitions] == [s.name for s in TOOLS]
    alone = ChatService(
        scripted_client(ScriptedClaude()),
        ChatConfig.from_settings(settings),
        TOOLS,
        app.state.paths,
        mode="fake",
    )
    assert chat.prompt_version == alone.prompt_version
