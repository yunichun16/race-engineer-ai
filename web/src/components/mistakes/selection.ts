/**
 * The /mistakes page's choices, worked out from its URL (`query.ts`) and the session catalog:
 * which session to show when the link names none, what each picker lists, and the words the page
 * puts around the charts. Pure, so `selection.test.ts` covers it without a browser.
 */

import type { CornerChart } from "../../charts/explain-corner.ts";
import type { MistakeList } from "../../charts/find-mistakes.ts";
import { FEATURED, type FeaturedCorner } from "../../content/featured.ts";
import type { CatalogSeason, CatalogWeekend, SessionCatalog, SessionCode, SessionDrivers } from "../../lib/api/types.ts";
import { eventShort, formatDate, sessionName } from "../../lib/format.ts";
import { withQuery } from "../../lib/url.ts";
import { normalizeTurn, type ExplainRef, type MistakesQuery } from "./query.ts";

/** A session as the tools name it. */
export interface Selection {
  year: number;
  event: string;
  session: SessionCode;
}

function scored(weekend: CatalogWeekend, code: SessionCode): boolean {
  return weekend.sessions.some((s) => s.code === code && s.scored);
}

/**
 * The session to show for a weekend: its race when the race is scored, else its qualifying when
 * that is, else its first scored session, else the race (or its last session) for the page to
 * say why there is nothing.
 */
export function defaultSession(weekend: CatalogWeekend): SessionCode {
  if (scored(weekend, "R")) return "R";
  if (scored(weekend, "Q")) return "Q";
  const first = weekend.sessions.find((s) => s.scored);
  if (first) return first.code;
  if (weekend.sessions.some((s) => s.code === "R")) return "R";
  return weekend.sessions[weekend.sessions.length - 1]?.code ?? "R";
}

/** A season's newest weekend with a scored race, else with a scored qualifying, else its newest. */
export function defaultWeekend(season: CatalogSeason): CatalogWeekend | undefined {
  return (
    season.weekends.find((w) => scored(w, "R")) ?? season.weekends.find((w) => scored(w, "Q")) ?? season.weekends[0]
  );
}

/** The session a weekend opens on. */
export function weekendSelection(year: number, weekend: CatalogWeekend): Selection {
  return { year, event: weekend.event, session: defaultSession(weekend) };
}

export function findSeason(catalog: SessionCatalog, year: number): CatalogSeason | undefined {
  return catalog.seasons.find((s) => s.year === year);
}

export function findWeekend(catalog: SessionCatalog, year: number, event: string): CatalogWeekend | undefined {
  return findSeason(catalog, year)?.weekends.find((w) => w.event === event);
}

/**
 * The session the page shows. A link that names the season, the event and the session is taken
 * as it is, without waiting for the catalog, so the page's first calls go out together (a
 * session the data doesn't have gets the tool's own message). Otherwise the catalog fills in what
 * is missing or not there: the newest season (or, for an event alone, the newest season with
 * it), that season's newest weekend with a scored race (else qualifying), and that weekend's race
 * (else qualifying). Null while that needs the catalog and it hasn't arrived, or it is empty.
 */
export function resolveSelection(query: MistakesQuery, catalog: SessionCatalog | null): Selection | null {
  if (query.year !== undefined && query.event && query.session) {
    return { year: query.year, event: query.event, session: query.session };
  }
  if (!catalog || catalog.seasons.length === 0) return null;

  let season = query.year !== undefined ? findSeason(catalog, query.year) : undefined;
  if (!season && query.year === undefined && query.event) {
    season = catalog.seasons.find((s) => s.weekends.some((w) => w.event === query.event));
  }
  season ??= catalog.seasons[0];

  const weekend = (query.event && season.weekends.find((w) => w.event === query.event)) || defaultWeekend(season);
  if (!weekend) return null;
  const session =
    query.session && weekend.sessions.some((s) => s.code === query.session) ? query.session : defaultSession(weekend);
  return { year: season.year, event: weekend.event, session };
}

/** "2026 Azerbaijan Grand Prix|R": one key per session, for remounting what belongs to one. */
export function selectionKey(sel: Selection): string {
  return `${sel.year} ${sel.event}|${sel.session}`;
}

// --- What the pickers list -----------------------------------------------------------------------

/** "R15 · Azerbaijan Grand Prix · 26 Sep" (the date of its last session, in UTC). */
export function weekendLabel(weekend: CatalogWeekend): string {
  return `R${weekend.round} · ${weekend.event} · ${formatDate(weekend.date)}`;
}

export function hasScoredSession(weekend: CatalogWeekend): boolean {
  return weekend.sessions.some((s) => s.scored);
}

export interface PickerOption {
  value: string;
  label: string;
  disabled?: boolean;
  note?: string;
}

/**
 * The Grand Prix picker's options for a season, newest first. A weekend with nothing scored says
 * so, and can't be picked while the results are current (when they aren't, nothing is scored and
 * every weekend stays pickable, so the page can say why). An event the season doesn't list (a
 * hand-made link) is kept as an option, so the picker still shows what the page is showing.
 */
export function weekendOptions(catalog: SessionCatalog | null, year: number, current?: string): PickerOption[] {
  const season = catalog ? findSeason(catalog, year) : undefined;
  const options: PickerOption[] = (season?.weekends ?? []).map((w) => {
    const none = !hasScoredSession(w);
    return {
      value: w.event,
      label: none ? `${weekendLabel(w)} (not scored)` : weekendLabel(w),
      disabled: none && catalog?.results_current === true,
    };
  });
  if (current && !options.some((o) => o.value === current)) options.unshift({ value: current, label: current });
  return options;
}

/**
 * The Session picker's options, in the order the weekend ran them. A session the model didn't
 * score is marked "(not scored)" and disabled while the results are current.
 */
export function sessionOptions(catalog: SessionCatalog | null, sel: Selection): PickerOption[] {
  const weekend = catalog ? findWeekend(catalog, sel.year, sel.event) : undefined;
  if (!weekend) return [{ value: sel.session, label: sessionName(sel.session) }];
  return weekend.sessions.map((s) => ({
    value: s.code,
    label: sessionName(s.code),
    disabled: !s.scored && catalog?.results_current === true,
    note: s.scored ? undefined : "(not scored)",
  }));
}

/**
 * The Driver picker's rows: "Whole field", then each driver as "LEC · Ferrari", by code so typing
 * a letter in the native picker jumps to it. A driver the session doesn't list (a hand-made link)
 * is kept as a row.
 */
export function driverOptions(drivers: SessionDrivers | null, current?: string): PickerOption[] {
  const rows = [...(drivers?.drivers ?? [])].sort((a, b) => a.driver.localeCompare(b.driver));
  const options: PickerOption[] = [{ value: "", label: "Whole field" }];
  for (const d of rows) options.push({ value: d.driver, label: `${d.driver} · ${d.team}` });
  if (current && !rows.some((d) => d.driver === current)) options.push({ value: current, label: current });
  return options;
}

/** The phone's selection bar: "2026 Azerbaijan GP · Race · All drivers". */
export function selectionSummary(sel: Selection, driver?: string): string {
  return `${sel.year} ${eventShort(sel.event)} · ${sessionName(sel.session)} · ${driver ?? "All drivers"}`;
}

// --- The featured corners ----------------------------------------------------------------------

/** The explorer state a featured card opens: its corner, or for a whole session that driver's list. */
export function featuredQuery(f: FeaturedCorner): MistakesQuery {
  const { event, year, session, driver, lap, turn } = f.args;
  const corner = lap !== undefined && turn !== undefined ? normalizeTurn(turn) : null;
  return corner && lap !== undefined
    ? { year, event, session, explain: { driver, lap, turn: corner } }
    : { year, event, session, driver };
}

/**
 * A card's line under the driver code: "Baku 2026 · Race · lap 16, T5", or "· whole session".
 * The place comes from the catalog when it is here ("Baku"), else the event's short name.
 */
export function featuredLine(f: FeaturedCorner, location?: string): string {
  const { event, year, session, lap, turn } = f.args;
  const head = `${location || eventShort(event)} ${year} · ${sessionName(session)}`;
  return lap !== undefined && turn !== undefined ? `${head} · lap ${lap}, T${normalizeTurn(turn) ?? turn}` : `${head} · whole session`;
}

/** Where the catalog says an event was held that season ("Baku"), if it lists it. */
export function locationOf(catalog: SessionCatalog | null, year: number, event: string): string | undefined {
  return catalog ? findWeekend(catalog, year, event)?.location : undefined;
}

/** The featured entry for an open corner, when it is one. */
export function featuredFor(sel: Selection, ref: ExplainRef): FeaturedCorner | undefined {
  return FEATURED.find(
    (f) =>
      f.args.event === sel.event &&
      f.args.year === sel.year &&
      f.args.session === sel.session &&
      f.args.driver === ref.driver &&
      f.args.lap === ref.lap &&
      f.args.turn !== undefined &&
      normalizeTurn(f.args.turn) === ref.turn,
  );
}

// --- The words around the charts -----------------------------------------------------------------

/**
 * The one line under an open corner's heading (plan 13: no accuracy figure, no review wording).
 * A featured corner gets its own blurb. Otherwise it is keyed on the corner's answer: a corner
 * the detectors scored and didn't flag is shown for comparison; a track-limits corner is one race
 * control named; one on a lap the detectors don't score (`flagged` null: the opening lap, say)
 * says so, since nothing found it; anything else the detectors found in the telemetry alone.
 * Null until the answer is here.
 */
export function evidenceText(
  featured: FeaturedCorner | undefined,
  corner: Pick<CornerChart, "flagged" | "type"> | null,
): string | null {
  if (featured) return featured.blurb;
  if (!corner) return null;
  if (corner.flagged === false) return "Not flagged: shown for comparison.";
  if (corner.type === "track_limits") return "Race control named this moment.";
  if (corner.flagged !== true) return "Not scored by the model: shown for comparison.";
  return "Found from the telemetry alone.";
}

/** The open corner's heading: "NOR · lap 16 · turn 5". */
export function cornerHeading(ref: ExplainRef): string {
  return `${ref.driver} · lap ${ref.lap} · turn ${ref.turn}`;
}

/** "the 2026 Azerbaijan Grand Prix race": a session in running text. */
export function sessionPhrase(sel: Selection): string {
  return `the ${sel.year} ${sel.event} ${sessionName(sel.session).toLowerCase()}`;
}

/**
 * "Ask about this corner in chat": the chat with the question filled in, never sent (the chat
 * only prefills from `q`; plan decision 8).
 */
export function askInChatHref(sel: Selection, ref: ExplainRef): string {
  const q = `Explain ${ref.driver}'s turn ${ref.turn} on lap ${ref.lap} at the ${sel.year} ${sel.event} (${sessionName(sel.session)}).`;
  return withQuery("/chat", { q });
}

/** The polite announcement once a list arrives: "Loaded 10 mistakes for the 2026 Azerbaijan Grand Prix race". */
export function loadedMessage(list: Pick<MistakeList, "mistakes" | "driver" | "year" | "event" | "session">): string {
  const n = list.mistakes.length;
  const what = `${n} ${n === 1 ? "mistake" : "mistakes"}`;
  const whose = list.driver ? ` by ${list.driver}` : "";
  return `Loaded ${what}${whose} for the ${list.year} ${list.event} ${list.session.toLowerCase()}`;
}

/**
 * Whether the list needs the reminder that nothing flagged isn't no mistakes: when the
 * detectors could score little of the session, or the chosen driver had no scored laps at all.
 */
export function needsCoverageNote(list: Pick<MistakeList, "driver" | "coverage">): boolean {
  return list.coverage.low || (list.driver !== null && list.coverage.scored_driver_laps === 0);
}
