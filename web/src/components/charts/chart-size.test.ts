/**
 * The reserved heights: the classes and the model agree on each chart's layouts, the model
 * follows the data it is given (more rows, more room; no map or replay, less), and every site
 * fixture gets a usable height. How close each height comes to the drawn chart is measured in
 * /dev/charts, not here: only a browser draws the charts.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { CHART_NAMES, CHART_SIZE } from "./chart-names.ts";
import { chartSizeStyle, reservedHeights, SIZE_BREAKS } from "./chart-size.ts";

const samples = new URL("../../mcp-app/", import.meta.url);

test("CHART_SIZE steps at SIZE_BREAKS, one height variable per layout", () => {
  for (const name of CHART_NAMES) {
    const classes = CHART_SIZE[name].split(" ");
    assert.equal(classes[0], "[--chart-h:var(--chart-h0)]", name);
    const steps = classes.slice(1).map((variant, i) => {
      const m = variant.match(/^@min-\[(\d+)px\]:\[--chart-h:var\(--chart-h(\d)\)\]$/);
      assert.ok(m, `${name}: ${variant}`);
      assert.equal(Number(m[2]), i + 1, `${name}: ${variant}`);
      return Number(m[1]);
    });
    assert.deepEqual(steps, [...SIZE_BREAKS[name]], name);
    assert.equal(reservedHeights(name).length, steps.length + 1, name);
  }
});

test("chartSizeStyle sets the variables CHART_SIZE reads, in whole pixels", () => {
  assert.deepEqual(chartSizeStyle("race-summary"), { "--chart-h0": "575px", "--chart-h1": "530px", "--chart-h2": "535px" });
  for (const name of CHART_NAMES) {
    const style = chartSizeStyle(name);
    assert.deepEqual(Object.keys(style), reservedHeights(name).map((_, i) => `--chart-h${i}`), name);
    for (const value of Object.values(style)) assert.match(value, /^\d+px$/, name);
  }
});

const list = (rows: number, track: object | null = {}) => ({ mistakes: Array.from({ length: rows }, () => ({})), track });

test("find-mistakes: a typical result without data, a row's room per mistake with it", () => {
  assert.deepEqual(reservedHeights("find-mistakes"), reservedHeights("find-mistakes", list(10)));
  const [narrow1, wide1] = reservedHeights("find-mistakes", list(1));
  const [narrow5, wide5] = reservedHeights("find-mistakes", list(5));
  const [narrow25, wide25] = reservedHeights("find-mistakes", list(25));
  assert.ok(narrow1 < narrow5 && narrow5 < narrow25);
  assert.ok(wide1 <= wide5 && wide5 < wide25);
  // Below 640 px the map stacks over the list; from 640 it sits beside it, so few rows cost
  // nothing more than the map.
  assert.equal(narrow5 - narrow1, 4 * 92);
  assert.equal(reservedHeights("find-mistakes", list(1))[1], reservedHeights("find-mistakes", list(2))[1]);
  // No track, no map: less room; no mistakes: only the note.
  assert.ok(reservedHeights("find-mistakes", list(3, null))[0] < reservedHeights("find-mistakes", list(3))[0]);
  assert.deepEqual(reservedHeights("find-mistakes", list(0)), [260, 190]);
});

test("explain-corner: less room below 561 px without a replay", () => {
  const [withReplay, wideWith] = reservedHeights("explain-corner", { replay: {} });
  const [without, wideWithout] = reservedHeights("explain-corner", { replay: null });
  assert.ok(without < withReplay);
  assert.equal(wideWith, wideWithout);
  assert.deepEqual(reservedHeights("explain-corner"), [withReplay, wideWith]);
});

test("race-summary: a row per position, as the chart sizes its rows", () => {
  const field = (drivers: number, lowest = drivers) => ({
    drivers: Array.from({ length: drivers }, (_, i) => ({ positions: [i + 1, i === 0 ? lowest : i + 1] })),
  });
  assert.deepEqual(reservedHeights("race-summary"), reservedHeights("race-summary", field(20)));
  // 22 rows of 13 px narrow and 15 px wide (plus 40 px of axes), as on a full 2026 grid.
  const [n22, m22, w22] = reservedHeights("race-summary", field(22));
  assert.deepEqual([n22, m22, w22], [275 + 40 + 22 * 13, 230 + 40 + 22 * 13, 195 + 40 + 22 * 15]);
  // Four drivers but positions down to 22 still need 22 rows.
  assert.deepEqual(reservedHeights("race-summary", field(4, 22)), [n22, m22, w22]);
  // A small field gets tall rows, at most 28 px.
  assert.deepEqual(reservedHeights("race-summary", field(4)), [275 + 40 + 4 * 28, 230 + 40 + 4 * 28, 195 + 40 + 4 * 28]);
  assert.deepEqual(reservedHeights("race-summary", field(10)), [275 + 40 + 10 * 24, 230 + 40 + 10 * 24, 195 + 40 + 10 * 24]);
});

test("data a chart can't take falls back to the typical result", () => {
  for (const name of CHART_NAMES) {
    for (const junk of [null, 1, "x", [], { mistakes: "no", drivers: 3 }]) {
      const heights = reservedHeights(name, junk);
      assert.equal(heights.length, SIZE_BREAKS[name].length + 1, name);
      for (const h of heights) assert.ok(Number.isFinite(h) && h > 0, `${name}: ${h}`);
    }
  }
});

test("every site fixture gets a height for each layout", () => {
  let seen = 0;
  for (const file of readdirSync(samples).filter((f) => /^sample-.*\.json$/.test(f))) {
    const sample = JSON.parse(readFileSync(new URL(file, samples), "utf8"));
    const chart = CHART_NAMES.find((name) => file === `sample-${name}.json` || file.startsWith(`sample-${name}-`));
    if (!chart || !sample.structuredContent) continue; // telemetry, and the error results
    const heights = reservedHeights(chart, sample.structuredContent);
    assert.equal(heights.length, SIZE_BREAKS[chart].length + 1, file);
    for (const h of heights) assert.ok(h >= 150 && h <= 3000, `${file}: ${h}`);
    seen++;
  }
  assert.ok(seen >= 12, `only ${seen} fixtures`);
});
