"""Asking the questions: the API in this process with the scripted chat, or a running API, and
the run that asks every conversation in order and checks each answer.

`in_process()` builds the app with `RACE_ENGINEER_CHAT=fake` whatever the environment says,
and refuses to go on if the health check reports any other chat: the shell this runs in may set
ANTHROPIC_BASE_URL and resolve credentials, so `auto` could call a real model through it. There
is deliberately no way to ask a real model in-process. `remote(url)` talks to a running API
over HTTP (a real model only from the user's own terminal, M7 plan section 17).

Both go through the API's own routes: GET /api/health (the chat's mode, the data), GET
/api/tools (the tool definitions, evidence for grounding), GET /api/tools/find_session (the
newest processed race, for "latest:R" and names_latest) and POST /api/chat.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import httpx2

from race_engineer.api.chat.prompt import SYSTEM_PROMPT
from race_engineer.chat_eval.checks import (
    Context,
    ConversationState,
    Events,
    QuestionResult,
    SessionRef,
    Turn,
    summarise_calls,
    turn_from_events,
)
from race_engineer.chat_eval.grounding import evidence_from
from race_engineer.chat_eval.questions import Conversation, latest_codes

if TYPE_CHECKING:
    from race_engineer.api.settings import Settings

SCRIPTED_MODEL = "scripted"


class ChatUnavailable(RuntimeError):
    """The API can't be evaluated: it can't be reached, its chat is off, or an in-process chat
    isn't the scripted one."""


# Answers that say the chat itself isn't working (the server's Anthropic key was refused, or the
# chat is switched off), not that the model answered badly. The first one stops the run before
# anything is judged or written: a report of 15 failures would measure the key, not the chat.
NOT_RUN_CODES = frozenset({"chat_not_configured", "chat_disabled"})


def _not_run_code(turn: Turn) -> str | None:
    """The code of an answer that means the chat isn't working, or None."""
    codes = [str(e.get("code")) for e in turn.stream_errors]
    if turn.status != 200 and turn.error:
        codes.append(str((turn.error.get("error") or {}).get("code")))
    return next((code for code in codes if code in NOT_RUN_CODES), None)


class Api(Protocol):
    where: str
    effort: str | None

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]: ...

    def chat(self, body: dict[str, Any]) -> tuple[int, Events, dict[str, Any]]: ...


def parse_sse(lines: Iterator[str], *, complete: bool = True) -> Events:
    """(event, data) pairs from SSE lines; comments (keep-alives) are skipped. With
    `complete=False` (a stream cut off), an event the stream didn't finish is left out."""
    events: Events = []
    name, data = None, []
    for line in [*lines, ""] if complete else lines:
        if not line:
            if name is not None:
                events.append((name, json.loads("\n".join(data))))
            name, data = None, []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return events


def _json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


@dataclass
class _InProcess:
    client: Any  # fastapi.testclient.TestClient
    where: str
    effort: str | None

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        response = self.client.get(path, params=params)
        return response.status_code, _json(response)

    def chat(self, body: dict[str, Any]) -> tuple[int, Events, dict[str, Any]]:
        response = self.client.post("/api/chat", json=body)
        if response.status_code != 200:
            return response.status_code, [], _json(response) or {}
        return 200, parse_sse(iter(response.text.splitlines())), {}


def scripted_settings(settings: Settings | None = None) -> Settings:
    """`settings` (default: the environment's) with the scripted chat, the limits off and no
    /mcp. The environment is read with RACE_ENGINEER_CHAT=fake in it, so a value such as
    `auto` never gets as far as building a client."""
    from race_engineer.api.settings import Settings

    if settings is None:
        env = dict(os.environ) | {"RACE_ENGINEER_CHAT": "fake", "RACE_ENGINEER_LIMITS": "off"}
        settings = Settings.from_env(env)
    return dataclasses.replace(settings, chat="fake", limits="off", mount_mcp=False)


@contextmanager
def in_process(settings: Settings | None = None) -> Iterator[Api]:
    """The API app in this process with the scripted chat (its data from RACE_ENGINEER_DATA, or
    from `settings`)."""
    from fastapi.testclient import TestClient

    from race_engineer.api.app import create_app

    settings = scripted_settings(settings)
    app = create_app(settings)
    with TestClient(app) as client:
        api = _InProcess(client, "in-process", settings.chat_effort)
        status, health = api.get("/api/health")
        chat = (health or {}).get("chat") or {}
        if status != 200 or chat.get("mode") != "fake" or chat.get("model") != SCRIPTED_MODEL:
            raise ChatUnavailable(f"the in-process chat isn't the scripted one: {chat}")
        yield api


CONNECTION_LOST = "connection_lost"  # the code of the stream error a dropped connection becomes


@dataclass
class _Remote:
    client: httpx2.Client
    where: str
    effort: str | None = None  # the health check doesn't report it

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        try:
            response = self.client.get(path, params=params)
        except httpx2.TransportError as exc:  # not running, or gone: the run can't start
            raise ChatUnavailable(f"can't reach {self.where} ({exc})") from None
        return response.status_code, _json(response)

    def chat(self, body: dict[str, Any]) -> tuple[int, Events, dict[str, Any]]:
        """The answer's events. A connection that fails or drops (the API stopped, a network
        blip, no byte for 180 s) ends the answer with a `connection_lost` stream error, so the
        run goes on and the report still gets written, with the answer counted as a problem."""
        lines: list[str] = []
        try:
            with self.client.stream("POST", "/api/chat", json=body) as response:
                if response.status_code != 200:
                    response.read()
                    return response.status_code, [], _json(response) or {}
                for line in response.iter_lines():
                    lines.append(line)
        except httpx2.TransportError as exc:
            lost = {"code": CONNECTION_LOST, "message": f"{type(exc).__name__}: {exc}"}
            return 200, [*parse_sse(iter(lines), complete=False), ("error", lost)], {}
        return 200, parse_sse(iter(lines)), {}


@contextmanager
def remote(url: str) -> Iterator[Api]:
    """A running API at `url`."""
    base = url.rstrip("/")
    with httpx2.Client(base_url=base, timeout=httpx2.Timeout(10, read=180)) as client:
        yield _Remote(client, base)


# ---- The run ----


def gather_context(api: Api, conversations: Sequence[Conversation]) -> Context:
    """The chat's mode, the data, the newest session of each kind the questions ask about,
    and the fixed evidence (the system prompt and the tool definitions as the API lists them)."""
    status, health = api.get("/api/health")
    if status != 200 or not isinstance(health, dict):
        raise ChatUnavailable(f"GET /api/health answered {status}")
    chat = health.get("chat") or {}
    data = health.get("data") or {}
    if chat.get("mode") not in ("anthropic", "fake"):
        raise ChatUnavailable(f"the chat is {chat.get('mode', 'unknown')} on {api.where}")
    status, catalog = api.get("/api/tools")
    definitions = catalog if status == 200 and isinstance(catalog, list) else []
    texts = [SYSTEM_PROMPT]
    for tool in definitions:
        keys = ("name", "description", "input_schema")
        texts.append(json.dumps({k: tool.get(k) for k in keys}, ensure_ascii=False))
    latest: dict[str, SessionRef] = {}
    for code in sorted(latest_codes(conversations)):
        status, found = api.get("/api/tools/find_session", {"recent": 0, "session": code})
        match = ((found or {}).get("data") or {}).get("match") if status == 200 else None
        if isinstance(match, dict):
            latest[code] = SessionRef.from_match(match)
    return Context(
        where=api.where,
        mode=str(chat.get("mode")),
        model=str(chat.get("model") or ""),
        effort=api.effort,
        latest_session=data.get("latest"),
        results_run=data.get("results_run"),
        latest=latest,
        evidence=evidence_from(texts),
        tools=tuple(str(t.get("name")) for t in definitions),
    )


@dataclass
class EvalRun:
    """Every answer and its checks, with what the run knew."""

    context: Context
    results: list[QuestionResult]
    started: datetime
    seconds: float
    repeat: int

    @property
    def models(self) -> list[str]:
        found = {r.turn.done.get("model") for r in self.results if r.turn.done}
        return sorted(str(m) for m in found if m)

    @property
    def scripted(self) -> bool:
        """Anything but a run whose chat is Claude on every answer: the health check says
        `anthropic` and no `done` names the scripted model."""
        return self.context.mode != "anthropic" or SCRIPTED_MODEL in self.models

    @property
    def failed(self) -> list[QuestionResult]:
        return [r for r in self.results if not r.passed]

    @property
    def problems(self) -> list[QuestionResult]:
        """Answers that didn't stream to the end (refused requests, stream errors)."""
        return [r for r in self.results if r.problem is not None]


def run(
    api: Api,
    conversations: Sequence[Conversation],
    *,
    repeat: int = 1,
    log: Callable[[str], None] = print,
) -> EvalRun:
    """Ask every conversation `repeat` times, each question after the previous answer's history,
    and check each answer as it comes."""
    started = datetime.now(UTC)
    clock = time.perf_counter()
    context = gather_context(api, conversations)
    log(
        f"{context.where}: chat {context.mode} ({context.model}), data {context.latest_session}, "
        f"scoring run {context.results_run}"
    )
    results: list[QuestionResult] = []
    for n in range(1, repeat + 1):
        for conversation in conversations:
            state = ConversationState(context.evidence)
            done: dict[str, Any] | None = None
            for question in conversation.questions:
                body: dict[str, Any] = {"message": question.text}
                if done is not None:
                    body |= {"history": done["history"], "signature": done["signature"]}
                start = time.perf_counter()
                status, events, error = api.chat(body)
                turn = turn_from_events(
                    question.text, status, events, error, time.perf_counter() - start
                )
                if (code := _not_run_code(turn)) is not None:
                    raise ChatUnavailable(
                        f"the chat on {context.where} answered {code} (its Anthropic key was "
                        "refused, or the chat is off), so nothing was judged"
                    )
                if turn.done is not None:
                    done = turn.done
                    signature = str(turn.done.get("signature") or "")
                    if context.prompt_version is None and "." in signature:
                        context.prompt_version = signature.split(".")[0]
                result = state.judge(question, turn, context, n)
                results.append(result)
                log(_line(result, repeat))
    return EvalRun(context, results, started, time.perf_counter() - clock, repeat)


def _line(r: QuestionResult, repeat: int) -> str:
    g = r.grounding
    run = f"run {r.run} " if repeat > 1 else ""
    verdict = "pass" if r.passed else "FAIL"
    detail = r.problem or (
        f"tools {summarise_calls(r.turn.calls)}; numbers {len(g.verdicts)} "
        f"({g.count('ungrounded')} ungrounded)"
    )
    return f"{run}{r.question.label:>4} {verdict:4} {r.turn.seconds:5.1f} s  {detail}"
