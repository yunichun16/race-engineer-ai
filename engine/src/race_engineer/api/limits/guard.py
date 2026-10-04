"""ChatGuard: admits a chat question against the limits before it is answered, and settles it
after (plan sections 3.1-3.3, 3.5-3.7, 3.9).

`accept()` (`routes/chat.py`) calls `admit(request)` after its own checks; a refusal is a
`ChatRejected` the route answers as JSON before any stream starts:

| status | code | when | retry_after_s |
|---|---|---|---|
| 429 | rate_limited (`limit`: hour or day) | over 10 in the rolling hour, or 25 today | a slot |
| 503 | daily_cap | spend and reservations would pass the cap, or 1,000 questions | 00:00 UTC |
| 503 | chat_paused | the kill switch (the store's key, or RACE_ENGINEER_PAUSED=1) | null |
| 503 | chat_unavailable | the store unconfigured or unreachable, or no client address | 60 |

The limits fail closed: when the store can't be read the chat is unavailable, never unlimited
(the tools and /mcp cost no model money and keep answering). After a store failure the chat stays
unavailable for 30 s before the store is tried again, so an outage costs one timeout, not one per
request.

`settle(admission)` runs in `accept()`'s `finally`, once, shielded from cancellation: it adds
what the question cost (`Admission.charge_micro`: the runner's own accounting, plus $0.02 for an
API call cut off in flight), releases the $0.10 reservation and gives the visitor's question back
for the outcomes in `policy.REFUNDED_OUTCOMES`. A failed settle leaves the reservation counted
until the day ends, which errs on the safe side.

Upstash's free plan counts every command (500K a month; a question costs about 25), so traffic
that would only be refused is kept off it, in process (valid because the API runs in one
container): a refused visitor is refused again from memory until its `retry_after_s` (or until
one of their questions is refunded), the cap and the kill switch are remembered for 60 s,
`status` answers are cached for 10 s per visitor and the health check's global read for 60 s.
`status` gives what remembered refusals would give, so the page and the chat agree. The request
rates (`http.RequestRate`) come first.

Nothing here logs a visitor's code, an address or a question.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

import anyio
import anyio.to_thread

from race_engineer.api.chat.session import ChatRejected
from race_engineer.api.limits.identity import Identity, visitor_tag
from race_engineer.api.limits.memory import MemoryStore
from race_engineer.api.limits.policy import (
    Admission,
    Policy,
    StoreStatus,
    micro,
    next_question,
    refunded,
    until_midnight,
)
from race_engineer.api.limits.store import LimitStore, StoreError
from race_engineer.api.limits.upstash import UpstashStore

if TYPE_CHECKING:
    from starlette.requests import Request

    from race_engineer.api.settings import Settings

log = logging.getLogger(__name__)

STATUS_CACHE_S = 10  # GET /api/chat/status, per visitor
HEALTH_CACHE_S = 60  # the health check's global read
REMEMBER_GLOBAL_S = 60  # daily_cap and chat_paused, for everyone
BREAKER_S = 30  # after a store failure, before the store is tried again
UNAVAILABLE_RETRY_S = 60
SETTLE_TIMEOUT_S = 5.0
_PRUNE_AT = 2048  # remembered visitors before expired ones are dropped

# Refusal codes as the status route and the health check name them.
REASONS = {
    "chat_paused": "paused",
    "daily_cap": "daily_cap",
    "rate_limited": "rate_limited",
    "chat_unavailable": "unavailable",
}
NOT_CONFIGURED = "limits store not configured"
CLIENT_MISSING = "client address missing"
SECRET_MISSING = (
    "chat secret not set (RACE_ENGINEER_CHAT_SECRET): a restart ends every conversation"
)
STORE_DOWN = "limits store unreachable"


def _minutes(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def _hours_minutes(seconds: int) -> str:
    hours, minutes = divmod(max(1, math.ceil(seconds / 60)), 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def refusal(
    code: str, policy: Policy, retry_after_s: int | None = None, limit: str | None = None
) -> ChatRejected:
    """The ChatRejected for a refusal code, worded for the visitor (the site has its own copy;
    this is its fallback)."""
    if code == "rate_limited":
        retry = retry_after_s or 60
        if limit == "day":
            message = (
                f"That's today's {policy.per_day} questions from this connection. The count "
                f"resets at midnight UTC, in {_hours_minutes(retry)}."
            )
        else:
            message = (
                f"That's {policy.per_hour} questions in the last hour from this connection. "
                f"You can ask again in {_minutes(retry)}."
            )
        return ChatRejected(429, code, message, retry_after_s=retry, extra={"limit": limit})
    if code == "daily_cap":
        message = "The chat has used today's budget for this free demo. It resets at midnight UTC."
        return ChatRejected(503, code, message, retry_after_s=retry_after_s)
    if code == "chat_paused":
        return ChatRejected(503, code, "The chat is paused for now.", retry_after_s=None)
    message = "The chat can't check its limits right now, so it's off for a moment."
    return ChatRejected(503, "chat_unavailable", message, retry_after_s=UNAVAILABLE_RETRY_S)


@dataclass(frozen=True)
class ChatStatus:
    """GET /api/chat/status: whether a question would be admitted now, without counting one."""

    available: bool
    mode: str  # "anthropic", "fake" or "off"
    reason: str | None  # None, off, paused, daily_cap, rate_limited, unavailable
    retry_after_s: int | None = None
    quota: dict[str, int] | None = None  # null while the limits are off
    visitor: str | None = None  # the caller's own 6-hex tag; null without limits or an address
    saved: list[dict[str, str]] = field(default_factory=list)  # [{id, recorded_at}]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Cached:
    until: float
    reason: str | None
    retry_at: float | None
    quota: dict[str, int] | None


def chat_mode(state: Any) -> str:
    """The chat's mode from an app's state: "anthropic", "fake" or "off"."""
    chat = getattr(state, "chat", None)
    if chat is None or (hasattr(chat, "client") and chat.client is None):
        return "off"
    mode = getattr(chat, "mode", "off")
    return mode if mode in ("anthropic", "fake") else "off"


async def saved_entries(state: Any) -> list[dict[str, str]]:
    """The saved example answers on offer (`routes/saved.saved_index`), as id and recorded_at."""
    settings = getattr(state, "settings", None)
    if settings is None:
        return []
    from race_engineer.api.routes.saved import saved_index

    try:
        found = await anyio.to_thread.run_sync(
            saved_index, settings.saved_dir, settings.saved_allow_fake
        )
    except Exception:  # a broken folder never breaks the status
        log.exception("saved answers couldn't be listed")
        return []
    return [{"id": e["id"], "recorded_at": e["recorded_at"]} for e in found]


async def chat_status(request: Request) -> ChatStatus:
    """The status for a request, with or without a guard on the app."""
    state = request.app.state
    guard = getattr(state, "guard", None)
    if isinstance(guard, ChatGuard):
        return await guard.status(request)
    mode = chat_mode(state)
    reason = "off" if mode == "off" else None
    saved = await saved_entries(state)
    return ChatStatus(available=reason is None, mode=mode, reason=reason, saved=saved)


class ChatGuard:
    """The limits for one app (`build_guard` makes it from the settings at startup)."""

    def __init__(
        self,
        *,
        enabled: bool,
        store: LimitStore | None,
        policy: Policy,
        identity: Identity,
        paused: bool = False,
        problems: tuple[str, ...] = (),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.enabled = enabled
        self.store = store
        self.policy = policy
        self.identity = identity
        self.paused = paused
        self.clock = clock
        self._problems = problems
        self._refused: dict[str, tuple[float, str | None]] = {}  # visitor: (until, limit)
        self._global: tuple[str, float] | None = None  # (daily_cap or chat_paused, until)
        self._statuses: dict[str, _Cached] = {}
        self._health: tuple[float, StoreStatus] | None = None
        self._down_until = 0.0
        self._client_missing = False

    async def aclose(self) -> None:
        if self.store is not None:
            await self.store.aclose()

    # Admission and settling.

    async def admit(self, request: Request) -> Admission | None:
        """Count the question and hold its reservation, or raise ChatRejected. None when the
        limits are off (nothing to settle)."""
        now = self.clock()
        if self.paused:
            raise refusal("chat_paused", self.policy)
        if not self.enabled:
            return None
        visitor = self.identity.visitor(request.scope, now)
        if visitor is None:
            self._client_missing = True
            raise refusal("chat_unavailable", self.policy)
        self._statuses.pop(visitor, None)
        self._refuse_from_memory(visitor, now)
        store = self.store
        assert store is not None  # _refuse_from_memory refuses without one
        try:
            admission = await store.admit(visitor, now, self.policy)
        except Exception as exc:
            self._store_failed(exc, now)
            raise refusal("chat_unavailable", self.policy) from None
        if not admission.admitted:
            self._remember(admission, now)
            raise refusal(
                admission.refused or "chat_unavailable",
                self.policy,
                admission.retry_after_s,
                admission.limit,
            )
        return admission

    def _refuse_from_memory(self, visitor: str, now: float) -> None:
        """Raise what the store would answer, without asking it, while that is known."""
        if self.store is None:
            raise refusal("chat_unavailable", self.policy)
        remembered = self._remembered(visitor, now)
        if remembered is not None:
            code, retry, limit = remembered
            raise refusal(code, self.policy, retry, limit)
        if self._down_until > now:
            raise refusal("chat_unavailable", self.policy)

    def _remembered(self, visitor: str, now: float) -> tuple[str, int | None, str | None] | None:
        """The refusal a question from `visitor` gets from memory now, as (code, retry_after_s,
        limit), or None; what has run out is forgotten. `admit` and `status` both ask, so the
        page is told what a question would get."""
        if self._global is not None:
            code, until = self._global
            if until > now:
                return code, until_midnight(now) if code == "daily_cap" else None, None
            self._global = None
        remembered = self._refused.get(visitor)
        if remembered is not None:
            until, limit = remembered
            if until > now:
                return "rate_limited", math.ceil(until - now), limit
            del self._refused[visitor]
        return None

    def _remember(self, admission: Admission, now: float) -> None:
        retry = admission.retry_after_s or 0
        if admission.refused == "rate_limited":
            if len(self._refused) >= _PRUNE_AT:
                self._refused = {v: r for v, r in self._refused.items() if r[0] > now}
            self._refused[admission.visitor] = (now + retry, admission.limit)
        elif admission.refused == "daily_cap":
            self._global = ("daily_cap", now + min(REMEMBER_GLOBAL_S, max(retry, 1)))
        elif admission.refused == "chat_paused":
            self._global = ("chat_paused", now + REMEMBER_GLOBAL_S)

    def _store_failed(self, exc: Exception, now: float) -> None:
        self._down_until = now + BREAKER_S
        self._health = None
        if isinstance(exc, StoreError):
            log.warning("limits: %s; the chat is unavailable for %d s", exc, BREAKER_S)
        else:
            log.exception("limits: the store failed; the chat is unavailable for %d s", BREAKER_S)

    async def settle(self, admission: Admission | None) -> None:
        """Charge the question, release its reservation and refund it if its outcome says so.
        Once per admission (later calls do nothing), shielded from cancellation, at most 5 s,
        and never raises."""
        if admission is None or admission.settled:
            return
        admission.settled = True
        self._statuses.pop(admission.visitor, None)
        store = self.store
        if store is None:
            return
        charge = admission.charge_micro()
        refund = refunded(admission.outcome, admission.api_calls)
        if refund:
            # A refusal remembered while this question was in flight counted it; the visitor
            # has a question back, so the store decides their next one.
            self._refused.pop(admission.visitor, None)
        with anyio.CancelScope(shield=True), anyio.move_on_after(SETTLE_TIMEOUT_S) as scope:
            try:
                await store.settle(admission, charge, refund)
            except Exception as exc:
                log.warning(
                    "limits: settling failed (%s); the reservation stays counted until the day "
                    "ends",
                    exc if isinstance(exc, StoreError) else type(exc).__name__,
                )
        if scope.cancelled_caught:
            log.warning("limits: settling timed out; the reservation stays counted")

    # What a page or a health check can read.

    async def status(self, request: Request) -> ChatStatus:
        """Whether this caller's next question would be admitted, read without counting it."""
        now = self.clock()
        state = request.app.state
        mode = chat_mode(state)
        saved = await saved_entries(state)
        visitor = self.identity.visitor(request.scope, now) if self.enabled else None
        tag = visitor_tag(visitor) if visitor else None

        def answer(
            reason: str | None,
            retry_at: float | None = None,
            quota: dict[str, int] | None = None,
        ) -> ChatStatus:
            retry = None if retry_at is None else max(1, math.ceil(retry_at - now))
            return ChatStatus(reason is None, mode, reason, retry, quota, tag, saved)

        if mode == "off":
            return answer("off")
        if self.paused:
            return answer("paused")
        if not self.enabled:
            return answer(None)
        unavailable_until = now + UNAVAILABLE_RETRY_S
        if visitor is None:
            self._client_missing = True
            return answer("unavailable", unavailable_until)
        if self.store is None or self._down_until > now:
            return answer("unavailable", unavailable_until)
        cached = self._statuses.get(visitor)
        if cached is None or cached.until <= now:
            try:
                found = await self.store.status(visitor, now, self.policy)
            except Exception as exc:
                self._store_failed(exc, now)
                return answer("unavailable", unavailable_until)
            decision = next_question(self.policy, now, found)
            quota = {
                "hour_left": max(0, self.policy.per_hour - (found.hour_used or 0)),
                "day_left": max(0, self.policy.per_day - (found.day_used or 0)),
                "per_hour": self.policy.per_hour,
                "per_day": self.policy.per_day,
            }
            reason = REASONS.get(decision.refused or "")
            retry_at = None if decision.retry_after_s is None else now + decision.retry_after_s
            if len(self._statuses) >= _PRUNE_AT:
                self._statuses = {v: c for v, c in self._statuses.items() if c.until > now}
            cached = self._statuses[visitor] = _Cached(
                now + STATUS_CACHE_S, reason, retry_at, quota
            )
        # A question is refused from memory first (`admit`), so the page is told the same.
        remembered = self._remembered(visitor, now)
        if remembered is not None:
            code, retry, _ = remembered
            return answer(REASONS[code], None if retry is None else now + retry, cached.quota)
        return answer(cached.reason, cached.retry_at, cached.quota)

    async def health(self, mode: str) -> tuple[str | None, dict[str, Any]]:
        """The chat's `reason` for the health check (None, off, paused, daily_cap, unavailable)
        and its `limits` part. No dollar amounts."""
        now = self.clock()
        reason = await self._health_reason(mode, now)
        limits = {
            "enabled": self.enabled,
            "store": self.store.name if self.enabled and self.store is not None else None,
            "per_hour": self.policy.per_hour,
            "per_day": self.policy.per_day,
            "problems": self.problems(now),
        }
        return reason, limits

    async def _health_reason(self, mode: str, now: float) -> str | None:
        if mode == "off":
            return "off"
        if self.paused:
            return "paused"
        if not self.enabled:
            return None
        if self.store is None or self._down_until > now:
            return "unavailable"
        if self._global is not None and self._global[1] > now:
            return REASONS[self._global[0]]
        if self._health is None or self._health[0] <= now:
            try:
                found = await self.store.status(None, now, self.policy)
            except Exception as exc:
                self._store_failed(exc, now)
                return "unavailable"
            self._health = (now + HEALTH_CACHE_S, found)
        decision = next_question(self.policy, now, self._health[1])
        return REASONS.get(decision.refused or "")

    def problems(self, now: float | None = None) -> list[str]:
        now = self.clock() if now is None else now
        problems = list(self._problems)
        if self._client_missing:
            problems.append(CLIENT_MISSING)
        if self.enabled and self._down_until > now:
            problems.append(STORE_DOWN)
        return problems


def limits_enabled(settings: Settings, chat_mode: str) -> bool:
    """RACE_ENGINEER_LIMITS: `on`, `off`, or `auto` (on for the real model on a public host)."""
    if settings.limits == "auto":
        return chat_mode == "anthropic" and not settings.loopback
    return settings.limits == "on"


def policy_of(settings: Settings) -> Policy:
    return Policy(
        per_hour=settings.rate_hour,
        per_day=settings.rate_day,
        daily_cap_micro=micro(settings.daily_cap_usd),
        reserve_micro=micro(settings.reserve_usd),
        daily_questions=settings.daily_questions,
    )


def build_guard(
    settings: Settings, chat_mode: str, *, store: LimitStore | None = None
) -> ChatGuard:
    """The guard for an app whose chat started in `chat_mode`. `store` replaces the one the
    settings choose (tests); the guard keeps the store's clock when it has one."""
    enabled = limits_enabled(settings, chat_mode)
    problems: list[str] = []
    if not settings.loopback and not settings.chat_secret_set and chat_mode != "off":
        log.warning(
            "chat: RACE_ENGINEER_CHAT_SECRET is not set on a public host: %s", SECRET_MISSING
        )
        problems.append(SECRET_MISSING)
    if enabled and store is None:
        url, token = settings.upstash_url, settings.upstash_token
        if settings.limits_store == "memory" or (
            settings.limits_store == "auto" and not (url and token)
        ):
            store = MemoryStore()
        elif url and token:
            store = UpstashStore.from_credentials(url, token)
        else:
            problems.append(NOT_CONFIGURED)
            log.warning(
                "limits: %s (UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN); the chat is "
                "unavailable",
                NOT_CONFIGURED,
            )
    clock: Callable[[], float] = getattr(store, "clock", time.time)
    guard = ChatGuard(
        enabled=enabled,
        store=store if enabled else None,
        policy=policy_of(settings),
        identity=Identity(settings.chat_secret, settings.trusted_proxy_hops, settings.loopback),
        paused=settings.paused,
        problems=tuple(problems),
        clock=clock,
    )
    log.info(
        "limits: %s%s",
        f"on, store {guard.store.name if guard.store else 'missing'}, {settings.rate_hour}/hour, "
        f"{settings.rate_day}/day, cap ${settings.daily_cap_usd:.2f}/day"
        if enabled
        else "off",
        " (paused: RACE_ENGINEER_PAUSED=1)" if settings.paused else "",
    )
    return guard


def guard_for(state: Any) -> ChatGuard | None:
    """The app's guard, or None (an app without one, as in tests of the chat route alone)."""
    guard = getattr(state, "guard", None)
    return guard if isinstance(guard, ChatGuard) else None
