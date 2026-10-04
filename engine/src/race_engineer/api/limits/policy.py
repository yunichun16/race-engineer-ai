"""What the chat's limits allow, the records a question leaves, and the decision they lead to.

Amounts are integer micro-dollars (1 dollar = 1,000,000), the same in every store, so the
counters are plain `INCRBY` integers with no rounding. Days are UTC dates ("2026-10-04").

A question is admitted against, in this order: the kill switch (`chat_paused`), the day's
spending cap and the global question backstop (`daily_cap`), the visitor's rolling hour and the
visitor's UTC day (`rate_limited`, with `limit` "hour" or "day"). Admission reserves
`reserve_micro` for the question while it runs; settling adds what the question cost and
releases the reservation (`ChatGuard.settle`).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MICRO = 1_000_000  # micro-dollars per dollar
HOUR_S = 3600
HOUR_MS = HOUR_S * 1000
DAY_KEY_TTL_S = 48 * 3600  # the day's global counters (spend, reserved, questions)
# Charged for an API call still in flight when its question ended with no usage reported: the
# provider may bill its input.
IN_FLIGHT_ESTIMATE_USD = 0.02

# Outcomes that leave the visitor's question uncounted: the conversation didn't move on, through
# no fault of the visitor. `internal_error` is refunded too when no API call was made.
REFUNDED_OUTCOMES = frozenset(
    {
        "not_started",
        "busy",
        "upstream_busy",
        "upstream_unavailable",
        "upstream_unreachable",
        "chat_not_configured",
    }
)


def micro(usd: float) -> int:
    """Dollars as whole micro-dollars."""
    return round(usd * MICRO)


def utc_day(now: float) -> str:
    """The UTC date of `now` (epoch seconds)."""
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def day_end(day: str) -> float:
    """Epoch seconds of the 00:00 UTC that ends `day`."""
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return (start + timedelta(days=1)).timestamp()


def until_midnight(now: float) -> int:
    """Whole seconds from `now` to the next 00:00 UTC (at least 1)."""
    return max(1, math.ceil(day_end(utc_day(now)) - now))


def day_key_ttl(day: str, now: float) -> int:
    """Seconds a visitor's count for `day` is kept from `now`: until an hour after the day ends
    (at least 1, so a late write never leaves a key without an expiry)."""
    return max(1, math.ceil(day_end(day) + HOUR_S - now))


def refunded(outcome: str | None, api_calls: int = 0) -> bool:
    """Whether a question that ended with `outcome` gives the visitor's question back (never
    the spend). A question whose outcome was never recorded didn't start."""
    outcome = outcome or "not_started"
    return outcome in REFUNDED_OUTCOMES or (outcome == "internal_error" and api_calls == 0)


@dataclass(frozen=True)
class Policy:
    """The limits' numbers (RACE_ENGINEER_RATE_HOUR, _RATE_DAY, _DAILY_CAP_USD, _RESERVE_USD and
    _DAILY_QUESTIONS)."""

    per_hour: int = 10
    per_day: int = 25
    daily_cap_micro: int = 3 * MICRO
    reserve_micro: int = MICRO // 10
    daily_questions: int = 1000


@dataclass
class Admission:
    """What the store decided for one question, the keys it used, and the ledger the answer
    fills in for settling.

    The keys (`day`, `visitor`, `member`, `reserve_micro`) are the ones the admission counted
    under: a refusal's undo, the settling and a refund use exactly them, so a question admitted
    before midnight and settled after it releases the earlier day's reservation. `refused` is
    None for an admitted question, else `chat_paused`, `daily_cap` or `rate_limited` (with
    `limit` and `retry_after_s`). `hour_left` and `day_left` count this question as asked.

    The ledger: `QuestionRun` records `outcome`, `spent_usd` (what the question cost, 0 for the
    scripted chat unless counted), `in_flight_unbilled` (an API call was in flight with no usage
    reported) and `api_calls` (requests sent); `ChatGuard.settle` reads them once and sets
    `settled`."""

    store: str
    day: str
    visitor: str
    member: str
    reserve_micro: int
    refused: str | None = None
    retry_after_s: int | None = None
    limit: str | None = None
    hour_left: int = 0
    day_left: int = 0
    outcome: str | None = None
    spent_usd: float = 0.0
    in_flight_unbilled: bool = False
    api_calls: int = 0
    settled: bool = False

    @property
    def admitted(self) -> bool:
        return self.refused is None

    def charge_micro(self) -> int:
        """What settling adds to the day's spend."""
        estimate = IN_FLIGHT_ESTIMATE_USD if self.in_flight_unbilled else 0.0
        return max(0, micro(self.spent_usd + estimate))


@dataclass(frozen=True)
class StoreStatus:
    """The counters as they are, read without counting anything. The visitor's part is None
    when no visitor was asked about (the health check's global read)."""

    paused: bool = False
    spend_micro: int = 0
    reserved_micro: int = 0
    questions: int = 0
    hour_used: int | None = None
    day_used: int | None = None
    oldest_ms: int | None = None  # the visitor's oldest question in the rolling hour


@dataclass(frozen=True)
class Decision:
    refused: str | None = None
    retry_after_s: int | None = None
    limit: str | None = None


def decide(
    policy: Policy,
    now: float,
    *,
    paused: bool,
    questions: int,
    spend_micro: int,
    reserved_micro: int,
    hour_count: int | None = None,
    oldest_ms: int | None = None,
    day_count: int | None = None,
) -> Decision:
    """The decision for a question, from counts that include it (the question, its reservation,
    its place in the hour and the day); the visitor's counts may be None (not checked)."""
    if paused:
        return Decision("chat_paused")
    if (
        policy.daily_cap_micro <= 0  # a $0 cap closes the chat, whatever the reservation
        or questions > policy.daily_questions
        or spend_micro + max(reserved_micro, 0) > policy.daily_cap_micro
    ):
        return Decision("daily_cap", until_midnight(now))
    if hour_count is not None and hour_count > policy.per_hour:
        if oldest_ms is None:
            retry = HOUR_S
        else:
            retry = max(1, math.ceil((oldest_ms + HOUR_MS - now * 1000) / 1000))
        return Decision("rate_limited", retry, "hour")
    if day_count is not None and day_count > policy.per_day:
        return Decision("rate_limited", until_midnight(now), "day")
    return Decision()


def next_question(policy: Policy, now: float, status: StoreStatus) -> Decision:
    """The decision a new question would get now, from counts read without it (the status
    route and the health check)."""
    return decide(
        policy,
        now,
        paused=status.paused,
        questions=status.questions + 1,
        spend_micro=status.spend_micro,
        reserved_micro=status.reserved_micro + policy.reserve_micro,
        hour_count=None if status.hour_used is None else status.hour_used + 1,
        oldest_ms=status.oldest_ms,
        day_count=None if status.day_used is None else status.day_used + 1,
    )
