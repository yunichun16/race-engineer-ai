/**
 * Checks the running site and API end to end, the way a visitor's browser uses them (plan 10.2 as
 * amended in plan 13). Not CI: it needs `make web` (or a production `next start`) and
 * `make api-fake`, with the processed data and the M4 results, or a deployment
 * (`make deploy-check`, M7 plan 13.7), which has the same data.
 *
 *   node scripts/site-smoke.ts [--site http://localhost:3000] [--api http://127.0.0.1:8000]
 *                              [--origin <the site's origin>] [--chat fake|skip|one]
 *                                                                      (or make site-smoke)
 *
 * 1. Site: every route answers 200 with its h1 in the HTML, each report slug in the manifest
 *    included (18; m4-review and m7-chat-accuracy aren't published, so they are 404s like /nope),
 *    every figure is a PNG, /dev/charts is 200 under `next dev` and 404 in a production build,
 *    and the web app manifest, its icons and the apple-touch icon resolve. No page shows a figure
 *    from the human review (the site quotes no accuracy), and every internal link on the pages
 *    resolves, anchors included. A production build sends the security headers (M7 plan 7.3),
 *    its Content-Security-Policy naming the API's origin in connect-src; the headers, HSTS
 *    included (Vercel sends its own on its domains, a local build none), are printed after the
 *    table. The hero card's snapshot holds every example, starting on the landing corner, and
 *    /saved/index.json lists the seven example questions' saved answers, each of which loads and
 *    passes its guards. Both are written at build time from the API (make site-snapshots locally,
 *    `npm run build:vercel` on Vercel): missing, they are skipped, except on an https site (a
 *    deployment), where missing means the build's prebuild didn't reach the API.
 * 2. API: /api/health is "ok"; the three catalog routes pass the site's own checks
 *    (lib/api/catalog.ts), with 263 sessions over 107 weekends; every featured corner
 *    (content/featured.ts) and style example (content/style-examples.ts) answers through the
 *    site's client (lib/api/client.ts) and passes its chart's guard; the landing corner is
 *    flagged and has a replay, and the nothing-flagged session has an empty list. find_mistakes
 *    at limit 50 for the explorer's default session records its payload size, and compare_laps
 *    runs for Norris and Piastri in 2025 Abu Dhabi qualifying.
 * 3. Chat, `--chat fake` (the default), with the scripted chat only (chat.mode "fake"; a real
 *    model would cost money): every question in content/questions.ts streams events the site's
 *    own parser (lib/chat/sse.ts) reads, in a valid order with `done` last, calling the tool the
 *    chip is there for, with charts that pass their guards and explorer links (lib/chat/links.ts)
 *    that open; "Show telemetry" on the find-mistakes card draws its corner; "#refuse" gives a
 *    refusal, then done; a history with one changed byte gives 400 invalid_history; a ninth
 *    question in one conversation gives 409 turn_limit (both through lib/chat/client.ts). That is
 *    17 questions, past the 10 an hour of an API with the limits on. `--chat one` asks the
 *    "latest" question once, of whichever chat the API runs (a real model: about $0.01, and one
 *    of this connection's questions that hour), and checks that its events arrived spread over
 *    the answer rather than all at the end (a proxy that buffers the stream). `--chat skip` asks
 *    nothing.
 * 4. CORS: a preflight for POST /api/chat, and a GET, from both dev origins (or from `--origin`
 *    alone: a deployed API allows only its site) are allowed, and one from an unlisted origin
 *    isn't.
 *
 * It prints a table, exits with 1 when any check fails (2 on a bad argument), and writes nothing.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isChartName } from "../src/components/charts/chart-names.ts";
import { HERO_URL, heroEntries, parseHeroFile } from "../src/components/landing/hero-examples.ts";
import { parseMistakesQuery } from "../src/components/mistakes/query.ts";
import { resolveSelection, type Selection } from "../src/components/mistakes/selection.ts";
import { cornerParams, FEATURED, featuredHref, LANDING_CORNER } from "../src/content/featured.ts";
import { QUESTIONS } from "../src/content/questions.ts";
import { STYLE_EXAMPLES } from "../src/content/style-examples.ts";
import { getSessionCatalog, getSessionDrivers, getStyleCatalog } from "../src/lib/api/catalog.ts";
import { ApiError, callTool, errorFromResponse, TOOL_GUARDS, type RequestOptions } from "../src/lib/api/client.ts";
import { loadHealth } from "../src/lib/api/health.ts";
import type { SessionCatalog, ToolName } from "../src/lib/api/types.ts";
import { streamChat } from "../src/lib/chat/client.ts";
import { explainCornerArgs, explorerLink } from "../src/lib/chat/links.ts";
import { parseSavedAnswer, parseSavedIndex, STATIC_SAVED_BASE } from "../src/lib/chat/saved.ts";
import { isChatEventType, parseChatEvent, parseSSE } from "../src/lib/chat/sse.ts";
import type { ChatEvent, ChatRequest, DoneEvent } from "../src/lib/chat/types.ts";
import { FIGURES } from "../src/lib/report/figures.ts";
import { EXCLUDED, REPORTS } from "../src/lib/report/manifest.ts";
import { parseReport } from "../src/lib/report/markdown.ts";
import { pngSize } from "../src/lib/report/png.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPORT_DIR = path.resolve(HERE, "../../report");

const PAGE_TIMEOUT_MS = 120_000; // `next dev` compiles a route on its first visit
const TOOL_TIMEOUT_MS = 90_000; // a cold explain_corner loads a whole session
const CHAT_TIMEOUT_MS = 120_000;

/** What the data holds today (plan 5.4): a rebuilt dataset changes these on purpose. */
const SESSIONS = 263;
const WEEKENDS = 107;
const PAIRS_2025 = 13;
/**
 * The published reports: the plan's 15, minus m4-review (plan 13), plus m6-website, and M7's
 * technical-report, model-card and m7-ship (m7_chat_accuracy.md isn't published, M7 decision 13).
 */
const REPORT_SLUGS = 18;
/** The brand navy (plan 13): the installed app's background and theme colour. */
const NAVY = "#070a12";

/** The pages and their h1s. Report slugs are added from the manifest, with the file's title. */
const PAGES: readonly { path: string; h1: string }[] = [
  { path: "/", h1: "Find the corner where the lap went wrong." },
  { path: "/chat", h1: "Ask the race engineer" },
  { path: "/mistakes", h1: "Mistake explorer" },
  { path: "/styles", h1: "How teammates drive differently" },
  { path: "/report", h1: "What the model found, and where it falls short" },
];

/** Not pages: m4-review and the chat accuracy check aren't published (plan 13, M7 decision 13). */
const MISSING: readonly string[] = ["/nope", "/report/m4-review", "/report/m7-chat-accuracy"];

/**
 * Anchors that a page's client island renders after hydration. A production build's HTML holds
 * only the Suspense fallback there, so the id isn't in it (under `next dev` it is, and is checked).
 * The explorer scrolls to its strip itself (components/mistakes/MistakesExplorer.tsx).
 */
const CLIENT_FRAGMENTS: Readonly<Record<string, readonly string[]>> = { "/mistakes": ["featured"] };

/**
 * Figures from the human review that the site must not show (plan 13: no accuracy figure),
 * searched in each page's visible text.
 */
const REVIEW_FIGURES: readonly string[] = ["71%", "69%", "35 / 49", "35 of 49", "34 of 49", "7 in 10"];

/** The tool each example question is there for (the scripted chat picks tools by rule). */
const QUESTION_TOOLS: Readonly<Record<string, ToolName | null>> = {
  latest: "find_session",
  "monaco-2023": "get_race_summary",
  "last-race-mistakes": "find_mistakes",
  "abu-dhabi-gain": "compare_laps",
  "ham-lec-style": "compare_driving_styles",
  "baku-sc": "get_race_summary",
  "out-of-scope": null, // no tool at all
};
const CHART_TOOLS: ReadonlySet<string> = new Set([
  "get_race_summary",
  "find_mistakes",
  "explain_corner",
  "compare_laps",
  "compare_driving_styles",
]);
const QUESTIONS_PER_CONVERSATION = 8;
const CHEAP_QUESTION = "Who will win the 2027 championship?"; // the scripted chat calls no tool

/** `--chat one`: an answer that took longer than this can tell a stream from a buffered one. */
const STREAM_TELLS_MS = 1000;
/** ...and streamed if its events spread over at least this share of it (buffered: about 0). */
const STREAM_SPREAD = 0.25;

/** The response headers printed after the table (M7 plan 7.3; HSTS comes from the host). */
const SHOWN_HEADERS = [
  "content-security-policy",
  "strict-transport-security",
  "x-content-type-options",
  "x-frame-options",
  "referrer-policy",
  "permissions-policy",
];

type ChatOption = "fake" | "skip" | "one";
const CHAT_OPTIONS: readonly ChatOption[] = ["fake", "skip", "one"];

// ---- The table ------------------------------------------------------------------------------------

type Status = "ok" | "FAIL" | "skip";

interface Row {
  area: string;
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
    // fetch's TypeError says only "fetch failed"; the reason ("ECONNREFUSED") is on its cause.
    const cause = (error.cause as { code?: unknown } | undefined)?.code;
    return typeof cause === "string" ? `${error.message} (${cause})` : error.message;
  }
  return String(error);
}

/** Runs one check and adds its row: "ok" with the detail it returns, or "FAIL" with why. */
async function check(area: string, name: string, run: () => Promise<string | void>): Promise<boolean> {
  const started = performance.now();
  let status: Status = "ok";
  let detail = "";
  try {
    detail = (await run()) ?? "";
  } catch (error) {
    status = "FAIL";
    detail = describeError(error);
  }
  rows.push({ area, check: name, status, ms: String(Math.round(performance.now() - started)), detail });
  return status === "ok";
}

function skip(area: string, name: string, why: string): void {
  rows.push({ area, check: name, status: "skip", ms: "", detail: why });
}

function kb(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KB`;
}

// ---- Arguments ------------------------------------------------------------------------------------

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

// ---- The site ------------------------------------------------------------------------------------

interface Page {
  status: number;
  type: string;
  html: string;
  headers: Headers;
}

/** Every page fetched so far, by path (with its query), for the link check. */
const pages = new Map<string, Page>();

async function getPage(site: string, pagePath: string): Promise<Page> {
  const cached = pages.get(pagePath);
  if (cached) return cached;
  const response = await fetch(`${site}${pagePath}`, { signal: AbortSignal.timeout(PAGE_TIMEOUT_MS) });
  const type = response.headers.get("content-type") ?? "";
  const page = { status: response.status, type, html: await response.text(), headers: response.headers };
  pages.set(pagePath, page);
  return page;
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

// Inline elements join the text around them ("71<span>%</span>" reads "71%"); any other tag
// ends a word. React separates neighbouring text with empty comments, which go too.
const INLINE_TAG = /<\/?(?:a|abbr|b|code|em|i|kbd|mark|q|s|small|span|strong|sub|sup|time|u)\b[^>]*>/gi;

function textOf(fragment: string): string {
  const text = fragment.replace(/<!--[\s\S]*?-->/g, "").replace(INLINE_TAG, "").replace(/<[^>]*>/g, " ");
  return decodeEntities(text).replace(/\s+/g, " ").trim();
}

/**
 * The page's first h1, as text. `next dev` streams a Suspense boundary's content after its
 * fallback, so the chat's HTML holds the h1 twice; the first is the static shell's.
 */
function firstH1(html: string): string | null {
  const match = /<h1\b[^>]*>([\s\S]*?)<\/h1>/i.exec(html);
  return match ? textOf(match[1]) : null;
}

/** What a reader sees: the HTML without its scripts (the RSC payload repeats every string). */
function visibleText(html: string): string {
  return textOf(html.replace(/<(script|style|template)\b[\s\S]*?<\/\1>/gi, " "));
}

/** The page is HTML with this h1 and no figure from the human review. */
function checkPage(page: Page, h1: string): void {
  ensure(page.type.startsWith("text/html"), `content-type ${page.type || "(none)"}`);
  const found = firstH1(page.html);
  ensure(found === h1, found === null ? "no h1" : `h1 is "${found}"`);
  const text = visibleText(page.html);
  const shown = REVIEW_FIGURES.filter((figure) => text.includes(figure));
  ensure(shown.length === 0, `shows ${shown.map((s) => `"${s}"`).join(", ")} (the site quotes no accuracy)`);
}

function reportTitle(file: string): string {
  const title = parseReport(readFileSync(path.join(REPORT_DIR, file), "utf8")).title;
  if (!title) throw new Failure(`report/${file} has no # title`);
  return title.text;
}

/** An image route: 200, image/png, a PNG signature; its size. */
async function checkPng(site: string, pngPath: string): Promise<{ width: number; height: number; bytes: number }> {
  const response = await fetch(`${site}${pngPath}`, { signal: AbortSignal.timeout(PAGE_TIMEOUT_MS) });
  ensure(response.status === 200, `HTTP ${response.status}`);
  const type = response.headers.get("content-type") ?? "";
  ensure(type.startsWith("image/png"), `content-type ${type || "(none)"}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  const size = pngSize(bytes); // throws on anything that isn't a PNG
  return { ...size, bytes: bytes.byteLength };
}

/** The href of the first `<link rel="...">` in the head. */
function linkHref(html: string, rel: string): string | null {
  for (const tag of html.match(/<link\b[^>]*>/gi) ?? []) {
    if (new RegExp(`\\srel="${rel}"`).test(tag)) {
      const href = /\shref="([^"]*)"/.exec(tag);
      if (href) return decodeEntities(href[1]);
    }
  }
  return null;
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

/** The status a missing build-time file gets: on a deployment it means the prebuild failed. */
function missingWhy(response: Response | null, step: string): string {
  if (response?.status === 404) return `missing: the build's prebuild (${step}) didn't reach the API`;
  return `HTTP ${response?.status ?? "unreachable"}`;
}

async function checkSite(site: string, api: string): Promise<void> {
  const area = "site";
  const deployed = new URL(site).protocol === "https:";

  await check(area, "report manifest", async () => {
    ensure(!REPORTS.some((r) => r.slug === "m4-review"), "m4-review is published");
    ensure(EXCLUDED.includes("m7_chat_accuracy.md"), "m7_chat_accuracy.md isn't in EXCLUDED");
    ensure(REPORTS.length === REPORT_SLUGS, `${REPORTS.length} slugs, expected ${REPORT_SLUGS}`);
    return `${REPORTS.length} slugs, m4-review and m7-chat-accuracy left out`;
  });

  for (const { path: pagePath, h1 } of PAGES) {
    await check(area, `GET ${pagePath}`, async () => {
      const page = await getPage(site, pagePath);
      ensure(page.status === 200, `HTTP ${page.status}`);
      checkPage(page, h1);
      return h1;
    });
  }

  for (const report of REPORTS) {
    await check(area, `GET /report/${report.slug}`, async () => {
      const h1 = reportTitle(report.file);
      const page = await getPage(site, `/report/${report.slug}`);
      ensure(page.status === 200, `HTTP ${page.status}`);
      checkPage(page, h1);
      return h1;
    });
  }

  for (const missing of MISSING) {
    await check(area, `GET ${missing} is 404`, async () => {
      const page = await getPage(site, missing);
      ensure(page.status === 404, `HTTP ${page.status}`);
      checkPage(page, "No page here");
      return "No page here";
    });
  }

  await check(area, `figures (${FIGURES.length})`, async () => {
    for (const figure of FIGURES) {
      try {
        await checkPng(site, `/report/figures/${figure.file}`);
      } catch (error) {
        throw new Failure(`${figure.file}: ${describeError(error)}`);
      }
    }
    return FIGURES.map((f) => f.file).join(", ");
  });

  // The hero card's examples are a gitignored snapshot (make site-snapshots; on Vercel the
  // prebuild writes it): without the file the card falls back to the abstract circuit, so a
  // missing file is a skip, not a failure, except on a deployment.
  const hero = await fetch(`${site}${HERO_URL}`, { signal: AbortSignal.timeout(PAGE_TIMEOUT_MS) }).catch(() => null);
  if (hero?.status === 404 && !deployed) skip(area, `hero examples (${HERO_URL})`, "no snapshot; run make site-snapshots");
  else
    await check(area, `hero examples (${HERO_URL})`, async () => {
      ensure(hero !== null && hero.status === 200, missingWhy(hero, "snapshots.ts --tolerant"));
      const examples = parseHeroFile(await hero.json().catch(() => null));
      ensure(examples !== null, "the card can't draw the file");
      const ids = heroEntries().map((e) => e.id);
      ensure(examples.length === ids.length, `${examples.length} drawable examples, expected ${ids.length}`);
      ensure(examples[0].id === LANDING_CORNER.id, `starts on ${examples[0].id}, not the landing corner`);
      return `${examples.length} examples, starting on ${LANDING_CORNER.id}`;
    });

  // The saved answers' static copies (M7 plan 4.4), written at build time by `saved.ts pull`:
  // without them the chat reads the API's, so missing is a skip, except on a deployment.
  const savedName = `saved answers (${STATIC_SAVED_BASE}/index.json)`;
  const saved = await fetch(`${site}${STATIC_SAVED_BASE}/index.json`, { signal: AbortSignal.timeout(PAGE_TIMEOUT_MS) }).catch(
    () => null,
  );
  if (saved?.status === 404 && !deployed) skip(area, savedName, "no static copies; the chat reads the API's");
  else
    await check(area, savedName, async () => {
      ensure(saved !== null && saved.status === 200, missingWhy(saved, "saved.ts pull"));
      const index = parseSavedIndex(await saved.json().catch(() => null));
      ensure(index !== null, "not a saved-answer index");
      const listed = index.map((row) => row.id);
      const absent = QUESTIONS.map((q) => q.saved ?? q.id).filter((id) => !listed.includes(id));
      ensure(absent.length === 0, `${listed.length} listed, without ${absent.join(", ")}`);
      const modes = [...new Set(index.map((row) => row.mode))];
      // A deployed API serves only answers recorded with Claude (RACE_ENGINEER_SAVED_ALLOW_FAKE off).
      if (deployed) ensure(modes.join() === "anthropic", `recorded with ${modes.join(", ")}, not only Claude`);
      for (const row of index) {
        const url = `${site}${STATIC_SAVED_BASE}/${row.id}.json`;
        const response = await fetch(url, { signal: AbortSignal.timeout(PAGE_TIMEOUT_MS) });
        ensure(response.status === 200, `${row.id}.json: HTTP ${response.status}`);
        const answer = parseSavedAnswer(await response.json().catch(() => null));
        ensure(answer !== null && answer.id === row.id, `${row.id}.json fails the site's guard`);
        for (const event of answer.events) {
          if (event.type !== "tool_result" || event.chart === null) continue;
          ensure(TOOL_GUARDS[event.name as ToolName](event.chart.data), `${row.id}: ${event.name}'s chart fails its guard`);
        }
      }
      return `${index.length} answers (${modes.join(", ")}), each loads and passes its guards`;
    });

  // `next dev` serves its HMR client from every page; a production build doesn't.
  const home = pages.get("/");
  const dev = home !== undefined && home.html.includes("hmr-client");
  await check(area, `GET /dev/charts (${dev ? "dev: 200" : "production: 404"})`, async () => {
    const page = await getPage(site, "/dev/charts");
    if (dev) {
      ensure(page.status === 200, `HTTP ${page.status}`);
      const found = firstH1(page.html);
      ensure(found === "Chart gallery", `h1 is "${found}"`);
      return "Chart gallery";
    }
    ensure(page.status === 404, `HTTP ${page.status}`);
    return "not in a production build";
  });

  if (dev) skip(area, "security headers", "next dev sends none (M7 plan 7.3)");
  else
    await check(area, "security headers", async () => {
      ensure(home !== undefined && home.status === 200, "/ didn't load");
      const policy = home.headers.get("content-security-policy");
      ensure(policy, "no Content-Security-Policy");
      const directives = parsePolicy(policy);
      const connect = directives.get("connect-src") ?? [];
      const apiOrigin = new URL(api).origin;
      ensure(connect.includes(apiOrigin), `connect-src is "${connect.join(" ")}", without the API's origin ${apiOrigin}`);
      ensure(directives.get("default-src")?.join(" ") === "'self'", "default-src isn't 'self'");
      ensure(directives.get("frame-ancestors")?.join(" ") === "'none'", "frame-ancestors isn't 'none'");
      if (apiOrigin.startsWith("https:")) ensure(directives.has("upgrade-insecure-requests"), "no upgrade-insecure-requests");
      ensure(home.headers.get("x-content-type-options") === "nosniff", "X-Content-Type-Options isn't nosniff");
      ensure(home.headers.get("x-frame-options") === "DENY", "X-Frame-Options isn't DENY");
      ensure(home.headers.get("referrer-policy") !== null, "no Referrer-Policy");
      const hsts = home.headers.get("strict-transport-security");
      return `CSP with connect-src ${connect.join(" ")} · HSTS ${hsts ?? "none"} (printed below)`;
    });

  await check(area, "web app manifest and icons", async () => {
    ensure(home !== undefined && home.status === 200, "/ didn't load");
    const manifestHref = linkHref(home.html, "manifest");
    ensure(manifestHref === "/manifest.webmanifest", `<link rel="manifest"> is ${manifestHref ?? "missing"}`);
    const response = await fetch(`${site}${manifestHref}`, { signal: AbortSignal.timeout(PAGE_TIMEOUT_MS) });
    ensure(response.status === 200, `manifest: HTTP ${response.status}`);
    const manifest = (await response.json()) as {
      name?: unknown;
      short_name?: unknown;
      start_url?: unknown;
      display?: unknown;
      background_color?: unknown;
      theme_color?: unknown;
      icons?: { src: string; sizes: string; purpose?: string }[];
    };
    ensure(manifest.name === "Race Engineer AI", `name ${JSON.stringify(manifest.name)}`);
    ensure(manifest.short_name === "Race Engineer", `short_name ${JSON.stringify(manifest.short_name)}`);
    ensure(manifest.start_url === "/" && manifest.display === "standalone", "start_url or display");
    ensure(manifest.background_color === NAVY && manifest.theme_color === NAVY, "colours aren't the brand navy");
    const icons = manifest.icons ?? [];
    ensure(icons.some((i) => i.purpose === "maskable"), "no maskable icon");
    for (const icon of icons) {
      const size = await checkPng(site, icon.src);
      ensure(`${size.width}x${size.height}` === icon.sizes, `${icon.src} is ${size.width}x${size.height}, listed ${icon.sizes}`);
    }
    const apple = linkHref(home.html, "apple-touch-icon");
    ensure(apple !== null, 'no <link rel="apple-touch-icon">');
    const appleSize = await checkPng(site, apple);
    ensure(appleSize.width === 180 && appleSize.height === 180, `apple-touch-icon is ${appleSize.width}x${appleSize.height}`);
    return `${icons.length} icons (${icons.map((i) => i.sizes + (i.purpose === "maskable" ? " maskable" : "")).join(", ")}), apple 180x180`;
  });

  await check(area, "internal links and anchors", async () => {
    // Every link on the pages above (the chart gallery aside): its page answers 200, and a
    // #fragment names an id on it. Pages linked to but not yet fetched are fetched here.
    const sources = [...pages.entries()].filter(([p, page]) => page.status === 200 && p !== "/dev/charts");
    const targets = new Map<string, Set<string>>(); // path -> fragments ("" for none)
    for (const [from, page] of sources) {
      for (const match of page.html.matchAll(/<a\b[^>]*?\shref="([^"]*)"/gi)) {
        const href = decodeEntities(match[1]);
        let target: string;
        if (href.startsWith("#")) target = from.split("?")[0] + href;
        else if (href.startsWith("/") && !href.startsWith("//") && !href.startsWith("/_next/")) target = href;
        else continue;
        const [beforeHash, fragment = ""] = target.split("#");
        const linkPath = beforeHash.split("?")[0] || "/";
        const set = targets.get(linkPath) ?? new Set<string>();
        set.add(fragment);
        targets.set(linkPath, set);
      }
    }
    const broken: string[] = [];
    for (const [linkPath, fragments] of targets) {
      if (linkPath.startsWith("/report/figures/") || linkPath.startsWith("/pwa-icon/")) {
        try {
          await checkPng(site, linkPath);
        } catch (error) {
          broken.push(`${linkPath} (${describeError(error)})`);
        }
        continue;
      }
      const page = await getPage(site, linkPath);
      if (page.status !== 200) {
        broken.push(`${linkPath} (HTTP ${page.status})`);
        continue;
      }
      for (const fragment of fragments) {
        if (!fragment || page.html.includes(`id="${fragment}"`)) continue;
        if (dev || !CLIENT_FRAGMENTS[linkPath]?.includes(fragment)) broken.push(`${linkPath}#${fragment} (no such id)`);
      }
    }
    ensure(broken.length === 0, `${broken.length} broken: ${broken.slice(0, 6).join("; ")}`);
    const anchors = [...targets.values()].reduce((n, set) => n + [...set].filter(Boolean).length, 0);
    return `${targets.size} paths and ${anchors} anchors from ${sources.length} pages`;
  });
}

// ---- The API --------------------------------------------------------------------------------------

/** The site's request options against `api`, with a timeout, recording each answer's size. */
function measured(api: string, sizes: number[], timeoutMs = TOOL_TIMEOUT_MS): RequestOptions {
  const measuringFetch: typeof fetch = async (input, init) => {
    const response = await fetch(input, init);
    const body = await response.arrayBuffer();
    sizes.push(body.byteLength);
    return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
  };
  return { baseUrl: api, fetch: measuringFetch, signal: AbortSignal.timeout(timeoutMs) };
}

function last(sizes: number[]): number {
  return sizes[sizes.length - 1] ?? 0;
}

async function checkApi(site: string, api: string): Promise<string | null> {
  const area = "api";
  // What the checks below learn, for the ones after them (assigned inside the check callbacks).
  const found: { chatMode: string | null; catalog: SessionCatalog | null } = { chatMode: null, catalog: null };

  await check(area, "GET /api/health", async () => {
    // The site's own check (lib/api/health.ts), without its startup retries.
    const health = await loadHealth(AbortSignal.timeout(TOOL_TIMEOUT_MS), { baseUrl: api, retryMs: [] });
    found.chatMode = health.chat.mode;
    ensure(health.status === "ok", `degraded: ${health.problems.join("; ")}`);
    return `ok · chat.mode ${health.chat.mode} · ${health.data.sessions} sessions · latest ${health.data.latest ?? "none"}`;
  });

  await check(area, "GET /api/catalog/sessions", async () => {
    const sizes: number[] = [];
    const catalog = await getSessionCatalog(measured(api, sizes));
    found.catalog = catalog;
    const weekends = catalog.seasons.flatMap((s) => s.weekends);
    const sessions = weekends.reduce((n, w) => n + w.sessions.length, 0);
    ensure(sessions === SESSIONS && catalog.coverage.sessions === SESSIONS, `${sessions} sessions, expected ${SESSIONS}`);
    ensure(weekends.length === WEEKENDS, `${weekends.length} weekends, expected ${WEEKENDS}`);
    ensure(catalog.results_current, "results_current is false: find_mistakes would answer 503");
    return `${sessions} sessions, ${weekends.length} weekends, ${catalog.seasons.length} seasons · ${kb(last(sizes))}`;
  });

  // The explorer's default session with no URL (plan 7.3: on real data, 2026 Azerbaijan R).
  const selection: Selection | null = found.catalog
    ? resolveSelection(parseMistakesQuery(new URLSearchParams()), found.catalog)
    : null;
  const named = selection ? `${selection.year} ${selection.event} ${selection.session}` : "";

  if (selection) {
    await check(area, "GET /api/catalog/drivers", async () => {
      const drivers = await getSessionDrivers(selection, measured(api, []));
      ensure(drivers.drivers.length > 0, "no drivers");
      const order = drivers.drivers.map((d) => `${d.team}\u0000${d.driver}`);
      ensure(order.every((key, i) => i === 0 || order[i - 1] <= key), "not sorted by team, then code");
      return `${named}: ${drivers.drivers.length} drivers`;
    });
  } else {
    skip(area, "GET /api/catalog/drivers", "no default session (the session catalog failed)");
  }

  await check(area, "GET /api/catalog/styles", async () => {
    const newest = await getStyleCatalog(null, measured(api, []));
    ensure(newest.year === newest.years[0], `default year ${newest.year}, newest ${newest.years[0]}`);
    const season = await getStyleCatalog(2025, measured(api, []));
    ensure(season.pairs.length === PAIRS_2025, `${season.pairs.length} pairs in 2025, expected ${PAIRS_2025}`);
    return `years ${newest.years.join(", ")} · default ${newest.year} · ${season.pairs.length} pairs in 2025`;
  });

  for (const [index, entry] of FEATURED.entries()) {
    const params = cornerParams(entry);
    await check(area, `featured ${index} ${entry.id}`, async () => {
      const sizes: number[] = [];
      if (!params) {
        const { event, year, session, driver } = entry.args;
        const { data } = await callTool("find_mistakes", { event, year, session, driver }, measured(api, sizes));
        if (entry.kind === "nothing-flagged") {
          ensure(data.mistakes.length === 0, `${data.mistakes.length} mistakes listed, expected none`);
        }
        const flagged = `${data.flagged} of ${data.scored} scored corners flagged`;
        return `find_mistakes · ${data.mistakes.length} rows · ${flagged} · ${kb(last(sizes))}`;
      }
      const { data } = await callTool("explain_corner", params, measured(api, sizes));
      ensure(
        data.driver === params.driver && data.lap_number === params.lap && data.turn.toUpperCase() === params.corner.toUpperCase(),
        `answered ${data.driver} lap ${data.lap_number} T${data.turn}`,
      );
      if (entry.kind === "flagged") ensure(data.flagged === true, `flagged is ${String(data.flagged)}`);
      if (entry === LANDING_CORNER) ensure(data.replay != null, "the landing corner has no replay");
      const replay = data.replay != null ? "replay" : "no replay";
      return `explain_corner · ${data.type} · flagged ${String(data.flagged)} · ${replay} · ${kb(last(sizes))}`;
    });
  }

  for (const example of STYLE_EXAMPLES) {
    await check(area, `style example ${example.id}`, async () => {
      const sizes: number[] = [];
      const params = { driver_a: example.a, driver_b: example.b, year: example.year };
      const { data } = await callTool("compare_driving_styles", params, measured(api, sizes));
      ensure(data.teammates === !example.crossTeam, `teammates is ${String(data.teammates)} (${data.teams.join(" / ")})`);
      return `${data.teams.join(" / ")} · ${data.events} events · ${kb(last(sizes))}`;
    });
  }

  if (selection) {
    await check(area, "find_mistakes limit 50 (default session)", async () => {
      const sizes: number[] = [];
      const { event, year, session } = selection;
      const { data } = await callTool("find_mistakes", { event, year, session, limit: 50 }, measured(api, sizes));
      ensure(data.mistakes.length > 0 && data.mistakes.length <= 50, `${data.mistakes.length} rows`);
      return `${named} · ${data.mistakes.length} rows (${data.distinct_mistakes} distinct mistakes) · ${kb(last(sizes))}`;
    });
  } else {
    skip(area, "find_mistakes limit 50 (default session)", "no default session (the session catalog failed)");
  }

  await check(area, "compare_laps NOR/PIA 2025 Abu Dhabi Q", async () => {
    const sizes: number[] = [];
    const params = { event: "Abu Dhabi Grand Prix", driver_a: "NOR", driver_b: "PIA", year: 2025, session: "Q" as const };
    const { data } = await callTool("compare_laps", params, measured(api, sizes));
    return `gap ${data.gap_s.toFixed(3)} s · ${data.corners.length} corners · ${kb(last(sizes))}`;
  });

  // Every featured card opens on /mistakes (the page itself draws in the browser).
  await check(area, "featured cards open /mistakes", async () => {
    for (const entry of FEATURED) {
      const href = featuredHref(entry);
      const page = await getPage(site, href);
      ensure(page.status === 200, `${href}: HTTP ${page.status}`);
    }
    return `${FEATURED.length} links`;
  });

  return found.chatMode;
}

// ---- The chat ------------------------------------------------------------------------------------

interface Answer {
  events: ChatEvent[];
  done: DoneEvent;
  /** When each event arrived, in ms after the request was sent. */
  arrived: number[];
}

/**
 * Asks one question the way the site does (POST, content-type only) and reads the stream with
 * the site's parser. Throws a Failure when an event doesn't parse or the order is wrong.
 */
async function ask(api: string, request: ChatRequest): Promise<Answer> {
  const sent = performance.now();
  const response = await fetch(`${api}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
    signal: AbortSignal.timeout(CHAT_TIMEOUT_MS),
  });
  if (!response.ok) throw await errorFromResponse(response);
  const type = response.headers.get("content-type") ?? "";
  ensure(type.startsWith("text/event-stream") && response.body !== null, `not an event stream: ${type || "(none)"}`);

  const events: ChatEvent[] = [];
  const arrived: number[] = [];
  for await (const { event, data } of parseSSE(response.body)) {
    ensure(isChatEventType(event), `an event the site doesn't know: "${event}"`);
    const parsed = parseChatEvent(event, data);
    ensure(parsed !== null, `a "${event}" event the site can't read: ${data.slice(0, 120)}`);
    events.push(parsed);
    arrived.push(performance.now() - sent);
  }
  return { events, done: checkOrder(events), arrived };
}

/** `done` once and last; every tool_result follows its tool_call, and every call gets a result. */
function checkOrder(events: ChatEvent[]): DoneEvent {
  const done = events[events.length - 1];
  ensure(done?.type === "done", `the stream ended with ${done ? `"${done.type}"` : "nothing"}, not "done"`);
  ensure(events.filter((e) => e.type === "done").length === 1, "more than one done");
  const open = new Set<string>();
  const answered = new Set<string>();
  for (const event of events) {
    if (event.type === "tool_call") {
      ensure(!open.has(event.id) && !answered.has(event.id), `tool_call ${event.id} twice`);
      open.add(event.id);
    } else if (event.type === "tool_result") {
      ensure(open.has(event.id), `tool_result ${event.id} without its tool_call`);
      open.delete(event.id);
      answered.add(event.id);
    }
  }
  ensure(open.size === 0, `tool calls without a result: ${[...open].join(", ")}`);
  return done;
}

/** Every chart the answer carries: the site draws it, its guard passes, its explorer link opens. */
async function checkCharts(site: string, events: ChatEvent[]): Promise<string[]> {
  const bundles: string[] = [];
  for (const event of events) {
    if (event.type !== "tool_result" || event.chart === null) continue;
    const { name, chart } = event;
    ensure(CHART_TOOLS.has(name), `${name} sent a chart`);
    ensure(isChartName(chart.bundle), `${name}'s chart "${chart.bundle}" isn't one the site draws`);
    ensure(TOOL_GUARDS[name as ToolName](chart.data), `${name}'s chart data fails the site's guard`);
    const link = explorerLink(chart);
    ensure(link !== null, `${name}'s chart has no explorer link`);
    const page = await getPage(site, link.href);
    ensure(page.status === 200, `"${link.label}" (${link.href}): HTTP ${page.status}`);
    bundles.push(chart.bundle);
  }
  return bundles;
}

function toolsCalled(events: ChatEvent[]): string[] {
  return events.flatMap((e) => (e.type === "tool_call" ? [e.name] : []));
}

/** The first pre-stream refusal from the site's chat client, or a Failure when it answers. */
async function refusedBefore(api: string, request: ChatRequest): Promise<ApiError> {
  const stream = streamChat(request, { apiUrl: api, signal: AbortSignal.timeout(CHAT_TIMEOUT_MS) });
  try {
    await stream.next();
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  await stream.return(undefined);
  throw new Failure("the server answered instead of refusing");
}

/** The history with one character of the first question changed (still valid JSON). */
function tamper(history: unknown[], question: string): unknown[] {
  const text = JSON.stringify(history);
  const at = text.indexOf(question.slice(0, 12));
  ensure(at >= 0, "the question isn't in the history");
  const swapped = text[at] === "X" ? "Y" : "X";
  return JSON.parse(text.slice(0, at) + swapped + text.slice(at + 1)) as unknown[];
}

/**
 * `--chat one`: the "latest" question, once, of whichever chat the API runs. A stream that a proxy
 * buffers arrives all at once at the end; a streamed one spreads its events over the answer.
 */
async function checkOneQuestion(api: string, mode: string | null): Promise<void> {
  const area = "chat";
  const q = QUESTIONS.find((x) => x.id === "latest");
  const name = `ask "latest" once, streamed`;
  if (mode === null || mode === "off" || !q) {
    skip(area, name, mode === "off" ? "the chat is off" : mode === null ? "the health check failed" : 'no "latest" question');
    return;
  }
  await check(area, name, async () => {
    const { events, done, arrived } = await ask(api, { message: q.text, history: [], signature: null });
    const problem = events.find((e) => e.type === "error" || e.type === "refusal");
    ensure(!problem, `${problem?.type}: ${problem && "message" in problem ? problem.message : ""}`);
    const tools = toolsCalled(events);
    const answered = events.some((e) => e.type === "tool_result" && !e.is_error && ["find_session", "list_sessions"].includes(e.name));
    ensure(answered, `called ${tools.join(", ") || "no tool"}, expected find_session or list_sessions to answer`);
    const text = events.reduce((s, e) => (e.type === "text" ? s + e.delta : s), "");
    ensure(text.trim() !== "", "no answer text");
    const total = arrived[arrived.length - 1];
    const spread = total - arrived[0];
    const timing = `${events.length} events over ${(total / 1000).toFixed(1)} s, the first after ${Math.round(arrived[0])} ms`;
    if (total >= STREAM_TELLS_MS) {
      ensure(spread >= STREAM_SPREAD * total, `${timing}: they came all at once at the end, so something buffers the stream`);
    }
    const tell = total >= STREAM_TELLS_MS ? "streamed" : "too quick to tell streamed from buffered";
    const cost = done.cost_usd === null ? "" : ` · $${done.cost_usd.toFixed(4)}`;
    return `${timing} (${tell}) · model ${done.model}${cost}`;
  });
}

async function checkChat(site: string, api: string, mode: string | null, chat: ChatOption): Promise<void> {
  const area = "chat";
  if (chat === "skip") {
    skip(area, "questions", "--chat skip");
    return;
  }
  if (chat === "one") {
    await checkOneQuestion(api, mode);
    return;
  }
  if (mode !== "fake") {
    const why =
      mode === null
        ? "the health check failed"
        : `chat.mode is "${mode}": these checks ask about 18 questions, so they run only against the scripted chat (make api-fake)`;
    skip(area, "example questions, #refuse, invalid_history, turn_limit", why);
    return;
  }

  let first: { question: string; done: DoneEvent } | null = null;
  for (const q of QUESTIONS) {
    await check(area, `ask "${q.id}"`, async () => {
      const { events, done } = await ask(api, { message: q.text, history: [], signature: null });
      first ??= { question: q.text, done };
      ensure(done.model === "scripted", `model "${done.model}"`);
      ensure(done.questions_left === QUESTIONS_PER_CONVERSATION - 1, `questions_left ${done.questions_left}`);
      const problem = events.find((e) => e.type === "error" || e.type === "refusal");
      ensure(!problem, `${problem?.type}: ${problem && "message" in problem ? problem.message : ""}`);
      const tools = toolsCalled(events);
      const bundles = await checkCharts(site, events);
      if (Object.hasOwn(QUESTION_TOOLS, q.id)) {
        const want = QUESTION_TOOLS[q.id];
        if (want === null) ensure(tools.length === 0, `called ${tools.join(", ")}, expected no tool`);
        else {
          ensure(tools.includes(want), `called ${tools.join(", ") || "no tool"}, expected ${want}`);
          const ok = events.some((e) => e.type === "tool_result" && e.name === want && !e.is_error);
          ensure(ok, `${want} answered with an error`);
          if (CHART_TOOLS.has(want)) ensure(bundles.length > 0, `${want} sent no chart`);
        }
      }
      const text = events.reduce((s, e) => (e.type === "text" ? s + e.delta : s), "");
      ensure(text.trim() !== "", "no answer text");
      const charts = bundles.length ? ` · charts ${bundles.join(", ")}` : "";
      return `${events.length} events · tools ${tools.join(", ") || "none"}${charts}`;
    });
  }

  await check(area, "Show telemetry on a chat find-mistakes card", async () => {
    // What the find-mistakes card does on "Show telemetry": explain_corner over REST, with the
    // arguments built from the payload (lib/chat/links.ts), not through the model.
    const q = QUESTIONS.find((x) => x.id === "last-race-mistakes");
    ensure(q, 'no "last-race-mistakes" question');
    const { events } = await ask(api, { message: q.text, history: [], signature: null });
    const result = events.find((e) => e.type === "tool_result" && e.chart?.bundle === "find-mistakes");
    ensure(result?.type === "tool_result" && result.chart, "no find-mistakes chart");
    const list = result.chart.data as { mistakes?: unknown[] };
    const row = list.mistakes?.[0];
    ensure(row !== undefined, "the list is empty");
    const args = explainCornerArgs(list, row);
    ensure(args, "no explain_corner arguments from the first row");
    const { data } = await callTool("explain_corner", args, measured(api, []));
    return `${data.driver} lap ${data.lap_number} T${data.turn} · ${data.type}`;
  });

  await check(area, "#refuse gives refusal, then done", async () => {
    const { events } = await ask(api, { message: "#refuse Tell me something you won't.", history: [], signature: null });
    const refusal = events.findIndex((e) => e.type === "refusal");
    ensure(refusal >= 0, `no refusal: ${events.map((e) => e.type).join(", ")}`);
    ensure(refusal === events.length - 2, `events after the refusal: ${events.slice(refusal + 1).map((e) => e.type).join(", ")}`);
    ensure(toolsCalled(events).length === 0, "a tool ran");
    return events.map((e) => e.type).join(", ");
  });

  await check(area, "one changed byte gives 400 invalid_history", async () => {
    ensure(first, "no answered question to replay");
    const history = tamper(first.done.history, first.question);
    const error = await refusedBefore(api, { message: CHEAP_QUESTION, history, signature: first.done.signature });
    ensure(error.status === 400 && error.code === "invalid_history", describeError(error));
    return describeError(error);
  });

  await check(area, `a ninth question gives 409 turn_limit`, async () => {
    let history: unknown[] = [];
    let signature: string | null = null;
    for (let n = 1; n <= QUESTIONS_PER_CONVERSATION; n++) {
      const { done } = await ask(api, { message: CHEAP_QUESTION, history, signature });
      ensure(done.questions_left === QUESTIONS_PER_CONVERSATION - n, `question ${n}: questions_left ${done.questions_left}`);
      history = done.history;
      signature = done.signature;
    }
    const error = await refusedBefore(api, { message: CHEAP_QUESTION, history, signature });
    ensure(error.status === 409 && error.code === "turn_limit", describeError(error));
    return `8 answered, then ${describeError(error)}`;
  });
}

// ---- CORS ----------------------------------------------------------------------------------------

async function checkCors(site: string, api: string, siteOrigin: string): Promise<void> {
  const area = "cors";
  const dev = [new URL(site).origin, "http://localhost:3000", "http://127.0.0.1:3000"];
  const origins = siteOrigin ? [new URL(siteOrigin).origin] : [...new Set(dev)];
  for (const origin of origins) {
    await check(area, `from ${origin}`, async () => {
      const preflight = await fetch(`${api}/api/chat`, {
        method: "OPTIONS",
        headers: { origin, "access-control-request-method": "POST", "access-control-request-headers": "content-type" },
        signal: AbortSignal.timeout(TOOL_TIMEOUT_MS),
      });
      ensure(preflight.status === 200, `preflight for POST /api/chat: HTTP ${preflight.status}`);
      ensure(preflight.headers.get("access-control-allow-origin") === origin, "preflight: origin not allowed");
      const methods = preflight.headers.get("access-control-allow-methods") ?? "";
      ensure(/\bPOST\b/.test(methods), `preflight: methods "${methods}"`);
      const headers = (preflight.headers.get("access-control-allow-headers") ?? "").toLowerCase();
      ensure(/\bcontent-type\b/.test(headers), `preflight: headers "${headers}"`);
      const get = await fetch(`${api}/api/catalog/sessions`, { headers: { origin }, signal: AbortSignal.timeout(TOOL_TIMEOUT_MS) });
      await get.arrayBuffer();
      ensure(get.headers.get("access-control-allow-origin") === origin, "GET /api/catalog/sessions: origin not allowed");
      return "preflight POST /api/chat and GET /api/catalog/sessions allowed";
    });
  }
  await check(area, "from an unlisted origin", async () => {
    const origin = "http://example.com";
    const preflight = await fetch(`${api}/api/chat`, {
      method: "OPTIONS",
      headers: { origin, "access-control-request-method": "POST", "access-control-request-headers": "content-type" },
      signal: AbortSignal.timeout(TOOL_TIMEOUT_MS),
    });
    await preflight.arrayBuffer();
    ensure(preflight.headers.get("access-control-allow-origin") === null, "allowed");
    return `refused (HTTP ${preflight.status})`;
  });
}

// ---- Main ----------------------------------------------------------------------------------------

function printTable(site: string, api: string): void {
  const head = ["area", "check", "result", "ms", "detail"];
  const table = rows.map((r) => [r.area, r.check, r.status, r.ms, r.detail]);
  const widths = head.map((h, k) => Math.max(h.length, ...table.map((t) => t[k].length)));
  const line = (cells: string[]) =>
    cells
      .map((c, k) => (k === cells.length - 1 ? c : k === 3 ? c.padStart(widths[k]) : c.padEnd(widths[k])))
      .join("  ")
      .trimEnd();
  console.log(`Site smoke: site ${site}, API ${api}`);
  console.log(line(head));
  for (const t of table) console.log(line(t));
  const count = (s: Status) => rows.filter((r) => r.status === s).length;
  console.log(`${rows.length} checks: ${count("ok")} ok, ${count("FAIL")} failed, ${count("skip")} skipped`);
}

/** The home page's security headers, as the site sent them (M7 plan 7.3). */
function printHeaders(): void {
  const home = pages.get("/");
  if (!home) return;
  console.log("\nResponse headers of GET /:");
  for (const name of SHOWN_HEADERS) console.log(`  ${name}: ${home.headers.get(name) ?? "(none)"}`);
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2);
  const site = option(argv, "--site", "http://localhost:3000");
  const api = option(argv, "--api", process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000");
  const origin = option(argv, "--origin", "");
  const chat = option(argv, "--chat", "fake");
  if (!CHAT_OPTIONS.includes(chat as ChatOption)) {
    console.error(`site-smoke: --chat is one of ${CHAT_OPTIONS.join(", ")}, not "${chat}"`);
    return 2;
  }
  for (const [name, value] of [["--site", site], ["--api", api], ["--origin", origin]]) {
    if (value !== "" && !isHttpUrl(value)) {
      console.error(`site-smoke: ${name} is an http(s) URL, not "${value}"`);
      return 2;
    }
  }
  await checkSite(site, api);
  const mode = await checkApi(site, api);
  await checkChat(site, api, mode, chat as ChatOption);
  await checkCors(site, api, origin);
  printTable(site, api);
  printHeaders();
  return rows.some((r) => r.status === "FAIL") ? 1 : 0;
}

process.exitCode = await main();
