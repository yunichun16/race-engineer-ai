"""The chat's guardrails through the API, with the scripted chat (no network, no API key):
refusals and their bodies, settling (a disconnect mid-stream, an answer that never started, the
busy branch, a call cut off in flight), the per-question ceiling, the in-process pre-checks that
keep refused traffic off the store, request rates and sizes, the heavy-tool queue, gzip,
readiness, CORS and the settings.
"""

from __future__ import annotations

import gzip
import inspect
import json
import logging
import re
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import anthropic
import anyio
import httpx2
import pytest
from fake_upstash import FakeUpstash
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.types import Message

from race_engineer.api.app import create_app
from race_engineer.api.chat.service import ChatConfig, create_chat, service_for
from race_engineer.api.chat.session import ChatRejected
from race_engineer.api.limits.contract import Clock
from race_engineer.api.limits.guard import (
    NOT_CONFIGURED,
    SECRET_MISSING,
    STORE_DOWN,
    ChatGuard,
    build_guard,
    limits_enabled,
)
from race_engineer.api.limits.identity import normalise, visitor_id
from race_engineer.api.limits.memory import MemoryStore
from race_engineer.api.limits.policy import (
    Admission,
    Policy,
    StoreStatus,
    micro,
    until_midnight,
    utc_day,
)
from race_engineer.api.routes.chat import ChatRequest, accept
from race_engineer.api.settings import Settings
from race_engineer.tools import registry
from race_engineer.tools.definitions import TOOLS
from race_engineer.tools.params import Event, ToolParams
from race_engineer.tools.registry import (
    BUSY_MESSAGE,
    HEAVY_LIMIT,
    HEAVY_QUEUE,
    DataPaths,
    ToolBusyError,
    ToolOutput,
    ToolSpec,
    run_tool_async,
)
from race_engineer.tools.sessions import ToolInputError
from race_engineer.tools.synthetic_style import write_style_results

QUICK = "Who will win the 2027 championship?"  # answered without a tool: one API call
COMPARE = "Where did BBB gain on AAA in Sample qualifying 2026?"  # a tool, then a second call
LOCAL = "http://localhost:3000"


@pytest.fixture(autouse=True)
def empty_result_cache() -> None:
    registry.RESULTS.clear()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    from test_tools_mistakes import RUN_ID, mistakes_dataset

    root, results = mistakes_dataset(tmp_path_factory.mktemp("limits"))
    write_style_results(results, RUN_ID)  # complete data: health ok, /api/ready 200
    return root, results


def settings(dataset: tuple[Path, Path], **changes: Any) -> Settings:
    """The scripted chat with the limits as a deployment has them: a public host, limits on (the
    memory store), the chat secret set."""
    values = {
        "chat": "fake",
        "mount_mcp": False,
        "host": "0.0.0.0",
        "limits": "on",
        "limits_store": "memory",
        "chat_secret_set": True,
    } | changes
    return Settings(processed=dataset[0], results=dataset[1], **values)


def limited(dataset: tuple[Path, Path], store: Any = None, **changes: Any) -> FastAPI:
    return create_app(settings(dataset, **changes), limit_store=store)


def plain(dataset: tuple[Path, Path], **changes: Any) -> FastAPI:
    """The local defaults: loopback, the limits off."""
    values = {"chat": "fake", "mount_mcp": False} | changes
    return create_app(Settings(processed=dataset[0], results=dataset[1], **values))


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for chunk in text.replace("\r\n", "\n").split("\n\n"):
        name, data = None, []
        for line in chunk.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if name is not None:
            events.append((name, json.loads("\n".join(data))))
    return events


def ask(client: TestClient, message: str = QUICK) -> list[tuple[str, dict[str, Any]]]:
    response = client.post("/api/chat", json={"message": message})
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert events[-1][0] == "done"
    return events


def done(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    return events[-1][1]


def refused(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return error


def counters(store: MemoryStore, visitor: str | None = None) -> StoreStatus:
    return anyio.run(store.status, visitor, store.clock(), Policy())


def visitor_of(app: FastAPI, client: tuple[str, int] = ("testclient", 50000)) -> str:
    """The visitor code the app gives a client today."""
    secret = app.state.settings.chat_secret
    return visitor_id(normalise(client[0]), secret, utc_day(time.time()))


# Refusals.


def test_the_hour_runs_out_with_a_429_the_browser_can_read(dataset: tuple[Path, Path]) -> None:
    with TestClient(limited(dataset, rate_hour=3)) as client:
        for asked, left in enumerate((2, 1, 0), start=1):
            assert done(ask(client))["quota"] == {"hour_left": left, "day_left": 25 - asked}
        response = client.post("/api/chat", json={"message": QUICK}, headers={"Origin": LOCAL})
        error = refused(response, 429, "rate_limited")
        assert set(error) == {"code", "message", "retry_after_s", "limit"}
        assert error["limit"] == "hour" and 3500 < error["retry_after_s"] <= 3600
        assert error["message"] == (
            "That's 3 questions in the last hour from this connection. You can ask again in "
            "60 minutes."
        )
        assert response.headers["retry-after"] == str(error["retry_after_s"])
        assert response.headers["access-control-allow-origin"] == LOCAL
        assert "retry-after" in response.headers["access-control-expose-headers"].lower()
        status = client.get("/api/chat/status").json()
        assert (status["available"], status["reason"]) == (False, "rate_limited")
        assert status["quota"]["hour_left"] == 0 and 3500 < status["retry_after_s"] <= 3600


def test_the_day_runs_out(dataset: tuple[Path, Path]) -> None:
    with TestClient(limited(dataset, rate_day=2)) as client:
        ask(client)
        assert done(ask(client))["quota"] == {"hour_left": 8, "day_left": 0}
        error = refused(client.post("/api/chat", json={"message": QUICK}), 429, "rate_limited")
        assert error["limit"] == "day"
        assert abs(error["retry_after_s"] - until_midnight(time.time())) <= 5
        assert error["message"].startswith(
            "That's today's 2 questions from this connection. The count resets at midnight UTC"
        )


@pytest.mark.parametrize(("cap", "reserve"), [(0.0, 0.10), (0.01, 0.10), (0.0, 0.0)])
def test_a_small_cap_closes_the_chat_before_any_question(
    dataset: tuple[Path, Path], cap: float, reserve: float
) -> None:
    # A $0 cap closes the chat even when nothing is reserved per question.
    with TestClient(limited(dataset, daily_cap_usd=cap, reserve_usd=reserve)) as client:
        status = client.get("/api/chat/status").json()
        assert (status["available"], status["reason"]) == (False, "daily_cap")
        assert abs(status["retry_after_s"] - until_midnight(time.time())) <= 5
        chat = client.get("/api/health").json()["chat"]
        assert (chat["available"], chat["reason"]) == (False, "daily_cap")
        error = refused(client.post("/api/chat", json={"message": QUICK}), 503, "daily_cap")
        assert abs(error["retry_after_s"] - until_midnight(time.time())) <= 5
        assert "budget" in error["message"]


def test_the_kill_switch(dataset: tuple[Path, Path]) -> None:
    # RACE_ENGINEER_PAUSED=1, with the limits on or off.
    for limits in ("on", "off"):
        with TestClient(limited(dataset, paused=True, limits=limits)) as client:
            error = refused(client.post("/api/chat", json={"message": QUICK}), 503, "chat_paused")
            assert error["retry_after_s"] is None
            assert client.get("/api/chat/status").json()["reason"] == "paused"
            assert client.get("/api/health").json()["chat"]["reason"] == "paused"
    # The store's key (make chat-pause), remembered for 60 s once seen.
    clock = Clock(time.time())
    store = MemoryStore(clock=clock)
    with TestClient(limited(dataset, store)) as client:
        anyio.run(store.set_paused, True)
        refused(client.post("/api/chat", json={"message": QUICK}), 503, "chat_paused")
        assert client.get("/api/chat/status").json()["reason"] == "paused"
        anyio.run(store.set_paused, False)
        refused(client.post("/api/chat", json={"message": QUICK}), 503, "chat_paused")
        clock.advance(61)
        ask(client)


def test_an_unconfigured_store_closes_the_chat_but_not_the_tools(
    dataset: tuple[Path, Path],
) -> None:
    with TestClient(limited(dataset, limits_store="upstash")) as client:
        error = refused(client.post("/api/chat", json={"message": QUICK}), 503, "chat_unavailable")
        assert error["retry_after_s"] == 60
        assert client.get("/api/tools/list_sessions").status_code == 200
        assert client.get("/api/catalog/sessions").status_code == 200
        health = client.get("/api/health").json()
        assert health["status"] == "ok"  # the data is fine; the limits say what's wrong
        assert (health["chat"]["available"], health["chat"]["reason"]) == (False, "unavailable")
        assert health["limits"] == {
            "enabled": True,
            "store": None,
            "per_hour": 10,
            "per_day": 25,
            "problems": [NOT_CONFIGURED],
        }
        assert client.get("/api/chat/status").json()["reason"] == "unavailable"


def test_a_failing_store_fails_closed_and_is_left_alone_for_30_s(
    dataset: tuple[Path, Path],
) -> None:
    clock = Clock(time.time())
    fake = FakeUpstash(clock)
    with TestClient(limited(dataset, fake.store(clock))) as client:
        fake.down = True
        refused(client.post("/api/chat", json={"message": QUICK}), 503, "chat_unavailable")
        tried = fake.requests
        refused(client.post("/api/chat", json={"message": QUICK}), 503, "chat_unavailable")
        assert fake.requests == tried  # the circuit breaker: no second timeout
        health = client.get("/api/health").json()
        assert health["chat"]["reason"] == "unavailable"
        assert STORE_DOWN in health["limits"]["problems"]
        fake.down = False
        clock.advance(31)
        ask(client)
        assert STORE_DOWN not in client.get("/api/health").json()["limits"]["problems"]


def test_limits_off_change_nothing(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    # No saved answers: an empty folder, not the repository's data/saved.
    app = plain(dataset, rate_hour=1, tools_rpm=1, chat_max_body=10_000, saved_dir=tmp_path)
    with TestClient(app) as client:
        for _ in range(3):
            assert done(ask(client))["quota"] is None
            assert client.get("/api/tools/list_sessions").status_code == 200
        status = client.get("/api/chat/status").json()
        assert status == {
            "available": True,
            "mode": "fake",
            "reason": None,
            "retry_after_s": None,
            "quota": None,
            "visitor": None,
            "saved": [],
        }
        health = client.get("/api/health").json()
        assert health["limits"]["enabled"] is False and health["limits"]["store"] is None
        assert health["chat"]["available"] is True


# Status and health.


def test_the_status_route(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    saved = tmp_path / "saved"
    saved.mkdir()
    recording = {
        "v": 1,
        "id": "sample-question",
        "question": "Summarise the Sample GP.",
        "recorded_at": "2026-10-04T13:05:12Z",
        "mode": "fake",
        "events": [],
    }
    (saved / "sample-question.json").write_text(json.dumps(recording))
    app = limited(dataset, saved_dir=saved, saved_allow_fake=True)
    with TestClient(app) as client:
        response = client.get("/api/chat/status")
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        tag = body.pop("visitor")
        assert re.fullmatch(r"[0-9a-f]{6}", tag) and visitor_of(app).startswith(tag)
        assert body == {
            "available": True,
            "mode": "fake",
            "reason": None,
            "retry_after_s": None,
            "quota": {"hour_left": 10, "day_left": 25, "per_hour": 10, "per_day": 25},
            "saved": [{"id": "sample-question", "recorded_at": "2026-10-04T13:05:12Z"}],
        }
        ask(client)
        after = client.get("/api/chat/status").json()  # not served from the 10 s cache
        assert after["quota"]["hour_left"] == 9 and after["visitor"] == tag
        other = TestClient(app, client=("203.0.113.9", 4000)).get("/api/chat/status").json()
        assert other["visitor"] != tag and other["quota"]["hour_left"] == 10
    with TestClient(limited(dataset, saved_dir=saved)) as client:
        assert client.get("/api/chat/status").json()["saved"] == []  # scripted: hidden


def test_health_shows_no_dollar_amounts(dataset: tuple[Path, Path]) -> None:
    with TestClient(limited(dataset, limits_count_fake=True)) as client:
        ask(client)
        health = client.get("/api/health")
    text = health.text.lower()
    for word in ("$", "usd", "spend", "spent", "reserv", "cost", "budget"):
        assert word not in text
    body = health.json()
    assert body["limits"] == {
        "enabled": True,
        "store": "memory",
        "per_hour": 10,
        "per_day": 25,
        "problems": [],
    }
    assert (body["chat"]["available"], body["chat"]["reason"]) == (True, None)


def test_a_public_server_without_a_chat_secret_says_so(dataset: tuple[Path, Path]) -> None:
    with TestClient(limited(dataset, chat_secret_set=False)) as client:
        assert SECRET_MISSING in client.get("/api/health").json()["limits"]["problems"]
    with TestClient(plain(dataset)) as client:  # loopback: the random secret is fine
        assert client.get("/api/health").json()["limits"]["problems"] == []


# Settling.


def test_fake_spend_counts_only_when_asked(dataset: tuple[Path, Path]) -> None:
    for count in (False, True):
        store = MemoryStore()
        with TestClient(limited(dataset, store, limits_count_fake=count)) as client:
            cost = done(ask(client))["cost_usd"]
        found = counters(store)
        assert cost > 0 and found.reserved_micro == 0 and found.questions == 1
        assert found.spend_micro == (micro(cost) if count else 0)


def raw_scope(app: FastAPI, body: bytes, client: tuple[str, int] = ("testclient", 50000)) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": client,
        "server": ("testserver", 80),
        "app": app,
    }


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[FastAPI]:
    async with app.router.lifespan_context(app):
        yield app


@pytest.mark.anyio
async def test_spend_is_settled_after_a_disconnect_mid_stream(
    dataset: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    store = MemoryStore()
    app = limited(dataset, store, limits_count_fake=True)
    body = json.dumps({"message": COMPARE}).encode()
    first_event = anyio.Event()
    asked = False

    async def receive() -> Message:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": body, "more_body": False}
        await first_event.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_event.set()

    caplog.set_level(logging.INFO, "race_engineer.api.chat.runner")
    async with running(app):
        with anyio.fail_after(10):
            await app(raw_scope(app, body), receive, send)
    assert "outcome=disconnected" in caplog.text
    found = await store.status(visitor_of(app), time.time(), Policy())
    assert found.reserved_micro == 0  # released
    assert found.spend_micro > 0  # the call that was cut off still counts what it reported
    assert (found.questions, found.hour_used) == (1, 1)  # a disconnect isn't refunded


@pytest.mark.anyio
async def test_a_client_that_leaves_before_its_answer_starts_gets_it_back(
    dataset: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    store = MemoryStore()
    app = limited(dataset, store, limits_count_fake=True)
    body = json.dumps({"message": QUICK}).encode()
    messages = [
        {"type": "http.request", "body": body, "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        pass

    caplog.set_level(logging.INFO, "race_engineer.api.chat.runner")
    async with running(app):
        await app(raw_scope(app, body), receive, send)
        found = await store.status(visitor_of(app), time.time(), Policy())
    assert "chat request=" not in caplog.text  # no answer was started (nor paid for)
    assert (found.reserved_micro, found.spend_micro) == (0, 0)
    assert (found.questions, found.hour_used, found.day_used) == (0, 0, 0)  # refunded


@pytest.mark.anyio
async def test_an_admitted_answer_that_never_starts_settles_as_not_started(
    dataset: tuple[Path, Path],
) -> None:
    # The dependency exits before the endpoint iterates the answer: the answer's generator never
    # runs, so only the route can settle it.
    store = MemoryStore()
    app = limited(dataset, store)

    async def receive() -> Message:
        await anyio.sleep_forever()
        raise AssertionError

    async with running(app):
        request = Request(raw_scope(app, b"{}"), receive)
        dependency = cast(AsyncGenerator[Any], accept(request, ChatRequest(message=QUICK)))
        accepted = await anext(dependency)
        during = await store.status(visitor_of(app), time.time(), Policy())
        assert (during.reserved_micro, during.questions) == (100_000, 1)
        assert inspect.getasyncgenstate(accepted.events) == inspect.AGEN_CREATED  # not started
        await dependency.aclose()
        after = await store.status(visitor_of(app), time.time(), Policy())
    assert (after.reserved_micro, after.questions, after.hour_used) == (0, 0, 0)


@pytest.mark.anyio
async def test_settling_happens_once(dataset: tuple[Path, Path]) -> None:
    store = MemoryStore()
    app = limited(dataset, store)
    async with running(app):
        guard: ChatGuard = app.state.guard
        admission = await guard.admit(Request(raw_scope(app, b"{}")))
        assert admission is not None and admission.admitted
        admission.outcome, admission.spent_usd = "answered", 0.05
        await guard.settle(admission)
        await guard.settle(admission)  # a no-op
        await guard.settle(None)  # limits off: nothing to settle
        found = await store.status(None, time.time(), Policy())
    assert admission.settled and (found.spend_micro, found.reserved_micro) == (50_000, 0)


@pytest.mark.anyio
async def test_the_busy_branch_records_its_outcome_and_is_refunded(
    dataset: tuple[Path, Path],
) -> None:
    store = MemoryStore()
    app = limited(dataset, store, chat_concurrency=1)
    async with running(app):
        guard: ChatGuard = app.state.guard
        admission = await guard.admit(Request(raw_scope(app, b"{}")))
        service = service_for(app.state)
        assert service is not None and service.slots.try_acquire()  # every slot taken
        events = service.answer([], QUICK, ledger=admission)
        first = await anext(events)  # the busy branch records before it yields
        assert first.event == "error" and admission is not None and admission.outcome == "busy"
        await events.aclose()
        service.slots.release()
        await guard.settle(admission)
        found = await store.status(None, time.time(), Policy())
    assert (found.questions, found.reserved_micro, found.spend_micro) == (0, 0, 0)


@pytest.mark.anyio
async def test_a_call_cut_off_before_any_usage_is_charged_an_estimate(
    dataset: tuple[Path, Path],
) -> None:
    paths = DataPaths(*dataset)

    def client_hanging(started: anyio.Event) -> anthropic.AsyncAnthropic:
        async def hang(request: httpx2.Request) -> httpx2.Response:
            started.set()
            await anyio.sleep_forever()
            raise AssertionError

        transport = httpx2.MockTransport(hang)
        return anthropic.AsyncAnthropic(
            api_key="test",
            base_url="http://scripted.invalid",
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=transport),
        )

    for count in (True, False):
        started = anyio.Event()
        config = ChatConfig(mode="fake", count_fake=count)
        service = create_chat(config, TOOLS, paths, client=client_hanging(started))
        assert service is not None
        ledger = Admission("memory", utc_day(time.time()), "v", "m", 100_000)
        events = service.answer([], QUICK, ledger=ledger)

        async def consume(events: Any = events) -> None:
            async for _ in events:
                pass

        async with anyio.create_task_group() as tg:
            tg.start_soon(consume)
            await started.wait()
            tg.cancel_scope.cancel()  # the client left while the request was out
        await events.aclose()
        assert ledger.outcome == "disconnected" and ledger.spent_usd == 0
        assert ledger.api_calls == 1
        # The scripted chat costs nothing unless counted, its estimate included.
        assert ledger.in_flight_unbilled is count
        assert ledger.charge_micro() == (20_000 if count else 0)
        await service.aclose()

    store = MemoryStore()
    guard = build_guard(settings(dataset), "fake", store=store)
    admission = await store.admit("v", time.time(), Policy())
    admission.outcome, admission.in_flight_unbilled, admission.api_calls = "disconnected", True, 1
    await guard.settle(admission)
    assert (await store.status(None, time.time(), Policy())).spend_micro == 20_000


def test_the_ceiling_stops_a_question_before_its_next_call(
    dataset: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, "race_engineer.api.chat.runner")
    with TestClient(limited(dataset, chat_max_cost_usd=1e-9)) as client:
        events = ask(client, COMPARE)
    names = [n for n, _ in events if n != "text"]
    assert names[-4:] == ["tool_call", "tool_result", "error", "done"]
    error = next(d for n, d in events if n == "error")
    assert error == {
        "code": "too_costly",
        "message": "This question needed more work than this demo allows; try a narrower question.",
    }
    assert "outcome=too_costly calls=1 " in caplog.text  # stopped before the second call
    assert done(events)["questions_used"] == 1  # the completed step is kept
    assert done(events)["quota"]["hour_left"] == 9  # and counted


# The in-process pre-checks.


def test_refused_visitors_are_answered_without_the_store(dataset: tuple[Path, Path]) -> None:
    clock = Clock(time.time())
    fake = FakeUpstash(clock)
    with TestClient(limited(dataset, fake.store(clock), rate_hour=1)) as client:
        ask(client)
        before = fake.requests
        first = refused(client.post("/api/chat", json={"message": QUICK}), 429, "rate_limited")
        assert fake.requests == before + 2  # the admission and its undo
        clock.advance(5)
        again = refused(client.post("/api/chat", json={"message": QUICK}), 429, "rate_limited")
        assert fake.requests == before + 2  # from memory
        assert again["retry_after_s"] == first["retry_after_s"] - 5
        assert again["limit"] == "hour"
        clock.advance(3600)
        ask(client)
    fake = FakeUpstash(clock)
    with TestClient(limited(dataset, fake.store(clock), daily_cap_usd=0)) as client:
        refused(client.post("/api/chat", json={"message": QUICK}), 503, "daily_cap")
        before = fake.requests
        refused(client.post("/api/chat", json={"message": QUICK}), 503, "daily_cap")
        other = TestClient(client.app, client=("203.0.113.5", 1))
        refused(other.post("/api/chat", json={"message": QUICK}), 503, "daily_cap")
        assert fake.requests == before  # the cap is remembered for everyone, for 60 s


@pytest.mark.anyio
async def test_status_says_what_a_question_would_get(dataset: tuple[Path, Path]) -> None:
    # The status route answers from the same in-process memory as the chat, so the page isn't
    # told a question would be answered when the chat would refuse it; and a refund forgets a
    # refusal it was counted in.
    quota = {"hour_left": 1, "day_left": 25, "per_hour": 1, "per_day": 25}
    clock = Clock(time.time())
    store = MemoryStore(clock=clock)
    app = limited(dataset, store, rate_hour=1, daily_cap_usd=0.15)  # room for one reservation
    async with running(app):
        guard: ChatGuard = app.state.guard
        request = Request(raw_scope(app, b"{}"))
        in_flight = await guard.admit(request)
        assert in_flight is not None
        with pytest.raises(ChatRejected) as cap:
            await guard.admit(Request(raw_scope(app, b"{}", ("203.0.113.5", 1))))
        assert cap.value.code == "daily_cap"  # the reservation in flight leaves no room
        in_flight.outcome = "upstream_busy"  # Claude was overloaded: the question is given back
        await guard.settle(in_flight)  # and the reservation released
        status = await guard.status(request)
        assert (status.reason, status.quota) == ("daily_cap", quota)  # remembered for 60 s
        clock.advance(61)
        assert (await guard.status(request)).available

    clock = Clock(time.time())
    store = MemoryStore(clock=clock)
    app = limited(dataset, store, rate_hour=1)
    async with running(app):
        guard = app.state.guard
        request = Request(raw_scope(app, b"{}"))
        in_flight = await guard.admit(request)
        assert in_flight is not None
        with pytest.raises(ChatRejected) as hour:  # a second tab, while the first is answered
            await guard.admit(request)
        assert (hour.value.code, hour.value.extra) == ("rate_limited", {"limit": "hour"})
        status = await guard.status(request)
        assert (status.reason, status.retry_after_s) == ("rate_limited", hour.value.retry_after_s)
        in_flight.outcome = "upstream_busy"
        await guard.settle(in_flight)
        status = await guard.status(request)
        assert status.available and status.quota == quota
        assert await guard.admit(request) is not None  # the store decides again


def test_status_reads_are_cached_and_cost_three_commands(dataset: tuple[Path, Path]) -> None:
    clock = Clock(time.time())
    fake = FakeUpstash(clock)
    with TestClient(limited(dataset, fake.store(clock))) as client:
        before = len(fake.calls)
        client.get("/api/chat/status")
        assert fake.calls[before:] == [("/pipeline", 3)]
        client.get("/api/chat/status")
        assert len(fake.calls) == before + 1  # cached for 10 s
        clock.advance(11)
        client.get("/api/chat/status")
        assert len(fake.calls) == before + 2
        # The health check's global part: one MGET, then cached for 60 s.
        health_before = len(fake.calls)
        client.get("/api/health")
        client.get("/api/health")
        assert fake.calls[health_before:] == [("/pipeline", 1)]


def test_chat_and_status_request_rates(dataset: tuple[Path, Path]) -> None:
    clock = Clock(time.time())
    fake = FakeUpstash(clock)
    app = limited(dataset, fake.store(clock), chat_rpm=2, status_rpm=2)
    with TestClient(app) as client:
        for _ in range(2):  # refused by validation, but counted before it
            assert client.post("/api/chat", json={"message": ""}).status_code == 422
        error = refused(client.post("/api/chat", json={"message": QUICK}), 429, "rate_limited")
        assert error["message"].startswith("Too many requests from this connection")
        assert error["retry_after_s"] >= 1
        assert fake.requests == 0  # never reached the store
        for _ in range(2):
            assert client.get("/api/chat/status").status_code == 200
        refused(client.get("/api/chat/status"), 429, "rate_limited")
        other = TestClient(app, client=("203.0.113.5", 1))
        assert other.get("/api/chat/status").status_code == 200  # per visitor


def test_tool_request_rates(dataset: tuple[Path, Path]) -> None:
    with TestClient(limited(dataset, tools_rpm=2)) as client:
        for _ in range(2):
            assert client.get("/api/tools/list_sessions").status_code == 200
        response = client.get("/api/catalog/sessions", headers={"Origin": LOCAL})
        error = refused(response, 429, "rate_limited")
        assert response.headers["retry-after"] == str(error["retry_after_s"])
        assert response.headers["access-control-allow-origin"] == LOCAL
        assert client.get("/api/health").status_code == 200  # not rate-limited
    with TestClient(limited(dataset, tools_rpm=100, tools_global_rpm=3)) as client:
        visitors = [TestClient(client.app, client=(f"203.0.113.{i}", 1)) for i in range(4)]
        codes = [v.get("/api/tools/list_sessions").status_code for v in visitors]
        assert codes == [200, 200, 200, 429]
        last = visitors[-1].get("/api/tools/list_sessions").json()["error"]
        assert last["message"].startswith("The server is getting too many requests")


def test_mcp_has_a_global_rate(dataset: tuple[Path, Path]) -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    app = limited(dataset, mount_mcp=True, mcp_stateless=True, mcp_rpm=2)
    with TestClient(app) as client:
        codes = [
            TestClient(app, client=(f"203.0.113.{i}", 1))
            .post("/mcp", json=initialize, headers=headers)
            .status_code
            for i in range(3)
        ]
        assert codes == [200, 200, 429]
        assert client.get("/api/health").status_code == 200


# Request sizes.


async def call(app: FastAPI, scope: dict, chunks: list[bytes]) -> tuple[int, dict, bytes]:
    """Send a request body in `chunks`, without a Content-Length; the status, headers and body."""
    queue: list[Message] = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        if queue:
            return queue.pop(0)
        await anyio.sleep_forever()
        raise AssertionError

    async def send(message: Message) -> None:
        sent.append(message)

    with anyio.fail_after(10):
        await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, b"".join(m.get("body", b"") for m in sent[1:])


@pytest.mark.anyio
async def test_oversized_chat_bodies_get_413_both_ways(dataset: tuple[Path, Path]) -> None:
    app = plain(dataset, chat_max_body=4096)  # with the limits off too
    big = json.dumps({"message": "x" * 100, "history": [{"role": "user", "content": "y" * 5000}]})
    async with running(app):
        with TestClient(app) as client:  # by Content-Length, without reading the body
            error = refused(client.post("/api/chat", content=big), 413, "request_too_large")
            assert error["message"] == "The request is larger than 4 KB; start a new conversation."
        chunked = raw_scope(app, b"")
        chunked["headers"] = [(b"host", b"testserver"), (b"content-type", b"application/json")]
        data = big.encode()
        status, _, body = await call(app, chunked, [data[:3000], data[3000:]])
        assert status == 413 and json.loads(body)["error"]["code"] == "request_too_large"
        # A body under the limit, sent in pieces, still reaches the route whole.
        small = json.dumps({"message": QUICK}).encode()
        status, headers, body = await call(app, chunked, [small[:10], small[10:]])
        assert status == 200 and headers["content-type"].startswith("text/event-stream")
        assert b"event: done" in body


# The heavy-tool queue.


class SlowParams(ToolParams):
    event: Event


@pytest.mark.anyio
async def test_a_full_heavy_queue_refuses_the_next_heavy_call(tmp_path: Path) -> None:
    release = threading.Event()

    def slow(p: SlowParams, paths: DataPaths) -> ToolOutput:
        release.wait(10)
        return ToolOutput(f"done {p.event}")

    spec = ToolSpec("slow", "Slow", "Slow.", SlowParams, slow, heavy=True)
    paths = DataPaths(tmp_path, tmp_path)
    registry.set_heavy_limit(1)
    registry.set_heavy_queue(1)
    try:
        results: list[str] = []

        async def run(event: str) -> None:
            results.append((await run_tool_async(spec, {"event": event}, paths)).summary)

        async with anyio.create_task_group() as tg:
            tg.start_soon(run, "a")  # takes the one slot
            while registry.HEAVY.available_tokens >= 1:
                await anyio.sleep(0.01)
            tg.start_soon(run, "b")  # waits: the queue holds one
            while registry.HEAVY.statistics().tasks_waiting < 1:
                await anyio.sleep(0.01)
            with pytest.raises(ToolBusyError, match="busy"):
                await run_tool_async(spec, {"event": "c"}, paths)
            release.set()
        assert sorted(results) == ["done a", "done b"]
    finally:
        release.set()
        registry.set_heavy_limit(HEAVY_LIMIT)
        registry.set_heavy_queue(HEAVY_QUEUE)


def test_a_busy_tool_is_a_503(dataset: tuple[Path, Path]) -> None:
    def busy(p: SlowParams, paths: DataPaths) -> ToolOutput:
        raise ToolBusyError(BUSY_MESSAGE)

    spec = ToolSpec("busy_tool", "Busy", "Busy.", SlowParams, busy)
    app = create_app(
        Settings(processed=dataset[0], results=dataset[1], chat="off", mount_mcp=False),
        specs=(spec,),
    )
    with TestClient(app) as client:
        response = client.get("/api/tools/busy_tool", params={"event": "sample"})
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    assert response.json() == {
        "error": {"code": "busy", "message": BUSY_MESSAGE, "tool": "busy_tool"}
    }


# The rest of the HTTP surface.


def test_gzip_on_json_but_never_on_the_event_stream(dataset: tuple[Path, Path]) -> None:
    with TestClient(plain(dataset)) as client:
        catalog = client.get("/api/tools", headers={"Accept-Encoding": "gzip"})
        assert catalog.headers["content-encoding"] == "gzip" and catalog.json()
        stream = client.post(
            "/api/chat", json={"message": QUICK}, headers={"Accept-Encoding": "gzip"}
        )
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in stream.headers
        small = client.get("/api/ready", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in small.headers  # under 1 KB
    with TestClient(plain(dataset, gzip=False)) as client:
        assert "content-encoding" not in client.get("/api/tools").headers
    assert gzip.decompress(gzip.compress(b"x")) == b"x"


def test_ready(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    app = plain(dataset)
    response = TestClient(app).get("/api/ready")  # the lifespan hasn't run
    assert response.status_code == 503 and response.json()["ready"] is False
    with TestClient(app) as client:
        assert client.get("/api/ready").json() == {"ready": True, "problems": []}
    empty = tmp_path / "empty"
    (empty / "processed").mkdir(parents=True)
    (empty / "results").mkdir()
    with TestClient(plain((empty / "processed", empty / "results"))) as client:
        response = client.get("/api/ready")
    assert response.status_code == 503
    assert "no processed sessions (make data)" in response.json()["problems"]


def test_no_browser_origin_at_all(dataset: tuple[Path, Path]) -> None:
    assert Settings.from_env({"RACE_ENGINEER_CORS_ORIGINS": "none"}).cors_origins == ()
    preflight = {"Origin": LOCAL, "Access-Control-Request-Method": "GET"}
    with TestClient(plain(dataset, cors_origins=())) as client:
        response = client.options("/api/health", headers=preflight)
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers
        plain_get = client.get("/api/health", headers={"Origin": LOCAL})
        assert "access-control-allow-origin" not in plain_get.headers


def test_the_preview_origin_pattern(dataset: tuple[Path, Path]) -> None:
    pattern = r"https://race-engineer-[a-z0-9-]+\.vercel\.app"
    app = plain(
        dataset, cors_origins=("https://race-engineer.vercel.app",), cors_origin_regex=pattern
    )
    with TestClient(app) as client:
        for origin, allowed in (
            ("https://race-engineer.vercel.app", True),
            ("https://race-engineer-git-m7-me.vercel.app", True),
            ("https://evil.example", False),
            ("https://race-engineer-x.vercel.app.evil.example", False),
        ):
            headers = {"Origin": origin, "Access-Control-Request-Method": "POST"}
            response = client.options("/api/chat", headers=headers)
            assert (response.status_code == 200) is allowed, origin


def test_sse_padding(dataset: tuple[Path, Path]) -> None:
    with TestClient(plain(dataset, sse_pad=64)) as client:
        text = client.post("/api/chat", json={"message": QUICK}).text
    assert text.startswith(": " + "." * 64 + "\n\n")
    assert parse_sse(text)[-1][0] == "done"


@pytest.mark.anyio
async def test_mcp_error_results_lose_the_data_paths(dataset: tuple[Path, Path]) -> None:
    from test_api_mcp_mount import mcp_client

    def where(p: SlowParams, paths: DataPaths) -> ToolOutput:
        raise ToolInputError(f"Nothing for {p.event} in {paths.processed} or {paths.results}.")

    spec = ToolSpec("where", "Where", "Where.", SlowParams, where, chart="find-mistakes")
    root, results = dataset
    app = create_app(
        Settings(processed=root, results=results, chat="off", mcp_host_check=False),
        specs=(spec,),
    )
    async with mcp_client(app) as client:
        result = await client.call_tool("where", {"event": "x"})
    text = getattr(result.content[0], "text", "")
    assert result.is_error and text == "Nothing for x in data/processed or data/results."
    assert str(root) not in text


# Settings.


def test_the_guardrail_settings() -> None:
    defaults = Settings.from_env({})
    assert (defaults.limits, defaults.limits_store, defaults.paused) == ("auto", "auto", False)
    assert (defaults.rate_hour, defaults.rate_day, defaults.daily_questions) == (10, 25, 1000)
    assert (defaults.daily_cap_usd, defaults.reserve_usd, defaults.chat_max_cost_usd) == (
        3.0,
        0.10,
        0.30,
    )
    assert (defaults.tools_rpm, defaults.tools_global_rpm, defaults.mcp_rpm) == (120, 600, 240)
    assert (defaults.chat_rpm, defaults.chat_global_rpm, defaults.status_rpm) == (20, 120, 60)
    assert (defaults.chat_max_body, defaults.mcp_max_body) == (655360, 524288)
    assert (defaults.heavy_queue, defaults.trusted_proxy_hops, defaults.sse_pad) == (8, 0, 0)
    assert defaults.gzip and not defaults.mcp_stateless and not defaults.limits_count_fake
    assert defaults.cors_origin_regex is None and not defaults.saved_allow_fake
    assert not defaults.chat_secret_set

    env = {
        "RACE_ENGINEER_LIMITS": "on",
        "RACE_ENGINEER_LIMITS_STORE": "upstash",
        "UPSTASH_REDIS_REST_URL": "https://example.upstash.io",
        "UPSTASH_REDIS_REST_TOKEN": "token-value-7c1e",
        "RACE_ENGINEER_RATE_HOUR": "3",
        "RACE_ENGINEER_DAILY_CAP_USD": "$0.01",
        "RACE_ENGINEER_PAUSED": "1",
        "RACE_ENGINEER_TRUSTED_PROXY_HOPS": "1",
        "RACE_ENGINEER_MCP_STATELESS": "1",
        "RACE_ENGINEER_CHAT_SECRET": "s",
        "RACE_ENGINEER_SAVED": "/srv/saved",
        "RACE_ENGINEER_SAVED_ALLOW_FAKE": "1",
        "RACE_ENGINEER_CORS_ORIGIN_REGEX": r"https://x-[a-z]+\.example",
    }
    found = Settings.from_env(env)
    assert (found.limits, found.limits_store, found.rate_hour, found.daily_cap_usd) == (
        "on",
        "upstash",
        3,
        0.01,
    )
    assert found.paused and found.mcp_stateless and found.chat_secret_set
    assert found.trusted_proxy_hops == 1 and found.saved_dir == Path("/srv/saved")
    assert found.saved_allow_fake and found.cors_origin_regex == r"https://x-[a-z]+\.example"
    assert "token-value" not in repr(found) and "example.upstash.io" not in repr(found)
    assert Settings.from_env({"RACE_ENGINEER_DAILY_CAP_USD": "0"}).daily_cap_usd == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RACE_ENGINEER_LIMITS", "maybe"),
        ("RACE_ENGINEER_LIMITS_STORE", "redis"),
        ("RACE_ENGINEER_RATE_HOUR", "0"),
        ("RACE_ENGINEER_DAILY_CAP_USD", "-1"),
        ("RACE_ENGINEER_DAILY_CAP_USD", "lots"),
        ("RACE_ENGINEER_CHAT_MAX_COST_USD", "0"),
        ("RACE_ENGINEER_TRUSTED_PROXY_HOPS", "-1"),
        ("RACE_ENGINEER_CORS_ORIGIN_REGEX", "(["),
        ("RACE_ENGINEER_SSE_PAD", "x"),
        ("RACE_ENGINEER_GZIP", "maybe"),
        ("UPSTASH_REDIS_REST_URL", "redis://default:pw-3f9a@db.upstash.io:6379"),
    ],
)
def test_bad_guardrail_settings_name_the_variable(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name) as refused:
        Settings.from_env({name: value})
    if name.startswith("UPSTASH"):
        assert "pw-3f9a" not in str(refused.value)  # never printed


def test_limits_auto_means_the_real_model_on_a_public_host() -> None:
    public = Settings(host="0.0.0.0")
    assert limits_enabled(public, "anthropic")
    assert not limits_enabled(public, "fake")
    assert not limits_enabled(Settings(), "anthropic")  # loopback: the user's own runs
    assert limits_enabled(Settings(limits="on"), "fake")
    assert not limits_enabled(Settings(limits="off", host="0.0.0.0"), "anthropic")
