// The saved tool results the gallery draws: every src/mcp-app/sample-*.json the site has a chart
// for (telemetry is MCP-only). Each is a lazy import, so its JSON is a chunk of its own that only
// this development page ever loads.
import type { ChartName } from "@/components/charts/chart-names";

/** A saved MCP tool result (engine/scripts/export_sample_result.py). */
export interface Fixture {
  arguments?: Record<string, unknown>;
  content: { type: string; text?: string }[];
  structuredContent?: unknown;
  isError?: boolean;
}

export interface FixtureEntry {
  name: string;
  chart: ChartName;
  load: () => Promise<{ default: unknown }>;
  /**
   * For an error result: how the REST API answers the same failure (api/errors.py), so the
   * gallery shows it the way the site would.
   */
  error?: { status: number; code: string; tool: string };
}

const invalidInput = (tool: string) => ({ status: 422, code: "invalid_input", tool });

export const FIXTURES: readonly FixtureEntry[] = [
  { name: "sample-find-mistakes", chart: "find-mistakes", load: () => import("@/mcp-app/sample-find-mistakes.json") },
  {
    name: "sample-find-mistakes-driver",
    chart: "find-mistakes",
    load: () => import("@/mcp-app/sample-find-mistakes-driver.json"),
  },
  {
    name: "sample-find-mistakes-empty",
    chart: "find-mistakes",
    load: () => import("@/mcp-app/sample-find-mistakes-empty.json"),
  },
  {
    name: "sample-find-mistakes-error",
    chart: "find-mistakes",
    load: () => import("@/mcp-app/sample-find-mistakes-error.json"),
    error: { status: 503, code: "results_unavailable", tool: "find_mistakes" },
  },
  { name: "sample-explain-corner", chart: "explain-corner", load: () => import("@/mcp-app/sample-explain-corner.json") },
  {
    name: "sample-explain-corner-traffic",
    chart: "explain-corner",
    load: () => import("@/mcp-app/sample-explain-corner-traffic.json"),
  },
  {
    name: "sample-explain-corner-noreplay",
    chart: "explain-corner",
    load: () => import("@/mcp-app/sample-explain-corner-noreplay.json"),
  },
  {
    name: "sample-explain-corner-error",
    chart: "explain-corner",
    load: () => import("@/mcp-app/sample-explain-corner-error.json"),
    error: invalidInput("explain_corner"),
  },
  { name: "sample-compare-styles", chart: "compare-styles", load: () => import("@/mcp-app/sample-compare-styles.json") },
  {
    name: "sample-compare-styles-rivals",
    chart: "compare-styles",
    load: () => import("@/mcp-app/sample-compare-styles-rivals.json"),
  },
  {
    name: "sample-compare-styles-sparse",
    chart: "compare-styles",
    load: () => import("@/mcp-app/sample-compare-styles-sparse.json"),
  },
  {
    name: "sample-compare-styles-error",
    chart: "compare-styles",
    load: () => import("@/mcp-app/sample-compare-styles-error.json"),
    error: invalidInput("compare_driving_styles"),
  },
  { name: "sample-race-summary", chart: "race-summary", load: () => import("@/mcp-app/sample-race-summary.json") },
  {
    name: "sample-race-summary-small",
    chart: "race-summary",
    load: () => import("@/mcp-app/sample-race-summary-small.json"),
  },
  {
    name: "sample-race-summary-error",
    chart: "race-summary",
    load: () => import("@/mcp-app/sample-race-summary-error.json"),
    error: invalidInput("get_race_summary"),
  },
  { name: "sample-compare-laps", chart: "compare-laps", load: () => import("@/mcp-app/sample-compare-laps.json") },
];

const loading = new Map<string, Promise<Fixture>>();

/** The fixture's contents, loaded once: the same promise every time, as React's `use` needs. */
export function fixturePromise(entry: FixtureEntry): Promise<Fixture> {
  let promise = loading.get(entry.name);
  if (!promise) {
    promise = entry.load().then((module) => module.default as Fixture);
    loading.set(entry.name, promise);
  }
  return promise;
}

/** The result's text, as Claude reads it: the chart's text version. */
export function fixtureText(fixture: Fixture): string {
  return fixture.content.find((block) => block.type === "text")?.text ?? "";
}
