/**
 * Lap comparison chart (the `compare_laps` tool), shared by Claude's MCP App and the website.
 *
 * Two drivers' laps on one distance axis: speed traces, the running time gap shaded by who is
 * ahead, throttle, braking and corner markers, with a crosshair readout (pointer or arrow
 * keys). The Python tool sends the data as `structuredContent`.
 *
 * `render(root, data, opts)` draws the chart and knows nothing of the host:
 * `mcp-app/compare-laps.ts` feeds it tool results inside Claude, and the website wraps it in a
 * React component.
 */
import {
  contentWidth,
  crosshairKeys,
  finite,
  htmlEl,
  lapTime,
  linePath,
  nearestIndex,
  niceStep,
  svgEl,
  uid,
  type Series,
} from "./dom.ts";

export interface DriverLap {
  code: string;
  number?: string; // car number (not drawn)
  team: string;
  lap_number: number;
  lap_time_s: number;
  lap_class: string;
  compound: string;
  deleted: boolean;
  sectors_s?: (number | null)[]; // the three sector times; null where timing is missing (not drawn)
  speed: Series;
  throttle: Series;
  brake: Series;
}

// How each driver approached a turn: braked (brake_m says where), flat (full throttle, no
// braking), none (no braking of its own, e.g. still slow from the previous turn), unknown
// (the car data was frozen there).
type Braking = "braked" | "flat" | "none" | "unknown";

export interface Corner {
  label: string;
  number?: number; // the turn's number and letter, as in label (not drawn)
  letter?: string;
  apex_m: number;
  delta_s: number; // time B lost to A around this turn
  apex_speed: (number | null)[];
  brake_m: (number | null)[];
  braking: Braking[];
}

export interface Comparison {
  title?: string; // "AAA vs BBB" (not drawn)
  event: string;
  location: string;
  year: number;
  round?: number; // (not drawn)
  session: string;
  session_code?: string; // Q, SQ, SS, S or R (not drawn)
  date?: string; // YYYY-MM-DD (not drawn)
  track_length_m: number;
  gap_s: number; // lap time of B minus lap time of A
  distance_m: number[];
  delta_s: number[]; // time of B minus time of A at each distance: positive = A ahead
  drivers: DriverLap[];
  corners: Corner[];
  notes: string[];
}

const COLORS = ["var(--a)", "var(--b)"];
// Lap classes worth flagging next to the lap number (push and race laps are the norm).
const LAP_CLASS: Record<string, string> = {
  start: "opening lap",
  slow: "slow lap",
  yellow: "yellow flag",
  neutralised: "safety car",
  in: "in lap",
  out: "out lap",
};
const BRAKING_TEXT: Record<Braking, string> = {
  braked: "",
  flat: "flat",
  none: "none",
  unknown: "–",
};
const PAD = { left: 40, right: 12, top: 16, bottom: 20 };
const PANEL = { speed: 176, delta: 84, throttle: 44, brake: 7 };
const GAP = 12;

export interface RenderOptions {
  /** Whether the corner table starts open (the caller keeps it across redraws). */
  tableOpen?: boolean;
  /** Called when the reader opens or closes the corner table. */
  onTableToggle?: (open: boolean) => void;
}

export function isComparison(value: unknown): value is Comparison {
  const v = value as Partial<Comparison> | undefined;
  return Array.isArray(v?.distance_m) && Array.isArray(v?.delta_s) && v?.drivers?.length === 2;
}

/** Who is ahead by how much, for a gap of B minus A. */
function aheadText(gap: number, a: string, b: string): string {
  if (Math.abs(gap) < 0.0005) return "level";
  return `${gap > 0 ? a : b} ahead by ${Math.abs(gap).toFixed(3)} s`;
}

function renderHeader(root: HTMLElement, data: Comparison): void {
  const [a, b] = data.drivers;
  const header = htmlEl("header", "cmp-header", undefined, root);
  const titleRow = htmlEl("div", "title-row", undefined, header);
  htmlEl("div", "title", `${data.year} ${data.event} · ${data.session}`, titleRow);
  htmlEl(
    "div",
    "subtitle",
    `${data.location} · ${(data.track_length_m / 1000).toFixed(2)} km`,
    titleRow,
  );
  const drivers = htmlEl("div", "drivers", undefined, header);
  data.drivers.forEach((d, i) => {
    const chip = htmlEl("span", "driver", undefined, drivers);
    chip.style.setProperty("--swatch", COLORS[i]);
    htmlEl("span", "key", undefined, chip);
    htmlEl("strong", undefined, d.code, chip);
    const kind = LAP_CLASS[d.lap_class] ? ` · ${LAP_CLASS[d.lap_class]}` : "";
    const tyre = d.compound ? ` · ${d.compound.toLowerCase()}` : "";
    const deleted = d.deleted ? " · time deleted" : "";
    htmlEl("span", "meta", `${d.team} · lap ${d.lap_number}${kind}${tyre}${deleted}`, chip);
    htmlEl("span", "time", lapTime(d.lap_time_s), chip);
  });
  const gap = Math.abs(data.gap_s);
  const verdict =
    gap < 0.0005
      ? "Identical lap times"
      : `${data.gap_s > 0 ? a.code : b.code} faster by ${gap.toFixed(3)} s`;
  htmlEl("strong", "verdict", verdict, drivers);
}

function renderTable(root: HTMLElement, data: Comparison, opts: RenderOptions): void {
  if (!data.corners.length) return;
  const [a, b] = data.drivers;
  const details = htmlEl("details", undefined, undefined, root);
  details.open = opts.tableOpen ?? false;
  details.addEventListener("toggle", () => opts.onTableToggle?.(details.open));
  htmlEl("summary", undefined, "Corner by corner", details);
  const wrap = htmlEl("div", "table-wrap", undefined, details);
  const table = htmlEl("table", undefined, undefined, wrap);
  const head = htmlEl("tr", undefined, undefined, htmlEl("thead", undefined, undefined, table));
  const columns = [
    "Turn",
    "Time gained",
    `${a.code} apex`,
    `${b.code} apex`,
    `${a.code} brakes`,
    `${b.code} brakes`,
  ];
  for (const label of columns) htmlEl("th", undefined, label, head);
  const body = htmlEl("tbody", undefined, undefined, table);
  const kph = (v: number | null) => (v === null ? "–" : `${v.toFixed(0)} km/h`);
  const brakes = (c: Corner, k: number) => {
    const at = c.brake_m[k];
    return at === null ? BRAKING_TEXT[c.braking[k]] : `${(c.apex_m - at).toFixed(0)} m before`;
  };
  for (const c of data.corners) {
    const row = htmlEl("tr", undefined, undefined, body);
    const gained =
      Math.abs(c.delta_s) < 0.0005
        ? "level"
        : `${c.delta_s > 0 ? a.code : b.code} ${Math.abs(c.delta_s).toFixed(3)} s`;
    const cells = [
      c.label,
      gained,
      kph(c.apex_speed[0]),
      kph(c.apex_speed[1]),
      brakes(c, 0),
      brakes(c, 1),
    ];
    for (const cell of cells) htmlEl("td", undefined, cell, row);
  }
}

/** Draw the comparison into `root`, replacing what was there, at `root`'s current width. */
export function render(root: HTMLElement, data: Comparison, opts: RenderOptions = {}): void {
  const [a, b] = data.drivers;
  const width = contentWidth(root, 300);
  const plotW = width - PAD.left - PAD.right;
  const x = (d: number) => PAD.left + (d / data.track_length_m) * plotW;
  const xs = data.distance_m.map(x);

  const speedTop = PAD.top;
  const deltaTop = speedTop + PANEL.speed + GAP;
  const throttleTop = deltaTop + PANEL.delta + GAP;
  const brakeRows = [throttleTop + PANEL.throttle + GAP - 4];
  brakeRows.push(brakeRows[0] + PANEL.brake + 3);
  const plotBottom = brakeRows[1] + PANEL.brake;
  const height = plotBottom + PAD.bottom;

  const speeds = [...finite(a.speed), ...finite(b.speed)];
  const vLo = Math.max(0, Math.floor((Math.min(...speeds) - 5) / 50) * 50);
  const vHi = Math.ceil((Math.max(...speeds) + 5) / 50) * 50;
  const ySpeed = (v: number) => speedTop + PANEL.speed * (1 - (v - vLo) / (vHi - vLo));

  const dLo = Math.min(0, ...data.delta_s);
  const dHi = Math.max(0, ...data.delta_s);
  const dPad = Math.max(dHi - dLo, 0.2) * 0.15;
  let [dTop, dBottom] = [dHi + dPad, dLo - dPad];
  // Keep at least a quarter of the panel on each side of zero for the "ahead" labels.
  dBottom = Math.min(dBottom, -dTop / 3);
  dTop = Math.max(dTop, -dBottom / 3);
  const yDelta = (v: number) => deltaTop + (PANEL.delta * (dTop - v)) / (dTop - dBottom);
  const yThrottle = (t: number) => throttleTop + PANEL.throttle * (1 - t / 100);

  root.replaceChildren();
  renderHeader(root, data);
  const readout = htmlEl("div", "readout", undefined, root);
  readout.setAttribute("aria-live", "polite");
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    height,
    tabindex: 0,
    role: "img",
    "aria-label":
      `Lap comparison of ${a.code} and ${b.code}: speed, time gap, throttle and braking ` +
      "along the lap. Use the left and right arrow keys to read values.",
  });
  root.append(svg);

  const hline = (y: number, stroke: string) =>
    svgEl("line", { x1: PAD.left, x2: width - PAD.right, y1: y, y2: y, stroke }, svg);
  const label = (text: string, attrs: Record<string, string | number>) => {
    svgEl("text", attrs, svg).textContent = text;
  };

  // Corner markers, labelled where there's room.
  let lastLabel = -Infinity;
  for (const c of data.corners) {
    const px = x(c.apex_m);
    svgEl("line", { x1: px, x2: px, y1: speedTop, y2: plotBottom, stroke: "var(--grid)" }, svg);
    if (px - lastLabel >= 22) {
      label(c.label, { x: px, y: speedTop - 5, "text-anchor": "middle", "font-size": 9 });
      lastLabel = px;
    }
  }

  // Speed panel.
  const vStep = vHi - vLo > 200 ? 100 : 50;
  for (let v = Math.ceil(vLo / vStep) * vStep; v <= vHi; v += vStep) {
    hline(ySpeed(v), "var(--grid)");
    label(String(v), { x: PAD.left - 6, y: ySpeed(v) + 3, "text-anchor": "end" });
  }
  [b, a].forEach((d) => {
    const ys = d.speed.map((v) => (v === null ? null : ySpeed(v)));
    const color = COLORS[data.drivers.indexOf(d)];
    svgEl("path", { d: linePath(xs, ys), fill: "none", stroke: color, "stroke-width": 2 }, svg);
  });
  label("Speed km/h", { x: PAD.left + 4, y: speedTop + 10, class: "panel-title" });

  // Gap panel: above zero A is ahead, below zero B is.
  const dStep = niceStep(dTop - dBottom, 4);
  for (let v = Math.ceil(dBottom / dStep) * dStep; v <= dTop; v += dStep) {
    if (Math.abs(v) < 1e-9) continue;
    hline(yDelta(v), "var(--grid)");
    const text = Math.abs(v).toFixed(dStep < 0.1 ? 2 : 1);
    label(text, { x: PAD.left - 6, y: yDelta(v) + 3, "text-anchor": "end" });
  }
  const zeroY = yDelta(0);
  label("0 s", { x: PAD.left - 6, y: zeroY + 3, "text-anchor": "end" });
  const defs = svgEl("defs", {}, svg);
  const clipId = uid("gap"); // unique if a page shows several comparisons
  const clips = [
    [deltaTop, zeroY - deltaTop],
    [zeroY, deltaTop + PANEL.delta - zeroY],
  ];
  clips.forEach(([y, h], i) => {
    const clip = svgEl("clipPath", { id: `${clipId}-${i}` }, defs);
    svgEl("rect", { x: PAD.left, y, width: plotW, height: Math.max(h, 0) }, clip);
  });
  const deltaYs = data.delta_s.map(yDelta);
  const area = `${linePath(xs, deltaYs)}L${xs[xs.length - 1]},${zeroY}L${xs[0]},${zeroY}Z`;
  COLORS.forEach((color, i) => {
    const clip = `url(#${clipId}-${i})`;
    svgEl("path", { d: area, fill: color, "fill-opacity": 0.18, "clip-path": clip }, svg);
  });
  hline(zeroY, "var(--axis)");
  svgEl(
    "path",
    { d: linePath(xs, deltaYs), fill: "none", stroke: "var(--fg-muted)", "stroke-width": 1.5 },
    svg,
  );
  label(`▲ ${a.code} ahead`, { x: PAD.left + 4, y: deltaTop + 10, class: "panel-title" });
  label(`▼ ${b.code} ahead`, {
    x: PAD.left + 4,
    y: deltaTop + PANEL.delta - 4,
    class: "panel-title",
  });

  // Throttle panel.
  hline(yThrottle(0), "var(--axis)");
  hline(yThrottle(100), "var(--grid)");
  label("100", { x: PAD.left - 6, y: yThrottle(100) + 3, "text-anchor": "end" });
  label("0", { x: PAD.left - 6, y: yThrottle(0) + 3, "text-anchor": "end" });
  [b, a].forEach((d) => {
    const ys = d.throttle.map((t) => (t === null ? null : yThrottle(t)));
    const color = COLORS[data.drivers.indexOf(d)];
    svgEl("path", { d: linePath(xs, ys), fill: "none", stroke: color, "stroke-width": 1.5 }, svg);
  });
  label("Throttle %", { x: PAD.left + 4, y: throttleTop + 10, class: "panel-title" });

  // Braking strips, one per driver.
  data.drivers.forEach((d, i) => {
    const y = brakeRows[i];
    svgEl("rect", { x: PAD.left, y, width: plotW, height: PANEL.brake, fill: "var(--grid)" }, svg);
    label(d.code, { x: PAD.left - 6, y: y + PANEL.brake - 0.5, "text-anchor": "end", "font-size": 9 });
    let start = -1;
    d.brake.forEach((v, j) => {
      const on = v !== null && v >= 0.5;
      if (on && start < 0) start = j;
      if ((!on || j === d.brake.length - 1) && start >= 0) {
        const w = Math.max(xs[j] - xs[start], 1.5);
        svgEl("rect", { x: xs[start], y, width: w, height: PANEL.brake, fill: COLORS[i] }, svg);
        start = -1;
      }
    });
  });
  label("Brake", { x: PAD.left + 4, y: brakeRows[0] - 3, class: "panel-title" });

  // Distance axis.
  const kmStep = plotW / (data.track_length_m / 1000) < 45 ? 2 : 1;
  for (let km = 0; km * 1000 <= data.track_length_m; km += kmStep) {
    label(`${km} km`, { x: x(km * 1000), y: height - 5, "text-anchor": "middle" });
  }

  // Crosshair and readout.
  const cursor = svgEl(
    "line",
    { y1: speedTop, y2: plotBottom, stroke: "var(--fg)", "stroke-opacity": 0.45, visibility: "hidden" },
    svg,
  );
  const dot = (color: string) =>
    svgEl("circle", { r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }, svg);
  const dots = [dot(COLORS[0]), dot(COLORS[1]), dot("var(--fg-muted)")];

  const biggest = [...data.corners].sort((p, q) => Math.abs(q.delta_s) - Math.abs(p.delta_s))[0];
  const idle = () => {
    readout.replaceChildren();
    const swing = biggest
      ? `Biggest swing: ${biggest.label}, ${biggest.delta_s > 0 ? a.code : b.code} ` +
        `${Math.abs(biggest.delta_s).toFixed(3)} s`
      : "";
    htmlEl("span", undefined, swing, readout);
    htmlEl("span", undefined, "Hover or use ← → for values", readout);
  };

  const show = (index: number) => {
    const cx = xs[index];
    cursor.setAttribute("x1", String(cx));
    cursor.setAttribute("x2", String(cx));
    cursor.setAttribute("visibility", "visible");
    const values = [a.speed[index], b.speed[index]];
    values.forEach((v, k) => {
      dots[k].setAttribute("cx", String(cx));
      dots[k].setAttribute("cy", String(v === null ? 0 : ySpeed(v)));
      dots[k].setAttribute("visibility", v === null ? "hidden" : "visible");
    });
    dots[2].setAttribute("cx", String(cx));
    dots[2].setAttribute("cy", String(deltaYs[index]));
    dots[2].setAttribute("visibility", "visible");

    const d = data.distance_m[index];
    let near: Corner | undefined;
    for (const c of data.corners) {
      const off = Math.abs(c.apex_m - d);
      if (off <= 120 && (!near || off < Math.abs(near.apex_m - d))) near = c;
    }
    readout.replaceChildren();
    htmlEl("span", undefined, `${(d / 1000).toFixed(2)} km${near ? ` · ${near.label}` : ""}`, readout);
    data.drivers.forEach((driver, k) => {
      const item = htmlEl("span", undefined, undefined, readout);
      htmlEl("span", "key", undefined, item).style.setProperty("--swatch", COLORS[k]);
      const v = values[k];
      htmlEl("strong", undefined, v === null ? "–" : `${v.toFixed(0)} km/h`, item);
      item.append(` ${driver.code}`);
    });
    htmlEl("strong", undefined, aheadText(data.delta_s[index], a.code, b.code), readout);
  };
  const hide = () => {
    cursor.setAttribute("visibility", "hidden");
    dots.forEach((node) => node.setAttribute("visibility", "hidden"));
    idle();
  };

  crosshairKeys(
    svg,
    data.distance_m.length,
    (i) => (i < 0 ? hide() : show(i)),
    (px) => nearestIndex(data.distance_m, ((px - PAD.left) / plotW) * data.track_length_m),
  );
  idle();

  renderTable(root, data, opts);
  if (data.notes.length) htmlEl("p", "notes", data.notes.join(" "), root);
}
