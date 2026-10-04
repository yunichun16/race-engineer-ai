import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isCornerChart, type CornerChart } from "../../charts/explain-corner.ts";
import { claims } from "../../content/claims.ts";
import { FEATURED, LANDING_CORNER } from "../../content/featured.ts";
import { LANDING_QUESTIONS, QUESTIONS, chipLabel } from "../../content/questions.ts";
import { handOff, MAX_QUESTION_CHARS, PENDING_KEY } from "./ask.ts";
import {
  featuredCornerSnapshot,
  LIVE_TIMEOUT_MS,
  loadFeaturedCorner,
  resetFeaturedCorner,
  snapshotUrl,
  startFeaturedCorner,
  startWhenIdle,
  subscribeFeaturedCorner,
  type FeaturedPayload,
  type IdleHost,
} from "./featured-corner.ts";
import { ILLUSTRATION, inWords, parseInterval, phasePhrase, storyGeometry, storyHeadline, tracesFrom, worstPhase } from "./story.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// A real explain_corner payload (the MCP dev host's sample), so the shapes are the tool's own.
const sample = JSON.parse(readFileSync(path.resolve(HERE, "../../mcp-app/sample-explain-corner.json"), "utf8")) as {
  structuredContent?: unknown;
  content?: { text: string }[];
};
const corner = sample.structuredContent as CornerChart;
const summary = sample.content?.[0]?.text ?? "summary";

const API = "http://api.test";

/** A fake fetch that answers by URL, recording what was asked. */
function fakeFetch(routes: Record<string, () => Response | Promise<Response>>) {
  const calls: string[] = [];
  const send = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    for (const [prefix, answer] of Object.entries(routes)) if (url.startsWith(prefix)) return answer();
    throw new TypeError("Failed to fetch");
  }) as typeof fetch;
  return { send, calls };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

test("the sample is a corner chart", () => {
  assert.ok(isCornerChart(corner));
});

// --- The three load paths of the example ----------------------------------------------------

test("load path 1: the snapshot when it is there; the API is never called", async () => {
  const { send, calls } = fakeFetch({ [snapshotUrl(LANDING_CORNER)]: () => json({ tool: "explain_corner", summary, data: corner, chart: null }) });
  const got = await loadFeaturedCorner(LANDING_CORNER, { fetch: send, baseUrl: API });
  assert.equal(got?.source, "snapshot");
  assert.equal(got?.summary, summary);
  assert.deepEqual(calls, ["/snapshots/2026_15_R_1_16_T5.json"]);
});

test("load path 2: no snapshot (404) falls through to the live explain_corner call", async () => {
  const { send, calls } = fakeFetch({
    "/snapshots/": () => new Response("<!doctype html>Not found", { status: 404 }),
    [`${API}/api/tools/explain_corner`]: () => json({ tool: "explain_corner", summary, data: corner, chart: null }),
  });
  const got = await loadFeaturedCorner(LANDING_CORNER, { fetch: send, baseUrl: API });
  assert.equal(got?.source, "live");
  assert.equal(calls.length, 2);
  assert.equal(
    calls[1],
    `${API}/api/tools/explain_corner?corner=5&driver=NOR&event=Azerbaijan+Grand+Prix&lap=16&session=R&year=2026`,
  );
});

test("load path 2b: a snapshot that isn't a corner chart is ignored, not drawn", async () => {
  const { send } = fakeFetch({
    "/snapshots/": () => json({ summary, data: { nope: true } }),
    [`${API}/api/tools/explain_corner`]: () => json({ tool: "explain_corner", summary, data: corner, chart: null }),
  });
  assert.equal((await loadFeaturedCorner(LANDING_CORNER, { fetch: send, baseUrl: API }))?.source, "live");
});

test("load path 3: no snapshot and no API gives null, never a throw", async () => {
  const { send, calls } = fakeFetch({ "/snapshots/": () => new Response("", { status: 404 }) });
  assert.equal(await loadFeaturedCorner(LANDING_CORNER, { fetch: send, baseUrl: API }), null);
  assert.equal(calls.length, 2); // the snapshot, then the API (which refused)
  const { send: broken } = fakeFetch({
    "/snapshots/": () => new Response("{not json", { status: 200 }),
    [`${API}/`]: () => json({ error: { code: "results_unavailable", message: "rebuild" } }, 503),
  });
  assert.equal(await loadFeaturedCorner(LANDING_CORNER, { fetch: broken, baseUrl: API }), null);
});

test("load path 3b: an API that never answers gives up after the time limit, then null", async () => {
  const send = (async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).startsWith("/snapshots/")) return new Response("", { status: 404 });
    // Accepts the request and never answers, until the signal gives up on it.
    return new Promise<Response>((_, reject) => {
      init?.signal?.addEventListener("abort", () => reject(init.signal?.reason ?? new DOMException("aborted", "AbortError")));
    });
  }) as typeof fetch;
  const started = Date.now();
  assert.equal(await loadFeaturedCorner(LANDING_CORNER, { fetch: send, baseUrl: API, timeoutMs: 30 }), null);
  assert.ok(Date.now() - started < 2000, "it gave up at the time limit");
  assert.ok(LIVE_TIMEOUT_MS >= 20_000, "long enough for a cold explain_corner");
});

test("the store loads once and tells every subscriber", async () => {
  resetFeaturedCorner();
  const seen: string[] = [];
  const stop = subscribeFeaturedCorner(() => seen.push(featuredCornerSnapshot().status));
  let calls = 0;
  const payload: FeaturedPayload = { summary, data: corner, source: "snapshot" };
  const load = async () => {
    calls++;
    return payload;
  };
  startFeaturedCorner(load);
  startFeaturedCorner(load); // the idle start and the story's observer may both fire
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls, 1);
  assert.deepEqual(seen, ["loading", "ready"]);
  const state = featuredCornerSnapshot();
  assert.ok(state.status === "ready" && state.payload === payload);
  stop();

  resetFeaturedCorner();
  startFeaturedCorner(async () => null);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(featuredCornerSnapshot().status, "missing");
  resetFeaturedCorner();
});

test("the idle start waits for load, uses requestIdleCallback with a 2 s timeout, and cancels", () => {
  const events: string[] = [];
  let onLoad: (() => void) | null = null;
  const host: IdleHost = {
    document: { readyState: "loading" },
    addEventListener: (_type, listener) => {
      onLoad = listener;
    },
    removeEventListener: () => events.push("unlisten"),
    requestIdleCallback: (cb, opts) => {
      events.push(`idle ${opts?.timeout}`);
      cb();
      return 7;
    },
    cancelIdleCallback: (id) => events.push(`cancel ${id}`),
    setTimeout: () => 0,
    clearTimeout: () => {},
  };
  const cancel = startWhenIdle(host, () => events.push("start"));
  assert.deepEqual(events, []);
  (onLoad as unknown as () => void)();
  assert.deepEqual(events, ["idle 2000", "start"]);
  cancel();
  assert.deepEqual(events.slice(2), ["unlisten", "cancel 7"]);

  // Safari: no requestIdleCallback, so a short timer after load.
  const timers: number[] = [];
  startWhenIdle({ ...host, document: { readyState: "complete" }, requestIdleCallback: undefined, setTimeout: (_cb, ms) => timers.push(ms) }, () => {});
  assert.deepEqual(timers, [200]);
});

// --- The story ------------------------------------------------------------------------------

test("time lost in words for the story's headline", () => {
  assert.equal(inWords(0.79), "eight tenths");
  assert.equal(inWords(0.14), "one tenth");
  assert.equal(inWords(0.91), "nine tenths");
  assert.equal(inWords(0.02), "under a tenth");
  assert.equal(inWords(0.97), "a second");
  assert.equal(inWords(1.24), "1.2 seconds");
});

test("the headline uses the name and the payload, and a plain line without them", () => {
  const nor = { ...corner, lap_number: 16, turn: "5", driver: "NOR", time_lost_s: 0.79 };
  assert.equal(storyHeadline(nor, "Norris"), "Lap 16, turn 5: where Norris lost eight tenths.");
  assert.equal(storyHeadline(nor), "Lap 16, turn 5: where NOR lost eight tenths.");
  assert.equal(storyHeadline(null, "Norris"), "Where the lap went wrong, corner by corner.");
  assert.equal(storyHeadline({ ...nor, time_lost_s: null }), "Where the lap went wrong, corner by corner.");
});

test("the worst phase and how the tool words it", () => {
  const phases = [
    { name: "entry", start_m: -250, end_m: -50, loss_s: -0.02 },
    { name: "apex", start_m: -50, end_m: 30, loss_s: -0.12 },
    { name: "exit", start_m: 30, end_m: 145, loss_s: 0.93 },
  ];
  assert.equal(worstPhase(phases)?.name, "exit");
  assert.equal(worstPhase([{ name: "apex", start_m: 0, end_m: 1, loss_s: -1 }]), null);
  assert.equal(phasePhrase("exit"), "on exit");
  assert.equal(phasePhrase("apex"), "through the apex");
  assert.equal(phasePhrase("entry"), "on entry");
});

test("the illustration draws all four layers inside the box", () => {
  const g = storyGeometry(ILLUSTRATION);
  assert.match(g.band, /^M[\d.]+,[\d.]+C.*L[\d.]+,[\d.]+C.*Z$/);
  assert.match(g.usual, /^M/);
  assert.match(g.lap, /^M/);
  assert.ok(g.stretch && g.stretch.width > 0 && g.stretch.x >= 0 && g.stretch.x + g.stretch.width <= 420);
  assert.ok(g.dot && g.dot.y > 16 && g.dot.y <= 214, "the slowest point sits inside the plot");
  assert.equal(g.apexX, Math.round((250 / 395) * 420 * 10) / 10);
  // Same text every time (server and browser must agree).
  assert.equal(storyGeometry(ILLUSTRATION).lap, g.lap);
});

test("the real payload: speed, its band, and where the time went from the chart's own rule", () => {
  const traces = tracesFrom(corner);
  assert.equal(traces.dist, corner.distance_m);
  assert.equal(traces.lap, corner.lap.speed);
  const g = storyGeometry(traces);
  assert.ok(g.lap.startsWith("M0.0,"), g.lap.slice(0, 20));
  assert.ok(g.dot);
  // The slowest sample is the dot.
  const speeds = corner.lap.speed.filter((v): v is number => v !== null);
  const i = corner.lap.speed.indexOf(Math.min(...speeds));
  assert.equal(g.dot.x, Math.round(((corner.distance_m[i] - corner.distance_m[0]) / (corner.distance_m.at(-1)! - corner.distance_m[0])) * 4200) / 10);
  // Without a band, only the dashed line.
  assert.equal(storyGeometry(tracesFrom({ ...corner, usual_lo: null, usual_hi: null })).band, "");
});

test("the style bar parses the claim it draws", () => {
  assert.deepEqual(parseInterval(claims.hamBrakesEarlier.value), { est: -6.1, lo: -9.2, hi: -3.1 });
  assert.equal(parseInterval("6.1 m [3.1, 9.2]"), null);
  assert.equal(parseInterval("1 [2, 3]"), null, "an estimate outside its interval");
});

// --- The Ask box's hand-over ------------------------------------------------------------------

test("asking leaves the question for the chat and goes to /chat", () => {
  const store = new Map<string, string>();
  const storage = { setItem: (k: string, v: string) => void store.set(k, v) };
  assert.equal(handOff(storage, "  Who won in Baku?  "), "/chat");
  assert.equal(store.get(PENDING_KEY), "Who won in Baku?");
  assert.equal(PENDING_KEY, "re.chat.pending");
  assert.equal(handOff(storage, "   "), null, "a blank question goes nowhere");
  handOff(storage, "x".repeat(MAX_QUESTION_CHARS + 50));
  assert.equal(store.get(PENDING_KEY)?.length, MAX_QUESTION_CHARS);
});

test("with storage blocked, the question only prefills the chat (/chat?q=)", () => {
  const blocked = {
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
  };
  assert.equal(handOff(blocked, "Who won in Baku?"), "/chat?q=Who+won+in+Baku%3F");
  assert.equal(handOff(null, "Hi"), "/chat?q=Hi");
});

test("the landing offers three chips with short labels, and sends the full question", () => {
  assert.deepEqual(
    LANDING_QUESTIONS.map((q) => q.id),
    ["monaco-2023", "abu-dhabi-gain", "ham-lec-style"],
  );
  assert.deepEqual(LANDING_QUESTIONS.map(chipLabel), [
    "Summarise the 2023 Monaco Grand Prix",
    "Norris vs Piastri, Abu Dhabi",
    "Hamilton vs Leclerc style",
  ]);
  assert.deepEqual(
    QUESTIONS.map((q) => q.id),
    ["latest", "monaco-2023", "last-race-mistakes", "abu-dhabi-gain", "ham-lec-style", "baku-sc", "out-of-scope"],
  );
  assert.equal(new Set(QUESTIONS.map((q) => q.id)).size, QUESTIONS.length);
});

// --- Copy rules -----------------------------------------------------------------------------

const LANDING_FILES = readdirSync(HERE).filter((f) => /\.tsx?$/.test(f) && !f.endsWith(".test.ts"));

test("no verdict, review or accuracy wording on the landing (plan 13)", () => {
  assert.ok(LANDING_FILES.length >= 10, `found ${LANDING_FILES.length} landing files`);
  const banned = /checked by a human|a reviewer (checked|judged)|real mistake|checked on video|precision(All|Rc|Tel|First)|71%|7 in 10|\bverified\b/i;
  for (const file of LANDING_FILES) {
    const text = readFileSync(path.join(HERE, file), "utf8");
    assert.doesNotMatch(text, banned, file);
  }
  for (const f of FEATURED) assert.doesNotMatch(f.blurb, banned, f.id);
});
