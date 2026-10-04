/**
 * The "On this page" list for a report: its h2 and h3 headings, in order, the title left out.
 * A page with fewer than two shows no list (a one-table report needs none).
 */
import type { Heading } from "./markdown.ts";

export interface TocItem {
  id: string;
  text: string;
  level: 2 | 3;
}

export function tocItems(headings: readonly Heading[]): TocItem[] {
  const items = headings.flatMap((h): TocItem[] =>
    h.level === 2 || h.level === 3 ? [{ id: h.id, text: h.text, level: h.level }] : [],
  );
  return items.length >= 2 ? items : [];
}
