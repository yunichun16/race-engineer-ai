/**
 * The trace check for the technical report and the model card (plan 10.3). Both are assembled
 * from the other reports, so every number they state must be one a source states: each number in
 * a paragraph, a list item or a table row must appear, written the same way, in a file that the
 * same block links, or that its section's "Sources:" line links. A section's line covers its
 * subsections; the line under the title covers only the text before the first `## `. A list item
 * also counts the links of the items it sits in, and a table row those of the header. A source
 * that changes turns `trace.test.ts` red until the report is fixed.
 *
 * Numbers are compared as tokens (`numberTokens`, the same on both sides), never as substrings:
 * "0.37" doesn't find "3" or "37", "0.8" isn't "0.80" and "-0.10" isn't "0.10". Not counted:
 * headings, code (inline or fenced), digits that are part of a name ("F1", "RQ1", "M7", "d64"),
 * the years 2022-2027, and the few numbers in `TRACE_EXEMPT`, each with its reason.
 *
 * A source is a repository file: a published report, or a file outside `report/` such as
 * `README.md` or `engine/configs/ablations.toml`. Nothing else in `report/` is: not an excluded
 * report (`m4_review.md`, `m7_chat_accuracy.md`) nor the review's data files. The site doesn't
 * publish them, so a number taken from one would reach a page with no source a reader can open,
 * and decision 13 links the chat accuracy report without quoting it.
 *
 * The test runs the check twice: on each file as the repository holds it, against its sources as
 * written; and on the page as the site renders it (omitted sections taken out), against what the
 * site shows of each source (its omitted sections taken out too). The second run keeps the human
 * review off the site: a number outside "Human-checked precision" must come from a part of a
 * source the site shows.
 */
import { resolveLink } from "./links.ts";
import { reportBySlug } from "./manifest.ts";
import { walkBlocks, type Block, type Inline } from "./markdown.ts";
import type { PageBlock } from "./omit.ts";

/** The report files whose numbers are traced. */
export const TRACED: readonly string[] = ["technical_report.md", "model_card.md"];

export interface Exemption {
  file: string; // in report/
  token: string; // as `numberTokens` gives it
  reason: string;
}

/**
 * Numbers that may stand without a source, at most 10, each with why. The test fails on an entry
 * that no longer matches anything, so the list can't go stale.
 */
export const TRACE_EXEMPT: readonly Exemption[] = [];

export const MAX_EXEMPT = 10;

// A number as the reports write them: a sign, digits with or without thousands separators, any
// decimal parts ("16.3.7" is one token), an exponent ("1.1e-05") and a percent sign.
const NUMBER = /[-+−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)*(?:e[-+]?\d+)?%?/gu;
// What a number can't follow, or it is part of a name or a longer number.
const ATTACHED = /[\p{L}\p{N}_.]/u;
// What a hyphen joins to a number rather than signing it: "2022-2024", "top-1", "0.6%-1.0%".
const JOINS = /[\p{L}\p{N}_%)]/u;
const YEAR = /^202[2-7]$/;

/** Every number in some text, as written. */
export function numberTokens(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(NUMBER)) {
    let token = m[0];
    let at = m.index;
    if (/^[-+−]/.test(token) && JOINS.test(text[at - 1] ?? "")) {
      token = token.slice(1);
      at += 1;
    }
    if (ATTACHED.test(text[at - 1] ?? "")) continue;
    out.push(token);
  }
  return out;
}

/** A year of the data or the project, which needs no source. */
export function isYear(token: string): boolean {
  return YEAR.test(token);
}

/**
 * The repository path of a link that can be a source, or null: another report (published, not
 * excluded) or a file in the repository. Anchors, figures and web addresses are never sources.
 */
export function sourcePath(href: string): string | null {
  const target = resolveLink(href);
  if (target.kind === "report") {
    const entry = reportBySlug(target.slug);
    return entry ? `report/${entry.file}` : null;
  }
  // Under report/, only a published report is a source: not an excluded one, nor the review's
  // data files (`m4_review_queue.csv` and the like).
  if (target.kind !== "repo" || target.path.startsWith("report/")) return null;
  return target.path;
}

/** One block whose numbers are checked. */
export interface TraceUnit {
  section: string; // the heading it sits under; "" before the first
  text: string; // its text without code, for messages
  numbers: string[]; // what has to be traced (years left out)
  sources: string[]; // repository paths, the block's own links first
}

/** The text of some inlines with code left out (a space in its place, so numbers either side don't join). */
function prose(nodes: Inline[]): string {
  let out = "";
  for (const n of nodes) {
    if (n.type === "text") out += n.text;
    else if (n.type === "code") out += " ";
    else out += prose(n.children);
  }
  return out;
}

function linksIn(nodes: Inline[], out: string[] = []): string[] {
  for (const n of nodes) {
    if (n.type === "link") {
      const path = sourcePath(n.href);
      if (path !== null && !out.includes(path)) out.push(path);
    }
    if (n.type === "link" || n.type === "strong" || n.type === "em") linksIn(n.children, out);
  }
  return out;
}

function unite(...lists: readonly string[][]): string[] {
  return [...new Set(lists.flat())];
}

const SOURCES_LINE = /^\s*Sources?:/;

/** The units of a report's blocks (as parsed, or as a page with omitted sections), in order. */
export function traceUnits(blocks: readonly PageBlock[]): TraceUnit[] {
  // Sections: the title's (index 0, level 1), then one per heading. A heading's parent is the
  // nearest open section above it of a lower level; the title's line is never inherited.
  const sections: { title: string; level: number; parent: number; own: string[] }[] = [
    { title: "", level: 1, parent: -1, own: [] },
  ];
  const sectionOf: number[] = [];
  let current = 0;
  for (const b of blocks) {
    if (b.type === "heading") {
      let parent = current;
      while (parent > 0 && sections[parent].level >= b.level) parent = sections[parent].parent;
      sections.push({ title: b.text, level: b.level, parent: parent === 0 ? -1 : parent, own: [] });
      current = sections.length - 1;
    } else if (b.type === "paragraph" && SOURCES_LINE.test(prose(b.children))) {
      sections[current].own.push(...linksIn(b.children));
    }
    sectionOf.push(current);
  }
  const inherited = (k: number): string[] => {
    const out: string[] = [];
    for (let s = k; s >= 0; s = sections[s].parent) out.push(...sections[s].own);
    return unite(out);
  };

  const units: TraceUnit[] = [];
  const add = (section: string, text: string, own: string[], from: string[]) => {
    const numbers = numberTokens(text).filter((t) => !isYear(t));
    if (numbers.length > 0) units.push({ section, text: text.replace(/\s+/g, " ").trim(), numbers, sources: unite(own, from) });
  };
  const visit = (b: Block, section: string, from: string[]) => {
    if (b.type === "paragraph") add(section, prose(b.children), linksIn(b.children), from);
    else if (b.type === "image") add(section, b.alt, [], from);
    else if (b.type === "table") {
      const head = unite(...b.head.map((cell) => linksIn(cell)));
      add(section, b.head.map(prose).join(" | "), head, from);
      for (const row of b.rows) add(section, row.map(prose).join(" | "), unite(head, ...row.map((cell) => linksIn(cell))), from);
    } else if (b.type === "list") {
      for (const item of b.items) {
        const own: string[] = [];
        walkBlocks(
          item.filter((x) => x.type !== "list"),
          (x) => {
            if (x.type === "paragraph") linksIn(x.children, own);
          },
        );
        const text = item.flatMap((x) => (x.type === "paragraph" ? [prose(x.children)] : [])).join(" ");
        add(section, text, own, from);
        for (const x of item) if (x.type !== "paragraph") visit(x, section, unite(own, from));
      }
    }
    // Headings, code blocks and rules hold nothing to trace.
  };
  blocks.forEach((b, k) => {
    if (b.type === "omitted") return;
    visit(b, sections[sectionOf[k]].title, inherited(sectionOf[k]));
  });
  return units;
}

export interface Untraced {
  section: string;
  text: string;
  token: string;
  sources: string[];
}

/**
 * The numbers that no source of their block states. `tokensOf` gives a source's numbers, or null
 * when the file can't be read (every number of that block then counts as untraced).
 */
export function untraced(units: readonly TraceUnit[], tokensOf: (path: string) => ReadonlySet<string> | null): Untraced[] {
  const out: Untraced[] = [];
  for (const u of units) {
    const sets = u.sources.map(tokensOf).filter((s): s is ReadonlySet<string> => s !== null);
    for (const token of u.numbers) {
      if (!sets.some((s) => s.has(token))) out.push({ section: u.section, text: u.text, token, sources: u.sources });
    }
  }
  return out;
}

/** Drop the exempt numbers of one file; also returns the exemptions nothing matched. */
export function applyExemptions(
  file: string,
  found: readonly Untraced[],
  exempt: readonly Exemption[] = TRACE_EXEMPT,
): { remaining: Untraced[]; unused: Exemption[] } {
  const mine = exempt.filter((e) => e.file === file);
  const used = new Set<Exemption>();
  const remaining = found.filter((u) => {
    const hit = mine.find((e) => e.token === u.token);
    if (hit) used.add(hit);
    return !hit;
  });
  return { remaining, unused: mine.filter((e) => !used.has(e)) };
}

/** One line per untraced number, for a test's failure message. */
export function describeUntraced(file: string, u: Untraced): string {
  const at = u.text.indexOf(u.token);
  const from = Math.max(0, at - 50);
  const snippet = `${from > 0 ? "…" : ""}${u.text.slice(from, at + u.token.length + 50)}${at + u.token.length + 50 < u.text.length ? "…" : ""}`;
  const where = u.sources.length > 0 ? `not in ${u.sources.join(", ")}` : "no source linked (link one in the block, or add a Sources: line)";
  return `${file} › ${u.section || "(before the first section)"}: "${u.token}" in "${snippet}": ${where}`;
}

/**
 * Everything the site shows of a report, as text: the title, the generated line and every block
 * left after its omissions, code included. A source's published text, for the site-copy check.
 */
export function pageText(page: { titleInlines: Inline[]; meta: Inline[] | null; blocks: readonly PageBlock[] }): string {
  const all = (nodes: Inline[]): string =>
    nodes.map((n) => (n.type === "text" || n.type === "code" ? n.text : all(n.children))).join("");
  const parts: string[] = [all(page.titleInlines), all(page.meta ?? [])];
  const visit = (b: Block) => {
    if (b.type === "heading" || b.type === "paragraph") parts.push(all(b.children));
    else if (b.type === "table") for (const row of [b.head, ...b.rows]) parts.push(row.map(all).join(" | "));
    else if (b.type === "code") parts.push(b.text);
    else if (b.type === "image") parts.push(b.alt);
    else if (b.type === "list") for (const item of b.items) item.forEach(visit);
  };
  for (const b of page.blocks) if (b.type !== "omitted") visit(b);
  return parts.join("\n");
}
