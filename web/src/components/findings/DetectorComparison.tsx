"use client";

import { useState } from "react";
import { Segmented } from "@/components/ui/Segmented";
import { claimHref } from "@/content/claims";
import { FigureFrame, NumbersTable } from "./FigureFrame";
import { Num, numText } from "./Num";
import {
  DETECTORS,
  DETECTOR_DATA,
  threeDecimals as f3,
  type DetectorRole,
  type EventSet,
  type Metric,
} from "./figure-data";
import { useMarks } from "./useMarks";
import {
  AxisTicks,
  Gridlines,
  Legend,
  LegendItem,
  MARK_ROW,
  Marker,
  MarkerKey,
  Readout,
  linearPos,
  logPos,
  type Shape,
  type Tone,
} from "./parts";

const LOOK: Record<DetectorRole, { shape: Shape; tone: Tone }> = {
  transformer: { shape: "circle", tone: "a" },
  baseline: { shape: "square", tone: "b" },
  other: { shape: "diamond", tone: "muted" },
};

const EVENT_LABEL: Record<EventSet, string> = { all: "all events, 2022-2026", test: "the 2026 test events" };

// AUROC on a linear axis from a coin flip to perfect; average precision on a log axis, because
// the estimates sit near the base rate while the test-event intervals reach ten times higher.
const AXES: Record<Metric, { name: string; inText: string; pos: (v: number) => number; ticks: number[]; tickLabel: (v: number) => string }> = {
  auroc: {
    name: "AUROC",
    inText: "AUROC",
    pos: (v) => linearPos(v, 0.5, 1),
    ticks: [0.5, 0.6, 0.7, 0.8, 0.9, 1],
    tickLabel: (v) => v.toFixed(1),
  },
  ap: {
    name: "Average precision",
    inText: "average precision",
    pos: (v) => logPos(v, 0.0008, 0.12),
    ticks: [0.001, 0.01, 0.1],
    tickLabel: (v) => String(v),
  },
};

const METRICS: readonly Metric[] = ["auroc", "ap"];
const BASE_RATE = 0.001; // the claim apBaseRate, "about 0.001", drawn as the dashed line

/**
 * The five mistake detectors of m3_results.md side by side: AUROC (how well a score ranks the
 * labelled mistakes above other corners) and average precision (how many labelled mistakes sit
 * at the top of its list), each with its interval, on all events or on the 2026 test events.
 * The Transformer's chosen score is series A, Isolation Forest (the best baseline) series B.
 */
export function DetectorComparison() {
  const [events, setEvents] = useState<EventSet>("all");
  const { active, markProps } = useMarks(DETECTORS.length * METRICS.length);
  const ci = numText("ciLevel");

  const readout = (() => {
    if (active === null) return null;
    const metric = METRICS[Math.floor(active / DETECTORS.length)];
    const row = DETECTOR_DATA[events][metric][active % DETECTORS.length];
    return `${row.label}: ${AXES[metric].inText} ${f3(row.est)}, ${ci} interval ${f3(row.lo ?? row.est)} to ${f3(row.hi ?? row.est)}, on ${EVENT_LABEL[events]}.`;
  })();

  return (
    <FigureFrame
      kicker="Mistake detection, five scores"
      title="The baseline ranks better overall; the Transformer is sharper at the top"
      caption={
        <>
          Each mark is a score&apos;s estimate against the labelled mistakes (track-limits violations named by race control), and its
          line the <Num id="ciLevel" /> interval from resampling whole events. The intervals are wide because labels are sparse.
        </>
      }
      controls={
        <Segmented<EventSet>
          legend="Events"
          name="detector-events"
          value={events}
          onChange={setEvents}
          options={[
            { value: "all", label: "All events" },
            { value: "test", label: "2026 test events" },
          ]}
        />
      }
      legend={
        <Legend>
          <LegendItem keyMark={<MarkerKey shape="circle" tone="a" />}>Transformer, the score kept</LegendItem>
          <LegendItem keyMark={<MarkerKey shape="square" tone="b" />}>Isolation Forest, the best baseline</LegendItem>
          <LegendItem keyMark={<MarkerKey shape="diamond" tone="muted" />}>Other scores</LegendItem>
        </Legend>
      }
      sources={[{ href: claimHref(events === "all" ? "detAllExit" : "detTestExit"), label: "report/m3_results.md" }]}
      numbers={
        <NumbersTable
          label={`the five detectors on all events and on the 2026 test events, with ${ci} intervals`}
          head={["Detector", "Events", `AUROC [${ci} CI]`, `Average precision [${ci} CI]`]}
          numeric={[2, 3]}
          rows={(["all", "test"] as const).flatMap((set) =>
            DETECTORS.map((d, k) => {
              const a = DETECTOR_DATA[set].auroc[k];
              const p = DETECTOR_DATA[set].ap[k];
              return [
                <span key="d" className="grid grid-cols-[1rem_minmax(0,1fr)] items-start gap-x-2">
                  <span className="pt-1">
                    <MarkerKey {...LOOK[d.role]} />
                  </span>
                  <span>
                    {d.label}
                    <span className="block font-mono text-[13px] text-muted">{d.name}</span>
                  </span>
                </span>,
                set === "all" ? "All" : "2026 test",
                `${f3(a.est)} [${f3(a.lo ?? a.est)}, ${f3(a.hi ?? a.est)}]`,
                `${f3(p.est)} [${f3(p.lo ?? p.est)}, ${f3(p.hi ?? p.est)}]`,
              ];
            }),
          )}
        />
      }
    >
      <div
        role="group"
        aria-label={`Five mistake detectors on ${EVENT_LABEL[events]}: AUROC and average precision. Use the arrow keys to move between them.`}
        className="grid gap-x-8 gap-y-6 lg:grid-cols-2"
      >
        {METRICS.map((metric, m) => {
          const axis = AXES[metric];
          const ticks = axis.ticks.map((t) => axis.pos(t));
          return (
            <div key={metric} className="min-w-0">
              <p className="px-2 text-sm font-semibold text-fg">{axis.name}</p>
              <p className="px-2 text-xs/[1.5] text-muted">
                {metric === "auroc" ? (
                  "How well a score ranks labelled mistakes above other corners. The left edge is a coin flip."
                ) : (
                  <>
                    How many labelled mistakes sit at the top of its list, on a log scale. The dashed line is the base rate, <Num id="apBaseRate" />.
                  </>
                )}
              </p>
              <div className="mt-2 grid">
                {DETECTORS.map((d, k) => {
                  const row = DETECTOR_DATA[events][metric][k];
                  const look = LOOK[d.role];
                  const lineTone = look.tone === "a" ? "stroke-series-a" : look.tone === "b" ? "stroke-series-b" : "stroke-muted";
                  return (
                    <div key={d.id} role="img" aria-label={`${d.label}, ${axis.name}`} className={`${MARK_ROW} px-2 pt-1.5`} {...markProps(m * DETECTORS.length + k)}>
                      <div className="flex items-baseline justify-between gap-3 text-sm/[1.4]" aria-hidden="true">
                        <span className={d.role === "other" ? "text-muted" : "font-medium text-fg"}>{d.label}</span>
                        <span className="font-mono text-[13px] text-fg tabular-nums">{f3(row.est)}</span>
                      </div>
                      <svg className="block h-7 w-full overflow-visible" aria-hidden="true">
                        <Gridlines at={ticks} />
                        {metric === "ap" ? (
                          <line x1={`${axis.pos(BASE_RATE)}%`} x2={`${axis.pos(BASE_RATE)}%`} y1={0} y2="100%" className="stroke-muted" strokeWidth={1.5} strokeDasharray="3 3" />
                        ) : null}
                        <line
                          x1={`${axis.pos(row.lo ?? row.est)}%`}
                          x2={`${axis.pos(row.hi ?? row.est)}%`}
                          y1="50%"
                          y2="50%"
                          className={lineTone}
                          strokeWidth={2}
                          strokeLinecap="round"
                        />
                        <Marker shape={look.shape} tone={look.tone} x={`${axis.pos(row.est)}%`} y="50%" />
                      </svg>
                    </div>
                  );
                })}
              </div>
              <div className="px-2">
                <AxisTicks ticks={axis.ticks.map((t, k) => ({ at: ticks[k], label: axis.tickLabel(t) }))} />
              </div>
            </div>
          );
        })}
      </div>
      <Readout text={readout} hint="Point at a row, or Tab to the figure and use the arrow keys, to read its interval." />
    </FigureFrame>
  );
}
