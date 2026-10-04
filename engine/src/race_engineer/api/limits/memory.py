"""The limits in this process's memory: for local runs and tests, and for the in-process tests of
the Upstash client (`tests/fake_upstash.py` serves a `MiniRedis` over HTTP).

`MiniRedis` runs the few Redis commands the limits use, with Redis's replies (integers as ints,
values and scores as strings, nil as None) and expiry times, under a lock: one call of `run` is
one transaction, with nothing else in between. The counters reset when the process restarts,
which is why a deployment uses Upstash (`RACE_ENGINEER_LIMITS_STORE=upstash`).
"""

from __future__ import annotations

import fnmatch
import math
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from race_engineer.api.limits.store import KEY_PREFIX, Command, CommandStore, StoreError


class RedisError(Exception):
    """A command Redis would answer with an error (inside a transaction, the others still run)."""


def _score(text: str) -> tuple[float, bool]:
    """A score bound: "-inf", "+inf", "12" or "(12" (exclusive)."""
    exclusive = text.startswith("(")
    value = text[1:] if exclusive else text
    try:
        return float(value.replace("inf", "Infinity")), exclusive
    except ValueError:
        raise RedisError("ERR min or max is not a float") from None


def _in_range(score: float, low: tuple[float, bool], high: tuple[float, bool]) -> bool:
    above = score > low[0] if low[1] else score >= low[0]
    below = score < high[0] if high[1] else score <= high[0]
    return above and below


def _format_score(score: float) -> str:
    """As Redis prints a score: 1696000000000, 1.5."""
    return str(int(score)) if score.is_integer() else repr(score)


def _integer(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise RedisError("ERR value is not an integer or out of range") from None


class MiniRedis:
    """Strings and sorted sets with expiry: GET SET DEL INCR DECR INCRBY EXPIRE TTL MGET ZADD
    ZCARD ZCOUNT ZRANGE ZRANGEBYSCORE ZREMRANGEBYSCORE ZREM SCAN PING."""

    COMMANDS = frozenset(
        {
            *("GET", "SET", "DEL", "INCR", "DECR", "INCRBY", "EXPIRE", "TTL", "MGET"),
            *("ZADD", "ZCARD", "ZCOUNT", "ZRANGE", "ZRANGEBYSCORE", "ZREMRANGEBYSCORE", "ZREM"),
            *("SCAN", "PING"),
        }
    )

    def __init__(self) -> None:
        self.values: dict[str, str | dict[str, float]] = {}
        self.expires: dict[str, float] = {}
        self._lock = threading.Lock()

    def run(self, commands: Sequence[Command], now: float) -> list[Any]:
        """Each command's reply, a RedisError for one that failed; all under one lock."""
        with self._lock:
            replies: list[Any] = []
            for command in commands:
                try:
                    replies.append(self._one([str(a) for a in command], now))
                except RedisError as exc:
                    replies.append(exc)
            return replies

    def unknown(self, commands: Sequence[Command]) -> list[str]:
        """The commands in `commands` this class doesn't run."""
        return [str(c[0]) for c in commands if not c or str(c[0]).upper() not in self.COMMANDS]

    # Keys and expiry.

    def _live(self, key: str, now: float) -> str | dict[str, float] | None:
        expires = self.expires.get(key)
        if expires is not None and expires <= now:
            self.values.pop(key, None)
            self.expires.pop(key, None)
        return self.values.get(key)

    def _string(self, key: str, now: float) -> str | None:
        value = self._live(key, now)
        if isinstance(value, dict):
            raise RedisError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return value

    def _zset(self, key: str, now: float, create: bool = False) -> dict[str, float] | None:
        value = self._live(key, now)
        if value is None:
            if not create:
                return None
            value = self.values[key] = {}
        if not isinstance(value, dict):
            raise RedisError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return value

    def _set_string(self, key: str, value: str, keep_ttl: bool) -> None:
        self.values[key] = value
        if not keep_ttl:
            self.expires.pop(key, None)

    def _delete(self, key: str) -> bool:
        self.expires.pop(key, None)
        return self.values.pop(key, None) is not None

    def _drop_if_empty(self, key: str, zset: dict[str, float]) -> None:
        if not zset:
            self._delete(key)

    # Commands.

    def _one(self, args: list[str], now: float) -> Any:
        name, rest = args[0].upper(), args[1:]
        if name not in self.COMMANDS:
            raise RedisError(f"ERR unknown command '{args[0]}'")
        return getattr(self, f"_cmd_{name.lower()}")(rest, now)

    def _cmd_ping(self, args: list[str], now: float) -> str:
        return "PONG"

    def _cmd_get(self, args: list[str], now: float) -> str | None:
        return self._string(args[0], now)

    def _cmd_mget(self, args: list[str], now: float) -> list[str | None]:
        return [v if isinstance(v := self._live(key, now), str) else None for key in args]

    def _cmd_set(self, args: list[str], now: float) -> str:
        self._live(args[0], now)
        self._set_string(args[0], args[1], keep_ttl=False)
        return "OK"

    def _cmd_del(self, args: list[str], now: float) -> int:
        return sum(self._live(key, now) is not None and self._delete(key) for key in args)

    def _incr(self, key: str, by: int, now: float) -> int:
        value = _integer(self._string(key, now) or "0") + by
        self._set_string(key, str(value), keep_ttl=True)
        return value

    def _cmd_incr(self, args: list[str], now: float) -> int:
        return self._incr(args[0], 1, now)

    def _cmd_decr(self, args: list[str], now: float) -> int:
        return self._incr(args[0], -1, now)

    def _cmd_incrby(self, args: list[str], now: float) -> int:
        return self._incr(args[0], _integer(args[1]), now)

    def _cmd_expire(self, args: list[str], now: float) -> int:
        if self._live(args[0], now) is None:
            return 0
        seconds = _integer(args[1])
        if seconds <= 0:
            self._delete(args[0])
        else:
            self.expires[args[0]] = now + seconds
        return 1

    def _cmd_ttl(self, args: list[str], now: float) -> int:
        if self._live(args[0], now) is None:
            return -2
        expires = self.expires.get(args[0])
        return -1 if expires is None else math.ceil(expires - now)

    def _cmd_zadd(self, args: list[str], now: float) -> int:
        zset = self._zset(args[0], now, create=True)
        assert zset is not None
        added = 0
        for score, member in zip(args[1::2], args[2::2], strict=True):
            added += member not in zset
            zset[member] = _score(score)[0]
        return added

    def _cmd_zcard(self, args: list[str], now: float) -> int:
        return len(self._zset(args[0], now) or {})

    def _cmd_zcount(self, args: list[str], now: float) -> int:
        low, high = _score(args[1]), _score(args[2])
        return sum(_in_range(s, low, high) for s in (self._zset(args[0], now) or {}).values())

    def _cmd_zrem(self, args: list[str], now: float) -> int:
        zset = self._zset(args[0], now)
        if zset is None:
            return 0
        removed = sum(zset.pop(member, None) is not None for member in args[1:])
        self._drop_if_empty(args[0], zset)
        return removed

    def _cmd_zremrangebyscore(self, args: list[str], now: float) -> int:
        zset = self._zset(args[0], now)
        if zset is None:
            return 0
        low, high = _score(args[1]), _score(args[2])
        gone = [m for m, s in zset.items() if _in_range(s, low, high)]
        for member in gone:
            del zset[member]
        self._drop_if_empty(args[0], zset)
        return len(gone)

    @staticmethod
    def _ordered(zset: dict[str, float]) -> list[tuple[str, float]]:
        return sorted(zset.items(), key=lambda item: (item[1], item[0]))

    @staticmethod
    def _flat(entries: list[tuple[str, float]], scores: bool) -> list[str]:
        if not scores:
            return [member for member, _ in entries]
        return [part for m, s in entries for part in (m, _format_score(s))]

    def _cmd_zrange(self, args: list[str], now: float) -> list[str]:
        entries = self._ordered(self._zset(args[0], now) or {})
        start, stop = _integer(args[1]), _integer(args[2])
        size = len(entries)
        start, stop = (start + size if start < 0 else start), (stop + size if stop < 0 else stop)
        chosen = entries[max(start, 0) : stop + 1]
        return self._flat(chosen, "WITHSCORES" in (a.upper() for a in args[3:]))

    def _cmd_zrangebyscore(self, args: list[str], now: float) -> list[str]:
        low, high = _score(args[1]), _score(args[2])
        entries = [
            e for e in self._ordered(self._zset(args[0], now) or {}) if _in_range(e[1], low, high)
        ]
        options = [a.upper() for a in args[3:]]
        if "LIMIT" in options:
            at = options.index("LIMIT")
            offset, count = _integer(args[3 + at + 1]), _integer(args[3 + at + 2])
            entries = entries[offset:] if count < 0 else entries[offset : offset + count]
        return self._flat(entries, "WITHSCORES" in options)

    def _cmd_scan(self, args: list[str], now: float) -> list[Any]:
        """The whole keyspace in one page (cursor "0"), filtered by MATCH."""
        options = [a.upper() for a in args[1:]]
        pattern = args[1 + options.index("MATCH") + 1] if "MATCH" in options else "*"
        keys = [k for k in list(self.values) if self._live(k, now) is not None]
        return ["0", [k for k in keys if fnmatch.fnmatchcase(k, pattern)]]


class MemoryStore(CommandStore):
    """The limits in this process (`MiniRedis`): reset on every restart."""

    name = "memory"

    def __init__(self, prefix: str = KEY_PREFIX, clock: Callable[[], float] = time.time) -> None:
        super().__init__(prefix, clock)
        self.redis = MiniRedis()

    async def run(
        self, commands: Sequence[Command], *, atomic: bool, now: float | None = None
    ) -> list[Any]:
        # One lock for the whole list, so pipelined reads are atomic here too.
        replies = self.redis.run(commands, self.clock() if now is None else now)
        for reply in replies:
            if isinstance(reply, RedisError):
                raise StoreError(f"memory store: {reply}")
        return replies
