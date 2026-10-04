// Sending one chat question: POST /api/chat, then the answer's events as they stream.
//
// The request carries only `content-type: application/json`, the one request header the API's
// CORS rules allow, so the browser's preflight passes. Everything the server checks before it
// starts streaming (routes/chat.py: chat off, history too large, tampered or expired history,
// the question limit, busy, a bad body) comes back as a plain JSON error body with a non-2xx
// status, and is thrown here as an ApiError before any event is yielded. Once events flow, a
// broken connection or an abort surfaces as whatever the body's reader throws (an AbortError
// for an abort), never as an ApiError, so the caller can tell "never started" from "cut off".

import { API_URL } from "../env.ts";
import { ApiError, errorFromResponse, networkError } from "../api/client.ts";
import { parseChatStream } from "./sse.ts";
import type { ChatEvent, ChatRequest } from "./types.ts";

/** Used when no apiUrl is passed: NEXT_PUBLIC_API_URL, else the local FastAPI. */
export const DEFAULT_API_URL = API_URL;

export interface StreamChatOptions {
  signal?: AbortSignal;
  fetch?: typeof fetch;
  apiUrl?: string;
}

export async function* streamChat(
  request: ChatRequest,
  options: StreamChatOptions = {},
): AsyncGenerator<ChatEvent> {
  const { signal } = options;
  // Called as a plain function, never as options.fetch(...): a browser's fetch throws
  // "Illegal invocation" when `this` is some other object.
  const send = options.fetch ?? globalThis.fetch;
  const base = (options.apiUrl ?? DEFAULT_API_URL).replace(/\/+$/, "");

  let response: Response;
  try {
    response = await send(`${base}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    throw networkError(error, signal); // "unreachable", or "aborted" when the signal fired
  }

  if (!response.ok) {
    // The pre-stream refusals are the API's ErrorBody, read as every other API error is
    // (lib/api/client.ts). An abort while that body was read is still an abort.
    const error = await errorFromResponse(response);
    throw signal?.aborted ? networkError(null, signal) : error;
  }

  // content-type is a CORS-safelisted response header, so the page can always read it.
  const type = (response.headers.get("content-type") ?? "").toLowerCase();
  if (!type.startsWith("text/event-stream") || response.body === null) {
    await response.body?.cancel().catch(() => {});
    throw new ApiError(response.status, "bad_payload", "The chat server didn't answer with a stream.");
  }

  yield* parseChatStream(response.body, signal);
}
