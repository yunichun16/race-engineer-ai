/**
 * The pairs the /styles page suggests (plan 9.3): the page's default, two teammate battles from
 * the 2026 report with a finding to show, and a pair from different teams to show what the
 * "Different cars" warning means.
 *
 * Titles and notes carry no numbers. A finding's numbers come from `content/claims.ts`: `text` is
 * the sentence the report writes ("6.1 m [3.1, 9.2], at 13 of 15 events") and `bar` its table
 * cell, A minus B with the interval ("-6.1 [-9.2, -3.1]"), which the mini bar draws. Both are
 * checked against `report/m4_style.md` by `claims.test.ts`, and `style-examples.test.ts` checks
 * that they name this pair, this season and the direction the title states.
 *
 * The 2025 pairs aren't in a report: the default is the API's own example, and `ver-nor-2025`
 * was checked against the running API (teams differ, so the tool answers with its warning). If
 * that ever fails, swap in another 2025 pair from different teams.
 */

import type { ClaimId } from "./claims.ts";
import { stylesHref } from "../components/styles/query.ts";

export interface StyleFinding {
  /** The report's sentence: "6.1 m [3.1, 9.2], at 13 of 15 events". */
  text: ClaimId;
  /** The table cell, A minus B: "-6.1 [-9.2, -3.1]". */
  bar: ClaimId;
  /** What the bar measures, for its label: "braking point". */
  metric: string;
  /** The words for each direction, as the report writes them: A "brakes earlier" / "brakes later". */
  less: string;
  more: string;
}

export interface StyleExample {
  id: string; // "<a>-<b>-<year>", lower case
  year: number;
  a: string;
  b: string;
  /** "Ferrari", or both teams for drivers of different cars. */
  team: string;
  /** The teaser: "Hamilton brakes earlier than Leclerc." */
  title: string;
  /** One plain line, no numbers, for an example without a finding. */
  note?: string;
  finding?: StyleFinding;
  /** Drivers of different teams: the page shows its "Different cars" warning. */
  crossTeam?: boolean;
}

export const STYLE_EXAMPLES: readonly StyleExample[] = [
  {
    id: "ham-lec-2025",
    year: 2025,
    a: "HAM",
    b: "LEC",
    team: "Ferrari",
    title: "Hamilton against Leclerc in the same car.",
    note: "Teammates for the whole season: the pair this page opens on.",
  },
  {
    id: "ham-lec-2026",
    year: 2026,
    a: "HAM",
    b: "LEC",
    team: "Ferrari",
    title: "Hamilton brakes earlier than Leclerc.",
    finding: { text: "hamBrakesEarlierText", bar: "hamBrakesEarlier", metric: "braking point", less: "brakes earlier", more: "brakes later" },
  },
  {
    id: "alb-sai-2026",
    year: 2026,
    a: "ALB",
    b: "SAI",
    team: "Williams",
    title: "Albon coasts more than Sainz.",
    finding: { text: "albCoastsMore", bar: "albCoastsMoreCell", metric: "coasting", less: "coasts less", more: "coasts more" },
  },
  {
    id: "ver-nor-2025",
    year: 2025,
    a: "VER",
    b: "NOR",
    team: "Red Bull Racing and McLaren",
    title: "Different cars: what the warning means.",
    note: "Drivers of different teams can be compared, but their differences mix the car with the driver.",
    crossTeam: true,
  },
];

/** The /styles link that opens an example. */
export function exampleHref(e: StyleExample): string {
  return stylesHref({ year: e.year, a: e.a, b: e.b });
}

/**
 * A signed table cell, "-6.1 [-9.2, -3.1]" or "+6.3 [+3.5, +9.1]", as numbers; null for anything
 * else, or when the estimate isn't inside its interval.
 */
export function parseCell(value: string): { est: number; lo: number; hi: number } | null {
  const num = "([+-]?\\d+(?:\\.\\d+)?)";
  const m = new RegExp(`^${num} \\[${num}, ${num}\\]$`).exec(value.trim());
  if (!m) return null;
  const [est, lo, hi] = m.slice(1).map(Number);
  return lo <= est && est <= hi ? { est, lo, hi } : null;
}
