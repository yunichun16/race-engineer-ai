"""The store's contract: cases every `LimitStore` must pass, written once and run by
`tests/test_limits_store.py` (on `MemoryStore`, and on `UpstashStore` over a fake of Upstash's
REST API) and by `scripts/limits_admin.py check` (on the real Upstash database, under a
`re:test:<uuid>:` prefix that is deleted afterwards).

Each case gets a fresh store from a factory, with a clock it controls, and checks one behaviour
with plain asserts: the refusal order, the rolling hour and its retry time, the UTC day,
reservations and settling, refunds, the kill switch, refusals leaving every counter as it was,
concurrent admissions at the limits, a question settled after midnight, and expiries re-applied
by late writes. Times are on a fixed day in 2031, so a real store's live keys are never touched.
Key expiry by time isn't waited for: a case deletes keys to stand for their expiry.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import anyio

from race_engineer.api.limits.policy import Admission, Policy, StoreStatus
from race_engineer.api.limits.store import CommandStore

BASE = datetime(2031, 3, 4, 12, 0, tzinfo=UTC).timestamp()  # noon on a day far from today
NEAR_MIDNIGHT = datetime(2031, 3, 4, 23, 59, 0, tzinfo=UTC).timestamp()
POLICY = Policy(
    per_hour=3, per_day=5, daily_cap_micro=1_000_000, reserve_micro=100_000, daily_questions=50
)


@dataclass
class Clock:
    """Epoch seconds the case sets."""

    t: float = BASE

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# A store for a case: (the case's clock, the case's name) -> store.
Factory = Callable[[Clock, str], CommandStore]
Case = Callable[[CommandStore, Clock], Awaitable[None]]


def _visitor() -> str:
    return secrets.token_hex(16)


async def _admit(
    store: CommandStore, clock: Clock, visitor: str, policy: Policy = POLICY
) -> Admission:
    return await store.admit(visitor, clock(), policy)


async def _status(store: CommandStore, clock: Clock, visitor: str | None) -> StoreStatus:
    return await store.status(visitor, clock(), POLICY)


async def refusal_order(store: CommandStore, clock: Clock) -> None:
    """The kill switch first, then the cap, then the hour, then the day."""
    visitor = _visitor()
    tight = replace(POLICY, per_hour=1, per_day=1)
    assert (await _admit(store, clock, visitor, tight)).admitted  # holds a reservation
    await store.set_paused(True)
    assert (await _admit(store, clock, visitor, tight)).refused == "chat_paused"
    await store.set_paused(False)
    capped = replace(tight, daily_cap_micro=tight.reserve_micro)  # room for one reservation
    refused = await _admit(store, clock, visitor, capped)
    assert refused.refused == "daily_cap" and refused.retry_after_s == 12 * 3600
    refused = await _admit(store, clock, visitor, tight)
    assert (refused.refused, refused.limit) == ("rate_limited", "hour")
    clock.advance(3601)  # the hour has passed; the day hasn't
    refused = await _admit(store, clock, visitor, tight)
    assert (refused.refused, refused.limit) == ("rate_limited", "day")
    assert refused.retry_after_s == 12 * 3600 - 3601


async def rolling_hour(store: CommandStore, clock: Clock) -> None:
    """Three in the hour, then a refusal until the oldest leaves the window."""
    visitor = _visitor()
    start = clock()
    for left in (2, 1, 0):
        admission = await _admit(store, clock, visitor)
        assert admission.admitted and admission.hour_left == left
        clock.advance(10)
    clock.t = start + 30
    refused = await _admit(store, clock, visitor)
    assert (refused.refused, refused.limit) == ("rate_limited", "hour")
    assert refused.retry_after_s == 3600 - 30
    clock.t = start + 3601  # the first has left the hour
    assert (await _admit(store, clock, visitor)).admitted
    clock.t = start + 3605
    refused = await _admit(store, clock, visitor)
    assert refused.refused == "rate_limited" and refused.retry_after_s == 5  # the second's turn


async def day_resets_at_midnight(store: CommandStore, clock: Clock) -> None:
    """The visitor's day ends at 00:00 UTC."""
    clock.t = NEAR_MIDNIGHT
    visitor = _visitor()
    daily = replace(POLICY, per_hour=10, per_day=2)
    assert (await _admit(store, clock, visitor, daily)).admitted
    assert (await _admit(store, clock, visitor, daily)).admitted
    refused = await _admit(store, clock, visitor, daily)
    assert (refused.refused, refused.limit, refused.retry_after_s) == ("rate_limited", "day", 60)
    clock.advance(61)
    admitted = await _admit(store, clock, visitor, daily)
    assert admitted.admitted and admitted.day_left == 1 and admitted.day != refused.day


async def reserve_and_settle(store: CommandStore, clock: Clock) -> None:
    """Admission holds the reservation; settling adds the cost and releases it."""
    visitor = _visitor()
    admission = await _admit(store, clock, visitor)
    found = await _status(store, clock, visitor)
    assert (found.reserved_micro, found.spend_micro, found.questions) == (100_000, 0, 1)
    await store.settle(admission, 12_345, refund_quota=False)
    found = await _status(store, clock, visitor)
    assert (found.reserved_micro, found.spend_micro, found.questions) == (0, 12_345, 1)
    assert (found.hour_used, found.day_used) == (1, 1)


async def refunds(store: CommandStore, clock: Clock) -> None:
    """A refund gives the question back everywhere it was counted, never the spend."""
    visitor = _visitor()
    admission = await _admit(store, clock, visitor)
    await store.settle(admission, 2_000, refund_quota=True)
    found = await _status(store, clock, visitor)
    assert (found.questions, found.hour_used, found.day_used) == (0, 0, 0)
    assert (found.reserved_micro, found.spend_micro) == (0, 2_000)


async def kill_switch(store: CommandStore, clock: Clock) -> None:
    visitor = _visitor()
    await store.set_paused(True)
    assert (await _admit(store, clock, visitor)).refused == "chat_paused"
    assert (await _status(store, clock, None)).paused
    await store.set_paused(False)
    assert not (await _status(store, clock, None)).paused
    assert (await _admit(store, clock, visitor)).admitted


async def refusals_leave_the_counters(store: CommandStore, clock: Clock) -> None:
    """A refused attempt, for any reason, leaves every counter as it was."""
    visitor = _visitor()
    tight = replace(POLICY, per_hour=1)
    assert (await _admit(store, clock, visitor, tight)).admitted
    before = await _status(store, clock, visitor)
    assert (await _admit(store, clock, visitor, tight)).refused == "rate_limited"
    assert await _status(store, clock, visitor) == before
    capped = replace(POLICY, daily_cap_micro=0)
    assert (await _admit(store, clock, _visitor(), capped)).refused == "daily_cap"
    assert await _status(store, clock, visitor) == before
    await store.set_paused(True)
    assert (await _admit(store, clock, _visitor())).refused == "chat_paused"
    await store.set_paused(False)
    assert await _status(store, clock, visitor) == before


async def concurrent_hour(store: CommandStore, clock: Clock) -> None:
    """Twenty at once from one visitor with ten an hour: exactly ten are admitted."""
    visitor = _visitor()
    many = replace(POLICY, per_hour=10, per_day=100, daily_cap_micro=100 * 100_000)
    results: list[Admission] = []

    async def one() -> None:
        results.append(await _admit(store, clock, visitor, many))

    async with anyio.create_task_group() as tg:
        for _ in range(20):
            tg.start_soon(one)
    assert sum(a.admitted for a in results) == 10
    found = await _status(store, clock, visitor)
    assert (found.hour_used, found.questions, found.reserved_micro) == (10, 10, 10 * 100_000)


async def concurrent_cap(store: CommandStore, clock: Clock) -> None:
    """Two at once when the cap has room for one reservation: exactly one is admitted."""
    room_for_one = replace(POLICY, daily_cap_micro=150_000)
    results: list[Admission] = []

    async def one() -> None:
        results.append(await _admit(store, clock, _visitor(), room_for_one))

    async with anyio.create_task_group() as tg:
        tg.start_soon(one)
        tg.start_soon(one)
    assert sorted(str(a.refused) for a in results) == ["None", "daily_cap"]
    assert (await _status(store, clock, None)).reserved_micro == 100_000


async def settled_after_midnight(store: CommandStore, clock: Clock) -> None:
    """A question admitted before midnight and settled after it releases the earlier day's
    reservation and leaves the new day's at 0."""
    clock.t = NEAR_MIDNIGHT + 58
    before = clock()
    admission = await _admit(store, clock, _visitor())
    clock.advance(4)  # the next UTC day
    await store.settle(admission, 5_000, refund_quota=False)
    today = await _status(store, clock, None)
    assert (today.reserved_micro, today.spend_micro, today.questions) == (0, 0, 0)
    earlier = await store.status(None, before, POLICY)
    assert (earlier.reserved_micro, earlier.spend_micro, earlier.questions) == (0, 5_000, 1)


async def late_writes_keep_an_expiry(store: CommandStore, clock: Clock) -> None:
    """A settle or refund after its keys expired leaves no key without an expiry (DECR and
    INCRBY would recreate them without one)."""
    visitor = _visitor()
    admission = await _admit(store, clock, visitor)
    spend, reserved, questions = store.day_keys(admission.day)
    today = store.visitor_day_key(admission.day, visitor)
    hour = store.hour_key(visitor)
    keys = [spend, reserved, questions, today, hour]
    await store.run([["DEL", *keys]], atomic=True)  # as if every key had expired
    await store.settle(admission, 1_000, refund_quota=True)
    ttls = await store.run([["TTL", key] for key in keys], atomic=False)
    assert all(ttl != -1 for ttl in ttls), dict(zip(keys, ttls, strict=True))
    assert ttls[-1] == -2  # ZREM never recreates the hour


async def status_counts_nothing(store: CommandStore, clock: Clock) -> None:
    visitor = _visitor()
    await _admit(store, clock, visitor)
    first = await _status(store, clock, visitor)
    assert first == await _status(store, clock, visitor)
    assert (first.questions, first.hour_used, first.day_used) == (1, 1, 1)
    assert first.oldest_ms == int(clock() * 1000)


CASES: tuple[Case, ...] = (
    refusal_order,
    rolling_hour,
    day_resets_at_midnight,
    reserve_and_settle,
    refunds,
    kill_switch,
    refusals_leave_the_counters,
    concurrent_hour,
    concurrent_cap,
    settled_after_midnight,
    late_writes_keep_an_expiry,
    status_counts_nothing,
)


async def run_case(case: Case, factory: Factory) -> None:
    """Run one case on a fresh store (closed afterwards)."""
    clock = Clock()
    store = factory(clock, case.__name__)
    try:
        await case(store, clock)
    finally:
        await store.aclose()
