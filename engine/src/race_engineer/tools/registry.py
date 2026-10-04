"""The tool registry: one definition per tool, served by the MCP server, the REST API and the chat.

Each tool is a `ToolSpec`: name, title and description, a pydantic `Params` model for its inputs
(`tools.params`), a `run(params, paths)` function returning a `ToolOutput` and, for tools with
one, the chart bundle that draws the result. The front ends all build from the same specs
(`tools.definitions.TOOLS`), so descriptions, schemas, validation and results are identical
everywhere. A `ToolOutput` has two parts: `summary`, the text the model reads, and `data`, the
chart payload. The model only ever sees the summary; the data goes to MCP `structuredContent`,
the REST response and the browser.

`run_tool` validates the arguments, runs the tool and caches the result, keyed on the
arguments and on the data's version (`data_version`), so `make data` or `make m4` while a server
runs takes effect on the next call. `run_tool_async` runs it in a worker thread under a capacity
limiter: a heavy tool (explain_corner peaks at 1.2-1.4 GB) gets one of HEAVY's few slots, and
when HEAVY_QUEUE heavy calls are already waiting for one the next is refused (`ToolBusyError`)
rather than queued without end.

Errors: a `ToolInputError` (bad arguments, an unknown event or driver) carries a message to
relay to the user. Its subclass `ResultsUnavailableError` means the M4 results are missing or
stale; the REST API answers it with 503 instead of 422, and `ToolBusyError` with 503 busy.
`short_paths` takes the data folders' absolute paths out of a message, for every front end that
relays one to a remote client.
"""

from __future__ import annotations

import functools
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from pydantic import BaseModel, ValidationError

from race_engineer.config import PROCESSED_DIR, RESULTS_DIR
from race_engineer.inference import provenance
from race_engineer.tools.cache import listing_signature
from race_engineer.tools.sessions import ResultsUnavailableError, ToolInputError, list_sessions

log = logging.getLogger(__name__)

SUMMARY_MAX = 16_000  # characters of summary the model gets; the largest real one is ~6K
# Budget for a result's chart data, as compact JSON: 30 KiB. Every tool keeps to it except
# compare_laps, whose two full-lap traces (37-63 KB on real laps) predate the budget.
PAYLOAD_MAX_BYTES = 30 * 1024
RESULT_CACHE_SIZE = 128  # tool results kept; the largest payload is ~63 KB
HEAVY_LIMIT = 2  # heavy tools running at once (explain_corner: 1.2-1.4 GB each)
LIGHT_LIMIT = 8  # other tools running at once
HEAVY_QUEUE = 8  # heavy calls that may wait for a slot; the next one is refused as busy

# Files whose change means cached results may be out of date (see data_version).
MISTAKES_FILE = "mistakes.parquet"  # tools.mistakes.MISTAKES_FILE, written by `infer explain`
STYLE_FILE = "style_corners.parquet"  # written by `infer style`
CIRCUITS_FILE = "circuits.json"  # track map rotations, in the processed folder

UI_PREFIX = "ui://race-engineer/"


def ui_uri(name: str) -> str:
    """The MCP resource URI of the chart bundle `name` (web/src/mcp-app/<name>.html)."""
    return f"{UI_PREFIX}{name}.html"


class UnknownToolError(LookupError):
    """A tool name no spec has; the message lists the tools."""


class ToolBusyError(ToolInputError):
    """Too many heavy tool calls are waiting for a slot: try again shortly. A ToolInputError, so
    MCP and the chat relay its message as the tool's error; the REST API answers 503 busy."""


@dataclass(frozen=True)
class DataPaths:
    """Where the tools read: the processed dataset and the M4 results (both follow
    RACE_ENGINEER_DATA; tests pass temporary folders)."""

    processed: Path = PROCESSED_DIR
    results: Path = RESULTS_DIR


@dataclass(frozen=True)
class ToolOutput:
    """A tool's answer: `summary` for the model, `data` for the chart (or the REST client).
    Results are cached and shared, so treat `data` as read-only."""

    summary: str
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolSpec:
    """One tool, as every front end serves it.

    `run` receives the validated `params` instance and the data paths, and raises
    ToolInputError (or ResultsUnavailableError) with a message for the user when the request
    can't be answered. `heavy` tools run under the HEAVY limiter. `warm`, if given, loads what
    the tool's first call would otherwise wait for (called once at API startup)."""

    name: str
    title: str
    description: str
    params: type[BaseModel]
    run: Callable[[Any, DataPaths], ToolOutput] = field(repr=False)
    chart: str | None = None  # chart bundle: web/src/mcp-app/<chart>.html
    heavy: bool = False
    warm: Callable[[DataPaths], object] | None = field(default=None, repr=False)

    @property
    def resource_uri(self) -> str | None:
        """The chart's `ui://` resource, or None for a text-only tool."""
        return ui_uri(self.chart) if self.chart else None

    def input_schema(self) -> dict[str, Any]:
        """The JSON schema of the inputs, without pydantic's generated titles (the chat's tool
        definition and the REST catalog; the MCP server lists the same schema)."""
        return without_titles(self.params.model_json_schema())


def without_titles(schema: Any) -> Any:
    """A JSON schema without its "title" keywords (keeping any property named "title")."""
    if isinstance(schema, list):
        return [without_titles(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "title" and isinstance(value, str):
            continue
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            out[key] = {name: without_titles(s) for name, s in value.items()}
        else:
            out[key] = without_titles(value)
    return out


def get_spec(name: str, specs: Sequence[ToolSpec] | None = None) -> ToolSpec:
    """The spec called `name` among `specs` (default: every tool, tools.definitions.TOOLS)."""
    if specs is None:
        from race_engineer.tools.definitions import TOOLS  # imports this module

        specs = TOOLS
    for spec in specs:
        if spec.name == name:
            return spec
    names = ", ".join(s.name for s in specs)
    raise UnknownToolError(f"No tool {name!r}. Tools: {names}.")


# Validation messages, worded for the model and the user rather than for a developer.
_MESSAGES = {
    "missing": "required",
    "greater_than_equal": "must be at least {ge}",
    "less_than_equal": "must be at most {le}",
    "greater_than": "must be more than {gt}",
    "less_than": "must be less than {lt}",
    "string_too_short": "must have at least {min_length} character(s)",
    "string_too_long": "must have at most {max_length} characters",
    "int_type": "must be a whole number",
    "int_parsing": "must be a whole number",
    "int_from_float": "must be a whole number",
    "string_type": "must be text",
    "bool_type": "must be true or false",
    "bool_parsing": "must be true or false",
}
_TYPE_ERRORS = {"int_type", "int_parsing", "int_from_float", "string_type"}


def validation_message(name: str, params: type[BaseModel], exc: ValidationError) -> str:
    """One readable line for pydantic's errors, e.g. "Invalid arguments for explain_corner:
    lap: must be at least 1; corner: required." (see describe_errors)."""
    return f"Invalid arguments for {name}: {describe_errors(exc.errors(), params)}."


def describe_errors(
    errors: Iterable[Mapping[str, Any]], params: type[BaseModel] | None = None
) -> str:
    """pydantic's errors as one readable phrase per field: "lap: must be at least 1; corner:
    required", and unknown arguments as "unknown parameter(s) zzz (parameters: ...)", listing
    the fields of `params` when given. For a field that takes one of several types (corner: a
    number or text), a type mismatch is left out when another branch says more. The REST API
    words its 422s with it too (api/errors.py), so every front end says the same."""
    by_field: dict[str, list[tuple[str, str]]] = {}
    unknown: list[str] = []
    for error in errors:
        loc = error["loc"]
        where = str(loc[0]) if loc else "arguments"
        if error["type"] == "extra_forbidden":
            unknown.append(where)
            continue
        template = _MESSAGES.get(error["type"])
        if template is not None:
            text = template.format(**error.get("ctx", {}))
        else:
            text = error["msg"][:1].lower() + error["msg"][1:]
        by_field.setdefault(where, []).append((error["type"], text))
    parts = []
    for where, found in by_field.items():
        telling = [text for kind, text in found if kind not in _TYPE_ERRORS]
        texts = list(dict.fromkeys(telling or [text for _, text in found]))
        parts.append(f"{where}: {' or '.join(texts)}")
    if unknown:
        known = f" (parameters: {', '.join(params.model_fields)})" if params else ""
        parts.append(f"unknown parameter(s) {', '.join(unknown)}{known}")
    return "; ".join(parts)


def validate(spec: ToolSpec, args: Mapping[str, Any]) -> BaseModel:
    """The spec's Params from raw arguments; ToolInputError with a readable line if invalid."""
    try:
        return spec.params.model_validate(dict(args))
    except ValidationError as exc:
        raise ToolInputError(validation_message(spec.name, spec.params, exc)) from None


def capped(summary: str, limit: int = SUMMARY_MAX) -> str:
    """The summary, cut to `limit` characters with a note saying so."""
    if len(summary) <= limit:
        return summary
    note = f"\n[Cut at {limit:,} characters; the chart data has the full result.]"
    return summary[: limit - len(note)].rstrip() + note


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def data_version(paths: DataPaths) -> tuple[Hashable, ...]:
    """What changes when the data under `paths` is rebuilt: the scoring run, the M4 mistakes and
    style tables, the circuit rotations and the processed sessions (`make data` adds a session's
    file or renames a new one over it, see cache.listing_signature). A few stat calls and one
    directory read, about 0.5 ms locally."""
    try:
        run = provenance.scoring_run(paths.results)
    except (OSError, ValueError):
        run = "unreadable"
    return (
        run,
        _mtime(paths.results / MISTAKES_FILE),
        _mtime(paths.results / STYLE_FILE),
        _mtime(paths.processed / CIRCUITS_FILE),
        listing_signature(paths.processed / "sessions"),
    )


class _ResultCache:
    """A thread-safe LRU of tool outputs."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self._items: OrderedDict[Hashable, ToolOutput] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: Hashable) -> ToolOutput | None:
        with self._lock:
            found = self._items.get(key)
            if found is None:
                self.misses += 1
            else:
                self.hits += 1
                self._items.move_to_end(key)
            return found

    def put(self, key: Hashable, value: ToolOutput) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.maxsize:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.hits = self.misses = 0

    def __len__(self) -> int:
        return len(self._items)


RESULTS = _ResultCache(RESULT_CACHE_SIZE)


def run_tool(
    spec: ToolSpec | str, args: Mapping[str, Any], paths: DataPaths | None = None
) -> ToolOutput:
    """Validate `args`, run the tool and return its output, cached while the data is unchanged.

    Raises ToolInputError for invalid arguments or a request the data can't answer,
    ResultsUnavailableError when the M4 results are missing or stale, UnknownToolError for an
    unknown name. Anything else is a bug, left for the front end to log.
    """
    spec = get_spec(spec) if isinstance(spec, str) else spec
    paths = paths or DataPaths()
    params = validate(spec, args)
    canonical = json.dumps(params.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    # The spec itself, not its name: a spec replaced under the same name never gets old results.
    key = (spec, paths, canonical, data_version(paths))
    cached = RESULTS.get(key)
    if cached is not None:
        return cached
    try:
        output = spec.run(params, paths)
    except provenance.StaleResultsError as exc:
        raise ResultsUnavailableError(str(exc)) from exc
    output = ToolOutput(capped(output.summary), output.data)
    RESULTS.put(key, output)
    return output


# Created outside an event loop, anyio binds them to the first one that uses them.
HEAVY = anyio.CapacityLimiter(HEAVY_LIMIT)
LIGHT = anyio.CapacityLimiter(LIGHT_LIMIT)


BUSY_MESSAGE = "The server is busy with other detailed analyses right now; try again in a minute."
_heavy_queue = HEAVY_QUEUE


def set_heavy_limit(total: int) -> None:
    """How many heavy tools may run at once (RACE_ENGINEER_TOOL_CONCURRENCY in the API)."""
    HEAVY.total_tokens = max(1, int(total))


def set_heavy_queue(waiting: int) -> None:
    """How many heavy calls may wait for a slot before the next is refused
    (RACE_ENGINEER_HEAVY_QUEUE in the API)."""
    global _heavy_queue
    _heavy_queue = max(0, int(waiting))


async def run_tool_async(
    spec: ToolSpec | str, args: Mapping[str, Any], paths: DataPaths | None = None
) -> ToolOutput:
    """run_tool in a worker thread, under the HEAVY or LIGHT limiter, for async front ends.
    ToolBusyError when the heavy queue is full."""
    spec = get_spec(spec) if isinstance(spec, str) else spec
    if spec.heavy and HEAVY.available_tokens < 1:
        waiting = HEAVY.statistics().tasks_waiting
        if waiting >= _heavy_queue:
            log.warning("tool %s refused: %d heavy calls already waiting", spec.name, waiting)
            raise ToolBusyError(BUSY_MESSAGE)
    call = functools.partial(run_tool, spec, dict(args), paths)
    return await anyio.to_thread.run_sync(call, limiter=HEAVY if spec.heavy else LIGHT)


def short_paths(message: str, paths: DataPaths) -> str:
    """`message` with the data folders' absolute paths replaced by data/processed and
    data/results, so a deployed server doesn't publish its file system (the REST API's errors,
    MCP's error results)."""
    for folder, short in ((paths.results, "data/results"), (paths.processed, "data/processed")):
        for spelling in sorted({str(folder), str(Path(folder).resolve())}, key=len, reverse=True):
            message = message.replace(spelling, short)
    return message


def warm(
    paths: DataPaths | None = None, specs: Sequence[ToolSpec] | None = None
) -> dict[str, float]:
    """Load what the first requests would otherwise wait for: the session catalog, the M4
    results check and each spec's own `warm` (the style tables). Seconds per step, each step's
    own time. Never raises: a step that fails is logged and skipped, and the API's health check
    reports the cause.

    The steps run side by side and all have finished on return. They read different files (the
    sessions table, the results), each through its own caches, so on a network filesystem (the
    deployed API's volume) their round trips overlap instead of adding up."""
    paths = paths or DataPaths()
    if specs is None:
        from race_engineer.tools.definitions import TOOLS

        specs = TOOLS
    steps: dict[str, Callable[[], object]] = {
        "catalog": lambda: list_sessions(root=paths.processed),
        "results": lambda: provenance.check_current(
            paths.results / MISTAKES_FILE, "explain", paths.results
        ),
    }
    steps |= {spec.name: functools.partial(spec.warm, paths) for spec in specs if spec.warm}

    def timed(name: str, step: Callable[[], object]) -> float:
        start = time.perf_counter()
        try:
            step()
        except Exception as exc:  # reported by the health check, not fatal at startup
            log.warning("warm-up step %s failed: %s", name, exc)
        return round(time.perf_counter() - start, 3)

    with ThreadPoolExecutor(max_workers=len(steps), thread_name_prefix="warm") as pool:
        running = {name: pool.submit(timed, name, step) for name, step in steps.items()}
    return {name: future.result() for name, future in running.items()}
