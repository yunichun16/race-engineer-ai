// The chat transcript as a pure reducer: questions, streamed events and their outcomes in,
// a new ChatState out. No fetch, no storage, no clock: the store (store.ts) does those and
// passes times in, so every rule here is a plain unit test, and a recorded answer can be folded
// into a turn exactly as it streamed (replay, for M7's saved example answers).
//
// The rules, in the order of plan section 8.4:
// - `text` appends to the last streamed part if it is text, else starts a new text part.
// - `tool_call` adds a tool part; `tool_result` fills the part with the same id (or adds one).
//   From then on the result and its chart keep their identity: updates copy the arrays around
//   them, never the chart, so a memoised chart doesn't redraw on every text delta.
// - `retry` and `refusal` drop the text after the last tool part that has a result, or all of
//   the turn's text when there is none (report/m5_backend.md section 7: that text is void).
// - `error` keeps the partial text and records the problem; `done` still follows.
// - `done` stores the history, signature and questions left exactly as sent.
// - `ended` (the stream closed without `done`) and `stopped` keep the old history and
//   signature, so the question isn't counted.
// - `failed` (refused before the stream started) either blocks the conversation or marks the
//   turn for "Try again".
// - `restore` marks any turn that was still streaming as cut off, and drops a "chat off" block
//   (the server is checked again after a reload).
// - `attach` and `attached` (a corner chart opened from a card, through REST) never touch the
//   history, the signature or the questions left.
// M7 adds the chat's limits and saved example answers (plan 4.4):
// - A refusal by a limit before the stream (`rate_limited`, `daily_cap`, `chat_paused`,
//   `chat_unavailable`) sets `unavailable` and takes the refused question back out of the
//   transcript: the server didn't count it, and the page puts it back in the composer.
//   `chat_disabled` keeps M6's block and sets `unavailable` too.
// - `chatStatus` (GET /api/chat/status, read as the page opens) sets `unavailable` before anyone
//   types, and the quota; `tick` clears `unavailable` once its `retryAt` has passed.
// - `showSaved` appends a recorded answer as a turn marked `saved`, folded exactly as it
//   streamed (replay). Like attachments it never touches the history, the signature or the
//   questions left, so a saved turn is never sent to the server.
// - No question can be asked while `unavailable` is set.
// Events for a turn that has already finished are ignored, so a late event after Stop can't
// change anything.

import type { ChatStatus } from "../api/types.ts";
import type { ChatEvent, ChatQuota, StreamErrorEvent, ToolResultEvent } from "./types.ts";

/** A chart as a tool part keeps it. `dropped` is set by persist.ts when the chart's data
 *  couldn't be kept in storage: `data` then holds only the fields explorer links need. */
export interface ToolChart {
  bundle: string;
  data: unknown;
  dropped?: boolean;
}

export interface ToolResult {
  is_error: boolean;
  summary: string;
  chart: ToolChart | null;
}

export interface TextPart {
  kind: "text";
  text: string;
}

export interface ToolPart {
  kind: "tool";
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: ToolResult;
}

/** Which corner an attachment shows, as the find-mistakes row named it: the card's title while
 *  it loads, and the row the list marks as open. */
export interface AttachmentCorner {
  driver: string;
  lap_number: number;
  turn: string;
}

/** A corner chart opened from a find-mistakes card ("Show telemetry"), not part of the
 *  conversation. Attachments always sit after the streamed parts. */
export interface AttachmentPart {
  kind: "attachment";
  id: string;
  forToolId: string;
  state: "loading" | "ok" | "error";
  corner?: AttachmentCorner;
  data?: unknown;
  summary?: string;
  error?: string;
  dropped?: boolean;
}

export type Part = TextPart | ToolPart | AttachmentPart;

export type TurnPhase = "sending" | "streaming" | "done";
export type TurnOutcome = "answered" | "refused" | "error" | "stopped" | "interrupted";

export interface TurnProblem {
  code: string;
  message: string;
  retryable: boolean; // shows "Try again"
}

export interface TurnMeta {
  model: string;
  ms: number;
  cost_usd: number | null;
  questions_left: number;
}

export interface Turn {
  id: string;
  question: string;
  startedAt: number; // the `ask` time, so `done` can work out how long the answer took
  parts: Part[];
  notes: string[]; // the status texts, in order
  phase: TurnPhase;
  outcome?: TurnOutcome;
  problem?: TurnProblem;
  meta?: TurnMeta;
  // Set by `done`: the history came back longer, so the server counted this question (it can
  // fail mid-answer after a completed tool step and still count). Such a turn is part of the
  // conversation the model holds, so "Try again" never hides it.
  counted?: boolean;
  /** A saved example answer (`showSaved`), not part of the conversation the server holds. */
  saved?: SavedMark;
}

/** Which recording a saved turn shows, and when it was made (ISO 8601, UTC). */
export interface SavedMark {
  id: string;
  recordedAt: string;
}

export interface ChatBlock {
  code: string;
  message: string;
}

/** The codes that close the chat for a while: the limits' refusals (plan 3.6), and the chat
 *  switched off. */
export type UnavailableCode = "rate_limited" | "daily_cap" | "chat_paused" | "chat_unavailable" | "chat_disabled";

/** Why the chat can't take a question now. The page shows saved answers meanwhile. */
export interface Unavailable {
  code: UnavailableCode;
  message: string; // the server's words, the fallback for the site's copy ("" from the status)
  retryAt: number | null; // when asking may work again (ms since the epoch); null: not said
  limit?: "hour" | "day"; // which per-visitor limit, for rate_limited
  max?: number; // that limit's size (10 an hour, 25 a day), when the status said
  question?: string; // the question it refused, if it refused one (the page puts it back)
}

/** The visitor's quota: from the last `done`, and from the status (which adds the sizes). */
export interface QuotaState extends ChatQuota {
  per_hour?: number;
  per_day?: number;
}

export interface ChatState {
  turns: Turn[];
  history: unknown[]; // the last `done` history, sent back unchanged
  signature: string | null;
  questionsLeft: number | null; // null until the first `done`
  blocked: ChatBlock | null; // no more questions in this conversation
  unavailable: Unavailable | null; // no questions from this visitor for now (limits, chat off)
  quota: QuotaState | null; // null while the server has no limits on, or hasn't said
}

/** What `failed` needs from an ApiError (lib/api/client.ts), which satisfies it. */
export interface PreStreamError {
  status: number;
  code: string;
  message: string;
  retryAfterS?: number | null; // the limits' refusals say when to try again
  limit?: string; // "hour" or "day" for rate_limited
}

export type ChatAction =
  // `replaces`: "Try again" asks the same question in place of the last turn, which failed. A
  // failed turn the server counted (`counted`) stays, and the new turn follows it.
  | { type: "ask"; id: string; question: string; at: number; replaces?: string }
  | { type: "event"; id: string; event: ChatEvent; at: number }
  // `at` turns a refusal's retry_after_s into a time; without it there is no retryAt.
  | { type: "failed"; id: string; error: PreStreamError; at?: number }
  | { type: "ended"; id: string }
  | { type: "stopped"; id: string }
  | { type: "reset" }
  | { type: "restore"; state: ChatState }
  | { type: "attach"; turnId: string; forToolId: string; id: string; corner?: AttachmentCorner }
  | { type: "attached"; turnId: string; id: string; data?: unknown; summary?: string; error?: string }
  // A saved example answer: `id` is the recording's, `turnId` the new turn's (else derived).
  | {
      type: "showSaved";
      id: string;
      question: string;
      events: readonly ChatEvent[];
      recordedAt: string;
      turnId?: string;
    }
  | { type: "chatStatus"; status: ChatStatus; at: number }
  | { type: "tick"; at: number };

/** The copy the reducer puts into turns and blocks (plan section 8.8). */
export const COPY = {
  turnLimit: "That's the 8-question limit for one conversation. Start a new one to keep going.",
  cantContinue:
    "This conversation can't continue: the server restarted or was updated. Your answers stay " +
    "here until you start a new one.",
  tooLarge: "This conversation has grown too long to send. Start a new one.",
  invalidRequestPrefix: "That question couldn't be sent: ",
  cutOff: "The answer was cut off.",
  stopped: "Stopped.",
  notConfigured: "The chat isn't configured on this server.",
  askingAgain: "Asking again…",
  refused: "Claude declined to answer this. Try asking another way.",
  unreachable: "Can't reach the analysis server.",
  streamError: "Something went wrong while answering.",
  attachmentLost: "The chart didn't finish loading.",
  chatOff: "The chat is switched off on this server.",
} as const;

/** Stream `error` codes worth a "Try again" with the same history (plan 8.8). */
export const RETRYABLE_STREAM_CODES: ReadonlySet<string> = new Set([
  "busy",
  "upstream_busy",
  "upstream_unavailable",
  "upstream_unreachable",
  "timeout",
  "internal_error",
]);

export const INITIAL_CHAT_STATE: ChatState = {
  turns: [],
  history: [],
  signature: null,
  questionsLeft: null,
  blocked: null,
  unavailable: null,
  quota: null,
};

/** The pre-stream codes of the limits (plan 3.6), which set `unavailable` instead of failing a
 *  turn. */
export const LIMIT_CODES: ReadonlySet<string> = new Set(["rate_limited", "daily_cap", "chat_paused", "chat_unavailable"]);

/** What the status's reason means for the page. */
const STATUS_CODES: Record<NonNullable<ChatStatus["reason"]>, UnavailableCode> = {
  off: "chat_disabled",
  paused: "chat_paused",
  daily_cap: "daily_cap",
  rate_limited: "rate_limited",
  unavailable: "chat_unavailable",
};

// ---- Parts ----

/** Where streamed parts go: before any attachments, which always trail. */
function streamEnd(parts: Part[]): number {
  let end = parts.length;
  while (end > 0 && parts[end - 1].kind === "attachment") end--;
  return end;
}

function insertStreamed(parts: Part[], part: Part): Part[] {
  const out = parts.slice();
  out.splice(streamEnd(parts), 0, part);
  return out;
}

function appendText(parts: Part[], delta: string): Part[] {
  const end = streamEnd(parts);
  const previous = end > 0 ? parts[end - 1] : undefined;
  if (previous?.kind === "text") {
    const out = parts.slice();
    out[end - 1] = { kind: "text", text: previous.text + delta };
    return out;
  }
  return insertStreamed(parts, { kind: "text", text: delta });
}

function addResult(parts: Part[], event: ToolResultEvent): Part[] {
  // The event's chart object is kept as it is, so its identity holds from here on.
  const result: ToolResult = { is_error: event.is_error, summary: event.summary, chart: event.chart };
  for (let i = parts.length - 1; i >= 0; i--) {
    const part = parts[i];
    if (part.kind === "tool" && part.id === event.id) {
      const out = parts.slice();
      out[i] = { ...part, result };
      return out;
    }
  }
  return insertStreamed(parts, { kind: "tool", id: event.id, name: event.name, input: {}, result });
}

/** The text after the last tool part with a result is void (retry, refusal). */
function dropVoidText(parts: Part[]): Part[] {
  let anchor = -1;
  for (let i = parts.length - 1; i >= 0; i--) {
    const part = parts[i];
    if (part.kind === "tool" && part.result !== undefined) {
      anchor = i;
      break;
    }
  }
  const kept = parts.filter((part, i) => i <= anchor || part.kind !== "text");
  return kept.length === parts.length ? parts : kept;
}

// ---- Problems ----

function streamProblem(event: StreamErrorEvent): TurnProblem {
  const message =
    event.code === "chat_not_configured" ? COPY.notConfigured : event.message || COPY.streamError;
  return { code: event.code, message, retryable: RETRYABLE_STREAM_CODES.has(event.code) };
}

/** What a refusal before the stream does: the turn's problem, and a block when no question can
 *  succeed in this conversation any more. */
export function preStreamOutcome(error: PreStreamError): { problem: TurnProblem; block: ChatBlock | null } {
  const { code } = error;
  const blocking = (message: string) => ({
    problem: { code, message, retryable: false },
    block: { code, message },
  });
  switch (code) {
    case "chat_disabled":
      return blocking(error.message);
    case "turn_limit":
      return blocking(COPY.turnLimit);
    case "invalid_history":
    case "conversation_expired":
      return blocking(COPY.cantContinue);
    case "history_too_large":
      return blocking(COPY.tooLarge);
    case "busy":
    case "bad_payload": // a 200 that wasn't an event stream (plan 6.6: Try again)
      return { problem: { code, message: error.message, retryable: true }, block: null };
    case "unreachable":
      return { problem: { code, message: error.message || COPY.unreachable, retryable: true }, block: null };
    case "invalid_request":
      return {
        problem: { code, message: COPY.invalidRequestPrefix + error.message, retryable: false },
        block: null,
      };
    default:
      // Unknown codes show the server's own message. (The limits' codes never get here:
      // `failed` turns them into `unavailable`.)
      return {
        problem: {
          code,
          message: error.message || `The server answered ${error.status}.`,
          retryable: false,
        },
        block: null,
      };
  }
}

// ---- The limits ----

function limitOf(value: unknown): "hour" | "day" | undefined {
  return value === "hour" || value === "day" ? value : undefined;
}

/** `unavailable` for a limit's refusal (of `question`, which the page puts back), or for
 *  chat_disabled (whose turn stays, so there is no question to give back). */
function refusedBy(error: PreStreamError, question: string | null, quota: QuotaState | null, at: number | undefined): Unavailable {
  const wait = error.retryAfterS;
  const retryAt = typeof wait === "number" && Number.isFinite(wait) && at !== undefined ? at + wait * 1000 : null;
  const out: Unavailable = { code: error.code as UnavailableCode, message: error.message, retryAt };
  if (question !== null) out.question = question;
  const limit = error.code === "rate_limited" ? limitOf(error.limit) : undefined;
  if (limit !== undefined) {
    out.limit = limit;
    const max = limit === "hour" ? quota?.per_hour : quota?.per_day;
    if (max !== undefined) out.max = max;
  }
  return out;
}

/** `unavailable` as the status describes it; null when the chat would take a question. */
function fromStatus(status: ChatStatus, at: number): Unavailable | null {
  if (status.available) return null;
  const code = STATUS_CODES[status.reason ?? "unavailable"];
  const retryAt = status.retry_after_s === null ? null : at + status.retry_after_s * 1000;
  const out: Unavailable = { code, message: "", retryAt };
  if (code === "rate_limited") {
    // The status doesn't name the limit: the day's is the one at zero, else the hour's.
    const quota = status.quota;
    const limit = quota !== null && quota.day_left <= 0 ? "day" : "hour";
    out.limit = limit;
    if (quota !== null) out.max = limit === "hour" ? quota.per_hour : quota.per_day;
  }
  return out;
}

function isSavedMark(value: unknown): value is SavedMark {
  if (typeof value !== "object" || value === null) return false;
  const mark = value as Record<string, unknown>;
  return typeof mark.id === "string" && typeof mark.recordedAt === "string";
}

/** A turn id no turn uses yet, from `base`. */
function freeTurnId(state: ChatState, base: string): string {
  let id = base;
  for (let n = 2; indexOfTurn(state, id) !== -1; n++) id = `${base}-${n}`;
  return id;
}

// ---- The reducer ----

function indexOfTurn(state: ChatState, id: string): number {
  for (let i = state.turns.length - 1; i >= 0; i--) if (state.turns[i].id === id) return i;
  return -1;
}

function withTurn(state: ChatState, index: number, turn: Turn): ChatState {
  const turns = state.turns.slice();
  turns[index] = turn;
  return { ...state, turns };
}

/** The turn still being answered, if any. */
export function activeTurn(state: ChatState): Turn | null {
  const last = state.turns[state.turns.length - 1];
  return last !== undefined && last.phase !== "done" ? last : null;
}

/** Whether a new question can be sent now. */
export function canAsk(state: ChatState): boolean {
  return state.blocked === null && state.unavailable === null && activeTurn(state) === null;
}

function applyEvent(state: ChatState, id: string, event: ChatEvent, at: number): ChatState {
  const index = indexOfTurn(state, id);
  if (index === -1) return state;
  const turn = state.turns[index];
  if (turn.phase === "done") return state;
  let next: Turn = turn.phase === "sending" ? { ...turn, phase: "streaming" } : turn;

  switch (event.type) {
    case "status":
      next = { ...next, notes: [...next.notes, event.text] };
      break;
    case "text":
      if (event.delta !== "") next = { ...next, parts: appendText(next.parts, event.delta) };
      break;
    case "tool_call":
      next = {
        ...next,
        parts: insertStreamed(next.parts, {
          kind: "tool",
          id: event.id,
          name: event.name,
          input: event.input,
        }),
      };
      break;
    case "tool_result":
      next = { ...next, parts: addResult(next.parts, event) };
      break;
    case "retry":
      next = { ...next, parts: dropVoidText(next.parts), notes: [...next.notes, COPY.askingAgain] };
      break;
    case "refusal":
      next = {
        ...next,
        parts: dropVoidText(next.parts),
        outcome: "refused",
        problem: { code: "refusal", message: event.message || COPY.refused, retryable: false },
      };
      break;
    case "error":
      next = { ...next, outcome: "error", problem: streamProblem(event) };
      break;
    case "done": {
      next = {
        ...next,
        phase: "done",
        outcome: next.outcome ?? "answered",
        // The history is append-only, and a question that got nowhere comes back unchanged.
        counted: event.history.length > state.history.length,
        meta: {
          model: event.model,
          ms: Math.max(0, at - turn.startedAt),
          cost_usd: event.cost_usd,
          questions_left: event.questions_left,
        },
      };
      const blocked =
        event.questions_left <= 0 ? { code: "turn_limit", message: COPY.turnLimit } : state.blocked;
      return {
        ...withTurn(state, index, next),
        history: event.history,
        signature: event.signature,
        questionsLeft: event.questions_left,
        blocked,
        // The status's limit sizes stay; no quota means the server has no limits on.
        quota: event.quota === null ? null : { ...state.quota, ...event.quota },
      };
    }
  }
  return next === turn ? state : withTurn(state, index, next);
}

/** Ends an unfinished turn; an unknown or finished turn leaves the state as it was. */
function finish(state: ChatState, id: string, change: (turn: Turn) => Turn): ChatState {
  const index = indexOfTurn(state, id);
  if (index === -1 || state.turns[index].phase === "done") return state;
  return withTurn(state, index, { ...change(state.turns[index]), phase: "done" });
}

const CUT_OFF: TurnProblem = { code: "interrupted", message: COPY.cutOff, retryable: true };

function restoredTurn(turn: Turn): Turn {
  let next = turn;
  // The mark is shown as text (the date), so one that came back malformed is dropped.
  if (turn.saved !== undefined && !isSavedMark(turn.saved)) {
    next = { ...turn };
    delete next.saved;
  }
  if (next.phase !== "done") {
    next = {
      ...next,
      phase: "done",
      outcome: next.outcome ?? "interrupted",
      problem: next.problem ?? CUT_OFF,
    };
  }
  if (next.parts.some((part) => part.kind === "attachment" && part.state === "loading")) {
    next = {
      ...next,
      parts: next.parts.map((part): Part =>
        part.kind === "attachment" && part.state === "loading"
          ? { ...part, state: "error", error: COPY.attachmentLost }
          : part,
      ),
    };
  }
  return next;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "ask": {
      if (!canAsk(state) || indexOfTurn(state, action.id) !== -1) return state;
      let turns = state.turns;
      const last = turns[turns.length - 1];
      if (action.replaces !== undefined && last?.id === action.replaces && last.counted !== true) {
        turns = turns.slice(0, -1);
      }
      const turn: Turn = {
        id: action.id,
        question: action.question,
        startedAt: action.at,
        parts: [],
        notes: [],
        phase: "sending",
      };
      return { ...state, turns: [...turns, turn] };
    }

    case "event":
      return applyEvent(state, action.id, action.event, action.at);

    case "failed": {
      if (action.error.code === "aborted") {
        return finish(state, action.id, (turn) => ({ ...turn, outcome: turn.outcome ?? "stopped" }));
      }
      if (LIMIT_CODES.has(action.error.code)) {
        // Nothing was counted and nothing streamed: the turn goes, and its question with the
        // refusal, for the page to put back in the composer.
        const index = indexOfTurn(state, action.id);
        if (index === -1 || state.turns[index].phase === "done") return state;
        const { question } = state.turns[index];
        return {
          ...state,
          turns: state.turns.filter((_, i) => i !== index),
          unavailable: refusedBy(action.error, question, state.quota, action.at),
        };
      }
      const { problem, block } = preStreamOutcome(action.error);
      const next = finish(state, action.id, (turn) => ({ ...turn, outcome: "error", problem }));
      if (next === state || block === null) return next;
      return {
        ...next,
        blocked: block,
        questionsLeft: block.code === "turn_limit" ? 0 : next.questionsLeft,
        // The chat switched off: saved answers are offered as for the limits (plan 4.4).
        unavailable: block.code === "chat_disabled" ? refusedBy(action.error, null, null, undefined) : next.unavailable,
      };
    }

    case "ended":
      return finish(state, action.id, (turn) => ({
        ...turn,
        outcome: turn.outcome ?? "interrupted",
        problem: turn.problem ?? CUT_OFF,
      }));

    case "stopped":
      return finish(state, action.id, (turn) => ({ ...turn, outcome: turn.outcome ?? "stopped" }));

    case "reset":
      // A new conversation; the limits are this visitor's, not the conversation's, so they stay.
      if (state.unavailable === null && state.quota === null) return INITIAL_CHAT_STATE;
      return { ...INITIAL_CHAT_STATE, unavailable: state.unavailable, quota: state.quota };

    case "restore": {
      const turns = action.state.turns.map(restoredTurn);
      const changed = turns.some((turn, i) => turn !== action.state.turns[i]);
      // "Chat off" described the server when the question was sent, not the conversation. The
      // page checks the server again after a reload, so that block isn't kept: otherwise the tab
      // would stay switched off after the server's chat came back.
      const stale = action.state.blocked?.code === "chat_disabled";
      // The limits and the quota described the server too, and the page asks for its status
      // again. (A copy saved before M7 has neither field: `undefined` isn't null either.)
      const limits = action.state.unavailable !== null || action.state.quota !== null;
      if (!changed && !stale && !limits) return action.state;
      return {
        ...action.state,
        turns,
        blocked: stale ? null : action.state.blocked,
        unavailable: null,
        quota: null,
      };
    }

    case "attach": {
      const index = indexOfTurn(state, action.turnId);
      if (index === -1) return state;
      const turn = state.turns[index];
      const hasTool = turn.parts.some((part) => part.kind === "tool" && part.id === action.forToolId);
      const taken = turn.parts.some((part) => part.kind === "attachment" && part.id === action.id);
      if (!hasTool || taken) return state;
      const part: AttachmentPart = {
        kind: "attachment",
        id: action.id,
        forToolId: action.forToolId,
        state: "loading",
      };
      if (action.corner !== undefined) part.corner = action.corner;
      return withTurn(state, index, { ...turn, parts: [...turn.parts, part] });
    }

    case "attached": {
      const index = indexOfTurn(state, action.turnId);
      if (index === -1) return state;
      const turn = state.turns[index];
      const at = turn.parts.findIndex((part) => part.kind === "attachment" && part.id === action.id);
      if (at === -1) return state;
      const old = turn.parts[at] as AttachmentPart;
      const part: AttachmentPart = {
        kind: "attachment",
        id: old.id,
        forToolId: old.forToolId,
        state: action.error !== undefined ? "error" : "ok",
      };
      if (old.corner !== undefined) part.corner = old.corner;
      if (action.data !== undefined) part.data = action.data;
      if (action.summary !== undefined) part.summary = action.summary;
      if (action.error !== undefined) part.error = action.error;
      const parts = turn.parts.slice();
      parts[at] = part;
      return withTurn(state, index, { ...turn, parts });
    }

    case "showSaved": {
      // Once per recording in a conversation, and never while an answer streams: the newest
      // turn is the one being answered.
      if (activeTurn(state) !== null || state.turns.some((turn) => turn.saved?.id === action.id)) return state;
      const id = action.turnId ?? freeTurnId(state, `saved-${action.id}`);
      if (indexOfTurn(state, id) !== -1) return state;
      const turn: Turn = { ...replay(action.events, action.question, id), saved: { id: action.id, recordedAt: action.recordedAt } };
      return { ...state, turns: [...state.turns, turn] };
    }

    case "chatStatus":
      return {
        ...state,
        unavailable: fromStatus(action.status, action.at),
        quota: action.status.quota === null ? null : { ...action.status.quota },
      };

    case "tick": {
      const { unavailable } = state;
      if (unavailable === null || unavailable.retryAt === null || action.at < unavailable.retryAt) return state;
      return { ...state, unavailable: null };
    }
  }
}

/**
 * A recorded answer folded into a turn, exactly as reducing the events live would give
 * (M7's saved example answers). A recording without `done` ends as cut off.
 */
export function replay(events: readonly ChatEvent[], question: string, id = "replay"): Turn {
  let state = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id, question, at: 0 });
  for (const event of events) state = chatReducer(state, { type: "event", id, event, at: 0 });
  state = chatReducer(state, { type: "ended", id });
  return state.turns[0];
}

// ---- The limits' words (plan 4.4: the site's own copy; the server's message isn't shown) ----

export interface UnavailableCopy {
  /** The notice: what happened, and when asking works again if the server said. */
  title: string;
  /** The title as a screen reader gets it: the same, without a countdown. The notice is a live
   *  region, so "in 23 min" turning into "in 22 min" would be read out again every minute; this
   *  names the time instead ("at 14:05", in the visitor's own clock), which holds while it shows. */
  spoken: string;
  /** The line that leads into the saved answers, shown only when there are some. */
  saved: string;
  /** A few words for the composer's bar while it can't send. */
  meter: string;
  tone: "info" | "warn";
}

const EARLIER = "Here are answers it gave earlier.";
const MEANWHILE = "Meanwhile, saved answers show what the chat does.";

/** Whole minutes until `retryAt`, rounded up, at least 1. */
function minutesUntil(retryAt: number, now: number): number {
  return Math.max(1, Math.ceil((retryAt - now) / 60_000));
}

/** "12 min", "3 h", "3 h 12 min". */
function hoursMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** "45 s" under two minutes, else "3 min": the request rate's waits are short. */
function shortWait(retryAt: number, now: number): string {
  const s = Math.max(1, Math.ceil((retryAt - now) / 1000));
  return s < 120 ? `${s} s` : `${minutesUntil(retryAt, now)} min`;
}

/** The time of day `at` (ms since the epoch) on the visitor's clock: "14:05", "2:05 PM". */
export function clockTime(at: number): string {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(at);
}

/** The notice's words for `unavailable` at time `now` (the page re-renders every 30 s). */
export function unavailableCopy(u: Unavailable, now: number): UnavailableCopy {
  switch (u.code) {
    case "rate_limited": {
      if (u.limit === "hour") {
        const first =
          u.max !== undefined
            ? `You've asked ${u.max} questions in the last hour from this connection.`
            : "You've reached this connection's hourly limit of questions.";
        const minutes = u.retryAt === null ? null : minutesUntil(u.retryAt, now);
        return {
          tone: "warn",
          title: `${first} ${minutes === null ? "You can ask again later." : `You can ask again in ${minutes} min.`}`,
          spoken: `${first} ${u.retryAt === null ? "You can ask again later." : `You can ask again at ${clockTime(u.retryAt)}.`}`,
          saved: MEANWHILE,
          meter: minutes === null ? "Hourly limit reached" : `Ask again in ${minutes} min`,
        };
      }
      if (u.limit === "day") {
        const first =
          u.max !== undefined
            ? `That's today's ${u.max} questions from this connection.`
            : "That's today's limit of questions from this connection.";
        const left = u.retryAt === null ? null : hoursMinutes(minutesUntil(u.retryAt, now));
        return {
          tone: "warn",
          title: `${first} The count resets at midnight UTC${left === null ? "" : `, in ${left}`}.`,
          spoken: `${first} The count resets at midnight UTC.`,
          saved: MEANWHILE,
          meter: left === null ? "Daily limit reached" : `Ask again in ${left}`,
        };
      }
      // The request rate (several questions within a minute), or a limit this site doesn't know.
      const wait = u.retryAt === null ? null : shortWait(u.retryAt, now);
      return {
        tone: "warn",
        title: `Too many questions from this connection at once. ${wait === null ? "Try again shortly." : `You can ask again in ${wait}.`}`,
        spoken: "Too many questions from this connection at once. Try again shortly.",
        saved: MEANWHILE,
        meter: wait === null ? "Try again shortly" : `Ask again in ${wait}`,
      };
    }
    case "daily_cap":
      return steady("info", "The chat has used today's budget for this free demo. It resets at midnight UTC.", "Resets at midnight UTC");
    case "chat_paused":
      return steady("info", "The chat is paused for now.", "Chat paused");
    case "chat_unavailable":
      return steady("warn", "The chat can't check its limits right now, so it's off for a moment.", "Chat off for a moment");
    case "chat_disabled":
      return steady("info", COPY.chatOff, "Chat off");
  }
}

/** A notice with no countdown, which leads into the saved answers given earlier. */
function steady(tone: UnavailableCopy["tone"], title: string, meter: string): UnavailableCopy {
  return { tone, title, spoken: title, saved: EARLIER, meter };
}

/** The line under a saved turn's label: how it was made, and why it shows instead of a live
 *  answer, while that still holds. */
export function savedNote(u: Unavailable | null): string {
  const base = "Recorded earlier with the same tools";
  if (u === null) return `${base}.`;
  let state: string;
  switch (u.code) {
    case "rate_limited":
      state = u.limit === "hour" ? "at your hourly limit" : u.limit === "day" ? "at your daily limit" : "at its limit for now";
      break;
    case "daily_cap":
      state = "at today's limit";
      break;
    case "chat_paused":
      state = "paused";
      break;
    case "chat_unavailable":
      state = "off for a moment";
      break;
    case "chat_disabled":
      state = "off";
      break;
  }
  return `${base}, while the live chat is ${state}.`;
}

/** "3 questions left today" once 5 or fewer remain under the visitor's daily limit and that
 *  limit comes before the conversation's (plan 4.4); null otherwise. */
export function dailyMeter(quota: QuotaState | null, questionsLeft: number | null): string | null {
  if (quota === null || quota.day_left > 5) return null;
  if (questionsLeft !== null && questionsLeft < quota.day_left) return null;
  if (quota.day_left <= 0) return "No questions left today";
  return quota.day_left === 1 ? "1 question left today" : `${quota.day_left} questions left today`;
}

// ---- Selectors for the views ----

/** The tool parts whose result has a chart, in tool order (the chart cards under a turn). */
export function chartParts(turn: Turn): ToolPart[] {
  return turn.parts.filter(
    (part): part is ToolPart => part.kind === "tool" && part.result?.chart != null,
  );
}

/** The attachments opened from one tool's card, oldest first. */
export function attachmentsFor(turn: Turn, toolId: string): AttachmentPart[] {
  return turn.parts.filter(
    (part): part is AttachmentPart => part.kind === "attachment" && part.forToolId === toolId,
  );
}

/** The answer's text, with the tool steps left out (for copying, or a text version). The
 *  pieces between tool steps become paragraphs. */
export function answerText(turn: Turn): string {
  return turn.parts
    .filter((part): part is TextPart => part.kind === "text")
    .map((part) => part.text.trim())
    .filter((text) => text !== "")
    .join("\n\n");
}
