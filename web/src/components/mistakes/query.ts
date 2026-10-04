/**
 * The /mistakes page's state in its URL (plan 7.3): the session, the driver filter, how many rows
 * to list and the open corner.
 *
 * Parsing drops anything invalid, key by key, so a hand-edited or stale link loads what it can
 * and never reaches the API as a 422. Serializing gives the values `setQuery` writes. A new
 * session, weekend or corner is a history entry (Back undoes it); the driver filter and the row
 * count only replace the current entry.
 */
import { SESSION_ORDER, type SessionCode } from "../../lib/format.ts";
import { historyMode, intParam, pickParam, withQuery, type QueryLike } from "../../lib/url.ts";

// The tools refuse seasons, laps and turns outside these (engine tools/params.py).
export const FIRST_YEAR = 2018;
export const LAST_YEAR = 2030;
const MAX_LAP = 100;
const MAX_TURN = 99;
const MAX_EVENT_LENGTH = 100;

export const LIMITS = [10, 25, 50] as const;
export type Limit = (typeof LIMITS)[number];
export const DEFAULT_LIMIT: Limit = 10;

/** The open corner: `explain=LEC:10:9A`. The turn is upper-cased and has no leading "T". */
export interface ExplainRef {
  driver: string;
  lap: number;
  turn: string;
}

export interface MistakesQuery {
  year?: number;
  event?: string; // an exact event name, as the catalog lists it
  session?: SessionCode;
  driver?: string; // the list filter; absent means the whole field
  limit?: Limit;
  explain?: ExplainRef; // only with an event: a corner means nothing without its session
}

/** Keys whose change is a new history entry; the others replace the current one. */
export const MISTAKES_PUSH_KEYS = ["year", "event", "session", "explain"] as const;

/** A three-letter driver code, upper-cased ("lec" reads as "LEC"). */
function driverCode(text: string | null | undefined): string | undefined {
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

/**
 * A turn as the URL writes it: "9A" for 9a, "T9a", "t09A" or 9. Null when it isn't a turn number
 * from 1 to 99 with at most one letter.
 */
export function normalizeTurn(turn: string | number): string | null {
  // Matched before upper-casing, so only ASCII letters count ("9ı" isn't "9I").
  const m = /^T?0*(\d{1,2})([A-Z]?)$/i.exec(String(turn).trim());
  if (!m) return null;
  const n = Number(m[1]);
  return n >= 1 && n <= MAX_TURN ? `${n}${m[2].toUpperCase()}` : null;
}

/** An open corner from its parts (a payload row, say), checked and normalised. */
export function explainRef(driver: string, lap: number | string, turn: string | number): ExplainRef | null {
  const code = driverCode(driver);
  const lapNumber = intParam(String(lap), 1, MAX_LAP);
  const label = normalizeTurn(turn);
  return code && lapNumber !== undefined && label ? { driver: code, lap: lapNumber, turn: label } : null;
}

/** "ALB:51:11" or "LEC:10:9a" as an open corner; null if any part is invalid. */
export function parseExplain(text: string | null | undefined): ExplainRef | null {
  if (!text) return null;
  const parts = text.split(":");
  return parts.length === 3 ? explainRef(parts[0], parts[1].trim(), parts[2]) : null;
}

export function formatExplain(ref: ExplainRef): string {
  return `${ref.driver}:${ref.lap}:${ref.turn}`;
}

/** Whether a find_mistakes row is the open corner (the payload writes turns as "7" or "9a"). */
export function sameCorner(
  ref: ExplainRef | null | undefined,
  row: { driver: string; lap_number: number; turn: string },
): boolean {
  if (!ref) return false;
  return ref.driver === row.driver && ref.lap === row.lap_number && ref.turn === normalizeTurn(row.turn);
}

/** The open corner in the shape `FindMistakesChart`'s `selected` takes (turn as the payload has it). */
export function explainSelection(
  ref: ExplainRef | null | undefined,
): { driver: string; lap_number: number; turn: string } | null {
  return ref ? { driver: ref.driver, lap_number: ref.lap, turn: ref.turn.toLowerCase() } : null;
}

export function parseMistakesQuery(params: QueryLike): MistakesQuery {
  const q: MistakesQuery = {};
  const year = intParam(params.get("year"), FIRST_YEAR, LAST_YEAR);
  const event = eventName(params.get("event"));
  const session = pickParam(params.get("session"), SESSION_ORDER);
  const driver = driverCode(params.get("driver"));
  const rows = intParam(params.get("limit"), 1, 50);
  const limit = LIMITS.find((n) => n === rows);
  const explain = event ? parseExplain(params.get("explain")) : null;
  if (year !== undefined) q.year = year;
  if (event) q.event = event;
  if (session) q.session = session;
  if (driver) q.driver = driver;
  if (limit !== undefined) q.limit = limit;
  if (explain) q.explain = explain;
  return q;
}

/** The query values for `setQuery` or `withQuery`: every key, `undefined` where unset. */
export function serializeMistakesQuery(q: MistakesQuery): Record<string, string | undefined> {
  return {
    year: q.year?.toString(),
    event: q.event,
    session: q.session,
    driver: q.driver,
    limit: q.limit?.toString(),
    explain: q.event && q.explain ? formatExplain(q.explain) : undefined,
  };
}

/** A link to /mistakes in this state, e.g. "/mistakes?event=…&explain=NOR:16:5&session=R&year=2026". */
export function mistakesHref(q: MistakesQuery): string {
  return withQuery("/mistakes", serializeMistakesQuery(q));
}

/** "push", "replace" or null (no change) for moving from one state to the next. */
export function mistakesUpdate(prev: MistakesQuery, next: MistakesQuery): "push" | "replace" | null {
  return historyMode(serializeMistakesQuery(prev), serializeMistakesQuery(next), MISTAKES_PUSH_KEYS);
}
