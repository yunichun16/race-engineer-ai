import type { CSSProperties, Ref } from "react";
import { CHART_SIZE, type ChartName } from "./chart-names";
import { chartSizeStyle } from "./chart-size";

export interface ChartFrameProps {
  name: ChartName;
  /** The chart's accessible name: "Flagged mistakes in the 2026 Azerbaijan Grand Prix race". */
  label: string;
  ref?: Ref<HTMLDivElement>;
  /** The data about to be drawn, so the empty root keeps the room this data will take. */
  data?: unknown;
}

/**
 * The markup every chart draws into (charts/styles/base.css): an unpadded frame, which is the
 * container the charts' breakpoints measure, around the padded root the chart owns. Until the
 * chart draws, the empty root keeps the height its data will take, so nothing below it moves. In a frame
 * narrower than a chart's floor (about 328 px) the drawing shrinks to fit (its svg is 100% wide);
 * anything else wider than the frame scrolls sideways inside it, never the page.
 */
export function ChartFrame({ name, label, ref, data }: ChartFrameProps) {
  // The root is content-box, so its min-height leaves out its 20 px of padding (12 + 8), which
  // the measured height includes.
  return (
    <div className="re-chart-frame overflow-x-auto">
      <div
        ref={ref}
        className={`re-chart re-chart--${name} ${CHART_SIZE[name]} empty:min-h-[calc(var(--chart-h)_-_20px)]`}
        style={chartSizeStyle(name, data) as CSSProperties}
        role="group"
        aria-label={label}
      />
    </div>
  );
}
