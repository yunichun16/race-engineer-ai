"""The limits in Upstash Redis, over its REST API (the deployment's store).

Upstash's REST API (upstash.com/docs/redis/features/restapi, read 2026-10-04):
- `POST {url}/multi-exec` with a JSON array of commands (each an array, `["INCRBY", "k", 5]`)
  runs them as one MULTI/EXEC transaction and answers a JSON array with one `{"result": ...}`
  or `{"error": "..."}` per command; a transaction discarded as a whole (a syntax error, an
  unsupported command, a size or quota limit) is a single `{"error": "..."}` object with a 4xx
  status. `POST {url}/pipeline` takes and answers the same shapes, without atomicity.
- Replies are Redis's, as JSON: integers as numbers, values and sorted-set scores as strings
  (`ZRANGE ... WITHSCORES` is a flat list `[member, score, ...]`), nil as null.
- `Authorization: Bearer <token>`. Every command inside a transaction or pipeline counts
  against the plan's monthly commands (the free plan: 500K), which is why the guard keeps
  refused and repeated traffic off the store (`guard.py`).

The client never retries: a transaction whose answer was lost may have run, and running it again
would count the question twice. It waits at most 2 seconds. Neither the URL nor the token is ever
logged or put in an error message (httpx2's own "HTTP Request: POST <url>" line is filtered out
for the database's host).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx2

from race_engineer.api.limits.store import KEY_PREFIX, Command, CommandStore, StoreError

TIMEOUT_S = 2.0
_ERROR_CHARS = 160  # of Upstash's error text kept in a StoreError


class _HideHost(logging.Filter):
    """Drops log records that name a host: httpx2 logs every request's URL at INFO."""

    def __init__(self, host: str) -> None:
        super().__init__()
        self.host = host

    def filter(self, record: logging.LogRecord) -> bool:
        return self.host not in record.getMessage()


def _hide_from_logs(host: str) -> None:
    """Keep `host` out of httpx2's log lines (once per host)."""
    logger = logging.getLogger("httpx2")
    if host and not any(isinstance(f, _HideHost) and f.host == host for f in logger.filters):
        logger.addFilter(_HideHost(host))


class UpstashClient:
    """Upstash's REST API: transactions, pipelines and single commands."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout_s: float = TIMEOUT_S,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx2.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
            transport=transport or httpx2.AsyncHTTPTransport(retries=0),
            follow_redirects=False,
        )
        _hide_from_logs(self._http.base_url.host)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def multi_exec(self, commands: Sequence[Command]) -> list[Any]:
        """The replies to `commands`, run as one transaction."""
        return self._replies(await self._post("/multi-exec", commands), len(commands))

    async def pipeline(self, commands: Sequence[Command]) -> list[Any]:
        """The replies to `commands`, sent in one request (not atomic)."""
        return self._replies(await self._post("/pipeline", commands), len(commands))

    async def command(self, *args: str | int) -> Any:
        """One command's reply."""
        body = await self._post("", list(args))
        if not isinstance(body, dict) or "result" not in body:
            raise StoreError("Upstash answered a command with an unexpected body")
        return body["result"]

    async def _post(self, path: str, body: Any) -> Any:
        try:
            response = await self._http.post(path, json=body)
        except httpx2.TimeoutException:
            raise StoreError("Upstash didn't answer in time") from None
        except httpx2.HTTPError as exc:
            raise StoreError(f"Upstash couldn't be reached ({type(exc).__name__})") from None
        try:
            data = response.json()
        except ValueError:
            raise StoreError(f"Upstash answered {response.status_code} without JSON") from None
        if isinstance(data, dict) and "error" in data:
            message = str(data["error"])[:_ERROR_CHARS]
            raise StoreError(f"Upstash refused the request ({response.status_code}): {message}")
        if response.status_code != 200:
            raise StoreError(f"Upstash answered {response.status_code}")
        return data

    @staticmethod
    def _replies(data: Any, expected: int) -> list[Any]:
        if not isinstance(data, list) or len(data) != expected:
            raise StoreError("Upstash answered a transaction with an unexpected body")
        replies = []
        for item in data:
            if not isinstance(item, dict):
                raise StoreError("Upstash answered a command with an unexpected body")
            if "error" in item:
                raise StoreError(f"Upstash refused a command: {str(item['error'])[:_ERROR_CHARS]}")
            replies.append(item.get("result"))
        return replies


class UpstashStore(CommandStore):
    """The limits in Upstash Redis: shared by every container and kept across restarts."""

    name = "upstash"

    def __init__(
        self,
        client: UpstashClient,
        prefix: str = KEY_PREFIX,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(prefix, clock)
        self.client = client

    @classmethod
    def from_credentials(
        cls, url: str, token: str, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> UpstashStore:
        return cls(UpstashClient(url, token, transport=transport))

    async def run(
        self, commands: Sequence[Command], *, atomic: bool, now: float | None = None
    ) -> list[Any]:
        if atomic:
            return await self.client.multi_exec(commands)
        return await self.client.pipeline(commands)

    async def aclose(self) -> None:
        await self.client.aclose()
