/**
 * The hero's real examples (plan 13, changed by the user on 2026-10-04): a few flagged corners,
 * each drawn on its real circuit with only that corner lit, rotating in the hero card.
 *
 * The browser makes no API call for them. `scripts/snapshots.ts` turns the explain_corner answers
 * it already saves into one small static file, `public/snapshots/hero.json` (`heroFile`), keeping
 * only what the card draws. Like the other snapshots it is gitignored, because the outlines come
 * from car positions; M7 writes it at deploy. The page fetches it once after first paint
 * (`startHero`), checks it (`parseHeroFile`) and fits each outline into the card (`heroGeometry`).
 * Without the file the card falls back to the abstract circuit.
 *
 * Pure apart from the fetch, which a test can replace.
 */

import type { CornerChart } from "../../charts/explain-corner.ts";
import { FEATURED, featuredHref, LANDING_CORNER, type FeaturedCorner } from "../../content/featured.ts";
import { eventShort, sessionName } from "../../lib/format.ts";

/**
 * The examples, in the order they rotate (the approved mockup, plan 13: all five). The first is the
 * landing example, so the hero and the story below it start on the same corner. Flagged corners
 * only (no energy or saved-time ones): Sainz in Melbourne, Hülkenberg at Monza's first chicane,
 * Sainz's Silverstone qualifying lap and Albon in Madrid follow. Madrid's map is north-up (the
 * tool has no FastF1 rotation for it), so it stands taller than the others.
 */
export const HERO_IDS: readonly string[] = [
  LANDING_CORNER.id,
  "2026_01_R_55_53_T3",
  "2026_13_R_27_22_T1",
  "2026_09_Q_55_2_T3",
  "2026_14_R_23_55_T3",
];

/** Where the file is served (public/snapshots/). */
export const HERO_URL = "/snapshots/hero.json";

export const HERO_VERSION = 1;

export type Point = [number, number];

export interface HeroStart {
  x: number;
  y: number;
  /** The direction of travel over the line, a unit vector. */
  dx: number;
  dy: number;
}

/** One example as the card draws it: the readout, the caption, the map and the link. */
export interface HeroExample {
  id: string;
  driver: string;
  /** The driver's name from content/featured.ts; the code stands in without it. */
  name: string | null;
  event: string;
  location: string;
  year: number;
  session_code: string;
  session_name: string;
  lap: number;
  turn: string;
  time_lost_s: number;
  type_text: string;
  map: {
    width: number;
    height: number;
    /** The circuit's centre line in map px, from the start line round to it (closed). */
    outline: Point[];
    /** The flagged corner: the driver's apex on the map. */
    corner: Point;
    /** Where the tool's own map puts that turn's number, beside the track; null without one. */
    label: Point | null;
    start: HeroStart | null;
  };
  /** The corner open in the mistake explorer. */
  href: string;
}

export interface HeroFile {
  version: typeof HERO_VERSION;
  examples: HeroExample[];
}

/** The featured entries the hero shows, in order. */
export function heroEntries(ids: readonly string[] = HERO_IDS): FeaturedCorner[] {
  return ids.map((id) => FEATURED.find((f) => f.id === id)).filter((f): f is FeaturedCorner => f !== undefined);
}

const r1 = (v: number) => Math.round(v * 10) / 10;
const r2 = (v: number) => Math.round(v * 100) / 100;
const r3 = (v: number) => Math.round(v * 1000) / 1000;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/**
 * How far (map px) a dropped point may sit from the simplified line. The maps are about 300 px
 * wide and drawn at most about 1.5 times that, so 0.1 px never shows; it halves the file, because
 * the tool samples the lap every few metres, straights included.
 */
export const SIMPLIFY_PX = 0.1;

function segmentDistance(p: Point, a: Point, b: Point): number {
  const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
  const len2 = dx * dx + dy * dy;
  const t = len2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

/**
 * The line with the points that add nothing left out (Ramer-Douglas-Peucker): every dropped point
 * lies within `tolerance` of what is kept. The first and last points always stay.
 */
export function simplifyLine(points: Point[], tolerance: number = SIMPLIFY_PX): Point[] {
  if (points.length < 3) return points.slice();
  const keep = new Array<boolean>(points.length).fill(false);
  keep[0] = keep[points.length - 1] = true;
  const stack: [number, number][] = [[0, points.length - 1]];
  while (stack.length) {
    const [i, j] = stack.pop()!;
    let far = -1;
    let at = -1;
    for (let k = i + 1; k < j; k++) {
      const d = segmentDistance(points[k], points[i], points[j]);
      if (d > far) [far, at] = [d, k];
    }
    if (far > tolerance) {
      keep[at] = true;
      stack.push([i, at], [at, j]);
    }
  }
  return points.filter((_, k) => keep[k]);
}

/**
 * What the card keeps of one explain_corner answer, or null when it can't be a hero example:
 * not a flagged corner, no time lost, or no usable map. Coordinates are rounded to 0.1 px (the
 * map is about 300 px wide), repeated points dropped, and the closing point too (the path closes);
 * then the points that add nothing to the line's shape are left out (`simplifyLine`).
 */
export function heroExample(data: CornerChart, entry: FeaturedCorner): HeroExample | null {
  const map = data.map;
  if (entry.kind !== "flagged" || data.flagged !== true) return null;
  if (!finite(data.time_lost_s) || data.time_lost_s <= 0) return null;
  if (!map || !finite(map.width) || !finite(map.height) || map.width <= 0 || map.height <= 0) return null;
  if (!Array.isArray(map.outline) || !map.apex || !finite(map.apex.x) || !finite(map.apex.y)) return null;

  const outline: Point[] = [];
  for (const p of map.outline) {
    if (!Array.isArray(p) || !finite(p[0]) || !finite(p[1])) continue;
    const q: Point = [r1(p[0]), r1(p[1])];
    const last = outline[outline.length - 1];
    if (!last || last[0] !== q[0] || last[1] !== q[1]) outline.push(q);
  }
  if (outline.length > 1) {
    const [a, z] = [outline[0], outline[outline.length - 1]];
    if (a[0] === z[0] && a[1] === z[1]) outline.pop();
  }
  if (outline.length < 3) return null;
  const line = simplifyLine(outline);
  if (line.length < 3) return null;

  const turn = (Array.isArray(map.turns) ? map.turns : []).find((t) => t.label === data.turn);
  const label: Point | null = turn && finite(turn.tx) && finite(turn.ty) ? [r1(turn.tx), r1(turn.ty)] : null;
  const s = map.start;
  const start: HeroStart | null =
    s && finite(s.x) && finite(s.y) && finite(s.dx) && finite(s.dy) ? { x: r1(s.x), y: r1(s.y), dx: r3(s.dx), dy: r3(s.dy) } : null;

  return {
    id: entry.id,
    driver: data.driver,
    name: entry.name ?? null,
    event: data.event,
    location: data.location,
    year: data.year,
    session_code: data.session_code,
    session_name: data.session,
    lap: data.lap_number,
    turn: data.turn,
    time_lost_s: r2(data.time_lost_s),
    type_text: data.type_text,
    map: { width: r1(map.width), height: r1(map.height), outline: line, corner: [r1(map.apex.x), r1(map.apex.y)], label, start },
    href: featuredHref(entry),
  };
}

/** The file scripts/snapshots.ts writes. */
export function heroFile(examples: HeroExample[]): HeroFile {
  return { version: HERO_VERSION, examples };
}

// --- The guard ------------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const isText = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0;
const isPoint = (v: unknown): v is Point => Array.isArray(v) && v.length === 2 && finite(v[0]) && finite(v[1]);

/** Whether one example has every field the card reads, with real numbers. */
export function isHeroExample(value: unknown): value is HeroExample {
  if (!isRecord(value)) return false;
  const v = value;
  const texts = ["id", "driver", "event", "session_code", "session_name", "turn", "type_text"] as const;
  if (!texts.every((k) => isText(v[k]))) return false;
  if (typeof v.location !== "string") return false;
  if (v.name !== null && !isText(v.name)) return false;
  if (!finite(v.year) || !Number.isInteger(v.year) || v.year < 1950) return false;
  if (!finite(v.lap) || !Number.isInteger(v.lap) || v.lap < 1) return false;
  if (!finite(v.time_lost_s) || v.time_lost_s <= 0) return false;
  if (typeof v.href !== "string" || !v.href.startsWith("/mistakes?")) return false;
  const m = v.map;
  if (!isRecord(m)) return false;
  if (!finite(m.width) || !finite(m.height) || m.width <= 0 || m.height <= 0) return false;
  if (!Array.isArray(m.outline) || m.outline.length < 3 || !m.outline.every(isPoint)) return false;
  if (!isPoint(m.corner)) return false;
  if (m.label !== null && !isPoint(m.label)) return false;
  if (m.start !== null) {
    const s = m.start;
    if (!isRecord(s) || !finite(s.x) || !finite(s.y) || !finite(s.dx) || !finite(s.dy)) return false;
  }
  return true;
}

/**
 * The examples of a hero.json body that the card can draw, in the file's order; null when the
 * body isn't the file or none of its examples is usable (then the card falls back).
 */
export function parseHeroFile(value: unknown): HeroExample[] | null {
  if (!isRecord(value) || value.version !== HERO_VERSION || !Array.isArray(value.examples)) return null;
  const examples = value.examples.filter(isHeroExample);
  return examples.length ? examples : null;
}

// --- Fitting a circuit into the card --------------------------------------------------------

/** The card's drawing box (the approved mockup's stage, plan 13). */
export const VIEW = { w: 400, h: 270 } as const;
/** Room around the circuit for the corner's halo and its label. */
export const PAD = 24;
export const ROAD_WIDTH = 15;
export const HALO_R = 22;
/**
 * The turn label's size in drawing units on a narrow card, where the drawing is scaled down (about
 * 13 px on a phone); a card 440 px wide or more draws it at 14 units. Its spot is chosen for the
 * larger box.
 */
export const LABEL_FONT = 18;

export interface Fit {
  scale: number;
  dx: number;
  dy: number;
}

/**
 * One scale and an offset that put `points` in the middle of a `w`×`h` box, `pad` from its edges
 * on the tighter side: the shape keeps its aspect, only its size changes.
 */
export function fitInto(points: Point[], box: { w: number; h: number } = VIEW, pad: number = PAD): Fit {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of points) {
    x0 = Math.min(x0, x);
    y0 = Math.min(y0, y);
    x1 = Math.max(x1, x);
    y1 = Math.max(y1, y);
  }
  if (!points.length) return { scale: 1, dx: 0, dy: 0 };
  const [bw, bh] = [x1 - x0, y1 - y0];
  const [aw, ah] = [Math.max(1, box.w - 2 * pad), Math.max(1, box.h - 2 * pad)];
  const scale = bw > 0 && bh > 0 ? Math.min(aw / bw, ah / bh) : bw > 0 ? aw / bw : bh > 0 ? ah / bh : 1;
  return { scale, dx: (box.w - bw * scale) / 2 - x0 * scale, dy: (box.h - bh * scale) / 2 - y0 * scale };
}

export function project(p: Point, fit: Fit): Point {
  return [p[0] * fit.scale + fit.dx, p[1] * fit.scale + fit.dy];
}

type Rect = [number, number, number, number]; // left, top, right, bottom

function pointToRect([x, y]: Point, [l, t, r, b]: Rect): number {
  const dx = Math.max(l - x, 0, x - r);
  const dy = Math.max(t - y, 0, y - b);
  return Math.hypot(dx, dy);
}

/** The closest the closed line `pts` comes to the rectangle (segments sampled every 2 units). */
function lineToRect(pts: Point[], rect: Rect): number {
  let best = Infinity;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % pts.length];
    const steps = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 2));
    for (let k = 0; k < steps; k++) {
      const t = k / steps;
      best = Math.min(best, pointToRect([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], rect));
    }
  }
  return best;
}

export interface LabelSpot {
  x: number;
  y: number;
  /** How far the label's box is from the line, in drawing units. */
  clearance: number;
}

/** The label's box, centred on (x, y): monospace digits about 0.6 em wide, caps about 0.72 em high. */
export function labelRect(x: number, y: number, text: string, font: number = LABEL_FONT): Rect {
  const hw = (text.length * font * 0.6) / 2 + 1;
  const hh = font * 0.36 + 1;
  return [x - hw, y - hh, x + hw, y + hh];
}

const RADII = [34, 42, 52, 64];
const STEP = Math.PI / 8;

/**
 * Where the turn's label goes: clear of the road and of the corner's halo, inside the box, and as
 * close as it can be to the side the tool's own map puts it (`prefer`, else away from the
 * circuit's middle). When nothing is clear, the spot farthest from the line.
 */
export function placeLabel(
  corner: Point,
  prefer: Point | null,
  line: Point[],
  text: string,
  box: { w: number; h: number } = VIEW,
): LabelSpot {
  let angle: number;
  const toPrefer = prefer ? [prefer[0] - corner[0], prefer[1] - corner[1]] : [0, 0];
  if (Math.hypot(toPrefer[0], toPrefer[1]) > 0.5) angle = Math.atan2(toPrefer[1], toPrefer[0]);
  else {
    const cx = line.reduce((s, p) => s + p[0], 0) / Math.max(1, line.length);
    const cy = line.reduce((s, p) => s + p[1], 0) / Math.max(1, line.length);
    const away = [corner[0] - cx, corner[1] - cy];
    angle = Math.hypot(away[0], away[1]) > 0.5 ? Math.atan2(away[1], away[0]) : -Math.PI / 2;
  }
  const clearOfRoad = ROAD_WIDTH / 2 + 4;
  let best: (LabelSpot & { score: number }) | null = null;
  let roomiest: LabelSpot | null = null;
  for (let k = 0; k <= 8; k++) {
    for (const sign of k === 0 || k === 8 ? [1] : [1, -1]) {
      const a = angle + sign * k * STEP;
      for (let ri = 0; ri < RADII.length; ri++) {
        const x = corner[0] + Math.cos(a) * RADII[ri];
        const y = corner[1] + Math.sin(a) * RADII[ri];
        const rect = labelRect(x, y, text);
        if (rect[0] < 3 || rect[1] < 3 || rect[2] > box.w - 3 || rect[3] > box.h - 3) continue;
        const clearance = lineToRect(line, rect);
        if (!roomiest || clearance > roomiest.clearance) roomiest = { x, y, clearance };
        if (clearance < clearOfRoad || pointToRect(corner, rect) < HALO_R + 2) continue;
        const score = k + ri * 1.2;
        if (!best || score < best.score) best = { x, y, clearance, score };
      }
    }
  }
  const pick = best ?? roomiest;
  if (!pick) return { x: corner[0], y: corner[1] - RADII[0], clearance: 0 };
  return { x: r1(pick.x), y: r1(pick.y), clearance: r1(pick.clearance) };
}

export interface HeroGeometry {
  /** The closed centre line in drawing units, for both the road and the racing line. */
  path: string;
  corner: Point;
  label: LabelSpot & { text: string };
  /** The start line: a short stroke across the road. */
  start: { x1: number; y1: number; x2: number; y2: number } | null;
}

/** An example's drawing in the card's box: its outline fitted with the aspect kept. */
export function heroGeometry(example: HeroExample, box: { w: number; h: number } = VIEW): HeroGeometry {
  const { outline, corner, label, start } = example.map;
  const fit = fitInto(outline, box);
  const line = outline.map((p) => project(p, fit));
  const path = `M${line.map(([x, y]) => `${r1(x)},${r1(y)}`).join("L")}Z`;
  const c = project(corner, fit);
  const text = `T${example.turn}`;
  const spot = placeLabel(c, label ? project(label, fit) : null, line, text, box);
  let startLine: HeroGeometry["start"] = null;
  if (start) {
    const [sx, sy] = project([start.x, start.y], fit);
    const half = ROAD_WIDTH / 2 + 3;
    const n = Math.hypot(start.dx, start.dy) || 1;
    const [px, py] = [-start.dy / n, start.dx / n];
    startLine = { x1: r1(sx - px * half), y1: r1(sy - py * half), x2: r1(sx + px * half), y2: r1(sy + py * half) };
  }
  return { path, corner: [r1(c[0]), r1(c[1])], label: { ...spot, text }, start: startLine };
}

// --- Words ----------------------------------------------------------------------------------

type CaptionParts = Pick<HeroExample, "name" | "driver" | "year" | "event" | "session_name">;

/** "Norris · 2026 Azerbaijan GP · Race · real circuit". */
export function heroCaption(e: CaptionParts): string {
  return `${e.name ?? e.driver} · ${e.year} ${eventShort(e.event)} · ${e.session_name} · real circuit`;
}

/** The caption of a featured entry before the file is here (the card reserves their room). */
export function entryCaption(f: FeaturedCorner): string {
  const { driver, year, event, session } = f.args;
  return heroCaption({ name: f.name ?? null, driver, year, event, session_name: sessionName(session) });
}

/** The place: "Baku" (the event's short name without one). */
export function heroPlace(e: Pick<HeroExample, "location" | "event">): string {
  return e.location.trim() || eventShort(e.event);
}

/** The readout's label (the approved mockup): "NOR · Baku 2026 · Lap 16". */
export function readoutLabel(e: HeroExample): string {
  return `${e.driver} · ${heroPlace(e)} ${e.year} · Lap ${e.lap}`;
}

/**
 * The corner's type, short, for the readout's "Turn 5 · over-slowing": without the tool's
 * parenthesis or its second name ("track limits / off track (race control)" is "track limits").
 */
export function shortType(typeText: string): string {
  const short = typeText.replace(/\s*\([^)]*\)\s*$/, "").split(" / ")[0].trim();
  return short || typeText.trim();
}

/** The picker's name for an example: "Show Sainz, Melbourne 2026". */
export function pickLabel(e: HeroExample): string {
  return `Show ${e.name ?? e.driver}, ${heroPlace(e)} ${e.year}`;
}

/** The link's accessible name: what the drawing shows, and where the link goes. */
export function linkLabel(e: HeroExample): string {
  return (
    `${heroPlace(e)} circuit with turn ${e.turn} lit: ${e.name ?? e.driver}, lap ${e.lap}, ` +
    `${e.year} ${eventShort(e.event)} ${e.session_name.toLowerCase()}, ${e.type_text}, ` +
    `${e.time_lost_s.toFixed(2)} s lost. Open this corner in the mistake explorer.`
  );
}

/** What a screen reader hears when the reader picks an example. */
export function pickAnnouncement(e: HeroExample): string {
  return `Showing ${e.name ?? e.driver}, ${heroPlace(e)} ${e.year}: lap ${e.lap}, turn ${e.turn}, ${e.time_lost_s.toFixed(2)} s lost.`;
}

// --- Loading the file -----------------------------------------------------------------------

/** The file's examples, or null when it is missing, unreadable or has none the card can draw. */
export async function loadHero(send: typeof fetch = globalThis.fetch, url: string = HERO_URL): Promise<HeroExample[] | null> {
  try {
    // A static file: the browser's normal cache rules apply.
    const response = await send(url);
    if (!response.ok) return null;
    return parseHeroFile(await response.json());
  } catch {
    return null;
  }
}

export type HeroState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; examples: HeroExample[] }
  | { status: "missing" };

const IDLE: HeroState = { status: "idle" };
let state: HeroState = IDLE;
const listeners = new Set<() => void>();

function set(next: HeroState): void {
  state = next;
  for (const listener of listeners) listener();
}

/** Starts the load once; later calls do nothing. */
export function startHero(load: () => Promise<HeroExample[] | null> = () => loadHero()): void {
  if (state.status !== "idle") return;
  set({ status: "loading" });
  load().then(
    (examples) => set(examples ? { status: "ready", examples } : { status: "missing" }),
    () => set({ status: "missing" }),
  );
}

export function subscribeHero(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function heroSnapshot(): HeroState {
  return state;
}

/** The server render (and hydration) sees "idle": the file is read in the browser. */
export function heroServerSnapshot(): HeroState {
  return IDLE;
}

/** Back to idle, for tests. */
export function resetHero(): void {
  set(IDLE);
}
