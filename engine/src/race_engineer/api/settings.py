"""The API's settings, read once at startup from environment variables (all optional).

- RACE_ENGINEER_DATA (<repo>/data): the dataset and the M4 results, read by `config.py`.
- RACE_ENGINEER_HOST (127.0.0.1): the host the API binds to; `race-engineer-api --host` sets it.
- RACE_ENGINEER_CORS_ORIGINS (http://localhost:3000,http://127.0.0.1:3000): the browser origins
  allowed to call the API, comma-separated (the Next.js dev server).
- RACE_ENGINEER_MOUNT_MCP (1): serve the MCP server at /mcp.
- RACE_ENGINEER_MCP_HOST_CHECK (1): while bound to localhost, /mcp refuses other Host headers
  (the MCP SDK's DNS-rebinding protection).
- RACE_ENGINEER_TOOL_CONCURRENCY (2): heavy tools (explain_corner, compare_laps) running at once.
- RACE_ENGINEER_CHAT (auto): `auto` (on when Anthropic credentials resolve), `off` or `fake` (the
  scripted stand-in for Claude); RACE_ENGINEER_CHAT_FAKE=1 is the same as `fake`.
- RACE_ENGINEER_CHAT_MODEL (claude-sonnet-5-5) and RACE_ENGINEER_CHAT_EFFORT (medium).
- RACE_ENGINEER_CHAT_MAX_TURNS (8): questions per conversation.
- RACE_ENGINEER_CHAT_MAX_ITERATIONS (6): API calls per question.
- RACE_ENGINEER_CHAT_CONCURRENCY (4): chats one process answers at once.
- RACE_ENGINEER_CHAT_SECRET (random per process): the key that signs conversation histories.
- RACE_ENGINEER_CHAT_FALLBACKS (1): send `fallbacks: "default"` (Claude API only).
- RACE_ENGINEER_CHAT_EAGER (1): `eager_input_streaming` on the chat's tools.
- RACE_ENGINEER_CHAT_MAX_COST_USD (0.30): a question stops before an API call once it has cost
  this much.

The guardrails (M7; `api/limits/`):
- RACE_ENGINEER_LIMITS (auto): `on`, `off`, or `auto`, which is on when the chat uses the real
  model and the API is bound to a host other than loopback (the deployment).
- RACE_ENGINEER_LIMITS_STORE (auto): `memory`, `upstash`, or `auto` (Upstash when both
  UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are set, else memory). `upstash` without
  them keeps the limits on and the chat unavailable. The URL (https://, the REST URL) and the
  token are never logged.
- RACE_ENGINEER_RATE_HOUR (10) and RACE_ENGINEER_RATE_DAY (25): questions per visitor in the
  rolling hour and in the UTC day.
- RACE_ENGINEER_DAILY_CAP_USD (3.00): the day's spending cap (0 closes the chat);
  RACE_ENGINEER_RESERVE_USD (0.10) is held for each question in flight;
  RACE_ENGINEER_DAILY_QUESTIONS (1000): questions a day from everyone together.
- RACE_ENGINEER_LIMITS_COUNT_FAKE (0): count the scripted chat's notional cost (tests and the
  local cap check only).
- RACE_ENGINEER_PAUSED (0): the kill switch without a store.
- RACE_ENGINEER_TRUSTED_PROXY_HOPS (0): proxies in front of the API whose X-Forwarded-For
  entries are trusted, counted from the right (0: the connection's own address).
- RACE_ENGINEER_TOOLS_RPM (120), _TOOLS_GLOBAL_RPM (600), _MCP_RPM (240), _CHAT_RPM (20),
  _CHAT_GLOBAL_RPM (120), _STATUS_RPM (60): requests a minute, per visitor or in all, while the
  limits are on.
- RACE_ENGINEER_HEAVY_QUEUE (8): heavy tool calls that may wait for a slot; the next is 503 busy.
- RACE_ENGINEER_CHAT_MAX_BODY (655360) and RACE_ENGINEER_MCP_MAX_BODY (524288): request bodies,
  in bytes, on POST /api/chat and /mcp.
- RACE_ENGINEER_CORS_ORIGINS also takes `none` (no browser origin at all), and
  RACE_ENGINEER_CORS_ORIGIN_REGEX allows origins matching a pattern (Vercel previews; off).
- RACE_ENGINEER_MCP_STATELESS (0): /mcp without sessions, answering with JSON (one request, one
  response), for hosts that may restart or scale the container.
- RACE_ENGINEER_GZIP (1): gzip JSON responses of 1 KB or more (never the chat's event stream).
- RACE_ENGINEER_SSE_PAD (0): bytes of padding sent as a comment at the start of each answer, for
  a proxy or browser that holds back the first events.
- RACE_ENGINEER_SAVED ($RACE_ENGINEER_DATA/saved) and RACE_ENGINEER_SAVED_ALLOW_FAKE (0): the
  saved example answers, and whether scripted recordings are offered.

The Anthropic credentials are whatever the SDK resolves (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN
or an `ant auth login` profile); they are never read or logged here. When ANTHROPIC_BASE_URL
points anywhere other than api.anthropic.com, fallbacks and eager streaming, which only the
Claude API itself offers, are off unless set to 1 explicitly, and the app logs a warning at
startup: a gateway in between would see every question and the key.

A bad value (RACE_ENGINEER_CHAT=maybe, a concurrency of 0, a negative cap) stops startup with a
message naming the variable, rather than running with a setting nobody asked for.
"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

from race_engineer.config import PROCESSED_DIR, RESULTS_DIR, SAVED_DIR
from race_engineer.tools.registry import HEAVY_LIMIT, HEAVY_QUEUE, DataPaths

ChatSetting = Literal["auto", "off", "fake"]
LimitsSetting = Literal["auto", "on", "off"]
StoreSetting = Literal["auto", "memory", "upstash"]

ANTHROPIC_API_HOST = "api.anthropic.com"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
DEFAULT_CHAT_MODEL = "claude-sonnet-5-5"
NO_ORIGINS = "none"  # RACE_ENGINEER_CORS_ORIGINS=none: no browser origin may call the API
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _new_secret() -> bytes:
    return secrets.token_bytes(32)


@dataclass(frozen=True)
class Settings:
    """Everything the app reads from its environment. Tests build one directly, with temporary
    data folders and the chat off; `from_env` is what `race-engineer-api` uses."""

    processed: Path = PROCESSED_DIR
    results: Path = RESULTS_DIR
    host: str = DEFAULT_HOST
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    mount_mcp: bool = True
    # With a localhost host the MCP SDK accepts only localhost Host headers (DNS-rebinding
    # protection); tests, whose host is "testserver", turn the check off.
    mcp_host_check: bool = True
    tool_concurrency: int = HEAVY_LIMIT
    chat: ChatSetting = "auto"
    chat_model: str = DEFAULT_CHAT_MODEL
    chat_effort: str = "medium"
    chat_max_turns: int = 8
    chat_max_iterations: int = 6
    chat_concurrency: int = 4
    chat_secret: bytes = field(default_factory=_new_secret, repr=False)
    chat_fallbacks: bool = True
    chat_eager: bool = True
    # The host of ANTHROPIC_BASE_URL (only the host: the URL could carry a token).
    base_url_host: str = ANTHROPIC_API_HOST
    # Whether RACE_ENGINEER_CHAT_SECRET was set (a random secret ends every conversation on a
    # restart, so a public server without one is reported).
    chat_secret_set: bool = field(default=False, repr=False)
    chat_max_cost_usd: float = 0.30
    # The guardrails (api/limits/).
    limits: LimitsSetting = "auto"
    limits_store: StoreSetting = "auto"
    upstash_url: str = field(default="", repr=False)
    upstash_token: str = field(default="", repr=False)
    rate_hour: int = 10
    rate_day: int = 25
    daily_cap_usd: float = 3.00
    reserve_usd: float = 0.10
    daily_questions: int = 1000
    limits_count_fake: bool = False
    paused: bool = False
    trusted_proxy_hops: int = 0
    tools_rpm: int = 120
    tools_global_rpm: int = 600
    mcp_rpm: int = 240
    chat_rpm: int = 20
    chat_global_rpm: int = 120
    status_rpm: int = 60
    heavy_queue: int = HEAVY_QUEUE
    chat_max_body: int = 640 * 1024
    mcp_max_body: int = 512 * 1024
    cors_origin_regex: str | None = None
    mcp_stateless: bool = False
    gzip: bool = True
    sse_pad: int = 0
    saved_dir: Path = SAVED_DIR
    saved_allow_fake: bool = False

    @property
    def paths(self) -> DataPaths:
        return DataPaths(self.processed, self.results)

    @property
    def third_party_base_url(self) -> bool:
        """Whether ANTHROPIC_BASE_URL sends the chat somewhere other than the Claude API."""
        return self.base_url_host != ANTHROPIC_API_HOST

    @property
    def loopback(self) -> bool:
        """Whether the API is bound to this machine only (127.0.0.1, localhost, ::1)."""
        return self.host.strip("[]").lower() in LOOPBACK_HOSTS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """The settings from `environ` (default: the process environment), defaults for the
        variables that aren't set. ValueError names a variable with a value that can't be used."""
        env = _Env(os.environ if environ is None else environ)
        base_url_host = _host(env.text("ANTHROPIC_BASE_URL", "")) or ANTHROPIC_API_HOST
        direct = base_url_host == ANTHROPIC_API_HOST
        chat = env.choice("RACE_ENGINEER_CHAT", "auto", get_args(ChatSetting))
        if env.flag("RACE_ENGINEER_CHAT_FAKE", False):
            chat = "fake"
        secret = env.text("RACE_ENGINEER_CHAT_SECRET", "")
        return cls(
            host=env.text("RACE_ENGINEER_HOST", DEFAULT_HOST),
            cors_origins=env.items("RACE_ENGINEER_CORS_ORIGINS", DEFAULT_CORS_ORIGINS),
            mount_mcp=env.flag("RACE_ENGINEER_MOUNT_MCP", True),
            mcp_host_check=env.flag("RACE_ENGINEER_MCP_HOST_CHECK", True),
            tool_concurrency=env.count("RACE_ENGINEER_TOOL_CONCURRENCY", HEAVY_LIMIT),
            chat=chat,
            chat_model=env.text("RACE_ENGINEER_CHAT_MODEL", DEFAULT_CHAT_MODEL),
            chat_effort=env.text("RACE_ENGINEER_CHAT_EFFORT", "medium"),
            chat_max_turns=env.count("RACE_ENGINEER_CHAT_MAX_TURNS", 8),
            chat_max_iterations=env.count("RACE_ENGINEER_CHAT_MAX_ITERATIONS", 6),
            chat_concurrency=env.count("RACE_ENGINEER_CHAT_CONCURRENCY", 4),
            chat_secret=secret.encode() if secret else _new_secret(),
            chat_fallbacks=env.flag("RACE_ENGINEER_CHAT_FALLBACKS", direct),
            chat_eager=env.flag("RACE_ENGINEER_CHAT_EAGER", direct),
            base_url_host=base_url_host,
            chat_secret_set=bool(secret),
            chat_max_cost_usd=env.money("RACE_ENGINEER_CHAT_MAX_COST_USD", 0.30, positive=True),
            limits=env.choice("RACE_ENGINEER_LIMITS", "auto", get_args(LimitsSetting)),
            limits_store=env.choice("RACE_ENGINEER_LIMITS_STORE", "auto", get_args(StoreSetting)),
            upstash_url=env.secret_url("UPSTASH_REDIS_REST_URL"),
            upstash_token=env.text("UPSTASH_REDIS_REST_TOKEN", ""),
            rate_hour=env.count("RACE_ENGINEER_RATE_HOUR", 10),
            rate_day=env.count("RACE_ENGINEER_RATE_DAY", 25),
            daily_cap_usd=env.money("RACE_ENGINEER_DAILY_CAP_USD", 3.00),
            reserve_usd=env.money("RACE_ENGINEER_RESERVE_USD", 0.10),
            daily_questions=env.count("RACE_ENGINEER_DAILY_QUESTIONS", 1000),
            limits_count_fake=env.flag("RACE_ENGINEER_LIMITS_COUNT_FAKE", False),
            paused=env.flag("RACE_ENGINEER_PAUSED", False),
            trusted_proxy_hops=env.whole("RACE_ENGINEER_TRUSTED_PROXY_HOPS", 0),
            tools_rpm=env.count("RACE_ENGINEER_TOOLS_RPM", 120),
            tools_global_rpm=env.count("RACE_ENGINEER_TOOLS_GLOBAL_RPM", 600),
            mcp_rpm=env.count("RACE_ENGINEER_MCP_RPM", 240),
            chat_rpm=env.count("RACE_ENGINEER_CHAT_RPM", 20),
            chat_global_rpm=env.count("RACE_ENGINEER_CHAT_GLOBAL_RPM", 120),
            status_rpm=env.count("RACE_ENGINEER_STATUS_RPM", 60),
            heavy_queue=env.whole("RACE_ENGINEER_HEAVY_QUEUE", HEAVY_QUEUE),
            chat_max_body=env.count("RACE_ENGINEER_CHAT_MAX_BODY", 640 * 1024),
            mcp_max_body=env.count("RACE_ENGINEER_MCP_MAX_BODY", 512 * 1024),
            cors_origin_regex=env.pattern("RACE_ENGINEER_CORS_ORIGIN_REGEX"),
            mcp_stateless=env.flag("RACE_ENGINEER_MCP_STATELESS", False),
            gzip=env.flag("RACE_ENGINEER_GZIP", True),
            sse_pad=env.whole("RACE_ENGINEER_SSE_PAD", 0),
            saved_dir=env.path("RACE_ENGINEER_SAVED", SAVED_DIR),
            saved_allow_fake=env.flag("RACE_ENGINEER_SAVED_ALLOW_FAKE", False),
        )


def _host(url: str) -> str:
    """The host of a URL ("" for none); a bare host name is taken as it is."""
    url = url.strip()
    if not url:
        return ""
    return (urlsplit(url if "//" in url else f"//{url}").hostname or "").lower()


class _Env:
    """Typed reads of environment variables; an empty value counts as unset."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def text(self, name: str, default: str) -> str:
        return self._environ.get(name, "").strip() or default

    def flag(self, name: str, default: bool) -> bool:
        value = self.text(name, "").lower()
        if not value:
            return default
        if value in _TRUE:
            return True
        if value in _FALSE:
            return False
        raise ValueError(f"{name} must be 1 or 0 (or true/false), not {value!r}.")

    def count(self, name: str, default: int) -> int:
        return self.whole(name, default, minimum=1)

    def whole(self, name: str, default: int, minimum: int = 0) -> int:
        value = self.text(name, "")
        if not value:
            return default
        try:
            number = int(value)
        except ValueError:
            number = minimum - 1
        if number < minimum:
            raise ValueError(f"{name} must be a whole number of at least {minimum}, not {value!r}.")
        return number

    def money(self, name: str, default: float, *, positive: bool = False) -> float:
        """Dollars: at least 0, or more than 0 when `positive`."""
        value = self.text(name, "").removeprefix("$")
        if not value:
            return default
        try:
            number = float(value)
        except ValueError:
            number = float("nan")
        if not number >= 0 or (positive and number == 0) or number == float("inf"):
            least = "more than 0" if positive else "at least 0"
            raise ValueError(f"{name} must be an amount in dollars, {least}, not {value!r}.")
        return number

    def choice(self, name: str, default: str, choices: tuple[str, ...]) -> Any:
        """One of `choices`, for a Literal setting (the caller's annotation says which)."""
        value = self.text(name, default).lower()
        if value not in choices:
            raise ValueError(f"{name} must be one of {', '.join(choices)}, not {value!r}.")
        return value

    def items(self, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
        value = self.text(name, "")
        if not value:
            return default
        if value.lower() == NO_ORIGINS:
            return ()
        return tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())

    def secret_url(self, name: str) -> str:
        """An https:// URL that is never printed, not even in this error; empty when unset."""
        value = self.text(name, "")
        if value and urlsplit(value).scheme != "https":
            raise ValueError(f"{name} must be an https:// URL (the REST URL, not redis://).")
        return value

    def path(self, name: str, default: Path) -> Path:
        value = self.text(name, "")
        return Path(value).expanduser() if value else default

    def pattern(self, name: str) -> str | None:
        """A regular expression, checked here so a typo stops startup; None when unset."""
        value = self.text(name, "")
        if not value:
            return None
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"{name} isn't a valid regular expression ({exc}).") from None
        return value
