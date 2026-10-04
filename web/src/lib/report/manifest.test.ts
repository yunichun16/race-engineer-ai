import assert from "node:assert/strict";
import { test } from "node:test";
import {
  EXCLUDED,
  GROUPS,
  REPORTS,
  neighbours,
  reportByFile,
  reportBySlug,
  reportsByGroup,
  slugForFile,
} from "./manifest.ts";
import { parseReport } from "./markdown.ts";
import { squash } from "./omit.ts";
import { readReport, reportFiles } from "./testing.ts";

test("every report file is published or excluded on purpose, never both", () => {
  const listed = new Set([...REPORTS.map((r) => r.file), ...EXCLUDED]);
  const missing = reportFiles().filter((f) => !listed.has(f));
  assert.deepEqual(missing, [], "add these to REPORTS or EXCLUDED in lib/report/manifest.ts");
  assert.deepEqual(
    EXCLUDED.filter((f) => reportByFile(f)),
    [],
  );
});

test("every listed file exists", () => {
  const onDisk = new Set(reportFiles());
  assert.deepEqual(
    [...REPORTS.map((r) => r.file), ...EXCLUDED].filter((f) => !onDisk.has(f)),
    [],
  );
});

test("the human review, the chat accuracy check and the ablation smoke run are not published", () => {
  assert.deepEqual([...EXCLUDED].sort(), ["m3_ablations_smoke.md", "m7_chat_accuracy.md"]);
  assert.equal(reportBySlug("m4-review"), undefined);
  assert.equal(reportBySlug("m3-ablations-smoke"), undefined);
  assert.equal(reportBySlug("m7-chat-accuracy"), undefined);
});

test("the slug is the file stem with _ turned into -, and slugs are unique", () => {
  for (const r of REPORTS) assert.equal(r.slug, slugForFile(r.file));
  assert.equal(slugForFile("m3_style_within_season.md"), "m3-style-within-season");
  assert.equal(new Set(REPORTS.map((r) => r.slug)).size, REPORTS.length);
});

test("the 18 published reports, grouped as the plan lists them", () => {
  assert.equal(REPORTS.length, 18);
  assert.deepEqual(
    reportsByGroup().map((g) => [g.group, g.reports.map((r) => r.slug)]),
    [
      ["Summaries", ["technical-report", "model-card", "m4-summary", "m3-summary"]],
      ["Mistakes", ["m4-mistakes", "m4-scoring", "m3-results", "baselines", "m3-ablations"]],
      ["Driving style", ["m4-style", "m3-style-within-season"]],
      ["The 2026 rule change", ["m3-shift"]],
      ["Data", ["dataset-card", "data-quality"]],
      ["Engineering", ["m4-onnx", "m5-backend", "m6-website", "m7-ship"]],
    ],
  );
  assert.deepEqual(
    REPORTS.map((r) => r.group),
    [...REPORTS.map((r) => r.group)].sort((a, b) => GROUPS.indexOf(a) - GROUPS.indexOf(b)),
    "REPORTS is in group order",
  );
});

test("lookups and the summary labels", () => {
  assert.equal(reportBySlug("m4-summary")?.file, "m4_summary.md");
  assert.equal(reportBySlug("nope"), undefined);
  assert.equal(reportBySlug("M4-SUMMARY"), undefined, "exact matches only");
  assert.equal(reportBySlug("constructor"), undefined);
  assert.equal(reportByFile("m4_summary.md")?.label, "From flagged corners to explanations");
  assert.equal(reportBySlug("m3-summary")?.label, "What the Transformer learned");
  assert.equal(reportBySlug("m6-website")?.file, "m6_website.md");
  assert.equal(reportBySlug("m6-website")?.label, "M6: the website");
  assert.equal(reportBySlug("baselines")?.label, undefined);
  assert.equal(reportBySlug("technical-report")?.file, "technical_report.md");
  assert.equal(reportBySlug("technical-report")?.label, "Technical report");
  assert.equal(reportBySlug("model-card")?.file, "model_card.md");
  assert.equal(reportBySlug("model-card")?.label, "Model card");
  assert.equal(reportBySlug("m7-ship")?.group, "Engineering");
});

test("neighbours follow the list order", () => {
  assert.deepEqual(
    [neighbours("technical-report").previous, neighbours("technical-report").next?.slug],
    [null, "model-card"],
  );
  assert.deepEqual(
    [neighbours("m4-summary").previous?.slug, neighbours("m4-summary").next?.slug],
    ["model-card", "m3-summary"],
  );
  assert.deepEqual(
    [neighbours("m3-summary").previous?.slug, neighbours("m3-summary").next?.slug],
    ["m4-summary", "m4-mistakes"],
  );
  assert.deepEqual(
    [neighbours("m5-backend").previous?.slug, neighbours("m5-backend").next?.slug],
    ["m4-onnx", "m6-website"],
  );
  assert.deepEqual([neighbours("m6-website").previous?.slug, neighbours("m6-website").next?.slug], ["m5-backend", "m7-ship"]);
  assert.deepEqual([neighbours("m7-ship").previous?.slug, neighbours("m7-ship").next], ["m6-website", null]);
  assert.deepEqual(neighbours("m4-review"), { previous: null, next: null });
});

test("m4-summary, the technical report and the model card leave out exactly the human-checked precision (plan 13)", () => {
  assert.deepEqual(reportBySlug("m4-summary")?.omit, {
    sections: ["Human-checked precision"],
    sentences: [
      "What is left is mostly real: about 7 in 10 of the top findings, with race-control findings far more reliable than telemetry-only ones.",
      "The intervals are wide, and some verdicts come from the traces alone (7 of 50).",
    ],
  });
  for (const slug of ["technical-report", "model-card"]) {
    assert.deepEqual(reportBySlug(slug)?.omit, { sections: ["Human-checked precision"] }, slug);
  }
  assert.deepEqual(
    REPORTS.filter((r) => r.omit).map((r) => r.slug),
    ["technical-report", "model-card", "m4-summary"],
  );
});

// If a report changes so that an omitted part no longer appears word for word, the omission
// would silently stop working (or the build would fail). Either way the report needs a look.
for (const r of REPORTS.filter((e) => e.omit)) {
  test(`${r.file}: every omitted heading and sentence still appears verbatim in the file`, () => {
    const source = readReport(r.file);
    const headings = parseReport(source).headings.map((h) => h.text);
    for (const section of r.omit?.sections ?? []) {
      assert.ok(headings.includes(section), `no heading "${section}" in report/${r.file}: update its omit entry`);
    }
    const text = squash(source);
    for (const sentence of r.omit?.sentences ?? []) {
      assert.ok(text.includes(squash(sentence)), `no sentence "${sentence}" in report/${r.file}: update its omit entry`);
    }
  });
}
