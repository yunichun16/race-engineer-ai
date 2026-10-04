"""The FastAPI app: REST endpoints for every tool, the health check, the chat and the MCP server
at /mcp, all built from the same tool specs (`tools.definitions.TOOLS`).

    uv run race-engineer-api            # http://127.0.0.1:8000, docs at /docs
    uvicorn race_engineer.api.app:create_app --factory

Startup (the lifespan) warms the caches the first requests would otherwise wait for (the session
catalog, the M4 results check, the style tables), starts the chat's Anthropic client (or the
scripted one) and runs the MCP server's session manager. Missing data doesn't stop startup: the
health check reports it as `degraded`, and the tools that need it answer with an error saying how
to rebuild it. Tool results are cached by `registry.run_tool`, keyed on the data's version, so
`make data` or `make m4` while the API runs takes effect on the next request.

What the routes find on `app.state`: `settings`, `paths`, `specs`, `server` (the MCPServer),
`chat` (a ChatState: the mode and the client; the client is None when the chat is off), `guard`
(the chat's limits, `api/limits/guard.ChatGuard`), `warm_s` (seconds per warm-up step),
`problems` (startup problems outside the data, e.g. a chat client that failed to start),
`ready` (the warm-up finished), and `mcp_mounted`, `mcp_path`, `mcp_tools` for the health check.
All but `chat`, `guard`, `warm_s`, `problems`, `ready` and `mcp_tools` are set when the app is
created; those have "off", None, empty, false and zero values until the lifespan runs (the guard
is built once the chat's mode is known: RACE_ENGINEER_LIMITS=auto depends on it).

Middleware, outermost first: CORS (so every refusal below carries its headers; Retry-After is
exposed), GZip (JSON of 1 KB or more; Starlette never compresses the chat's event stream),
RequestRate and BodyLimit (`api/limits/http.py`), then InternalErrors (a 500 with the CORS
headers). With RACE_ENGINEER_MCP_STATELESS=1, /mcp keeps no sessions and answers with JSON, so a
restarted or second container breaks nothing and no long-lived stream meets a host's request
time limit.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from race_engineer import __version__
from race_engineer.api.errors import InternalErrors, add_exception_handlers, error_response
from race_engineer.api.limits.guard import build_guard
from race_engineer.api.limits.http import BodyLimit, Rates, RequestRate
from race_engineer.api.limits.store import LimitStore
from race_engineer.api.routes import catalog, chat, health, saved, tools
from race_engineer.api.settings import ANTHROPIC_API_HOST, Settings
from race_engineer.mcp_server.server import create_server
from race_engineer.tools.definitions import TOOLS
from race_engineer.tools.registry import (
    ToolBusyError,
    ToolSpec,
    set_heavy_limit,
    set_heavy_queue,
    warm,
)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

log = logging.getLogger(__name__)
# The HTTP client under the Upstash store logs every request URL at INFO; the limits store's
# URL must never reach the log (plan 13.1), on Modal, the Dockerfile path or plain uvicorn.
logging.getLogger("httpx2").setLevel(logging.WARNING)

MCP_PATH = "/mcp"
CHAT_PATH = "/api/chat"
SCRIPTED_MODEL = "scripted"  # what the health check reports for the fake chat
GZIP_MIN_BYTES = 1024

ChatMode = Literal["anthropic", "fake", "off"]


@dataclass(frozen=True)
class ChatState:
    """The chat as the lifespan started it. `client` is an AsyncAnthropic (real, or the
    scripted stand-in in fake mode) and None when the chat is off; `model` and `base_url_host`
    are what the health check reports."""

    mode: ChatMode = "off"
    client: AsyncAnthropic | None = None
    model: str = ""
    base_url_host: str = ANTHROPIC_API_HOST


class McpEndpoint:
    """The ASGI endpoint at /mcp: hands each request to the MCP server's streamable-HTTP app
    while the lifespan runs its session manager, and answers 503 otherwise.

    A session manager can run only once, so each start of the lifespan builds a new one
    (`MCPServer.streamable_http_app`) and points this endpoint at it."""

    def __init__(self) -> None:
        self.app: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.app is None:
            message = "The MCP server isn't running (the app hasn't started)."
            await error_response(503, "unavailable", message)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _has_credentials(client: AsyncAnthropic) -> bool:
    """Whether the SDK resolved any credentials (a key, a token or a profile); never which."""
    return any(x is not None for x in (client.api_key, client.auth_token, client.credentials))


def _build_client(settings: Settings) -> tuple[AsyncAnthropic, ChatMode]:
    """The scripted stand-in for Claude in fake mode (it reads event names from the session
    catalog), else the SDK's client with whatever credentials it resolves (maybe none: see
    _has_credentials)."""
    if settings.chat == "fake":
        from race_engineer.api.chat.fake import scripted_client

        return scripted_client(paths=settings.paths), "fake"
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(), "anthropic"


async def _start_chat(
    settings: Settings, client: AsyncAnthropic | None, stack: AsyncExitStack
) -> tuple[ChatState, list[str]]:
    """The chat's state and any problem starting it. A client the caller passed in (tests) is
    used as it is and left for the caller to close; one built here is closed at shutdown."""
    off = ChatState(model=settings.chat_model, base_url_host=settings.base_url_host)
    if client is not None:
        mode: ChatMode = "fake" if settings.chat == "fake" else "anthropic"
    elif settings.chat == "off":
        log.info("chat: off (RACE_ENGINEER_CHAT=off)")
        return off, []
    else:
        try:
            client, mode = _build_client(settings)
        except Exception as exc:  # reported by the health check; the rest of the API still runs
            log.exception("chat: the client failed to start")
            return off, [f"chat failed to start ({type(exc).__name__}); see the server log"]
        stack.push_async_callback(client.close)
        if mode == "anthropic" and not _has_credentials(client):
            log.info(
                "chat: off (no Anthropic credentials: set ANTHROPIC_API_KEY or run "
                "`ant auth login`, or RACE_ENGINEER_CHAT=fake for the scripted chat)"
            )
            return ChatState(model=settings.chat_model, base_url_host=client.base_url.host), []

    host = client.base_url.host
    model = SCRIPTED_MODEL if mode == "fake" else settings.chat_model
    log.info("chat: %s, model %s, via %s", mode, model, host)
    if mode == "anthropic" and host != ANTHROPIC_API_HOST:
        log.warning(
            "chat: requests go to %s, not the Claude API (%s): it sees every question and the "
            "credentials. Fallbacks %s, eager input streaming %s.",
            host,
            ANTHROPIC_API_HOST,
            "on" if settings.chat_fallbacks else "off",
            "on" if settings.chat_eager else "off",
        )
    return ChatState(mode=mode, client=client, model=model, base_url_host=host), []


async def _busy(request: Request, exc: Exception) -> JSONResponse:
    """A heavy tool refused because too many are waiting (registry.ToolBusyError): 503 busy."""
    tool = getattr(request.scope.get("route"), "name", None)
    return error_response(503, "busy", str(exc), tool, headers={"Retry-After": "30"})


def create_app(
    settings: Settings | None = None,
    *,
    anthropic_client: AsyncAnthropic | None = None,
    specs: Sequence[ToolSpec] | None = None,
    limit_store: LimitStore | None = None,
) -> FastAPI:
    """The app. `settings` default to the environment's (`Settings.from_env`); tests pass
    their own, with temporary data folders. `anthropic_client` replaces the client the lifespan
    would build (the tests' scripted Claude). `specs` defaults to every tool. `limit_store`
    replaces the limits' store the settings would choose (tests; the guard keeps its clock)."""
    settings = settings or Settings.from_env()
    specs = tuple(TOOLS if specs is None else specs)
    paths = settings.paths
    set_heavy_limit(settings.tool_concurrency)
    set_heavy_queue(settings.heavy_queue)
    server = create_server(paths.processed, paths.results, specs)
    mcp = McpEndpoint()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        started = time.perf_counter()
        state = app.state
        state.warm_s = await anyio.to_thread.run_sync(warm, paths, specs)
        status = await anyio.to_thread.run_sync(health.data_status, paths)
        for problem in status.problems:
            log.warning("data: %s", problem)
        async with AsyncExitStack() as stack:
            state.chat, state.problems = await _start_chat(settings, anthropic_client, stack)
            state.guard = build_guard(settings, state.chat.mode, store=limit_store)
            stack.push_async_callback(state.guard.aclose)
            stack.callback(setattr, state, "guard", None)
            if settings.mount_mcp:
                security = (
                    None  # the SDK's default: localhost Host headers only on a localhost host
                    if settings.mcp_host_check
                    else TransportSecuritySettings(enable_dns_rebinding_protection=False)
                )
                mcp.app = server.streamable_http_app(
                    streamable_http_path=MCP_PATH,
                    host=settings.host,
                    transport_security=security,
                    json_response=settings.mcp_stateless,
                    stateless_http=settings.mcp_stateless,
                    max_request_body_size=settings.mcp_max_body,
                )
                await stack.enter_async_context(server.session_manager.run())
                stack.callback(setattr, mcp, "app", None)
            state.mcp_tools = len(await server.list_tools())
            log.info(
                "ready in %.1f s: %d sessions, warm-up %s, MCP %s",
                time.perf_counter() - started,
                status.health.sessions,
                ", ".join(f"{step} {s:.2f} s" for step, s in state.warm_s.items()),
                (
                    f"at {MCP_PATH} ({state.mcp_tools} tools"
                    f"{', stateless' if settings.mcp_stateless else ''})"
                    if settings.mount_mcp
                    else "not mounted"
                ),
            )
            state.ready = True
            stack.callback(setattr, state, "ready", False)
            yield

    app = FastAPI(
        title="Race Engineer AI",
        version=__version__,
        summary="F1 telemetry analysis tools, a chat that uses them, and the MCP server.",
        lifespan=lifespan,
    )
    state: Any = app.state
    state.settings, state.paths, state.specs, state.server = settings, paths, specs, server
    state.chat = ChatState(model=settings.chat_model, base_url_host=settings.base_url_host)
    state.guard, state.ready = None, False
    state.warm_s, state.problems = {}, []
    state.mcp_mounted, state.mcp_path, state.mcp_tools = settings.mount_mcp, MCP_PATH, 0

    add_exception_handlers(app)
    app.add_exception_handler(ToolBusyError, _busy)
    # Added first runs innermost. InternalErrors inside CORSMiddleware: a 500 keeps the CORS
    # headers, and so does every refusal from the middleware in between.
    app.add_middleware(InternalErrors)
    app.add_middleware(BodyLimit, limits={("POST", CHAT_PATH): settings.chat_max_body})
    app.add_middleware(
        RequestRate,
        rates=Rates(
            tools=settings.tools_rpm,
            tools_global=settings.tools_global_rpm,
            mcp_global=settings.mcp_rpm,
            chat=settings.chat_rpm,
            chat_global=settings.chat_global_rpm,
            status=settings.status_rpm,
        ),
    )
    if settings.gzip:
        app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_BYTES)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
        allow_credentials=False,
        expose_headers=["Retry-After"],
    )
    app.include_router(health.router)
    app.include_router(tools.build_router(specs))
    app.include_router(catalog.router)  # the site's pickers; not tools, so not in specs
    app.include_router(chat.router)
    app.include_router(saved.router)  # the chat's saved example answers
    if settings.mount_mcp:
        # A route at exactly /mcp, added after every API route. Mounting the MCP app under
        # /mcp instead would answer at /mcp/ behind a redirect that clients may not follow
        # for a POST.
        app.router.routes.append(Route(MCP_PATH, mcp, include_in_schema=False))
    return app
