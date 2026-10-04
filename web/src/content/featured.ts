/**
 * The corners the site shows without being asked (plan 9.2 as amended in plan 13): the landing
 * page's example (entry 0) and the /mistakes page's strip of examples.
 *
 * Entries of kind "flagged" are corners the detectors put in their session's most unusual 1% and
 * the explanation rules typed and costed; each is a row of `report/m4_review_queue.csv`, the
 * tool's own ranking of 2026's top findings. The others show what isn't a driving mistake: a
 * lift that is likely energy management, a cut that saved time, and a session where nothing was
 * flagged. The picks follow the tool's ranking and the mistake type only. The site publishes no
 * reviewer verdicts, so nothing here records one, and no blurb mentions a reviewer, a replay
 * check or a verdict. Blurbs carry no numbers either: the numbers come from the tool's answer.
 *
 * `featured.test.ts` checks every entry against those report files, and `site-smoke` checks
 * them live: an entry that fails is dropped, not reworded.
 *
 * Reserves if a live check fails (not yet checked against the files): GAS, 2025 Abu Dhabi R,
 * lap 5, T8; OCO, 2026 Canadian (Montreal) S, lap 19, T8; ALB lap 51 T11 and ALO lap 57 T16,
 * 2023 Monaco R.
 */

import { eventShort, sessionName } from "../lib/format.ts";
import { withQuery } from "../lib/url.ts";

export type FeaturedKind = "flagged" | "energy" | "saved-time" | "nothing-flagged";

export type FeaturedSession = "Q" | "R" | "S" | "SQ";

export interface FeaturedCorner {
  /** The review queue's id for a flagged corner; also the snapshot's file name. */
  id: string;
  kind: FeaturedKind;
  args: { event: string; year: number; session: FeaturedSession; driver: string; lap?: number; turn?: string };
  /**
   * The driver's name for prose ("where Norris lost eight tenths") and the hero's captions and
   * picker labels; the code stands in without it.
   */
  name?: string;
  /** The lap was looked at while the explanation rules were built (the queue's `inspected`). */
  inspected?: boolean;
  /** The explanation's type as the tools word it; absent for a whole session. */
  typeLabel?: string;
  /** One or two plain sentences, with no numbers, no reviewer and no verdict. */
  blurb: string;
}

export const FEATURED: readonly FeaturedCorner[] = [
  {
    // The landing example. The tool lists it first among the flagged corners of the 2026
    // Azerbaijan race (find_mistakes, the whole field), the latest race in the data; it is the
    // over-slowing type, whose evidence reads straight off the speed trace; a well-known driver;
    // and its lap wasn't looked at while the rules were built. Queue rank 29.
    id: "2026_15_R_1_16_T5",
    kind: "flagged",
    args: { event: "Azerbaijan Grand Prix", year: 2026, session: "R", driver: "NOR", lap: 16, turn: "5" },
    name: "Norris",
    inspected: false,
    typeLabel: "over-slowing",
    blurb: "Over-slowing: he took the corner slower than his usual and lost the time on the way out.",
  },
  {
    // Over-slowing again, high in the queue (rank 11). Madrid's map is the one north-up fallback,
    // so it isn't the landing example.
    id: "2026_14_R_23_55_T3",
    kind: "flagged",
    args: { event: "Spanish Grand Prix", year: 2026, session: "R", driver: "ALB", lap: 55, turn: "3" },
    name: "Albon",
    inspected: false,
    typeLabel: "over-slowing",
    blurb: "Over-slowing: he took the corner slower than his usual, losing the time mostly through the apex.",
  },
  {
    // The queue's highest-ranked race-control finding (rank 3).
    id: "2026_01_R_55_53_T3",
    kind: "flagged",
    args: { event: "Australian Grand Prix", year: 2026, session: "R", driver: "SAI", lap: 53, turn: "3" },
    name: "Sainz",
    inspected: false,
    typeLabel: "track limits / off track (race control)",
    blurb: "Race control deleted the lap for track limits at this corner, and he lost time on the way out.",
  },
  {
    // Monza's first chicane (rank 10).
    id: "2026_13_R_27_22_T1",
    kind: "flagged",
    args: { event: "Italian Grand Prix", year: 2026, session: "R", driver: "HUL", lap: 22, turn: "1" },
    name: "Hülkenberg",
    inspected: false,
    typeLabel: "track limits / off track (race control)",
    blurb: "Race control deleted the lap for track limits at Monza's first chicane, and he lost time on the way out.",
  },
  {
    // A qualifying lap (rank 14), so the strip isn't all races.
    id: "2026_09_Q_55_2_T3",
    kind: "flagged",
    args: { event: "British Grand Prix", year: 2026, session: "Q", driver: "SAI", lap: 2, turn: "3" },
    name: "Sainz",
    inspected: true,
    typeLabel: "track limits / off track (race control)",
    blurb: "Race control deleted the qualifying lap for track limits at this corner, and he was slower on the way out.",
  },
  {
    // m4_mistakes.md, Examples: "likely energy management (the 2026 cars harvest by lifting), not a mistake".
    id: "ant-2026-italian-r-23-4",
    kind: "energy",
    args: { event: "Italian Grand Prix", year: 2026, session: "R", driver: "ANT", lap: 23, turn: "4" },
    typeLabel: "lift and coast (likely energy or fuel management)",
    blurb: "Flagged for lifting and coasting into the corner. The explanation reads it as likely energy management, which the new cars need, not a driving mistake.",
  },
  {
    // m4_mistakes.md, Known incidents: "listed #1 of 1 for RUS", "not slower than usual (0.73 s quicker)".
    id: "rus-2025-monaco-r-48-10",
    kind: "saved-time",
    args: { event: "Monaco Grand Prix", year: 2025, session: "R", driver: "RUS", lap: 48, turn: "10" },
    typeLabel: "track limits / off track (race control)",
    blurb: "He cut the chicane: race control deleted the lap for track limits, but the cut saved time rather than losing it.",
  },
  {
    // Not in a report: the M5 smoke run's find_mistakes answer says "LEC: 0 of 66 scored corners
    // flagged" (M5 scratchpad, fchk2_smoke_url2.log). site-smoke checks it live. His laps were
    // scored, so this is a finding, not missing data ("no verdict" means no scored laps).
    id: "lec-2025-italian-q",
    kind: "nothing-flagged",
    args: { event: "Italian Grand Prix", year: 2025, session: "Q", driver: "LEC" },
    blurb: "Nothing flagged: none of his scored corners in this session stood out. His laps were scored, so this is a finding, not missing data.",
  },
];

/** The landing page's example. */
export const LANDING_CORNER: FeaturedCorner = FEATURED[0];

const KIND_TAGS: Record<Exclude<FeaturedKind, "flagged">, string> = {
  energy: "Energy management",
  "saved-time": "Saved time",
  "nothing-flagged": "Nothing flagged",
};

/**
 * A card's tags: the mistake type, plus what the example shows for the other kinds. Never a
 * verdict, a "checked" badge or a number (plan 13).
 */
export function featuredTags(f: FeaturedCorner): string[] {
  const tags: string[] = [];
  if (f.typeLabel) tags.push(typeTag(f.typeLabel));
  if (f.kind !== "flagged") tags.push(KIND_TAGS[f.kind]);
  return tags;
}

/** The type as a short tag: "Track limits" for "track limits / off track (race control)". */
export function typeTag(typeLabel: string): string {
  const short = typeLabel.split(/ \/ | \(/)[0].trim();
  return short.charAt(0).toUpperCase() + short.slice(1);
}

/** The card's title: "NOR · Azerbaijan GP 2026 · Race · lap 16, T5" (no lap or turn for a whole session). */
export function featuredTitle(f: FeaturedCorner): string {
  const { driver, event, year, session, lap, turn } = f.args;
  const head = `${driver} · ${eventShort(event)} ${year} · ${sessionName(session)}`;
  return lap !== undefined && turn !== undefined ? `${head} · lap ${lap}, T${turn}` : head;
}

/** The open corner in the explorer's URL: "NOR:16:5" (turn upper-cased, no leading "T"). */
export function explainParam(driver: string, lap: number, turn: string): string {
  return `${driver}:${lap}:${turn.replace(/^t/i, "").toUpperCase()}`;
}

/**
 * The entry in the mistake explorer: its session with the corner open, or for a whole session
 * that driver's list. The keys are the explorer's URL state (plan 7.3).
 */
export function featuredHref(f: FeaturedCorner): string {
  const { event, year, session, driver, lap, turn } = f.args;
  const corner = lap !== undefined && turn !== undefined;
  return withQuery("/mistakes", {
    year,
    event,
    session,
    driver: corner ? undefined : driver,
    explain: corner ? explainParam(driver, lap, turn) : undefined,
  });
}

/** The explain_corner call that draws an entry, or null for a whole session. */
export function cornerParams(f: FeaturedCorner): {
  event: string;
  driver: string;
  lap: number;
  corner: string;
  year: number;
  session: FeaturedSession;
} | null {
  const { event, year, session, driver, lap, turn } = f.args;
  if (lap === undefined || turn === undefined) return null;
  return { event, driver, lap, corner: turn, year, session };
}
