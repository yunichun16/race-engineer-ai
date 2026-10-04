import assert from "node:assert/strict";
import { test } from "node:test";

import { readFileSync } from "node:fs";

import { chartHeading, devMeta, inline, questionMeter, shortEvent, toBlocks, toolStepLabel } from "./format.ts";

const t = (text: string) => ({ kind: "text" as const, text });
const b = (text: string) => ({ kind: "bold" as const, text });
const c = (text: string) => ({ kind: "code" as const, text });

test("paragraphs split on blank lines and keep single line breaks", () => {
  assert.deepEqual(toBlocks("One.\nStill one.\n\n\nTwo.\r\n\r\nThree."), [
    { kind: "paragraph", lines: [[t("One.")], [t("Still one.")]] },
    { kind: "paragraph", lines: [[t("Two.")]] },
    { kind: "paragraph", lines: [[t("Three.")]] },
  ]);
  assert.deepEqual(toBlocks(""), []);
  assert.deepEqual(toBlocks("\n  \n"), []);
});

test("bullets with -, • and *, and a lead line straight before them", () => {
  assert.deepEqual(toBlocks("Here is what the tools returned:\n- one\n• two\n* three\nAfter."), [
    { kind: "paragraph", lines: [[t("Here is what the tools returned:")]] },
    { kind: "bullets", items: [[t("one")], [t("two")], [t("three")]] },
    { kind: "paragraph", lines: [[t("After.")]] },
  ]);
});

test("numbered lists keep their first number; an indented line continues the item", () => {
  assert.deepEqual(toBlocks("3. third\n4. fourth\n   carried on\n\n1. again"), [
    { kind: "numbered", start: 3, items: [[t("third")], [t("fourth carried on")]] },
    { kind: "numbered", start: 1, items: [[t("again")]] },
  ]);
  // A bullet list straight after a numbered one starts a new block.
  assert.deepEqual(
    toBlocks("1. a\n- b").map((block) => block.kind),
    ["numbered", "bullets"],
  );
});

test("things that only look like lists stay text", () => {
  assert.deepEqual(toBlocks("-5 °C at the start"), [{ kind: "paragraph", lines: [[t("-5 °C at the start")]] }]);
  assert.deepEqual(toBlocks("2026. A new era"), [{ kind: "paragraph", lines: [[t("2026. A new era")]] }]);
  assert.deepEqual(toBlocks("**Bold** start"), [{ kind: "paragraph", lines: [[b("Bold"), t(" start")]] }]);
  assert.deepEqual(toBlocks("-"), [{ kind: "paragraph", lines: [[t("-")]] }]);
});

test("bold and code inside a line", () => {
  assert.deepEqual(inline("NOR gained **0.27 s** at `T6`, then **lost it**."), [
    t("NOR gained "),
    b("0.27 s"),
    t(" at "),
    c("T6"),
    t(", then "),
    b("lost it"),
    t("."),
  ]);
  // Code is found first, so nothing inside it is bold, and bold can't wrap code: its markers
  // then stay literal.
  assert.deepEqual(inline("`**not bold**` and **`code` in bold**"), [
    c("**not bold**"),
    t(" and **"),
    c("code"),
    t(" in bold**"),
  ]);
});

test("unclosed or spaced markers stay literal", () => {
  assert.deepEqual(inline("half **bo"), [t("half **bo")]);
  assert.deepEqual(inline("a ` b"), [t("a ` b")]);
  assert.deepEqual(inline("2 ** 3 ** 4"), [t("2 ** 3 ** 4")]);
  assert.deepEqual(inline("****"), [t("****")]);
  assert.deepEqual(inline("``"), [t("``")]);
});

test("HTML stays text", () => {
  const evil = '<script>alert("x")</script> <img src=x onerror=alert(1)> &amp;';
  assert.deepEqual(toBlocks(evil), [{ kind: "paragraph", lines: [[t(evil)]] }]);
  assert.deepEqual(toBlocks("- **<b>hi</b>**"), [{ kind: "bullets", items: [[b("<b>hi</b>")]] }]);
});

test("a tool step's label: the verb, then the arguments in the model's order", () => {
  assert.equal(
    toolStepLabel("find_mistakes", { event: "monza", driver: "LEC", year: 2025, session: "Q", limit: 10 }),
    "Finding mistakes · monza · LEC · 2025 · Q",
  );
  assert.equal(
    toolStepLabel("explain_corner", { event: "abu dhabi", driver: "GAS", lap: 5, corner: "8", session: "R" }),
    "Explaining the corner · abu dhabi · GAS · lap 5 · turn 8 · R",
  );
  // find_session takes fields, not a free-text query (engine tools/find_session.py).
  assert.equal(toolStepLabel("find_session", { session: "R", recent: 0 }), "Finding the session · R · latest");
  assert.equal(toolStepLabel("find_session", { recent: 1, session: "Q" }), "Finding the session · previous · Q");
  assert.equal(toolStepLabel("find_session", { recent: 3 }), "Finding the session · 3 back");
  assert.equal(
    toolStepLabel("find_session", { event: "monza", year: 2025, session: "Q" }),
    "Finding the session · monza · 2025 · Q",
  );
  assert.equal(toolStepLabel("find_session", { year: 2024, round: 5 }), "Finding the session · 2024 · round 5");
  assert.equal(toolStepLabel("list_sessions", {}), "Listing the sessions");
  assert.equal(
    toolStepLabel("new_tool", { flag: true, nested: { a: 1 }, empty: "", list: [1] }),
    "New tool",
  );
  assert.equal(
    toolStepLabel("find_session", { event: "x".repeat(60) }),
    `Finding the session · ${"x".repeat(39)}…`,
  );
});

test("the question meter", () => {
  assert.equal(questionMeter(7), "7 questions left in this conversation");
  assert.equal(questionMeter(1), "Last question in this conversation");
  assert.equal(questionMeter(0), "No questions left in this conversation");
});

test("the development line", () => {
  assert.equal(devMeta({ model: "scripted", ms: 180, cost_usd: 0.003281 }), "scripted · 0.2 s · $0.0033 simulated");
  assert.equal(devMeta({ model: "claude-sonnet-5-5", ms: 12345, cost_usd: 0.05 }), "claude-sonnet-5-5 · 12.3 s · $0.0500");
  assert.equal(devMeta({ model: "", ms: 0, cost_usd: null }), "unknown model · 0.0 s");
});

/** A chart fixture's payload (the `structuredContent` of web/src/mcp-app/sample-<name>.json). */
function sample(name: string): Record<string, unknown> {
  const url = new URL(`../../mcp-app/sample-${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(url, "utf8")).structuredContent;
}

test("chart card headings come from the payload", () => {
  assert.deepEqual(chartHeading("find-mistakes", sample("find-mistakes")), {
    title: "Flagged mistakes",
    subtitle: "2026 Sample GP · Qualifying",
    label: "Flagged mistakes, 2026 Sample Grand Prix, Qualifying",
  });
  assert.equal(chartHeading("find-mistakes", sample("find-mistakes-driver")).title, "Flagged mistakes · AAA");
  assert.deepEqual(chartHeading("explain-corner", sample("explain-corner")), {
    title: "AAA · lap 10 · turn 3",
    subtitle: "2026 Sample GP · Qualifying",
    label: "AAA · lap 10 · turn 3, 2026 Sample Grand Prix, Qualifying",
  });
  assert.equal(chartHeading("race-summary", sample("race-summary")).title, "Race summary");
  assert.equal(chartHeading("race-summary", { ...sample("race-summary"), session: "Sprint" }).title, "Sprint summary");
  assert.deepEqual(chartHeading("compare-styles", sample("compare-styles")), {
    title: "Driving styles: AAA and BBB",
    subtitle: "2025 season",
    label: "Driving styles: AAA and BBB, 2025 season",
  });
  assert.equal(chartHeading("compare-laps", sample("compare-laps")).title, "Lap comparison: AAA and BBB");
});

test("a payload missing fields still gets a heading", () => {
  assert.deepEqual(chartHeading("find-mistakes", null), { title: "Flagged mistakes", subtitle: null, label: "Flagged mistakes" });
  assert.deepEqual(chartHeading("explain-corner", { driver: "NOR" }), {
    title: "Corner telemetry",
    subtitle: null,
    label: "Corner telemetry",
  });
  assert.equal(chartHeading("compare-laps", { drivers: [{ code: "NOR" }] }).title, "Lap comparison");
  assert.equal(chartHeading("telemetry", {}).title, "Chart");
  assert.equal(chartHeading("toString", {}).title, "Chart");
  assert.equal(shortEvent("Abu Dhabi Grand Prix"), "Abu Dhabi GP");
  assert.equal(shortEvent("Pre-Season Testing"), "Pre-Season Testing");
});
