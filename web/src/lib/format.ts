/**
 * Formatting shared by the site's pages: dates, seconds and lap times, session codes and event
 * names.
 *
 * Dates are always read and printed in UTC, so a race weekend never moves a day with the reader's
 * time zone ("2025-09-06" is the 6th in Tokyo and in Los Angeles). Month names come from a fixed
 * list rather than `Intl`, because ICU versions disagree on short months ("Sep" or "Sept"), and
 * the server and the browser must print exactly the same text or hydration fails.
 */

/**
 * Every session code, in the order of a sprint weekend since 2024 (sprint qualifying, sprint,
 * qualifying, race). Earlier seasons ran differently: in 2022 qualifying came before the sprint,
 * and in 2023 before the shootout and the sprint (`weekendOrder`). Where the catalog gives
 * sessions, keep its order, which is by date.
 */
export const SESSION_ORDER = ["SQ", "SS", "S", "Q", "R"] as const;

export type SessionCode = (typeof SESSION_ORDER)[number];

// 2022 and 2023: Friday qualifying set the race grid; the sprint (and in 2023 the shootout
// before it) ran on Saturday.
const ORDER_BEFORE_2024: readonly SessionCode[] = ["Q", "SQ", "SS", "S", "R"];

/** The order a season's weekends ran their sessions in; `SESSION_ORDER` when the year is unknown. */
export function weekendOrder(year?: number): readonly SessionCode[] {
  return year !== undefined && year < 2024 ? ORDER_BEFORE_2024 : SESSION_ORDER;
}

// The names the engine uses (`config.SESSION_CODES`). The Sprint Shootout was 2023's name for
// sprint qualifying.
const SESSION_NAMES: Record<SessionCode, string> = {
  SQ: "Sprint Qualifying",
  SS: "Sprint Shootout",
  S: "Sprint",
  Q: "Qualifying",
  R: "Race",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A dash for a missing number, as the charts print it. */
export const MISSING = "–";

export function isSessionCode(value: unknown): value is SessionCode {
  return typeof value === "string" && (SESSION_ORDER as readonly string[]).includes(value);
}

/** "Qualifying" for "Q"; an unknown code comes back unchanged rather than as nothing. */
export function sessionName(code: string): string {
  return isSessionCode(code) ? SESSION_NAMES[code] : code;
}

/**
 * Sort comparator for session codes in the order `year`'s weekends ran them (`weekendOrder`);
 * unknown codes go last. Use it as `(a, b) => compareSessions(a, b, year)`.
 */
export function compareSessions(a: string, b: string, year?: number): number {
  const order: readonly string[] = weekendOrder(year);
  const rank = (code: string) => {
    const i = order.indexOf(code);
    return i === -1 ? order.length : i;
  };
  return rank(a) - rank(b);
}

/** "Azerbaijan GP" for "Azerbaijan Grand Prix"; other names are left alone. */
export function eventShort(event: string): string {
  return event.replace(/ Grand Prix$/, " GP");
}

interface DateParts {
  year: number;
  month: number; // 1 to 12
  day: number;
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
const DATE_TIME = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;

function validParts(year: number, month: number, day: number): DateParts | null {
  // Date.UTC rolls 30 February over into March; a real date survives the round trip.
  const t = new Date(Date.UTC(year, month - 1, day));
  if (t.getUTCFullYear() !== year || t.getUTCMonth() !== month - 1 || t.getUTCDate() !== day) return null;
  return { year, month, day };
}

/**
 * The UTC calendar date of an ISO date or timestamp, or null if it can't be read.
 *
 * A plain date ("2025-09-06") and a timestamp without a zone are taken as written, never through
 * the local zone. A timestamp with a zone ("…T23:30:00-04:00") is converted to UTC.
 */
export function utcDateParts(iso: string): DateParts | null {
  const text = iso.trim();
  const plain = DATE_ONLY.exec(text);
  if (plain) return validParts(Number(plain[1]), Number(plain[2]), Number(plain[3]));
  const full = DATE_TIME.exec(text);
  if (!full || Number(full[4]) > 23 || Number(full[5]) > 59 || Number(full[6] ?? 0) > 59) return null;
  const parts = validParts(Number(full[1]), Number(full[2]), Number(full[3]));
  if (!parts || !full[7]) return parts;
  const t = new Date(text);
  if (Number.isNaN(t.getTime())) return null;
  return { year: t.getUTCFullYear(), month: t.getUTCMonth() + 1, day: t.getUTCDate() };
}

/**
 * "13 Sep", or "13 Sep 2026" with `year`. Text that isn't a date comes back unchanged, so a bad
 * value shows up on the page instead of disappearing.
 */
export function formatDate(iso: string, opts: { year?: boolean } = {}): string {
  const d = utcDateParts(iso);
  if (!d) return iso;
  const text = `${d.day} ${MONTHS[d.month - 1]}`;
  return opts.year ? `${text} ${d.year}` : text;
}

/**
 * A span of days: "6–8 Sep", "30 Aug – 1 Sep", or with years when they differ (always) or when
 * `year` is set: "6–8 Sep 2025", "30 Dec 2025 – 1 Jan 2026".
 */
export function formatDateRange(a: string, b: string, opts: { year?: boolean } = {}): string {
  const from = utcDateParts(a);
  const to = utcDateParts(b);
  if (!from || !to) return `${formatDate(a, opts)} – ${formatDate(b, opts)}`;
  const year = opts.year ? ` ${to.year}` : "";
  if (from.year !== to.year) return `${formatDate(a, { year: true })} – ${formatDate(b, { year: true })}`;
  if (from.month !== to.month) return `${formatDate(a)} – ${formatDate(b)}${year}`;
  if (from.day === to.day) return formatDate(b, opts);
  return `${from.day}–${to.day} ${MONTHS[to.month - 1]}${year}`;
}

function finiteNumber(n: number | null | undefined): n is number {
  return typeof n === "number" && Number.isFinite(n);
}

/** "0.37 s". A value that rounds to zero loses its sign ("0.00 s", never "-0.00 s"). */
export function seconds(n: number | null | undefined, digits = 2): string {
  if (!finiteNumber(n)) return MISSING;
  const fixed = n.toFixed(digits);
  return `${Number(fixed) === 0 ? fixed.replace("-", "") : fixed} s`;
}

/**
 * A lap time: "1:21.345", or "59.123" under a minute. Rounded to the millisecond first, so
 * 59.9996 s reads "1:00.000", not "60.000".
 */
export function lapTime(s: number | null | undefined): string {
  if (!finiteNumber(s) || s < 0) return MISSING;
  const ms = Math.round(s * 1000);
  const minutes = Math.floor(ms / 60000);
  const rest = ((ms - minutes * 60000) / 1000).toFixed(3);
  return minutes ? `${minutes}:${rest.padStart(6, "0")}` : rest;
}
