// Props shared by the chart wrappers and the `Chart` dispatcher. Type-only imports from the chart
// core, so importing this file never pulls a chart's code into a page.
import type { Plane, StyleKind } from "@/charts/compare-styles";
import type { Mistake } from "@/charts/find-mistakes";

/**
 * The level of a chart's own top heading on the website: one below the heading the chart sits
 * under (the page's h1 is never a chart's). explain-corner's and find-mistakes' title,
 * compare-styles' two section heads; race-summary and compare-laps draw no heading.
 */
export type ChartHeadingLevel = 2 | 3 | 4 | 5 | 6;

/** The mistake whose corner is open (find-mistakes marks its row). */
export interface MistakeSelection {
  driver: string;
  lap_number: number;
  turn: string;
}

/** The wrappers' own props, which the dispatcher passes on to the chart they belong to. */
export interface ChartPassThrough {
  /** find-mistakes: "Show telemetry" on a row. Without it the rows have no button. */
  onExplain?: (mistake: Mistake) => void;
  /** find-mistakes: the row to mark as open. */
  selected?: MistakeSelection | null;
  /** explain-corner: play the replay once when it first comes into view. */
  autoplay?: boolean;
  /** compare-styles: the sessions and the map plane to start from. */
  initialKind?: StyleKind;
  initialPlane?: Plane;
  onKindChange?: (kind: StyleKind) => void;
  onPlaneChange?: (plane: Plane) => void;
  /** compare-styles: a style-map point picked (never driver A). */
  onPointSelect?: (driver: string) => void;
  /**
   * Every chart: the level of its own top heading, 3 when missing (under a ChartCard's h2 title).
   * Inside a level-3 ChartCard pass 4.
   */
  headingLevel?: ChartHeadingLevel;
}

/** What a lazily loaded chart gets: data still to be checked, and the tool's summary. */
export interface UncheckedChartProps extends ChartPassThrough {
  data: unknown;
  summary?: string;
  label: string;
}
