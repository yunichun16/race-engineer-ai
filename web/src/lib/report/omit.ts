/**
 * Takes the parts a manifest entry omits out of a parsed report, so the page never renders them.
 * An omitted section becomes one `omitted` block in its place (the page shows `OMITTED_NOTE`
 * there); an omitted sentence is cut out of its paragraph. Anything the entry names but the file
 * no longer holds throws, which fails the build: a changed report gets looked at rather than
 * shown with the figure back in.
 */
import type { Omission } from "./manifest.ts";
import { walkBlocks, type Block, type Heading, type Inline, type ReportDoc } from "./markdown.ts";

/** Shown where an omitted section was. */
export const OMITTED_NOTE =
  "A first human check of the 50 top findings is in the repository's copy of this report. The site doesn't quote an accuracy from it yet: one reviewer and 50 findings is too small a sample, and a larger review is planned.";

export type OmittedBlock = { type: "omitted"; section: string };

/** A report block as the page renders it: the parser's blocks, plus where a section was left out. */
export type PageBlock = Block | OmittedBlock;

export interface PageDoc extends Omit<ReportDoc, "blocks"> {
  blocks: PageBlock[];
}

/** Collapse runs of whitespace (the reports wrap their lines) for comparing text. */
export function squash(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** The report without the omitted parts. The input is left as it was. */
export function applyOmission(doc: ReportDoc, omit: Omission | undefined): PageDoc {
  const blocks: PageBlock[] = structuredClone(doc.blocks);
  if (!omit) return { ...doc, blocks };

  for (const section of omit.sections ?? []) {
    const at = blocks.findIndex((b) => b.type === "heading" && b.text === section);
    if (at < 0) throw new Error(`The section "${section}" to omit is not a heading of this report`);
    const level = (blocks[at] as Extract<Block, { type: "heading" }>).level;
    let end = at + 1;
    while (end < blocks.length) {
      const b = blocks[end];
      if (b.type === "heading" && b.level <= level) break;
      end++;
    }
    blocks.splice(at, end - at, { type: "omitted", section });
  }

  for (const sentence of omit.sentences ?? []) {
    let found = 0;
    // Cuts the sentence out of these blocks; a paragraph, list item or list it empties goes.
    const visit = (list: PageBlock[]) => {
      for (let k = 0; k < list.length; k++) {
        const b = list[k];
        let empty = false;
        if (b.type === "paragraph") {
          const cut = cutSentence(b.children, squash(sentence));
          found += cut.count;
          b.children = cut.nodes;
          empty = cut.count > 0 && cut.nodes.length === 0;
        } else if (b.type === "list") {
          const before = b.items.length;
          b.items.forEach(visit);
          b.items = b.items.filter((item) => item.length > 0);
          empty = before > 0 && b.items.length === 0;
        }
        if (empty) list.splice(k--, 1);
      }
    };
    visit(blocks);
    if (found === 0) {
      throw new Error(`The sentence to omit is not in a paragraph of this report (as plain text): "${sentence}"`);
    }
  }

  const headings: Heading[] = doc.title ? [doc.title] : [];
  walkBlocks(
    blocks.filter((b): b is Block => b.type !== "omitted"),
    (b) => {
      if (b.type === "heading") headings.push({ level: b.level, id: b.id, text: b.text });
    },
  );
  return { ...doc, blocks, headings };
}

/**
 * Cut every copy of a sentence out of a paragraph's top-level text, with the space before or
 * after it. A sentence split by formatting (bold, a link, code) isn't found; the caller throws.
 */
function cutSentence(nodes: Inline[], sentence: string): { nodes: Inline[]; count: number } {
  let count = 0;
  const out: Inline[] = [];
  for (const n of nodes) {
    if (n.type !== "text") {
      out.push(n);
      continue;
    }
    let text = n.text;
    for (let at = text.indexOf(sentence); at >= 0; at = text.indexOf(sentence)) {
      count++;
      let from = at;
      let to = at + sentence.length;
      if (text[to] === " ") to++;
      else if (from > 0 && text[from - 1] === " ") from--;
      text = text.slice(0, from) + text.slice(to);
    }
    if (text !== "") out.push({ type: "text", text });
  }
  const empty = out.every((n) => n.type === "text" && n.text.trim() === "");
  return { nodes: empty ? [] : out, count };
}
