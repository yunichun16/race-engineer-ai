/**
 * One corner of one lap against the driver's usual (the `explain_corner` tool), shared by
 * Claude's MCP App and the website.
 *
 * Speed, time lost, throttle and braking through the corner against the usual, with the
 * phases, the stretch where the time went and any traffic shaded; a track map with the cars
 * around at the apex; and an animated replay of the corner (the car, a ghost of the usual lap
 * and the cars nearby) whose playhead is the charts' crosshair. The Python tool sends the data
 * as `structuredContent`.
 *
 * `render(root, data, opts)` draws the page and knows nothing of the host; `unmount(root)`
 * stops what it left running (the replay's animation and its keys).
 * `mcp-app/explain-corner.ts` feeds it tool results inside Claude, and the website wraps it in
 * a React component.
 */
import {
  contentWidth,
  crosshairKeys,
  finite,
  headingTag,
  htmlEl,
  linePath,
  monotonePath,
  nearestIndex,
  niceStep,
  prefersReducedMotion,
  svgEl,
  type HeadingLevel,
  type Series,
  type TrackPayload,
} from "./dom.ts";

export type Pt = [number, number] | null; // px on the map; null where the car has no position

export interface Traces {
  speed: Series;
  throttle: Series;
  brake: Series;
  gear: Series;
}

export interface Phase {
  name: string; // entry, apex or exit
  start_m: number; // metres from the apex
  end_m: number;
  loss_s: number | null;
}

export interface Deviation {
  feature: string;
  label: string;
  unit: string;
  value: number | null;
  usual: number | null;
  delta: number | null;
  z: number | null;
  adverse_z: number | null;
  threshold: number;
  evidence: boolean;
  higher_is_worse: boolean;
}

/** Another car near the corner (gaps: + behind, - ahead). */
export interface Encounter {
  driver: string;
  laps_ahead: number;
  lap_class: string;
  gap_start_s: number | null;
  gap_start_m: number | null;
  gap_before_loss_s: number | null;
  gap_before_loss_m: number | null;
  gap_window_start_s: number | null;
  closest_in_corner_s: number | null;
  passed: string; // "by", "of" or ""
  pass_m: number | null;
  approximate: boolean;
}

export interface MapCar {
  driver: string;
  gap_s: number; // + behind, - ahead
  laps: number; // laps up (+) or down (-), races only
  note: string;
  x: number;
  y: number;
}

export type CornerMap = TrackPayload & {
  window: [number, number][]; // the analysed stretch, px every 5 m from -250 to +145 m
  apex: { x: number; y: number };
  cars: MapCar[]; // the other cars when the driver reached the apex
};

export interface Replay {
  dt: number; // seconds per frame
  me: Pt[];
  dist: number[]; // metres from the apex at each frame (ascending)
  ghost?: Pt[];
  ghost_label?: string;
  cars: { driver: string; laps: number; approx: boolean; pts: Pt[] }[];
  box: [number, number, number, number]; // the corner's zoomed view: x, y, width, height in map px
}

export interface CornerChart {
  title: string;
  event: string;
  location: string;
  year: number;
  round: number;
  session: string;
  session_code: string;
  date: string;
  driver: string;
  team: string;
  lap_number: number;
  turn: string;
  lap_class: string;
  deleted: boolean;
  type: string;
  type_text: string;
  secondary_type: string | null;
  explanation: string;
  time_lost_s: number | null;
  segment_time_s: number;
  phases: Phase[];
  reference: { kind: string; laps: number };
  distance_m: number[]; // 80 points, metres from the apex
  lap: Traces;
  usual: Traces;
  delta_s: Series;
  deviations: Deviation[];
  flagged: boolean | null;
  score_pct: number | null;
  unscored: string;
  data_issue: string;
  race_control: string;
  context: string[];
  traffic: {
    type: string;
    cars: string[];
    window_m: [number | null, number | null];
    loss_onset_m: number | null;
    alongside_before_loss: string[];
    around: Encounter[];
  };
  map: CornerMap | null;
  replay: Replay | null;
  // The reference laps' middle half: their 25th and 75th percentiles (null without a reference).
  usual_lo?: { speed: Series; throttle: Series } | null;
  usual_hi?: { speed: Series; throttle: Series } | null;
  offset?: { lap: Series; usual: Series } | null; // m off the reference line, + to the left
  apex_m?: number | null; // the apex, metres along the lap
  ghost?: { driver: string; lap: number } | null; // the reference lap the replay's ghost drives
}

export type ReplayZoom = "corner" | "track";

/** Where the replay is, so a redraw (a new width) can carry on from there. */
export interface ReplayState {
  frame: number; // fractional while playing
  speed: number; // 0.5, 1 or 2
  zoom: ReplayZoom;
  playing: boolean;
}

export interface RenderOptions {
  /** Whether the deviations table starts open (the caller keeps it across redraws). */
  tableOpen?: boolean;
  /** Called when the reader opens or closes the deviations table. */
  onTableToggle?: (open: boolean) => void;
  /** The replay state to start from (the caller keeps it across redraws). */
  replay?: Partial<ReplayState>;
  /** Called whenever the replay's frame, speed, view or play state changes. */
  onReplayChange?: (state: ReplayState) => void;
  /**
   * Play the replay once when it first comes into view. Never with reduced motion: then it
   * plays only when the reader starts it.
   */
  autoplay?: boolean;
  /**
   * Whether P plays or pauses the replay from anywhere on the page (true when missing).
   * A page with several corner charts turns it off, or P would toggle every replay; Space,
   * ←/→ and Home/End still work while the replay has the focus.
   */
  pageKeys?: boolean;
  /**
   * The level of the corner's heading (1 when missing: the title of Claude's page). A page that
   * has its own h1 passes the level below the heading the chart sits under.
   */
  headingLevel?: HeadingLevel;
}

const X0 = -250; // the analysed stretch, metres from the apex
const X1 = 145;
const PAD = { left: 40, right: 12, bottom: 22 };
const PANEL = { speed: 140, delta: 62, throttle: 44, brake: 7 };
const HEADROOM = 18; // px kept clear at the top of the speed panel for its labels
const GAP = 16;
const SPEEDS = [0.5, 1, 2];
const MINUS = "−";
// Lap classes worth naming (push and race laps are the norm).
const LAP_CLASS: Record<string, string> = {
  start: "opening lap",
  slow: "slow lap",
  yellow: "yellow flag",
  neutralised: "safety car",
  in: "in lap",
  out: "out lap",
  no_time: "lap without a time",
};
// Traffic types as the label on the traffic band: "held up by DDD".
const TRAFFIC: Record<string, string> = {
  impeded: "held up by",
  battle: "racing",
  lapped: "being lapped by",
};
let svgIds = 0;
// Decimals per deviation unit.
const DIGITS: Record<string, number> = {
  "km/h": 0,
  "m from the apex": 0,
  m: 0,
  "m/s²": 1,
  "%": 0,
  s: 3,
};

/** Four traces of `n` points each. */
function isTraces(value: unknown, n: number): value is Traces {
  const t = value as Partial<Traces> | null | undefined;
  return (["speed", "throttle", "brake", "gear"] as const).every((k) => {
    const s = t?.[k];
    return Array.isArray(s) && s.length === n;
  });
}

/**
 * The keys `render` reads without checking. The optional parts (`map`, `replay`, the usual's
 * band) are checked where they're drawn, and left out when they don't have their shape.
 */
export function isCornerChart(value: unknown): value is CornerChart {
  const v = value as Partial<CornerChart> | null | undefined;
  if (!v || typeof v !== "object" || !Array.isArray(v.distance_m)) return false;
  const n = v.distance_m.length;
  return (
    n >= 2 &&
    Array.isArray(v.delta_s) &&
    v.delta_s.length === n &&
    isTraces(v.lap, n) &&
    isTraces(v.usual, n) &&
    Array.isArray(v.phases) &&
    Array.isArray(v.deviations) &&
    Array.isArray(v.context) &&
    typeof v.reference?.kind === "string" &&
    Array.isArray(v.traffic?.window_m) &&
    Array.isArray(v.traffic?.cars)
  );
}

function mapUsable(map: CornerMap | null | undefined): map is CornerMap {
  return (
    !!map &&
    map.width > 0 &&
    map.height > 0 &&
    Array.isArray(map.outline) &&
    map.outline.length >= 2 &&
    Array.isArray(map.window) &&
    map.window.length >= 2 &&
    Array.isArray(map.turns) &&
    Array.isArray(map.cars) &&
    typeof map.apex?.x === "number" &&
    typeof map.apex?.y === "number"
  );
}

function replayUsable(rp: Replay | null | undefined): rp is Replay {
  return (
    !!rp &&
    rp.dt > 0 &&
    Array.isArray(rp.me) &&
    rp.me.length >= 2 &&
    Array.isArray(rp.dist) &&
    rp.dist.length === rp.me.length &&
    rp.me.some((p) => p !== null) &&
    (rp.ghost === undefined || Array.isArray(rp.ghost)) &&
    Array.isArray(rp.cars) &&
    rp.cars.every((c) => Array.isArray(c?.pts)) &&
    Array.isArray(rp.box) &&
    rp.box.length === 4 &&
    rp.box[2] > 0 &&
    rp.box[3] > 0
  );
}

/** The usual's middle half (Phase 2), when both edges are there with both traces. */
function usualBand(data: CornerChart): {
  lo: CornerChart["usual_lo"];
  hi: CornerChart["usual_hi"];
} {
  const ok = (b: CornerChart["usual_lo"]) =>
    Array.isArray(b?.speed) &&
    b.speed.length === data.distance_m.length &&
    Array.isArray(b?.throttle) &&
    b.throttle.length === data.distance_m.length;
  return ok(data.usual_lo) && ok(data.usual_hi)
    ? { lo: data.usual_lo, hi: data.usual_hi }
    : { lo: undefined, hi: undefined };
}

/** A label's width in its SVG's units, measured when the browser can, else `fallback`. */
function textWidth(node: SVGTextElement, fallback: number): number {
  try {
    const w = node.getComputedTextLength();
    return w > 0 ? w : fallback;
  } catch {
    return fallback;
  }
}

// ---------------------------------------------------------------------------------------------
// Text

/** A number with a true minus sign, and no sign on a value that rounds to zero. */
function num(v: number, digits: number): string {
  const text = Math.abs(v).toFixed(digits);
  return v < 0 && Number(text) !== 0 ? MINUS + text : text;
}

/** A number with its sign ("+0.21", "−0.05"; "0.00" when it rounds to zero). */
function signed(v: number, digits: number): string {
  const text = Math.abs(v).toFixed(digits);
  return Number(text) === 0 ? text : (v < 0 ? MINUS : "+") + text;
}

function lapsText(n: number): string {
  if (!n) return "";
  const count = Math.abs(n) === 1 ? "1 lap" : `${Math.abs(n)} laps`;
  return `${count} ${n > 0 ? "up" : "down"}`;
}

/** "120 m before the apex", "at the apex", "30 m after the apex". */
function whereText(d: number): string {
  const m = Math.round(Math.abs(d));
  return m === 0 ? "at the apex" : `${m} m ${d < 0 ? "before" : "after"} the apex`;
}

/** A stretch of the corner: "135–70 m before the apex", "20 m before to 15 m after the apex". */
function stretchText(a: number, b: number): string {
  const [p, q] = [Math.round(a), Math.round(b)];
  if (q <= 0) return `${-p}–${-q} m before the apex`;
  if (p >= 0) return `${p}–${q} m after the apex`;
  return `${-p} m before to ${q} m after the apex`;
}

/** The detectors' percentile as "top 0.13%". */
function topText(scorePct: number): string {
  const top = Math.max(100 * (1 - scorePct), 0.01);
  return `top ${top.toFixed(top < 1 ? 2 : top < 10 ? 1 : 0)}%`;
}

/** The replay's ghost: its own label, else "usual: lap 14" (with the driver when not theirs). */
function ghostText(data: CornerChart, rp: Replay): string {
  if (rp.ghost_label) return rp.ghost_label;
  const g = data.ghost;
  if (!g) return "usual";
  return `usual: ${g.driver === data.driver ? "" : `${g.driver} `}lap ${g.lap}`;
}

function referenceText(ref: { kind: string; laps: number }): string {
  const laps = `${ref.laps} clean lap${ref.laps === 1 ? "" : "s"}`;
  return ref.kind === "field"
    ? `the field's usual (${laps})`
    : `their usual (${laps} of their own)`;
}

// ---------------------------------------------------------------------------------------------
// Shapes

/**
 * Where the time went: the stretch where the time lost rises from 10% to 90% of its peak
 * (metres from the apex), or null when the lap lost next to nothing.
 */
export function anomalyStretch(dist: number[], delta: Series): [number, number] | null {
  let peak = 0;
  let at = -1;
  delta.forEach((v, i) => {
    if (v !== null && v > peak) [peak, at] = [v, i];
  });
  if (at < 0 || peak < 0.02) return null;
  const lo = 0.1 * peak;
  const hi = 0.9 * peak;
  const value = (i: number) => delta[i] ?? 0;
  const cross = (i: number, level: number) => {
    // Where the trace crosses `level` between points i - 1 and i.
    const [a, b] = [value(i - 1), value(i)];
    const w = b === a ? 1 : (level - a) / (b - a);
    return dist[i - 1] + (dist[i] - dist[i - 1]) * Math.min(Math.max(w, 0), 1);
  };
  let s = at;
  while (s > 0 && value(s - 1) > lo) s--;
  const start = s > 0 ? cross(s, lo) : dist[0];
  let e = s;
  while (e < at && value(e) < hi) e++;
  const end = e > 0 && value(e - 1) < hi ? cross(e, hi) : dist[e];
  return end - start < 5 ? [start - 2.5, start + 2.5] : [start, end];
}

/**
 * A closed area between the trace and `zeroY`, one piece per run of values. Its edge is the
 * same smooth curve as the trace's line (`linePath`).
 */
function areaPath(xs: number[], ys: Series, zeroY: number): string {
  let d = "";
  let run: number[] = [];
  const close = () => {
    if (run.length > 1) {
      const zero = zeroY.toFixed(1);
      const edge = monotonePath(
        run.map((i) => xs[i]),
        run.map((i) => ys[i] as number),
        "L",
      );
      const [first, last] = [xs[run[0]].toFixed(1), xs[run[run.length - 1]].toFixed(1)];
      d += `M${first},${zero}${edge}L${last},${zero}Z`;
    }
    run = [];
  };
  ys.forEach((y, i) => (y === null ? close() : run.push(i)));
  close();
  return d;
}

/**
 * A band between two traces (the usual's middle half), skipping missing points: the upper
 * edge smoothed left to right, then the lower edge smoothed right to left (the same curve as
 * drawn forwards).
 */
function bandPath(xs: number[], lo: (number | null)[], hi: (number | null)[]): string {
  const keep = xs.map((_, i) => i).filter((i) => lo[i] !== null && hi[i] !== null);
  if (keep.length < 2) return "";
  const back = [...keep].reverse();
  const top = monotonePath(
    keep.map((i) => xs[i]),
    keep.map((i) => hi[i] as number),
  );
  const bottom = monotonePath(
    back.map((i) => xs[i]),
    back.map((i) => lo[i] as number),
    "L",
  );
  return `${top}${bottom}Z`;
}

/** Runs of the brake on (>= 0.5) as [first, last] indices. */
function brakeRuns(brake: Series): [number, number][] {
  const runs: [number, number][] = [];
  let start = -1;
  brake.forEach((v, i) => {
    const on = v !== null && v >= 0.5;
    if (on && start < 0) start = i;
    if ((!on || i === brake.length - 1) && start >= 0) {
      runs.push([start, on ? i : i - 1]);
      start = -1;
    }
  });
  return runs;
}

function polyline(points: [number, number][]): string {
  return points.length
    ? `M${points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join("L")}`
    : "";
}

/** Points of `map.window` (every 5 m from X0) between two distances from the apex. */
function windowSlice(map: CornerMap, from: number, to: number): [number, number][] {
  const a = Math.max(0, Math.floor((from - X0) / 5));
  const b = Math.min(map.window.length - 1, Math.ceil((to - X0) / 5));
  return map.window.slice(a, b + 1);
}

// ---------------------------------------------------------------------------------------------
// Header and table

function renderHeader(root: HTMLElement, data: CornerChart, headingLevel?: HeadingLevel): void {
  const header = htmlEl("header", "ec-header", undefined, root);
  const titleRow = htmlEl("div", "title-row", undefined, header);
  htmlEl(
    headingTag(headingLevel, 1),
    "title",
    `${data.driver} · lap ${data.lap_number} · T${data.turn}: ${data.type_text}`,
    titleRow,
  );
  htmlEl(
    "div",
    "subtitle",
    `${data.team} · ${data.year} ${data.event} · ${data.session}`,
    titleRow,
  );

  const facts = htmlEl("div", "facts", undefined, header);
  const lost = data.time_lost_s;
  if (data.reference.kind === "none") {
    htmlEl("span", "lost", "No usual to compare with: too few clean laps here", facts);
  } else if (lost === null) {
    htmlEl("span", "lost", `Time lost against ${referenceText(data.reference)}: unknown`, facts);
  } else {
    const line = htmlEl("span", "lost", lost < 0 ? "Gained " : "Lost ", facts);
    htmlEl("strong", undefined, `${Math.abs(lost).toFixed(2)} s`, line);
    line.append(` ${lost < 0 ? "on" : "against"} ${referenceText(data.reference)}`);
  }
  const chip = (text: string, cls = "") => htmlEl("span", `chip ${cls}`.trim(), text, facts);
  if (data.flagged) {
    chip(`flagged${data.score_pct === null ? "" : ` · ${topText(data.score_pct)}`}`, "flagged");
  } else {
    chip(data.flagged === false ? "not flagged" : "not scored");
  }
  if (LAP_CLASS[data.lap_class]) chip(LAP_CLASS[data.lap_class]);
  if (data.deleted) chip("lap time deleted");

  htmlEl("p", "explanation", data.explanation, header);
  const lines: [string, string][] = [];
  if (data.flagged === null && data.unscored) lines.push(["", data.unscored]);
  if (data.race_control) lines.push(["Race control: ", data.race_control]);
  if (data.data_issue) lines.push(["Data: ", data.data_issue]);
  for (const c of data.context) lines.push(["", c]);
  if (lines.length) {
    const list = htmlEl("ul", "lines", undefined, header);
    for (const [label, text] of lines) {
      const item = htmlEl("li", undefined, undefined, list);
      if (label) htmlEl("strong", undefined, label, item);
      item.append(text);
    }
  }
}

function renderTable(root: HTMLElement, data: CornerChart, opts: RenderOptions): void {
  const rows = data.deviations.filter((d) => d.value !== null || d.usual !== null);
  if (!rows.length) return;
  const details = htmlEl("details", undefined, undefined, root);
  details.open = opts.tableOpen ?? false;
  details.addEventListener("toggle", () => opts.onTableToggle?.(details.open));
  const evidence = rows.filter((d) => d.evidence).length;
  const count = evidence ? ` · ${evidence} beyond the usual spread` : "";
  htmlEl("summary", undefined, `Against the usual: ${rows.length} measures${count}`, details);
  const wrap = htmlEl("div", "table-wrap", undefined, details);
  const table = htmlEl("table", "deviations", undefined, wrap);
  const head = htmlEl("tr", undefined, undefined, htmlEl("thead", undefined, undefined, table));
  for (const label of ["Measure", "This lap", "Usual", "Δ", "z", "Evidence"]) {
    htmlEl("th", undefined, label, head);
  }
  const body = htmlEl("tbody", undefined, undefined, table);
  for (const d of rows) {
    const digits = DIGITS[d.unit] ?? 1;
    const fmt = (v: number | null) => (v === null ? "–" : num(v, digits));
    const row = htmlEl("tr", d.evidence ? "evidence" : undefined, undefined, body);
    const label = htmlEl("td", undefined, d.label, row);
    htmlEl("span", "unit", ` ${d.unit}`, label);
    htmlEl("td", undefined, fmt(d.value), row);
    htmlEl("td", undefined, fmt(d.usual), row);
    htmlEl("td", undefined, d.delta === null ? "–" : signed(d.delta, digits), row);
    htmlEl("td", undefined, d.z === null ? "–" : signed(d.z, 1), row);
    htmlEl("td", "mark", d.evidence ? "✓ yes" : "", row);
  }
  htmlEl(
    "p",
    "notes",
    "z: how far from the usual, in the usual spread of that measure; Evidence: further than " +
      "that measure's threshold, in the direction that costs time.",
    details,
  );
}

// ---------------------------------------------------------------------------------------------
// Traces

interface TraceView {
  svg: SVGSVGElement;
  /** Show the playhead at grid point `i` (the crosshair). */
  showIndex(i: number): void;
  /** Show the playhead at `d` metres from the apex (the replay); hide it outside the traces. */
  showDist(d: number): void;
  hide(): void;
  /** Whether the readout announces changes (off while the replay plays). */
  setLive(on: boolean): void;
}

function renderLegend(
  root: HTMLElement,
  data: CornerChart,
  anomaly: boolean,
  traffic: boolean,
): void {
  const legend = htmlEl("div", "legend", undefined, root);
  const item = (cls: string, text: string) => {
    const span = htmlEl("span", undefined, undefined, legend);
    htmlEl("span", `key ${cls}`, undefined, span).setAttribute("aria-hidden", "true");
    span.append(text);
  };
  item("lap", `${data.driver} lap ${data.lap_number}`);
  if (data.reference.kind !== "none") item("usual", "usual");
  if (usualBand(data).lo) item("band", "usual range (middle half)");
  if (anomaly) item("anomaly", "where the time went");
  if (traffic) item("hatch", "traffic");
}

function renderTraces(
  root: HTMLElement,
  data: CornerChart,
  width: number,
  anomaly: [number, number] | null,
): TraceView {
  const { lap, usual } = data;
  const n = data.distance_m.length;
  const plotW = width - PAD.left - PAD.right;
  const x = (d: number) => PAD.left + ((d - X0) / (X1 - X0)) * plotW;
  const xs = data.distance_m.map(x);

  const [tFrom, tTo] = data.traffic.window_m;
  const lane =
    data.traffic.type !== "" && tFrom !== null && tTo !== null && tTo > X0 && tFrom < X1
      ? ([Math.max(tFrom, X0), Math.min(tTo, X1)] as [number, number])
      : null;
  const speedTop = lane ? 38 : 24;
  const deltaTop = speedTop + PANEL.speed + GAP;
  const throttleTop = deltaTop + PANEL.delta + GAP;
  const brakeRows = [throttleTop + PANEL.throttle + GAP + 8];
  brakeRows.push(brakeRows[0] + PANEL.brake + 3);
  const plotBottom = brakeRows[1] + PANEL.brake;
  const height = plotBottom + PAD.bottom;

  const { lo, hi } = usualBand(data);
  const speeds = [
    ...finite(lap.speed),
    ...finite(usual.speed),
    ...(lo ? finite(lo.speed) : []),
    ...(hi ? finite(hi.speed) : []),
  ];
  const vLo = speeds.length ? Math.max(0, Math.floor((Math.min(...speeds) - 5) / 50) * 50) : 0;
  const vHi = speeds.length ? Math.ceil((Math.max(...speeds) + 5) / 50) * 50 : 350;
  const ySpeed = (v: number) =>
    speedTop + HEADROOM + (PANEL.speed - HEADROOM) * (1 - (v - vLo) / (vHi - vLo));

  const deltas = finite(data.delta_s);
  const dPad = Math.max(
    (deltas.length ? Math.max(...deltas) - Math.min(...deltas) : 0) * 0.12,
    0.03,
  );
  const dTop = Math.max(0, ...deltas) + dPad;
  const dBottom = Math.min(0, ...deltas) - (Math.min(0, ...deltas) < 0 ? dPad : 0);
  const yDelta = (v: number) => deltaTop + (PANEL.delta * (dTop - v)) / (dTop - dBottom);
  const yThrottle = (t: number) => throttleTop + PANEL.throttle * (1 - t / 100);

  const readout = htmlEl("div", "readout", undefined, root);
  readout.setAttribute("aria-live", "polite");
  renderLegend(root, data, anomaly !== null, lane !== null);
  const svg = svgEl("svg", {
    class: "traces",
    viewBox: `0 0 ${width} ${height}`,
    height,
    tabindex: 0,
    role: "img",
    "aria-label":
      `${data.driver}'s lap ${data.lap_number} through turn ${data.turn} against the usual: ` +
      "speed, time lost, throttle and braking from 250 m before the apex to 145 m after. " +
      "Use the left and right arrow keys to read values.",
  });
  root.append(svg);

  const label = (text: string, attrs: Record<string, string | number>) => {
    const node = svgEl("text", attrs, svg);
    node.textContent = text;
    return node;
  };
  const hline = (y: number, stroke: string) =>
    svgEl("line", { x1: PAD.left, x2: width - PAD.right, y1: y, y2: y, stroke }, svg);
  const vband = (from: number, to: number, attrs: Record<string, string | number>) =>
    svgEl(
      "rect",
      {
        x: x(from),
        y: speedTop,
        width: Math.max(x(to) - x(from), 1),
        height: plotBottom - speedTop,
        ...attrs,
      },
      svg,
    );

  // Shading first, under everything: the phases, the anomaly and the traffic.
  const phase = (name: string) => data.phases.find((p) => p.name === name);
  const apexPhase = phase("apex");
  if (apexPhase)
    vband(apexPhase.start_m, apexPhase.end_m, { fill: "var(--grid)", "fill-opacity": 0.6 });
  for (const p of data.phases.slice(1)) {
    const px = x(p.start_m);
    svgEl("line", { x1: px, x2: px, y1: 14, y2: plotBottom, stroke: "var(--grid)" }, svg);
  }
  if (anomaly) vband(anomaly[0], anomaly[1], { fill: "var(--loss)", "fill-opacity": 0.11 });

  const defs = svgEl("defs", {}, svg);
  const id = `ec${++svgIds}`; // ids for the patterns and clips, unique if a page shows several
  if (lane) {
    const hatch = svgEl(
      "pattern",
      {
        id: `${id}-hatch`,
        width: 5,
        height: 5,
        patternUnits: "userSpaceOnUse",
        patternTransform: "rotate(45)",
      },
      defs,
    );
    svgEl(
      "line",
      { x1: 0, x2: 0, y1: 0, y2: 5, stroke: "var(--fg-muted)", "stroke-width": 1.5 },
      hatch,
    );
    const y = speedTop - 13;
    svgEl(
      "rect",
      {
        x: x(lane[0]),
        y,
        width: x(lane[1]) - x(lane[0]),
        height: 9,
        fill: `url(#${id}-hatch)`,
        opacity: 0.7,
      },
      svg,
    );
    const who = data.traffic.cars.join(", ");
    const what = TRAFFIC[data.traffic.type] ?? data.traffic.type;
    label(`${what} ${who}`.trim(), { x: x(lane[0]) + 4, y: y + 8, class: "halo lane-label" });
  }

  // Phase labels along the top: "entry +0.27 s", the biggest loss in bold.
  const worst = data.phases.reduce<Phase | null>(
    (best, p) =>
      p.loss_s !== null && p.loss_s > 0 && (!best || p.loss_s > (best.loss_s ?? 0)) ? p : best,
    null,
  );
  for (const p of data.phases) {
    const from = Math.max(p.start_m, X0);
    const to = Math.min(p.end_m, X1);
    const room = x(to) - x(from) - 4;
    const loss = p.loss_s === null ? "" : `${signed(p.loss_s, 2)} s`;
    const full = loss ? `${p.name} ${loss}` : p.name;
    const text = full.length * 5.6 <= room ? full : loss.length * 5.6 <= room ? loss : "";
    if (!text) continue;
    label(text, {
      x: (x(from) + x(to)) / 2,
      y: 10,
      "text-anchor": "middle",
      class: p === worst ? "phase worst" : "phase",
    });
  }

  // The apex: a dashed line through every panel.
  svgEl(
    "line",
    {
      x1: x(0),
      x2: x(0),
      y1: speedTop,
      y2: plotBottom,
      stroke: "var(--fg-muted)",
      "stroke-dasharray": "3 3",
    },
    svg,
  );

  // Speed panel.
  const vStep = vHi - vLo > 200 ? 100 : 50;
  for (let v = Math.ceil(vLo / vStep) * vStep; v <= vHi; v += vStep) {
    hline(ySpeed(v), "var(--grid)");
    label(String(v), { x: PAD.left - 6, y: ySpeed(v) + 3, "text-anchor": "end" });
  }
  if (lo && hi) {
    const band = bandPath(
      xs,
      lo.speed.map((v) => (v === null ? null : ySpeed(v))),
      hi.speed.map((v) => (v === null ? null : ySpeed(v))),
    );
    svgEl("path", { d: band, fill: "var(--fg-muted)", "fill-opacity": 0.16 }, svg);
  }
  const usualLine = (ys: Series) =>
    svgEl(
      "path",
      {
        d: linePath(xs, ys),
        fill: "none",
        stroke: "var(--fg-muted)",
        "stroke-width": 1.5,
        "stroke-dasharray": "5 3",
      },
      svg,
    );
  const lapLine = (ys: Series, strokeWidth: number) =>
    svgEl(
      "path",
      {
        d: linePath(xs, ys),
        fill: "none",
        stroke: "var(--a)",
        "stroke-width": strokeWidth,
        "stroke-linejoin": "round",
      },
      svg,
    );
  usualLine(usual.speed.map((v) => (v === null ? null : ySpeed(v))));
  lapLine(
    lap.speed.map((v) => (v === null ? null : ySpeed(v))),
    2,
  );
  const speedTitle = label("Speed km/h", {
    x: PAD.left + 4,
    y: speedTop + 10,
    class: "panel-title halo",
  });

  // The anomaly's label over its band, clear of the panel title.
  if (anomaly) {
    const text = "where the time went";
    const half = (text.length * 5.6) / 2;
    const titleEnd = PAD.left + 4 + (speedTitle.textContent ?? "").length * 5.6;
    const mid = (x(anomaly[0]) + x(anomaly[1])) / 2;
    const cx = Math.min(Math.max(mid, titleEnd + 8 + half), width - PAD.right - half);
    label(text, { x: cx, y: speedTop + 10, "text-anchor": "middle", class: "anomaly-label halo" });
  }

  // Time-lost panel: rising means losing time to the usual.
  if (deltas.length) {
    const dStep = niceStep(dTop - dBottom, 3);
    for (let v = Math.ceil(dBottom / dStep) * dStep; v <= dTop + 1e-9; v += dStep) {
      if (Math.abs(v) < 1e-9) continue;
      hline(yDelta(v), "var(--grid)");
      label(signed(v, dStep < 0.1 ? 2 : 1), {
        x: PAD.left - 6,
        y: yDelta(v) + 3,
        "text-anchor": "end",
      });
    }
    const zeroY = yDelta(0);
    label("0 s", { x: PAD.left - 6, y: zeroY + 3, "text-anchor": "end" });
    const deltaYs = data.delta_s.map((v) => (v === null ? null : yDelta(v)));
    const area = areaPath(xs, deltaYs, zeroY);
    const clips = [
      [deltaTop, zeroY - deltaTop],
      [zeroY, deltaTop + PANEL.delta - zeroY],
    ];
    clips.forEach(([y, h], i) => {
      const clip = svgEl("clipPath", { id: `${id}-delta-${i}` }, defs);
      svgEl("rect", { x: PAD.left, y, width: plotW, height: Math.max(h, 0) }, clip);
    });
    svgEl(
      "path",
      { d: area, fill: "var(--loss)", "fill-opacity": 0.14, "clip-path": `url(#${id}-delta-0)` },
      svg,
    );
    svgEl(
      "path",
      {
        d: area,
        fill: "var(--fg-muted)",
        "fill-opacity": 0.14,
        "clip-path": `url(#${id}-delta-1)`,
      },
      svg,
    );
    hline(zeroY, "var(--axis)");
    svgEl(
      "path",
      { d: linePath(xs, deltaYs), fill: "none", stroke: "var(--loss)", "stroke-width": 1.5 },
      svg,
    );
  } else {
    label("No usual lap to compare with", { x: PAD.left + 4, y: deltaTop + PANEL.delta / 2 + 3 });
  }

  // Where the loss began (the traffic rules' onset), starting below the panel title when it
  // would cross it.
  const onset = data.traffic.loss_onset_m;
  if (onset !== null && onset > X0 && onset < X1 && deltas.length) {
    const ox = x(onset);
    svgEl(
      "line",
      {
        x1: ox,
        x2: ox,
        y1: ox < PAD.left + 4 + 20 * 5.6 + 4 ? deltaTop + 13 : deltaTop,
        y2: deltaTop + PANEL.delta,
        stroke: "var(--fg)",
        "stroke-opacity": 0.5,
        "stroke-dasharray": "2 2",
      },
      svg,
    );
    // Beside the line at the top of the panel, below the panel title if it would touch it.
    const right = ox < width - PAD.right - 60;
    const clear = right
      ? ox + 4 > PAD.left + 4 + 20 * 5.6 + 6
      : ox - 4 - 60 > PAD.left + 4 + 20 * 5.6 + 6;
    label("loss starts", {
      x: right ? ox + 4 : ox - 4,
      y: deltaTop + (clear ? 10 : 22),
      "text-anchor": right ? "start" : "end",
      class: "halo note-label",
    });
  }
  label("Time lost to usual s", { x: PAD.left + 4, y: deltaTop + 10, class: "panel-title halo" });

  // Throttle panel.
  hline(yThrottle(0), "var(--axis)");
  hline(yThrottle(100), "var(--grid)");
  label("100", { x: PAD.left - 6, y: yThrottle(100) + 3, "text-anchor": "end" });
  label("0", { x: PAD.left - 6, y: yThrottle(0) + 3, "text-anchor": "end" });
  if (lo && hi) {
    const band = bandPath(
      xs,
      lo.throttle.map((t) => (t === null ? null : yThrottle(t))),
      hi.throttle.map((t) => (t === null ? null : yThrottle(t))),
    );
    svgEl("path", { d: band, fill: "var(--fg-muted)", "fill-opacity": 0.16 }, svg);
  }
  usualLine(usual.throttle.map((t) => (t === null ? null : yThrottle(t))));
  lapLine(
    lap.throttle.map((t) => (t === null ? null : yThrottle(t))),
    1.5,
  );
  label("Throttle %", { x: PAD.left + 4, y: throttleTop + 10, class: "panel-title halo" });

  // Braking strips: this lap, then the usual (when there is one).
  const strips: [Series, string, string][] = [[lap.brake, "var(--a)", data.driver]];
  if (data.reference.kind !== "none") strips.push([usual.brake, "var(--fg-muted)", "usual"]);
  strips.forEach(([brake, color, name], k) => {
    const y = brakeRows[k];
    svgEl("rect", { x: PAD.left, y, width: plotW, height: PANEL.brake, fill: "var(--grid)" }, svg);
    label(name, {
      x: PAD.left - 6,
      y: y + PANEL.brake - 0.5,
      "text-anchor": "end",
      "font-size": 9,
    });
    for (const [a, b] of brakeRuns(brake)) {
      const x0 = xs[a];
      const x1 = b + 1 < n ? xs[b + 1] : xs[b];
      svgEl(
        "rect",
        { x: x0, y, width: Math.max(x1 - x0, 1.5), height: PANEL.brake, fill: color },
        svg,
      );
    }
  });
  label("Brake", { x: PAD.left + 4, y: brakeRows[0] - 4, class: "panel-title halo" });

  // Evidence: where this lap braked or got back on full throttle against the usual.
  const bracket = (dev: Deviation, y: number, text: string) => {
    const [a, b] = [x(dev.usual as number), x(dev.value as number)];
    const attrs = { stroke: "var(--fg)", "stroke-width": 1 };
    svgEl("line", { x1: a, x2: b, y1: y, y2: y, ...attrs }, svg);
    for (const px of [a, b]) svgEl("line", { x1: px, x2: px, y1: y - 3, y2: y + 3, ...attrs }, svg);
    // The words beside the bracket, on the side with room.
    const right = Math.max(a, b) + 6 + text.length * 5.6 <= width - PAD.right;
    label(text, {
      x: right ? Math.max(a, b) + 6 : Math.min(a, b) - 6,
      y: y + 3.5,
      "text-anchor": right ? "start" : "end",
      class: "evidence-label halo",
    });
  };
  for (const dev of data.deviations) {
    if (!dev.evidence || dev.value === null || dev.usual === null || dev.delta === null) continue;
    if (dev.value < X0 || dev.value > X1 || dev.usual < X0 || dev.usual > X1) continue;
    const m = Math.round(Math.abs(dev.delta));
    if (dev.feature === "brake_start_d") {
      bracket(dev, brakeRows[0] - 9, `braked ${m} m ${dev.delta < 0 ? "earlier" : "later"}`);
    } else if (dev.feature === "throttle_pickup_d") {
      bracket(dev, throttleTop - 8, `full throttle ${m} m ${dev.delta > 0 ? "later" : "earlier"}`);
    }
  }

  // Distance axis.
  for (let d = -200; d <= X1; d += 50) {
    const text = d === 0 ? "apex" : d === -200 ? `${MINUS}200 m` : signed(d, 0);
    label(text, { x: x(d), y: height - 6, "text-anchor": "middle" });
  }

  // Playhead (the crosshair, or the replay's position) and the readout.
  const cursor = svgEl(
    "line",
    {
      y1: speedTop,
      y2: plotBottom,
      stroke: "var(--fg)",
      "stroke-opacity": 0.5,
      visibility: "hidden",
    },
    svg,
  );
  const dot = (color: string) =>
    svgEl(
      "circle",
      { r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" },
      svg,
    );
  const dots = [dot("var(--fg-muted)"), dot("var(--a)"), dot("var(--loss)")];

  const idle = () => {
    readout.replaceChildren();
    if (anomaly) {
      const item = htmlEl("span", undefined, "Most of the time went ", readout);
      htmlEl("strong", undefined, stretchText(anomaly[0], anomaly[1]), item);
    }
    htmlEl("span", undefined, "Hover or use ← → for values", readout);
  };
  const values = (index: number, d: number) => {
    readout.replaceChildren();
    htmlEl("strong", undefined, whereText(d), readout);
    const pair = (
      name: string,
      a: number | null,
      b: number | null,
      unit: string,
      keyed = false,
    ) => {
      const item = htmlEl("span", undefined, undefined, readout);
      if (keyed) htmlEl("span", "key lap", undefined, item).setAttribute("aria-hidden", "true");
      item.append(`${name} `);
      htmlEl("strong", undefined, a === null ? "–" : `${a.toFixed(0)}${unit}`, item);
      if (data.reference.kind !== "none")
        item.append(` (usual ${b === null ? "–" : `${b.toFixed(0)}${unit}`})`);
    };
    pair(data.driver, lap.speed[index], usual.speed[index], " km/h", true);
    pair("throttle", lap.throttle[index], usual.throttle[index], "%");
    const brake = (v: number | null) => (v === null ? "–" : v >= 0.5 ? "on" : "off");
    const item = htmlEl("span", undefined, "brake ", readout);
    htmlEl("strong", undefined, brake(lap.brake[index]), item);
    if (data.reference.kind !== "none") item.append(` (usual ${brake(usual.brake[index])})`);
    pair("gear", lap.gear[index], usual.gear[index], "");
    const delta = data.delta_s[index];
    if (delta !== null) {
      const lost = htmlEl("span", undefined, undefined, readout);
      htmlEl("strong", undefined, `${signed(delta, 2)} s`, lost);
      lost.append(delta < 0 ? " ahead of usual" : " behind usual");
    }
  };
  const place = (px: number, index: number) => {
    cursor.setAttribute("x1", String(px));
    cursor.setAttribute("x2", String(px));
    cursor.setAttribute("visibility", "visible");
    const ys = [usual.speed[index], lap.speed[index]].map((v) => (v === null ? null : ySpeed(v)));
    ys.push(data.delta_s[index] === null ? null : yDelta(data.delta_s[index] as number));
    ys.forEach((y, k) => {
      dots[k].setAttribute("cx", String(px));
      dots[k].setAttribute("cy", String(y ?? 0));
      dots[k].setAttribute("visibility", y === null ? "hidden" : "visible");
    });
  };
  const hide = () => {
    cursor.setAttribute("visibility", "hidden");
    dots.forEach((node) => node.setAttribute("visibility", "hidden"));
    idle();
  };
  idle();

  return {
    svg,
    showIndex(i) {
      place(xs[i], i);
      values(i, data.distance_m[i]);
    },
    showDist(d) {
      if (d < X0 || d > X1) {
        hide(); // the replay's own readout says where the car is
        return;
      }
      const i = nearestIndex(data.distance_m, d);
      place(x(d), i);
      values(i, d);
    },
    hide,
    setLive(on) {
      readout.setAttribute("aria-live", on ? "polite" : "off");
    },
  };
}

// ---------------------------------------------------------------------------------------------
// Track map (static): where the corner is and who was around at the apex

type Box = [number, number, number, number]; // left, top, right, bottom

/** Label offsets from a dot to the label's start (dx < 0: the label ends there), baseline. */
// prettier-ignore
const SPOTS: [number, number][] = [
  [7, -5], [7, 12], [-7, -5], [-7, 12], [7, -17], [7, 24], [-7, -17], [-7, 24],
];

/**
 * The first spot around (x, y) where a label `w` wide and `h` high stays inside `bounds` and
 * clear of the `taken` boxes (which it joins); when none is clear, the one inside `bounds` that
 * covers the least of them. The label goes at (`x`, `baseline`) with `anchor`: a label left of
 * the point ends there, so it sits next to the point even when `w` is only an estimate.
 */
function pickSpot(
  x: number,
  y: number,
  w: number,
  h: number,
  taken: Box[],
  bounds: Box,
  spots: [number, number][] = SPOTS,
): { x: number; baseline: number; anchor: "start" | "end"; spot: [number, number] } {
  const boxAt = ([dx, dy]: [number, number]): Box => {
    const left = dx > 0 ? x + dx : x + dx - w;
    return [left, y + dy - h + 2, left + w, y + dy + 2];
  };
  const inside = (b: Box) =>
    b[0] >= bounds[0] && b[2] <= bounds[2] && b[1] >= bounds[1] && b[3] <= bounds[3];
  const clear = (b: Box) =>
    inside(b) && !taken.some((t) => b[0] < t[2] && b[2] > t[0] && b[1] < t[3] && b[3] > t[1]);
  const overlap = (b: Box) =>
    taken.reduce(
      (sum, t) =>
        sum +
        Math.max(0, Math.min(b[2], t[2]) - Math.max(b[0], t[0])) *
          Math.max(0, Math.min(b[3], t[3]) - Math.max(b[1], t[1])),
      0,
    );
  // Crowded: the spot inside the bounds that covers the least of the others.
  const fallback = () => {
    const ok = spots.filter((s) => inside(boxAt(s)));
    return ok.length
      ? ok.reduce((a, b) => (overlap(boxAt(b)) < overlap(boxAt(a)) ? b : a))
      : spots[0];
  };
  const spot = spots.find((s) => clear(boxAt(s))) ?? fallback();
  taken.push(boxAt(spot));
  return { x: x + spot[0], baseline: y + spot[1], anchor: spot[0] > 0 ? "start" : "end", spot };
}

function startLine(
  parent: SVGElement,
  start: NonNullable<TrackPayload["start"]>,
  project: (p: [number, number]) => [number, number],
  size: number,
): void {
  const [sx, sy] = project([start.x, start.y]);
  const [ux, uy] = [start.dx, start.dy];
  svgEl(
    "line",
    {
      x1: sx - uy * size,
      y1: sy + ux * size,
      x2: sx + uy * size,
      y2: sy - ux * size,
      stroke: "var(--fg)",
      "stroke-width": 2,
    },
    parent,
  );
  const tip = [sx + ux * size * 2.2, sy + uy * size * 2.2];
  const a = size * 0.7;
  const d =
    `M${tip[0] + ux * a},${tip[1] + uy * a}L${tip[0] - uy * a * 0.7},${tip[1] + ux * a * 0.7}` +
    `L${tip[0] + uy * a * 0.7},${tip[1] - ux * a * 0.7}Z`;
  svgEl("path", { d, fill: "var(--fg)" }, parent);
}

function carText(c: MapCar): string {
  const extras = [lapsText(c.laps), c.note].filter(Boolean).join(", ");
  const gap = `${Math.abs(c.gap_s).toFixed(1)} s ${c.gap_s < 0 ? "ahead" : "behind"}`;
  return `${c.driver} ${gap}${extras ? ` (${extras})` : ""}`;
}

function renderMap(
  parent: HTMLElement,
  data: CornerChart,
  map: CornerMap,
  anomaly: [number, number] | null,
): void {
  const fig = htmlEl("figure", "panel map-panel", undefined, parent);
  const head = htmlEl("div", "panel-head", undefined, fig);
  htmlEl("strong", undefined, `Turn ${data.turn}`, head);
  htmlEl("span", undefined, "at the apex", head);
  const svg = svgEl("svg", {
    class: "map",
    viewBox: `0 0 ${map.width} ${map.height}`,
    role: "img",
    "aria-label":
      `Track map with turn ${data.turn} and the analysed stretch highlighted, and the cars ` +
      `around ${data.driver} as they reached the apex.`,
  });
  fig.append(svg);
  const road = polyline(map.outline) + "Z";
  svgEl("path", { d: road, class: "road" }, svg);
  svgEl("path", { d: road, class: "road-centre" }, svg);
  svgEl("path", { d: polyline(map.window), class: "window" }, svg);
  if (anomaly)
    svgEl("path", { d: polyline(windowSlice(map, anomaly[0], anomaly[1])), class: "anomaly" }, svg);
  if (map.start) startLine(svg, map.start, (p) => p, 7);

  for (const t of map.turns) {
    const cls = t.label === data.turn ? "turn target" : "turn";
    svgEl("text", { x: t.tx, y: t.ty + 3, "text-anchor": "middle", class: cls }, svg).textContent =
      t.label;
  }
  const cars = [...map.cars].sort((a, b) => Math.abs(a.gap_s) - Math.abs(b.gap_s));
  for (const c of cars) {
    svgEl("circle", { cx: c.x, cy: c.y, r: 4, class: "car" }, svg);
  }
  svgEl("circle", { cx: map.apex.x, cy: map.apex.y, r: 5, class: "me" }, svg);
  // The labels keep clear of the turn numbers and of every dot.
  const taken = map.turns.map((t): Box => {
    const w = t.label.length * 6 + 2;
    return [t.tx - w / 2, t.ty - 7, t.tx + w / 2, t.ty + 5];
  });
  for (const c of cars) taken.push([c.x - 4, c.y - 4, c.x + 4, c.y + 4]);
  taken.push([map.apex.x - 5, map.apex.y - 5, map.apex.x + 5, map.apex.y + 5]);
  const bounds: Box = [1, 1, map.width - 1, map.height - 1];
  const place = (x: number, y: number, text: string, cls: string, charW: number) => {
    const node = svgEl("text", { class: cls }, svg);
    node.textContent = text;
    const spot = pickSpot(x, y, textWidth(node, text.length * charW) + 2, 10, taken, bounds);
    node.setAttribute("x", spot.x.toFixed(1));
    node.setAttribute("y", spot.baseline.toFixed(1));
    node.setAttribute("text-anchor", spot.anchor);
  };
  place(map.apex.x, map.apex.y, data.driver, "me-label", 7.6);
  for (const c of cars) place(c.x, c.y, c.driver, "car-label", 6.2);

  const cap = htmlEl("figcaption", undefined, undefined, fig);
  htmlEl("strong", undefined, "Around: ", cap);
  cap.append(map.cars.length ? `${map.cars.map(carText).join("; ")}.` : "no other car within 5 s.");
}

// ---------------------------------------------------------------------------------------------
// Replay

interface ReplayView {
  /** Metres from the apex at the current frame. */
  dist(): number;
  readonly frame: number;
  /** Jump to `d` metres from the apex and pause, without telling the charts (they moved it). */
  seekDist(d: number): void;
  dispose(): void;
}

interface ReplayHooks {
  /** The frame moved (playing, scrubbing, stepping): `d` metres from the apex. */
  onFrame: (d: number) => void;
  /** It started or stopped playing. */
  onPlaying: (playing: boolean) => void;
  onChange?: (state: ReplayState) => void;
}

function renderReplay(
  parent: HTMLElement,
  data: CornerChart,
  map: CornerMap,
  rp: Replay,
  anomaly: [number, number] | null,
  init: Partial<ReplayState>,
  autoplay: boolean,
  pageKeys: boolean,
  hooks: ReplayHooks,
): ReplayView {
  const fig = htmlEl("figure", "panel replay-panel", undefined, parent);
  const head = htmlEl("div", "panel-head", undefined, fig);
  htmlEl("strong", undefined, "Corner replay", head);
  const stage = htmlEl("div", "stage", undefined, fig);
  const W = Math.round(stage.clientWidth || 320);
  const H = Math.round(Math.min(Math.max(W * 0.68, 220), 300));
  const svg = svgEl("svg", {
    class: "replay",
    viewBox: `0 0 ${W} ${H}`,
    height: H,
    tabindex: 0,
    role: "img",
    "aria-label":
      `Replay of turn ${data.turn}: ${data.driver}` +
      `${rp.ghost ? `, a ghost of the usual lap (${ghostText(data, rp)})` : ""}` +
      `${rp.cars.length ? " and the cars around" : ""}. ` +
      "Space plays or pauses, the left and right arrow keys step one frame, Home and End " +
      (pageKeys ? "jump to the ends; P plays or pauses from anywhere." : "jump to the ends."),
  });
  stage.append(svg);
  const scene = svgEl("g", {}, svg);
  const marks = svgEl("g", {}, svg);

  const last = rp.me.length - 1;
  let frame = Math.min(Math.max(init.frame ?? 0, 0), last);
  let speed = SPEEDS.includes(init.speed ?? 1) ? (init.speed ?? 1) : 1;
  let zoom: ReplayZoom = init.zoom ?? "corner";
  let playing = false;
  let raf = 0;
  let lastTime: number | null = null;
  let resumeOnShow = false;
  let k = 1;
  let ox = 0;
  let oy = 0;
  const project = (p: [number, number]): [number, number] => [p[0] * k + ox, p[1] * k + oy];

  // The road, the analysed stretch and the turn numbers, drawn for the current view.
  const drawScene = () => {
    const view = zoom === "corner" ? rp.box : [0, 0, map.width, map.height];
    k = Math.min(W / view[2], H / view[3]) * (zoom === "corner" ? 1 : 0.94);
    ox = (W - view[2] * k) / 2 - view[0] * k;
    oy = (H - view[3] * k) / 2 - view[1] * k;
    scene.replaceChildren();
    const roadW = Math.min(Math.max(7 * k, 6), 20);
    const road = polyline(map.outline.map(project)) + "Z";
    svgEl("path", { d: road, class: "road", "stroke-width": roadW }, scene);
    svgEl("path", { d: road, class: "road-centre" }, scene);
    svgEl(
      "path",
      {
        d: polyline(map.window.map(project)),
        class: "window",
        "stroke-width": Math.max(roadW * 0.35, 3),
      },
      scene,
    );
    if (anomaly) {
      const stretch = windowSlice(map, anomaly[0], anomaly[1]).map(project);
      svgEl(
        "path",
        { d: polyline(stretch), class: "anomaly", "stroke-width": Math.max(roadW * 0.35, 3) },
        scene,
      );
    }
    if (map.start) startLine(scene, map.start, project, Math.max(roadW * 0.8, 7));
    turnBoxes = [];
    for (const t of map.turns) {
      const [tx, ty] = project([t.tx, t.ty]);
      if (tx < 6 || tx > W - 6 || ty < 8 || ty > H - 4) continue;
      const cls = t.label === data.turn ? "turn target" : "turn";
      const node = svgEl("text", { x: tx, y: ty + 4, "text-anchor": "middle", class: cls }, scene);
      node.textContent = t.label;
      const w = textWidth(node, t.label.length * 6.5) + 2;
      turnBoxes.push([tx - w / 2, ty - 5, tx + w / 2, ty + 6]);
    }
  };
  let turnBoxes: Box[] = []; // the turn numbers in view, kept clear by the cars' labels

  // The cars: others first, then the ghost, then the driver on top.
  interface Mark {
    pts: Pt[];
    dot: SVGCircleElement;
    text: SVGTextElement;
    width: number; // the label's, px
    spots: [number, number][]; // where its label may go, around the dot
    spot: [number, number]; // where its label went last frame, tried first (no flicker)
  }
  // prettier-ignore
  const LABEL_SPOTS: [number, number][] = [
    [8, -7], [8, 15], [-8, -7], [-8, 15], [8, -19], [8, 27], [-8, -19], [-8, 27],
  ];
  const mark = (
    pts: Pt[],
    r: number,
    cls: string,
    text: string,
    textCls: string,
    charW: number,
  ): Mark => {
    const dot = svgEl("circle", { r, class: cls }, marks);
    const node = svgEl("text", { class: `${textCls} halo` }, marks);
    node.textContent = text;
    const width = textWidth(node, text.length * charW) + 2;
    // Spots further out for a dot bigger than the driver's.
    const out = Math.max(r - 6, 0);
    const spots = LABEL_SPOTS.map(([dx, dy]): [number, number] => [
      dx + Math.sign(dx) * out,
      dy + Math.sign(dy) * out,
    ]);
    return { pts, dot, text: node, width, spots, spot: spots[0] };
  };
  // Drawn in this order (the driver on top); labels are placed driver first, then the ghost.
  // The ghost's ring is wider than the car, so it shows around it while they're level.
  const cars = rp.cars.map((c) =>
    mark(c.pts, 4, c.approx ? "car approx" : "car", c.driver, "car-label", 6.2),
  );
  const ghost = rp.ghost
    ? mark(rp.ghost, 8.5, "ghost", ghostText(data, rp), "ghost-label", 5.8)
    : null;
  if (ghost) ghost.spot = ghost.spots[3]; // below-left: clear of the driver's label when level
  const me = mark(rp.me, 6, "me", data.driver, "me-label", 7.6);
  const byPriority = [me, ...(ghost ? [ghost] : []), ...cars];

  const at = (pts: Pt[], f: number): [number, number] | null => {
    const i = Math.floor(f);
    const w = f - i;
    const a = pts[Math.min(i, pts.length - 1)];
    const b = pts[Math.min(i + 1, pts.length - 1)];
    if (!a || !b) return a ?? null;
    return [a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w];
  };
  const distAt = (f: number) => {
    const i = Math.floor(f);
    const a = rp.dist[Math.min(i, last)];
    const b = rp.dist[Math.min(i + 1, last)];
    return a + (b - a) * (f - i);
  };

  // Controls.
  const controls = htmlEl("div", "controls", undefined, fig);
  const play = htmlEl("button", "play", undefined, controls);
  play.type = "button";
  if (pageKeys) play.setAttribute("aria-keyshortcuts", "P");
  const group = (name: string, where: HTMLElement) => {
    const node = htmlEl("div", "seg", undefined, where);
    node.setAttribute("role", "group");
    node.setAttribute("aria-label", name);
    return node;
  };
  const speedGroup = group("Replay speed", controls);
  const speedButtons = SPEEDS.map((s) => {
    const b = htmlEl("button", undefined, `${s}×`, speedGroup);
    b.type = "button";
    b.addEventListener("click", () => {
      speed = s;
      syncButtons();
      changed();
    });
    return b;
  });
  const scrub = htmlEl("input", "scrub", undefined, controls);
  scrub.type = "range";
  scrub.min = "0";
  scrub.max = String(last);
  scrub.step = "1";
  scrub.setAttribute("aria-label", "Replay position");
  const zoomGroup = group("Replay view", head);
  const zoomButtons = (["corner", "track"] as ReplayZoom[]).map((z) => {
    const b = htmlEl("button", undefined, z === "corner" ? "Corner" : "Whole track", zoomGroup);
    b.type = "button";
    b.addEventListener("click", () => {
      if (zoom === z) return;
      zoom = z;
      drawScene();
      draw(false);
      syncButtons();
      changed();
    });
    return b;
  });
  const hud = htmlEl("div", "hud", undefined, fig);
  hud.setAttribute("aria-live", "polite");

  const legend = htmlEl("div", "replay-legend", undefined, fig);
  const key = (cls: string, text: string) => {
    const span = htmlEl("span", undefined, undefined, legend);
    htmlEl("span", `dot ${cls}`, undefined, span).setAttribute("aria-hidden", "true");
    span.append(text);
  };
  key("me", data.driver);
  if (rp.ghost) key("ghost", `${ghostText(data, rp)}, starting level`);
  if (rp.cars.length) {
    const names = rp.cars.map((c) => `${c.driver}${c.laps ? ` (${lapsText(c.laps)})` : ""}`);
    key("car", names.join(", "));
  }
  if (rp.cars.some((c) => c.approx)) key("car approx", "position estimated");
  htmlEl("span", "source", "From the telemetry, not video.", legend);

  const syncButtons = () => {
    play.textContent = playing ? "❚❚ Pause" : "▶ Play";
    play.setAttribute("aria-label", playing ? "Pause the replay" : "Play the replay");
    speedButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(SPEEDS[i] === speed)));
    zoomButtons.forEach((b, i) =>
      b.setAttribute("aria-pressed", String((i === 0) === (zoom === "corner"))),
    );
  };
  const changed = () => hooks.onChange?.({ frame, speed, zoom, playing });

  const draw = (notify = true) => {
    const taken: Box[] = [...turnBoxes];
    for (const m of byPriority) {
      const p = at(m.pts, frame);
      const shown = p ? "visible" : "hidden";
      m.dot.setAttribute("visibility", shown);
      m.text.setAttribute("visibility", shown);
      if (!p) continue;
      const [px, py] = project(p);
      m.dot.setAttribute("cx", px.toFixed(1));
      m.dot.setAttribute("cy", py.toFixed(1));
      // The car's size for every dot: a label may touch the ghost's wider ring (its halo
      // keeps it readable), so the driver's label can stay beside the driver while level.
      taken.push([px - 6, py - 6, px + 6, py + 6]);
    }
    for (const m of byPriority) {
      if (m.text.getAttribute("visibility") === "hidden") continue;
      const [px, py] = [Number(m.dot.getAttribute("cx")), Number(m.dot.getAttribute("cy"))];
      const spots = [m.spot, ...m.spots.filter((s) => s !== m.spot)];
      const spot = pickSpot(px, py, m.width, 11, taken, [0, 0, W, H], spots);
      m.spot = spot.spot;
      m.text.setAttribute("x", spot.x.toFixed(1));
      m.text.setAttribute("y", spot.baseline.toFixed(1));
      m.text.setAttribute("text-anchor", spot.anchor);
    }
    const d = distAt(frame);
    const outside = d < X0 || d > X1 ? " · outside the analysed stretch" : "";
    const text = `${(frame * rp.dt).toFixed(1)} s · ${whereText(d)}${outside}`;
    hud.textContent = text;
    scrub.value = String(Math.round(frame));
    scrub.setAttribute("aria-valuetext", text);
    if (notify) hooks.onFrame(d);
    changed();
  };

  const tick = (now: number) => {
    if (!playing) return;
    // At most 0.1 s per step: after a stall (a busy or throttled page) it carries on, not jumps.
    if (lastTime !== null) frame += ((Math.min(now - lastTime, 100) / 1000) * speed) / rp.dt;
    lastTime = now;
    if (frame >= last) {
      frame = last;
      setPlaying(false);
      draw();
      return;
    }
    draw();
    raf = requestAnimationFrame(tick);
  };
  // The position line speaks up when the reader moves the replay itself: not while it plays,
  // nor when the charts' crosshair moves it (their own readout says where).
  let chartDriven = false;
  const syncLive = () => hud.setAttribute("aria-live", playing || chartDriven ? "off" : "polite");
  const setPlaying = (on: boolean, fromVisibility = false) => {
    if (!fromVisibility) resumeOnShow = false; // the reader's own choice wins
    if (on) chartDriven = false;
    if (on === playing) return;
    playing = on;
    lastTime = null;
    syncLive();
    hooks.onPlaying(on);
    if (on) {
      if (frame >= last) frame = 0;
      raf = requestAnimationFrame(tick);
    } else {
      cancelAnimationFrame(raf);
    }
    syncButtons();
    changed();
  };
  const step = (f: number) => {
    setPlaying(false);
    chartDriven = false;
    syncLive();
    frame = Math.min(Math.max(f, 0), last);
    draw();
  };

  play.addEventListener("click", () => setPlaying(!playing));
  scrub.addEventListener("input", () => step(Number(scrub.value)));
  svg.addEventListener("keydown", (event) => {
    const moves: Record<string, number> = {
      ArrowRight: Math.round(frame) + 1,
      ArrowLeft: Math.round(frame) - 1,
      Home: 0,
      End: last,
    };
    if (event.key === " ") {
      event.preventDefault();
      setPlaying(!playing);
    } else if (event.key in moves) {
      event.preventDefault();
      step(moves[event.key]);
    }
  });
  // P plays or pauses from anywhere on the page (but not while typing).
  const onKey = (event: KeyboardEvent) => {
    if (event.key !== "p" && event.key !== "P") return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
    const target = event.target as HTMLElement | null;
    const typing =
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement ||
      (target instanceof HTMLInputElement && target.type !== "range") ||
      target?.isContentEditable;
    if (typing) return;
    event.preventDefault();
    setPlaying(!playing);
  };
  // Pause while the page is hidden, and carry on when it's back.
  const onVisibility = () => {
    if (document.hidden && playing) {
      setPlaying(false, true);
      resumeOnShow = true;
    } else if (!document.hidden && resumeOnShow) {
      resumeOnShow = false;
      if (frame < last) setPlaying(true, true);
    }
  };
  if (pageKeys) document.addEventListener("keydown", onKey);
  document.addEventListener("visibilitychange", onVisibility);

  // Play once when the replay first comes into view; never with reduced motion.
  let observer: IntersectionObserver | undefined;
  if (autoplay && !init.playing && !prefersReducedMotion() && "IntersectionObserver" in window) {
    observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        observer?.disconnect();
        observer = undefined;
        if (!playing && frame === 0) setPlaying(true);
      },
      { threshold: 0.6 },
    );
    observer.observe(stage);
  }

  drawScene();
  syncButtons();
  draw(frame > 0);
  if (init.playing) setPlaying(true);

  return {
    dist: () => distAt(frame),
    get frame() {
      return frame;
    },
    seekDist(d) {
      setPlaying(false);
      chartDriven = true;
      syncLive();
      let i = 0;
      while (i < last && rp.dist[i + 1] < d) i++;
      const [a, b] = [rp.dist[i], rp.dist[Math.min(i + 1, last)]];
      frame = Math.min(Math.max(i + (b === a ? 0 : (d - a) / (b - a)), 0), last);
      draw(false);
    },
    dispose() {
      playing = false;
      cancelAnimationFrame(raf);
      observer?.disconnect();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("visibilitychange", onVisibility);
    },
  };
}

// ---------------------------------------------------------------------------------------------
// The page

const running = new WeakMap<HTMLElement, () => void>();

/** Stop what `render` left running in `root` (the replay's animation and its keys). */
export function unmount(root: HTMLElement): void {
  running.get(root)?.();
  running.delete(root);
}

/** Draw the corner into `root`, replacing what was there, at `root`'s current width. */
export function render(root: HTMLElement, data: CornerChart, opts: RenderOptions = {}): void {
  unmount(root);
  root.replaceChildren();
  const width = contentWidth(root, 300);
  const anomaly = anomalyStretch(data.distance_m, data.delta_s);

  renderHeader(root, data, opts.headingLevel);
  const traces = renderTraces(root, data, width, anomaly);

  let replay: ReplayView | undefined;
  const crosshair = crosshairKeys(
    traces.svg,
    data.distance_m.length,
    (i) => {
      if (i >= 0) {
        traces.showIndex(i);
        replay?.seekDist(data.distance_m[i]);
      } else if (replay && replay.frame > 0) {
        traces.showDist(replay.dist());
      } else {
        traces.hide();
      }
    },
    (px) => {
      const plotW = width - PAD.left - PAD.right;
      return nearestIndex(data.distance_m, X0 + ((px - PAD.left) / plotW) * (X1 - X0));
    },
  );

  if (mapUsable(data.map)) {
    const row = htmlEl("div", "maps", undefined, root);
    renderMap(row, data, data.map, anomaly);
    if (replayUsable(data.replay)) {
      replay = renderReplay(
        row,
        data,
        data.map,
        data.replay,
        anomaly,
        opts.replay ?? {},
        opts.autoplay ?? false,
        opts.pageKeys ?? true,
        {
          onFrame: (d) => {
            if (crosshair.index < 0) traces.showDist(d);
          },
          onPlaying: (playing) => {
            // The readouts would change 60 times a second: announce them only when stopped.
            traces.setLive(!playing);
            if (playing && crosshair.index >= 0) crosshair.hide();
          },
          onChange: opts.onReplayChange,
        },
      );
      const view = replay;
      running.set(root, () => view.dispose());
    } else {
      const fig = htmlEl("figure", "panel replay-panel", undefined, row);
      const head = htmlEl("div", "panel-head", undefined, fig);
      htmlEl("strong", undefined, "Corner replay", head);
      htmlEl("p", "status unavailable", "Replay unavailable for this lap.", fig);
    }
  }

  renderTable(root, data, opts);
}
