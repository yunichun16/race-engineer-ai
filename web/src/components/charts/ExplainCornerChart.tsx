"use client";

import { isCornerChart, render, unmount, type CornerChart, type ReplayState } from "@/charts/explain-corner";
import { ChartFrame } from "./ChartFrame";
import { ChartUndrawable } from "./ChartUndrawable";
import type { ChartHeadingLevel, UncheckedChartProps } from "./types";
import { useChartRoot } from "./useChartRoot";

export interface ExplainCornerChartProps {
  data: CornerChart;
  /**
   * Play the replay once when it first comes into view (never with reduced motion). Read when
   * new data arrives. False when missing.
   */
  autoplay?: boolean;
  /**
   * The level of the corner's heading: 3 when missing, under a ChartCard's h2 title; pass 4 inside
   * a level-3 ChartCard. Read when the chart draws.
   */
  headingLevel?: ChartHeadingLevel;
  label: string;
}

interface Kept {
  tableOpen: boolean;
  replay: Partial<ReplayState>; // frame, speed, view and play state
  // The offer to play once by itself: a redraw before it plays (a new width) keeps it open,
  // and it lapses once the replay has played or been moved.
  autoplay: boolean;
}

/**
 * One corner on one lap against the driver's usual, with the corner map and replay, drawn by
 * charts/explain-corner.ts. P never plays it from elsewhere on the page (`pageKeys: false`),
 * because a page may hold several; Space and the arrow keys work while the replay has focus.
 */
export function ExplainCornerChart({ data, autoplay = false, headingLevel = 3, label }: ExplainCornerChartProps) {
  const ref = useChartRoot<CornerChart, Kept>(
    data,
    () => ({ tableOpen: false, replay: {}, autoplay }),
    (root, d, k) =>
      render(root, d, {
        tableOpen: k.tableOpen,
        onTableToggle: (open) => {
          k.tableOpen = open;
        },
        replay: k.replay,
        onReplayChange: (state) => {
          k.replay = state;
          if (state.playing || state.frame > 0) k.autoplay = false;
        },
        autoplay: k.autoplay,
        pageKeys: false,
        headingLevel,
      }),
    unmount,
  );
  return <ChartFrame ref={ref} name="explain-corner" label={label} data={data} />;
}

/** For the `Chart` dispatcher: checks the data before drawing it. */
export function UncheckedExplainCornerChart({ data, summary, label, autoplay, headingLevel }: UncheckedChartProps) {
  if (!isCornerChart(data)) return <ChartUndrawable summary={summary} />;
  return <ExplainCornerChart data={data} label={label} autoplay={autoplay} headingLevel={headingLevel} />;
}

export default ExplainCornerChart;
