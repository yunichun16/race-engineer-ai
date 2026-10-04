"""ASGI middleware for the cheap guardrails, checked before a request reaches its route.

- `BodyLimit`: POST /api/chat bodies over RACE_ENGINEER_CHAT_MAX_BODY (640 KiB: the 512 KiB
  history, the question and the JSON around them) get 413 `request_too_large`, by Content-Length
  without reading the body, or by counting the bytes of a body sent without one. Without it
  FastAPI would parse a body of any size before the history's own 512 KB check.
- `RequestRate`: requests a minute while the limits are on, per visitor and in all: 120 and 600
  on the tools, the catalog and the saved answers, 20 and 120 on POST /api/chat, 60 per visitor
  on GET /api/chat/status, 240 in all on /mcp (Claude's connector calls from Anthropic's
  addresses, so per-visitor limits would lump every Claude user together). Refusals are 429
  `rate_limited` with `retry_after_s`. In process, which works because the API runs in one
  container; the store keeps only the money limits. They also stop a script hammering the chat
  from spending the store's monthly commands.

Both answer with the API's JSON error body and sit inside CORSMiddleware, so a browser can read
the refusal (`Retry-After` is exposed too).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PRUNE_AT = 4096  # buckets kept before full ones are dropped


def error_body(
    status: int,
    code: str,
    message: str,
    retry_after_s: int | None = None,
) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    headers = None
    if retry_after_s is not None:
        error["retry_after_s"] = retry_after_s
        headers = {"Retry-After": str(retry_after_s)}
    return JSONResponse({"error": error}, status_code=status, headers=headers)


class BodyLimit:
    """413 for request bodies over a limit, on the (method, path) pairs in `limits`."""

    def __init__(self, app: ASGIApp, limits: Mapping[tuple[str, str], int]) -> None:
        self.app = app
        self.limits = dict(limits)

    def _too_large(self, limit: int) -> JSONResponse:
        message = f"The request is larger than {limit // 1024} KB; start a new conversation."
        return error_body(413, "request_too_large", message)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = (
            self.limits.get((scope["method"], scope["path"])) if scope["type"] == "http" else None
        )
        if limit is None:
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                too_large = int(declared) > limit
            except ValueError:
                too_large = False
            if too_large:
                await self._too_large(limit)(scope, receive, send)
                return
            # The server holds the body to its Content-Length, so nothing more to count.
            await self.app(scope, receive, send)
            return
        # No Content-Length (a chunked body): read it here, counting, before the route does.
        body = bytearray()
        trailing: Message | None = None
        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailing = message
                break
            body += message.get("body", b"")
            if len(body) > limit:
                await self._too_large(limit)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        queued: list[Message] = [{"type": "http.request", "body": bytes(body), "more_body": False}]
        if trailing is not None:
            queued.append(trailing)

        async def replay() -> Message:
            return queued.pop(0) if queued else await receive()

        await self.app(scope, replay, send)


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass(frozen=True)
class Rule:
    """`per_minute` requests a minute for each visitor (`per_visitor`) or for everyone."""

    name: str
    per_minute: int
    per_visitor: bool


@dataclass(frozen=True)
class Rates:
    tools: int = 120
    tools_global: int = 600
    mcp_global: int = 240
    chat: int = 20
    chat_global: int = 120
    status: int = 60

    def rules(self, method: str, path: str) -> tuple[Rule, ...]:
        """The rules a request counts against (none for the health check, the docs, ...)."""
        if path in ("/mcp", "/mcp/"):
            return (Rule("mcp", self.mcp_global, False),)
        if path == "/api/chat/status" and method == "GET":
            return (Rule("status", self.status, True),)
        if path == "/api/chat" and method == "POST":
            return (Rule("chat", self.chat, True), Rule("chat-all", self.chat_global, False))
        if path.startswith(("/api/tools", "/api/catalog", "/api/saved")):
            return (Rule("tools", self.tools, True), Rule("tools-all", self.tools_global, False))
        return ()


class RequestRate:
    """Token buckets per rule and visitor: each holds a minute's requests and refills
    continuously. Applies only while the app's guard has the limits on (`state.guard`), and asks
    the guard who the visitor is."""

    def __init__(
        self, app: ASGIApp, rates: Rates, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.app = app
        self.rates = rates
        self.clock = clock
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def _wait(self, key: tuple[str, str], per_minute: int, now: float) -> float:
        """Seconds until the bucket has a request to give (0: now)."""
        bucket = self._buckets.get(key)
        if bucket is None:
            return 0.0
        tokens = min(per_minute, bucket.tokens + (now - bucket.updated) * per_minute / 60)
        bucket.tokens, bucket.updated = tokens, now
        return 0.0 if tokens >= 1 else (1 - tokens) * 60 / per_minute

    def _take(self, key: tuple[str, str], per_minute: int, now: float) -> None:
        bucket = self._buckets.setdefault(key, _Bucket(per_minute, now))
        bucket.tokens -= 1
        if len(self._buckets) > _PRUNE_AT:
            self._prune(now)

    def _prune(self, now: float) -> None:
        """Drop buckets that have refilled (their visitors have been quiet for a minute)."""
        self._buckets = {k: b for k, b in self._buckets.items() if now - b.updated < 60}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        guard = getattr(getattr(app, "state", None), "guard", None)
        rules = self.rates.rules(scope["method"], scope["path"])
        if guard is None or not getattr(guard, "enabled", False) or not rules:
            await self.app(scope, receive, send)
            return
        now = self.clock()
        visitor = (
            guard.identity.visitor(scope, time.time())
            if any(r.per_visitor for r in rules)
            else None
        )
        keys = []
        for rule in rules:
            if rule.per_visitor and visitor is None:
                continue  # no address: only the global limit (the chat refuses on its own)
            keys.append(((rule.name, visitor if rule.per_visitor else "*"), rule))
        waits = [(self._wait(key, rule.per_minute, now), rule) for key, rule in keys]
        wait, rule = max(waits, key=lambda w: w[0], default=(0.0, None))
        if wait > 0 and rule is not None:
            retry = max(1, math.ceil(wait))
            if rule.per_visitor:
                message = f"Too many requests from this connection; try again in {retry} s."
            else:
                message = f"The server is getting too many requests; try again in {retry} s."
            await error_body(429, "rate_limited", message, retry)(scope, receive, send)
            return
        for key, rule in keys:
            self._take(key, rule.per_minute, now)
        await self.app(scope, receive, send)
