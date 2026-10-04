"use client";

import { isComparison, render, type Comparison } from "@/charts/compare-laps";
import { ChartFrame } from "./ChartFrame";
import { ChartUndrawable } from "./ChartUndrawable";
import type { ChartHeadingLevel, UncheckedChartProps } from "./types";
import { useChartRoot } from "./useChartRoot";

export interface CompareLapsChartProps {
  data: Comparison;
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

/** Two drivers' laps compared corner by corner, drawn by charts/compare-laps.ts. */
export function CompareLapsChart({ data, label }: CompareLapsChartProps) {
  const ref = useChartRoot<Comparison, Kept>(
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
  return <ChartFrame ref={ref} name="compare-laps" label={label} data={data} />;
}

/** For the `Chart` dispatcher: checks the data before drawing it. */
export function UncheckedCompareLapsChart({ data, summary, label }: UncheckedChartProps) {
  if (!isComparison(data)) return <ChartUndrawable summary={summary} />;
  return <CompareLapsChart data={data} label={label} />;
}

export default CompareLapsChart;
