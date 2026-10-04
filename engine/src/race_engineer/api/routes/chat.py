"""POST /api/chat: one question in a conversation, answered as a stream of server-sent events.

The request carries the new question and, after the first, the history and signature from the
previous answer's `done` event (the server keeps no transcripts; see `api/chat/session.py`).
Everything that can be checked before answering is checked first, and a refusal is a plain JSON
error body, not a stream:

| status | error.code | when |
|---|---|---|
| 503 | chat_disabled | the chat is off (no credentials, or RACE_ENGINEER_CHAT=off) |
| 413 | history_too_large | the history is over 512 KB |
| 400 | invalid_history | a history without a signature, or one that doesn't match it |
| 409 | conversation_expired | a history signed before the prompt or tools changed |
| 409 | turn_limit | the conversation already has its 8 questions |
| 503 | busy | every chat slot is answering |
| 429 | rate_limited | the visitor's 10 questions an hour or 25 a day are used (`limit`) |
| 503 | daily_cap | the day's spending cap or question backstop is reached |
| 503 | chat_paused | the kill switch is on |
| 503 | chat_unavailable | the limits can't be checked (store unconfigured or unreachable) |
| 413 | request_too_large | the body is over 640 KiB (`api/limits/http.BodyLimit`) |
| 422 | (the app's validation error) | an empty or over-long message, or a malformed body |

The limits' refusals (`api/limits/guard.py`, when they are on) carry `retry_after_s` in the body
and a Retry-After header. The guard admits the question last, so the cheap checks never cost a
store round trip, and the route settles the admission once, after the answer is closed, whatever
happened to it: a client that left before the answer started gets its question back.

Then the answer streams as `text/event-stream` (events in `api/chat/runner.py`), with a
keep-alive comment every 15 s and `X-Accel-Buffering: no`. A client that disconnects cancels
the question, including the request to Claude in flight, and frees its chat slot.

GET /api/chat/status says whether a question would be admitted now, for the page to offer saved
answers before anyone types; it reads the limits but never counts (`guard.ChatStatus`).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

import anyio
import anyio.lowlevel
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, field_validator

from race_engineer.api.chat.service import ChatService, service_for
from race_engineer.api.chat.session import MAX_HISTORY_MESSAGES, ChatRejected
from race_engineer.api.limits.guard import chat_status, guard_for
from race_engineer.api.models import ChatStatusResponse

MAX_MESSAGE_CHARS = 2000
# The limits' refusal codes: their bodies always carry retry_after_s (null for chat_paused).
LIMIT_CODES = frozenset({"rate_limited", "daily_cap", "chat_paused", "chat_unavailable"})


class ChatRequest(BaseModel):
    """A question, with the conversation so far as the previous answer returned it."""

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS, description="The question.")
    history: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_MESSAGES,
        description="The previous `done` event's history, unchanged; empty for a new conversation.",
    )
    signature: str | None = Field(
        default=None, max_length=200, description="The previous `done` event's signature."
    )

    @field_validator("message")
    @classmethod
    def _not_blank(cls, message: str) -> str:
        # The API refuses a user turn without visible text, so a blank question is refused here,
        # before any stream starts.
        if not message.strip():
            raise ValueError("the question is blank")
        return message


@dataclass(frozen=True)
class Accepted:
    """A request that passed the checks: the chat answering it, the answer's events (not
    started yet) and the bytes of padding to send first (RACE_ENGINEER_SSE_PAD)."""

    chat: ChatService
    events: AsyncGenerator[ServerSentEvent]
    pad: int = 0


def rejection(exc: ChatRejected) -> JSONResponse:
    """The JSON error body for a refused request: code and message, plus `retry_after_s` (and
    the Retry-After header) and any extra fields for the limits' refusals."""
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.retry_after_s is not None or exc.code in LIMIT_CODES:
        error["retry_after_s"] = exc.retry_after_s
    error |= exc.extra or {}
    headers = None if exc.retry_after_s is None else {"Retry-After": str(exc.retry_after_s)}
    return JSONResponse(status_code=exc.status, content={"error": error}, headers=headers)


class ChatRoute(APIRoute):
    """Answers a request refused by the checks with the API's JSON error body, before any
    stream starts (the checks run as a dependency of the streaming endpoint)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def checked(request: Request) -> Response:
            try:
                return await handler(request)
            except ChatRejected as exc:
                return rejection(exc)

        return checked


router = APIRouter(route_class=ChatRoute)


async def _nothing() -> AsyncGenerator[ServerSentEvent]:
    """An answer with no events, for a client that left before its answer started."""
    return
    yield  # pragma: no cover  (makes this an async generator)


async def accept(request: Request, body: ChatRequest) -> AsyncIterator[Accepted]:
    """The checks before answering (see the module docstring); raises ChatRejected.

    The answer is closed when the request's dependencies exit, after the response. FastAPI only
    cancels the task reading the endpoint's generator when the client disconnects, which leaves
    the generator suspended until garbage collection, holding its chat slot and its runner; closing
    the answer here frees both at once (a no-op when the answer ran to the end).

    With the limits on, the question is admitted last, and settled here exactly once after the
    answer is closed, shielded from cancellation: the answer records its outcome and spend on
    the admission, and one that never started (its client left first) settles as `not_started`,
    with its reservation released and its question given back. Nothing between the admission
    and the `try` can raise, so every admitted question is settled."""
    state = request.app.state
    chat = service_for(state)
    if chat is None:
        raise ChatRejected(
            503,
            "chat_disabled",
            "The chat is off on this server (no Anthropic credentials, or RACE_ENGINEER_CHAT=off).",
        )
    chat.check(body.history, body.signature)
    if chat.busy:
        raise ChatRejected(
            503, "busy", "The chat is answering as many questions as it can; try again shortly."
        )
    guard = guard_for(state)
    admission = await guard.admit(request) if guard is not None else None
    events: AsyncGenerator[ServerSentEvent] | None = None
    try:
        # One turn of the event loop first, so a client that sent its question and hung up at
        # once is seen as gone (its connection's close is processed) before an answer is paid for.
        await anyio.lowlevel.checkpoint()
        if await request.is_disconnected():
            events = _nothing()  # don't start (and pay for) an answer nobody will read
        else:
            events = chat.answer(body.history, body.message, ledger=admission)
        settings = getattr(state, "settings", None)
        yield Accepted(chat, events, getattr(settings, "sse_pad", 0))
    finally:
        try:
            if events is not None:
                with anyio.CancelScope(shield=True):
                    await events.aclose()
        finally:
            if guard is not None:
                await guard.settle(admission)  # once; reads admission.outcome and spend


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"description": text}
    for status, text in {
        400: "invalid_history",
        409: "conversation_expired or turn_limit",
        413: "history_too_large or request_too_large",
        429: "rate_limited (retry_after_s and limit in the body)",
        503: "chat_disabled, busy, daily_cap, chat_paused or chat_unavailable",
    }.items()
}


@router.post(
    "/api/chat",
    response_class=EventSourceResponse,
    summary="Ask the chat a question",
    responses=ERRORS,
)
async def chat(accepted: Annotated[Accepted, Depends(accept)]) -> AsyncIterator[ServerSentEvent]:
    """Answer one question as server-sent events: status, text, tool_call, tool_result, retry,
    refusal and error events, then always `done` with the history and signature to send with
    the next question."""
    if accepted.pad:
        yield ServerSentEvent(comment="." * accepted.pad)
    async for event in accepted.events:
        yield event


@router.get(
    "/api/chat/status",
    summary="Whether the chat would answer now",
    response_model=ChatStatusResponse,
)
async def status(request: Request) -> JSONResponse:
    """Whether a question would be admitted now and, if not, why (`reason`: off, paused,
    daily_cap, rate_limited or unavailable) and for how long; the caller's questions left
    (`quota`, null while the limits are off); the caller's own visitor tag; and the saved
    example answers on offer. Reads the limits without counting anything."""
    found = await chat_status(request)
    body = ChatStatusResponse.model_validate(found.as_dict()).model_dump(mode="json")
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
