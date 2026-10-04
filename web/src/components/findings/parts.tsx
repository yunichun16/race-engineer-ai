import type { ReactNode } from "react";

/**
 * Small pieces the findings figures share: the marker shapes, legend keys, the axis labels and
 * the readout. Colours are the charts' series tokens (--a for the Transformer, --b for the best
 * baseline) and muted ink for the rest; every series also has its own shape, so nothing is told
 * by colour alone. Text is always ink or muted, never a series colour.
 */

export type Shape = "circle" | "square" | "diamond";
export type Tone = "a" | "b" | "muted" | "ink";

const FILL: Record<Tone, string> = {
  a: "fill-series-a",
  b: "fill-series-b",
  muted: "fill-(--color-background-primary)",
  ink: "fill-fg",
};
const STROKE: Record<Tone, string> = {
  a: "stroke-series-a",
  b: "stroke-series-b",
  muted: "stroke-muted",
  ink: "stroke-fg",
};

/** A marker's path, centred on 0,0, about 11 px across (dataviz: markers at least 8 px). */
function shapePath(shape: Shape): string {
  if (shape === "square") return "M-5,-5H5V5H-5Z";
  if (shape === "diamond") return "M0,-6.5L6.5,0L0,6.5L-6.5,0Z";
  return "M-5.5,0a5.5,5.5 0 1,0 11,0a5.5,5.5 0 1,0 -11,0Z";
}

/**
 * A marker for use inside an <svg>, centred at (x, y). The muted tone is hollow (a 2 px muted
 * outline on the opaque surface); the others are filled and wear a 2 px surface ring, so they
 * stay legible where they cross a line.
 */
export function Marker({ shape, tone, x, y }: { shape: Shape; tone: Tone; x: string | number; y: string | number }) {
  const hollow = tone === "muted";
  return (
    <svg x={x} y={y} overflow="visible" aria-hidden="true">
      <path
        d={shapePath(shape)}
        className={hollow ? `${FILL.muted} ${STROKE.muted}` : `${FILL[tone]} stroke-(--color-background-primary)`}
        strokeWidth={2}
        paintOrder="stroke"
      />
    </svg>
  );
}

/** The same marker as a small inline picture, for legends and table keys. */
export function MarkerKey({ shape, tone }: { shape: Shape; tone: Tone }) {
  return (
    <svg viewBox="-8 -8 16 16" className="size-4 shrink-0" aria-hidden="true">
      <Marker shape={shape} tone={tone} x={0} y={0} />
    </svg>
  );
}

/** A line key for a legend: solid, or dashed for a reference line (chance, a base rate). */
export function LineKey({ tone, dashed = false }: { tone: Tone; dashed?: boolean }) {
  return (
    <svg viewBox="0 0 20 10" className="h-2.5 w-5 shrink-0" aria-hidden="true">
      <line x1={1} x2={19} y1={5} y2={5} className={STROKE[tone]} strokeWidth={2} strokeDasharray={dashed ? "3 3" : undefined} strokeLinecap="round" />
    </svg>
  );
}

/** A band key: the muted wash a figure uses for a noise band. */
export function BandKey() {
  return <span aria-hidden="true" className="inline-block h-2.5 w-5 shrink-0 rounded-sm bg-muted/20" />;
}

export function Legend({ children }: { children: ReactNode }) {
  return <ul className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm text-fg">{children}</ul>;
}

export function LegendItem({ keyMark, children }: { keyMark: ReactNode; children: ReactNode }) {
  return (
    <li className="inline-flex items-center gap-2">
      {keyMark}
      <span>{children}</span>
    </li>
  );
}

/** Where a value sits along an axis, as a percentage of its width (for SVG x attributes and CSS left). */
export function linearPos(v: number, lo: number, hi: number): number {
  return ((v - lo) / (hi - lo)) * 100;
}

export function logPos(v: number, lo: number, hi: number): number {
  return ((Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * 100;
}

/**
 * Tick labels under a strip, placed by percentage: the first and last sit flush with the ends,
 * so nothing spills past the figure at 360 px. Muted mono, tabular.
 */
export function AxisTicks({ ticks, title }: { ticks: readonly { at: number; label: string }[]; title?: ReactNode }) {
  return (
    <div aria-hidden="true">
      <div className="relative h-5 font-mono text-xs text-muted tabular-nums">
        {ticks.map((t, k) => (
          <span
            key={k}
            className={
              "absolute top-0.5 whitespace-nowrap " +
              (t.at <= 1 ? "" : t.at >= 99 ? "-translate-x-full" : "-translate-x-1/2")
            }
            style={{ left: `${t.at}%` }}
          >
            {t.label}
          </span>
        ))}
      </div>
      {title ? <p className="mt-1 text-xs text-muted">{title}</p> : null}
    </div>
  );
}

/** Vertical gridlines for a strip, at the tick positions. */
export function Gridlines({ at }: { at: readonly number[] }) {
  return (
    <>
      {at.map((x, k) => (
        <line key={k} x1={`${x}%`} x2={`${x}%`} y1={0} y2="100%" className="stroke-line" strokeWidth={1} />
      ))}
    </>
  );
}

/**
 * What the figure says about the mark being read. The text goes in a polite live region (so a
 * keyboard reader hears each value as they move); the hint beside it is not live, so leaving the
 * figure announces nothing. It reserves two lines, so the panel doesn't jump.
 */
export function Readout({ text, hint }: { text: string | null; hint: string }) {
  return (
    <div className="mt-3 min-h-[3.2em] px-2 text-sm/[1.6]">
      <p aria-live="polite" className="font-medium text-fg">
        {text ?? ""}
      </p>
      {text ? null : <p className="text-muted">{hint}</p>}
    </div>
  );
}

/**
 * A readable mark (a figure row, or the hit area over a marker): a light wash while it is read,
 * and the site's focus ring (globals.css) when a keyboard reaches it, never on a click.
 */
export const MARK_ROW =
  "rounded-xl transition-[background-color] duration-(--dur-hover) hover:bg-hairline data-[active=true]:bg-hairline focus-visible:outline-offset-0";
