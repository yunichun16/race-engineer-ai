"use client";

import { useLayoutEffect, useRef } from "react";
import { isStyleChart, render, type Plane, type StyleChart, type StyleKind } from "@/charts/compare-styles";
import { ChartFrame } from "./ChartFrame";
import { ChartUndrawable } from "./ChartUndrawable";
import type { ChartHeadingLevel, UncheckedChartProps } from "./types";
import { useChartRoot } from "./useChartRoot";

export interface CompareStylesChartProps {
  data: StyleChart;
  /**
   * The sessions and map plane to start from, read when new data arrives. Later values never
   * redraw, so a page can keep them in its URL (from `onKindChange` and `onPlaneChange`) without
   * the chart losing focus.
   */
  initialKind?: StyleKind;
  initialPlane?: Plane;
  onKindChange?: (kind: StyleKind) => void;
  onPlaneChange?: (plane: Plane) => void;
  /**
   * A style-map point picked by click, Enter or Space (never driver A). With it the map says
   * "Select a driver to compare them with {A}". Whether it is given is read when the chart
   * draws, so give it from the start.
   */
  onPointSelect?: (driver: string) => void;
  /**
   * The level of the two section heads ("Differences by corner type", "Style map"): 3 when
   * missing, under a ChartCard's h2 title; pass 4 inside a level-3 ChartCard. Read when the chart
   * draws.
   */
  headingLevel?: ChartHeadingLevel;
  label: string;
}

interface Kept {
  kind: StyleKind | undefined;
  plane: Plane | undefined;
  tableOpen: boolean;
}

/** Two drivers' corner-taking compared over a season, drawn by charts/compare-styles.ts. */
export function CompareStylesChart(props: CompareStylesChartProps) {
  const { data, initialKind, initialPlane, headingLevel = 3, label } = props;
  const latest = useRef(props);
  useLayoutEffect(() => {
    latest.current = props;
  });
  const selects = props.onPointSelect !== undefined;

  const ref = useChartRoot<StyleChart, Kept>(
    data,
    () => ({ kind: initialKind, plane: initialPlane, tableOpen: false }),
    (root, d, k) =>
      render(root, d, {
        kind: k.kind,
        onKindChange: (kind) => {
          k.kind = kind;
          latest.current.onKindChange?.(kind);
        },
        plane: k.plane,
        onPlaneChange: (plane) => {
          k.plane = plane;
          latest.current.onPlaneChange?.(plane);
        },
        tableOpen: k.tableOpen,
        onTableToggle: (open) => {
          k.tableOpen = open;
        },
        onPointSelect: selects
          ? (driver) => {
              latest.current.onPointSelect?.(driver);
            }
          : undefined,
        headingLevel,
      }),
  );
  return <ChartFrame ref={ref} name="compare-styles" label={label} data={data} />;
}

/** For the `Chart` dispatcher: checks the data before drawing it. */
export function UncheckedCompareStylesChart({
  data,
  summary,
  label,
  initialKind,
  initialPlane,
  onKindChange,
  onPlaneChange,
  onPointSelect,
  headingLevel,
}: UncheckedChartProps) {
  if (!isStyleChart(data)) return <ChartUndrawable summary={summary} />;
  return (
    <CompareStylesChart
      data={data}
      label={label}
      initialKind={initialKind}
      initialPlane={initialPlane}
      onKindChange={onKindChange}
      onPlaneChange={onPlaneChange}
      onPointSelect={onPointSelect}
      headingLevel={headingLevel}
    />
  );
}

export default CompareStylesChart;
