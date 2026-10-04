// Keeping the conversation in this tab's sessionStorage, and the landing page's hand-over.
//
// - sessionStorage["re.chat.v1"] = { v: 1, state }. The server keeps no transcripts
//   (api/chat/session.py), so this is the only copy: it lets a reload, or a visit to another
//   page and back, show the conversation and carry on with its signed history.
// - If the state doesn't fit (a quota error), it is saved again with each chart's data cut down
//   to the fields its explorer link needs, marked `dropped`. Those cards then say the chart
//   wasn't kept and link to the explorer. If even that fails, the saved copy is removed rather
//   than left behind as an older conversation.
// - sessionStorage["re.chat.pending"] carries a question from the landing page's Ask box to
//   /chat, which reads and deletes it once (plan decision 8).
//
// Every storage access is in a try/catch: storage can be missing, full or blocked, and the chat
// must still work without it. What comes back from storage is checked before it is used.

import { linkFields } from "./links.ts";
import type { ChatState, Part, Turn } from "./reducer.ts";
import { MAX_MESSAGE_CHARS } from "./types.ts";

export const STORAGE_KEY = "re.chat.v1";
export const PENDING_KEY = "re.chat.pending";

/** The part of the Storage interface used here; sessionStorage satisfies it. */
export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** This tab's sessionStorage, or null where there is none (the server, a blocked browser). */
export function sessionStorageOrNull(): KeyValueStorage | null {
  // The window check comes first: Node 25 and later have a sessionStorage global of their own,
  // one in-memory store for the whole server process, which must never hold a visitor's chat.
  if (typeof window === "undefined") return null;
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null; // reading the property itself throws when storage is blocked
  }
}

export type SaveResult = "saved" | "saved-without-charts" | "failed";

function write(storage: KeyValueStorage, state: ChatState): boolean {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify({ v: 1, state }));
    return true;
  } catch {
    return false;
  }
}

export function saveChat(storage: KeyValueStorage | null, state: ChatState): SaveResult {
  if (storage === null) return "failed";
  if (write(storage, state)) return "saved";
  if (write(storage, withoutChartData(state))) return "saved-without-charts";
  try {
    storage.removeItem(STORAGE_KEY);
  } catch {
    // nothing more to do
  }
  return "failed";
}

/** The state with every chart's and attachment's data cut down to its link fields. */
export function withoutChartData(state: ChatState): ChatState {
  const lighter = (part: Part): Part => {
    if (part.kind === "tool" && part.result?.chart && !part.result.chart.dropped) {
      const chart = part.result.chart;
      return {
        ...part,
        result: { ...part.result, chart: { bundle: chart.bundle, data: linkFields(chart.data), dropped: true } },
      };
    }
    if (part.kind === "attachment" && part.data !== undefined && !part.dropped) {
      return { ...part, data: linkFields(part.data), dropped: true };
    }
    return part;
  };
  return { ...state, turns: state.turns.map((turn) => ({ ...turn, parts: turn.parts.map(lighter) })) };
}

// ---- Loading, with the shape checked ----

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function oneOf(value: unknown, options: readonly string[]): boolean {
  return typeof value === "string" && options.includes(value);
}

/** An optional field: absent, or of the right type. */
function optional(value: unknown, check: (value: unknown) => boolean): boolean {
  return value === undefined || check(value);
}

const isBoolean = (value: unknown): boolean => typeof value === "boolean";

/** An attachment's corner: its title and the row it marks, both shown as text. */
function isCorner(value: unknown): boolean {
  return isRecord(value) && isString(value.driver) && isNumber(value.lap_number) && isString(value.turn);
}

function isPart(value: unknown): boolean {
  if (!isRecord(value)) return false;
  switch (value.kind) {
    case "text":
      return isString(value.text);
    case "tool": {
      if (!isString(value.id) || !isString(value.name) || !isRecord(value.input)) return false;
      if (value.result === undefined) return true;
      const result = value.result;
      if (!isRecord(result) || typeof result.is_error !== "boolean" || !isString(result.summary)) return false;
      const chart = result.chart;
      return (
        chart === null ||
        (isRecord(chart) && isString(chart.bundle) && "data" in chart && optional(chart.dropped, isBoolean))
      );
    }
    case "attachment":
      // summary and error are shown as text, so anything else would break the card.
      return (
        isString(value.id) &&
        isString(value.forToolId) &&
        oneOf(value.state, ["loading", "ok", "error"]) &&
        optional(value.summary, isString) &&
        optional(value.error, isString) &&
        optional(value.dropped, isBoolean) &&
        optional(value.corner, isCorner)
      );
    default:
      return false;
  }
}

function isTurn(value: unknown): value is Turn {
  if (!isRecord(value)) return false;
  const { problem, meta } = value;
  return (
    isString(value.id) &&
    isString(value.question) &&
    isNumber(value.startedAt) &&
    Array.isArray(value.parts) &&
    value.parts.every(isPart) &&
    Array.isArray(value.notes) &&
    value.notes.every(isString) &&
    oneOf(value.phase, ["sending", "streaming", "done"]) &&
    (value.outcome === undefined ||
      oneOf(value.outcome, ["answered", "refused", "error", "stopped", "interrupted"])) &&
    optional(value.counted, isBoolean) &&
    (problem === undefined ||
      (isRecord(problem) &&
        isString(problem.code) &&
        isString(problem.message) &&
        typeof problem.retryable === "boolean")) &&
    (meta === undefined ||
      (isRecord(meta) &&
        isString(meta.model) &&
        isNumber(meta.ms) &&
        (meta.cost_usd === null || isNumber(meta.cost_usd)) &&
        isNumber(meta.questions_left)))
  );
}

function isChatState(value: unknown): value is ChatState {
  if (!isRecord(value)) return false;
  const { blocked } = value;
  return (
    Array.isArray(value.turns) &&
    value.turns.every(isTurn) &&
    Array.isArray(value.history) &&
    (value.signature === null || isString(value.signature)) &&
    (value.questionsLeft === null || isNumber(value.questionsLeft)) &&
    (blocked === null || (isRecord(blocked) && isString(blocked.code) && isString(blocked.message)))
  );
}

/** The saved conversation, or null when there is none or it doesn't fit this version. The
 *  caller restores it through the reducer, which marks a turn saved mid-answer as cut off. */
export function loadChat(storage: KeyValueStorage | null): ChatState | null {
  if (storage === null) return null;
  let text: string | null;
  try {
    text = storage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
  if (!text) return null;
  let saved: unknown;
  try {
    saved = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isRecord(saved) || saved.v !== 1 || !isChatState(saved.state)) return null;
  return saved.state;
}

// ---- The landing page's hand-over ----

/** Leaves a question for /chat to send once (the landing page's Ask box). False when storage
 *  is unavailable; the caller can then link to /chat?q=… instead, which only prefills. */
export function savePending(storage: KeyValueStorage | null, question: string): boolean {
  const text = question.trim();
  if (storage === null || text === "") return false;
  try {
    storage.setItem(PENDING_KEY, text.slice(0, MAX_MESSAGE_CHARS));
    return true;
  } catch {
    return false;
  }
}

/** Reads and deletes the pending question. If it can't be deleted, it isn't returned either,
 *  so a broken storage can never send the same question on every visit. */
export function takePending(storage: KeyValueStorage | null): string | null {
  if (storage === null) return null;
  let text: string | null;
  try {
    text = storage.getItem(PENDING_KEY);
    if (text === null) return null;
    storage.removeItem(PENDING_KEY);
  } catch {
    return null;
  }
  const question = text.trim();
  return question === "" ? null : question.slice(0, MAX_MESSAGE_CHARS);
}
