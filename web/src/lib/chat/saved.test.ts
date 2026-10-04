// Saved example answers (lib/chat/saved.ts): the guard, the index's order, and loading the
// static copy first and the API second, with a stand-in fetch.

import assert from "node:assert/strict";
import { test } from "node:test";

import { createSSEDecoder, parseChatEvent } from "./sse.ts";
import {
  createSavedCache,
  isSavedAnswer,
  loadSaved,
  loadSavedIndex,
  parseSavedAnswer,
  parseSavedIndex,
  savedIdFor,
  type SavedOptions,
} from "./saved.ts";
import { fixtureBytes, savedFixture } from "./test-support.ts";
import type { ChatEvent } from "./types.ts";

const SITE = "http://site.test/saved";
const API = "http://api.test";

const basic = () => savedFixture("saved-basic");

function row(id: string, question = `Question ${id}?`) {
  return { id, question, recorded_at: "2026-10-04T13:05:12Z", mode: "anthropic" };
}

/** A fetch serving `routes` (URL → body, or a status for an error); anything else is a 404. */
function site(routes: Record<string, unknown>) {
  const asked: string[] = [];
  const fetch = (async (input: string | URL | Request) => {
    const url = String(input);
    asked.push(url);
    if (!(url in routes)) return new Response("<html>Not found</html>", { status: 404 });
    const body = routes[url];
    if (typeof body === "number") return new Response("", { status: body });
    if (body instanceof Error) throw body;
    return new Response(typeof body === "string" ? body : JSON.stringify(body), {
      headers: { "content-type": "application/json" },
    });
  }) as typeof globalThis.fetch;
  return { fetch, asked };
}

function options(fetch: typeof globalThis.fetch): SavedOptions {
  return { fetch, apiUrl: API, staticBase: SITE, cache: createSavedCache() };
}

test("the fixture passes the guard, and its events come back as the site's own parser reads them", () => {
  const answer = parseSavedAnswer(basic());
  assert.ok(answer);
  assert.equal(answer.id, "last-race-mistakes");
  assert.equal(answer.mode, "fake");
  assert.deepEqual(answer.data, { latest: "2026 Sample Grand Prix Qualifying", results_run: "2026-10-01T00:00:00+00:00/synthetic" });
  // The same events as the basic stream, with done's history and signature emptied.
  const decoder = createSSEDecoder();
  const live = [...decoder.feed(fixtureBytes("basic")), ...decoder.end()].map((m) => parseChatEvent(m.event, m.data) as ChatEvent);
  assert.deepEqual(
    answer.events,
    live.map((event) => (event.type === "done" ? { ...event, history: [], signature: "" } : event)),
  );
  assert.equal(isSavedAnswer(basic()), true);
});

test("malformed recordings are rejected", () => {
  const broken: [string, (r: Record<string, unknown>) => void][] = [
    ["not version 1", (r) => (r.v = 2)],
    ["a bad id", (r) => (r.id = "../etc/passwd")],
    ["an upper-case id", (r) => (r.id = "Monaco")],
    ["a blank question", (r) => (r.question = "  ")],
    ["a date that isn't one", (r) => (r.recorded_at = "yesterday")],
    ["an unknown mode", (r) => (r.mode = "openai")],
    ["no model", (r) => delete r.model],
    ["no events", (r) => (r.events = [])],
    ["events that aren't a list", (r) => (r.events = { type: "done" })],
    ["no done", (r) => (r.events = (r.events as unknown[]).slice(0, -1))],
    ["done not last", (r) => (r.events = [...(r.events as unknown[]).slice(-1), ...(r.events as unknown[]).slice(0, -1)])],
    ["two dones", (r) => (r.events = [...(r.events as unknown[]), (r.events as unknown[]).at(-1)])],
    ["a known event that doesn't parse", (r) => ((r.events as unknown[])[0] = { type: "status", text: 3 })],
    ["an event without a type", (r) => ((r.events as unknown[])[0] = { text: "x" })],
    ["an error event", (r) => (r.events as unknown[]).splice(1, 0, { type: "error", code: "timeout", message: "x" })],
    ["a retry event", (r) => (r.events as unknown[]).splice(1, 0, { type: "retry", message: "x" })],
    ["a refusal", (r) => (r.events as unknown[]).splice(1, 0, { type: "refusal", message: "x", category: null })],
  ];
  for (const [what, change] of broken) {
    const recording = basic();
    change(recording);
    assert.equal(parseSavedAnswer(recording), null, what);
  }
  for (const junk of [null, [], "x", 1, {}]) assert.equal(parseSavedAnswer(junk), null);
});

test("unknown events are skipped as in a live stream; missing data fields become null", () => {
  const recording = basic();
  (recording.events as unknown[]).splice(1, 0, { type: "thinking_summary", text: "new in a later recorder" });
  delete recording.data;
  const answer = parseSavedAnswer(recording);
  assert.ok(answer);
  assert.equal(answer.events.length, 8);
  assert.deepEqual(answer.data, { latest: null, results_run: null });
});

test("the index: rows that fit, each id once, in the example questions' order, others after", () => {
  const index = parseSavedIndex({
    answers: [
      row("zzz-extra"),
      row("out-of-scope"),
      row("monaco-2023"),
      { id: "bad id!", question: "x", recorded_at: "2026-10-04T13:05:12Z", mode: "anthropic" },
      { ...row("latest"), recorded_at: "never" },
      row("monaco-2023", "A second copy"),
      row("aaa-extra"),
      row("latest"),
      "junk",
    ],
  });
  assert.deepEqual(
    index?.map((r) => r.id),
    ["latest", "monaco-2023", "out-of-scope", "zzz-extra", "aaa-extra"],
  );
  assert.equal(index?.find((r) => r.id === "monaco-2023")?.question, "Question monaco-2023?");
  assert.equal(parseSavedIndex({ answers: "x" }), null);
  assert.equal(parseSavedIndex([row("latest")]), null);
  assert.deepEqual(parseSavedIndex({ answers: [] }), []);
});

test("savedIdFor: an example question's text finds its recording, when the index has one", () => {
  const index = [row("monaco-2023"), row("ham-lec-style")];
  assert.equal(savedIdFor("Summarise the 2023 Monaco Grand Prix.", index), "monaco-2023");
  assert.equal(savedIdFor("  Summarise the 2023 Monaco Grand Prix.  ", index), "monaco-2023");
  assert.equal(savedIdFor("Who made the biggest mistakes in the last race?", index), null); // not recorded
  assert.equal(savedIdFor("Summarise the 2023 Monaco GP", index), null); // not a chip's text
});

test("the index is read from the static copy first, and the API isn't asked", async () => {
  const { fetch, asked } = site({ [`${SITE}/index.json`]: { answers: [row("monaco-2023")] } });
  const index = await loadSavedIndex(options(fetch));
  assert.deepEqual(index.map((r) => r.id), ["monaco-2023"]);
  assert.deepEqual(asked, [`${SITE}/index.json`]);
});

test("without a static copy (or with a broken or empty one) the API's index is used", async () => {
  for (const missing of [undefined, 500, "not json", { answers: [] }, { nothing: true }]) {
    const routes: Record<string, unknown> = { [`${API}/api/saved`]: { answers: [row("latest")] } };
    if (missing !== undefined) routes[`${SITE}/index.json`] = missing;
    const { fetch, asked } = site(routes);
    const index = await loadSavedIndex(options(fetch));
    assert.deepEqual(index.map((r) => r.id), ["latest"], String(missing));
    assert.deepEqual(asked, [`${SITE}/index.json`, `${API}/api/saved`]);
  }
});

test("neither answering gives no saved answers; that isn't kept, so the next look asks again", async () => {
  const unreachable = new TypeError("fetch failed");
  const { fetch, asked } = site({ [`${SITE}/index.json`]: unreachable, [`${API}/api/saved`]: unreachable });
  const opts = options(fetch);
  assert.deepEqual(await loadSavedIndex(opts), []);
  assert.deepEqual(await loadSavedIndex(opts), []);
  assert.equal(asked.length, 4);
});

test("a loaded index is kept for the page's life: one request for many callers", async () => {
  const { fetch, asked } = site({ [`${SITE}/index.json`]: { answers: [row("latest")] } });
  const opts = options(fetch);
  const [a, b] = await Promise.all([loadSavedIndex(opts), loadSavedIndex(opts)]);
  assert.equal(a, b);
  await loadSavedIndex(opts);
  assert.equal(asked.length, 1);
});

test("a recording: the static copy first, else the API's; a wrong id or a malformed file isn't shown", async () => {
  const fromStatic = site({ [`${SITE}/last-race-mistakes.json`]: basic() });
  const answer = await loadSaved("last-race-mistakes", options(fromStatic.fetch));
  assert.equal(answer?.question, "Who made the biggest mistakes in the last race?");
  assert.deepEqual(fromStatic.asked, [`${SITE}/last-race-mistakes.json`]);

  const malformed = { ...basic(), events: [] };
  const fromApi = site({ [`${SITE}/last-race-mistakes.json`]: malformed, [`${API}/api/saved/last-race-mistakes`]: basic() });
  assert.equal((await loadSaved("last-race-mistakes", options(fromApi.fetch)))?.id, "last-race-mistakes");
  assert.deepEqual(fromApi.asked, [`${SITE}/last-race-mistakes.json`, `${API}/api/saved/last-race-mistakes`]);

  // A file whose id isn't the one asked for, or an id the API wouldn't take, gives nothing.
  const other = site({ [`${SITE}/latest.json`]: basic(), [`${API}/api/saved/latest`]: basic() });
  assert.equal(await loadSaved("latest", options(other.fetch)), null);
  const none = site({});
  assert.equal(await loadSaved("../index", options(none.fetch)), null);
  assert.deepEqual(none.asked, []);
});

test("once the index came from the API, recordings are asked of the API alone (the static copies aren't there)", async () => {
  const fromApi = site({ [`${API}/api/saved`]: { answers: [row("last-race-mistakes")] }, [`${API}/api/saved/last-race-mistakes`]: basic() });
  const opts = options(fromApi.fetch);
  await loadSavedIndex(opts);
  assert.equal((await loadSaved("last-race-mistakes", opts))?.id, "last-race-mistakes");
  assert.deepEqual(fromApi.asked, [`${SITE}/index.json`, `${API}/api/saved`, `${API}/api/saved/last-race-mistakes`]);

  // A static index keeps the static copy first, with the API behind it for a missing file.
  const fromStatic = site({ [`${SITE}/index.json`]: { answers: [row("last-race-mistakes")] }, [`${API}/api/saved/last-race-mistakes`]: basic() });
  const staticOpts = options(fromStatic.fetch);
  await loadSavedIndex(staticOpts);
  assert.equal((await loadSaved("last-race-mistakes", staticOpts))?.id, "last-race-mistakes");
  assert.deepEqual(fromStatic.asked, [`${SITE}/index.json`, `${SITE}/last-race-mistakes.json`, `${API}/api/saved/last-race-mistakes`]);
});

test("recordings are kept once loaded, and a failed one is asked again", async () => {
  const good = site({ [`${SITE}/last-race-mistakes.json`]: basic() });
  const opts = options(good.fetch);
  const first = await loadSaved("last-race-mistakes", opts);
  assert.equal(await loadSaved("last-race-mistakes", opts), first);
  assert.equal(good.asked.length, 1);
  const bad = site({});
  const badOpts = options(bad.fetch);
  assert.equal(await loadSaved("latest", badOpts), null);
  assert.equal(await loadSaved("latest", badOpts), null);
  assert.equal(bad.asked.length, 4);
});
