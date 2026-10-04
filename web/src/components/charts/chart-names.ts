// The five charts the site draws, by the bundle names the API gives them (a tool response's
// `chart.bundle`, from engine/src/race_engineer/tools/definitions.py), and the classes that
// keep each one's room before it is drawn (the heights are in chart-size.ts).

export const CHART_NAMES = ["find-mistakes", "explain-corner", "compare-styles", "race-summary", "compare-laps"] as const;

export type ChartName = (typeof CHART_NAMES)[number];

export function isChartName(value: string): value is ChartName {
  return (CHART_NAMES as readonly string[]).includes(value);
}

/**
 * The room each chart takes before it is drawn, as Tailwind classes that set `--chart-h` from
 * the heights in `chartSizeStyle(name, data)` (chart-size.ts), one per layout: `--chart-h0`
 * below the chart's first breakpoint, then `--chart-h1`, `--chart-h2` from each `@min-[…]` step.
 * Put both on the same element (ChartFrame's empty root, ChartSkeleton's block). The `@min-[…]`
 * variants query the nearest container: the chart frame for the empty root, ChartSkeleton's own
 * wrapper for the skeleton, so both follow the width the chart will have. The steps match
 * SIZE_BREAKS (a test checks it); the class strings are written out in full so Tailwind finds
 * them.
 */
export const CHART_SIZE: Record<ChartName, string> = {
  "find-mistakes": "[--chart-h:var(--chart-h0)] @min-[640px]:[--chart-h:var(--chart-h1)]",
  "explain-corner": "[--chart-h:var(--chart-h0)] @min-[561px]:[--chart-h:var(--chart-h1)]",
  "compare-styles": "[--chart-h:var(--chart-h0)] @min-[481px]:[--chart-h:var(--chart-h1)]",
  "race-summary":
    "[--chart-h:var(--chart-h0)] @min-[420px]:[--chart-h:var(--chart-h1)] @min-[481px]:[--chart-h:var(--chart-h2)]",
  "compare-laps": "[--chart-h:var(--chart-h0)] @min-[481px]:[--chart-h:var(--chart-h1)]",
};
