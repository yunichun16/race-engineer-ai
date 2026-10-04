"""The API's response bodies, as pydantic models (they are also the schemas in /docs)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from race_engineer.tools.registry import ToolSpec


class ChartRef(BaseModel):
    """The chart bundle that draws a tool's data: web/src/mcp-app/<bundle>.html, served to MCP
    hosts as `resource_uri`."""

    bundle: str = Field(examples=["find-mistakes"])
    resource_uri: str = Field(examples=["ui://race-engineer/find-mistakes.html"])

    @classmethod
    def of(cls, spec: ToolSpec) -> ChartRef | None:
        if spec.chart is None or spec.resource_uri is None:
            return None
        return cls(bundle=spec.chart, resource_uri=spec.resource_uri)


class ToolInfo(BaseModel):
    """One tool as GET /api/tools lists it: what the model is told, plus its chart."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    chart: ChartRef | None


class ToolResponse(BaseModel):
    """A tool's answer: `summary` is the text the model would read, `data` the chart payload
    (or the structured result of a text tool), `chart` the bundle that draws it."""

    tool: str
    summary: str
    data: dict[str, Any] | None
    chart: ChartRef | None


class ErrorDetail(BaseModel):
    code: str = Field(examples=["invalid_input"])
    message: str
    tool: str | None = None


class ErrorBody(BaseModel):
    """Every error the API answers with (except inside a chat's event stream)."""

    error: ErrorDetail


class DataHealth(BaseModel):
    sessions: int  # processed sessions
    latest: str | None  # the latest of them, e.g. "2026 Azerbaijan Grand Prix Race"
    results_run: str | None  # the current M4 scoring run
    results_current: bool  # mistakes.parquet comes from that run
    mistakes: int | None  # rows in mistakes.parquet
    style: bool  # the driving-style tables are built and current
    circuits: bool  # circuits.json (track map rotations) is built


ChatReason = Literal["off", "paused", "daily_cap", "unavailable"]


class ChatHealth(BaseModel):
    mode: Literal["anthropic", "fake", "off"]
    model: str
    base_url_host: str
    # Whether a question would be admitted now (a visitor's own quota aside), else why not.
    available: bool = True
    reason: ChatReason | None = None


class LimitsHealth(BaseModel):
    """The chat's limits (M7): whether they are on, the store keeping the counters, the
    per-visitor limits and what keeps them from working. No dollar amounts."""

    enabled: bool
    store: Literal["memory", "upstash"] | None
    per_hour: int
    per_day: int
    problems: list[str]


class McpHealth(BaseModel):
    mounted: bool
    path: str
    tools: int


class HealthResponse(BaseModel):
    """GET /api/health: `degraded` with the reasons in `problems` when the data or the chat
    can't serve every tool. No absolute paths: the app is deployed in M7."""

    status: Literal["ok", "degraded"]
    version: str
    data: DataHealth
    chat: ChatHealth
    mcp: McpHealth
    limits: LimitsHealth
    warm_s: dict[str, float]  # seconds per warm-up step at startup
    problems: list[str]


class ReadyResponse(BaseModel):
    """GET /api/ready: 200 once the server has warmed up and its data has no problems."""

    ready: bool
    problems: list[str] = []


class ChatQuota(BaseModel):
    hour_left: int
    day_left: int
    per_hour: int
    per_day: int


class SavedRef(BaseModel):
    id: str = Field(examples=["monaco-2023"])
    recorded_at: str = Field(examples=["2026-10-04T13:05:12Z"])


class ChatStatusResponse(BaseModel):
    """GET /api/chat/status: whether a question would be admitted now, read without counting
    one. `reason` says why not; `quota` is null while the limits are off; `visitor` is the
    caller's own 6-hex tag (null without limits or a client address), shown to no one else."""

    available: bool
    mode: Literal["anthropic", "fake", "off"]
    reason: Literal["off", "paused", "daily_cap", "rate_limited", "unavailable"] | None
    retry_after_s: int | None
    quota: ChatQuota | None
    visitor: str | None
    saved: list[SavedRef]
