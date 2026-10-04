import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { parseReport, plainText, slugify, walkBlocks, type Block, type Inline, type ReportDoc } from "./markdown.ts";
import { REPORTS } from "./manifest.ts";
import { readReport } from "./testing.ts";

/** Parse a snippet that has no title of its own. */
function body(md: string): ReportDoc {
  return parseReport(`# Title\n\n${md}`);
}

/** The issue reasons, for snippets that should be flagged. */
function reasons(md: string): string[] {
  return body(md).issues.map((i) => i.reason);
}

/** A compact, readable form of inlines: **x** for strong, *x* for em, `x`, [x](href). */
function show(nodes: Inline[]): string {
  return nodes
    .map((n) => {
      if (n.type === "text") return n.text;
      if (n.type === "code") return `\`${n.text}\``;
      if (n.type === "strong") return `**${show(n.children)}**`;
      if (n.type === "em") return `*${show(n.children)}*`;
      return `[${show(n.children)}](${n.href})`;
    })
    .join("");
}

function only<T extends Block["type"]>(doc: ReportDoc, type: T): Extract<Block, { type: T }> {
  assert.equal(doc.blocks.length, 1, `expected one block, got ${JSON.stringify(doc.blocks)}`);
  assert.equal(doc.blocks[0].type, type);
  return doc.blocks[0] as Extract<Block, { type: T }>;
}

const para = (md: string) => show(only(body(md), "paragraph").children);

describe("headings", () => {
  test("levels, GitHub ids, repeats and closing hashes", () => {
    const doc = body("## A b\n\n### A b\n\n#### `code` and *em*\n\n## Trailing ##\n\n###### Six");
    assert.deepEqual(
      doc.headings.map((h) => [h.level, h.id, h.text]),
      [
        [1, "title", "Title"],
        [2, "a-b", "A b"],
        [3, "a-b-1", "A b"],
        [4, "code-and-em", "code and em"],
        [2, "trailing", "Trailing"],
        [6, "six", "Six"],
      ],
    );
    const h4 = doc.blocks[2];
    assert.equal(h4.type === "heading" && show(h4.children), "`code` and *em*");
  });

  test("ids as GitHub makes them", () => {
    const used = new Map<string, number>();
    assert.equal(slugify("Dataset card: F1 telemetry corner segments (2022–2026)", used), "dataset-card-f1-telemetry-corner-segments-20222026");
    assert.equal(slugify("How it's built", used), "how-its-built");
    assert.equal(slugify("find_mistakes ranking", used), "find_mistakes-ranking");
    assert.equal(slugify("1. What M5 built", used), "1-what-m5-built");
    assert.equal(slugify("The 2026 rule change: how fast does the model adapt?", used), "the-2026-rule-change-how-fast-does-the-model-adapt");
    assert.equal(slugify("A", used), "a");
    assert.equal(slugify("A", used), "a-1");
    assert.equal(slugify("A 1", used), "a-1-1");
    assert.equal(slugify("A", used), "a-2");
  });

  test("a hash without a space, or seven of them, is a paragraph", () => {
    assert.equal(para("#hashtag and #16"), "#hashtag and #16");
    assert.equal(para("####### seven"), "####### seven");
  });

  test("the first heading is the title; the generated line is metadata", () => {
    const doc = parseReport("# M4: scoring\n\n_Generated 2026-10-01 by `race_engineer.models.finetune` from `a_b.pt`._\n\nText.");
    assert.equal(doc.title?.text, "M4: scoring");
    assert.equal(doc.title?.id, "m4-scoring");
    assert.equal(show(doc.titleInlines), "M4: scoring");
    assert.equal(show(doc.meta ?? []), "Generated 2026-10-01 by `race_engineer.models.finetune` from `a_b.pt`.");
    assert.deepEqual(doc.blocks, [{ type: "paragraph", children: [{ type: "text", text: "Text." }] }]);
    assert.deepEqual(doc.issues, []);
  });

  test("an italic first paragraph that isn't the generated line stays a paragraph", () => {
    const doc = body("_Just a note._");
    assert.equal(doc.meta, null);
    assert.equal(show(only(doc, "paragraph").children), "*Just a note.*");
  });
});

describe("paragraphs", () => {
  test("wrapped lines are joined with spaces", () => {
    assert.equal(para("one\n  two\nthree"), "one two three");
  });

  test("a wrapped line starting with a year is not a list (only `1.` interrupts a paragraph)", () => {
    assert.equal(para("fell from 259 of 385 in\n2026. Traffic among"), "fell from 259 of 385 in 2026. Traffic among");
  });

  test("a `-` list or a `1.` list interrupts a paragraph", () => {
    const doc = body("Next:\n- Apply\n- Tighten");
    assert.deepEqual(
      doc.blocks.map((b) => b.type),
      ["paragraph", "list"],
    );
    const ordered = body("Steps:\n1. one\n2. two").blocks[1];
    assert.ok(ordered.type === "list" && ordered.ordered && ordered.items.length === 2);
  });

  test("pipes inside a sentence are text", () => {
    assert.equal(para("per segment: |a - b| / |b| and ||a - b||"), "per segment: |a - b| / |b| and ||a - b||");
  });
});

describe("lists", () => {
  test("bullets, tight", () => {
    const list = only(body("- a\n- b\n- c"), "list");
    assert.equal(list.ordered, false);
    assert.equal(list.tight, true);
    assert.deepEqual(
      list.items.map((it) => it.map((b) => (b.type === "paragraph" ? show(b.children) : b.type))),
      [["a"], ["b"], ["c"]],
    );
  });

  test("ordered, with its start number", () => {
    const list = only(body("3. three\n4. four"), "list");
    assert.equal(list.ordered, true);
    assert.equal(list.start, 3);
  });

  test("blank lines between items make the list loose", () => {
    assert.equal(only(body("- a\n\n- b"), "list").tight, false);
  });

  test("continuation lines at the content indent join the item's paragraph", () => {
    const list = only(body("1. **Load** each session,\n   car telemetry\n   and weather.\n2. **Next**"), "list");
    const first = list.items[0][0];
    assert.equal(first.type === "paragraph" && show(first.children), "**Load** each session, car telemetry and weather.");
  });

  test("a less indented line straight after the item's text continues it (lazy), as in `10.` items", () => {
    const list = only(body("9. nine\n   more\n10. ten starts\n   lazy line\n11. eleven"), "list");
    assert.equal(list.items.length, 3);
    const ten = list.items[1][0];
    assert.equal(ten.type === "paragraph" && show(ten.children), "ten starts lazy line");
  });

  test("two-space indent nests a list, with its own continuation lines", () => {
    const md = [
      "- **Why.** The verdicts",
      "  combine notes.",
      "  - **Other cars (6).** Four battles",
      "    and a car rejoining.",
      "  - **Laps given up (3).**",
      "- **Changed.** One answer",
    ].join("\n");
    const list = only(body(md), "list");
    assert.equal(list.items.length, 2);
    const [intro, nested] = list.items[0];
    assert.equal(intro.type === "paragraph" && show(intro.children), "**Why.** The verdicts combine notes.");
    assert.ok(nested.type === "list" && !nested.ordered);
    assert.deepEqual(
      nested.items.map((it) => (it[0].type === "paragraph" ? show(it[0].children) : "")),
      ["**Other cars (6).** Four battles and a car rejoining.", "**Laps given up (3).**"],
    );
  });

  test("an item can hold paragraphs and a fenced code block (the M5 report's shape)", () => {
    const md = [
      "1. **The chat.** Start the API",
      "   from a terminal:",
      "",
      "   ```bash",
      "   make api   # health says \"anthropic\"",
      "   ```",
      "",
      "   Look for: the tool.",
      "2. **The charts.**",
    ].join("\n");
    const list = only(body(md), "list");
    assert.equal(list.items.length, 2);
    assert.equal(list.tight, false);
    assert.deepEqual(
      list.items[0].map((b) => b.type),
      ["paragraph", "code", "paragraph"],
    );
    assert.deepEqual(list.items[0][1], { type: "code", lang: "bash", text: 'make api   # health says "anthropic"' });
  });

  test("only blank lines between an item's own blocks, or between items, make a list loose", () => {
    // CommonMark: a blank line inside a nested list or inside a code fence doesn't loosen the outer list.
    const nested = only(body("- a\n  - b\n\n  - c\n- d"), "list");
    assert.equal(nested.tight, true);
    const inner = nested.items[0][1];
    assert.ok(inner.type === "list" && !inner.tight);
    assert.equal(only(body("- a\n  ```\n  x\n\n  y\n  ```\n- b"), "list").tight, true);
    // A nested list, a blank line, then more of the same item: loose.
    assert.equal(only(body("- a\n  - b\n\n  more of a\n- c"), "list").tight, false);
    assert.equal(only(body("- a\n  - b\n\n- c"), "list").tight, false);
  });

  test("an item may start on the line after its marker, but not after a blank line", () => {
    const later = only(body("-\n  foo\n- bar"), "list");
    assert.equal(later.tight, true);
    assert.deepEqual(
      later.items.map((it) => it.map((b) => (b.type === "paragraph" ? show(b.children) : b.type))),
      [["foo"], ["bar"]],
    );
    assert.deepEqual(
      body("-\n\n  foo").blocks.map((b) => (b.type === "list" ? `list of ${b.items.length}, first empty: ${b.items[0].length === 0}` : b.type)),
      ["list of 1, first empty: true", "paragraph"],
    );
    const twoItems = only(body("-\n\n- b"), "list");
    assert.equal(twoItems.items.length, 2);
    assert.equal(twoItems.tight, false);
  });

  test("a less indented line continues an item only as more paragraph text", () => {
    // After a closed fence, or after an empty nested item, it ends the list.
    assert.deepEqual(
      body("- ```\n  x\n  ```\nafter").blocks.map((b) => b.type),
      ["list", "paragraph"],
    );
    assert.deepEqual(
      body("1. * *\nafter").blocks.map((b) => b.type),
      ["list", "paragraph"],
    );
    // A wrapped line starting `2026.` is paragraph text, so the next line still continues it.
    const list = only(body("- a\n  2026. # x\nlazy"), "list");
    const p = list.items[0][0];
    assert.equal(p.type === "paragraph" && show(p.children), "a 2026. # x lazy");
  });

  test("a different marker starts a new list", () => {
    assert.deepEqual(
      body("- a\n1. b").blocks.map((b) => b.type),
      ["list", "list"],
    );
  });
});

describe("tables", () => {
  test("header, alignment, trimmed cells, an empty header cell and inline formatting", () => {
    const table = only(body("|  | a | b | c |\n|---|:---|---:|:-:|\n| **mean** | `x_y` | 0.5 [0.1, 0.9] |  into pit |"), "table");
    assert.deepEqual(table.align, [null, "left", "right", "center"]);
    assert.deepEqual(table.head.map(show), ["", "a", "b", "c"]);
    assert.deepEqual(table.rows.map((r) => r.map(show)), [["**mean**", "`x_y`", "0.5 [0.1, 0.9]", "into pit"]]);
  });

  test("an escaped pipe stays inside its cell", () => {
    const table = only(body("| a | b |\n|---|---|\n| x \\| y | z |"), "table");
    assert.deepEqual(table.rows[0].map(show), ["x | y", "z"]);
  });

  test("a row with the wrong number of cells is flagged (and padded)", () => {
    const doc = body("| a | b |\n|---|---|\n| 1 |");
    assert.deepEqual(only(doc, "table").rows[0].map(show), ["1", ""]);
    assert.deepEqual(
      doc.issues.map((i) => [i.line, i.reason]),
      [[5, "table row has 1 cells, the header has 2"]],
    );
  });

  test("a table ends at the blank line; a plain line straight after it is flagged", () => {
    assert.deepEqual(reasons("| a |\n|---|\n| 1 |\nmore"), ["text straight after a table (GitHub reads it as a row; add a blank line)"]);
  });
});

describe("code, images, rules", () => {
  test("fenced code keeps its text exactly, `#`, `<` and `-` included", () => {
    const code = only(body("```\ncd engine\nuv run x   # export\n<b>not html</b>\n- not a list\n```"), "code");
    assert.deepEqual(code, { type: "code", lang: "", text: "cd engine\nuv run x   # export\n<b>not html</b>\n- not a list" });
  });

  test("the info string gives the language; tildes work too", () => {
    assert.equal(only(body("```text\nx\n```"), "code").lang, "text");
    assert.equal(only(body("~~~python\nx\n~~~"), "code").lang, "python");
  });

  test("an image on its own line is a figure", () => {
    assert.deepEqual(only(body("![Reconstruction error](figures/shift_finetune.png)"), "image"), {
      type: "image",
      src: "figures/shift_finetune.png",
      alt: "Reconstruction error",
    });
  });

  test("a thematic break", () => {
    assert.deepEqual(body("a\n\n---\n\nb").blocks[1], { type: "hr" });
  });
});

describe("inlines", () => {
  test("emphasis with * and _, strong, and both", () => {
    assert.equal(para("*a* _b_ **c** __d__ ***e***"), "*a* *b* **c** **d** ***e***");
  });

  test("underscores inside words never emphasise", () => {
    assert.equal(para("mae_error_exit and in_clusters_of_3_plus vs r_pc1"), "mae_error_exit and in_clusters_of_3_plus vs r_pc1");
  });

  test("a star after a number is text, with no issue (the style report's `+4.3*`)", () => {
    const doc = body("**ALB vs SAI** (slow +4.3*, medium +5.4*; races +5.7*), * = clear");
    assert.equal(show(only(doc, "paragraph").children), "**ALB vs SAI** (slow +4.3*, medium +5.4*; races +5.7*), * = clear");
    assert.deepEqual(doc.issues, []);
  });

  test("bold may span wrapped lines; brackets and signs inside are fine", () => {
    assert.equal(para("**7. About 7 in 10; 85% when\nrace control named it.** A"), "**7. About 7 in 10; 85% when race control named it.** A");
    assert.equal(para("**+5.6 [+3.4, +7.7]** and **-0.8 [-1.4, -0.1]**"), "**+5.6 [+3.4, +7.7]** and **-0.8 [-1.4, -0.1]**");
  });

  test("code spans: double backticks, one space stripped, contents literal", () => {
    assert.equal(para("``a ` b`` and ` x ` and `*not em*` and `<tag>`"), "`a ` b` and `x` and `*not em*` and `<tag>`");
  });

  test("a link address keeps balanced parentheses and drops escapes", () => {
    assert.equal(
      para("see [Monza](https://en.wikipedia.org/wiki/Monza_(circuit)) here"),
      "see [Monza](https://en.wikipedia.org/wiki/Monza_(circuit)) here",
    );
    const link = only(body("[Monza](https://en.wikipedia.org/wiki/Monza_(circuit))."), "paragraph").children[0];
    assert.equal(link.type === "link" && link.href, "https://en.wikipedia.org/wiki/Monza_(circuit)");
    const escaped = only(body("[a](m3\\_results.md)"), "paragraph").children[0];
    assert.equal(escaped.type === "link" && escaped.href, "m3_results.md");
  });

  test("links, with formatting in the text", () => {
    const p = only(body("see ([m3_ablations.md](m3_ablations.md)) and [`code` *x*](https://example.com/a)"), "paragraph");
    assert.equal(show(p.children), "see ([m3_ablations.md](m3_ablations.md)) and [`code` *x*](https://example.com/a)");
  });

  test("brackets that are not links stay text", () => {
    assert.equal(para("the [CLS] embedding, ['batch', 5, 80] and [95% CI]"), "the [CLS] embedding, ['batch', 5, 80] and [95% CI]");
  });

  test("a backslash escapes punctuation", () => {
    assert.equal(para("\\*not em\\* and a\\_b"), "*not em* and a_b");
  });

  test("`<` is always text, never HTML", () => {
    assert.equal(para("p < 0.05, (< 120 km/h) and a<b"), "p < 0.05, (< 120 km/h) and a<b");
    assert.deepEqual(reasons("p < 0.05, (< 120 km/h) and a<b"), []);
    const doc = body("before <script>alert(1)</script> after");
    assert.equal(show(only(doc, "paragraph").children), "before <script>alert(1)</script> after");
    assert.ok(doc.issues.some((i) => i.reason.startsWith("raw HTML")));
  });

  test("plainText drops the formatting", () => {
    assert.equal(plainText(only(body("**a** `b` [c](d) *e*"), "paragraph").children), "a b c e");
  });
});

describe("what the parser refuses (listed in issues, with the line)", () => {
  const cases: [string, string][] = [
    ["> quoted", "block quote"],
    ["<div>x</div>", "raw HTML block"],
    ["Title line\n===", "setext heading underline (use `#` headings; `---` needs a blank line above)"],
    ["text\n---", "setext heading underline (use `#` headings; `---` needs a blank line above)"],
    ["    indented code", "indented code block (use a ``` fence)"],
    ["a\tb", "tab character (use spaces)"],
    ["[a][ref]", "reference-style link"],
    ["[ref]: https://example.com", "link reference definition"],
    ["note[^1]", "footnote"],
    ["[a](b c)", "link address that is empty, has spaces or a title"],
    ["[a]()", "link address that is empty, has spaces or a title"],
    ["[a](<b>)", "link address in angle brackets"],
    ["[a](b(c)", "link address that is never closed"],
    ["![](figures/style_map.png)", "an image without alt text (the alt text is its caption)"],
    ["![a [b](c)](figures/style_map.png)", "an image inside text (put it on a line of its own)"],
    ["- item\n| a | b |\n|---|---|", "a table straight after a list item (GitHub reads it as part of the item; add a blank line)"],
    ["    | a |\n    |---|", "indented code block (use a ``` fence)"],
    ["## #", "a heading with no text (it would get no id)"],
    ["<https://example.com>", "autolink in angle brackets (write [text](url))"],
    ["see https://example.com", "bare URL (GitHub would link it; write [text](url))"],
    ["fish &amp; chips", "HTML entity (it would show as written)"],
    ["~~gone~~", "strikethrough"],
    ["an `open code span", "inline code that is never closed"],
    ["**never closed", "`**` opens emphasis that is never closed"],
    ["a ![fig](figures/x.png) in text", "an image inside text (put it on a line of its own)"],
    ["one  \ntwo", "hard line break"],
    ["| a | b |\n| 1 | 2 |", "a `|` line that is not a table (no delimiter row, or the cell counts differ)"],
    ["```\nnever closed", "code fence is never closed"],
    ["# Second title", "a second `# ` heading (the page has one h1)"],
  ];
  for (const [md, reason] of cases) {
    test(reason + ": " + JSON.stringify(md), () => {
      assert.ok(reasons(md).includes(reason), `got ${JSON.stringify(reasons(md))}`);
    });
  }

  test("issues carry the file's line number and text", () => {
    const doc = parseReport("# T\n\nok\n\n> quoted\n");
    assert.deepEqual(doc.issues, [{ line: 5, reason: "block quote", text: "> quoted" }]);
  });

  test("a file without a title is flagged", () => {
    assert.deepEqual(
      parseReport("Just text.").issues.map((i) => i.reason),
      ["the file does not start with a `# ` title"],
    );
  });

  test("tabs inside a code fence are fine", () => {
    assert.deepEqual(reasons("```make\nall:\n\techo hi\n```"), []);
  });
});

describe("the output is data, never HTML", () => {
  const ALLOWED_BLOCKS = new Set(["heading", "paragraph", "list", "table", "code", "image", "hr"]);
  const ALLOWED_INLINES = new Set(["text", "code", "strong", "em", "link"]);

  function checkInlines(nodes: Inline[]): void {
    for (const n of nodes) {
      assert.ok(ALLOWED_INLINES.has(n.type), n.type);
      if (n.type === "strong" || n.type === "em" || n.type === "link") checkInlines(n.children);
    }
  }
  function checkBlocks(blocks: Block[]): void {
    for (const b of blocks) {
      assert.ok(ALLOWED_BLOCKS.has(b.type), b.type);
      if (b.type === "heading" || b.type === "paragraph") checkInlines(b.children);
      if (b.type === "list") b.items.forEach(checkBlocks);
      if (b.type === "table") [b.head, ...b.rows].forEach((row) => row.forEach(checkInlines));
    }
  }

  test("every node is one of the known kinds, for hostile input too", () => {
    const doc = body('<img src=x onerror="alert(1)">\n\n- <b>x</b> [y](javascript:alert(1))\n\n| <i>a</i> |\n|---|\n| &lt; |');
    checkBlocks(doc.blocks);
    assert.match(JSON.stringify(doc.blocks), /<img src=x onerror=\\"alert\(1\)\\">/); // kept as text for React to escape
  });
});

/** The words of the parsed document, in reading order (list numbers included). */
function docWords(doc: ReportDoc): string[] {
  const out: string[] = [];
  const add = (s: string) => out.push(...(s.match(/[\p{L}\p{N}]+/gu) ?? []));
  const inl = (nodes: Inline[]) => {
    for (const n of nodes) {
      if (n.type === "text" || n.type === "code") add(n.text);
      else inl(n.children);
    }
  };
  const blocks = (bs: Block[]) => {
    for (const b of bs) {
      if (b.type === "heading" || b.type === "paragraph") inl(b.children);
      if (b.type === "code") add(`${b.lang} ${b.text}`);
      if (b.type === "image") add(b.alt);
      if (b.type === "table") [b.head, ...b.rows].forEach((row) => row.forEach(inl));
      if (b.type === "list") {
        b.items.forEach((item, k) => {
          if (b.ordered) add(String(b.start + k));
          blocks(item);
        });
      }
    }
  };
  inl(doc.titleInlines);
  if (doc.meta) inl(doc.meta);
  blocks(doc.blocks);
  return out;
}

describe("every published report", () => {
  for (const { file } of REPORTS) {
    test(`${file} parses with no unhandled line`, () => {
      const doc = parseReport(readReport(file));
      const listed = doc.issues.map((i) => `  ${file}:${i.line}: ${i.reason}\n      ${i.text}`).join("\n");
      assert.equal(doc.issues.length, 0, `unhandled lines:\n${listed}`);
      assert.ok(doc.title, "a `# ` title");
    });

    test(`${file} keeps every word, in order`, () => {
      // Link addresses are not text; one may hold balanced parentheses.
      const source = readReport(file).replace(/\]\((?:[^()\s]|\([^()\s]*\))*\)/g, "]");
      const expected = source.match(/[\p{L}\p{N}]+/gu) ?? [];
      const got = docWords(parseReport(readReport(file)));
      let at = -1;
      for (let k = 0; k < Math.max(expected.length, got.length) && at < 0; k++) if (expected[k] !== got[k]) at = k;
      const near = (words: string[]) => JSON.stringify(words.slice(Math.max(0, at - 3), at + 6));
      assert.equal(at, -1, `first difference at word ${at}: the file has ${near(expected)}, the tokens ${near(got)}`);
    });

    test(`${file} has every \`-\` line as a list item at the depth its indent gives, and ordered lists start at 1`, () => {
      // Outside code fences a line starting with `- ` is always a list item; its depth is the
      // number of less indented `- ` lines still open above it. A heading closes them all (a
      // bullet list nested in a `1.` item, after an earlier list, is depth 0, as in m5_backend.md).
      // A nesting bug changes the depths. An ordered list that starts elsewhere is usually a line
      // beginning with a year after a blank line, which GitHub would number too.
      let fenced = false;
      const open: number[] = [];
      const expected: number[] = [];
      for (const line of readReport(file).split("\n")) {
        if (/^\s*(?:`{3,}|~{3,})/.test(line)) fenced = !fenced;
        if (!fenced && /^ {0,3}#{1,6}(?:\s|$)/.test(line)) open.length = 0;
        if (fenced || !/^\s*- \S/.test(line)) continue;
        const indent = line.length - line.trimStart().length;
        while (open.length > 0 && open[open.length - 1] >= indent) open.pop();
        expected.push(open.length);
        open.push(indent);
      }
      const depths: number[] = [];
      const starts: number[] = [];
      const visit = (blocks: Block[], depth: number) => {
        for (const b of blocks) {
          if (b.type !== "list") continue;
          if (b.ordered) starts.push(b.start);
          for (const item of b.items) {
            if (!b.ordered) depths.push(depth);
            visit(item, b.ordered ? depth : depth + 1);
          }
        }
      };
      visit(parseReport(readReport(file)).blocks, 0);
      assert.deepEqual(depths, expected);
      assert.deepEqual(
        starts.filter((s) => s !== 1),
        [],
      );
    });

    test(`${file} has no emphasis starting or ending inside a word`, () => {
      // Catches `_` flanking bugs: mae_error_exit must never become mae<em>error</em>exit.
      const wordy = /[\p{L}\p{N}]/u;
      const found: string[] = [];
      const check = (nodes: Inline[]) =>
        nodes.forEach((n, k) => {
          if (n.type === "text" || n.type === "code") return;
          if (n.type !== "link") {
            const before = nodes[k - 1];
            const after = nodes[k + 1];
            if (before?.type === "text" && wordy.test(before.text.slice(-1))) found.push(plainText(n.children));
            if (after?.type === "text" && wordy.test(after.text[0])) found.push(plainText(n.children));
          }
          check(n.children);
        });
      walkBlocks(parseReport(readReport(file)).blocks, (b) => {
        if (b.type === "heading" || b.type === "paragraph") check(b.children);
        if (b.type === "table") [b.head, ...b.rows].forEach((row) => row.forEach(check));
      });
      assert.deepEqual(found, []);
    });

    test(`${file} has unique heading ids`, () => {
      const ids = parseReport(readReport(file)).headings.map((h) => h.id);
      assert.deepEqual(
        ids.filter((id, k) => ids.indexOf(id) !== k),
        [],
      );
      assert.ok(ids.every((id) => id !== ""), "every heading has an id");
    });
  }
});
