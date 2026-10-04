import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { resolveImage, resolveLink } from "./links.ts";
import { REPORTS, reportBySlug } from "./manifest.ts";
import { collectLinks, parseReport, type Block } from "./markdown.ts";
import { OMITTED_NOTE, applyOmission, squash, type PageDoc } from "./omit.ts";
import { readReport } from "./testing.ts";

const DOC = `# Title

Intro.

## Keep

One. Cut this sentence. Two.

## Drop me

Gone, with its table:

| a | b |
|---|---|
| 71% | 35 / 49 |

### Under it

Gone too.

## After

- An item. Cut this sentence.
- Cut this sentence.
`;

describe("applyOmission", () => {
  test("without an omission the page is the parsed report", () => {
    const doc = parseReport(DOC);
    const page = applyOmission(doc, undefined);
    assert.deepEqual(page.blocks, doc.blocks);
    assert.deepEqual(page.headings, doc.headings);
    assert.notEqual(page.blocks, doc.blocks, "a copy, so the parsed report is never changed");
  });

  test("a section becomes one omitted block, up to the next heading of its level", () => {
    const page = applyOmission(parseReport(DOC), { sections: ["Drop me"] });
    assert.deepEqual(
      page.blocks.map((b) => (b.type === "heading" ? `h${b.level} ${b.text}` : b.type)),
      ["paragraph", "h2 Keep", "paragraph", "omitted", "h2 After", "list"],
    );
    assert.deepEqual(page.blocks[3], { type: "omitted", section: "Drop me" });
    assert.deepEqual(
      page.headings.map((h) => h.text),
      ["Title", "Keep", "After"],
      "the contents lose the section's headings",
    );
    assert.ok(!JSON.stringify(page).includes("71%"));
  });

  test("a sentence is cut with one space, everywhere, and an emptied item or paragraph goes", () => {
    const page = applyOmission(parseReport(DOC), { sentences: ["Cut  this\nsentence."] });
    const json = JSON.stringify(page.blocks);
    assert.ok(!json.includes("Cut this"));
    assert.ok(json.includes('"One. Two."'));
    const list = page.blocks.at(-1) as Extract<Block, { type: "list" }>;
    assert.deepEqual(list.items, [[{ type: "paragraph", children: [{ type: "text", text: "An item." }] }]]);
    const alone = applyOmission(parseReport("# T\n\nCut me.\n\n- Cut me.\n\nKept."), { sentences: ["Cut me."] });
    assert.deepEqual(alone.blocks, [{ type: "paragraph", children: [{ type: "text", text: "Kept." }] }]);
  });

  test("the parsed report is left as it was", () => {
    const doc = parseReport(DOC);
    const before = JSON.stringify(doc);
    applyOmission(doc, { sections: ["Drop me"], sentences: ["Cut this sentence."] });
    assert.equal(JSON.stringify(doc), before);
  });

  test("anything named but missing throws, so the build fails rather than showing it", () => {
    assert.throws(() => applyOmission(parseReport(DOC), { sections: ["Not here"] }), /"Not here" to omit is not a heading/);
    assert.throws(() => applyOmission(parseReport(DOC), { sentences: ["Not here."] }), /sentence to omit is not in a paragraph/);
    // A sentence split by formatting isn't plain text, so it isn't found either.
    assert.throws(() => applyOmission(parseReport("# T\n\nA **bold** sentence."), { sentences: ["A bold sentence."] }));
  });

  test("the note says why, without a number", () => {
    assert.match(OMITTED_NOTE, /^A first human check of the 50 top findings is in the repository's copy of this report\./);
    assert.ok(!/\d+ ?%/.test(OMITTED_NOTE));
  });
});

/** Figures from the human review, as written in its reports (site-smoke searches the built pages for the same). */
const REVIEW_FIGURES: readonly string[] = ["71%", "69%", "35 / 49", "35 of 49", "34 of 49", "7 in 10"];

/** Every published page as the site renders it. */
const PAGES = new Map<string, PageDoc>(REPORTS.map((r) => [r.slug, applyOmission(parseReport(readReport(r.file)), r.omit)]));

describe("no accuracy figure on any report page (plan 13)", () => {
  test("m4-summary renders without the human-checked precision", () => {
    const page = PAGES.get("m4-summary")!;
    const tokens = JSON.stringify(page);
    for (const figure of ["71%", "35 / 49", "7 in 10"]) assert.ok(!tokens.includes(figure), `"${figure}" is on the page`);
    assert.ok(!page.headings.some((h) => h.text === "Human-checked precision"));
    assert.equal(page.blocks.filter((b) => b.type === "omitted").length, 1);
    // The omitted section sat between Findings and What it means; both stay.
    const order = page.blocks.flatMap((b) => (b.type === "heading" ? [b.text] : b.type === "omitted" ? ["(omitted)"] : []));
    assert.deepEqual(order, ["Findings", "(omitted)", "What it means", "Limits"]);
    // The rest of "What it means" is still there.
    assert.ok(squash(tokens).includes("removes most of the costly-looking ones that aren't driving. The tools should say which kind each finding is."));
  });

  test("the technical report and the model card render without it, and keep it in the repository (M7 decision 12)", () => {
    for (const slug of ["technical-report", "model-card"]) {
      const entry = reportBySlug(slug)!;
      const page = PAGES.get(slug)!;
      const tokens = JSON.stringify(page);
      for (const figure of REVIEW_FIGURES) assert.ok(!tokens.includes(figure), `"${figure}" is on ${slug}`);
      assert.ok(!page.headings.some((h) => h.text === "Human-checked precision"), slug);
      assert.equal(page.blocks.filter((b) => b.type === "omitted").length, 1, slug);
      // The repository copy keeps the section, its figures and the one-reviewer caveat.
      const source = readReport(entry.file);
      assert.ok(parseReport(source).headings.some((h) => h.text === "Human-checked precision"), entry.file);
      for (const kept of ["71% [58%, 82%]", "one reviewer and 50 findings"]) assert.ok(squash(source).includes(kept), `${entry.file} lacks "${kept}"`);
    }
  });

  test("no page shows a figure from the human review", () => {
    for (const [slug, page] of PAGES) {
      const tokens = JSON.stringify(page);
      assert.deepEqual(
        REVIEW_FIGURES.filter((figure) => tokens.includes(figure)),
        [],
        slug,
      );
    }
  });

  test("no page quotes a percentage with an interval", () => {
    for (const [slug, page] of PAGES) assert.doesNotMatch(JSON.stringify(page), /\d+% \[/, slug);
  });

  test("no page links to the human review", () => {
    for (const [slug, page] of PAGES) {
      const lead: Block = { type: "paragraph", children: [...page.titleInlines, ...(page.meta ?? [])] };
      const blocks = [lead, ...page.blocks.filter((b): b is Block => b.type !== "omitted")];
      for (const { href, image } of collectLinks(blocks)) {
        const target = image ? resolveImage(href) : resolveLink(href);
        assert.ok(!("href" in target && target.href?.startsWith("/report/m4-review")), `${slug} links ${href}`);
      }
    }
    assert.equal(reportBySlug("m4-review"), undefined);
    assert.equal(resolveLink("m4_review.md").kind, "repo", "a link to it shows as a file in the repository");
  });
});
