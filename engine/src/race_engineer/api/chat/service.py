"""The chat as an app holds it: its settings, its Anthropic client and what it froze at
startup (the tool definitions, the prompt version and the history-signing key).

The API app (`api/app.py`) starts the client in its lifespan and keeps it on `app.state.chat` (a
`ChatState`: the mode and the client). `service_for(app.state)` builds a `ChatService` on that
client the first time the chat route needs one, with the app's settings, specs and data paths,
and keeps it on `app.state.chat_service` (a new one when a restarted lifespan brings a new
client). Tests and scripts without that app call `create_chat(config, specs, paths)`, which
makes the client itself, and put the service straight on `app.state.chat`.

The settings are the API's (`api/settings.py`), which reads RACE_ENGINEER_CHAT (auto, off or
fake), RACE_ENGINEER_CHAT_MODEL, _EFFORT, _MAX_TURNS, _MAX_ITERATIONS, _CONCURRENCY, _SECRET,
_FALLBACKS, _EAGER and _MAX_COST_USD, and RACE_ENGINEER_LIMITS_COUNT_FAKE, from the
environment. Credentials are whatever the Anthropic SDK resolves and are never logged. Behind
an ANTHROPIC_BASE_URL other than api.anthropic.com, fallbacks and eager input streaming are off
unless set to 1: a proxy may reject them, and it sees every question and the key.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from anthropic import AsyncAnthropic
from fastapi.sse import ServerSentEvent

from race_engineer.api.chat import runner
from race_engineer.api.chat.fake import SCRIPTED_MODEL, ScriptedClaude, scripted_client
from race_engineer.api.chat.prompt import prompt_version
from race_engineer.api.chat.session import check_history
from race_engineer.api.chat.tools import tool_definition
from race_engineer.api.limits.policy import Admission
from race_engineer.api.settings import Settings
from race_engineer.tools.registry import DataPaths, ToolSpec
from race_engineer.tools.sessions import list_sessions

log = logging.getLogger(__name__)

ANTHROPIC_HOST = "api.anthropic.com"
Mode = Literal["anthropic", "fake"]


@dataclass(frozen=True)
class ChatConfig:
    """The chat's settings."""

    mode: Literal["auto", "off", "fake"] = "auto"
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"
    max_turns: int = 8
    max_iterations: int = 6
    concurrency: int = 4
    secret: bytes | None = field(default=None, repr=False)  # None: random per process
    fallbacks: bool | None = None  # None: on for the Claude API, off behind another base URL
    eager: bool | None = None  # same rule
    max_tokens: int = 16_000
    timeout_s: float = 120.0  # for one question, all its API calls and tools together
    # A question stops before its next API call once it has cost this much (too_costly).
    max_cost_usd: float = 0.30
    # Count the scripted chat's notional cost towards the daily cap (tests, the local cap check).
    count_fake: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> ChatConfig:
        """The chat part of the API's settings."""
        return cls(
            mode=settings.chat,
            model=settings.chat_model,
            effort=settings.chat_effort,
            max_turns=settings.chat_max_turns,
            max_iterations=settings.chat_max_iterations,
            concurrency=settings.chat_concurrency,
            secret=settings.chat_secret,
            fallbacks=settings.chat_fallbacks,
            eager=settings.chat_eager,
            max_cost_usd=settings.chat_max_cost_usd,
            count_fake=settings.limits_count_fake,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ChatConfig:
        """From the environment, read as the API reads it (ValueError for a bad value)."""
        return cls.from_settings(Settings.from_env(env))


def _host(url: object) -> str:
    return urlsplit(str(url)).hostname or ""


def has_credentials(client: AsyncAnthropic) -> bool:
    """Whether the SDK resolved an API key, a token or a credentials profile."""
    return bool(client.api_key or client.auth_token or client.credentials is not None)


class Slots:
    """A count of chats being answered, capped at `total`. Never waits: a full house is answered
    503 busy. (Plain counting rather than a semaphore, so it isn't bound to one event loop.)"""

    def __init__(self, total: int) -> None:
        self.total = total
        self.used = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self.used >= self.total:
                return False
            self.used += 1
            return True

    def release(self) -> None:
        with self._lock:
            self.used = max(0, self.used - 1)


class ChatService:
    """One process's chat: the client, the frozen tool definitions and prompt version, the
    signing key and the limit on chats answered at once."""

    def __init__(
        self,
        client: AsyncAnthropic,
        config: ChatConfig,
        specs: Sequence[ToolSpec],
        paths: DataPaths,
        *,
        mode: Mode,
        owns_client: bool = True,
        scripted: ScriptedClaude | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.specs = tuple(specs)
        self.paths = paths
        self.mode = mode
        self.scripted = scripted
        self.owns_client = owns_client
        self.base_url_host = _host(client.base_url)
        on_claude_api = mode == "fake" or self.base_url_host == ANTHROPIC_HOST
        self.fallbacks = on_claude_api if config.fallbacks is None else config.fallbacks
        self.eager = on_claude_api if config.eager is None else config.eager
        self.tool_definitions = [tool_definition(s, self.eager) for s in self.specs]
        # A history from the scripted stand-in never validates against the real model.
        self.model_label = SCRIPTED_MODEL if mode == "fake" else config.model
        self.prompt_version = prompt_version(self.model_label, self.tool_definitions)
        self.secret = config.secret or secrets.token_bytes(32)
        self.slots = Slots(config.concurrency)

    def describe(self) -> dict[str, Any]:
        """For the health check: mode, model and the API host (never credentials)."""
        return {"mode": self.mode, "model": self.model_label, "base_url_host": self.base_url_host}

    @property
    def busy(self) -> bool:
        """Whether every chat slot is taken."""
        return self.slots.used >= self.slots.total

    def check(self, history: Sequence[Any], signature: str | None) -> int:
        """Raise ChatRejected if the conversation can't continue; questions asked so far."""
        return check_history(
            history,
            signature,
            secret=self.secret,
            version=self.prompt_version,
            max_turns=self.config.max_turns,
        )

    def answer(
        self, history: Sequence[Any], question: str, *, ledger: Admission | None = None
    ) -> AsyncGenerator[ServerSentEvent]:
        """The SSE events answering `question` after `history` (see runner.answer). `ledger` is
        the question's admission by the limits, on which the answer records its outcome and
        spend for the route to settle."""
        return runner.answer(self, list(history), question, ledger=ledger)

    async def aclose(self) -> None:
        if self.owns_client:
            await self.client.close()


def create_chat(
    config: ChatConfig,
    specs: Sequence[ToolSpec],
    paths: DataPaths,
    *,
    client: AsyncAnthropic | None = None,
) -> ChatService | None:
    """A chat for `config` outside the API app (tests, scripts), or None when it is off (asked
    to be, or `auto` without credentials). `client` replaces the client it would make (tests
    pass a scripted one); a client made here is closed by `aclose()`. Never called at import
    time: building a client reads the environment and credential profiles."""
    if config.mode == "off":
        log.info("chat: off (RACE_ENGINEER_CHAT=off)")
        return None
    if config.mode == "fake":
        scripted = None
        if client is None:
            root = paths.processed
            scripted = ScriptedClaude(catalog=lambda: list_sessions(root=root))
            client = scripted_client(scripted)
        log.info("chat: scripted stand-in for Claude (RACE_ENGINEER_CHAT=fake), no API calls")
        return ChatService(client, config, specs, paths, mode="fake", scripted=scripted)
    owns = client is None
    client = client or AsyncAnthropic()
    host = _host(client.base_url)
    if owns and not has_credentials(client):
        log.info("chat: off (no Anthropic credentials found)")
        return None
    service = ChatService(client, config, specs, paths, mode="anthropic", owns_client=owns)
    log.info(
        "chat: %s via %s (fallbacks %s, eager input streaming %s)",
        config.model,
        host,
        "on" if service.fallbacks else "off",
        "on" if service.eager else "off",
    )
    if host != ANTHROPIC_HOST:
        log.warning("chat: requests go to %s, not the Claude API (%s)", host, ANTHROPIC_HOST)
    return service


_building = threading.Lock()


def service_for(state: Any) -> ChatService | None:
    """The ChatService answering for an app, from its `state`, or None when the chat is off.

    `state.chat` is either the service itself (tests, scripts) or the API app's ChatState (its
    mode and client, see `api/app.py`). For the latter the service is built on that client the
    first time it is needed, from `state.settings`, `state.specs` and `state.paths`, and kept on
    `state.chat_service` until a restarted lifespan brings a new client. The app's lifespan owns
    and closes the client."""
    chat = getattr(state, "chat", None)
    if isinstance(chat, ChatService):
        return chat
    client = getattr(chat, "client", None)
    mode = getattr(chat, "mode", "off")
    if client is None or mode not in ("anthropic", "fake"):
        return None
    with _building:
        built = getattr(state, "chat_service", None)
        if isinstance(built, ChatService) and built.client is client:
            return built
        settings = getattr(state, "settings", None)
        config = ChatConfig.from_settings(settings) if settings else ChatConfig.from_env()
        specs = getattr(state, "specs", None)
        if specs is None:
            from race_engineer.tools.definitions import TOOLS

            specs = TOOLS
        paths = getattr(state, "paths", None) or DataPaths()
        built = ChatService(client, config, specs, paths, mode=mode, owns_client=False)
        state.chat_service = built
        log.info(
            "chat: answering with %s (fallbacks %s, eager input streaming %s)",
            built.model_label,
            "on" if built.fallbacks else "off",
            "on" if built.eager else "off",
        )
        return built
