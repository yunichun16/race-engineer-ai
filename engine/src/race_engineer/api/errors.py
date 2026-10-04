"""How the API answers errors: always an `ErrorBody`, {"error": {"code", "message", "tool"}}.

- RequestValidationError (a bad query or body): 422 `invalid_request`.
- ToolInputError (the tool can't answer: an unknown event, driver or lap): 422 `invalid_input`.
- ResultsUnavailableError or StaleResultsError (the M4 results need rebuilding): 503
  `results_unavailable`.
- UnknownToolError: 404 `unknown_tool`.
- HTTPException (no such path, the wrong method): its status, `not_found`,
  `method_not_allowed` and so on.
- Anything else: 500 `internal_error` (from `InternalErrors`, so the CORS headers are on it).

A tool's message is relayed as the tool wrote it, since it says what to change (another year,
the drivers in the session) or how to rebuild the results, with one change: the data folders'
absolute paths are shortened to `data/processed` and `data/results`, so a deployed server
doesn't publish its file system. An internal error says only "see the server log", with a request
id that the logged traceback carries too.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from race_engineer.api.models import ErrorBody, ErrorDetail
from race_engineer.inference.provenance import StaleResultsError
from race_engineer.tools.registry import DataPaths, UnknownToolError, describe_errors
from race_engineer.tools.sessions import ResultsUnavailableError, ToolInputError

log = logging.getLogger(__name__)

TOOL_PATH = "/api/tools/"  # GET /api/tools/<name>, one route per tool

_HTTP_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "too_large",
    503: "unavailable",
}


def error_response(
    status: int,
    code: str,
    message: str,
    tool: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """An ErrorBody response."""
    body = ErrorBody(error=ErrorDetail(code=code, message=message, tool=tool))
    return JSONResponse(body.model_dump(mode="json"), status_code=status, headers=headers)


def tool_of(request: Request) -> str | None:
    """The tool a request was for, when it was for one: each tool's route is named after it
    (routes/tools.py), and the catch-all route for unknown names has it in the path."""
    route = request.scope.get("route")
    if not str(getattr(route, "path", "")).startswith(TOOL_PATH):
        return None
    return request.path_params.get("name") or getattr(route, "name", None)


def short_paths(message: str, request: Request) -> str:
    """`message` with the data folders' absolute paths replaced by data/processed and
    data/results."""
    paths: DataPaths | None = getattr(request.app.state, "paths", None)
    if paths is None:
        return message
    for folder, short in ((paths.results, "data/results"), (paths.processed, "data/processed")):
        for spelling in {str(folder), str(Path(folder).resolve())}:
            message = message.replace(spelling, short)
    return message


def _where(error: Mapping[str, Any]) -> tuple[str, ...]:
    """The error's location without FastAPI's leading "query", "body" or "path", which says
    where the value came from. Only the first element: a parameter may itself be called
    "query" (find_session's old free-text argument)."""
    loc = tuple(str(p) for p in error.get("loc", ()))
    return loc[1:] if loc and loc[0] in ("query", "body", "path") else loc


def flatten(exc: RequestValidationError, params: type[BaseModel] | None = None) -> str:
    """FastAPI's validation details as one line. A tool's query arguments (`params`, its
    Params model) are worded as the MCP server and the chat word them: "Invalid request: lap:
    must be at least 1; unknown parameter(s) zzz (parameters: ...)". Anything else (the chat's
    body) keeps pydantic's words, with the field's path: "history.0.role: Field required"."""
    errors = [error | {"loc": _where(error)} for error in exc.errors()]
    if params is not None and all(len(error["loc"]) == 1 for error in errors):
        parts = [describe_errors(errors, params)] if errors else []
    else:
        parts = []
        for error in errors:
            message = str(error.get("msg", "invalid"))
            parts.append(f"{'.'.join(error['loc'])}: {message}" if error["loc"] else message)
    return f"Invalid request: {'; '.join(parts) or 'see the API docs at /docs'}."


async def _invalid_request(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    tool = tool_of(request)
    specs = getattr(request.app.state, "specs", ())
    params = next((s.params for s in specs if s.name == tool), None)
    return error_response(422, "invalid_request", flatten(exc, params), tool)


async def _results_unavailable(request: Request, exc: Exception) -> JSONResponse:
    message = short_paths(str(exc), request)
    return error_response(503, "results_unavailable", message, tool_of(request))


async def _invalid_input(request: Request, exc: Exception) -> JSONResponse:
    message = short_paths(str(exc), request)
    return error_response(422, "invalid_input", message, tool_of(request))


async def _unknown_tool(request: Request, exc: Exception) -> JSONResponse:
    return error_response(404, "unknown_tool", str(exc), tool_of(request))


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    headers = dict(exc.headers) if exc.headers else None
    return error_response(exc.status_code, code, str(exc.detail), headers=headers)


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    request_id = uuid.uuid4().hex[:12]
    log.error(
        "internal error (request %s) on %s %s",
        request_id,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    message = f"Internal error; see the server log (request {request_id})."
    return error_response(500, "internal_error", message, tool_of(request))


class InternalErrors:
    """ASGI middleware that answers an exception no handler took with the 500 ErrorBody.

    Starlette sends its own 500 from the outermost middleware, outside CORSMiddleware, so a
    browser on the Next.js dev server would see a CORS failure instead of the message and its
    request id. Added inside CORSMiddleware, this answers first, with the CORS headers. An
    exception after the response has started (in a stream) can't be answered and is re-raised.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def sending(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, sending)
        except Exception as exc:
            if started:
                raise
            response = await _internal_error(Request(scope), exc)
            await response(scope, receive, send)


def add_exception_handlers(app: FastAPI) -> None:
    """Register the handlers above. Starlette picks the most specific class an exception is an
    instance of, so ResultsUnavailableError gets 503 although it is also a ToolInputError."""
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(ResultsUnavailableError, _results_unavailable)
    app.add_exception_handler(StaleResultsError, _results_unavailable)
    app.add_exception_handler(ToolInputError, _invalid_input)
    app.add_exception_handler(UnknownToolError, _unknown_tool)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(Exception, _internal_error)
