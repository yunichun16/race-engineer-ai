/**
 * The site's chart names against the bundles the tools declare (engine/src/race_engineer/tools,
 * `chart="..."`), so a new or renamed chart bundle can't reach the `Chart` dispatcher unseen,
 * and the names the dispatcher accepts. The reserved heights are tested in chart-size.test.ts.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { CHART_NAMES, isChartName } from "./chart-names.ts";

const tools = new URL("../../../../engine/src/race_engineer/tools/", import.meta.url);

function declaredBundles(): Set<string> {
  const bundles = new Set<string>();
  for (const file of readdirSync(tools).filter((f) => f.endsWith(".py"))) {
    for (const m of readFileSync(new URL(file, tools), "utf8").matchAll(/\bchart="([a-z-]+)"/g)) bundles.add(m[1]);
  }
  return bundles;
}

test("the site has a chart for every bundle a tool declares, and no other", () => {
  const declared = declaredBundles();
  assert.ok(declared.size >= 5, `found only ${[...declared].join(", ")}`);
  assert.deepEqual([...declared].sort(), [...CHART_NAMES].sort());
});

test("isChartName accepts the five charts only", () => {
  for (const name of CHART_NAMES) assert.equal(isChartName(name), true);
  for (const name of ["telemetry", "", "Find-Mistakes", "find_mistakes", "constructor"]) assert.equal(isChartName(name), false);
});
