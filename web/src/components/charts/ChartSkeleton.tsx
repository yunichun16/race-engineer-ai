import type { CSSProperties } from "react";
import { CHART_SIZE, type ChartName } from "./chart-names";
import { chartSizeStyle } from "./chart-size";

export interface ChartSkeletonProps {
  name: ChartName;
  /** What is loading, for screen readers: "Finding the flagged corners…". */
  label: string;
  /**
   * The data, when it is here and only the chart's code is loading, so the skeleton takes the
   * room this data will. Without it, the room a typical real result takes.
   */
  data?: unknown;
}

/**
 * The chart's place while its data or code loads: the drawn chart's height at this width (so it
 * arrives without moving the page), hidden from screen readers except for the label, and still
 * when the reader prefers reduced motion. It is padded like the chart's own root (12 px 14 px
 * 8 px), so in a chart card the block sits where the chart will draw.
 */
export function ChartSkeleton({ name, label, data }: ChartSkeletonProps) {
  // Its own container, so the height follows the width the chart will draw at. The padding is
  // part of the measured height, so the block takes the rest.
  return (
    <div className="@container">
      <div className={`${CHART_SIZE[name]} px-3.5 pt-3 pb-2`} style={chartSizeStyle(name, data) as CSSProperties}>
        <div aria-hidden="true" className="shimmer h-[calc(var(--chart-h)_-_20px)] rounded-xl bg-hairline" />
        <span className="sr-only">{label}</span>
      </div>
    </div>
  );
}
