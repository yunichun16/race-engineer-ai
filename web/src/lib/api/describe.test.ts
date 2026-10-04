/** describe(): the states catalogue's words (plan 6.6) for every error code, in both modes. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "./client.ts";
import { describe } from "./describe.ts";

const dev = { dev: true };
const prod = { dev: false };

test("invalid_input shows the server's message unchanged, with no Try again", () => {
  const message = "No processed event matches 'Nowhere'. Did you mean: Monaco Grand Prix, Mexico City Grand Prix?";
  for (const mode of [dev, prod]) {
    assert.deepEqual(describe(new ApiError(422, "invalid_input", message, "find_mistakes"), mode), {
      tone: "warn",
      title: "That didn't match the data",
      body: message,
      retry: false,
    });
  }
});

test("invalid_request says the request wasn't valid, with the server's message", () => {
  const message = "Invalid request: unknown parameter(s) zzz (parameters: event, driver, year, session, limit).";
  assert.deepEqual(describe(new ApiError(422, "invalid_request", message), prod), {
    tone: "warn",
    title: "That request wasn't valid",
    body: message,
    retry: false,
  });
});

test("results_unavailable shows the rebuild message in development, 'Try again later' otherwise", () => {
  const error = new ApiError(503, "results_unavailable", "Driving-style data hasn't been built yet: run `uv run race-engineer-infer style`.");
  const title = "The analysis results on the server need rebuilding.";
  assert.deepEqual(describe(error, dev), { tone: "error", title, body: error.message, retry: false });
  assert.deepEqual(describe(error, prod), { tone: "error", title, body: "Try again later.", retry: false });
});

test("internal_error names the request id from the server's message, when there is one", () => {
  const withId = new ApiError(500, "internal_error", "Internal error; see the server log (request 1a2b3c4d5e6f).");
  assert.deepEqual(describe(withId, prod), {
    tone: "error",
    title: "The server hit an error (request 1a2b3c4d5e6f).",
    retry: true,
  });
  assert.equal(describe(new ApiError(500, "internal_error", "Internal error."), prod).title, "The server hit an error.");
});

test("bad_payload: the answer couldn't be drawn (the page adds the text version)", () => {
  assert.deepEqual(describe(new ApiError(200, "bad_payload", "x", "compare_laps"), prod), {
    tone: "error",
    title: "The server's answer couldn't be drawn.",
    retry: true,
  });
});

test("unreachable: can't reach the server, and what still works", () => {
  assert.deepEqual(describe(new ApiError(0, "unreachable", "x"), prod), {
    tone: "error",
    title: "Can't reach the analysis server.",
    body: "The findings and the example on the home page still work.",
    retry: true,
  });
});

test("aborted and the page's own faults", () => {
  assert.equal(describe(new ApiError(0, "aborted", "x"), prod).title, "The request was cancelled.");
  const bug = describe(new TypeError("Cannot read properties of undefined"), prod);
  assert.deepEqual(bug, { tone: "error", title: "Something went wrong on this page.", retry: true });
});

test("rate_limited: too many requests from this connection, with the wait in seconds when sent", () => {
  const limited = new ApiError(429, "rate_limited", "Too many requests.", "find_mistakes", { retryAfterS: 41.2 });
  for (const mode of [dev, prod]) {
    assert.deepEqual(describe(limited, mode), {
      tone: "warn",
      title: "Too many requests from this connection; try again in 42 s.",
      retry: true,
    });
  }
  assert.equal(
    describe(new ApiError(429, "rate_limited", "x", undefined, { retryAfterS: 0 }), prod).title,
    "Too many requests from this connection; try again in 1 s.",
  );
  assert.equal(
    describe(new ApiError(429, "rate_limited", "x"), prod).title,
    "Too many requests from this connection; try again shortly.",
  );
  // A chat refusal handed over as a plain object keeps its wait.
  assert.equal(
    describe({ status: 429, code: "rate_limited", message: "x", retryAfterS: 5 }, prod).title,
    "Too many requests from this connection; try again in 5 s.",
  );
});

test("busy: the heavy-analysis queue is full, worth trying again", () => {
  assert.deepEqual(describe(new ApiError(503, "busy", "Too many analyses are waiting.", "explain_corner"), prod), {
    tone: "error",
    title: "The server is busy with other analyses; try again in a moment.",
    retry: true,
  });
});

test("a code the table doesn't know shows the server's message; Try again only where it can help", () => {
  const capped = describe(new ApiError(503, "daily_cap", "The chat has used today's budget."), prod);
  assert.deepEqual(capped, {
    tone: "error",
    title: "The analysis server couldn't answer that.",
    body: "The chat has used today's budget.",
    retry: true,
  });
  assert.equal(describe(new ApiError(502, "http_502", "Bad Gateway"), prod).retry, true);
  assert.equal(describe(new ApiError(404, "unknown_tool", "No tool 'x'."), prod).retry, false);
  assert.equal(describe(new ApiError(404, "not_found", "Not Found"), prod).body, "Not Found");
  assert.equal(describe(new ApiError(405, "http_405", ""), prod).body, undefined);
});

test("the chat's pre-stream errors (plain objects with the fields) are read too", () => {
  const copy = describe({ status: 422, code: "invalid_input", message: "AAA didn't drive lap 99." }, prod);
  assert.equal(copy.title, "That didn't match the data");
  assert.equal(copy.body, "AAA didn't drive lap 99.");
});
