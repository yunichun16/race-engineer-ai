import assert from "node:assert/strict";
import { test } from "node:test";
import { REPORTS } from "./manifest.ts";
import { parseReport } from "./markdown.ts";
import { applyOmission } from "./omit.ts";
import { readReport } from "./testing.ts";
import { tocItems } from "./toc.ts";

test("h2 and h3 in order, the title and deeper levels left out", () => {
  const doc = parseReport("# T\n\n## A\n\n### B\n\n#### C\n\n## D");
  assert.deepEqual(tocItems(doc.headings), [
    { id: "a", text: "A", level: 2 },
    { id: "b", text: "B", level: 3 },
    { id: "d", text: "D", level: 2 },
  ]);
});

test("fewer than two headings make no list", () => {
  assert.deepEqual(tocItems(parseReport("# T\n\nText.").headings), []);
  assert.deepEqual(tocItems(parseReport("# T\n\n## Only").headings), []);
});

test("the published reports: which pages get a list", () => {
  const without = REPORTS.filter((r) => tocItems(applyOmission(parseReport(readReport(r.file)), r.omit).headings).length === 0);
  assert.deepEqual(
    without.map((r) => r.slug),
    ["m4-scoring", "m3-style-within-season", "m3-shift"],
  );
});

test("m4-summary's list has no entry for the omitted section", () => {
  const entry = REPORTS.find((r) => r.slug === "m4-summary")!;
  const items = tocItems(applyOmission(parseReport(readReport(entry.file)), entry.omit).headings);
  assert.deepEqual(
    items.map((i) => i.text),
    ["Findings", "What it means", "Limits"],
  );
});
