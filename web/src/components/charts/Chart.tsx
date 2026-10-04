"use client";

import dynamic from "next/dynamic";
import { createContext, useContext, type ComponentType } from "react";
import { ChartErrorBoundary } from "./ChartErrorBoundary";
import { ChartSkeleton } from "./ChartSkeleton";
import { isChartName, type ChartName } from "./chart-names";
import type { UncheckedChartProps } from "./types";

export interface ChartProps extends UncheckedChartProps {
  /** The API's name for the chart (a tool response's `chart.bundle`): "find-mistakes" and so on. */
  bundle: string;
}

// next/dynamic renders its `loading` component without the chart's props, so the skeleton
// learns which chart is loading, for what, and the data it will draw, from here.
const Pending = createContext<{ name: ChartName; label: string; data: unknown } | null>(null);

function ChartLoading() {
  const pending = useContext(Pending);
  return pending ? (
    <ChartSkeleton name={pending.name} label={`Loading the chart: ${pending.label}`} data={pending.data} />
  ) : null;
}

// One chunk per chart (its wrapper and its core), loaded the first time a page shows that chart.
const FindMistakes = dynamic(() => import("./FindMistakesChart").then((m) => m.UncheckedFindMistakesChart), {
  ssr: false,
  loading: ChartLoading,
});
const ExplainCorner = dynamic(() => import("./ExplainCornerChart").then((m) => m.UncheckedExplainCornerChart), {
  ssr: false,
  loading: ChartLoading,
});
const CompareStyles = dynamic(() => import("./CompareStylesChart").then((m) => m.UncheckedCompareStylesChart), {
  ssr: false,
  loading: ChartLoading,
});
const RaceSummary = dynamic(() => import("./RaceSummaryChart").then((m) => m.UncheckedRaceSummaryChart), {
  ssr: false,
  loading: ChartLoading,
});
const CompareLaps = dynamic(() => import("./CompareLapsChart").then((m) => m.UncheckedCompareLapsChart), {
  ssr: false,
  loading: ChartLoading,
});

const CHARTS: Record<ChartName, ComponentType<UncheckedChartProps>> = {
  "find-mistakes": FindMistakes,
  "explain-corner": ExplainCorner,
  "compare-styles": CompareStyles,
  "race-summary": RaceSummary,
  "compare-laps": CompareLaps,
};

/**
 * Any of the five charts by its bundle name, for data that arrives untyped (a chat answer, a
 * tool response). The chart's code loads on first use, behind a skeleton of its height. Data the
 * chart can't take shows "This chart couldn't be drawn" with the summary as text; a bundle the
 * site has no chart for shows the summary alone. The other props go to the chart's wrapper as
 * they are, `headingLevel` included (3 when missing; pass 4 inside a level-3 ChartCard).
 */
export function Chart({ bundle, ...props }: ChartProps) {
  if (!isChartName(bundle)) {
    // Padded like a chart's root, since a chart card's chart area has none.
    return props.summary ? <p className="px-4 pt-3 pb-4 text-sm whitespace-pre-wrap text-fg">{props.summary}</p> : null;
  }
  const Drawn = CHARTS[bundle];
  return (
    <ChartErrorBoundary summary={props.summary}>
      <Pending value={{ name: bundle, label: props.label, data: props.data }}>
        <Drawn {...props} />
      </Pending>
    </ChartErrorBoundary>
  );
}
