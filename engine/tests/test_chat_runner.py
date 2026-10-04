"""The chat endpoint end to end, offline: the real Anthropic SDK and tool runner talk to the
scripted stand-in for Claude (api/chat/fake.py) through an httpx MockTransport, and the route
streams SSE through FastAPI's TestClient.

Checks the event order, the request bodies (model, effort, thinking, fallbacks, caching, eager
streaming, tool order), that chart data never reaches a request, that the history is append-only,
and the refusal, truncation, invalid-argument, unreadable-input, rate-limit, fallback, step-limit
and timeout paths.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from typing import Any

import anthropic
import anyio
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message

from race_engineer.api.app import create_app
from race_engineer.api.chat.fake import (
    SCRIPTED_NOTE,
    SIGNATURE,
    Reader,
    ScriptedClaude,
    scripted_client,
    sse_event,
    sse_stream,
)
from race_engineer.api.chat.pricing import Usage, cost_usd
from race_engineer.api.chat.runner import error_code
from race_engineer.api.chat.service import ChatConfig, ChatService, create_chat
from race_engineer.api.chat.tools import ChartSink, ToolRecord
from race_engineer.api.routes.chat import router
from race_engineer.api.settings import Settings
from race_engineer.tools import registry
from race_engineer.tools.definitions import TOOLS
from race_engineer.tools.params import Event, ToolParams, Year
from race_engineer.tools.registry import DataPaths, ToolOutput, ToolSpec
from race_engineer.tools.sessions import SessionInfo, ToolInputError, list_sessions
from race_engineer.tools.synthetic import sample_comparison_session, write_session

MARKER = "CHART-ONLY-7f3a"  # in chart data, never in a summary


@pytest.fixture(autouse=True)
def empty_result_cache() -> None:
    registry.RESULTS.clear()


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> DataPaths:
    root = tmp_path_factory.mktemp("chat")
    (root / "processed").mkdir()
    (root / "results").mkdir()
    write_session(sample_comparison_session(), root / "processed")
    return DataPaths(root / "processed", root / "results")


class EchoParams(ToolParams):
    event: Event
    year: Year = None


def echo_run(p: EchoParams, paths: DataPaths) -> ToolOutput:
    if p.year == 2025:
        raise ToolInputError("No 2025 Sample Grand Prix in the data; it ran in 2026.")
    return ToolOutput(f"Echo for {p.event}.", {"marker": MARKER, "event": p.event})


def slow_run(p: EchoParams, paths: DataPaths) -> ToolOutput:
    time.sleep(0.6)
    return ToolOutput("slow")


ECHO = ToolSpec(
    name="get_race_summary",  # a name the scripted rules pick ("summarise", "safety car")
    title="Echo",
    description="Echo the event.",
    params=EchoParams,
    run=echo_run,
    chart="race-summary",
)


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """(event, data) pairs from an SSE body; comments (keep-alives) are skipped."""
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


class Chat:
    """An app with only the chat route, answered by a scripted Claude."""

    def __init__(
        self,
        paths: DataPaths,
        specs: tuple[ToolSpec, ...] = TOOLS,
        config: ChatConfig | None = None,
    ) -> None:
        self.claude = ScriptedClaude(catalog=lambda: list_sessions(root=paths.processed))
        service = create_chat(
            config or ChatConfig(mode="fake"), specs, paths, client=scripted_client(self.claude)
        )
        assert service is not None
        self.service: ChatService = service
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.state.chat = service
        self.client = TestClient(self.app)

    def ask(
        self, message: str, done: dict[str, Any] | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        body: dict[str, Any] = {"message": message}
        if done is not None:
            body |= {"history": done["history"], "signature": done["signature"]}
        response = self.client.post("/api/chat", json=body)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(response.text)
        assert events[-1][0] == "done"
        return events


def names(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Event names with runs of text deltas collapsed to one."""
    out: list[str] = []
    for name, _ in events:
        if not (name == "text" and out and out[-1] == "text"):
            out.append(name)
    return out


def of(events: list[tuple[str, dict[str, Any]]], name: str) -> list[dict[str, Any]]:
    return [data for n, data in events if n == name]


def text_of(events: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(d["delta"] for d in of(events, "text"))


COMPARE = "Where did BBB gain on AAA in Sample qualifying 2026?"


def test_events_come_in_order_with_the_chart(paths: DataPaths) -> None:
    chat = Chat(paths)
    events = chat.ask(COMPARE)
    assert names(events) == ["status", "text", "tool_call", "tool_result", "text", "done"]
    call = of(events, "tool_call")[0]
    assert call["name"] == "compare_laps"
    assert call["input"] == {
        "event": "sample",
        "driver_a": "BBB",
        "driver_b": "AAA",
        "year": 2026,
        "session": "Q",
    }
    result = of(events, "tool_result")[0]
    assert result["id"] == call["id"] and not result["is_error"]
    assert result["summary"].startswith("2026 Sample Grand Prix Qualifying")
    assert result["chart"]["bundle"] == "compare-laps"
    assert result["chart"]["resource_uri"] == "ui://race-engineer/compare-laps.html"
    assert isinstance(result["chart"]["data"], dict) and result["chart"]["data"]
    assert SCRIPTED_NOTE in text_of(events)
    done = of(events, "done")[0]
    assert done["questions_used"] == 1 and done["questions_left"] == 7
    assert done["stop_reason"] == "end_turn" and done["model"] == "scripted"
    assert done["signature"].startswith(chat.service.prompt_version + ".")
    assert [m["role"] for m in done["history"]] == ["user", "assistant", "user", "assistant"]


def test_request_bodies_follow_the_plan(paths: DataPaths) -> None:
    chat = Chat(paths)
    chat.ask(COMPARE)
    assert len(chat.claude.requests) == 2
    first_tools = chat.claude.requests[0].body["tools"]
    for request in chat.claude.requests:
        body = request.body
        assert body["model"] == "claude-sonnet-5-5"
        assert body["max_tokens"] == 16000
        assert body["output_config"] == {"effort": "medium"}
        assert body["thinking"] == {"type": "adaptive", "display": "updates"}
        assert body["fallbacks"] == "default"
        assert set(request.betas) >= {
            "server-side-fallback-2026-07-01",
            "thinking-display-updates-2026-08-18",
        }
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "Race Engineer AI" in body["system"][0]["text"]
        assert body["cache_control"] == {"type": "ephemeral"}
        assert body.get("tool_choice", {"type": "auto"}) == {"type": "auto"}
        assert [t["name"] for t in body["tools"]] == [s.name for s in TOOLS]
        assert all(t["eager_input_streaming"] is True for t in body["tools"])
        assert body["tools"] == first_tools  # byte-identical on every request
        assert body["tools"] == chat.service.tool_definitions
    # The tool definitions are the specs' own descriptions and schemas.
    spec = TOOLS[-1]
    assert first_tools[-1]["description"] == spec.description
    assert first_tools[-1]["input_schema"] == spec.input_schema()


def test_chart_data_never_reaches_the_model(paths: DataPaths) -> None:
    chat = Chat(paths, specs=(ECHO,))
    events = chat.ask("Summarise the 2026 Sample Grand Prix.")
    result = of(events, "tool_result")[0]
    assert result["chart"]["data"]["marker"] == MARKER
    assert result["summary"] == "Echo for sample grand prix."
    for request in chat.claude.requests:
        assert MARKER not in json.dumps(request.body)
    assert MARKER not in json.dumps(of(events, "done")[0]["history"])


def test_the_history_is_append_only(paths: DataPaths) -> None:
    chat = Chat(paths)
    first = chat.ask(COMPARE)
    done = of(first, "done")[0]
    history = done["history"]
    # Within the question, the second call starts with the first call's messages.
    requests = chat.claude.requests
    assert requests[1].body["messages"] == history[:3]
    thinking = history[1]["content"][0]
    assert thinking["type"] == "thinking" and thinking["signature"] == SIGNATURE
    assert thinking["thinking"] == "Checking sample"  # unchanged, as streamed

    second = chat.ask("And what about turn 3 on lap 9 for BBB?", done)
    assert of(second, "done")[0]["questions_used"] == 2
    later = chat.claude.requests[2].body["messages"]
    assert later[: len(history)] == history
    assert later[len(history)] == {
        "role": "user",
        "content": "And what about turn 3 on lap 9 for BBB?",
    }
    # Everything from the first answer is still there, unchanged, in the second answer.
    assert of(second, "done")[0]["history"][: len(history)] == history


def test_usage_adds_up_and_the_cache_is_read_from_the_second_call(paths: DataPaths) -> None:
    chat = Chat(paths)
    done = of(chat.ask(COMPARE), "done")[0]
    reported = [r.usage for r in chat.claude.requests]
    assert done["usage"] == {
        "input": sum(u["input_tokens"] for u in reported),
        "output": sum(u["output_tokens"] for u in reported),
        "cache_read": sum(u["cache_read_input_tokens"] for u in reported),
        "cache_write": sum(u["cache_creation_input_tokens"] for u in reported),
    }
    assert reported[0]["cache_read_input_tokens"] == 0
    assert all(u["cache_read_input_tokens"] > 0 for u in reported[1:])
    assert done["cost_usd"] > 0
    # The next question reads the whole conversation so far from the cache.
    chat.ask("Summarise it.", done)
    assert (
        chat.claude.requests[2].usage["cache_read_input_tokens"]
        > reported[1]["cache_read_input_tokens"]
    )


def test_a_refusal_runs_no_tools_and_leaves_the_history(paths: DataPaths) -> None:
    chat = Chat(paths)
    chat.claude.queue("refusal")
    events = chat.ask(COMPARE)
    assert names(events) == ["refusal", "done"]
    assert of(events, "refusal")[0]["category"] == "cyber"
    done = of(events, "done")[0]
    assert done["history"] == [] and done["questions_used"] == 0
    assert done["stop_reason"] == "refusal"
    assert len(chat.claude.requests) == 1


def test_a_cut_off_tool_call_is_not_run(paths: DataPaths) -> None:
    chat = Chat(paths, specs=(ECHO,))
    chat.claude.queue("max_tokens")
    events = chat.ask("Summarise the 2026 Sample Grand Prix.")
    assert "tool_call" not in names(events)
    assert of(events, "error") == [
        {"code": "truncated", "message": of(events, "error")[0]["message"]}
    ]
    assert of(events, "done")[0]["history"] == []
    assert len(chat.claude.requests) == 1


def test_invalid_arguments_go_back_as_an_error_and_the_model_continues(paths: DataPaths) -> None:
    chat = Chat(paths)
    events = chat.ask("Where did BBB gain in Sample qualifying 2026?")  # one driver only
    result = of(events, "tool_result")[0]
    assert result["is_error"] and result["chart"] is None
    assert result["summary"].startswith("Invalid arguments for compare_laps: driver_b: required")
    assert len(chat.claude.requests) == 2
    sent = chat.claude.requests[1].body["messages"][-1]["content"][0]
    assert sent["is_error"] is True and sent["type"] == "tool_result"
    assert "returned an error" in text_of(events)
    assert names(events)[-2:] == ["text", "done"]


def test_a_tool_error_from_the_data_is_relayed(paths: DataPaths) -> None:
    chat = Chat(paths, specs=(ECHO,))
    events = chat.ask("Summarise the 2025 Sample Grand Prix.")
    result = of(events, "tool_result")[0]
    assert result["is_error"] and result["chart"] is None
    assert result["summary"] == "No 2025 Sample Grand Prix in the data; it ran in 2026."
    sent = chat.claude.requests[1].body["messages"][-1]["content"][0]
    assert sent["content"] == result["summary"] and sent["is_error"] is True


def test_unreadable_tool_input_is_asked_again_once(paths: DataPaths) -> None:
    chat = Chat(paths)
    chat.claude.queue("broken_input")
    events = chat.ask(COMPARE)
    assert names(events)[:4] == ["status", "retry", "status", "text"]
    assert of(events, "tool_result")[0]["chart"]["bundle"] == "compare-laps"
    requests = chat.claude.requests
    assert len(requests) == 3
    assert requests[1].body["messages"] == requests[0].body["messages"]
    done = of(events, "done")[0]
    assert done["questions_used"] == 1
    # The unreadable call is billed too, so its input counts.
    reported = [r.usage for r in requests]
    assert done["usage"]["input"] == sum(u["input_tokens"] for u in reported)
    assert done["usage"]["cache_write"] == sum(u["cache_creation_input_tokens"] for u in reported)

    chat.claude.queue("broken_input", "broken_input")
    events = chat.ask(COMPARE)
    assert of(events, "error")[0]["code"] == "internal_error"
    assert of(events, "done")[0]["history"] == []


@pytest.mark.parametrize(
    ("directive", "code"), [("rate_limit", "upstream_busy"), ("overloaded", "upstream_unavailable")]
)
def test_upstream_errors_become_error_events(paths: DataPaths, directive: str, code: str) -> None:
    chat = Chat(paths)
    chat.claude.queue(directive)
    events = chat.ask(COMPARE)
    assert names(events) == ["error", "done"]
    assert of(events, "error")[0]["code"] == code
    assert of(events, "done")[0]["history"] == []


def test_an_error_in_the_middle_of_a_stream_is_reported_and_counted(paths: DataPaths) -> None:
    # An overloaded API can answer 200 and send an `error` event after some text.
    usage = {"input_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 9}
    partial = sse_stream([{"type": "text", "text": "Checking that now."}], "end_turn", usage)
    cut = partial.decode().split("event: content_block_stop")[0]
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    body = (cut + sse_event("error", error)).encode()

    def overloaded(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    client = anthropic.AsyncAnthropic(
        api_key="test",
        base_url="http://scripted.invalid",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(overloaded)),
    )
    service = create_chat(ChatConfig(mode="fake"), TOOLS, paths, client=client)
    app = FastAPI()
    app.include_router(router)
    app.state.chat = service
    events = parse_sse(TestClient(app).post("/api/chat", json={"message": COMPARE}).text)
    assert names(events) == ["text", "error", "done"]
    assert of(events, "error")[0]["code"] == "upstream_unavailable"
    done = of(events, "done")[0]
    assert done["history"] == [] and done["questions_used"] == 0
    assert done["usage"]["input"] == 50 and done["usage"]["cache_write"] == 9


def test_a_mid_answer_fallback_drops_the_declined_part(paths: DataPaths) -> None:
    chat = Chat(paths)
    chat.claude.queue("fallback")
    events = chat.ask(COMPARE)
    calls = of(events, "tool_call")
    assert [c["name"] for c in calls] == ["compare_laps"]
    assert not calls[0]["id"].endswith("_7")  # the declined, cut-off call never ran
    assert of(events, "tool_result")[0]["chart"]["bundle"] == "compare-laps"
    history = of(events, "done")[0]["history"]
    turn = [b["type"] for b in history[1]["content"]]
    assert turn == ["text", "fallback", "thinking", "text", "tool_use"]
    fallback = history[1]["content"][1]
    assert fallback["from"]["model"] == "claude-sonnet-5-5"  # sent back under its API name
    # The next request (a new runner) starts from exactly that history.
    assert chat.claude.requests[1].body["messages"] == history[:3]
    # Each attempt is priced: the usage sums the iterations.
    assert of(events, "done")[0]["usage"]["output"] > 0


def test_the_step_limit_keeps_the_completed_steps(paths: DataPaths) -> None:
    chat = Chat(paths, config=ChatConfig(mode="fake", max_iterations=1))
    events = chat.ask(COMPARE)
    assert names(events)[-3:] == ["tool_result", "error", "done"]
    assert of(events, "error")[0]["code"] == "step_limit"
    done = of(events, "done")[0]
    assert [m["role"] for m in done["history"]] == ["user", "assistant", "user"]
    assert done["questions_used"] == 1
    assert len(chat.claude.requests) == 1


def test_a_question_that_runs_too_long_times_out(paths: DataPaths) -> None:
    slow = ToolSpec(
        name="get_race_summary",
        title="Slow",
        description="Slow.",
        params=EchoParams,
        run=slow_run,
    )
    chat = Chat(paths, specs=(slow,), config=ChatConfig(mode="fake", timeout_s=0.3))
    events = chat.ask("Summarise the 2026 Sample Grand Prix.")
    # The tool runs in a worker thread, which can't be cancelled: it finishes, its result is
    # kept, and the question stops there instead of asking Claude again.
    assert names(events)[-3:] == ["tool_result", "error", "done"]
    assert of(events, "error")[0]["code"] == "timeout"
    done = of(events, "done")[0]
    assert [m["role"] for m in done["history"]] == ["user", "assistant", "user"]
    assert len(chat.claude.requests) == 1
    assert chat.service.slots.used == 0


def test_a_quiet_stream_gets_keep_alive_comments(
    paths: DataPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fastapi.routing._PING_INTERVAL", 0.05)  # 15 s in production
    slow = ToolSpec(
        name="get_race_summary", title="Slow", description="Slow.", params=EchoParams, run=slow_run
    )
    chat = Chat(paths, specs=(slow,))
    response = chat.client.post("/api/chat", json={"message": "Summarise the 2026 Sample GP."})
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
    assert ": ping" in response.text
    assert parse_sse(response.text)[-1][0] == "done"


def test_a_loose_session_is_found_first(paths: DataPaths) -> None:
    chat = Chat(paths)  # every tool, find_session first
    events = chat.ask("Where did BBB gain on AAA in the last qualifying?")
    calls = [c["name"] for c in of(events, "tool_call")]
    assert calls == ["find_session", "compare_laps"]
    assert of(events, "tool_call")[0]["input"] == {"recent": 0, "session": "Q"}
    # The session on find_session's "Use event=..." line.
    assert of(events, "tool_call")[1]["input"] == {
        "event": "Sample Grand Prix",
        "driver_a": "BBB",
        "driver_b": "AAA",
        "year": 2026,
        "session": "Q",
    }
    assert not of(events, "tool_result")[1]["is_error"]


@pytest.mark.parametrize(
    ("question", "fields"),
    [
        ("Where did AAA make mistakes in the last race?", {"recent": 0, "session": "R"}),
        ("Who won the race before last?", {"recent": 1, "session": "R"}),
        ("Any mistakes 2 races ago?", {"recent": 1, "session": "R"}),  # the race before last
        ("Any mistakes 3 races ago?", {"recent": 2, "session": "R"}),
        ("Tell us about the latest sprint", {"recent": 0, "session": "S"}),
        ("Show us round 5 2025", {"round": 5, "year": 2025}),
        ("Who won round 1?", {"round": 1}),
        # "last year" counts from the newest processed season: the scripted model has no date.
        ("Last year's Sample GP race", {"event": "sample", "year": 2025, "session": "R"}),
        ("Tell us about the Sample GP", {"event": "sample"}),
    ],
)
def test_scripted_find_session_fields(question: str, fields: dict[str, Any]) -> None:
    """The scripted model fills find_session's fields from the simple ways of naming a session
    without its event, rather than answering every one with the latest session."""
    sessions = [
        SessionInfo(year, 1, "R", "Sample Grand Prix", "Synthetic Circuit", "Race", date, 5e3, 5)
        for year, date in ((2025, "2025-03-16"), (2026, "2026-03-08"))
    ]
    claude = ScriptedClaude(catalog=lambda: sessions)
    turn = claude._next_turn([{"role": "user", "content": question}], ["find_session"])
    assert turn.calls == [("find_session", fields)]


def test_the_follow_up_uses_the_session_find_session_matched() -> None:
    """After find_session, the scripted model calls the tool with the match's "Use event=..."
    line, not with the first or longest event name anywhere in the answer (the last line names
    the newest session), and calls nothing more when there was no single match."""
    sessions = [
        SessionInfo(2026, 1, "R", "Sample Grand Prix", "Synthetic Circuit", "Race", "", 5e3, 5),
        SessionInfo(2026, 2, "R", "Azerbaijan Grand Prix", "Baku", "Race", "", 5e3, 5),
    ]
    claude = ScriptedClaude(catalog=lambda: sessions)
    question = {"role": "user", "content": "Where did AAA make mistakes in the race before last?"}
    call = {"type": "tool_use", "id": "toolu_1", "name": "find_session", "input": {}}

    def after(summary: str) -> list[tuple[str, dict[str, Any]]]:
        result = {"type": "tool_result", "tool_use_id": "toolu_1", "content": summary}
        messages = [
            question,
            {"role": "assistant", "content": [call]},
            {"role": "user", "content": [result]},
        ]
        return claude._next_turn(messages, ["find_session", "find_mistakes"]).calls

    matched = (
        "2026 Sample Grand Prix, Race (R), 2026-03-08, Synthetic Circuit: round 1. That "
        "weekend: Sprint Qualifying (SQ), Qualifying (Q), Race (R).\n"
        "Use event='Sample Grand Prix', year=2026, session='R' in other tools.\n"
        "The data ends with the 2026 Azerbaijan Grand Prix, Race (2026-09-26)."
    )
    assert after(matched) == [
        (
            "find_mistakes",
            {"event": "Sample Grand Prix", "driver": "AAA", "year": 2026, "session": "R"},
        )
    ]
    assert after("No single session matches: ... Candidates, best match first: ...") == []


def test_no_tool_for_questions_the_data_cannot_answer(paths: DataPaths) -> None:
    chat = Chat(paths)
    events = chat.ask("Who will win the 2027 championship?")
    assert names(events) == ["text", "done"]
    assert "can't answer that from the data" in text_of(events)


def test_disabled_and_busy_chats_answer_with_json(paths: DataPaths) -> None:
    chat = Chat(paths, config=ChatConfig(mode="fake", concurrency=1))
    assert chat.service.slots.try_acquire()
    response = chat.client.post("/api/chat", json={"message": COMPARE})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "busy"
    chat.service.slots.release()

    chat.app.state.chat = None
    response = chat.client.post("/api/chat", json={"message": COMPARE})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "chat_disabled"


@pytest.mark.anyio
async def test_stopping_early_closes_the_runner_and_frees_the_slot(paths: DataPaths) -> None:
    chat = Chat(paths)
    events = chat.service.answer([], COMPARE)
    first = await anext(events)
    assert first.event == "status"
    assert chat.service.slots.used == 1
    await events.aclose()  # what a client disconnect does to the stream
    assert chat.service.slots.used == 0


@pytest.mark.anyio
async def test_a_client_that_hangs_up_frees_its_slot(
    paths: DataPaths, caplog: pytest.LogCaptureFixture
) -> None:
    # FastAPI only cancels the task reading the endpoint's generator when the client goes, which
    # leaves the generator suspended; the route closes the answer itself when the request ends.
    chat = Chat(paths)
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

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    with caplog.at_level(logging.INFO, "race_engineer.api.chat.runner"), anyio.fail_after(10):
        await chat.app(scope, receive, send)
    assert first_event.is_set()
    assert chat.service.slots.used == 0
    assert "outcome=disconnected" in caplog.text


def test_behind_another_base_url_fallbacks_and_eager_streaming_are_off(paths: DataPaths) -> None:
    claude = ScriptedClaude(catalog=lambda: list_sessions(root=paths.processed))
    service = create_chat(ChatConfig(mode="auto"), TOOLS, paths, client=scripted_client(claude))
    assert service is not None and service.mode == "anthropic"
    assert not service.fallbacks and not service.eager
    assert service.describe() == {
        "mode": "anthropic",
        "model": "claude-sonnet-5-5",
        "base_url_host": "scripted.invalid",
    }
    app = FastAPI()
    app.include_router(router)
    app.state.chat = service
    TestClient(app).post("/api/chat", json={"message": COMPARE})
    body = claude.requests[0].body
    assert "fallbacks" not in body
    assert claude.requests[0].betas == ["thinking-display-updates-2026-08-18"]
    assert all("eager_input_streaming" not in t for t in body["tools"])

    on = ChatConfig(mode="auto", fallbacks=True, eager=True)
    forced = create_chat(on, TOOLS, paths, client=scripted_client(claude))
    assert forced is not None and forced.fallbacks and forced.eager


def test_the_api_app_answers_with_the_client_its_lifespan_started(paths: DataPaths) -> None:
    claude = ScriptedClaude(catalog=lambda: list_sessions(root=paths.processed))
    settings = Settings(
        processed=paths.processed, results=paths.results, chat="fake", mount_mcp=False
    )
    app = create_app(settings, anthropic_client=scripted_client(claude))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["chat"]["mode"] == "fake"
        events = parse_sse(client.post("/api/chat", json={"message": COMPARE}).text)
        assert names(events) == ["status", "text", "tool_call", "tool_result", "text", "done"]
        assert of(events, "tool_result")[0]["chart"]["bundle"] == "compare-laps"
        service = app.state.chat_service
        assert isinstance(service, ChatService) and service.mode == "fake"
        assert service.secret == settings.chat_secret and not service.owns_client
        done = of(events, "done")[0]
        again = parse_sse(
            client.post(
                "/api/chat",
                json={"message": "Summarise it.", **{k: done[k] for k in ("history", "signature")}},
            ).text
        )
        assert of(again, "done")[0]["questions_used"] == 2
        assert app.state.chat_service is service  # built once per client

    off = Settings(processed=paths.processed, results=paths.results, chat="off", mount_mcp=False)
    with TestClient(create_app(off)) as client:
        response = client.post("/api/chat", json={"message": COMPARE})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "chat_disabled"


def test_create_chat_modes(paths: DataPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    assert create_chat(ChatConfig(mode="off"), TOOLS, paths) is None
    fake = create_chat(ChatConfig(mode="fake"), TOOLS, paths)
    assert fake is not None and fake.describe()["mode"] == "fake"
    assert fake.scripted is not None and fake.describe()["model"] == "scripted"
    # auto without credentials: off (the client is built only here, never at import).
    monkeypatch.setattr("race_engineer.api.chat.service.has_credentials", lambda client: False)
    assert create_chat(ChatConfig(mode="auto"), TOOLS, paths) is None


def test_config_from_the_environment() -> None:
    # Read as the API reads it (api/settings.py).
    proxied = {"ANTHROPIC_BASE_URL": "http://proxy.local", "RACE_ENGINEER_CHAT_EAGER": "1"}
    config = ChatConfig.from_env(
        proxied | {"RACE_ENGINEER_CHAT_MAX_TURNS": "3", "RACE_ENGINEER_CHAT_SECRET": "s3cret"}
    )
    assert config.mode == "auto" and config.max_turns == 3 and config.secret == b"s3cret"
    assert config.fallbacks is False and config.eager is True  # behind a proxy unless set
    assert config.model == "claude-sonnet-5-5" and config.effort == "medium"
    assert ChatConfig.from_env({"RACE_ENGINEER_CHAT_FAKE": "1"}).mode == "fake"
    direct = ChatConfig.from_env({})
    assert direct.mode == "auto" and direct.fallbacks and direct.eager
    with pytest.raises(ValueError, match="RACE_ENGINEER_CHAT"):
        ChatConfig.from_env({"RACE_ENGINEER_CHAT": "maybe"})


def test_the_scripted_reader_finds_sessions_and_drivers(paths: DataPaths) -> None:
    reader = Reader(lambda: list_sessions(root=paths.processed))
    asked = reader.read("Where did Norris gain on PIA in Sample quali 2026, turn 4 on lap 12?")
    assert asked.event == "sample"
    assert asked.drivers == ["NOR", "PIA"]
    assert (asked.year, asked.session, asked.lap, asked.turn) == (2026, "Q", 12, "4")
    assert reader.read("the Synthetic Circuit sprint qualifying").session == "SQ"
    assert reader.read("nothing here").event is None


def test_the_chart_sink_pairs_calls_by_name_and_arguments() -> None:
    sink = ChartSink()
    sink.record(ToolRecord("a", {"x": 1}, error="bad"))
    sink.record(ToolRecord("a", {"x": 2}, output=ToolOutput("ok")))

    class Block:
        def __init__(self, name: str, input: Any) -> None:
            self.name, self.input = name, input

    found = sink.take([Block("a", "not an object"), Block("a", {"x": 2}), Block("a", {"x": 1})])
    assert found[0] is None
    assert found[1] is not None and found[1].output is not None
    assert found[2] is not None and found[2].error == "bad"


def test_usage_is_priced_per_attempt() -> None:
    class Attempt:
        def __init__(self, kind: str, model: str, n: int) -> None:
            self.type, self.model = kind, model
            self.input_tokens = self.output_tokens = n
            self.cache_read_input_tokens = self.cache_creation_input_tokens = 0

    class Reported:
        input_tokens, output_tokens = 1, 1
        cache_read_input_tokens = cache_creation_input_tokens = 0

        def __init__(self) -> None:
            self.iterations = [
                Attempt("message", "claude-sonnet-5-5", 100),
                Attempt("fallback_message", "claude-sonnet-5", 200),
            ]

    usage = Usage()
    usage.add(Reported(), "claude-sonnet-5-5")
    assert (usage.input, usage.output) == (300, 300)
    assert usage.cost_usd == pytest.approx(300 * 2 / 1e6 + 300 * 10 / 1e6)
    assert cost_usd(0, 0, 1_000_000, 1_000_000, model="claude-sonnet-5-5") == pytest.approx(2.70)


def test_api_errors_map_to_event_codes() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(code: int, body: Any = None) -> anthropic.APIStatusError:
        response = httpx2.Response(code, request=request)
        return anthropic.APIStatusError("error", response=response, body=body)

    assert error_code(status(429)) == "upstream_busy"
    assert error_code(status(529)) == "upstream_unavailable"
    assert error_code(status(500)) == "upstream_unavailable"
    assert error_code(status(401)) == "chat_not_configured"
    assert error_code(status(400)) == "bad_request"
    # An `error` event in the middle of a 200 stream carries its type in the body.
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    assert error_code(status(200, overloaded)) == "upstream_unavailable"
    limited = {"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}
    assert error_code(status(200, limited)) == "upstream_busy"
    assert error_code(anthropic.APIConnectionError(request=request)) == "upstream_unreachable"
    assert error_code(anthropic.APITimeoutError(request=request)) == "timeout"


def test_keep_alive_comments_are_ignored_by_the_parser() -> None:
    assert parse_sse(': ping\n\nevent: text\ndata: {"delta": "a"}\n\n') == [
        ("text", {"delta": "a"})
    ]


def test_nothing_is_built_at_import() -> None:
    # Importing the chat builds no Anthropic client: that happens in create_chat, at startup.
    code = (
        "import gc, anthropic\n"
        "import race_engineer.api.routes.chat, race_engineer.api.chat.fake\n"
        "print(sum(isinstance(o, anthropic.AsyncAnthropic) for o in gc.get_objects()))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "0"
