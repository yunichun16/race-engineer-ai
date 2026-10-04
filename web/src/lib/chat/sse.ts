// Reading the chat's server-sent events from a fetch body.
//
// EventSource can't be used: it only sends GET, and the chat is a POST with a JSON body. So this
// follows the WHATWG event-stream rules itself
// (https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation):
// - the bytes are UTF-8, decoded with { stream: true } so a character split across two chunks
//   comes out whole;
// - one leading byte order mark is dropped;
// - a line ends with \r\n, \n or \r, and a \r at the end of one chunk followed by \n at the
//   start of the next is one line end, not two;
// - "field:value" loses one space after the colon; a line with no colon is a field with an
//   empty value;
// - several data lines join with \n;
// - comments (": ping", the server's keep-alive every 15 s), id and retry are ignored;
// - a blank line dispatches the event, named "message" when no event line came; an event with
//   no data line is not dispatched;
// - an event the stream ends in the middle of is dropped.
//
// parseChatEvent then turns one event into a typed ChatEvent, or null for an event this client
// doesn't know (so a newer server can add events) or one whose JSON doesn't fit.

import {
  CHAT_EVENT_TYPES,
  type ChartPayload,
  type ChatEvent,
  type ChatEventType,
  type ChatQuota,
  type ChatUsage,
} from "./types.ts";

/** One dispatched event: its name and its data lines joined with \n. */
export interface SSEMessage {
  event: string;
  data: string;
}

/** The parser without the stream around it: feed it chunks, get the events they complete. */
export interface SSEDecoder {
  feed(chunk: Uint8Array): SSEMessage[];
  /** The stream ended: anything unfinished is dropped. */
  end(): SSEMessage[];
}

const LF = 10;
const CR = 13;
const COLON = 58;
const SPACE = 32;
const BOM = 0xfeff;

export function createSSEDecoder(): SSEDecoder {
  // ignoreBOM keeps the mark in the text, so exactly one is removed here, as the spec says.
  const decoder = new TextDecoder("utf-8", { ignoreBOM: true });
  let bomChecked = false;
  let partial = ""; // the start of a line whose end hasn't arrived; never holds \r or \n
  let skipLF = false; // the last chunk ended in \r, so a \n opening the next one belongs to it
  let eventType = "";
  let data = ""; // each data line followed by \n, as the spec's data buffer

  function dispatch(out: SSEMessage[]): void {
    if (data !== "") {
      out.push({ event: eventType === "" ? "message" : eventType, data: data.slice(0, -1) });
    }
    eventType = "";
    data = "";
  }

  function line(text: string, out: SSEMessage[]): void {
    if (text === "") {
      dispatch(out);
      return;
    }
    if (text.charCodeAt(0) === COLON) return; // a comment
    const colon = text.indexOf(":");
    let field = text;
    let value = "";
    if (colon !== -1) {
      field = text.slice(0, colon);
      value = text.slice(colon + 1);
      if (value.charCodeAt(0) === SPACE) value = value.slice(1);
    }
    if (field === "event") eventType = value;
    else if (field === "data") data += value + "\n";
    // "id", "retry" and unknown fields are ignored: the chat never reconnects.
  }

  function push(text: string, out: SSEMessage[]): void {
    let pos = 0;
    const n = text.length;
    if (skipLF && n > 0) {
      skipLF = false;
      if (text.charCodeAt(0) === LF) pos = 1;
    }
    while (pos < n) {
      // Only the new text is searched: `partial` has no line ends in it.
      let end = pos;
      while (end < n) {
        const c = text.charCodeAt(end);
        if (c === LF || c === CR) break;
        end++;
      }
      if (end === n) {
        partial += text.slice(pos);
        return;
      }
      const whole = partial + text.slice(pos, end);
      partial = "";
      if (text.charCodeAt(end) === CR) {
        if (end + 1 < n) {
          pos = text.charCodeAt(end + 1) === LF ? end + 2 : end + 1;
        } else {
          pos = n;
          skipLF = true;
        }
      } else {
        pos = end + 1;
      }
      line(whole, out);
    }
  }

  function decoded(text: string, out: SSEMessage[]): SSEMessage[] {
    if (!bomChecked && text.length > 0) {
      bomChecked = true;
      if (text.charCodeAt(0) === BOM) text = text.slice(1);
    }
    push(text, out);
    return out;
  }

  return {
    feed(chunk) {
      return decoded(decoder.decode(chunk, { stream: true }), []);
    },
    end() {
      // Flushing turns a character cut off by the end into U+FFFD; it can't end a line, so
      // whatever it joins is dropped with the unfinished event below.
      const out = decoded(decoder.decode(), []);
      partial = "";
      skipLF = false;
      eventType = "";
      data = "";
      return out;
    },
  };
}

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
}

/**
 * The events in a response body, in order. Aborting `signal` cancels the body and throws the
 * signal's reason; breaking out of the loop early also cancels the body, which closes the
 * connection, and the server then stops answering.
 */
export async function* parseSSE(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SSEMessage> {
  const reader = body.getReader();
  const decoder = createSSEDecoder();
  let finished = false;
  const onAbort = (): void => {
    reader.cancel().catch(() => {}); // wakes a pending read
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    if (signal?.aborted) throw abortError(signal);
    while (true) {
      const { done, value } = await reader.read();
      if (signal?.aborted) throw abortError(signal);
      if (done) {
        finished = true;
        yield* decoder.end();
        return;
      }
      for (const message of decoder.feed(value)) {
        if (signal?.aborted) throw abortError(signal);
        yield message;
      }
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    if (!finished) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

// ---- Typed chat events ----

const KNOWN: ReadonlySet<string> = new Set<string>(CHAT_EVENT_TYPES);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function chartOf(value: unknown): ChartPayload | null {
  if (!isRecord(value) || !isString(value.bundle) || !("data" in value)) return null;
  return {
    bundle: value.bundle,
    resource_uri: isString(value.resource_uri) ? value.resource_uri : "",
    data: value.data,
  };
}

/** The visitor's quota after a question; null when absent or malformed (limits off). */
function quotaOf(value: unknown): ChatQuota | null {
  if (!isRecord(value) || !isNumber(value.hour_left) || !isNumber(value.day_left)) return null;
  return { hour_left: value.hour_left, day_left: value.day_left };
}

function usageOf(value: unknown): ChatUsage {
  const usage = isRecord(value) ? value : {};
  const count = (key: string): number => {
    const n = usage[key];
    return isNumber(n) ? n : 0;
  };
  return {
    input: count("input"),
    output: count("output"),
    cache_read: count("cache_read"),
    cache_write: count("cache_write"),
  };
}

/** Whether `name` is an event this client knows; parseChatEvent returning null for a known
 *  name means its data didn't fit. */
export function isChatEventType(name: string): name is ChatEventType {
  return KNOWN.has(name);
}

/**
 * One SSE event as a ChatEvent, or null when the name is unknown or the data doesn't fit.
 *
 * The checks are strict only where the reducer depends on a field (the ids, the text, the
 * history and signature in `done`). Fields that are only shown or logged get a neutral default,
 * so a small server change can't drop a whole `done` or `tool_result`.
 */
export function parseChatEvent(event: string, data: string): ChatEvent | null {
  if (!KNOWN.has(event)) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  if (!isRecord(raw)) return null;
  switch (event) {
    case "status":
      return isString(raw.text) ? { type: "status", text: raw.text } : null;
    case "text":
      return isString(raw.delta) ? { type: "text", delta: raw.delta } : null;
    case "tool_call":
      if (!isString(raw.id) || !isString(raw.name)) return null;
      return {
        type: "tool_call",
        id: raw.id,
        name: raw.name,
        input: isRecord(raw.input) ? raw.input : {},
      };
    case "tool_result":
      if (!isString(raw.id) || !isString(raw.name)) return null;
      return {
        type: "tool_result",
        id: raw.id,
        name: raw.name,
        is_error: raw.is_error === true,
        summary: isString(raw.summary) ? raw.summary : "",
        chart: raw.is_error === true ? null : chartOf(raw.chart),
      };
    case "retry":
      return { type: "retry", message: isString(raw.message) ? raw.message : "" };
    case "refusal":
      return {
        type: "refusal",
        message: isString(raw.message) ? raw.message : "",
        category: isString(raw.category) ? raw.category : null,
      };
    case "error":
      return {
        type: "error",
        code: isString(raw.code) && raw.code !== "" ? raw.code : "unknown",
        message: isString(raw.message) ? raw.message : "",
      };
    case "done":
      if (!Array.isArray(raw.history) || !isString(raw.signature)) return null;
      if (!isNumber(raw.questions_left)) return null;
      return {
        type: "done",
        history: raw.history,
        signature: raw.signature,
        questions_used: isNumber(raw.questions_used) ? raw.questions_used : 0,
        questions_left: raw.questions_left,
        stop_reason: isString(raw.stop_reason) ? raw.stop_reason : null,
        model: isString(raw.model) ? raw.model : "",
        usage: usageOf(raw.usage),
        cost_usd: isNumber(raw.cost_usd) ? raw.cost_usd : null,
        request_id: isString(raw.request_id) ? raw.request_id : "",
        quota: quotaOf(raw.quota),
      };
    default:
      return null;
  }
}

/** parseSSE and parseChatEvent together: the chat events in a body, unknown ones skipped. */
export async function* parseChatStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  for await (const { event, data } of parseSSE(body, signal)) {
    const parsed = parseChatEvent(event, data);
    if (parsed) yield parsed;
  }
}
