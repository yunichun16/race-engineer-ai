/**
 * The /styles page's state in its URL (plan 7.4): the season, the two drivers, the chart's kind
 * and plane, and the weekend for the lap-by-lap comparison.
 *
 * Parsing drops anything invalid, key by key, and a pair of the same driver keeps only `a`.
 * The season and the pair are history entries; `kind`, `plane` and `laps` only replace the
 * current entry, and reach the chart only as its initial kind and plane, so writing them into
 * the URL never redraws the chart or moves focus.
 */
import { historyMode, intParam, pickParam, withQuery, type QueryLike } from "../../lib/url.ts";

// The tools refuse seasons outside these (engine tools/params.py).
export const FIRST_YEAR = 2018;
export const LAST_YEAR = 2030;
const MAX_EVENT_LENGTH = 100;

// The same values as the chart's `StyleKind` and `Plane` (charts/compare-styles.ts).
export const KINDS = ["all", "quali", "race"] as const;
export type StyleKind = (typeof KINDS)[number];
export const PLANES = ["style", "car"] as const;
export type Plane = (typeof PLANES)[number];

export interface StylesQuery {
  year?: number;
  a?: string; // driver A, a three-letter code
  b?: string; // driver B, never the same as A
  kind?: StyleKind;
  plane?: Plane;
  laps?: string; // the weekend for "Lap by lap", an exact event name
}

/** With no URL at all: Hamilton and Leclerc, Ferrari teammates in 2025 (the plan's example 6). */
export const DEFAULT_STYLES: Readonly<{ year: number; a: string; b: string }> = { year: 2025, a: "HAM", b: "LEC" };

/** Keys whose change is a new history entry; the others replace the current one. */
export const STYLES_PUSH_KEYS = ["year", "a", "b"] as const;

/** The seasons to offer when the style catalog can't be read (plan 7.4), newest first. */
export const FALLBACK_YEARS: readonly number[] = [2026, 2025, 2024, 2023, 2022];

/** A three-letter driver code, upper-cased ("ham" reads as "HAM"); also for typed codes. */
export function driverCode(text: string | null | undefined): string | undefined {
  // ASCII letters only, checked before upper-casing: "ß" or "ı" would upper-case into ASCII.
  const code = text?.trim();
  return code && /^[A-Za-z]{3}$/.test(code) ? code.toUpperCase() : undefined;
}

/** An event name as given, trimmed: up to 100 characters, no control characters. */
function eventName(text: string | null | undefined): string | undefined {
  const name = text?.trim();
  if (!name || name.length > MAX_EVENT_LENGTH) return undefined;
  for (let i = 0; i < name.length; i++) {
    const c = name.charCodeAt(i);
    if (c < 0x20 || c === 0x7f) return undefined;
  }
  return name;
}

export function parseStylesQuery(params: QueryLike): StylesQuery {
  const q: StylesQuery = {};
  const year = intParam(params.get("year"), FIRST_YEAR, LAST_YEAR);
  const a = driverCode(params.get("a"));
  const b = driverCode(params.get("b"));
  const kind = pickParam(params.get("kind"), KINDS);
  const plane = pickParam(params.get("plane"), PLANES);
  const laps = eventName(params.get("laps"));
  if (year !== undefined) q.year = year;
  if (a) q.a = a;
  if (b && b !== a) q.b = b;
  if (kind) q.kind = kind;
  if (plane) q.plane = plane;
  if (laps) q.laps = laps;
  return q;
}

/** The query values for `setQuery` or `withQuery`: every key, `undefined` where unset. */
export function serializeStylesQuery(q: StylesQuery): Record<string, string | undefined> {
  return {
    year: q.year?.toString(),
    a: q.a,
    b: q.b && q.b !== q.a ? q.b : undefined,
    kind: q.kind,
    plane: q.plane,
    laps: q.laps,
  };
}

/** A link to /styles in this state, e.g. "/styles?a=HAM&b=LEC&year=2025". */
export function stylesHref(q: StylesQuery): string {
  return withQuery("/styles", serializeStylesQuery(q));
}

/** "push", "replace" or null (no change) for moving from one state to the next. */
export function stylesUpdate(prev: StylesQuery, next: StylesQuery): "push" | "replace" | null {
  return historyMode(serializeStylesQuery(prev), serializeStylesQuery(next), STYLES_PUSH_KEYS);
}

/**
 * The state with the default season and pair filled in when the URL names neither a season nor a
 * driver (`/styles`, or `/styles?kind=race`). A URL with a season but no pair (the season picker
 * writes one) is left for the page, which picks a pair from that season's list once it has it,
 * keeping the drivers it showed before where it can (`pickPair` in pairs.ts).
 */
export function withStylesDefaults(q: StylesQuery): StylesQuery {
  if (q.a || q.b || q.year !== undefined) return q;
  return { ...q, year: DEFAULT_STYLES.year, a: DEFAULT_STYLES.a, b: DEFAULT_STYLES.b };
}
