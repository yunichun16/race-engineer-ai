"use client";

import { linePath } from "@/charts/dom";
import { claimHref } from "@/content/claims";
import { FigureFrame, NumbersTable } from "./FigureFrame";
import { Num } from "./Num";
import { SHIFT_NOISE, SHIFT_SERIES, signedPercent } from "./figure-data";
import { useMarks } from "./useMarks";
import { BandKey, Legend, LegendItem, MARK_ROW, Marker, MarkerKey, Readout, linearPos, type Shape, type Tone } from "./parts";

const H = 200; // the plot's height in px
const Y_MAX = 0.8;
const Y_MIN = -1.6;
const Y_TICKS = [0.5, 0, -0.5, -1, -1.5];
const X_MAX = 8;
// The x axis is inset so the end markers and their focus rings stay inside the plot.
const xPos = (races: number) => 4 + linearPos(races, 0, X_MAX) * 0.92;
const yPx = (v: number) => ((Y_MAX - v) / (Y_MAX - Y_MIN)) * H;

const LOOK: Record<string, { shape: Shape; tone: Tone }> = {
  "2026": { shape: "circle", tone: "a" },
  control: { shape: "square", tone: "b" },
};

// One mark per point; the shared "no fine-tuning" point is drawn once, in ink.
const POINTS = [
  { series: null, row: SHIFT_SERIES[0].rows[0] },
  ...SHIFT_SERIES.flatMap((s) => s.rows.slice(1).map((row) => ({ series: s, row }))),
];

/** "Fine-tuned on 4 races of 2026", "Fine-tuned on 1 race of 2025 (the control)". */
function pointName(series: string, races: number): string {
  return `Fine-tuned on ${races} race${races === 1 ? "" : "s"} of ${series === "2026" ? "2026" : "2025 (the control)"}`;
}

const tickLabel = (v: number) => (v === 0 ? "0" : signedPercent(v));

/**
 * m3_shift.md's fine-tuning study: the change in reconstruction error on the held-out 2026 races
 * as the model is fine-tuned on more 2026 races (series A) or, as a control, on as many 2025
 * races (series B), against the shaded repeat-run noise. Lines are the chart core's monotone
 * curve (linePath), drawn in a stretched view box with a stroke that doesn't stretch.
 */
export function ShiftCurve() {
  const { active, markProps } = useMarks(POINTS.length);
  const noise = SHIFT_NOISE.est;

  const readout = (() => {
    if (active === null) return null;
    const { series, row } = POINTS[active];
    if (!series) return "No fine-tuning: the model trained on 2022-2024 only, the point every other one is measured against.";
    const inside = Math.abs(row.est) <= noise ? "within" : "beyond";
    return `${pointName(series.id, row.x ?? 0)}: ${signedPercent(row.est)} against no fine-tuning, ${inside} the repeat-run noise.`;
  })();

  return (
    <FigureFrame
      kicker="Fine-tuning on the new cars"
      title="More 2026 races barely move the error"
      caption={
        <>
          The change in reconstruction error on the held-out 2026 races, against the model with no fine-tuning, by the number of races
          fine-tuned on. 2026 races move it by <Num id="ftGain" /> with no steady trend; the control, 2025 races, by <Num id="ftControl" />.
          The band is the repeat-run noise: repeating the study moved single rows by up to <Num id="shiftNoise" />.
        </>
      }
      legend={
        <Legend>
          <LegendItem keyMark={<MarkerKey shape="diamond" tone="ink" />}>No fine-tuning</LegendItem>
          {SHIFT_SERIES.map((s) => (
            <LegendItem key={s.id} keyMark={<MarkerKey {...LOOK[s.id]} />}>
              {s.label}
            </LegendItem>
          ))}
          <LegendItem keyMark={<BandKey />}>Repeat-run noise</LegendItem>
        </Legend>
      }
      sources={[{ href: claimHref("shiftNone"), label: "report/m3_shift.md" }]}
      numbers={
        <NumbersTable
          label="change in reconstruction error against no fine-tuning, by races fine-tuned on"
          head={["Fine-tuned on", "Races", "Against none"]}
          numeric={[1, 2]}
          rows={POINTS.map(({ series, row }) => [
            series ? (series.id === "2026" ? "2026 races" : "2025 races (control)") : "Nothing",
            String(row.x),
            signedPercent(row.est),
          ])}
        />
      }
    >
      <div className="grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-1 pr-2">
        {/* The y axis: muted mono labels at the gridlines. */}
        <div className="relative font-mono text-xs text-muted tabular-nums" style={{ height: H }} aria-hidden="true">
          {Y_TICKS.map((v) => (
            <span key={v} className="absolute right-1 -translate-y-1/2" style={{ top: yPx(v) }}>
              {tickLabel(v)}
            </span>
          ))}
        </div>

        <div
          role="group"
          aria-label="Change in reconstruction error by races fine-tuned on. Use the arrow keys to move between the points."
          className="relative"
          style={{ height: H }}
        >
          <svg className="absolute inset-0 block h-full w-full overflow-visible" aria-hidden="true">
            <rect x={0} width="100%" y={yPx(noise)} height={yPx(-noise) - yPx(noise)} className="fill-muted/15" />
            {Y_TICKS.map((v) => (
              <line key={v} x1={0} x2="100%" y1={yPx(v)} y2={yPx(v)} className={v === 0 ? "stroke-line-strong" : "stroke-line"} strokeWidth={1} />
            ))}
          </svg>
          {/* The lines: x in 0-100 view-box units (the plot's width in percent), y in px. */}
          <svg className="absolute inset-0 block h-full w-full overflow-visible" viewBox={`0 0 100 ${H}`} preserveAspectRatio="none" aria-hidden="true">
            {SHIFT_SERIES.map((s) => (
              <path
                key={s.id}
                d={linePath(
                  s.rows.map((r) => xPos(r.x ?? 0)),
                  s.rows.map((r) => yPx(r.est)),
                )}
                className={`fill-none ${s.id === "2026" ? "stroke-series-a" : "stroke-series-b"}`}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
          <svg className="absolute inset-0 block h-full w-full overflow-visible" aria-hidden="true">
            {POINTS.map(({ series, row }, k) => (
              <Marker
                key={k}
                shape={series ? LOOK[series.id].shape : "diamond"}
                tone={series ? LOOK[series.id].tone : "ink"}
                x={`${xPos(row.x ?? 0)}%`}
                y={yPx(row.est)}
              />
            ))}
          </svg>
          {/* The focus targets: 28 px round hit areas over the markers, each a stop for the arrow keys. */}
          {POINTS.map(({ series, row }, k) => (
            <span
              key={k}
              role="img"
              aria-label={series ? pointName(series.id, row.x ?? 0) : "No fine-tuning"}
              className={`${MARK_ROW} absolute size-7 -translate-x-1/2 -translate-y-1/2 rounded-full! hover:bg-transparent data-[active=true]:bg-transparent data-[active=true]:ring-2 data-[active=true]:ring-line-strong`}
              style={{ left: `${xPos(row.x ?? 0)}%`, top: yPx(row.est) }}
              {...markProps(k)}
            />
          ))}
        </div>

        <div aria-hidden="true" />
        <div className="relative h-5 font-mono text-xs text-muted tabular-nums" aria-hidden="true">
          {SHIFT_SERIES[0].rows.map((r) => (
            <span key={r.x} className="absolute top-1 -translate-x-1/2" style={{ left: `${xPos(r.x ?? 0)}%` }}>
              {r.x}
            </span>
          ))}
        </div>
        <div aria-hidden="true" />
        <p className="mt-1.5 text-xs text-muted" aria-hidden="true">
          Races fine-tuned on
        </p>
      </div>
      <Readout text={readout} hint="Point at a marker, or Tab to the figure and use the arrow keys, to read it against the noise." />
    </FigureFrame>
  );
}
