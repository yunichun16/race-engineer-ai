// Saved example answers (plan 4.1, 4.4): the chat's answers to the example questions, recorded
// by scripts/saved.ts and shown "Saved answer from {date}" while the live chat is rate-limited,
// capped, paused or off.
//
// - The site reads the static copies first (/saved/index.json and /saved/{id}.json, which
//   `saved.ts pull` writes into public/saved at build time), then the API's (GET /api/saved and
//   /api/saved/{id}). Both serve the same JSON; neither is in the repository, because the charts
//   carry data drawn from car positions.
// - A recording is checked whole, and each event goes through the site's own parser
//   (parseChatEvent), so a saved answer is drawn exactly as the live answer was. One that ended
//   with an error, a retry or a refusal, or without `done`, isn't shown.
// - The index comes back in the order of the example questions (content/questions.ts), then any
//   other recordings as listed.
// - What loads is kept for the page's life (one request per file at most); a failure, or an
//   empty index, is asked again the next time.
//
// The page loads this module lazily, with the saved-answer chips, so /chat's first load doesn't
// carry it.

import { QUESTIONS } from "../../content/questions.ts";
import { API_URL } from "../env.ts";
import { isChatEventType, parseChatEvent } from "./sse.ts";
import type { ChatEvent } from "./types.ts";

/** One recording (data/saved/{id}.json), as the site uses it. */
export interface SavedAnswer {
  v: 1;
  id: string;
  question: string;
  recorded_at: string; // ISO 8601, UTC
  mode: "anthropic" | "fake";
  model: string;
  prompt_version: string; // the part of done.signature before the dot
  data: { latest: string | null; results_run: string | null }; // health's, when it was recorded
  events: ChatEvent[]; // the site's own events; done.history and done.signature emptied
}

/** One row of the index: {"answers": [...]}. */
export interface SavedIndexEntry {
  id: string;
  question: string;
  recorded_at: string;
  mode: string;
}

/** A recording's id, as the API accepts it (routes/saved.py). */
export const SAVED_ID = /^[a-z0-9-]{1,40}$/;

/** Where the build puts the static copies (public/saved). */
export const STATIC_SAVED_BASE = "/saved";

// Events that mean the recording didn't answer cleanly (scripts/saved.ts keeps none of them).
const BROKEN: ReadonlySet<string> = new Set(["error", "retry", "refusal"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isTime(value: unknown): value is string {
  return isString(value) && Number.isFinite(Date.parse(value));
}

function stringOrNull(value: unknown): string | null {
  return isString(value) ? value : null;
}

/**
 * The events of a recording through the site's parser: null when one this site knows doesn't
 * parse, when one is an error, retry or refusal, or when `done` isn't there once and last.
 * Events this site doesn't know are skipped, as in a live stream.
 */
export function parseSavedEvents(value: unknown): ChatEvent[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const events: ChatEvent[] = [];
  for (const raw of value) {
    if (!isRecord(raw) || !isString(raw.type)) return null;
    const event = parseChatEvent(raw.type, JSON.stringify(raw));
    if (event === null) {
      if (isChatEventType(raw.type)) return null; // an event this site knows, malformed
      continue; // a newer recorder's event
    }
    if (BROKEN.has(event.type)) return null;
    events.push(event);
  }
  const doneAt = events.findIndex((event) => event.type === "done");
  return doneAt === events.length - 1 ? events : null;
}

/** A recording, checked and with its events parsed; null when it isn't one. */
export function parseSavedAnswer(value: unknown): SavedAnswer | null {
  if (!isRecord(value) || value.v !== 1) return null;
  const { id, question, recorded_at, mode, model, prompt_version, data } = value;
  if (!isString(id) || !SAVED_ID.test(id)) return null;
  if (!isString(question) || question.trim() === "" || !isTime(recorded_at)) return null;
  if (mode !== "anthropic" && mode !== "fake") return null;
  if (!isString(model) || !isString(prompt_version)) return null;
  const events = parseSavedEvents(value.events);
  if (events === null) return null;
  const info = isRecord(data) ? data : {};
  return {
    v: 1,
    id,
    question,
    recorded_at,
    mode,
    model,
    prompt_version,
    data: { latest: stringOrNull(info.latest), results_run: stringOrNull(info.results_run) },
    events,
  };
}

export function isSavedAnswer(value: unknown): value is SavedAnswer {
  return parseSavedAnswer(value) !== null;
}

/** The example questions' order, for sorting the index. */
function questionRank(id: string): number {
  const at = QUESTIONS.findIndex((q) => (q.saved ?? q.id) === id);
  return at === -1 ? QUESTIONS.length : at;
}

/** The index's rows that fit, each id once, in the example questions' order; null when the body
 *  isn't an index at all. */
export function parseSavedIndex(value: unknown): SavedIndexEntry[] | null {
  if (!isRecord(value) || !Array.isArray(value.answers)) return null;
  const seen = new Set<string>();
  const rows: SavedIndexEntry[] = [];
  for (const row of value.answers) {
    if (!isRecord(row)) continue;
    const { id, question, recorded_at, mode } = row;
    if (!isString(id) || !SAVED_ID.test(id) || seen.has(id)) continue;
    if (!isString(question) || !isTime(recorded_at) || !isString(mode)) continue;
    seen.add(id);
    rows.push({ id, question, recorded_at, mode });
  }
  return rows
    .map((row, i) => ({ row, i }))
    .sort((a, b) => questionRank(a.row.id) - questionRank(b.row.id) || a.i - b.i)
    .map(({ row }) => row);
}

/** The recording for one of the example questions, when the index has it: a chip's text (the
 *  landing page's, or one a limit refused) can show its saved answer at once. */
export function savedIdFor(question: string, index: readonly SavedIndexEntry[]): string | null {
  const text = question.trim();
  const example = QUESTIONS.find((q) => q.text === text);
  const id = example ? (example.saved ?? null) : null;
  return id !== null && index.some((row) => row.id === id) ? id : null;
}

// ---- Loading ----

export interface SavedCache {
  index: Promise<SavedIndexEntry[]> | null;
  answers: Map<string, Promise<SavedAnswer | null>>;
  // Where the index came from. The build copies the index and the recordings together, so when
  // the index had to come from the API the static recordings aren't there either, and asking for
  // each would only add a 404 (and a round trip) per chip.
  source: "static" | "api" | null;
}

export function createSavedCache(): SavedCache {
  return { index: null, answers: new Map(), source: null };
}

const pageCache = createSavedCache();

export interface SavedOptions {
  fetch?: typeof fetch; // called as a plain function
  apiUrl?: string; // default NEXT_PUBLIC_API_URL
  staticBase?: string; // default "/saved" on this site
  cache?: SavedCache; // default: one for the page's life
  signal?: AbortSignal;
}

/** GET `url` as JSON; null for any failure (not found, not reachable, not JSON). */
async function getBody(url: string, options: SavedOptions): Promise<unknown> {
  const send = options.fetch ?? globalThis.fetch;
  try {
    const response = await send(url, { signal: options.signal });
    if (!response.ok) {
      await response.body?.cancel().catch(() => {});
      return null;
    }
    return JSON.parse(await response.text()) as unknown;
  } catch {
    return null;
  }
}

function bases(options: SavedOptions): { local: string; api: string } {
  return {
    local: (options.staticBase ?? STATIC_SAVED_BASE).replace(/\/+$/, ""),
    api: `${(options.apiUrl ?? API_URL).replace(/\/+$/, "")}/api/saved`,
  };
}

/** The saved answers there are: the static index, else the API's; [] when neither answers. */
export function loadSavedIndex(options: SavedOptions = {}): Promise<SavedIndexEntry[]> {
  const cache = options.cache ?? pageCache;
  if (cache.index) return cache.index;
  const { local, api } = bases(options);
  const load = (async () => {
    const fromStatic = parseSavedIndex(await getBody(`${local}/index.json`, options));
    if (fromStatic !== null && fromStatic.length > 0) {
      cache.source = "static";
      return fromStatic;
    }
    const fromApi = parseSavedIndex(await getBody(api, options)) ?? [];
    if (fromApi.length > 0) cache.source = "api";
    return fromApi;
  })();
  cache.index = load;
  // Nothing found is asked again next time (the API may have been starting).
  void load.then((rows) => {
    if (rows.length === 0 && cache.index === load) cache.index = null;
  });
  return load;
}

/** One recording: the static copy, else the API's; null when neither has a valid one. The static
 *  copy isn't asked for once the index has come from the API (SavedCache.source). */
export function loadSaved(id: string, options: SavedOptions = {}): Promise<SavedAnswer | null> {
  if (!SAVED_ID.test(id)) return Promise.resolve(null);
  const cache = options.cache ?? pageCache;
  const cached = cache.answers.get(id);
  if (cached) return cached;
  const { local, api } = bases(options);
  const fits = (answer: SavedAnswer | null) => (answer !== null && answer.id === id ? answer : null);
  const load = (async () => {
    if (cache.source !== "api") {
      const fromStatic = fits(parseSavedAnswer(await getBody(`${local}/${id}.json`, options)));
      if (fromStatic !== null) return fromStatic;
    }
    return fits(parseSavedAnswer(await getBody(`${api}/${id}`, options)));
  })();
  cache.answers.set(id, load);
  void load.then((answer) => {
    if (answer === null && cache.answers.get(id) === load) cache.answers.delete(id);
  });
  return load;
}
