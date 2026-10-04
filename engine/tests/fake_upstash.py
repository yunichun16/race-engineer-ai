"""A fake of the parts of Upstash's REST API the limits use, for `httpx2.MockTransport`.

Shapes as Upstash's docs give them (upstash.com/docs/redis/features/restapi, read 2026-10-04):
`POST /multi-exec` and `POST /pipeline` take a JSON array of commands and answer a JSON array of
`{"result": ...}` or `{"error": "..."}`, one per command; a transaction with an unknown command
is discarded whole, answered 400 with a single `{"error": "..."}` object; a wrong token is 401
`{"error": "WRONGPASS ..."}`. Replies are Redis's (RESP2) as JSON: integers as numbers, values
and scores as strings, `ZRANGE ... WITHSCORES` a flat list, nil as null.

Each transaction runs whole, under one lock, as Redis runs a MULTI/EXEC. The data lives in a
`MiniRedis`, which keeps every key's expiry, so tests can read TTLs; `calls` and `commands`
count the requests and the commands (Upstash bills every command).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx2

from race_engineer.api.limits.memory import MiniRedis, RedisError
from race_engineer.api.limits.store import KEY_PREFIX
from race_engineer.api.limits.upstash import UpstashClient, UpstashStore

URL = "https://fake-upstash.invalid"
TOKEN = "fake-token-for-tests"


def _json(status: int, body: Any) -> httpx2.Response:
    return httpx2.Response(status, json=body)


class FakeUpstash:
    """The MockTransport handler. `down` makes every request fail to connect; `hang` makes it
    time out."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.redis = MiniRedis()
        self.clock = clock
        self.calls: list[tuple[str, int]] = []  # (path, commands) per request answered
        self.requests = 0  # every request, those that failed included
        self.down = False
        self.hang = False

    @property
    def commands(self) -> int:
        return sum(n for _, n in self.calls)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests += 1
        if self.down:
            raise httpx2.ConnectError("connection refused", request=request)
        if self.hang:
            raise httpx2.ReadTimeout("timed out", request=request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return _json(401, {"error": "WRONGPASS invalid or missing auth token"})
        if request.method != "POST":
            return _json(405, {"error": "ERR method not allowed"})
        try:
            body = json.loads(request.content)
        except ValueError:
            return _json(400, {"error": "ERR failed to parse the request body"})
        path = request.url.path
        if path in ("/multi-exec", "/pipeline"):
            if not isinstance(body, list) or not all(isinstance(c, list) and c for c in body):
                return _json(400, {"error": "ERR a transaction is an array of commands"})
            unknown = self.redis.unknown(body)
            if unknown:
                return _json(400, {"error": f"ERR unknown command '{unknown[0]}'"})
            replies = self.redis.run(body, self.clock())
            self.calls.append((path, len(body)))
            return _json(200, [self._reply(r) for r in replies])
        if path in ("", "/"):
            if not isinstance(body, list) or not body or self.redis.unknown([body]):
                return _json(400, {"error": "ERR unknown command"})
            (reply,) = self.redis.run([body], self.clock())
            self.calls.append((path, 1))
            if isinstance(reply, RedisError):
                return _json(400, {"error": str(reply)})
            return _json(200, {"result": reply})
        return _json(404, {"error": "ERR not found"})

    @staticmethod
    def _reply(reply: Any) -> dict[str, Any]:
        if isinstance(reply, RedisError):
            return {"error": str(reply)}
        return {"result": reply}

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self)

    def store(
        self, clock: Callable[[], float] | None = None, prefix: str = KEY_PREFIX
    ) -> UpstashStore:
        """An UpstashStore talking to this fake."""
        client = UpstashClient(URL, TOKEN, transport=self.transport())
        return UpstashStore(client, prefix, clock or self.clock)

    def ttl(self, key: str) -> int:
        (reply,) = self.redis.run([["TTL", key]], self.clock())
        return reply
