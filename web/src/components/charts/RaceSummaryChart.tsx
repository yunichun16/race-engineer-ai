"use client";

import { isRaceSummary, render, type RaceSummary } from "@/charts/race-summary";
import { ChartFrame } from "./ChartFrame";
import { ChartUndrawable } from "./ChartUndrawable";
import type { ChartHeadingLevel, UncheckedChartProps } from "./types";
import { useChartRoot } from "./useChartRoot";

export interface RaceSummaryChartProps {
  data: RaceSummary;
  /**
   * Taken like every chart wrapper's, so a page can pass it whichever chart it shows, but unused:
   * this chart draws no heading.
   */
  headingLevel?: ChartHeadingLevel;
  label: string;
}

interface Kept {
  tableOpen: boolean;
}

/** A race or sprint's positions, safety cars and pit stops, drawn by charts/race-summary.ts. */
export function RaceSummaryChart({ data, label }: RaceSummaryChartProps) {
  const ref = useChartRoot<RaceSummary, Kept>(
    data,
    () => ({ tableOpen: false }),
    (root, d, k) =>
      render(root, d, {
        tableOpen: k.tableOpen,
        onTableToggle: (open) => {
          k.tableOpen = open;
        },
      }),
  );
  return <ChartFrame ref={ref} name="race-summary" label={label} data={data} />;
}

/** For the `Chart` dispatcher: checks the data before drawing it. */
export function UncheckedRaceSummaryChart({ data, summary, label }: UncheckedChartProps) {
  if (!isRaceSummary(data)) return <ChartUndrawable summary={summary} />;
  return <RaceSummaryChart data={data} label={label} />;
}

export default RaceSummaryChart;
