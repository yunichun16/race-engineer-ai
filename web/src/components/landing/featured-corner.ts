/**
 * The landing example's data (spec h.2): one module-level resource that the hero readout, the
 * corner story and the real chart share, so the corner is fetched once.
 *
 * Order: the saved snapshot `/snapshots/<id>.json` (written by scripts/snapshots.ts; a 404 there
 * is expected when none were made), then the API's explain_corner, then nothing. Nothing is a
 * normal outcome, not an error: the page is complete without it (the story draws its
 * illustration and the numbers stay out), and the landing never shows "API down".
 *
 * It starts once, from whichever comes first: the browser going idle after load
 * (`startWhenIdle`), or the story coming within 400 px of the viewport (`startFeaturedCorner`).
 * Pure apart from the fetches, which a test can replace.
 */

import { isCornerChart, type CornerChart } from "../../charts/explain-corner.ts";
import { callTool, type RequestOptions } from "../../lib/api/client.ts";
import { cornerParams, LANDING_CORNER, type FeaturedCorner } from "../../content/featured.ts";

export type FeaturedSource = "snapshot" | "live";

export interface FeaturedPayload {
  /** The tool's summary: the chart's full text version. */
  summary: string;
  data: CornerChart;
  source: FeaturedSource;
}

export type FeaturedState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; payload: FeaturedPayload }
  | { status: "missing" }; // no snapshot and no API: the page shows what it has without numbers

export interface LoadOptions {
  /** A stand-in for the global fetch (tests). */
  fetch?: typeof fetch;
  /** Another API server than NEXT_PUBLIC_API_URL. */
  baseUrl?: string;
  /** Cancels both fetches. Without it, the live call gives up after `timeoutMs`. */
  signal?: AbortSignal;
  /** How long the live call may take when no `signal` is given (tests shorten it). */
  timeoutMs?: number;
}

/**
 * How long the live explain_corner call may take before the page settles for the illustration
 * and the offline card, so a server that accepts the connection but never answers can't leave
 * the example loading for ever. Generous: a cold call loads a whole session first.
 */
export const LIVE_TIMEOUT_MS = 30_000;

/** Where an entry's snapshot is served (public/snapshots/). */
export function snapshotUrl(entry: FeaturedCorner): string {
  return `/snapshots/${entry.id}.json`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The snapshot's corner when it is there and has the API's shape, else null. */
async function fromSnapshot(entry: FeaturedCorner, send: typeof fetch, signal?: AbortSignal): Promise<FeaturedPayload | null> {
  try {
    const response = await send(snapshotUrl(entry), { signal });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    if (!isRecord(body) || typeof body.summary !== "string" || !isCornerChart(body.data)) return null;
    return { summary: body.summary, data: body.data, source: "snapshot" };
  } catch {
    return null;
  }
}

/**
 * The featured corner: the snapshot, else the live tool, else null. It never throws: any failure
 * (a 404, a stopped API, a server that doesn't answer in time, a payload the chart can't draw)
 * falls through to the next source.
 */
export async function loadFeaturedCorner(
  entry: FeaturedCorner = LANDING_CORNER,
  options: LoadOptions = {},
): Promise<FeaturedPayload | null> {
  const send = options.fetch ?? globalThis.fetch;
  const saved = await fromSnapshot(entry, send, options.signal);
  if (saved) return saved;
  const params = cornerParams(entry);
  if (!params) return null;
  // AbortSignal.timeout is in every browser the site supports; without it, no time limit.
  const signal = options.signal ?? (typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(options.timeoutMs ?? LIVE_TIMEOUT_MS) : undefined);
  try {
    const init: RequestOptions = { fetch: send, baseUrl: options.baseUrl, signal };
    const { summary, data } = await callTool("explain_corner", params, init);
    return { summary, data, source: "live" };
  } catch {
    return null;
  }
}

// --- The shared store -----------------------------------------------------------------------

const IDLE: FeaturedState = { status: "idle" };
let state: FeaturedState = IDLE;
const listeners = new Set<() => void>();

function set(next: FeaturedState): void {
  state = next;
  for (const listener of listeners) listener();
}

/** Starts the load once; later calls do nothing. */
export function startFeaturedCorner(load: () => Promise<FeaturedPayload | null> = () => loadFeaturedCorner()): void {
  if (state.status !== "idle") return;
  set({ status: "loading" });
  load().then(
    (payload) => set(payload ? { status: "ready", payload } : { status: "missing" }),
    () => set({ status: "missing" }),
  );
}

export function subscribeFeaturedCorner(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function featuredCornerSnapshot(): FeaturedState {
  return state;
}

/** The server render (and hydration) always sees "idle": the corner is fetched in the browser. */
export function featuredCornerServerSnapshot(): FeaturedState {
  return IDLE;
}

/** Back to idle, for tests. */
export function resetFeaturedCorner(): void {
  set(IDLE);
}

/** The minimal window this needs, so a test can pass a fake one. */
export interface IdleHost {
  document: { readyState: string };
  addEventListener(type: "load", listener: () => void, options?: { once?: boolean }): void;
  removeEventListener(type: "load", listener: () => void): void;
  requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
  cancelIdleCallback?: (handle: number) => void;
  setTimeout(callback: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

/** How long the idle start may wait after load before it runs anyway. */
export const IDLE_TIMEOUT_MS = 2000;

/**
 * Calls `start` once the page has loaded and the browser is idle (at most 2 s after load); where
 * there is no requestIdleCallback (Safari), shortly after load. Returns a cancel function.
 */
export function startWhenIdle(host: IdleHost, start: () => void): () => void {
  let idle: number | null = null;
  let timer: number | null = null;
  const schedule = () => {
    if (host.requestIdleCallback) idle = host.requestIdleCallback(start, { timeout: IDLE_TIMEOUT_MS });
    else timer = host.setTimeout(start, 200);
  };
  if (host.document.readyState === "complete") schedule();
  else host.addEventListener("load", schedule, { once: true });
  return () => {
    host.removeEventListener("load", schedule);
    if (idle !== null) host.cancelIdleCallback?.(idle);
    if (timer !== null) host.clearTimeout(timer);
  };
}
