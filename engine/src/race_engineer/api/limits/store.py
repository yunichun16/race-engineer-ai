"""The limits' counters, kept as Redis keys and changed by Redis commands run in one transaction.

`CommandStore` holds the logic, written once as lists of commands; `MemoryStore` runs them
against a small in-process Redis (`memory.py`) and `UpstashStore` sends them to Upstash's REST
API (`upstash.py`), so both behave alike. Every key starts with `re:v1:` (`KEY_PREFIX`):

| key | type | expiry | holds |
|---|---|---|---|
| kill | string "1" | none | the kill switch |
| spend:{day} | integer | 48 h | micro-dollars settled today |
| reserved:{day} | integer | 48 h | micro-dollars held by questions in flight |
| q:{day} | integer | 48 h | questions admitted today, everyone together |
| h:{visitor} | sorted set of "{ms}:{token}" by ms | 1 h | the visitor's rolling hour |
| d:{day}:{visitor} | integer | the day's end + 1 h | the visitor's questions today |

Admission counts first, then decides, then undoes the counts if it refused, so refused attempts
never count and two requests at once see each other (Redis runs a MULTI/EXEC whole). Every write
re-applies its key's expiry, because DECR and INCRBY recreate an expired key without one: a
visitor key must never outlive its day.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from race_engineer.api.limits.policy import (
    DAY_KEY_TTL_S,
    HOUR_MS,
    HOUR_S,
    Admission,
    Policy,
    StoreStatus,
    day_key_ttl,
    decide,
    utc_day,
)

KEY_PREFIX = "re:v1:"

Command = Sequence[str | int]


class StoreError(Exception):
    """The store couldn't be reached or refused a command. The message names no URL or token."""


class LimitStore(Protocol):
    name: str  # "memory" or "upstash"

    async def admit(self, visitor: str, now: float, policy: Policy) -> Admission: ...

    async def settle(self, admission: Admission, spent_micro: int, refund_quota: bool) -> None: ...

    async def status(self, visitor: str | None, now: float, policy: Policy) -> StoreStatus: ...

    async def set_paused(self, paused: bool) -> None: ...

    async def ping(self) -> None: ...

    async def aclose(self) -> None: ...


def _int(value: Any) -> int:
    """A Redis integer reply, or a string holding one (GET), or 0 for nil."""
    if value is None:
        return 0
    return int(value)


def oldest_ms(reply: Any) -> int | None:
    """The score of the first entry of a `ZRANGE ... WITHSCORES` reply: a flat list
    [member, score, ...] (RESP2, as Upstash's REST API returns it) or [[member, score], ...]
    (RESP3). Falls back to the member's "{ms}:" prefix."""
    if not reply:
        return None
    first = reply[0]
    if isinstance(first, (list, tuple)):
        member, score = first[0], first[1] if len(first) > 1 else None
    else:
        member, score = first, reply[1] if len(reply) > 1 else None
    if score is not None:
        return int(float(score))
    return int(str(member).split(":", 1)[0])


class CommandStore:
    """The limits as Redis commands; a subclass says how they run (`run`)."""

    name = "commands"

    def __init__(self, prefix: str = KEY_PREFIX, clock: Callable[[], float] = time.time) -> None:
        self.prefix = prefix
        self.clock = clock

    async def run(
        self, commands: Sequence[Command], *, atomic: bool, now: float | None = None
    ) -> list[Any]:
        """The replies to `commands`, run as one transaction when `atomic` (else in one round
        trip, not atomic). StoreError when the store can't run them."""
        raise NotImplementedError

    async def aclose(self) -> None:
        return None

    # Keys.

    def kill_key(self) -> str:
        return f"{self.prefix}kill"

    def day_keys(self, day: str) -> tuple[str, str, str]:
        """spend, reserved and questions for `day`."""
        return f"{self.prefix}spend:{day}", f"{self.prefix}reserved:{day}", f"{self.prefix}q:{day}"

    def hour_key(self, visitor: str) -> str:
        return f"{self.prefix}h:{visitor}"

    def visitor_day_key(self, day: str, visitor: str) -> str:
        return f"{self.prefix}d:{day}:{visitor}"

    # The protocol.

    async def admit(self, visitor: str, now: float, policy: Policy) -> Admission:
        day = utc_day(now)
        now_ms = int(now * 1000)
        member = f"{now_ms}:{secrets.token_hex(6)}"
        spend, reserved, questions = self.day_keys(day)
        hour, today = self.hour_key(visitor), self.visitor_day_key(day, visitor)
        reserve = policy.reserve_micro
        replies = await self.run(
            [
                ["GET", self.kill_key()],
                ["INCR", questions],
                ["EXPIRE", questions, DAY_KEY_TTL_S],
                ["INCRBY", reserved, reserve],
                ["EXPIRE", reserved, DAY_KEY_TTL_S],
                ["GET", spend],
                ["ZREMRANGEBYSCORE", hour, "-inf", f"({now_ms - HOUR_MS}"],
                ["ZADD", hour, now_ms, member],
                ["ZCARD", hour],
                ["ZRANGE", hour, 0, 0, "WITHSCORES"],
                ["EXPIRE", hour, HOUR_S],
                ["INCR", today],
                ["EXPIRE", today, day_key_ttl(day, now)],
            ],
            atomic=True,
            now=now,
        )
        hour_count, day_count = _int(replies[8]), _int(replies[11])
        decision = decide(
            policy,
            now,
            paused=replies[0] is not None,
            questions=_int(replies[1]),
            spend_micro=_int(replies[5]),
            reserved_micro=_int(replies[3]),
            hour_count=hour_count,
            oldest_ms=oldest_ms(replies[9]),
            day_count=day_count,
        )
        admission = Admission(
            store=self.name,
            day=day,
            visitor=visitor,
            member=member,
            reserve_micro=reserve,
            refused=decision.refused,
            retry_after_s=decision.retry_after_s,
            limit=decision.limit,
            hour_left=max(0, policy.per_hour - hour_count),
            day_left=max(0, policy.per_day - day_count),
        )
        if not admission.admitted:
            # Undo every count, so a refused attempt leaves the counters as they were.
            await self.run(self._uncount(admission, now, release=True), atomic=True, now=now)
        return admission

    def _uncount(self, admission: Admission, now: float, *, release: bool) -> list[Command]:
        """Give back the admission's question (and its reservation when `release`), each write
        with its expiry."""
        _, reserved, questions = self.day_keys(admission.day)
        today = self.visitor_day_key(admission.day, admission.visitor)
        commands: list[Command] = []
        if release:
            commands += [
                ["INCRBY", reserved, -admission.reserve_micro],
                ["EXPIRE", reserved, DAY_KEY_TTL_S],
            ]
        return [
            *commands,
            ["DECR", questions],
            ["EXPIRE", questions, DAY_KEY_TTL_S],
            ["ZREM", self.hour_key(admission.visitor), admission.member],
            ["DECR", today],
            ["EXPIRE", today, day_key_ttl(admission.day, now)],
        ]

    async def settle(self, admission: Admission, spent_micro: int, refund_quota: bool) -> None:
        """Add what the question cost to its day's spend and release its reservation; give the
        question back when `refund_quota`."""
        now = self.clock()
        spend, reserved, _ = self.day_keys(admission.day)
        commands: list[Command] = []
        if spent_micro > 0:
            commands += [["INCRBY", spend, spent_micro], ["EXPIRE", spend, DAY_KEY_TTL_S]]
        commands += [
            ["INCRBY", reserved, -admission.reserve_micro],
            ["EXPIRE", reserved, DAY_KEY_TTL_S],
        ]
        if refund_quota:
            commands += self._uncount(admission, now, release=False)
        await self.run(commands, atomic=True, now=now)

    async def status(self, visitor: str | None, now: float, policy: Policy) -> StoreStatus:
        """The counters, read in one round trip without changing any (one command for the
        global part, three with a visitor)."""
        day = utc_day(now)
        keys = [self.kill_key(), *self.day_keys(day)]
        if visitor is None:
            (values,) = await self.run([["MGET", *keys]], atomic=False, now=now)
            return StoreStatus(
                paused=values[0] is not None,
                spend_micro=_int(values[1]),
                reserved_micro=_int(values[2]),
                questions=_int(values[3]),
            )
        hour = self.hour_key(visitor)
        start = int(now * 1000) - HOUR_MS
        values, hour_used, oldest = await self.run(
            [
                ["MGET", *keys, self.visitor_day_key(day, visitor)],
                ["ZCOUNT", hour, start, "+inf"],
                ["ZRANGEBYSCORE", hour, start, "+inf", "WITHSCORES", "LIMIT", 0, 1],
            ],
            atomic=False,
            now=now,
        )
        return StoreStatus(
            paused=values[0] is not None,
            spend_micro=_int(values[1]),
            reserved_micro=_int(values[2]),
            questions=_int(values[3]),
            hour_used=_int(hour_used),
            day_used=_int(values[4]),
            oldest_ms=oldest_ms(oldest),
        )

    async def set_paused(self, paused: bool) -> None:
        command = ["SET", self.kill_key(), "1"] if paused else ["DEL", self.kill_key()]
        await self.run([command], atomic=False)

    async def ping(self) -> None:
        await self.run([["PING"]], atomic=False)
