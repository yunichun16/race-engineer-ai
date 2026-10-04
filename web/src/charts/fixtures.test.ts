/**
 * Every chart's type guard against the dev host's sample results (src/mcp-app/sample-*.json,
 * synthetic, written by engine/scripts/export_sample_result.py): each result passes its own
 * chart's guard and no other chart's, and each error result (no structuredContent) fails it
 * and carries the tool's message for the status line. The fixtures are read with fs: a JSON
 * import would need import attributes under Node.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { isComparison } from "./compare-laps.ts";
import { isStyleChart } from "./compare-styles.ts";
import { isCornerChart } from "./explain-corner.ts";
import { isMistakeList } from "./find-mistakes.ts";
import { isRaceSummary } from "./race-summary.ts";

const GUARDS: Record<string, (value: unknown) => boolean> = {
  "compare-laps": isComparison,
  "find-mistakes": isMistakeList,
  "explain-corner": isCornerChart,
  "compare-styles": isStyleChart,
  "race-summary": isRaceSummary,
};

interface Fixture {
  content: { type: string; text?: string }[];
  structuredContent?: unknown;
  isError?: boolean;
}

const dir = new URL("../mcp-app/", import.meta.url);
const fixtures = readdirSync(dir)
  .filter((f) => /^sample-.*\.json$/.test(f) && !f.startsWith("sample-telemetry"))
  .map((file) => {
    const named = (name: string) => new RegExp(`^sample-${name}(-|\\.json$)`).test(file);
    const chart = Object.keys(GUARDS).find(named);
    const data = JSON.parse(readFileSync(new URL(file, dir), "utf8")) as Fixture;
    return { file, chart, data };
  });

test("every sample result belongs to a chart", () => {
  assert.ok(fixtures.length >= 16, `found ${fixtures.length} fixtures`);
  for (const { file, chart } of fixtures) assert.ok(chart, `${file} matches no chart`);
  for (const name of Object.keys(GUARDS)) {
    assert.ok(fixtures.some((f) => f.chart === name && !f.data.isError), `no sample for ${name}`);
  }
});

test("each result passes its own chart's guard and no other's", () => {
  for (const { file, chart, data } of fixtures.filter((f) => !f.data.isError)) {
    for (const [name, guard] of Object.entries(GUARDS)) {
      assert.equal(guard(data.structuredContent), name === chart, `${name} guard on ${file}`);
    }
  }
});

test("each error result fails its guard and says why", () => {
  const errors = fixtures.filter((f) => f.data.isError);
  assert.ok(errors.length >= 4, `found ${errors.length} error fixtures`);
  for (const { file, chart, data } of errors) {
    assert.equal(GUARDS[chart!](data.structuredContent), false, file);
    const first = data.content[0];
    assert.ok(first?.type === "text" && first.text?.trim(), `${file} has no message`);
  }
});

test("the guards reject junk", () => {
  for (const [name, guard] of Object.entries(GUARDS)) {
    for (const junk of [undefined, null, 0, "text", [], {}]) {
      assert.equal(guard(junk), false, `${name} guard on ${JSON.stringify(junk)}`);
    }
  }
});
