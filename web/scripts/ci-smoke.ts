/**
 * The end-to-end check in CI (M7 plan 9, the `e2e` job): a production build of the site (`next
 * build` with NEXT_PUBLIC_API_URL set, then `next start`) against the API serving the made-up data
 * of engine/scripts/synthetic_tree.py, with the scripted chat and the limits on (memory store).
 * Checked with the site's own client, parsers and guards, in two phases, one per API setting:
 *
 *   node scripts/ci-smoke.ts --phase rate [--site http://localhost:3000] [--api http://127.0.0.1:8000]
 *       the API started with RACE_ENGINEER_RATE_HOUR=2
 *   node scripts/ci-smoke.ts --phase cap  [--site ...] [--api ...]
 *       the same API restarted with RACE_ENGINEER_DAILY_CAP_USD=0
 *
 * rate: every page answers 200 with its h1 (each report slug in the manifest included) and /nope
 * 404; the security headers are there and the Content-Security-Policy parses and names the API's
 * origin in connect-src; /robots.txt, /sitemap.xml (every page and report) and /opengraph-image
 * (a 1200 × 630 PNG) answer; /api/health is ok, with the scripted chat and the limits on; a
 * question streams through the site's chat client with `done` last and a chart that passes its
 * guard, and a second one answers too; the third in the hour is refused 429 rate_limited with
 * retry_after_s in the body, and that response, to a request sent with the site's Origin, names
 * Retry-After in Access-Control-Expose-Headers (Node's fetch ignores CORS, so that header is how
 * "the page can read it" is checked); a preflight from the site's origin is allowed and one from
 * https://example.com isn't; a 700 KiB chat body gets 413.
 * cap: /api/chat/status says daily_cap before any question, a question is refused 503 daily_cap,
 * and the saved answer synthetic_tree.py recorded is listed, loads through lib/chat/saved.ts and
 * passes the site's guard and its charts' guards.
 *
 * It first waits up to 90 s for both servers, then prints a table and exits with 1 when any check
 * fails (2 on a bad argument). It asks nothing of a chat that isn't the scripted one.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { QUESTIONS } from "../src/content/questions.ts";
import { loadChatStatus } from "../src/lib/api/chat-status.ts";
import { ApiError, errorFromResponse, getJSON, TOOL_GUARDS } from "../src/lib/api/client.ts";
import { loadHealth } from "../src/lib/api/health.ts";
import type { ToolName } from "../src/lib/api/types.ts";
import { streamChat } from "../src/lib/chat/client.ts";
import { createSavedCache, loadSaved, loadSavedIndex, type SavedOptions } from "../src/lib/chat/saved.ts";
import type { ChatEvent } from "../src/lib/chat/types.ts";
import { REPORTS } from "../src/lib/report/manifest.ts";
import { parseReport } from "../src/lib/report/markdown.ts";
import { pngSize } from "../src/lib/report/png.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPORT_DIR = path.resolve(HERE, "../../report");

const TIMEOUT_MS = 60_000;
const WAIT_MS = 90_000; // for `next start` and the API to come up
const RATE_HOUR = 2; // the e2e job's RACE_ENGINEER_RATE_HOUR
const BIG_BODY_BYTES = 700 * 1024; // over the chat's 640 KiB body limit
const UNLISTED_ORIGIN = "https://example.com";

/** The saved answer engine/scripts/synthetic_tree.py records. */
const SAVED_ID = "last-race-mistakes";
/** A question the scripted chat answers with find_mistakes and its chart on the made-up data. */
const CHART_QUESTION = QUESTIONS.find((q) => q.id === SAVED_ID)?.text ?? "Who made the biggest mistakes in the last race?";
const CHEAP_QUESTION = "Who will win the 2027 championship?"; // the scripted chat calls no tool

/** The pages and their h1s. Report slugs are added from the manifest, with the file's title. */
const PAGES: readonly { path: string; h1: string }[] = [
  { path: "/", h1: "Find the corner where the lap went wrong." },
  { path: "/chat", h1: "Ask the race engineer" },
  { path: "/mistakes", h1: "Mistake explorer" },
  { path: "/styles", h1: "How teammates drive differently" },
  { path: "/report", h1: "What the model found, and where it falls short" },
];

// ---- The table ------------------------------------------------------------------------------------

type Status = "ok" | "FAIL" | "skip";

interface Row {
  check: string;
  status: Status;
  ms: string;
  detail: string;
}

const rows: Row[] = [];

/** A check that didn't hold; its message is the row's detail. */
class Failure extends Error {}

function ensure(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Failure(message);
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status ? `${error.code} (HTTP ${error.status}): ${error.message}` : `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    if (error.name === "TimeoutError") return "timed out";
    const cause = (error.cause as { code?: unknown } | undefined)?.code;
    return typeof cause === "string" ? `${error.message} (${cause})` : error.message;
  }
  return String(error);
}

async function check(name: string, run: () => Promise<string | void>): Promise<boolean> {
  const started = performance.now();
  let status: Status = "ok";
  let detail = "";
  try {
    detail = (await run()) ?? "";
  } catch (error) {
    status = "FAIL";
    detail = describeError(error);
  }
  rows.push({ check: name, status, ms: String(Math.round(performance.now() - started)), detail });
  return status === "ok";
}

function printTable(phase: string, site: string, api: string): void {
  const head = ["check", "result", "ms", "detail"];
  const table = rows.map((r) => [r.check, r.status, r.ms, r.detail]);
  const widths = head.map((h, k) => Math.max(h.length, ...table.map((t) => t[k].length)));
  const line = (cells: string[]) =>
    cells
      .map((c, k) => (k === cells.length - 1 ? c : k === 2 ? c.padStart(widths[k]) : c.padEnd(widths[k])))
      .join("  ")
      .trimEnd();
  console.log(`CI smoke, phase ${phase}: site ${site}, API ${api}`);
  console.log(line(head));
  for (const t of table) console.log(line(t));
  const count = (s: Status) => rows.filter((r) => r.status === s).length;
  console.log(`${rows.length} checks: ${count("ok")} ok, ${count("FAIL")} failed, ${count("skip")} skipped`);
}

// ---- Helpers --------------------------------------------------------------------------------------

function option(argv: string[], name: string, fallback: string): string {
  const i = argv.indexOf(name);
  const given = i >= 0 ? argv[i + 1] : undefined;
  return (given || fallback).replace(/\/+$/, "");
}

/** An http(s) URL, as --site, --api and --origin take. */
function isHttpUrl(value: string): boolean {
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

function get(url: string, init: RequestInit = {}): Promise<Response> {
  return fetch(url, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS) });
}

const ENTITIES: Readonly<Record<string, string>> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, name: string) => {
    if (name[0] === "#") {
      const code = name[1] === "x" || name[1] === "X" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    return ENTITIES[name.toLowerCase()] ?? whole;
  });
}

// As site-smoke.ts reads text: inline elements join the text around them, any other tag ends a
// word, and React's empty comments go.
const INLINE_TAG = /<\/?(?:a|abbr|b|code|em|i|kbd|mark|q|s|small|span|strong|sub|sup|time|u)\b[^>]*>/gi;

/** The page's first h1, as text. */
function firstH1(html: string): string | null {
  const match = /<h1\b[^>]*>([\s\S]*?)<\/h1>/i.exec(html);
  if (!match) return null;
  const text = match[1].replace(/<!--[\s\S]*?-->/g, "").replace(INLINE_TAG, "").replace(/<[^>]*>/g, " ");
  return decodeEntities(text).replace(/\s+/g, " ").trim();
}

/** A Content-Security-Policy's directives by name, each with its sources. */
function parsePolicy(policy: string): Map<string, string[]> {
  const directives = new Map<string, string[]>();
  for (const part of policy.split(";")) {
    const [name, ...sources] = part.trim().split(/\s+/);
    if (name) directives.set(name.toLowerCase(), sources);
  }
  return directives;
}

/** Waits until `url` answers 200 (a server starting up), or throws after WAIT_MS. */
async function waitFor(url: string): Promise<number> {
  const started = performance.now();
  for (;;) {
    const status = await fetch(url, { signal: AbortSignal.timeout(5_000) })
      .then(async (r) => (await r.arrayBuffer(), r.status))
      .catch(() => 0);
    if (status === 200) return performance.now() - started;
    if (performance.now() - started > WAIT_MS) throw new Failure(`${url} didn't answer 200 in ${WAIT_MS / 1000} s (last: ${status || "no answer"})`);
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
}

/** Every event of one question through the site's chat client; `done` once and last. */
async function answer(api: string, message: string): Promise<ChatEvent[]> {
  const events: ChatEvent[] = [];
  for await (const event of streamChat({ message, history: [], signature: null }, { apiUrl: api, signal: AbortSignal.timeout(TIMEOUT_MS) })) {
    events.push(event);
  }
  const doneAt = events.findIndex((e) => e.type === "done");
  ensure(doneAt === events.length - 1 && doneAt >= 0, `the stream ended with ${events.at(-1)?.type ?? "nothing"}, not done`);
  const problem = events.find((e) => e.type === "error" || e.type === "refusal" || e.type === "retry");
  ensure(!problem, `a ${problem?.type} event`);
  return events;
}

/** Each chart the events carry passes its tool's guard; their bundles. */
function chartsPass(events: readonly ChatEvent[]): string[] {
  const bundles: string[] = [];
  for (const event of events) {
    if (event.type !== "tool_result" || event.chart === null) continue;
    ensure(TOOL_GUARDS[event.name as ToolName](event.chart.data), `${event.name}'s chart fails the site's guard`);
    bundles.push(event.chart.bundle);
  }
  return bundles;
}

function reportTitle(file: string): string {
  const title = parseReport(readFileSync(path.join(REPORT_DIR, file), "utf8")).title;
  if (!title) throw new Failure(`report/${file} has no # title`);
  return title.text;
}

// ---- Phase rate: the site, and the API at RATE_HOUR questions an hour -----------------------------

async function checkPages(site: string, api: string): Promise<void> {
  const pages = [...PAGES, ...REPORTS.map((r) => ({ path: `/report/${r.slug}`, h1: reportTitle(r.file) }))];
  const found: { home: Headers | null } = { home: null }; // the home page's headers, for the next check
  await check(`pages (${pages.length}) answer 200 with their h1`, async () => {
    for (const page of pages) {
      const response = await get(`${site}${page.path}`);
      const html = await response.text();
      ensure(response.status === 200, `${page.path}: HTTP ${response.status}`);
      const h1 = firstH1(html);
      ensure(h1 === page.h1, `${page.path}: h1 is ${h1 === null ? "missing" : `"${h1}"`}`);
      if (page.path === "/") found.home = response.headers;
    }
    const missing = await get(`${site}/nope`);
    await missing.arrayBuffer();
    ensure(missing.status === 404, `/nope: HTTP ${missing.status}`);
    return `${PAGES.length} pages and ${REPORTS.length} reports; /nope 404`;
  });

  await check("security headers, CSP connect-src names the API", async () => {
    const headers = found.home;
    ensure(headers !== null, "/ didn't load");
    const policy = headers.get("content-security-policy");
    ensure(policy, "no Content-Security-Policy (not a production build?)");
    const directives = parsePolicy(policy);
    ensure(directives.get("default-src")?.join(" ") === "'self'", "default-src isn't 'self'");
    const connect = directives.get("connect-src") ?? [];
    const apiOrigin = new URL(api).origin;
    ensure(connect.includes(apiOrigin), `connect-src is "${connect.join(" ")}", without ${apiOrigin}`);
    ensure(directives.get("frame-ancestors")?.join(" ") === "'none'", "frame-ancestors isn't 'none'");
    ensure(directives.get("object-src")?.join(" ") === "'none'", "object-src isn't 'none'");
    ensure(headers.get("x-content-type-options") === "nosniff", "X-Content-Type-Options isn't nosniff");
    ensure(headers.get("x-frame-options") === "DENY", "X-Frame-Options isn't DENY");
    ensure(headers.get("referrer-policy") !== null, "no Referrer-Policy");
    return `${directives.size} directives; connect-src ${connect.join(" ")}`;
  });

  await check("/robots.txt, /sitemap.xml, /opengraph-image", async () => {
    const robots = await get(`${site}/robots.txt`);
    const rules = await robots.text();
    ensure(robots.status === 200, `/robots.txt: HTTP ${robots.status}`);
    ensure(/^disallow:\s*\/dev\/?\s*$/im.test(rules), "/robots.txt doesn't disallow /dev/");
    ensure(/^sitemap:\s*\S+\/sitemap\.xml\s*$/im.test(rules), "/robots.txt names no sitemap");
    const sitemap = await get(`${site}/sitemap.xml`);
    const xml = await sitemap.text();
    ensure(sitemap.status === 200, `/sitemap.xml: HTTP ${sitemap.status}`);
    const listed = new Set([...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => new URL(m[1]).pathname));
    const want = [...PAGES.map((p) => p.path), ...REPORTS.map((r) => `/report/${r.slug}`)];
    const absent = want.filter((p) => !listed.has(p));
    ensure(absent.length === 0, `/sitemap.xml leaves out ${absent.join(", ")}`);
    const og = await get(`${site}/opengraph-image`);
    ensure(og.status === 200, `/opengraph-image: HTTP ${og.status}`);
    ensure((og.headers.get("content-type") ?? "").startsWith("image/png"), "/opengraph-image isn't a PNG");
    const size = pngSize(new Uint8Array(await og.arrayBuffer()));
    ensure(size.width === 1200 && size.height === 630, `/opengraph-image is ${size.width} × ${size.height}`);
    return `sitemap ${listed.size} URLs; OG image ${size.width} × ${size.height}`;
  });
}

/** Health ok with the scripted chat and the limits on; false (nothing asked) otherwise. */
async function checkHealth(api: string, perHour: number | null): Promise<boolean> {
  return check("/api/health", async () => {
    const health = await loadHealth(AbortSignal.timeout(TIMEOUT_MS), { baseUrl: api, retryMs: [] });
    ensure(health.chat.mode === "fake", `chat.mode is "${health.chat.mode}": this check asks only the scripted chat`);
    ensure(health.status === "ok", `degraded: ${health.problems.join("; ")}`);
    const { limits } = await getJSON<{ limits?: { enabled?: boolean; store?: string; per_hour?: number } }>(
      "/api/health",
      {},
      { baseUrl: api, signal: AbortSignal.timeout(TIMEOUT_MS) },
    );
    ensure(limits?.enabled === true && limits.store === "memory", `limits ${JSON.stringify(limits)}, expected on with the memory store`);
    if (perHour !== null) ensure(limits.per_hour === perHour, `${limits.per_hour} questions an hour, expected ${perHour}`);
    return `ok · ${health.data.sessions} sessions (latest ${health.data.latest}) · chat fake · limits memory, ${limits.per_hour}/hour`;
  });
}

async function checkRate(site: string, api: string): Promise<void> {
  const origin = new URL(site).origin;
  await checkPages(site, api);
  if (!(await checkHealth(api, RATE_HOUR))) return;

  await check("question 1 streams, done last, its chart passes", async () => {
    const events = await answer(api, CHART_QUESTION);
    const bundles = chartsPass(events);
    ensure(bundles.length > 0, "no chart");
    const tools = events.flatMap((e) => (e.type === "tool_call" ? [e.name] : []));
    return `${events.length} events · tools ${tools.join(", ")} · charts ${bundles.join(", ")}`;
  });

  await check("question 2 answers", async () => {
    const events = await answer(api, CHEAP_QUESTION);
    const done = events.at(-1);
    ensure(done?.type === "done" && done.quota !== null, "done has no quota, so the limits didn't count it");
    return `${events.length} events · quota ${done.quota.hour_left}/${done.quota.day_left} left`;
  });

  await check(`question 3 in the hour: 429, Retry-After exposed to the site`, async () => {
    const response = await get(`${api}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json", origin },
      body: JSON.stringify({ message: CHEAP_QUESTION, history: [], signature: null }),
    });
    const text = await response.text();
    ensure(response.status === 429, `HTTP ${response.status}`);
    const body = JSON.parse(text) as { error?: { code?: unknown; retry_after_s?: unknown } };
    const retry = body.error?.retry_after_s;
    ensure(typeof retry === "number" && retry > 0, `retry_after_s is ${JSON.stringify(retry)} in the body`);
    const error = await errorFromResponse(new Response(text, { status: response.status, headers: response.headers }));
    ensure(error.code === "rate_limited" && error.limit === "hour", `the site reads ${error.code} (limit ${error.limit})`);
    ensure(response.headers.get("access-control-allow-origin") === origin, `not allowed for ${origin}`);
    const exposed = (response.headers.get("access-control-expose-headers") ?? "").toLowerCase().split(/\s*,\s*/);
    ensure(exposed.includes("retry-after"), `Access-Control-Expose-Headers is "${exposed.join(", ")}"`);
    ensure(response.headers.get("retry-after") !== null, "no Retry-After header");
    return `rate_limited, retry_after_s ${retry}, Retry-After ${response.headers.get("retry-after")} exposed`;
  });

  await check(`preflights: ${origin} allowed, ${UNLISTED_ORIGIN} refused`, async () => {
    const preflight = (from: string) =>
      get(`${api}/api/chat`, {
        method: "OPTIONS",
        headers: { origin: from, "access-control-request-method": "POST", "access-control-request-headers": "content-type" },
      });
    const allowed = await preflight(origin);
    await allowed.arrayBuffer();
    ensure(allowed.status === 200 && allowed.headers.get("access-control-allow-origin") === origin, `${origin}: HTTP ${allowed.status}, not allowed`);
    const refused = await preflight(UNLISTED_ORIGIN);
    await refused.arrayBuffer();
    ensure(refused.headers.get("access-control-allow-origin") === null, `${UNLISTED_ORIGIN} allowed`);
    return `allowed (200); refused (HTTP ${refused.status})`;
  });

  await check(`a ${BIG_BODY_BYTES / 1024} KiB chat body: 413`, async () => {
    const message = "x".repeat(BIG_BODY_BYTES);
    const response = await get(`${api}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, history: [], signature: null }),
    });
    const error = await errorFromResponse(response);
    ensure(response.status === 413, `HTTP ${response.status} ${error.code}`);
    return `${error.code}`;
  });
}

// ---- Phase cap: the API restarted with a $0 daily cap ----------------------------------------------

async function checkCap(site: string, api: string): Promise<void> {
  if (!(await checkHealth(api, null))) return;

  await check("status says daily_cap before any question", async () => {
    const status = await loadChatStatus({ baseUrl: api, signal: AbortSignal.timeout(TIMEOUT_MS) });
    ensure(!status.available && status.reason === "daily_cap", `available ${status.available}, reason ${status.reason}`);
    ensure(status.saved.some((s) => s.id === SAVED_ID), `saved lists ${status.saved.map((s) => s.id).join(", ") || "nothing"}`);
    return `daily_cap, retry in ${status.retry_after_s} s, ${status.saved.length} saved answer(s)`;
  });

  await check("a question: 503 daily_cap", async () => {
    try {
      await answer(api, CHEAP_QUESTION);
    } catch (error) {
      ensure(error instanceof ApiError, describeError(error));
      ensure(error.status === 503 && error.code === "daily_cap", describeError(error));
      return describeError(error);
    }
    throw new Failure("the question was answered");
  });

  await check(`the saved answer "${SAVED_ID}" loads and passes its guards`, async () => {
    const options: SavedOptions = { apiUrl: api, staticBase: `${site}/saved`, cache: createSavedCache() };
    const index = await loadSavedIndex(options);
    ensure(index.some((row) => row.id === SAVED_ID), `the index lists ${index.map((row) => row.id).join(", ") || "nothing"}`);
    const saved = await loadSaved(SAVED_ID, options);
    ensure(saved !== null, "it doesn't load, or fails the site's guard");
    const bundles = chartsPass(saved.events);
    ensure(bundles.length > 0, "no chart");
    return `${saved.events.length} events, recorded ${saved.recorded_at} (${saved.mode}) · charts ${bundles.join(", ")}`;
  });
}

// ---- Main ----------------------------------------------------------------------------------------

async function main(): Promise<number> {
  const argv = process.argv.slice(2);
  const phase = option(argv, "--phase", "");
  if (phase !== "rate" && phase !== "cap") {
    console.error("usage: node scripts/ci-smoke.ts --phase rate|cap [--site <url>] [--api <url>]");
    return 2;
  }
  const site = option(argv, "--site", "http://localhost:3000");
  const api = option(argv, "--api", process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000");
  for (const [name, value] of [["--site", site], ["--api", api]]) {
    if (!isHttpUrl(value)) {
      console.error(`ci-smoke: ${name} is an http(s) URL, not "${value}"`);
      return 2;
    }
  }
  const up = await check("the site and the API are up", async () => {
    const [siteMs, apiMs] = await Promise.all([waitFor(`${site}/`), waitFor(`${api}/api/ready`)]);
    return `site after ${(siteMs / 1000).toFixed(1)} s, API ready after ${(apiMs / 1000).toFixed(1)} s`;
  });
  if (up) await (phase === "rate" ? checkRate(site, api) : checkCap(site, api));
  printTable(phase, site, api);
  return rows.some((r) => r.status === "FAIL") ? 1 : 0;
}

process.exitCode = await main();
