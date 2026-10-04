/**
 * The words a page shows for a failed API call (plan section 6.6, the states catalogue), by
 * error code. Messages the server wrote to be read (`invalid_input`, and `results_unavailable`
 * in development) are shown unchanged: they name the drivers in the session, suggest events, or
 * say how to rebuild the data. Codes this table doesn't know show the server's message, so a new
 * code never reads as a blank error. M7 adds the REST tools' guardrails: `rate_limited` (too many
 * requests from one connection, with the wait) and `busy` (the heavy-analysis queue is full).
 * The chat's own refusals are worded by the chat (lib/chat/reducer.ts, unavailableCopy).
 */

import { IS_DEV } from "../env.ts";
import { toApiError } from "./client.ts";

export interface ErrorCopy {
  /** "warn" for a request the reader can change; "error" for a failure on the way or the server. */
  tone: "warn" | "error";
  title: string;
  body?: string;
  /** Whether trying the same request again can help (pages show Try again only then). */
  retry: boolean;
}

/** The request id in the API's "Internal error; see the server log (request 1a2b3c4d5e6f)." */
function requestId(message: string): string | null {
  return /\(request ([0-9a-f]+)\)/i.exec(message)?.[1] ?? null;
}

export function describe(error: unknown, opts: { dev?: boolean } = {}): ErrorCopy {
  const { dev = IS_DEV } = opts;
  const e = toApiError(error);
  switch (e.code) {
    case "invalid_input":
      return { tone: "warn", title: "That didn't match the data", body: e.message, retry: false };
    case "invalid_request":
      // The server's message already starts "Invalid request: ...", so it goes in the body.
      return { tone: "warn", title: "That request wasn't valid", body: e.message, retry: false };
    case "results_unavailable":
      return {
        tone: "error",
        title: "The analysis results on the server need rebuilding.",
        body: dev ? e.message : "Try again later.",
        retry: false,
      };
    case "internal_error": {
      const id = requestId(e.message);
      return { tone: "error", title: id ? `The server hit an error (request ${id}).` : "The server hit an error.", retry: true };
    }
    case "bad_payload":
      return { tone: "error", title: "The server's answer couldn't be drawn.", retry: true };
    case "unreachable":
      return {
        tone: "error",
        title: "Can't reach the analysis server.",
        body: "The findings and the example on the home page still work.",
        retry: true,
      };
    case "aborted":
      return { tone: "warn", title: "The request was cancelled.", retry: true };
    case "client_error": // a bug in this page, not the server's doing (toApiError)
      return { tone: "error", title: "Something went wrong on this page.", retry: true };
    case "rate_limited": {
      // api/limits/http.py: per connection, a minute's window, so the wait is seconds.
      const wait = e.retryAfterS === null ? null : Math.max(1, Math.ceil(e.retryAfterS));
      const title =
        wait === null
          ? "Too many requests from this connection; try again shortly."
          : `Too many requests from this connection; try again in ${wait} s.`;
      return { tone: "warn", title, retry: true };
    }
    case "busy":
      // More heavy analyses are already waiting than the server queues (tools/registry.py).
      return { tone: "error", title: "The server is busy with other analyses; try again in a moment.", retry: true };
    default: {
      const retry = e.status === 0 || e.status === 408 || e.status === 429 || e.status >= 500;
      return { tone: "error", title: "The analysis server couldn't answer that.", body: e.message || undefined, retry };
    }
  }
}
