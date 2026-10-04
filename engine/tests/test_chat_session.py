"""Histories held by the browser: signing, the checks before a question is answered (signature,
prompt version, question cap, size) and how the chat route reports them."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from race_engineer.api.chat.fake import ScriptedClaude, scripted_client
from race_engineer.api.chat.prompt import SYSTEM_PROMPT, prompt_version
from race_engineer.api.chat.service import ChatConfig, ChatService, create_chat
from race_engineer.api.chat.session import (
    MAX_HISTORY_BYTES,
    ChatRejected,
    canonical,
    check_history,
    count_questions,
    sign,
    verify,
)
from race_engineer.api.routes.chat import router
from race_engineer.tools.definitions import TOOLS
from race_engineer.tools.registry import DataPaths

SECRET = b"test-secret"
VERSION = "abcdef123456"


def exchange(question: str) -> list[dict[str, Any]]:
    """A question answered after one tool call, as the chat records it."""
    return [
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "t1", "name": "find_mistakes", "input": {"lap": 2}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Lap 2, turn 4"}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Turn 4 on lap 2."}]},
    ]


def test_a_signed_history_verifies() -> None:
    history = exchange("Where did LEC lose time?")
    signature = sign(history, SECRET, VERSION)
    assert signature.startswith(VERSION + ".")
    verify(history, signature, SECRET, VERSION)
    verify([], None, SECRET, VERSION)  # a new conversation needs no signature


def test_one_changed_byte_is_refused() -> None:
    history = exchange("Where did LEC lose time?")
    signature = sign(history, SECRET, VERSION)
    forged = json.loads(json.dumps(history))
    forged[2]["content"][0]["content"] = "Lap 2, turn 5"
    with pytest.raises(ChatRejected) as caught:
        verify(forged, signature, SECRET, VERSION)
    assert (caught.value.status, caught.value.code) == (400, "invalid_history")
    for bad in (None, "", "no-dot", VERSION + ".", sign(history, b"other", VERSION)):
        with pytest.raises(ChatRejected, match="start a new"):
            verify(history, bad, SECRET, VERSION)


def test_a_history_from_an_older_prompt_has_expired() -> None:
    history = exchange("Where did LEC lose time?")
    old = sign(history, SECRET, "000000000000")
    with pytest.raises(ChatRejected) as caught:
        verify(history, old, SECRET, VERSION)
    assert (caught.value.status, caught.value.code) == (409, "conversation_expired")
    # A made-up version with a wrong digest is a forgery, not an expired conversation.
    with pytest.raises(ChatRejected) as caught:
        verify(history, "000000000000." + "0" * 64, SECRET, VERSION)
    assert caught.value.status == 400


def test_the_signature_survives_the_browsers_json() -> None:
    history = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t", "name": "x", "input": {"lap": 2.0, "gap": 0.25}}
            ],
        },
    ]
    signature = sign(history, SECRET, VERSION)
    # JavaScript writes 2.0 as 2; key order may change too.
    round_trip = json.loads(json.dumps(history).replace("2.0", "2"))
    round_trip[1]["content"][0] = dict(reversed(list(round_trip[1]["content"][0].items())))
    verify(round_trip, signature, SECRET, VERSION)
    assert canonical(history) == canonical(round_trip)


def test_questions_are_counted_without_tool_results() -> None:
    history = exchange("one") + exchange("two")
    assert count_questions(history) == 2
    assert count_questions([{"role": "user", "content": [{"type": "text", "text": "q"}]}]) == 1
    assert count_questions([]) == 0


def test_the_checks_before_answering() -> None:
    history = [m for i in range(8) for m in exchange(f"question {i}")]
    signature = sign(history, SECRET, VERSION)
    assert (
        check_history(
            history[:4],
            sign(history[:4], SECRET, VERSION),
            secret=SECRET,
            version=VERSION,
            max_turns=8,
        )
        == 1
    )
    with pytest.raises(ChatRejected) as caught:
        check_history(history, signature, secret=SECRET, version=VERSION, max_turns=8)
    assert (caught.value.status, caught.value.code) == (409, "turn_limit")
    big = [{"role": "user", "content": "x" * (MAX_HISTORY_BYTES + 1)}]
    with pytest.raises(ChatRejected) as caught:
        check_history(big, sign(big, SECRET, VERSION), secret=SECRET, version=VERSION, max_turns=8)
    assert (caught.value.status, caught.value.code) == (413, "history_too_large")


def test_the_prompt_version_follows_the_prompt_tools_and_model() -> None:
    tools = [{"name": "a", "description": "A.", "input_schema": {"type": "object"}}]
    base = prompt_version("claude-sonnet-5-5", tools)
    assert len(base) == 12 and base == prompt_version("claude-sonnet-5-5", tools)
    assert base != prompt_version("scripted", tools)
    assert base != prompt_version("claude-sonnet-5-5", [*tools, *tools])
    assert base != prompt_version("claude-sonnet-5-5", tools, SYSTEM_PROMPT + " ")
    eager = [{**tools[0], "eager_input_streaming": True}]
    assert base != prompt_version("claude-sonnet-5-5", eager)


# The same checks through the route: plain JSON errors, never a stream.


@pytest.fixture
def route(tmp_path) -> tuple[TestClient, ChatService]:
    paths = DataPaths(tmp_path / "processed", tmp_path / "results")
    config = ChatConfig(mode="fake", secret=SECRET)
    service = create_chat(config, TOOLS, paths, client=scripted_client(ScriptedClaude()))
    assert service is not None
    app = FastAPI()
    app.include_router(router)
    app.state.chat = service
    return TestClient(app), service


def post(client: TestClient, **body: Any) -> tuple[int, dict[str, Any]]:
    response = client.post("/api/chat", json={"message": "Next question?", **body})
    assert response.headers["content-type"].startswith("application/json")
    return response.status_code, response.json()


def test_the_route_refuses_bad_histories(route: tuple[TestClient, ChatService]) -> None:
    client, service = route
    history = exchange("Where did LEC lose time?")
    good = sign(history, SECRET, service.prompt_version)

    forged = json.loads(json.dumps(history))
    forged[3]["content"][0]["text"] = "Turn 5 on lap 2."
    assert post(client, history=forged, signature=good)[1]["error"]["code"] == "invalid_history"
    status, body = post(client, history=history)  # unsigned
    assert status == 400 and body["error"]["code"] == "invalid_history"

    expired = sign(history, SECRET, "000000000000")
    status, body = post(client, history=history, signature=expired)
    assert status == 409 and body["error"]["code"] == "conversation_expired"

    full = [m for i in range(8) for m in exchange(f"question {i}")]
    status, body = post(client, history=full, signature=sign(full, SECRET, service.prompt_version))
    assert status == 409 and body["error"]["code"] == "turn_limit"
    assert "8 questions" in body["error"]["message"]

    big = [{"role": "user", "content": "x" * (MAX_HISTORY_BYTES + 1)}]
    status, body = post(client, history=big, signature=sign(big, SECRET, service.prompt_version))
    assert status == 413 and body["error"]["code"] == "history_too_large"


def test_the_route_validates_the_message(route: tuple[TestClient, ChatService]) -> None:
    client, _ = route
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": " \n\t"}).status_code == 422  # blank
    assert client.post("/api/chat", json={"message": "x" * 2001}).status_code == 422


def test_the_ninth_question_is_refused(route: tuple[TestClient, ChatService]) -> None:
    client, _ = route
    done: dict[str, Any] = {"history": [], "signature": None}
    for i in range(8):
        response = client.post(
            "/api/chat",
            json={
                "message": f"Which races are there? ({i})",
                "history": done["history"],
                "signature": done["signature"],
            },
        )
        assert response.status_code == 200
        done = json.loads(response.text.rsplit("data: ", 1)[1])
        assert done["questions_used"] == i + 1
    assert done["questions_left"] == 0
    response = client.post(
        "/api/chat",
        json={"message": "One more?", "history": done["history"], "signature": done["signature"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "turn_limit"
