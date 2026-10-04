/**
 * The season's teammate pairs and drivers as the /styles page shows them, from the style catalog
 * (GET /api/catalog/styles): the pair list's labels, the custom pair's driver list, whether two
 * drivers shared a car, and which pair to show when the URL names a season but no pair.
 *
 * "Teammates" means the catalog lists them as a pair: they drove the same car at the same events.
 * Two drivers who swapped seats mid-season (2025's LAW and TSU) each drove both cars, but never
 * together, so they aren't teammates, exactly as the tool says (`teammates: false`).
 */
import type { StyleCatalog, StylePair } from "../../lib/api/types.ts";
import { DEFAULT_STYLES } from "./query.ts";

/** Fewer events than this give no interval, so nothing is called clear (inference/style.py). */
export const MIN_INTERVAL_EVENTS = 4;

/** A pair named in either order. */
export interface PairChoice {
  a: string;
  b: string;
}

/** "HAM-LEC" for HAM and LEC in either order: one key per pair. */
export function pairKey(a: string, b: string): string {
  return a < b ? `${a}-${b}` : `${b}-${a}`;
}

/** The catalog's pair of these two drivers (in either order), if they were teammates. */
export function findPair(catalog: StyleCatalog, a: string, b: string): StylePair | undefined {
  const key = pairKey(a, b);
  return catalog.pairs.find((p) => pairKey(p.a, p.b) === key);
}

/**
 * Whether two drivers shared a car that season: true or false when the catalog has both of them,
 * null when it doesn't know one of them (a code typed by hand, or another season's driver).
 */
export function areTeammates(catalog: StyleCatalog, a: string, b: string): boolean | null {
  const known = new Set(catalog.drivers.map((d) => d.driver));
  if (!known.has(a) || !known.has(b)) return null;
  return findPair(catalog, a, b) !== undefined;
}

/** "24 events", or "2 events, too few for intervals" for a short mid-season pair. */
export function pairEventsText(pair: StylePair): string {
  const count = `${pair.events} ${pair.events === 1 ? "event" : "events"}`;
  return pair.events < MIN_INTERVAL_EVENTS ? `${count}, too few for intervals` : count;
}

/** "Ferrari · HAM and LEC · 24 events": a pair button's whole name. */
export function pairLabel(pair: StylePair): string {
  return `${pair.team} · ${pair.a} and ${pair.b} · ${pairEventsText(pair)}`;
}

/** The phone list's options: grouped by team (the catalog's order), the key as the value. */
export function pairOptions(catalog: StyleCatalog): { value: string; label: string; group: string }[] {
  return catalog.pairs.map((p) => ({ value: pairKey(p.a, p.b), label: `${p.a} and ${p.b} · ${pairEventsText(p)}`, group: p.team }));
}

export interface DriverChoice {
  driver: string;
  teams: string[]; // in the catalog's order; two for a driver who changed teams mid-season
  label: string; // "HAM · Ferrari", "LAW · Racing Bulls, Red Bull Racing"
}

/** Each of the season's drivers once, by code, with every team they drove for. */
export function driverChoices(catalog: StyleCatalog): DriverChoice[] {
  const teams = new Map<string, string[]>();
  for (const row of catalog.drivers) {
    const list = teams.get(row.driver) ?? [];
    if (!list.includes(row.team)) list.push(row.team);
    teams.set(row.driver, list);
  }
  return [...teams.entries()]
    .sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0))
    .map(([driver, list]) => ({ driver, teams: list, label: `${driver} · ${list.join(", ")}` }));
}

// Most events first; on a tie the catalog's order (by team, then code) stands.
function busiest(pairs: readonly StylePair[]): StylePair | undefined {
  return pairs.reduce<StylePair | undefined>((best, p) => (!best || p.events > best.events ? p : best), undefined);
}

/** The teammate `driver` shared the most events with that season, if any. */
export function teammateOf(catalog: StyleCatalog, driver: string): string | undefined {
  const pair = busiest(catalog.pairs.filter((p) => p.a === driver || p.b === driver));
  return pair ? (pair.a === driver ? pair.b : pair.a) : undefined;
}

// The pair with `first` as driver A.
function oriented(pair: StylePair, first?: string): PairChoice {
  return first === pair.b ? { a: pair.b, b: pair.a } : { a: pair.a, b: pair.b };
}

/**
 * The pair to show for a season when the URL names none, keeping what the reader had:
 * 1. the same two drivers, if they were teammates that season;
 * 2. else driver A with the teammate they shared the most events with, A kept first;
 * 3. else the same for driver B, B kept second;
 * 4. else the page's default pair (HAM and LEC) when that season has it;
 * 5. else the pair with the most events (the catalog's order breaks ties).
 * Null for a season with no pairs.
 */
export function pickPair(catalog: StyleCatalog, prefer: Partial<PairChoice> = {}): PairChoice | null {
  const { a, b } = prefer;
  if (a && b) {
    const same = findPair(catalog, a, b);
    if (same) return oriented(same, a);
  }
  if (a) {
    const withA = busiest(catalog.pairs.filter((p) => p.a === a || p.b === a));
    if (withA) return oriented(withA, a);
  }
  if (b) {
    const withB = busiest(catalog.pairs.filter((p) => p.a === b || p.b === b));
    if (withB) {
      const pair = oriented(withB, b);
      return { a: pair.b, b: pair.a };
    }
  }
  const fallback = findPair(catalog, DEFAULT_STYLES.a, DEFAULT_STYLES.b);
  if (fallback) return oriented(fallback, DEFAULT_STYLES.a);
  const most = busiest(catalog.pairs);
  return most ? oriented(most) : null;
}
