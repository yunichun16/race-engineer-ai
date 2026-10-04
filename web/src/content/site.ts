/**
 * Site-wide names, links and credit: the header, the footer and the landing's "How it's built"
 * read them from here.
 */

import { REPO_URL } from "../lib/env.ts";

export interface CreditLink {
  label: string;
  href: string;
}

export interface Credit {
  name: string;
  links: readonly CreditLink[];
}

export interface Site {
  name: string;
  /** Who built it, as the author asked to be credited; null shows no credit line. */
  credit: Credit | null;
  repoUrl: string | null;
  fastf1Url: string;
  licence: string;
  unofficial: string;
  /** The site quotes no accuracy figure yet; this line says so wherever the footer's lines go. */
  caveat: string;
  /** Both of those in one short line, for small spaces: the foot of the phone menu. */
  shortCaveat: string;
}

export const site: Site = {
  name: "Race Engineer AI",
  credit: {
    name: "Yuchun Wu",
    links: [
      { label: "GitHub", href: "https://github.com/yunichun16" },
      { label: "LinkedIn", href: "https://www.linkedin.com/in/yuchun-wu" },
    ],
  },
  repoUrl: REPO_URL,
  fastf1Url: "https://github.com/theOehrly/Fast-F1",
  licence: "MIT",
  unofficial: "Unofficial fan project, not affiliated with Formula 1 or the FIA.",
  caveat: "A fan project: findings can be wrong.",
  shortCaveat: "Unofficial fan project. Findings can be wrong.",
};
