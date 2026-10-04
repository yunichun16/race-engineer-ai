"use client";

import { useEffect, useLayoutEffect, useRef } from "react";
import { isMistakeList, markSelected, render, type Mistake, type MistakeList, type ViewState } from "@/charts/find-mistakes";
import { ChartFrame } from "./ChartFrame";
import { ChartUndrawable } from "./ChartUndrawable";
import type { ChartHeadingLevel, MistakeSelection, UncheckedChartProps } from "./types";
import { useChartRoot } from "./useChartRoot";

export interface FindMistakesChartProps {
  data: MistakeList;
  /**
   * "Show telemetry" on a row. It returns nothing: the chart then shows no "Asking…" state, which
   * belongs to asking Claude inside the conversation. Whether it is given is read when the chart
   * draws, so give it from the start.
   */
  onExplain?: (mistake: Mistake) => void;
  /** The row whose corner is open: marked without redrawing, so focus stays where it is. */
  selected?: MistakeSelection | null;
  /**
   * The level of the list's heading: 3 when missing, under a ChartCard's h2 title; pass 4 inside a
   * level-3 ChartCard. Read when the chart draws.
   */
  headingLevel?: ChartHeadingLevel;
  label: string;
}

interface Kept {
  view: ViewState | undefined; // the turn filter and the open explanations
}

/** The mistake list and track map, drawn by charts/find-mistakes.ts. */
export function FindMistakesChart({
  data,
  onExplain,
  selected = null,
  headingLevel = 3,
  label,
}: FindMistakesChartProps) {
  const latest = useRef(onExplain);
  useLayoutEffect(() => {
    latest.current = onExplain;
  });
  const explains = onExplain !== undefined;

  const ref = useChartRoot<MistakeList, Kept>(
    data,
    () => ({ view: undefined }),
    (root, d, k) => {
      render(root, d, {
        onExplain: explains
          ? (mistake) => {
              latest.current?.(mistake);
            }
          : undefined,
        view: k.view,
        onViewChange: (view) => {
          k.view = view;
        },
        explainLabel: "Show telemetry",
        headingLevel,
      });
      markSelected(root, selected); // render rebuilt the rows
    },
  );

  useEffect(() => {
    const root = ref.current;
    if (root) markSelected(root, selected);
  }, [ref, selected]);

  return <ChartFrame ref={ref} name="find-mistakes" label={label} data={data} />;
}

/** For the `Chart` dispatcher: checks the data before drawing it. */
export function UncheckedFindMistakesChart({
  data,
  summary,
  label,
  onExplain,
  selected,
  headingLevel,
}: UncheckedChartProps) {
  if (!isMistakeList(data)) return <ChartUndrawable summary={summary} />;
  return (
    <FindMistakesChart
      data={data}
      label={label}
      onExplain={onExplain}
      selected={selected}
      headingLevel={headingLevel}
    />
  );
}

export default FindMistakesChart;
