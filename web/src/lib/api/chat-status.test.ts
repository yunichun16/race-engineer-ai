/** GET /api/chat/status (plan 13.3): the body as the API sends it, checked field by field. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { CHAT_STATUS_PATH, loadChatStatus, parseChatStatus } from "./chat-status.ts";
import { errorBody, fakeFetch, json } from "./test-support.ts";

const BASE = "http://api.test";

// The frozen example from plan 13.3.
const LIMITED = {
  available: true,
  mode: "anthropic",
  reason: null,
  retry_after_s: null,
  quota: { hour_left: 8, day_left: 21, per_hour: 10, per_day: 25 },
  visitor: "3fa2c1",
  saved: [{ id: "monaco-2023", recorded_at: "2026-10-04T13:05:12Z" }],
};

test("the frozen example parses as it is", () => {
  assert.deepEqual(parseChatStatus(LIMITED), LIMITED);
});

test("limits off: no quota and no visitor; an empty saved list", () => {
  const off = { available: true, mode: "fake", reason: null, retry_after_s: null, quota: null, visitor: null, saved: [] };
  assert.deepEqual(parseChatStatus(off), off);
});

test("every refusal reason is kept, with its wait", () => {
  for (const reason of ["off", "paused", "daily_cap", "rate_limited", "unavailable"] as const) {
    const status = parseChatStatus({ ...LIMITED, available: false, reason, retry_after_s: 60 });
    assert.equal(status?.reason, reason);
    assert.equal(status?.retry_after_s, 60);
  }
});

test("only `available` is required; the rest falls back to neutral values", () => {
  assert.equal(parseChatStatus(null), null);
  assert.equal(parseChatStatus([]), null);
  assert.equal(parseChatStatus({ reason: "paused" }), null);
  assert.equal(parseChatStatus({ available: "yes" }), null);
  assert.deepEqual(parseChatStatus({ available: true }), {
    available: true,
    mode: "anthropic",
    reason: null,
    retry_after_s: null,
    quota: null,
    visitor: null,
    saved: [],
  });
  const odd = parseChatStatus({
    available: false,
    mode: "other",
    reason: "solar_flare",
    retry_after_s: -4,
    quota: { hour_left: 1, day_left: "2", per_hour: 10, per_day: 25 },
    visitor: 7,
    saved: [{ id: "a", recorded_at: "2026-10-04T13:05:12Z" }, { id: 3 }, "x"],
  });
  assert.deepEqual(odd, {
    available: false,
    mode: "anthropic",
    reason: "unavailable", // a reason this site doesn't know, while the chat is closed
    retry_after_s: null,
    quota: null,
    visitor: null,
    saved: [{ id: "a", recorded_at: "2026-10-04T13:05:12Z" }],
  });
  // An available chat has no reason, whatever the field says; "off" implies the mode.
  assert.equal(parseChatStatus({ available: true, reason: "paused" })?.reason, null);
  assert.equal(parseChatStatus({ available: false, reason: "off" })?.mode, "off");
});

test("loadChatStatus asks GET /api/chat/status once and checks the body", async () => {
  const fetch = fakeFetch(() => json(LIMITED));
  assert.deepEqual(await loadChatStatus({ fetch, baseUrl: BASE }), LIMITED);
  assert.equal(fetch.calls.length, 1);
  assert.equal(fetch.calls[0].url, `${BASE}${CHAT_STATUS_PATH}`);
  await assert.rejects(loadChatStatus({ fetch: fakeFetch(() => json({ ok: true })), baseUrl: BASE }), {
    name: "ApiError",
    code: "bad_payload",
  });
  // An API from before M7 has no such route.
  await assert.rejects(loadChatStatus({ fetch: fakeFetch(() => json(errorBody("not_found", "Not Found"), 404)), baseUrl: BASE }), {
    status: 404,
    code: "not_found",
  });
  // The status route's own request rate (60 a minute per visitor) answers like the tools'.
  const busy = fakeFetch(() => json({ error: { code: "rate_limited", message: "Slow down.", retry_after_s: 12 } }, 429));
  await assert.rejects(loadChatStatus({ fetch: busy, baseUrl: BASE }), { code: "rate_limited", retryAfterS: 12 });
});
