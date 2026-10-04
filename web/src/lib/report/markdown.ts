/**
 * A small Markdown parser for the files in `report/`, so `/report/<slug>` needs no Markdown
 * package. It covers the subset those files use: headings, paragraphs with wrapped lines, `-` and
 * `1.` lists (nested by indent), pipe tables, fenced code, an image on its own line, `---`, links,
 * emphasis and inline code. It returns plain data that `components/report/Markdown.tsx` renders as
 * React elements, never HTML: a `<` is just a character of text.
 *
 * Anything outside the subset (raw HTML, block quotes, reference links, setext headings, …) is
 * listed in `issues` with its line number. A test parses every published report and fails on any.
 * The rules follow CommonMark and GitHub where the reports need them (emphasis flanking, lazy
 * paragraph continuation, an ordered list interrupts a paragraph only when it starts at 1).
 */

export type Inline =
  | { type: "text"; text: string }
  | { type: "code"; text: string }
  | { type: "strong"; children: Inline[] }
  | { type: "em"; children: Inline[] }
  | { type: "link"; href: string; children: Inline[] }; // href as written; links.ts resolves it

export type Align = "left" | "center" | "right" | null;

export type Block =
  | { type: "heading"; level: number; id: string; text: string; children: Inline[] }
  | { type: "paragraph"; children: Inline[] }
  | { type: "list"; ordered: boolean; start: number; tight: boolean; items: Block[][] }
  | { type: "table"; align: Align[]; head: Inline[][]; rows: Inline[][][] }
  | { type: "code"; lang: string; text: string }
  | { type: "image"; src: string; alt: string }
  | { type: "hr" };

export interface Issue {
  line: number; // 1-based line in the file
  reason: string;
  text: string; // the line as written
}

export interface Heading {
  level: number;
  id: string;
  text: string;
}

export interface ReportDoc {
  title: Heading | null; // the first `# ` heading, shown as the page h1
  titleInlines: Inline[];
  meta: Inline[] | null; // the `_Generated … by …_` line, shown muted
  blocks: Block[]; // everything after the title and the meta line
  headings: Heading[]; // every heading in order, the title included (for the contents and anchors)
  issues: Issue[];
}

interface Line {
  text: string;
  no: number;
}

interface Ctx {
  issues: Issue[];
  source: string[];
  slugs: Map<string, number>; // GitHub-style ids already used in this file
  h1: boolean; // a `# ` heading has been seen
}

const FENCE = /^( {0,3})(`{3,}|~{3,})[ \t]*([^`\s]*)[^`]*$/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const HEADING = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/;
const HR = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const LIST = /^( {0,3})([-*+]|\d{1,9}[.)])(?:([ \t]+)(.*))?$/;
const SETEXT = /^ {0,3}(?:=+|-+)[ \t]*$/;
const DELIM_CELL = /^:?-+:?$/;
// An image alone in its paragraph. The alt text holds no brackets and the address no spaces or
// parentheses; anything fancier is parsed inline, where an image is flagged.
const LONE_IMAGE = /^!\[([^[\]]*)\]\(([^\s()]+)\)$/;
const PUNCT = /[\p{P}\p{S}]/u;
const SPACE = /\s/u;
const ASCII_PUNCT = /[!-\/:-@[-`{-~]/;

/** Parse one report file. */
export function parseReport(source: string): ReportDoc {
  const raw = source.replace(/\r\n?/g, "\n").split("\n");
  const ctx: Ctx = { issues: [], source: raw, slugs: new Map(), h1: false };
  let fenced = false; // tabs are fine inside code fences, nowhere else
  raw.forEach((text, k) => {
    if (/^\s*(?:`{3,}|~{3,})/.test(text)) fenced = !fenced;
    else if (!fenced && text.includes("\t")) issue(ctx, k + 1, "tab character (use spaces)");
  });
  const { blocks } = parseBlocks(
    raw.map((text, k) => ({ text, no: k + 1 })),
    ctx,
  );

  let title: Heading | null = null;
  let titleInlines: Inline[] = [];
  const first = blocks[0];
  if (first?.type === "heading" && first.level === 1) {
    title = { level: 1, id: first.id, text: first.text };
    titleInlines = first.children;
    blocks.shift();
  } else {
    ctx.issues.push({ line: 1, reason: "the file does not start with a `# ` title", text: raw[0] ?? "" });
  }

  let meta: Inline[] | null = null;
  const next = blocks[0];
  if (next?.type === "paragraph" && next.children.length === 1) {
    const only = next.children[0];
    if (only.type === "em" && plainText(only.children).startsWith("Generated ")) {
      meta = only.children;
      blocks.shift();
    }
  }

  const headings: Heading[] = title ? [title] : [];
  walkBlocks(blocks, (b) => {
    if (b.type === "heading") headings.push({ level: b.level, id: b.id, text: b.text });
  });

  ctx.issues.sort((a, b) => a.line - b.line);
  return { title, titleInlines, meta, blocks, headings, issues: ctx.issues };
}

/** The text of some inlines with the formatting dropped (heading ids, alt text, titles). */
export function plainText(nodes: Inline[]): string {
  let out = "";
  for (const n of nodes) out += n.type === "text" || n.type === "code" ? n.text : plainText(n.children);
  return out;
}

/** Call `fn` on every block, list items included, in document order. */
export function walkBlocks(blocks: Block[], fn: (b: Block) => void): void {
  for (const b of blocks) {
    fn(b);
    if (b.type === "list") for (const item of b.items) walkBlocks(item, fn);
  }
}

/** Every link and image address in some blocks, in document order. */
export function collectLinks(blocks: Block[]): { href: string; image: boolean }[] {
  const out: { href: string; image: boolean }[] = [];
  const inl = (nodes: Inline[]) => {
    for (const n of nodes) {
      if (n.type === "link") out.push({ href: n.href, image: false });
      if (n.type === "link" || n.type === "strong" || n.type === "em") inl(n.children);
    }
  };
  walkBlocks(blocks, (b) => {
    if (b.type === "heading" || b.type === "paragraph") inl(b.children);
    if (b.type === "table") for (const row of [b.head, ...b.rows]) for (const cell of row) inl(cell);
    if (b.type === "image") out.push({ href: b.src, image: true });
  });
  return out;
}

/** GitHub's heading id: lower case, punctuation dropped, spaces to hyphens, `-1`, `-2` on repeats. */
export function slugify(text: string, used: Map<string, number>): string {
  const base = text.toLowerCase().replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, "").replace(/ /g, "-");
  let id = base;
  while (used.has(id)) {
    const n = (used.get(base) ?? 0) + 1;
    used.set(base, n);
    id = `${base}-${n}`;
  }
  used.set(id, 0);
  return id;
}

// ---- Blocks -------------------------------------------------------------------------------

function issue(ctx: Ctx, line: number, reason: string): void {
  ctx.issues.push({ line, reason, text: ctx.source[line - 1] ?? "" });
}

const isBlank = (s: string) => s.trim() === "";
const indentOf = (s: string) => s.length - s.trimStart().length;
const stripIndent = (s: string, n: number) => s.slice(Math.min(n, indentOf(s)));

function isTableStart(lines: Line[], i: number): boolean {
  // Four spaces of indent would make it indented code, which `unsupported` flags.
  if (!/^ {0,3}\|/.test(lines[i].text) || i + 1 >= lines.length) return false;
  const delims = splitRow(lines[i + 1].text);
  return (
    lines[i + 1].text.trimStart().startsWith("|") &&
    delims.every((d) => DELIM_CELL.test(d)) &&
    delims.length === splitRow(lines[i].text).length
  );
}

/** Would this line end the paragraph above it? */
function interrupts(lines: Line[], i: number): boolean {
  const t = lines[i].text;
  const m = LIST.exec(t);
  const listStart = m !== null && (m[4] ?? "").trim() !== "" && (!/\d/.test(m[2]) || parseInt(m[2], 10) === 1);
  return FENCE.test(t) || HEADING.test(t) || HR.test(t) || /^ {0,3}>/.test(t) || isTableStart(lines, i) || listStart;
}

/** Does this line start a block of its own (which ends a list item's lazy continuation)? */
function startsBlock(lines: Line[], i: number): boolean {
  const t = lines[i].text;
  return LIST.test(t) || FENCE.test(t) || HEADING.test(t) || HR.test(t) || /^ {0,3}[|>]/.test(t);
}

/** Lines that start something this parser doesn't support; they still render, as a paragraph. */
function unsupported(t: string): string | null {
  const s = t.trimStart();
  if (indentOf(t) >= 4) return "indented code block (use a ``` fence)";
  if (s.startsWith(">")) return "block quote";
  if (/^<\/?[A-Za-z!]/.test(s)) return "raw HTML block";
  if (/^\[\^[^\]]+\]:/.test(s)) return "footnote definition";
  if (/^\[[^\]]+\]:\s/.test(s)) return "link reference definition";
  if (s.startsWith("|")) return "a `|` line that is not a table (no delimiter row, or the cell counts differ)";
  return null;
}

/**
 * Parse lines into blocks. `gap` says whether a blank line separates two of them, which is what
 * makes a list item loose in CommonMark. Blank lines inside a code fence or a nested list don't
 * count, except the ones a nested list ends with when another block follows it.
 */
function parseBlocks(lines: Line[], ctx: Ctx): { blocks: Block[]; gap: boolean } {
  const blocks: Block[] = [];
  let gap = false;
  let blankBefore = false; // a blank line since the last block ended
  let i = 0;
  while (i < lines.length) {
    const { text, no } = lines[i];
    if (isBlank(text)) {
      blankBefore = true;
      i++;
      continue;
    }
    if (blankBefore && blocks.length > 0) gap = true;
    blankBefore = false;
    const fence = FENCE.exec(text);
    if (fence) {
      i = parseFence(lines, i, fence, ctx, blocks);
      continue;
    }
    const heading = HEADING.exec(text);
    if (heading) {
      const content = heading[2] ?? "";
      const children = parseInline(/^#+$/.test(content) ? "" : content, ctx, no); // `## #` is empty
      const plain = plainText(children);
      if (plain.trim() === "") issue(ctx, no, "a heading with no text (it would get no id)");
      if (heading[1].length === 1 && ctx.h1) issue(ctx, no, "a second `# ` heading (the page has one h1)");
      ctx.h1 ||= heading[1].length === 1;
      blocks.push({ type: "heading", level: heading[1].length, id: slugify(plain, ctx.slugs), text: plain, children });
      i++;
      continue;
    }
    if (HR.test(text)) {
      blocks.push({ type: "hr" });
      i++;
      continue;
    }
    if (isTableStart(lines, i)) {
      i = parseTable(lines, i, ctx, blocks);
      continue;
    }
    if (LIST.test(text)) {
      const list = parseList(lines, i, ctx, blocks);
      i = list.next;
      blankBefore = list.endsBlank;
      continue;
    }
    const odd = unsupported(text);
    if (odd) issue(ctx, no, odd);
    i = parseParagraph(lines, i, ctx, blocks);
  }
  return { blocks, gap };
}

function parseParagraph(lines: Line[], i: number, ctx: Ctx, blocks: Block[]): number {
  const start = i;
  const parts: string[] = [];
  while (i < lines.length && !isBlank(lines[i].text)) {
    const { text, no } = lines[i];
    if (i > start) {
      if (SETEXT.test(text)) issue(ctx, no, "setext heading underline (use `#` headings; `---` needs a blank line above)");
      else if (interrupts(lines, i)) break;
      else if (text.trimStart().startsWith("|")) issue(ctx, no, "a `|` line inside a paragraph (a table needs a blank line above)");
    }
    parts.push(text.trim());
    i++;
  }
  // Two trailing spaces or a backslash would be a hard line break; the reports never use one.
  for (let k = start; k < i - 1; k++) {
    if (/(?: {2,}|\\)$/.test(lines[k].text)) issue(ctx, lines[k].no, "hard line break");
  }
  const joined = parts.join("\n");
  const image = LONE_IMAGE.exec(joined);
  if (image) {
    const alt = plainText(parseInline(image[1], ctx, lines[start].no)).trim();
    if (alt === "") issue(ctx, lines[start].no, "an image without alt text (the alt text is its caption)");
    blocks.push({ type: "image", src: image[2], alt });
  } else {
    blocks.push({ type: "paragraph", children: parseInline(joined, ctx, lines[start].no) });
  }
  return i;
}

function parseFence(lines: Line[], i: number, open: RegExpExecArray, ctx: Ctx, blocks: Block[]): number {
  const indent = open[1].length;
  const marker = open[2];
  const body: string[] = [];
  let j = i + 1;
  let closed = false;
  for (; j < lines.length; j++) {
    const close = FENCE_CLOSE.exec(lines[j].text);
    if (close && close[1][0] === marker[0] && close[1].length >= marker.length) {
      closed = true;
      j++;
      break;
    }
    body.push(stripIndent(lines[j].text, indent));
  }
  if (!closed) issue(ctx, lines[i].no, "code fence is never closed");
  blocks.push({ type: "code", lang: open[3], text: body.join("\n") });
  return j;
}

/** Split a table row on unescaped pipes, dropping the outer ones; `\|` stays as a literal `|`. */
function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells: string[] = [];
  let cur = "";
  for (let k = 0; k < s.length; k++) {
    if (s[k] === "\\" && s[k + 1] === "|") {
      cur += "|";
      k++;
    } else if (s[k] === "|") {
      cells.push(cur.trim());
      cur = "";
    } else cur += s[k];
  }
  cells.push(cur.trim());
  return cells;
}

function parseTable(lines: Line[], i: number, ctx: Ctx, blocks: Block[]): number {
  const head = splitRow(lines[i].text);
  const align: Align[] = splitRow(lines[i + 1].text).map((d) =>
    d.startsWith(":") && d.endsWith(":") ? "center" : d.endsWith(":") ? "right" : d.startsWith(":") ? "left" : null,
  );
  const rows: Inline[][][] = [];
  let j = i + 2;
  for (; j < lines.length && lines[j].text.trimStart().startsWith("|"); j++) {
    const { text, no } = lines[j];
    const cells = splitRow(text);
    if (cells.length !== head.length) issue(ctx, no, `table row has ${cells.length} cells, the header has ${head.length}`);
    while (cells.length < head.length) cells.push("");
    rows.push(cells.slice(0, head.length).map((c) => parseInline(c, ctx, no)));
  }
  // GitHub would read a plain line straight after a table as one more row.
  if (j < lines.length && !isBlank(lines[j].text) && !startsBlock(lines, j)) {
    issue(ctx, lines[j].no, "text straight after a table (GitHub reads it as a row; add a blank line)");
  }
  blocks.push({ type: "table", align, head: head.map((c) => parseInline(c, ctx, lines[i].no)), rows });
  return j;
}

/** The marker's family: a bullet character, or an ordered list's `.` or `)`. */
const markerKind = (marker: string) => (/\d/.test(marker) ? marker.slice(-1) : marker);

/**
 * Does the item so far end inside a paragraph? Only then may a less indented line continue it
 * lazily (CommonMark). The lines are parsed on a scratch context, so nothing is reported twice.
 */
function endsInParagraph(body: Line[]): boolean {
  const scratch: Ctx = { issues: [], source: [], slugs: new Map(), h1: true };
  let blocks = parseBlocks(body, scratch).blocks;
  for (;;) {
    const last = blocks[blocks.length - 1];
    if (last?.type === "paragraph" || last?.type === "image") return true; // a lone image is a paragraph too
    if (last?.type !== "list") return false;
    blocks = last.items[last.items.length - 1];
  }
}

/**
 * A list: each item takes the lines indented at least to its content column, blank lines inside
 * it, and lazy continuation lines (less indented, straight after paragraph text). The item's lines,
 * with that indent removed, are parsed as blocks of their own, which is how lists nest. Returns
 * the next line and whether the list ended with blank lines (for the enclosing item's tightness).
 */
function parseList(lines: Line[], i: number, ctx: Ctx, blocks: Block[]): { next: number; endsBlank: boolean } {
  const first = LIST.exec(lines[i].text)!;
  const kind = markerKind(first[2]);
  const ordered = /\d/.test(first[2]);
  const items: Block[][] = [];
  let tight = true;
  let trailing = 0;
  while (i < lines.length) {
    const m = LIST.exec(lines[i].text);
    if (!m || markerKind(m[2]) !== kind || HR.test(lines[i].text)) break;
    const spaces = m[3] ?? "";
    const rest = m[4] ?? "";
    const wide = rest === "" || spaces.length > 4; // the content starts one space after the marker
    const offset = m[1].length + m[2].length + (wide ? 1 : spaces.length);
    const body: Line[] = [{ text: wide && rest !== "" ? " ".repeat(spaces.length - 1) + rest : rest, no: lines[i].no }];
    let fence: string | null = null; // the open fence inside this item, if any
    const track = (t: string) => {
      const f = FENCE.exec(t);
      const c = FENCE_CLOSE.exec(t);
      if (fence === null && f) fence = f[2];
      else if (fence !== null && c && c[1][0] === fence[0] && c[1].length >= fence.length) fence = null;
    };
    track(body[0].text);
    let blank = false;
    let j = i + 1;
    for (; j < lines.length; j++) {
      const { text, no } = lines[j];
      if (isBlank(text)) {
        if (rest === "" && j === i + 1) {
          // An item may start with one blank line, not two: this one stays empty. The list goes
          // on if another item follows the blank lines.
          while (j < lines.length && isBlank(lines[j].text)) body.push({ text: "", no: lines[j++].no });
          break;
        }
        body.push({ text: "", no });
        blank = true;
        continue;
      }
      if (indentOf(text) >= offset) {
        body.push({ text: text.slice(offset), no });
        track(text.slice(offset));
        blank = false;
        continue;
      }
      // A less indented line stays in the item only as more text of its last paragraph (lazily).
      if (blank || fence !== null) break;
      const table = isTableStart(lines, j);
      if ((startsBlock(lines, j) && !table) || !endsInParagraph(body)) break;
      if (table) {
        // GitHub reads the table's lines as more text of the item; this parser would draw a table.
        issue(ctx, no, "a table straight after a list item (GitHub reads it as part of the item; add a blank line)");
        break;
      }
      body.push({ text: text.trimStart(), no });
    }
    trailing = 0;
    while (body.length > 1 && isBlank(body[body.length - 1].text)) {
      body.pop();
      trailing++;
    }
    const item = parseBlocks(body, ctx);
    if (item.gap) tight = false; // a blank line between two blocks of the item
    items.push(item.blocks);
    i = j;
    const again = i < lines.length ? LIST.exec(lines[i].text) : null;
    if (trailing > 0 && again && markerKind(again[2]) === kind && !HR.test(lines[i].text)) tight = false;
  }
  blocks.push({ type: "list", ordered, start: ordered ? parseInt(first[2], 10) : 1, tight, items });
  return { next: i, endsBlank: trailing > 0 };
}

// ---- Inlines ------------------------------------------------------------------------------

interface Delim {
  type: "delim";
  char: "*" | "_";
  count: number; // characters not yet used
  orig: number; // the run's length as written (CommonMark's rule of 3)
  open: boolean;
  close: boolean;
  line: number;
}

type Tok = Inline | Delim;

/** Index of the `]` that closes the `[` at `open`, skipping code spans and escapes; -1 if none. */
function matchBracket(src: string, open: number): number {
  let depth = 0;
  for (let k = open; k < src.length; k++) {
    const c = src[k];
    if (c === "\\") k++;
    else if (c === "`") {
      const run = runLength(src, k, "`");
      const close = closingTicks(src, k + run, run);
      k = close < 0 ? k + run - 1 : close + run - 1;
    } else if (c === "[") depth++;
    else if (c === "]" && --depth === 0) return k;
  }
  return -1;
}

/**
 * Index of the `)` that ends a link address starting at `from` (just after the `(`), or -1. As in
 * CommonMark, balanced parentheses belong to the address, so
 * `https://en.wikipedia.org/wiki/Monza_(circuit)` stays whole, and `\)` is escaped.
 */
function destinationEnd(src: string, from: number): number {
  let depth = 0;
  for (let k = from; k < src.length; k++) {
    const c = src[k];
    if (c === "\\" && ASCII_PUNCT.test(src[k + 1] ?? "")) k++;
    else if (c === "(") depth++;
    else if (c === ")" && depth-- === 0) return k;
  }
  return -1;
}

function runLength(src: string, at: number, ch: string): number {
  let n = 0;
  while (src[at + n] === ch) n++;
  return n;
}

/** Start of a backtick run of exactly `n` at or after `from`; -1 if there is none. */
function closingTicks(src: string, from: number, n: number): number {
  for (let k = from; k < src.length; k++) {
    if (src[k] !== "`") continue;
    const run = runLength(src, k, "`");
    if (run === n) return k;
    k += run - 1;
  }
  return -1;
}

/** Parse inline Markdown. `src` may hold several source lines joined by "\n", the first at `line`. */
function parseInline(src: string, ctx: Ctx, line: number): Inline[] {
  const toks: Tok[] = [];
  let text = "";
  const flush = () => {
    if (text) toks.push({ type: "text", text });
    text = "";
  };
  const lineAt = (k: number) => line + (src.slice(0, k).match(/\n/g)?.length ?? 0);
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    const rest = src.slice(i);
    if (c === "\\" && ASCII_PUNCT.test(src[i + 1] ?? "")) {
      text += src[i + 1];
      i += 2;
    } else if (c === "`") {
      const run = runLength(src, i, "`");
      const close = closingTicks(src, i + run, run);
      if (close < 0) {
        issue(ctx, lineAt(i), "inline code that is never closed");
        text += "`".repeat(run);
        i += run;
        continue;
      }
      flush();
      let code = src.slice(i + run, close).replace(/\n/g, " ");
      if (code.length > 1 && code.startsWith(" ") && code.endsWith(" ") && code.trim() !== "") code = code.slice(1, -1);
      toks.push({ type: "code", text: code });
      i = close + run;
    } else if (c === "[" || (c === "!" && src[i + 1] === "[")) {
      const open = c === "!" ? i + 1 : i;
      const end = matchBracket(src, open);
      const close = end >= 0 && src[end + 1] === "(" ? destinationEnd(src, end + 2) : -1;
      const written = close >= 0 ? src.slice(end + 2, close).trim() : null;
      if (written !== null && /^[^\s<]\S*$/.test(written)) {
        flush();
        const href = written.replace(/\\([!-/:-@[-`{-~])/g, "$1"); // `\_` in an address means `_`
        const label = parseInline(src.slice(open + 1, end), ctx, lineAt(open));
        if (c === "!") issue(ctx, lineAt(i), "an image inside text (put it on a line of its own)");
        toks.push({ type: "link", href, children: c === "!" ? [{ type: "text", text: plainText(label) }] : label });
        i = close + 1;
        continue;
      }
      if (written !== null && written.startsWith("<")) issue(ctx, lineAt(i), "link address in angle brackets");
      else if (written !== null) issue(ctx, lineAt(i), "link address that is empty, has spaces or a title");
      else if (end >= 0 && src[end + 1] === "(") issue(ctx, lineAt(i), "link address that is never closed");
      else if (end >= 0 && src[end + 1] === "[") issue(ctx, lineAt(i), "reference-style link");
      else if (src[open + 1] === "^") issue(ctx, lineAt(i), "footnote");
      text += c;
      i++;
    } else if (c === "*" || c === "_") {
      // CommonMark's flanking rules: `_` inside a word (`mae_error_exit`) never emphasises.
      const run = runLength(src, i, c);
      const before = i === 0 ? " " : src[i - 1];
      const after = src[i + run] ?? " ";
      const left = !SPACE.test(after) && (!PUNCT.test(after) || SPACE.test(before) || PUNCT.test(before));
      const right = !SPACE.test(before) && (!PUNCT.test(before) || SPACE.test(after) || PUNCT.test(after));
      flush();
      toks.push({
        type: "delim",
        char: c,
        count: run,
        orig: run,
        open: c === "*" ? left : left && (!right || PUNCT.test(before)),
        close: c === "*" ? right : right && (!left || PUNCT.test(after)),
        line: lineAt(i),
      });
      i += run;
    } else {
      if (c === "<" && /^<(?:\/?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?\/?>|!--)/.test(rest)) {
        issue(ctx, lineAt(i), "raw HTML (it would show as text)");
      } else if (c === "<" && /^<[A-Za-z][A-Za-z0-9+.-]*:[^\s<>]*>/.test(rest)) {
        issue(ctx, lineAt(i), "autolink in angle brackets (write [text](url))");
      } else if (c === "&" && /^&(?:#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});/.test(rest)) {
        issue(ctx, lineAt(i), "HTML entity (it would show as written)");
      } else if (c === "~" && src[i + 1] === "~") {
        issue(ctx, lineAt(i), "strikethrough");
      } else if (/^(?:https?:\/\/|www\.)/.test(rest) && !/[\p{L}\p{N}]/u.test(src[i - 1] ?? "")) {
        issue(ctx, lineAt(i), "bare URL (GitHub would link it; write [text](url))");
      }
      text += c;
      i++;
    }
  }
  flush();
  return finishInlines(emphasis(toks), ctx);
}

/**
 * CommonMark's emphasis pass: each closing run, left to right, takes the nearest opening run of
 * the same character; two characters on both sides make strong, one makes em. Runs between them
 * that found no partner become text.
 */
function emphasis(toks: Tok[]): Tok[] {
  let i = 0;
  while (i < toks.length) {
    const closer = toks[i];
    if (closer.type !== "delim" || !closer.close || closer.count === 0) {
      i++;
      continue;
    }
    let j = i - 1;
    for (; j >= 0; j--) {
      const op = toks[j];
      if (op.type !== "delim" || op.char !== closer.char || !op.open || op.count === 0) continue;
      const either = op.close || closer.open;
      if (either && (op.orig + closer.orig) % 3 === 0 && !(op.orig % 3 === 0 && closer.orig % 3 === 0)) continue;
      break;
    }
    if (j < 0) {
      i++;
      continue;
    }
    const op = toks[j] as Delim;
    const n = op.count >= 2 && closer.count >= 2 ? 2 : 1;
    op.count -= n;
    closer.count -= n;
    const inner: Tok[] = toks.slice(j + 1, i);
    toks.splice(j + 1, i - j - 1, { type: n === 2 ? "strong" : "em", children: inner as Inline[] });
    i = j + 2;
  }
  return toks;
}

/** Turn leftover delimiter runs into text, join neighbouring text and make line breaks spaces. */
function finishInlines(toks: Tok[], ctx: Ctx): Inline[] {
  const out: Inline[] = [];
  for (const t of toks) {
    let node: Inline;
    if (t.type === "delim") {
      if (t.count === 0) continue;
      if (t.open && !t.close) issue(ctx, t.line, `\`${t.char.repeat(t.orig)}\` opens emphasis that is never closed`);
      node = { type: "text", text: t.char.repeat(t.count) };
    } else if (t.type === "text") node = { type: "text", text: t.text.replace(/\n/g, " ") };
    else if (t.type === "code") node = t;
    else node = { ...t, children: finishInlines(t.children as Tok[], ctx) };
    const prev = out[out.length - 1];
    if (node.type === "text" && prev?.type === "text") prev.text += node.text;
    else out.push(node);
  }
  return out;
}
