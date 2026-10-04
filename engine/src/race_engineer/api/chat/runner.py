"""Answering one chat question: the SDK tool runner, streamed to the browser as SSE events.

`answer()` asks Claude (`claude-sonnet-5-5` through `client.beta.messages.tool_runner`, streamed)
with the conversation so far plus the question, and turns what comes back into events:

- `status` {text}: a non-empty progress note (a `thinking` block with `display: "updates"`).
- `text` {delta}: each piece of the answer's text.
- `tool_call` {id, name, input}: after a turn ends with tool calls, before they run.
- `tool_result` {id, name, is_error, summary, chart}: after they run. `chart` is
  {bundle, resource_uri, data} for a chart tool that succeeded, else null.
- `retry` {message}: the model's tool call couldn't be read, so the text streamed since the last
  tool result is void and the turn is asked again (once).
- `refusal` {message, category}: the model declined (`stop_reason: "refusal"`); no tools run.
- `error` {code, message}: busy, truncated, step_limit, too_costly, timeout, upstream_busy,
  upstream_unavailable, upstream_unreachable, chat_not_configured, bad_request or
  internal_error.
- `done` {history, signature, questions_used, questions_left, stop_reason, model, usage,
  cost_usd, request_id, quota}: always last. `quota` is {hour_left, day_left} for the visitor
  when the limits are on, else null.

The model only sees each tool's summary; chart data goes to the browser in `tool_result` and
never into a request. The history in `done` is append-only: every message exactly as the API saw
it, thinking blocks unchanged, up to the last complete step. A question that made no progress
(refused, failed before any answer) leaves the history as it was, so it doesn't use up one of
the conversation's questions.

Each API call's text streams as it arrives. One question may take up to `max_iterations` calls
and `timeout_s` seconds in all, and stops before its next call once it has cost `max_cost_usd`
(too_costly). Nothing the user wrote is logged: one line per question gives the request id,
calls, tools, stop reason, tokens, cost, time and the visitor's questions left (`quota=`).

With the limits on, the question comes with its admission (`ledger`, an `Admission`): the run
records its outcome, what it cost (0 for the scripted chat unless counted), whether an API call
was cut off in flight with no usage reported, and how many requests it sent. The route settles
the admission afterwards (`routes/chat.accept`), even when the answer never started.
"""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Sequence
from contextlib import aclosing
from typing import TYPE_CHECKING, Any, TypeVar, cast

import anthropic
import anyio
from anthropic import omit
from anthropic.lib.tools import ToolError
from anthropic.types.beta import BetaMessageParam
from fastapi.sse import ServerSentEvent

from race_engineer.api.chat.pricing import Usage
from race_engineer.api.chat.prompt import system_blocks
from race_engineer.api.chat.session import count_questions, sign
from race_engineer.api.chat.tools import ChartSink, build_chat_tools
from race_engineer.api.limits.policy import Admission, refunded

if TYPE_CHECKING:
    from race_engineer.api.chat.service import ChatService

log = logging.getLogger(__name__)

T = TypeVar("T")

FALLBACK_BETA = "server-side-fallback-2026-07-01"  # fallbacks: "default"
UPDATES_BETA = "thinking-display-updates-2026-08-18"  # thinking.display: "updates"
# What a mid-answer fallback leaves before its `fallback` block that must not be sent back: the
# declined model's reasoning and its tool calls, which the refusal may have cut off.
DROPPED_BEFORE_FALLBACK = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}

MESSAGES = {
    "busy": "The chat is answering as many questions as it can; try again in a moment.",
    "truncated": "The answer was cut off at the length limit; try a narrower question.",
    "step_limit": "The answer needed more tool calls than the chat allows; try a narrower "
    "question.",
    "upstream_busy": "Claude is rate-limited right now; try again in a minute.",
    "upstream_unavailable": "Claude is unavailable or overloaded right now; try again shortly.",
    "upstream_unreachable": "The chat couldn't reach the Claude API.",
    "chat_not_configured": "The chat's Anthropic credentials were refused.",
    "bad_request": "Claude rejected the request; the server log has details.",
    "too_costly": "This question needed more work than this demo allows; try a narrower question.",
    "retry": "The model's tool call came through garbled; asking again.",
    "unreadable_tool_input": "The model's tool call came through garbled twice in a row; try "
    "again.",
}
REFUSAL_MESSAGE = "Claude declined to answer this. Try asking another way."


def sse(event: str, data: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(event=event, data=data)


def error_code(exc: BaseException) -> str:
    """The `error` event code for an Anthropic SDK error."""
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.APIConnectionError):
        return "upstream_unreachable"
    if isinstance(exc, anthropic.CredentialsError):
        return "chat_not_configured"
    if not isinstance(exc, anthropic.APIStatusError):
        return "internal_error"
    status = exc.status_code
    if status == 200:  # an `error` event in the middle of a stream
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        kind = error.get("type") if isinstance(error, dict) else None
        status = {
            "rate_limit_error": 429,
            "authentication_error": 401,
            "permission_error": 403,
            "invalid_request_error": 400,
        }.get(str(kind), 529)
    if status == 429:
        return "upstream_busy"
    if status in (401, 403):
        return "chat_not_configured"
    if status >= 500:
        return "upstream_unavailable"
    return "bad_request"


def block_param(block: Any) -> dict[str, Any]:
    """A response content block as the SDK sends it back in a request, so the history the
    browser holds is byte for byte what the API saw (the SDK's own `openapi_model_dump`)."""
    exclude = getattr(block, "__api_exclude__", None)
    return block.model_dump(mode="json", by_alias=True, exclude_unset=True, exclude=exclude)


def echo_blocks(blocks: Sequence[Any]) -> tuple[list[Any], bool]:
    """The blocks to send back, and whether any were dropped: after a mid-answer fallback, the
    API wants the declined model's thinking and tool calls before the last `fallback` block left
    out (its text, and everything after the block, stay)."""
    last = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=None)
    if last is None:
        return list(blocks), False
    kept = [b for i, b in enumerate(blocks) if i > last or b.type not in DROPPED_BEFORE_FALLBACK]
    return kept, len(kept) != len(blocks)


def content_text(content: Any) -> str:
    """A tool result's content as text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return "" if content is None else str(content)


def has_tool_calls(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "assistant"
        and isinstance(content, list)
        and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    )


async def close_runner(runner: object) -> None:
    """Stop an SDK tool runner part-way, closing its open stream. The runner has no public
    close, and leaving it to the garbage collector logs an error from the SDK's context manager,
    so its generator is closed here, shielded from the request's cancellation."""
    iterator = getattr(runner, "_iterator", None)
    aclose = getattr(iterator, "aclose", None)
    if aclose is None:
        return
    with anyio.CancelScope(shield=True):
        try:
            await aclose()
        except Exception:  # closing must never hide the error that stopped the runner
            log.debug("closing the tool runner failed", exc_info=True)


class UnreadableToolInput(Exception):
    """The SDK couldn't parse the model's tool input twice in a row."""


class QuestionRun:
    """One question's state: the history being extended, usage, tools called and outcome."""

    def __init__(
        self,
        chat: ChatService,
        history: list[dict[str, Any]],
        question: str,
        ledger: Admission | None = None,
    ) -> None:
        self.chat = chat
        self.ledger = ledger
        self.config = chat.config
        self.history = history
        self.messages: list[dict[str, Any]] = [*history, {"role": "user", "content": question}]
        self.sink = ChartSink()
        self.tools = build_chat_tools(chat.specs, chat.paths, self.sink, eager=chat.eager)
        self.tools_by_name = {t.name: t for t in self.tools}
        self.specs_by_name = {s.name: s for s in chat.specs}
        self.usage = Usage()
        self.calls = 0  # API calls that streamed a response for this question
        self.sent = 0  # API requests sent, those that failed included
        # A request is out with no usage counted for it yet: if the question ends now, the call
        # may still be billed (the ledger charges an estimate).
        self.in_flight = False
        self.tools_called: list[str] = []
        self.stop_reason: str | None = None
        self.model: str | None = None
        self.outcome = "answered"
        self.request_id = uuid.uuid4().hex[:12]
        self.started = time.perf_counter()
        self.deadline = 0.0

    async def events(self) -> AsyncGenerator[ServerSentEvent]:
        if not self.chat.slots.try_acquire():
            self.outcome = "busy"
            self._log()
            self._record()  # before yielding: a client may leave at the first event
            yield self.error("busy")
            yield self.done()
            return
        try:
            self.deadline = anyio.current_time() + self.config.timeout_s
            try:
                async with aclosing(self._answer()) as steps:
                    async for event in steps:
                        yield event
            except (GeneratorExit, anyio.get_cancelled_exc_class()):
                self.outcome = "disconnected"  # the client left; the route closed the answer
                raise
            except TimeoutError:
                self.outcome = "timeout"
                yield self.error(
                    "timeout", f"The answer took longer than {self.config.timeout_s:.0f} seconds."
                )
            except UnreadableToolInput:
                self.outcome = "unreadable_tool_input"
                yield self.error("internal_error", MESSAGES["unreadable_tool_input"])
            except anthropic.AnthropicError as exc:
                code = error_code(exc)
                self.outcome = code
                log.warning("chat request=%s: %s from the API: %s", self.request_id, code, exc)
                yield self.error(code)
            except Exception:
                self.outcome = "internal_error"
                log.exception("chat request=%s failed", self.request_id)
                yield self.error(
                    "internal_error",
                    f"Internal error; see the server log (request {self.request_id}).",
                )
            yield self.done()
        finally:
            self.chat.slots.release()
            self._log()
            self._record()

    def _record(self) -> None:
        """Note the outcome and the spend on the admission, for the route to settle."""
        ledger = self.ledger
        if ledger is None:
            return
        counted = self.chat.mode != "fake" or self.config.count_fake
        ledger.outcome = self.outcome
        ledger.spent_usd = self.usage.cost_usd if counted else 0.0
        ledger.in_flight_unbilled = counted and self.in_flight
        ledger.api_calls = self.sent

    def _too_costly(self) -> bool:
        """Whether the question has cost its ceiling, checked before each new API call."""
        return self.usage.cost_usd >= self.config.max_cost_usd

    def _runner(self, max_iterations: int) -> Any:
        betas: list[Any] = [UPDATES_BETA]
        if self.chat.fallbacks:
            betas.insert(0, FALLBACK_BETA)
        return self.chat.client.beta.messages.tool_runner(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=cast(Any, system_blocks()),
            tools=self.tools,
            messages=cast(list[BetaMessageParam], list(self.messages)),
            thinking={"type": "adaptive", "display": "updates"},
            output_config={"effort": cast(Any, self.config.effort)},
            cache_control={"type": "ephemeral"},
            fallbacks="default" if self.chat.fallbacks else omit,
            betas=betas,
            stream=True,
            max_iterations=max_iterations,
        )

    async def _within(self, awaitable: Awaitable[T]) -> T:
        """Await within what is left of the question's time. A step that can't be cancelled (a
        tool running in a worker thread) may overrun it; the next step then times out at once."""
        left = self.deadline - anyio.current_time()
        if left <= 0:
            if inspect.iscoroutine(awaitable):
                awaitable.close()  # never started
            raise TimeoutError
        with anyio.fail_after(left):
            return await awaitable

    async def _answer(self) -> AsyncGenerator[ServerSentEvent]:
        parse_failures = 0
        while True:
            if self._too_costly():  # a restart after a retry or a fallback
                self.outcome = "too_costly"
                yield self.error("too_costly")
                return
            remaining = self.config.max_iterations - self.calls
            if remaining <= 0:
                self.outcome = "step_limit"
                yield self.error("step_limit")
                return
            runner = self._runner(remaining)
            restart = False
            try:
                while True:
                    if self._too_costly():  # the next anext() would start another API call
                        self.outcome = "too_costly"
                        yield self.error("too_costly")
                        return
                    self.in_flight = True
                    try:
                        stream = await self._within(anext(runner))
                    except StopAsyncIteration:
                        self.in_flight = False  # the runner stopped without a request
                        break
                    except anthropic.APIStatusError:
                        # Refused with an error status (429, 529, ...): nothing to bill.
                        self.in_flight = False
                        self.sent += 1
                        raise
                    except BaseException:
                        self.sent += 1  # cut off or unreachable: it may have been billed
                        raise
                    self.sent += 1
                    self.calls += 1
                    try:
                        while True:
                            try:
                                event = await self._within(anext(stream))
                            except StopAsyncIteration:
                                break
                            out = self._stream_event(event)
                            if out is not None:
                                yield out
                        message = await self._within(stream.get_final_message())
                    except ValueError as exc:
                        # Tool input the SDK couldn't parse (possible with eager streaming):
                        # ask the same turn again from the history, once.
                        self._count_unfinished(stream)
                        parse_failures += 1
                        log.warning(
                            "chat request=%s: unreadable tool input (%d): %s",
                            self.request_id,
                            parse_failures,
                            # Without the input itself, which can quote the user's words.
                            str(exc).split(" JSON: ")[0][:200],
                        )
                        if parse_failures > 1:
                            raise UnreadableToolInput from exc
                        yield sse("retry", {"message": MESSAGES["retry"]})
                        restart = True
                        break
                    except BaseException:
                        # An error event, a timeout or a client that left: the call is still
                        # billed, so what it reported so far counts.
                        self._count_unfinished(stream)
                        raise

                    self.usage.add(message.usage, message.model or self.config.model)
                    self.in_flight = False
                    self.model = message.model
                    self.stop_reason = message.stop_reason
                    if message.stop_reason == "refusal":
                        self.outcome = "refusal"
                        category = getattr(message.stop_details, "category", None)
                        yield sse("refusal", {"message": REFUSAL_MESSAGE, "category": category})
                        return
                    if message.stop_reason in ("max_tokens", "model_context_window_exceeded"):
                        # A cut-off text answer is kept; a cut-off tool call or unsigned
                        # thinking block can't be sent back, so that turn is dropped.
                        whole = all(
                            b.type != "tool_use" and (b.type != "thinking" or b.signature)
                            for b in message.content
                        )
                        if whole and message.content:
                            content = [block_param(b) for b in message.content]
                            self.messages.append({"role": "assistant", "content": content})
                        self.outcome = "truncated"
                        yield self.error("truncated")
                        return

                    kept, fell_back = echo_blocks(message.content)
                    self.messages.append(
                        {"role": "assistant", "content": [block_param(b) for b in kept]}
                    )
                    calls = [b for b in kept if b.type == "tool_use"]
                    if message.stop_reason != "tool_use" or not calls:
                        return  # the final answer
                    for block in calls:
                        self.tools_called.append(block.name)
                        yield sse(
                            "tool_call", {"id": block.id, "name": block.name, "input": block.input}
                        )
                    if fell_back:
                        # The runner would send back the declined part too: run this turn's
                        # tools here and carry on with a new runner from the history.
                        results = await self._within(self._call_tools(calls))
                    else:
                        results = await self._within(runner.generate_tool_call_response())
                    if results is None:
                        return
                    self.messages.append(cast(dict[str, Any], results))
                    for out in self._tool_results(calls, cast(dict[str, Any], results)):
                        yield out
                    if fell_back:
                        restart = True
                        break
                if not restart:
                    # The runner stopped after a tool turn: no API calls left for this question.
                    self.outcome = "step_limit"
                    yield self.error("step_limit")
                    return
            finally:
                await close_runner(runner)

    def _count_unfinished(self, stream: Any) -> None:
        """Add the usage a call reported before it broke off (the input and cache counts from
        `message_start`, the output so far), so the log and `done` don't undercount it."""
        try:
            snapshot = stream.current_message_snapshot
        except AssertionError:  # nothing arrived
            return
        if snapshot is not None:  # (None when asserts are off)
            self.usage.add(snapshot.usage, snapshot.model or self.config.model)
            self.in_flight = False

    def _stream_event(self, event: Any) -> ServerSentEvent | None:
        if event.type == "content_block_delta" and event.delta.type == "text_delta":
            return sse("text", {"delta": event.delta.text})
        if event.type == "content_block_stop" and event.content_block.type == "thinking":
            note = event.content_block.thinking.strip()
            if note:
                return sse("status", {"text": note})
        return None

    async def _call_tools(self, calls: Sequence[Any]) -> dict[str, Any]:
        """Run tool calls as the SDK runner does: in order, errors as `is_error` results."""
        content: list[dict[str, Any]] = []
        for block in calls:
            result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id}
            tool = self.tools_by_name.get(block.name)
            if tool is None:
                result |= {"content": f"Error: Tool '{block.name}' not found", "is_error": True}
            else:
                try:
                    result["content"] = await tool.call(block.input)
                except ToolError as exc:
                    result |= {"content": exc.content, "is_error": True}
                except Exception as exc:
                    log.exception("chat tool %s failed", block.name)
                    result |= {"content": repr(exc), "is_error": True}
            content.append(result)
        return {"role": "user", "content": content}

    def _tool_results(self, calls: Sequence[Any], results: dict[str, Any]) -> list[ServerSentEvent]:
        by_id = {r.get("tool_use_id"): r for r in results.get("content", []) if isinstance(r, dict)}
        events = []
        for block, record in zip(calls, self.sink.take(calls), strict=True):
            result = by_id.get(block.id, {})
            is_error = bool(result.get("is_error"))
            spec = self.specs_by_name.get(block.name)
            chart = None
            if (
                not is_error
                and spec is not None
                and spec.chart
                and record is not None
                and record.output is not None
                and record.output.data is not None
            ):
                chart = {
                    "bundle": spec.chart,
                    "resource_uri": spec.resource_uri,
                    "data": record.output.data,
                }
            events.append(
                sse(
                    "tool_result",
                    {
                        "id": block.id,
                        "name": block.name,
                        "is_error": is_error,
                        "summary": content_text(result.get("content")),
                        "chart": chart,
                    },
                )
            )
        return events

    def error(self, code: str, message: str | None = None) -> ServerSentEvent:
        return sse("error", {"code": code, "message": message or MESSAGES.get(code, code)})

    def final_history(self) -> list[dict[str, Any]]:
        """The history to hand back: up to the last complete step (never a turn whose tool calls
        have no results), or the history as it came if the question got nowhere."""
        messages = list(self.messages)
        if messages and has_tool_calls(messages[-1]):
            messages.pop()
        if len(messages) <= len(self.history) + 1:
            return list(self.history)
        return messages

    def done(self) -> ServerSentEvent:
        history = self.final_history()
        used = count_questions(history)
        return sse(
            "done",
            {
                "history": history,
                "signature": sign(history, self.chat.secret, self.chat.prompt_version),
                "questions_used": used,
                "questions_left": max(0, self.config.max_turns - used),
                "stop_reason": self.stop_reason,
                "model": self.model or self.chat.model_label,
                "usage": self.usage.as_dict(),
                "cost_usd": round(self.usage.cost_usd, 6),
                "request_id": self.request_id,
                "quota": self.quota(),
            },
        )

    def quota(self) -> dict[str, int] | None:
        """The visitor's questions left after this one (given back when its outcome is
        refunded), or None without limits."""
        ledger = self.ledger
        if ledger is None:
            return None
        back = 1 if refunded(self.outcome, self.sent) else 0
        return {"hour_left": ledger.hour_left + back, "day_left": ledger.day_left + back}

    def _log(self) -> None:
        u = self.usage
        quota = self.quota()
        log.info(
            "chat request=%s outcome=%s calls=%d tools=%s stop=%s tokens in=%d out=%d "
            "cache_read=%d cache_write=%d cost=$%.4f latency=%.2fs quota=%s",
            self.request_id,
            self.outcome,
            self.calls,
            ",".join(self.tools_called) or "-",
            self.stop_reason,
            u.input,
            u.output,
            u.cache_read,
            u.cache_write,
            u.cost_usd,
            time.perf_counter() - self.started,
            f"{quota['hour_left']}/{quota['day_left']}" if quota else "-",
        )


async def answer(
    chat: ChatService,
    history: list[dict[str, Any]],
    question: str,
    *,
    ledger: Admission | None = None,
) -> AsyncGenerator[ServerSentEvent]:
    """The SSE events answering `question` after the (already checked) `history`; the outcome
    and spend are recorded on `ledger` when given."""
    async with aclosing(QuestionRun(chat, history, question, ledger).events()) as events:
        async for event in events:
            yield event
