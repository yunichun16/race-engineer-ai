"""The analysis tools as the chat model sees them, built from the shared registry's specs.

Each spec becomes an SDK function tool with the spec's name, description and input schema, so the
model reads the same text the MCP server and the REST catalog show. Calling it runs the tool
through `registry.run_tool_async`, which validates the arguments against the spec's `Params`
model. That check is required, not just tidy: with `eager_input_streaming` the API no longer
validates tool inputs, and the SDK's tolerant parser can hand over a cut-off object.

The model only gets the tool's summary. The chart data goes into a `ChartSink`, from which the
runner sends it to the browser in the `tool_result` event, so it never reaches a request body.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from anthropic import beta_async_tool
from anthropic.lib.tools import BetaAsyncFunctionTool, ToolError

from race_engineer.tools.registry import DataPaths, ToolOutput, ToolSpec, run_tool_async
from race_engineer.tools.sessions import ToolInputError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolRecord:
    """One tool call as it ran: the arguments the model sent, and the output or error text."""

    name: str
    input: dict[str, Any]
    output: ToolOutput | None = None
    error: str | None = None


@dataclass
class ChartSink:
    """Collects one question's tool calls, so the chart data can follow the model's `tool_use`
    blocks to the browser.

    The SDK calls a tool function without the `tool_use` id, so `take` pairs records with blocks
    by order, name and arguments (the runner calls the tools one at a time in block order; a call
    the SDK refuses before it reaches the function, such as one whose input isn't an object, has
    no record)."""

    records: list[ToolRecord] = field(default_factory=list)

    def record(self, record: ToolRecord) -> None:
        self.records.append(record)

    def take(self, blocks: Sequence[Any]) -> list[ToolRecord | None]:
        """The record for each `tool_use` block (None where the call never ran), removing the
        matched records."""
        found: list[ToolRecord | None] = []
        for block in blocks:
            match = next(
                (
                    i
                    for i, r in enumerate(self.records)
                    if r.name == block.name and r.input == block.input
                ),
                None,
            )
            found.append(None if match is None else self.records.pop(match))
        return found


def tool_definition(spec: ToolSpec, eager: bool) -> dict[str, Any]:
    """The tool as it appears in the request's `tools` list."""
    definition: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema(),
    }
    if eager:
        definition["eager_input_streaming"] = True
    return definition


def chat_tool(
    spec: ToolSpec, paths: DataPaths, sink: ChartSink, *, eager: bool
) -> BetaAsyncFunctionTool[Any]:
    """One spec as a function tool whose calls are recorded in `sink`."""

    async def call(**kwargs: Any) -> str:
        try:
            output = await run_tool_async(spec, kwargs, paths)
        except ToolInputError as exc:  # includes ResultsUnavailableError
            sink.record(ToolRecord(spec.name, kwargs, error=str(exc)))
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            log.exception("chat tool %s failed", spec.name)
            message = f"Internal error in {spec.name}; the server log has details."
            sink.record(ToolRecord(spec.name, kwargs, error=message))
            raise ToolError(message) from exc
        sink.record(ToolRecord(spec.name, kwargs, output=output))
        return output.summary

    call.__name__ = spec.name
    definition = tool_definition(spec, eager)
    return beta_async_tool(
        call,
        name=spec.name,
        description=spec.description,
        input_schema=definition["input_schema"],
        eager_input_streaming=True if eager else None,
    )


def build_chat_tools(
    specs: Sequence[ToolSpec], paths: DataPaths, sink: ChartSink, *, eager: bool = True
) -> list[BetaAsyncFunctionTool[Any]]:
    """The function tools for one question, in the specs' order (the order is part of the cached
    prompt prefix, so it never changes). Their definitions are the same on every request; only
    the sink they record into is the question's own."""
    return [chat_tool(spec, paths, sink, eager=eager) for spec in specs]
