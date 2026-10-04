"""GET /api/health: what the server can answer right now. Always 200; `status` is `degraded`
when the data can't serve every tool (no processed sessions, M4 results missing or stale, style
tables not built) or the chat failed to start, with the reasons in `problems`.

The data checks run on every request, a few milliseconds of `stat` calls and parquet footers, so
the answer follows a `make data` or `make m4` while the server runs.

The chat part says whether a question would be admitted now (`available`, and `reason`: off,
paused, daily_cap or unavailable), and `limits` whether the limits are on, their store, the
per-visitor limits and their own problems. Limit problems stay out of the top-level `problems`,
which are about the data (the site's "degraded" banner). No dollar amounts; the limits' global
counters are read at most once a minute (`guard.HEALTH_CACHE_S`).

GET /api/ready: 200 `{"ready": true}` once the startup warm-up has finished and the data has no
problems, else 503, for container hosts' startup probes (Cloud Run, the Dockerfile).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import anyio.to_thread
import pyarrow.parquet as pq
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from race_engineer import __version__
from race_engineer.api.limits.guard import chat_mode, guard_for
from race_engineer.api.models import (
    ChatHealth,
    DataHealth,
    HealthResponse,
    LimitsHealth,
    McpHealth,
    ReadyResponse,
)
from race_engineer.inference import provenance
from race_engineer.tools.registry import (
    CIRCUITS_FILE,
    MISTAKES_FILE,
    STYLE_FILE,
    DataPaths,
)
from race_engineer.tools.sessions import SessionInfo, ToolInputError, list_sessions

router = APIRouter(tags=["health"])


@dataclass(frozen=True)
class DataStatus:
    health: DataHealth
    problems: list[str]


def _short(message: str, paths: DataPaths) -> str:
    """A provenance message without the results folder's absolute path."""
    return message.replace(str(paths.results), "the results folder")


def _current(path: Path, step: str, paths: DataPaths) -> str | None:
    """None when `path` exists and comes from the current scoring run, else why not."""
    try:
        provenance.check_current(path, step, paths.results)
    except provenance.StaleResultsError as exc:
        return _short(str(exc), paths)
    except (OSError, ValueError) as exc:  # an unreadable manifest or parquet footer
        return f"{path.name} can't be read ({type(exc).__name__})"
    return None


def data_status(paths: DataPaths) -> DataStatus:
    """The data's state and what's wrong with it, worded for the health check."""
    problems: list[str] = []
    sessions: list[SessionInfo] = []
    try:
        sessions = list_sessions(root=paths.processed)
    except ToolInputError:
        problems.append("no processed sessions (make data)")
    except Exception as exc:  # e.g. a session file half-written by `make data`: still answer
        problems.append(f"processed sessions can't be read ({type(exc).__name__}; make data)")

    try:
        run = provenance.scoring_run(paths.results)
    except (OSError, ValueError):
        run = None
    mistakes_path = paths.results / MISTAKES_FILE
    stale = _current(mistakes_path, "explain", paths)
    if stale is not None:
        problems.append(f"results stale: {stale}")
    try:
        mistakes = pq.read_metadata(mistakes_path).num_rows if mistakes_path.exists() else None
    except (OSError, ValueError):
        mistakes = None

    style_path = paths.results / STYLE_FILE
    if not style_path.exists():
        problems.append("style tables not built (race-engineer-infer style)")
        style = False
    else:
        style_stale = _current(style_path, "style", paths)
        if style_stale is not None:
            problems.append(f"style tables stale: {style_stale}")
        style = style_stale is None

    health = DataHealth(
        sessions=len(sessions),
        latest=sessions[-1].label if sessions else None,
        results_run=run,
        results_current=stale is None,
        mistakes=mistakes,
        style=style,
        circuits=(paths.processed / CIRCUITS_FILE).is_file(),
    )
    return DataStatus(health, problems)


@router.get("/api/health", summary="Server health")
async def health(request: Request) -> HealthResponse:
    """The data's state (sessions, the M4 results, the style tables, the circuit rotations),
    the chat's mode, the MCP mount, the startup warm-up times and anything that needs fixing."""
    state = request.app.state
    status = await anyio.to_thread.run_sync(data_status, state.paths)
    problems = [*status.problems, *state.problems]
    chat = state.chat
    guard = guard_for(state)
    if guard is not None:
        reason, limits = await guard.health(chat_mode(state))
    else:
        reason = "off" if chat_mode(state) == "off" else None
        settings = state.settings
        limits = {
            "enabled": False,
            "store": None,
            "per_hour": settings.rate_hour,
            "per_day": settings.rate_day,
            "problems": [],
        }
    return HealthResponse(
        status="degraded" if problems else "ok",
        version=__version__,
        data=status.health,
        chat=ChatHealth(
            mode=chat.mode,
            model=chat.model,
            base_url_host=chat.base_url_host,
            available=reason is None,
            reason=reason,  # pyright: ignore[reportArgumentType]  # one of ChatReason's values
        ),
        mcp=McpHealth(mounted=state.mcp_mounted, path=state.mcp_path, tools=state.mcp_tools),
        limits=LimitsHealth.model_validate(limits),
        warm_s=state.warm_s,
        problems=problems,
    )


@router.get(
    "/api/ready",
    summary="Ready to serve",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "Starting, or the data has problems"}},
)
async def ready(request: Request) -> JSONResponse:
    """200 once the startup warm-up has finished and the data has no problems, else 503 with
    the data's problems: a container host's startup probe."""
    state = request.app.state
    if not getattr(state, "ready", False):
        body = ReadyResponse(ready=False, problems=["starting"])
        return JSONResponse(body.model_dump(), status_code=503)
    status = await anyio.to_thread.run_sync(data_status, state.paths)
    body = ReadyResponse(ready=not status.problems, problems=status.problems)
    return JSONResponse(body.model_dump(), status_code=503 if status.problems else 200)
