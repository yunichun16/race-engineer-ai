/**
 * The corner story's drawing and words (spec h.2), as pure functions: the four layers' shapes
 * for the featured corner's data or for the built-in illustration, the headline, and the step
 * copy that depends on the data. The curves are the chart core's (`linePath`, `monotonePath`):
 * the story draws exactly the smooth line the real chart under it draws, never a second curve.
 */

import { linePath, monotonePath, type Series } from "../../charts/dom.ts";
import { anomalyStretch, type CornerChart, type Phase } from "../../charts/explain-corner.ts";

/** What the drawing needs: speed against metres from the apex, and where the time went. */
export interface StoryTraces {
  dist: number[]; // metres from the apex, ascending
  lap: Series; // the flagged lap's speed, km/h
  usual: Series; // the driver's usual speed
  lo: Series | null; // the usual's middle half (25th and 75th percentiles), when there is one
  hi: Series | null;
  stretch: [number, number] | null; // where the time went, metres from the apex
}

/**
 * The illustration shown until the featured corner arrives, and when it can't: a made-up corner
 * with the same story (brakes early, slow at the apex, the time lost on the way in). Constant
 * arrays rather than formulas, so the server and every browser draw the same path text. Not data.
 */
export const ILLUSTRATION: StoryTraces = {
  dist: [
    -250, -239.9, -229.7, -219.6, -209.5, -199.4, -189.2, -179.1, -169, -158.8, -148.7, -138.6, -128.5, -118.3, -108.2,
    -98.1, -87.9, -77.8, -67.7, -57.6, -47.4, -37.3, -27.2, -17.1, -6.9, 3.2, 13.3, 23.5, 33.6, 43.7, 53.8, 64, 74.1,
    84.2, 94.4, 104.5, 114.6, 124.7, 134.9, 145,
  ],
  lap: [
    282.4, 282.5, 282.9, 283.4, 284.1, 284.8, 283.8, 280.2, 274, 265.3, 254.3, 241.5, 227.3, 212.1, 196.5, 180.9, 165.7,
    151.4, 138.1, 126.3, 116.1, 107.6, 101.1, 96.6, 94.3, 102.8, 114.5, 125.5, 135.9, 145.9, 155.4, 164.5, 173.1, 181.1,
    188.3, 194.6, 199.8, 203.8, 206.3, 207.2,
  ],
  usual: [
    284, 284.3, 284.5, 284.6, 284.6, 284.5, 284.4, 284.1, 282.7, 278.5, 271.7, 262.5, 251.2, 238.3, 224.1, 209, 193.6,
    178.2, 163.5, 149.8, 137.6, 127.4, 119.5, 114.2, 111.7, 120, 131.5, 142.4, 152.9, 162.8, 172.1, 180.7, 188.5, 195.6,
    201.7, 206.9, 211.2, 214.5, 216.7, 217.5,
  ],
  lo: [
    284.6, 284.6, 284.5, 284.4, 284.1, 283.8, 283.6, 283.4, 283.2, 280.7, 275.2, 266.8, 255.8, 242.7, 227.7, 211.5, 194.5,
    177.5, 161, 145.6, 131.9, 120.5, 111.7, 106, 103.6, 112, 123.5, 134.5, 144.9, 154.6, 163.5, 171.7, 179.1, 185.6,
    191.4, 196.4, 200.6, 203.9, 206.3, 207.4,
  ],
  hi: [
    284.1, 283.8, 283.6, 283.4, 283.4, 283.4, 283.6, 283.5, 281.2, 276.5, 269.4, 260.2, 249, 236.3, 222.4, 207.9, 193.2,
    178.7, 165.1, 152.7, 141.9, 133.1, 126.4, 122.2, 120.4, 129.1, 140.7, 151.6, 161.8, 171.3, 180.1, 188.1, 195.5,
    202.3, 208.4, 213.9, 218.5, 222.3, 225, 226.4,
  ],
  stretch: [-128, -18],
};

/** The usual's middle half, when both edges are there and as long as the distances. */
function band(data: CornerChart): { lo: Series; hi: Series } | null {
  const n = data.distance_m.length;
  const lo = data.usual_lo?.speed;
  const hi = data.usual_hi?.speed;
  return Array.isArray(lo) && Array.isArray(hi) && lo.length === n && hi.length === n ? { lo, hi } : null;
}

/** The phase that lost the most time, or null when none lost any. */
export function worstPhase(phases: readonly Phase[]): Phase | null {
  let worst: Phase | null = null;
  for (const p of phases) {
    if (p.loss_s !== null && p.loss_s > 0 && (worst === null || p.loss_s > (worst.loss_s ?? 0))) worst = p;
  }
  return worst;
}

/** The featured corner's traces: speed, the usual and its band, and where the time went. */
export function tracesFrom(data: CornerChart): StoryTraces {
  const b = band(data);
  const worst = worstPhase(data.phases);
  const stretch = anomalyStretch(data.distance_m, data.delta_s) ?? (worst ? [worst.start_m, worst.end_m] : null);
  return { dist: data.distance_m, lap: data.lap.speed, usual: data.usual.speed, lo: b?.lo ?? null, hi: b?.hi ?? null, stretch };
}

/** The drawing's box: the story svg's viewBox and the plot's top and bottom. */
export const STORY_BOX = { width: 420, height: 240, top: 16, bottom: 214 } as const;

export interface StoryGeometry {
  band: string; // the usual's middle half, closed ("" without one)
  usual: string; // the usual's line
  lap: string; // the flagged lap's line
  stretch: { x: number; width: number } | null;
  dot: { x: number; y: number } | null; // the lap's slowest sample
  label: { x: number; y: number; anchor: "start" | "end" } | null; // "this lap", beside the dot
  apexX: number | null; // null when the apex is outside the drawing
}

const round = (v: number) => Math.round(v * 10) / 10;

/** The four layers' shapes in the story's viewBox: x is metres from the apex, y is speed. */
export function storyGeometry(t: StoryTraces, box: typeof STORY_BOX = STORY_BOX): StoryGeometry {
  const d0 = t.dist[0];
  const d1 = t.dist[t.dist.length - 1];
  const values = [t.lap, t.usual, t.lo ?? [], t.hi ?? []].flat().filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  // The speed domain: the lap, the usual and its band, with 10 km/h to spare at each end.
  const vmin = Math.min(...values) - 10;
  const vmax = Math.max(...values) + 10;
  const X = (d: number) => round(((d - d0) / (d1 - d0 || 1)) * box.width);
  const Y = (v: number) => round(box.bottom - ((v - vmin) / (vmax - vmin || 1)) * (box.bottom - box.top));
  const xs = t.dist.map(X);
  const toY = (s: Series): Series => s.map((v) => (typeof v === "number" && Number.isFinite(v) ? Y(v) : null));

  let bandPath = "";
  if (t.lo && t.hi) {
    const lo = toY(t.lo);
    const hi = toY(t.hi);
    // Upper edge left to right, then the lower edge back along the same curve, over the samples
    // where both edges exist (explain-corner draws its band the same way).
    const keep = xs.map((_, i) => i).filter((i) => lo[i] !== null && hi[i] !== null);
    if (keep.length >= 2) {
      const back = [...keep].reverse();
      bandPath =
        monotonePath(
          keep.map((i) => xs[i]),
          keep.map((i) => hi[i] as number),
        ) +
        monotonePath(
          back.map((i) => xs[i]),
          back.map((i) => lo[i] as number),
          "L",
        ) +
        "Z";
    }
  }

  let stretch: StoryGeometry["stretch"] = null;
  if (t.stretch) {
    const a = X(Math.max(Math.min(t.stretch[0], t.stretch[1]), d0));
    const b = X(Math.min(Math.max(t.stretch[0], t.stretch[1]), d1));
    if (b > a) stretch = { x: a, width: round(b - a) };
  }

  let slowest = -1;
  t.lap.forEach((v, i) => {
    if (typeof v === "number" && Number.isFinite(v) && (slowest < 0 || v < (t.lap[slowest] as number))) slowest = i;
  });
  const dot = slowest >= 0 ? { x: xs[slowest], y: Y(t.lap[slowest] as number) } : null;
  // The label sits right of the dot, or left of it near the right edge.
  const label = dot
    ? dot.x > box.width - 80
      ? { x: round(dot.x - 10), y: round(dot.y + 16), anchor: "end" as const }
      : { x: round(dot.x + 10), y: round(dot.y + 16), anchor: "start" as const }
    : null;

  return {
    band: bandPath,
    usual: linePath(xs, toY(t.usual)),
    lap: linePath(xs, toY(t.lap)),
    stretch,
    dot,
    label,
    apexX: d0 <= 0 && 0 <= d1 ? X(0) : null,
  };
}

const TENTHS = ["", "one tenth", "two tenths", "three tenths", "four tenths", "five tenths", "six tenths", "seven tenths", "eight tenths", "nine tenths"];

/** Time lost in words for the headline: "eight tenths" under a second, "1.2 seconds" from one up. */
export function inWords(seconds: number): string {
  if (seconds >= 1) return `${seconds.toFixed(1)} seconds`;
  const tenths = Math.round(seconds * 10);
  if (tenths <= 0) return "under a tenth";
  if (tenths >= 10) return "a second";
  return TENTHS[tenths];
}

/** Where in the corner a phase is, as the tool words it: "on entry", "through the apex", "on exit". */
export function phasePhrase(name: string): string {
  if (name === "entry") return "on entry";
  if (name === "apex") return "through the apex";
  if (name === "exit") return "on exit";
  return `in the ${name}`;
}

/** The story's headline: "Lap 16, turn 5: where Norris lost eight tenths.", or a plain one without the data. */
export function storyHeadline(data: CornerChart | null, name?: string): string {
  if (!data || data.time_lost_s === null || !(data.time_lost_s > 0)) return "Where the lap went wrong, corner by corner.";
  return `Lap ${data.lap_number}, turn ${data.turn}: where ${name ?? data.driver} lost ${inWords(data.time_lost_s)}.`;
}

/** "-6.1 [-9.2, -3.1]" as numbers, for the landing's style bar; null if it isn't that shape. */
export function parseInterval(value: string): { est: number; lo: number; hi: number } | null {
  const m = /^(-?\d+(?:\.\d+)?) \[(-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\]$/.exec(value.trim());
  if (!m) return null;
  const [est, lo, hi] = m.slice(1).map(Number);
  return lo <= est && est <= hi ? { est, lo, hi } : null;
}

/** A number with a true minus sign: "−6.1". */
export function minus(text: string): string {
  return text.replace(/-/g, "−");
}
