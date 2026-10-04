import { linePath, monotonePath } from "@/charts/dom";
import type { ReactNode } from "react";
import { Num } from "./Num";
import { BandKey, Legend, LegendItem, LineKey } from "./parts";

/**
 * "The method" in five plain steps, with one drawing of the idea at its heart: hide a stretch of
 * a corner, let the model predict it, and score how far the lap strays from the prediction. The
 * drawing is an illustration (constant arrays, labelled as such), static SVG with no text inside
 * it, so it reads the same at 360 px; the legend and the description are HTML.
 */

// Distance from the apex in metres, every 10 m from braking to the exit, and a made-up speed trace.
const D = Array.from({ length: 32 }, (_, i) => -160 + i * 10);
const smooth = (t: number) => t * t * (3 - 2 * t);
// Flat at the apex from both sides, so the slowest point is a smooth minimum.
const usual = (d: number) => (d < 0 ? 105 + 145 * smooth(Math.min(1, -d / 150)) : 105 + 110 * Math.pow(d / 150, 1.6));
const lap = (d: number) => usual(d) - 26 * Math.exp(-(((d - 35) / 40) ** 2));
const HIDDEN: [number, number] = [-40, 90];
const BAND = 8;

const W = 640;
const H = 220;
const x = (d: number) => 10 + ((d + 160) / 310) * (W - 20);
const y = (v: number) => 12 + ((260 - v) / 185) * (H - 24);

const inWindow = D.filter((d) => d >= HIDDEN[0] && d <= HIDDEN[1]);
const xs = inWindow.map(x);
const hi = inWindow.map((d) => y(usual(d) + BAND));
const lo = inWindow.map((d) => y(usual(d) - BAND));
// The band, composed from the chart core's curve exactly as explain-corner builds its own.
const bandPath = `${monotonePath(xs, hi)}${monotonePath([...xs].reverse(), [...lo].reverse(), "L")}Z`;
const predictionPath = linePath(xs, inWindow.map((d) => y(usual(d))));
const lapPath = linePath(D.map(x), D.map((d) => y(lap(d))));

function Illustration() {
  return (
    <figure className="glass-panel -mx-2 overflow-hidden rounded-card sm:mx-0">
      <div className="px-3 pt-4 sm:px-5 sm:pt-5">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="block h-auto w-full"
          role="img"
          aria-label="Illustration: a speed trace through a corner. A stretch around the apex is hidden from the model, which predicts it as a dashed line inside a band. The lap dips below the band after the apex and recovers late, so the corner scores as unusual."
        >
          <rect x={x(HIDDEN[0])} width={x(HIDDEN[1]) - x(HIDDEN[0])} y={0} height={H} className="fill-muted/10" />
          {HIDDEN.map((d) => (
            <line key={d} x1={x(d)} x2={x(d)} y1={0} y2={H} className="stroke-line-strong" strokeWidth={1} strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
          ))}
          <line x1={x(0)} x2={x(0)} y1={H - 22} y2={H} className="stroke-muted" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
          <path d={bandPath} className="fill-series-a/15" />
          <path d={predictionPath} className="fill-none stroke-series-a" strokeWidth={2} strokeDasharray="6 5" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          <path d={lapPath} className="fill-none stroke-fg" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
      <figcaption className="grid gap-2.5 px-4 pt-3 pb-4 sm:px-6">
        <Legend>
          <LegendItem keyMark={<LineKey tone="ink" />}>The lap</LegendItem>
          <LegendItem keyMark={<LineKey tone="a" dashed />}>The model&apos;s prediction, with its band</LegendItem>
          <LegendItem keyMark={<BandKey />}>Hidden from the model</LegendItem>
        </Legend>
        <p className="text-sm/[1.6] text-muted">
          Illustration, not real data. Speed through one corner, braking on the left and accelerating on the right, with the apex
          ticked at the foot. Where the lap leaves the prediction after the apex, the corner scores as unusual.
        </p>
      </figcaption>
    </figure>
  );
}

const STEPS: readonly { title: string; body: ReactNode }[] = [
  {
    title: "Cut every lap into corners",
    body: (
      <>
        Each lap is resampled every <Num id="grid" link /> and cut into windows from <Num id="window" link /> each apex: <Num id="segments" link />{" "}
        corner segments.
      </>
    ),
  },
  {
    title: "Hide a stretch and ask for it back",
    body: "The Telemetry Transformer is a masked autoencoder. It sees a corner with part of it hidden and predicts the hidden part from the rest, so it learns how corners are usually taken. It never sees a label.",
  },
  {
    title: "Score the miss, get a second opinion",
    body: "How far the lap strays from the prediction after the apex is the Transformer's score. Isolation Forest, a simple baseline on hand-crafted features, scores the same corner, and the two are averaged.",
  },
  {
    title: "Flag the most unusual corners",
    body: (
      <>
        A corner is flagged when the combined score is in its session&apos;s <Num id="flagShare" link />: <Num id="flags" link /> flags in all.
      </>
    ),
  },
  {
    title: "Explain with rules, not the network",
    body: "Each flag is compared with the same driver's usual way through that turn in that session, costed in seconds and given a type (early braking, over-slowing, late throttle) by plain rules. Traffic, slow-downs and data problems are set apart.",
  },
];

export function MethodSteps() {
  return (
    <div className="grid gap-6">
      <Illustration />
      <ol className="grid border-t border-hairline">
        {STEPS.map((step, k) => (
          <li key={step.title} className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-3 border-b border-hairline py-4">
            <span className="pt-0.5 font-mono text-sm text-muted tabular-nums" aria-hidden="true">
              {String(k + 1).padStart(2, "0")}
            </span>
            <div className="grid gap-1">
              <h3 className="font-semibold text-fg">{step.title}</h3>
              <p className="text-base/[1.7] text-fg">{step.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
