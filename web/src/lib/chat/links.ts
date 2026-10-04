// Links and requests built from chart payloads (plan section 7.2), never from the model's text:
// the payload is what the tool computed, while the text is what the model chose to say.
//
// - "Open in Mistakes" / "Open in Styles" under each chart card, in the URL format the
//   explorers parse (plan 7.3 and 7.4). A value the explorer would drop is left out here, and a
//   card with no usable session gets no link at all.
// - The explain_corner arguments for "Show telemetry" inside a chat find-mistakes card.
// - The "Ask about this corner" question, which only ever prefills the composer.
//
// The payload types are read field by field from `unknown` (chat chart data is only checked by
// each chart's own guard when it draws), so a card can't throw while building its links.

import { withQuery } from "../url.ts";

export type SessionCode = "Q" | "SQ" | "SS" | "S" | "R";

const SESSION_CODES: readonly string[] = ["Q", "SQ", "SS", "S", "R"];
const DRIVER = /^[A-Z]{3}$/;
const TURN = /^\d{1,2}[A-Z]?$/;

export interface ExplorerLink {
  href: string;
  label: "Open in Mistakes" | "Open in Styles";
}

/** explain_corner's REST arguments (lib/api ToolParams["explain_corner"]). */
export interface ExplainCornerArgs {
  event: string;
  driver: string;
  lap: number;
  corner: string | number;
  year?: number;
  session?: SessionCode;
}

// ---- Field readers: each returns null for a value the explorers wouldn't accept ----

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function year(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 2018 && value <= 2030
    ? value
    : null;
}

function eventName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const name = value.trim();
  return name !== "" && name.length <= 100 ? name : null;
}

function session(value: unknown): SessionCode | null {
  return typeof value === "string" && SESSION_CODES.includes(value) ? (value as SessionCode) : null;
}

function driver(value: unknown): string | null {
  return typeof value === "string" && DRIVER.test(value) ? value : null;
}

function lap(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 ? value : null;
}

/** A turn label as the explorer's `explain` key writes it: upper case, no leading "T". */
export function normaliseTurn(value: unknown): string | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const turn = String(value).trim().toUpperCase().replace(/^T/, "");
  return TURN.test(turn) ? turn : null;
}

/** A path and its query in the site's canonical form (lib/url.ts: empty values dropped, keys
 *  sorted, ":" kept), the same form the explorers write into the address bar themselves. */
function href(path: string, pairs: [string, string | number | null][]): string {
  return withQuery(path, Object.fromEntries(pairs));
}

// ---- Explorer links, one per chart ----

/** /mistakes?year&event&session[&driver] */
export function findMistakesHref(data: unknown): string | null {
  const d = record(data);
  if (d === null) return null;
  const y = year(d.year);
  const e = eventName(d.event);
  const s = session(d.session_code);
  if (y === null || e === null || s === null) return null;
  return href("/mistakes", [
    ["year", y],
    ["event", e],
    ["session", s],
    ["driver", driver(d.driver)],
  ]);
}

/** The find-mistakes link for the corner's driver, plus &explain=DRV:LAP:TURN. */
export function explainCornerHref(data: unknown): string | null {
  const d = record(data);
  if (d === null) return null;
  const y = year(d.year);
  const e = eventName(d.event);
  const s = session(d.session_code);
  if (y === null || e === null || s === null) return null;
  const who = driver(d.driver);
  const l = lap(d.lap_number);
  const t = normaliseTurn(d.turn);
  const explain = who !== null && l !== null && t !== null ? `${who}:${l}:${t}` : null;
  return href("/mistakes", [
    ["year", y],
    ["event", e],
    ["session", s],
    ["driver", who],
    ["explain", explain],
  ]);
}

/** /mistakes?year&event&session=R|S */
export function raceSummaryHref(data: unknown): string | null {
  const d = record(data);
  if (d === null) return null;
  const y = year(d.year);
  const e = eventName(d.event);
  const s = session(d.session_code);
  if (y === null || e === null || (s !== "R" && s !== "S")) return null;
  return href("/mistakes", [
    ["year", y],
    ["event", e],
    ["session", s],
  ]);
}

/** /styles?year&a&b (two different drivers). */
export function compareStylesHref(data: unknown): string | null {
  const d = record(data);
  if (d === null) return null;
  const y = year(d.year);
  const a = driver(d.driver_a);
  const b = driver(d.driver_b);
  if (y === null || a === null || b === null || a === b) return null;
  return href("/styles", [
    ["year", y],
    ["a", a],
    ["b", b],
  ]);
}

/** /styles?year&a&b&laps={event}: the pair from the lap comparison, and its weekend. */
export function compareLapsHref(data: unknown): string | null {
  const d = record(data);
  if (d === null) return null;
  const y = year(d.year);
  const e = eventName(d.event);
  const drivers = Array.isArray(d.drivers) ? d.drivers : [];
  const a = driver(record(drivers[0])?.code);
  const b = driver(record(drivers[1])?.code);
  if (y === null) return null;
  const pair = a !== null && b !== null && a !== b;
  return href("/styles", [
    ["year", y],
    ["a", pair ? a : null],
    ["b", pair ? b : null],
    ["laps", e],
  ]);
}

const BUILDERS: Record<string, { build: (data: unknown) => string | null; label: ExplorerLink["label"] }> = {
  "find-mistakes": { build: findMistakesHref, label: "Open in Mistakes" },
  "explain-corner": { build: explainCornerHref, label: "Open in Mistakes" },
  "race-summary": { build: raceSummaryHref, label: "Open in Mistakes" },
  "compare-styles": { build: compareStylesHref, label: "Open in Styles" },
  "compare-laps": { build: compareLapsHref, label: "Open in Styles" },
};

/** The explorer link under a chart card, or null when the chart has none. */
export function explorerLink(chart: { bundle: string; data: unknown } | null | undefined): ExplorerLink | null {
  if (!chart) return null;
  const builder = Object.hasOwn(BUILDERS, chart.bundle) ? BUILDERS[chart.bundle] : undefined;
  if (builder === undefined) return null;
  const built = builder.build(chart.data);
  return built === null ? null : { href: built, label: builder.label };
}

const LINK_FIELDS = [
  "year",
  "event",
  "session",
  "session_code",
  "driver",
  "lap_number",
  "turn",
  "driver_a",
  "driver_b",
] as const;

/**
 * The few payload fields the links above read, for a chart whose data can't be kept in storage
 * (persist.ts): the card can still offer "Open it in the explorer".
 */
export function linkFields(data: unknown): Record<string, unknown> {
  const d = record(data);
  const out: Record<string, unknown> = {};
  if (d === null) return out;
  for (const key of LINK_FIELDS) {
    const value = d[key];
    if (typeof value === "string" || typeof value === "number") out[key] = value;
  }
  if (Array.isArray(d.drivers)) {
    out.drivers = d.drivers.slice(0, 2).map((item) => ({ code: record(item)?.code ?? null }));
  }
  return out;
}

// ---- Show telemetry and Ask about this corner ----

/** explain_corner's arguments for one row of a find-mistakes payload ("Show telemetry"). */
export function explainCornerArgs(list: unknown, mistake: unknown): ExplainCornerArgs | null {
  const l = record(list);
  const m = record(mistake);
  if (l === null || m === null) return null;
  const e = eventName(l.event);
  const who = driver(m.driver);
  const n = lap(m.lap_number);
  const t = typeof m.turn === "string" || typeof m.turn === "number" ? m.turn : null;
  if (e === null || who === null || n === null || t === null || normaliseTurn(t) === null) return null;
  const args: ExplainCornerArgs = { event: e, driver: who, lap: n, corner: t };
  const y = year(l.year);
  const s = session(l.session_code);
  if (y !== null) args.year = y;
  if (s !== null) args.session = s;
  return args;
}

/** "Explain NOR's turn 9A on lap 16 at the 2025 Abu Dhabi Grand Prix (Qualifying)." from an
 *  explain-corner payload, for the composer (plan 7.2 and 7.3). */
export function cornerQuestion(data: unknown): string | null {
  const d = record(data);
  if (d === null) return null;
  const who = driver(d.driver);
  const t = normaliseTurn(d.turn);
  const n = lap(d.lap_number);
  const y = year(d.year);
  const e = eventName(d.event);
  if (who === null || t === null || n === null || y === null || e === null) return null;
  const name = typeof d.session === "string" && d.session.trim() !== "" ? d.session.trim() : session(d.session_code);
  const where = name ? ` (${name})` : "";
  return `Explain ${who}'s turn ${t} on lap ${n} at the ${y} ${e}${where}.`;
}

/** /chat?q=…, which only prefills the composer (plan decision 8). */
export function chatPrefillHref(question: string): string {
  return href("/chat", [["q", question]]);
}
