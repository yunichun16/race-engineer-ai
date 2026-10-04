"""GET /api/tools and GET /api/tools/<name>: every registry tool as a REST endpoint.

One route per spec, generated: its query parameters are the spec's Params model (the same names,
descriptions and limits as over MCP and in the chat, and listed in /docs), and it answers with
a `ToolResponse`, the summary the model would read plus the chart data. Results come from
`registry.run_tool_async`, so they are cached while the data is unchanged and heavy tools wait
for a slot. Errors are mapped in api/errors.py.

Each response carries an ETag made from the data's version, the tool and its arguments, known
before the tool runs: a request with a matching If-None-Match gets 304 without running it.

No `from __future__ import annotations` here: FastAPI reads the endpoints' signatures, which
are built at runtime from each spec's model.
"""

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from race_engineer import __version__
from race_engineer.api.errors import TOOL_PATH
from race_engineer.api.models import ChartRef, ErrorBody, ToolInfo, ToolResponse
from race_engineer.tools.registry import DataPaths, ToolSpec, data_version, get_spec, run_tool_async

CACHE_CONTROL = "private, max-age=60"

_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorBody, "description": text}
    for status, text in (
        (404, "No such tool"),
        (422, "Invalid arguments, or a request the data can't answer (the message says why)"),
        (503, "The M4 results are missing or stale (the message says how to rebuild them)"),
    )
}


def tool_info(spec: ToolSpec) -> ToolInfo:
    return ToolInfo(
        name=spec.name,
        title=spec.title,
        description=spec.description,
        input_schema=spec.input_schema(),
        chart=ChartRef.of(spec),
    )


def etag(spec: ToolSpec, arguments: dict[str, Any], paths: DataPaths) -> str:
    """A strong ETag for the tool's answer: the same in every process while the data, the tool,
    its arguments and the package version are unchanged."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    key = repr((__version__, spec.name, canonical, data_version(paths)))
    return f'"{hashlib.sha1(key.encode()).hexdigest()}"'


def matches(if_none_match: str | None, tag: str) -> bool:
    """Whether an If-None-Match header names `tag` (weak tags compare equal, as RFC 9110 says
    for this header)."""
    if not if_none_match:
        return False
    candidates = [t.strip().removeprefix("W/") for t in if_none_match.split(",")]
    return "*" in candidates or tag in candidates


def tool_endpoint(spec: ToolSpec) -> Callable[..., Awaitable[Response | ToolResponse]]:
    """The endpoint for one spec. Its `params` parameter is declared as the spec's model taken
    from the query string (`Annotated[spec.params, Query()]`), set through `__signature__`
    because the model is only known at runtime."""

    async def endpoint(request: Request, response: Response, params: BaseModel) -> Any:
        paths: DataPaths = request.app.state.paths
        arguments = params.model_dump(mode="json")
        tag = etag(spec, arguments, paths)
        headers = {"ETag": tag, "Cache-Control": CACHE_CONTROL}
        if matches(request.headers.get("if-none-match"), tag):
            return Response(status_code=304, headers=headers)
        output = await run_tool_async(spec, arguments, paths)
        response.headers.update(headers)
        return ToolResponse(
            tool=spec.name, summary=output.summary, data=output.data, chart=ChartRef.of(spec)
        )

    endpoint.__name__ = endpoint.__qualname__ = spec.name
    keyword = inspect.Parameter.POSITIONAL_OR_KEYWORD
    endpoint.__signature__ = inspect.Signature(  # pyright: ignore[reportFunctionMemberAccess]
        [
            inspect.Parameter("request", keyword, annotation=Request),
            inspect.Parameter("response", keyword, annotation=Response),
            inspect.Parameter("params", keyword, annotation=Annotated[spec.params, Query()]),
        ],
        return_annotation=ToolResponse,
    )
    return endpoint


def build_router(specs: Sequence[ToolSpec]) -> APIRouter:
    """The catalog route, one route per spec (named after its tool, which errors.tool_of
    reads) and a catch-all that answers 404 unknown_tool for any other name."""
    router = APIRouter(tags=["tools"])
    catalog = [tool_info(spec) for spec in specs]

    @router.get("/api/tools", summary="List the tools")
    async def list_tools() -> list[ToolInfo]:
        """Every tool in the fixed order the chat sends them: name, title, the description the
        model reads, the input schema and the chart bundle, if any."""
        return catalog

    for spec in specs:
        router.add_api_route(
            f"{TOOL_PATH}{spec.name}",
            tool_endpoint(spec),
            methods=["GET"],
            name=spec.name,
            response_model=ToolResponse,
            summary=spec.title,
            description=spec.description,
            responses=_ERRORS,
        )

    @router.get(f"{TOOL_PATH}{{name}}", include_in_schema=False)
    async def unknown_tool(name: str) -> None:
        get_spec(name, specs)  # raises UnknownToolError, listing the tools
        raise AssertionError(f"{name} has its own route")

    return router
