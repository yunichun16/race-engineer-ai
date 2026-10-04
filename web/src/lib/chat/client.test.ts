import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../api/client.ts";
import { streamChat } from "./client.ts";
import { bytes, collect, fixtureBytes, splitAt, streamOf } from "./test-support.ts";
import type { ChatEvent, ChatRequest } from "./types.ts";

const REQUEST: ChatRequest = { message: "Who made the biggest mistakes?", history: [], signature: null };

interface Call {
  url: string;
  init: RequestInit;
}

/** A fetch that records its calls and answers with `respond`. */
function fakeFetch(respond: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const call = { url: String(input), init: init ?? {} };
    calls.push(call);
    return respond(call);
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

function sse(body: ReadableStream<Uint8Array>, status = 200): Response {
  return new Response(body, { status, headers: { "content-type": "text/event-stream; charset=utf-8" } });
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

test("a 200 event stream gives the chat events, from a POST with only content-type", async () => {
  const data = fixtureBytes("basic");
  const { fetch, calls } = fakeFetch(() => sse(streamOf(splitAt(data, [7, 900, 901])).stream));
  const events = await collect(streamChat(REQUEST, { fetch, apiUrl: "http://127.0.0.1:8000/" }));
  assert.deepEqual(
    events.map((e) => e.type),
    ["status", "text", "tool_call", "tool_result", "text", "text", "text", "done"],
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8000/api/chat");
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(calls[0].init.headers, { "content-type": "application/json" });
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), REQUEST);
});

test("the default API URL is the local FastAPI", async () => {
  const { fetch, calls } = fakeFetch(() => sse(streamOf([fixtureBytes("refusal")]).stream));
  await collect(streamChat(REQUEST, { fetch }));
  assert.equal(calls[0].url, "http://127.0.0.1:8000/api/chat");
});

test("a 409 JSON error gives ApiError('turn_limit') before any event", async () => {
  const message = "This conversation has reached its limit of 8 questions; start a new one.";
  const { fetch } = fakeFetch(() => json(409, { error: { code: "turn_limit", message } }));
  const seen: ChatEvent[] = [];
  await assert.rejects(
    (async () => {
      for await (const event of streamChat(REQUEST, { fetch })) seen.push(event);
    })(),
    (error: unknown) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 409);
      assert.equal(error.code, "turn_limit");
      assert.equal(error.message, message);
      assert.equal(error.tool, undefined);
      return true;
    },
  );
  assert.deepEqual(seen, []);
});

test("every pre-stream refusal keeps its status and code", async () => {
  const cases: [number, string][] = [
    [503, "chat_disabled"],
    [413, "history_too_large"],
    [400, "invalid_history"],
    [409, "conversation_expired"],
    [503, "busy"],
    [422, "invalid_request"],
  ];
  for (const [status, code] of cases) {
    const { fetch } = fakeFetch(() => json(status, { error: { code, message: `m ${code}` } }));
    await assert.rejects(collect(streamChat(REQUEST, { fetch })), { name: "ApiError", status, code, message: `m ${code}` });
  }
  // The app's validation errors carry "tool": null (seen in M5's chat probe).
  const message = "Invalid request: message: String should have at most 2000 characters.";
  const { fetch } = fakeFetch(() => json(422, { error: { code: "invalid_request", message, tool: null } }));
  await assert.rejects(collect(streamChat(REQUEST, { fetch })), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.deepEqual([error.status, error.code, error.message, error.tool], [422, "invalid_request", message, undefined]);
    return true;
  });
});

test("a non-JSON error gives http_<status> with the status text", async () => {
  const { fetch } = fakeFetch(() => new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" }));
  await assert.rejects(collect(streamChat(REQUEST, { fetch })), {
    name: "ApiError",
    status: 502,
    code: "http_502",
    message: "Bad Gateway",
  });
  const { fetch: other } = fakeFetch(() => json(500, { detail: "x" }));
  await assert.rejects(collect(streamChat(REQUEST, { fetch: other })), { code: "http_500", message: "HTTP 500" });
});

test("a 200 that isn't an event stream throws bad_payload and cancels the body", async () => {
  const probe = streamOf([bytes('{"ok": true}')], { hold: true });
  const { fetch } = fakeFetch(() => new Response(probe.stream, { status: 200, headers: { "content-type": "application/json" } }));
  await assert.rejects(collect(streamChat(REQUEST, { fetch })), { name: "ApiError", status: 200, code: "bad_payload" });
  assert.equal(probe.cancelled(), true);
});

test("a network error gives unreachable; an abort before the response gives aborted", async () => {
  const { fetch } = fakeFetch(() => {
    throw new TypeError("Failed to fetch");
  });
  await assert.rejects(collect(streamChat(REQUEST, { fetch })), { name: "ApiError", status: 0, code: "unreachable" });

  const controller = new AbortController();
  const { fetch: slow } = fakeFetch(
    (call) =>
      new Promise<Response>((_, reject) => {
        call.init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      }),
  );
  const pending = collect(streamChat(REQUEST, { fetch: slow, signal: controller.signal }));
  controller.abort();
  await assert.rejects(pending, { name: "ApiError", status: 0, code: "aborted" });
});

test("an abort mid-stream throws an AbortError, not an ApiError, and cancels the body", async () => {
  const probe = streamOf([bytes('event: text\ndata: {"delta": "Half"}\n\n')], { hold: true });
  const { fetch } = fakeFetch(() => sse(probe.stream));
  const controller = new AbortController();
  const seen: ChatEvent[] = [];
  await assert.rejects(
    (async () => {
      for await (const event of streamChat(REQUEST, { fetch, signal: controller.signal })) {
        seen.push(event);
        controller.abort();
      }
    })(),
    (error: unknown) => {
      assert.ok(!(error instanceof ApiError));
      assert.equal((error as Error).name, "AbortError");
      return true;
    },
  );
  assert.deepEqual(seen, [{ type: "text", delta: "Half" }]);
  assert.equal(probe.cancelled(), true);
});

test("a stream that ends without done just ends", async () => {
  const { fetch } = fakeFetch(() => sse(streamOf([fixtureBytes("no-done")]).stream));
  const events = await collect(streamChat(REQUEST, { fetch }));
  assert.deepEqual(events.map((e) => e.type), ["status", "text", "tool_call"]);
});
