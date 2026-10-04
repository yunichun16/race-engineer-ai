/**
 * The API client with a stand-in fetch: query strings, each way a call can fail (an ErrorBody,
 * another non-2xx answer, fetch throwing, an abort, a body that isn't the expected JSON) and the
 * chart guards on tool answers, against the dev host's sample payloads (read with fs).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ApiError, apiUrl, buildQuery, callTool, getJSON, toApiError, TOOL_GUARDS } from "./client.ts";
import { errorBody, fakeFetch, hangingFetch, json } from "./test-support.ts";
import type { ToolName } from "./types.ts";

const BASE = "http://api.test";

function sample(name: string): { summary: string; data: unknown } {
  const raw = JSON.parse(readFileSync(new URL(`../../mcp-app/sample-${name}.json`, import.meta.url), "utf8")) as {
    content: { text: string }[];
    structuredContent: unknown;
  };
  return { summary: raw.content[0].text, data: raw.structuredContent };
}

function toolBody(tool: string, summary: string, data: unknown) {
  return { tool, summary, data, chart: null };
}

const FIND_SESSION_DATA = {
  match: {
    key: "2025_16_Q",
    year: 2025,
    round: 16,
    session_code: "Q",
    session_name: "Qualifying",
    event: "Italian Grand Prix",
    location: "Monza",
    date: "2025-09-06",
    scored: true,
    style: true,
  },
  candidates: [],
  weekend: [],
  coverage: { first: "2022 Bahrain Grand Prix (Q)", last: "2026 Azerbaijan Grand Prix (R)", newest_season: 2026, sessions: 263 },
};

// --- Query strings --------------------------------------------------------------------------------

test("buildQuery drops undefined, null and empty values, sorts the keys and never adds one", () => {
  assert.equal(
    buildQuery({ year: 2025, event: "Italian Grand Prix", driver: undefined, session: null, limit: "" }),
    "event=Italian+Grand+Prix&year=2025",
  );
  assert.equal(buildQuery({ lap: 0, corner: "9a" }), "corner=9a&lap=0"); // 0 is a value
  assert.equal(buildQuery({}), "");
  assert.equal(buildQuery({ driver: undefined }), "");
});

test("equal parameters in any order give the same query, so the same URL and cache entry", () => {
  const a = buildQuery({ event: "São Paulo Grand Prix", year: 2024, session: "SQ", driver: "NOR" });
  const b = buildQuery({ driver: "NOR", session: "SQ", year: 2024, event: "São Paulo Grand Prix" });
  assert.equal(a, b);
  assert.equal(new URLSearchParams(a).get("event"), "São Paulo Grand Prix");
  assert.equal(new URLSearchParams(buildQuery({ event: "A&B=C" })).get("event"), "A&B=C");
});

test("apiUrl joins the base, the path and the query, with no '?' when there is no query", () => {
  assert.equal(apiUrl("/api/health", {}, BASE), `${BASE}/api/health`);
  assert.equal(apiUrl("/api/catalog/styles", { year: 2025 }, BASE), `${BASE}/api/catalog/styles?year=2025`);
  assert.equal(apiUrl("/api/health"), "http://127.0.0.1:8000/api/health"); // NEXT_PUBLIC_API_URL's default
});

// --- getJSON: answers and errors -----------------------------------------------------------------

test("getJSON calls fetch once, as a plain function, with the signal and no custom header", async () => {
  const fetch = fakeFetch(() => json({ ok: true }));
  const controller = new AbortController();
  const body = await getJSON<{ ok: boolean }>("/api/x", { b: 2, a: 1 }, { fetch, baseUrl: BASE, signal: controller.signal });
  assert.deepEqual(body, { ok: true });
  assert.equal(fetch.calls.length, 1);
  const [call] = fetch.calls;
  assert.equal(call.url, `${BASE}/api/x?a=1&b=2`);
  assert.equal(call.self, undefined);
  assert.deepEqual(Object.keys(call.init ?? {}), ["signal"]);
  assert.equal(call.init?.signal, controller.signal);
});

test("a non-2xx ErrorBody gives its status, code, message and tool", async () => {
  const fetch = fakeFetch(() =>
    json(errorBody("invalid_input", "LEC didn't drive lap 99 of the 2025 Italian Grand Prix race.", "explain_corner"), 422),
  );
  await assert.rejects(getJSON("/api/tools/explain_corner", {}, { fetch, baseUrl: BASE }), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.name, "ApiError");
    assert.equal(error.status, 422);
    assert.equal(error.code, "invalid_input");
    assert.equal(error.message, "LEC didn't drive lap 99 of the 2025 Italian Grand Prix race.");
    assert.equal(error.tool, "explain_corner");
    return true;
  });
});

test("a limit's refusal keeps retry_after_s and limit; Retry-After is the fallback; others have none", async () => {
  const body = {
    error: {
      code: "rate_limited",
      message: "That's 10 questions in the last hour from this connection. You can ask again in 23 minutes.",
      retry_after_s: 1380,
      limit: "hour",
    },
  };
  await assert.rejects(getJSON("/api/chat", {}, { fetch: fakeFetch(() => json(body, 429)), baseUrl: BASE }), {
    status: 429,
    code: "rate_limited",
    retryAfterS: 1380,
    limit: "hour",
  });
  const paused = { error: { code: "chat_paused", message: "Paused.", retry_after_s: null } };
  await assert.rejects(getJSON("/api/chat", {}, { fetch: fakeFetch(() => json(paused, 503)), baseUrl: BASE }), {
    code: "chat_paused",
    retryAfterS: null,
    limit: undefined,
  });
  const header = fakeFetch(
    () =>
      new Response(JSON.stringify(errorBody("rate_limited", "Slow down.", "find_mistakes")), {
        status: 429,
        headers: { "content-type": "application/json", "retry-after": "17" },
      }),
  );
  await assert.rejects(getJSON("/api/tools/find_mistakes", {}, { fetch: header, baseUrl: BASE }), { retryAfterS: 17 });
  const junk = { error: { code: "rate_limited", message: "x", retry_after_s: "soon", limit: 3 } };
  await assert.rejects(getJSON("/api/chat", {}, { fetch: fakeFetch(() => json(junk, 429)), baseUrl: BASE }), {
    retryAfterS: null,
    limit: undefined,
  });
  assert.equal(new ApiError(500, "internal_error", "x").retryAfterS, null);
  // A plain object with the fields (the chat's pre-stream errors) keeps them too.
  assert.equal(toApiError({ status: 429, code: "rate_limited", message: "x", retryAfterS: 9, limit: "day" }).limit, "day");
});

test("an ErrorBody whose tool is null gives no tool", async () => {
  const fetch = fakeFetch(() => json(errorBody("not_found", "Not Found"), 404, "Not Found"));
  await assert.rejects(getJSON("/api/nothing", {}, { fetch, baseUrl: BASE }), {
    name: "ApiError",
    status: 404,
    code: "not_found",
    message: "Not Found",
    tool: undefined,
  });
});

test("the API's 500 keeps its code and message (with the request id)", async () => {
  const message = "Internal error; see the server log (request 1a2b3c4d5e6f).";
  const fetch = fakeFetch(() => json(errorBody("internal_error", message, "find_mistakes"), 500));
  await assert.rejects(getJSON("/api/tools/find_mistakes", {}, { fetch, baseUrl: BASE }), {
    status: 500,
    code: "internal_error",
    message,
    tool: "find_mistakes",
  });
});

test("any other non-2xx answer gives http_<status> with the status text", async () => {
  const html = fakeFetch(() => new Response("<html>Bad gateway</html>", { status: 502, statusText: "Bad Gateway" }));
  await assert.rejects(getJSON("/api/health", {}, { fetch: html, baseUrl: BASE }), {
    name: "ApiError",
    status: 502,
    code: "http_502",
    message: "Bad Gateway",
  });
  const notErrorBody = fakeFetch(() => json({ detail: "nope" }, 500));
  await assert.rejects(getJSON("/api/health", {}, { fetch: notErrorBody, baseUrl: BASE }), {
    status: 500,
    code: "http_500",
    message: "HTTP 500",
  });
  const halfErrorBody = fakeFetch(() => json({ error: { code: 7, message: "x" } }, 418, "I'm a teapot"));
  await assert.rejects(getJSON("/api/health", {}, { fetch: halfErrorBody, baseUrl: BASE }), { code: "http_418" });
});

test("fetch throwing a TypeError (server stopped, CORS refused) gives unreachable, status 0", async () => {
  const fetch = fakeFetch(() => {
    throw new TypeError("Failed to fetch");
  });
  await assert.rejects(getJSON("/api/health", {}, { fetch, baseUrl: BASE }), {
    name: "ApiError",
    status: 0,
    code: "unreachable",
    message: "Can't reach the analysis server.",
  });
});

test("an abort gives aborted, not unreachable, whether before or during the request", async () => {
  const before = new AbortController();
  before.abort();
  await assert.rejects(getJSON("/api/health", {}, { fetch: hangingFetch(), baseUrl: BASE, signal: before.signal }), {
    status: 0,
    code: "aborted",
  });

  const during = new AbortController();
  const pending = getJSON("/api/health", {}, { fetch: hangingFetch(), baseUrl: BASE, signal: during.signal });
  during.abort();
  await assert.rejects(pending, { status: 0, code: "aborted" });

  // A fetch that rejects with a TypeError after the abort still reads as aborted.
  const late = new AbortController();
  const typeErrorFetch = fakeFetch(async () => {
    late.abort();
    throw new TypeError("network error");
  });
  await assert.rejects(getJSON("/api/health", {}, { fetch: typeErrorFetch, baseUrl: BASE, signal: late.signal }), {
    code: "aborted",
  });
});

test("a body that breaks off gives unreachable, or aborted when the signal fired", async () => {
  const broken = (onRead: () => void) =>
    fakeFetch(() => {
      const response = json({});
      Object.defineProperty(response, "text", {
        value: async () => {
          onRead();
          throw new TypeError("network error");
        },
      });
      return response;
    });
  await assert.rejects(getJSON("/api/x", {}, { fetch: broken(() => {}), baseUrl: BASE }), { code: "unreachable" });
  const controller = new AbortController();
  await assert.rejects(
    getJSON("/api/x", {}, { fetch: broken(() => controller.abort()), baseUrl: BASE, signal: controller.signal }),
    { code: "aborted" },
  );
});

test("an abort while an error answer's body is read gives aborted, not the error", async () => {
  const controller = new AbortController();
  const fetch = fakeFetch(() => {
    const response = json(errorBody("internal_error", "Internal error; see the server log (request 1a2b3c4d5e6f)."), 500);
    Object.defineProperty(response, "text", {
      value: async () => {
        controller.abort();
        throw new DOMException("The operation was aborted.", "AbortError");
      },
    });
    return response;
  });
  await assert.rejects(getJSON("/api/x", {}, { fetch, baseUrl: BASE, signal: controller.signal }), {
    status: 0,
    code: "aborted",
  });
});

test("a 2xx answer that isn't JSON gives bad_payload with its status", async () => {
  const fetch = fakeFetch(() => new Response("<!doctype html><p>Not the API</p>", { status: 200 }));
  await assert.rejects(getJSON("/api/health", {}, { fetch, baseUrl: BASE }), {
    name: "ApiError",
    status: 200,
    code: "bad_payload",
  });
});

// --- callTool and the chart guards ------------------------------------------------------------

test("callTool asks GET /api/tools/<name> with the parameters as a sorted query", async () => {
  const { summary, data } = sample("find-mistakes");
  const fetch = fakeFetch(() => json(toolBody("find_mistakes", summary, data)));
  await callTool(
    "find_mistakes",
    { event: "Italian Grand Prix", driver: undefined, year: 2025, session: "R", limit: 25 },
    { fetch, baseUrl: BASE },
  );
  assert.equal(fetch.calls[0].url, `${BASE}/api/tools/find_mistakes?event=Italian+Grand+Prix&limit=25&session=R&year=2025`);
});

test("each chart tool's sample payload passes its guard and comes back with the summary", async () => {
  const cases: [ToolName, string][] = [
    ["get_race_summary", "race-summary"],
    ["find_mistakes", "find-mistakes"],
    ["explain_corner", "explain-corner"],
    ["compare_laps", "compare-laps"],
    ["compare_driving_styles", "compare-styles"],
  ];
  for (const [tool, file] of cases) {
    const { summary, data } = sample(file);
    const fetch = fakeFetch(() => json(toolBody(tool, summary, data)));
    const result = await callTool(tool, {} as never, { fetch, baseUrl: BASE });
    assert.equal(result.summary, summary, tool);
    // Compared after the same JSON round trip the server's answer takes (-0 arrives as 0).
    assert.deepEqual(result.data, JSON.parse(JSON.stringify(data)), tool);
    assert.deepEqual(Object.keys(result).sort(), ["data", "summary"], tool);
  }
});

test("a payload its chart's guard rejects gives bad_payload, naming the tool", async () => {
  const laps = sample("compare-laps");
  const wrong = fakeFetch(() => json(toolBody("find_mistakes", laps.summary, laps.data)));
  await assert.rejects(callTool("find_mistakes", { event: "Monza" }, { fetch: wrong, baseUrl: BASE }), {
    name: "ApiError",
    status: 200,
    code: "bad_payload",
    tool: "find_mistakes",
    message: "The server's answer couldn't be drawn.",
  });
  const empty = fakeFetch(() => json(toolBody("explain_corner", "text only", null)));
  await assert.rejects(
    callTool("explain_corner", { event: "Monza", driver: "LEC", lap: 10, corner: "9a" }, { fetch: empty, baseUrl: BASE }),
    { code: "bad_payload", tool: "explain_corner" },
  );
});

test("a body that isn't a ToolResponse gives bad_payload", async () => {
  const { data } = sample("race-summary");
  for (const body of [{ data }, { summary: 3, data }, [], "text", null]) {
    const fetch = fakeFetch(() => json(body));
    await assert.rejects(callTool("get_race_summary", { event: "Baku" }, { fetch, baseUrl: BASE }), {
      code: "bad_payload",
    });
  }
});

test("the text tools: find_session's match passes its check, list_sessions has no data", async () => {
  const found = fakeFetch(() => json(toolBody("find_session", "2025 Italian Grand Prix, Qualifying…", FIND_SESSION_DATA)));
  const result = await callTool("find_session", { event: "Monza", year: 2025 }, { fetch: found, baseUrl: BASE });
  assert.equal(result.data.match?.key, "2025_16_Q");
  assert.equal(found.calls[0].url, `${BASE}/api/tools/find_session?event=Monza&year=2025`);

  const junk = fakeFetch(() => json(toolBody("find_session", "s", { match: "Monza" })));
  await assert.rejects(callTool("find_session", { recent: 0, session: "R" }, { fetch: junk, baseUrl: BASE }), {
    code: "bad_payload",
  });

  const listed = fakeFetch(() => json(toolBody("list_sessions", "40 processed sessions in 2026.", null)));
  assert.deepEqual(await callTool("list_sessions", { year: 2026 }, { fetch: listed, baseUrl: BASE }), {
    summary: "40 processed sessions in 2026.",
    data: null,
  });
});

test("callTool passes the API's errors through, and names the tool on network errors", async () => {
  const refused = fakeFetch(() =>
    json(errorBody("invalid_input", "No processed event matches 'Nowhere'.", "find_mistakes"), 422),
  );
  await assert.rejects(callTool("find_mistakes", { event: "Nowhere" }, { fetch: refused, baseUrl: BASE }), {
    status: 422,
    code: "invalid_input",
    message: "No processed event matches 'Nowhere'.",
    tool: "find_mistakes",
  });
  const down = fakeFetch(() => {
    throw new TypeError("Failed to fetch");
  });
  await assert.rejects(callTool("compare_driving_styles", { driver_a: "HAM", driver_b: "LEC" }, { fetch: down, baseUrl: BASE }), {
    code: "unreachable",
    tool: "compare_driving_styles",
  });
});

test("there is a guard for every tool, in the API's order", () => {
  assert.deepEqual(Object.keys(TOOL_GUARDS), [
    "find_session",
    "list_sessions",
    "get_race_summary",
    "find_mistakes",
    "explain_corner",
    "compare_laps",
    "compare_driving_styles",
  ]);
});

// --- toApiError ------------------------------------------------------------------------------------

test("toApiError keeps an ApiError and turns anything else into one", () => {
  const original = new ApiError(503, "results_unavailable", "Rebuild.");
  assert.equal(toApiError(original), original);
  assert.equal(toApiError(new DOMException("x", "AbortError")).code, "aborted");
  const chat = toApiError({ status: 409, code: "turn_limit", message: "Eight questions." });
  assert.ok(chat instanceof ApiError);
  assert.deepEqual([chat.status, chat.code, chat.message], [409, "turn_limit", "Eight questions."]);
  assert.deepEqual([toApiError(new RangeError("bad")).code, toApiError(new RangeError("bad")).message], ["client_error", "bad"]);
  assert.equal(toApiError("weird").code, "client_error");
});
