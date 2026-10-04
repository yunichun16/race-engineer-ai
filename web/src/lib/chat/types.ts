// The chat's wire format: the body of POST /api/chat and the server-sent events that answer it.
//
// These mirror the engine, which is the source of truth:
// - the events and their fields: engine/src/race_engineer/api/chat/runner.py (the list at the
//   top of the file, and the dictionaries built in QuestionRun);
// - the request body and its limits: engine/src/race_engineer/api/routes/chat.py (ChatRequest)
//   and api/chat/session.py (MAX_HISTORY_MESSAGES).
//
// Every event arrives as `event: <type>` plus one `data:` line of JSON. `done` is always last,
// even after `error` or `refusal`. This file holds types and constants only; the runtime checks
// that turn a raw event into one of these live in sse.ts (parseChatEvent).

/** A chart tool's result as the browser gets it: the bundle names the chart page. */
export interface ChartPayload {
  bundle: string; // "find-mistakes", "explain-corner", "race-summary", "compare-styles", "compare-laps"
  resource_uri: string; // the MCP App resource, e.g. "ui://race-engineer/find-mistakes.html"
  data: unknown; // the chart's own payload; each chart's guard checks it before drawing
}

/** Tokens one question used, summed over its API calls. */
export interface ChatUsage {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

/** A progress note (a thinking block shown as an update). */
export interface StatusEvent {
  type: "status";
  text: string;
}

/** A piece of the answer's text. */
export interface TextDeltaEvent {
  type: "text";
  delta: string;
}

/** The model asked for a tool; sent after its API call ends, before the tool runs. */
export interface ToolCallEvent {
  type: "tool_call";
  id: string;
  name: string;
  input: Record<string, unknown>;
}

/** A tool finished. `chart` is set only for a chart tool that succeeded. */
export interface ToolResultEvent {
  type: "tool_result";
  id: string;
  name: string;
  is_error: boolean;
  summary: string; // exactly what the model was shown ("What Claude saw")
  chart: ChartPayload | null;
}

/** The model's tool call came through garbled: the text since the last tool result is void. */
export interface RetryEvent {
  type: "retry";
  message: string;
}

/** The model declined. No tools ran for that step; the text streamed since the last tool
 *  result is void. */
export interface RefusalEvent {
  type: "refusal";
  message: string;
  category: string | null;
}

/** Something went wrong mid-answer; `done` still follows. */
export interface StreamErrorEvent {
  type: "error";
  code: string; // see STREAM_ERROR_CODES; unknown codes are shown with the server's message
  message: string;
}

/** What this visitor has left under the per-connection limits (M7), after this question. */
export interface ChatQuota {
  hour_left: number;
  day_left: number;
}

/** Always last. The history and signature go back with the next question, unchanged. */
export interface DoneEvent {
  type: "done";
  history: unknown[];
  signature: string;
  questions_used: number;
  questions_left: number;
  stop_reason: string | null;
  model: string; // "scripted" in fake mode
  usage: ChatUsage;
  cost_usd: number | null;
  request_id: string;
  quota: ChatQuota | null; // null when the server has no limits on (locally, and in fake mode)
}

export type ChatEvent =
  | StatusEvent
  | TextDeltaEvent
  | ToolCallEvent
  | ToolResultEvent
  | RetryEvent
  | RefusalEvent
  | StreamErrorEvent
  | DoneEvent;

export type ChatEventType = ChatEvent["type"];

/** The event names the client understands, in the order the runner documents them. */
export const CHAT_EVENT_TYPES: readonly ChatEventType[] = [
  "status",
  "text",
  "tool_call",
  "tool_result",
  "retry",
  "refusal",
  "error",
  "done",
];

/** The `error` event codes the runner sends today (runner.py MESSAGES and error_code).
 *  `too_costly` (M7) is the per-question cost ceiling: a narrower question can still work. */
export const STREAM_ERROR_CODES = [
  "busy",
  "truncated",
  "step_limit",
  "too_costly",
  "timeout",
  "upstream_busy",
  "upstream_unavailable",
  "upstream_unreachable",
  "chat_not_configured",
  "bad_request",
  "internal_error",
] as const;

/** The body of POST /api/chat. */
export interface ChatRequest {
  message: string; // 1 to MAX_MESSAGE_CHARS characters, not blank
  history: unknown[]; // the previous `done` history, unchanged; [] for a new conversation
  signature: string | null; // the previous `done` signature; null for a new conversation
}

/** routes/chat.py MAX_MESSAGE_CHARS. */
export const MAX_MESSAGE_CHARS = 2000;
/** session.py MAX_HISTORY_MESSAGES. */
export const MAX_HISTORY_MESSAGES = 128;
/** The client's own size check, under the server's 512 KB (session.py MAX_HISTORY_BYTES). */
export const MAX_HISTORY_CHARS = 500_000;
