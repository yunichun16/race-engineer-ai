/**
 * Two drivers' cornering styles over a season (the `compare_driving_styles` tool), shared by
 * Claude's MCP App and the website.
 *
 * Two panels: the differences between the drivers by corner type (braking point, minimum
 * speed, throttle and so on: a dot per corner type with its 95% interval, solid where the
 * interval excludes zero), and a style map of the season's drivers on the plane of their
 * learned style embeddings. The Python tool sends the data as `structuredContent`.
 *
 * `render(root, data, opts)` draws the page and knows nothing of the host:
 * `mcp-app/compare-styles.ts` feeds it tool results inside Claude and keeps the reader's
 * choices across redraws, and the website wraps it in a React component.
 */
import { crosshairKeys, headingTag, htmlEl, svgEl, type Crosshair, type HeadingLevel } from "./dom.ts";

export type StyleKind = "all" | "quali" | "race";
export type CornerType = "all" | "slow" | "medium" | "fast";
/** Which embedding plane the style map shows. */
export type Plane = "style" | "car";

export interface StyleMetric {
  name: string;
  label: string;
  unit: string;
  digits: number;
  more: string; // what a positive difference means for driver_a, e.g. "brakes later"
  less: string;
}

/** One difference, driver_a minus driver_b, with its 95% interval. */
export interface StyleRow {
  kind: StyleKind;
  corner_type: CornerType;
  metric: string;
  // NaN in pandas where no corner counts (corners 0), which arrives as null.
  diff: number | null;
  lo: number | null; // null below 4 events: no interval, and clear is false
  hi: number | null;
  p: number | null;
  corners: number;
  events: number;
  clear: boolean;
}

export interface StylePoint {
  driver: string;
  team: string;
  events: number;
  car: [number, number]; // field-relative centroid on its 2-D plane (car and driver together)
  style: [number, number]; // difference from the teammate on its 2-D plane (the driver alone)
  role: "a" | "b" | "other";
}

export interface StyleAxis {
  metric: string;
  label: string;
  r_pc1: number; // the metric's correlation with the style plane's axes: the arrow's direction
  r_pc2: number;
  R: number; // how much of the metric the plane holds, 0 to 1: the arrow's length
}

export interface StyleChart {
  year: number;
  driver_a: string;
  driver_b: string;
  teams: [string, string];
  teammates: boolean;
  events: number;
  corners: number;
  metrics: StyleMetric[];
  table: StyleRow[];
  by_event: { round: number; event: string; metric: string; diff: number; corners: number }[];
  embedding: Partial<
    Record<
      | "cos_centroids"
      | "cos_style"
      | "season_cos_teammate"
      | "season_cos_field"
      | "style_consistency"
      | "consistency_p"
      | "consistency_null95",
      number
    >
  >;
  map: {
    points: StylePoint[];
    axes: StyleAxis[];
    explained: { car: [number, number] | null; style: [number, number] | null };
  } | null;
  notes: string[];
}

export interface RenderOptions {
  /** Which sessions the differences come from (the caller keeps it across redraws). */
  kind?: StyleKind;
  onKindChange?: (kind: StyleKind) => void;
  /** Which plane the style map shows. */
  plane?: Plane;
  onPlaneChange?: (plane: Plane) => void;
  /** Whether the tables start open. */
  tableOpen?: boolean;
  onTableToggle?: (open: boolean) => void;
  /**
   * A click, Enter or Space on a style-map point: the website picks that driver to compare
   * with driver_a (never called for driver_a). Without it the points only name the drivers.
   */
  onPointSelect?: (driver: string) => void;
  /**
   * The level of the two section heads, "Differences by corner type" and "Style map" (2 when
   * missing, as on Claude's page). A page passes the level below the heading the chart sits under.
   */
  headingLevel?: HeadingLevel;
}

const COLUMNS: { type: CornerType; label: string; text: string }[] = [
  { type: "slow", label: "Slow", text: "slow corners" },
  { type: "medium", label: "Medium", text: "medium corners" },
  { type: "fast", label: "Fast", text: "fast corners" },
  { type: "all", label: "All corners", text: "all corners" },
];
const KINDS: { kind: StyleKind; label: string; text: string }[] = [
  { kind: "all", label: "All", text: "all sessions" },
  { kind: "quali", label: "Qualifying", text: "qualifying" },
  { kind: "race", label: "Race", text: "races" },
];
const PLANES: { value: Plane; label: string }[] = [
  { value: "style", label: "Style" },
  { value: "car", label: "Car" },
];
const MIN_INTERVAL_EVENTS = 4; // inference/style.py: fewer events give no interval
const MIN_EVENTS = 5; // inference/style.py: fewer events don't take part in fitting the map
const MIN_ARROW_R = 0.3; // weaker measures get no arrow: the plane barely holds them
const ROW_H = 30;
const CELL_GAP = 10;
const ALL_GAP = 22; // a wider gap, with a rule, before the all-corners column
const HIT_PX = 24; // pointer distance that still picks a dot on the map

let uid = 0; // radio group names, unique per render (M6 may put several charts on a page)

/** The keys `render` reads without a fallback (by_event, embedding, map and notes are optional). */
export function isStyleChart(value: unknown): value is StyleChart {
  const v = value as Partial<StyleChart> | null | undefined;
  return (
    typeof v?.driver_a === "string" &&
    typeof v?.driver_b === "string" &&
    typeof v?.year === "number" &&
    typeof v?.events === "number" &&
    typeof v?.corners === "number" &&
    Array.isArray(v?.teams) &&
    Array.isArray(v?.table) &&
    Array.isArray(v?.metrics) &&
    v.metrics.every(
      (m) =>
        typeof m?.name === "string" &&
        typeof m?.label === "string" &&
        typeof m?.unit === "string" &&
        typeof m?.more === "string" &&
        typeof m?.less === "string",
    )
  );
}

function isNum(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Whether there is a map to draw (a points list, even an empty one). */
function hasMap(data: StyleChart): data is StyleChart & { map: NonNullable<StyleChart["map"]> } {
  return Array.isArray(data.map?.points);
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A signed value with a typographic minus ("+3.2", "−0.40", "0.0" when it rounds to zero). */
function signed(value: number, digits: number): string {
  const size = Math.abs(value).toFixed(digits);
  if (Number(size) === 0) return size;
  return `${value > 0 ? "+" : "−"}${size}`;
}

function pText(p: number): string {
  return p < 0.001 ? "p < 0.001" : p < 0.01 ? `p = ${p.toFixed(3)}` : `p = ${p.toFixed(2)}`;
}

function eventsText(n: number): string {
  return n === 1 ? "1 event" : `${n} events`;
}

/** A row of native radio buttons styled as a segmented control (arrow keys come for free). */
function radioGroup<T extends string>(
  parent: HTMLElement,
  label: string,
  options: { value: T; label: string; disabled?: boolean }[],
  value: T,
  onChange: (value: T) => void,
): void {
  const group = htmlEl("div", "seg", undefined, parent);
  group.setAttribute("role", "radiogroup");
  group.setAttribute("aria-label", label);
  const name = `cs-${label.toLowerCase().replace(/\W+/g, "-")}-${++uid}`;
  for (const option of options) {
    const item = htmlEl("label", undefined, undefined, group);
    const input = htmlEl("input", undefined, undefined, item);
    input.type = "radio";
    input.name = name;
    input.value = option.value;
    input.checked = option.value === value;
    input.disabled = option.disabled ?? false;
    input.addEventListener("change", () => input.checked && onChange(option.value));
    htmlEl("span", undefined, option.label, item);
  }
}

function renderHeader(root: HTMLElement, data: StyleChart): void {
  const header = htmlEl("header", "cs-header", undefined, root);
  htmlEl("div", "title", `${data.driver_a} vs ${data.driver_b} · ${data.year}`, header);
  const drivers = htmlEl("div", "drivers", undefined, header);
  [data.driver_a, data.driver_b].forEach((code, i) => {
    const chip = htmlEl("span", "driver", undefined, drivers);
    const swatch = htmlEl("span", "key", undefined, chip);
    swatch.style.setProperty("--swatch", i ? "var(--b)" : "var(--a)");
    htmlEl("strong", undefined, code, chip);
    htmlEl("span", "meta", data.teams[i] ?? "", chip);
  });
  const meta = htmlEl("div", "meta-line", undefined, header);
  if (data.teammates) {
    htmlEl("span", undefined, "Teammates: same car", meta);
  } else {
    // ︎ asks for the text glyph, not the emoji.
    htmlEl("span", "warn", "⚠︎ Different cars: car and driver mixed", meta);
  }
  const corners = data.corners.toLocaleString("en");
  htmlEl("span", undefined, `${eventsText(data.events)} · ${corners} corners`, meta);
}

// --- Panel 1: differences by corner type ------------------------------------------------------

interface Cell {
  x: number;
  w: number;
}

/** The four cells of a row (slow, medium, fast, all) across `width` px. */
function cellLayout(width: number): Cell[] {
  const w = (width - 2 * CELL_GAP - ALL_GAP) / 4;
  const x = (i: number) => (i < 3 ? i * (w + CELL_GAP) : 3 * w + 2 * CELL_GAP + ALL_GAP);
  return COLUMNS.map((_, i) => ({ x: x(i), w }));
}

function findRow(data: StyleChart, kind: StyleKind, type: CornerType, metric: string) {
  return data.table.find((r) => r.kind === kind && r.corner_type === type && r.metric === metric);
}

function hasValue(row: StyleRow | undefined): row is StyleRow & { diff: number } {
  return row !== undefined && row.corners > 0 && isNum(row.diff);
}

/** "AAA brakes later than BBB by 10.2 m", the size and interval in the direction of the words. */
function describe(data: StyleChart, m: StyleMetric, row: StyleRow & { diff: number }) {
  const size = Math.abs(row.diff);
  const sizeText = size.toFixed(m.digits);
  const level = Number(sizeText) === 0;
  const phrase = row.diff > 0 ? m.more : m.less;
  const main = level
    ? `${data.driver_a} and ${data.driver_b} are level`
    : `${data.driver_a} ${phrase} than ${data.driver_b} by ${sizeText} ${m.unit}`;
  if (!isNum(row.lo) || !isNum(row.hi)) {
    return { main, interval: `no interval (fewer than ${MIN_INTERVAL_EVENTS} events)` };
  }
  // A level result keeps the interval as A minus B, the way the cell draws it.
  const [lo, hi] = row.diff >= 0 || level ? [row.lo, row.hi] : [-row.hi, -row.lo];
  const range = `95% interval ${signed(lo, m.digits)} to ${signed(hi, m.digits)} ${m.unit}`;
  return { main, interval: `${range} · ${row.clear ? "clear" : "not clear (includes zero)"}` };
}

/** The idle readout: the clearest difference in these sessions, or why there is none. */
function idleText(data: StyleChart, kind: StyleKind): string {
  let best: { row: StyleRow & { diff: number }; m: StyleMetric } | undefined;
  for (const m of data.metrics) {
    for (const col of COLUMNS) {
      const row = findRow(data, kind, col.type, m.name);
      if (!hasValue(row) || !row.clear) continue;
      if (!best || (row.p ?? 1) < (best.row.p ?? 1)) best = { row, m };
    }
  }
  if (best) {
    const col = COLUMNS.find((c) => c.type === best.row.corner_type)!;
    const where = col.type === "all" ? "" : ` in ${col.text}`;
    return `Clearest: ${describe(data, best.m, best.row).main}${where}`;
  }
  const rows = data.table.filter((r) => r.kind === kind && hasValue(r));
  if (rows.length && rows.every((r) => !isNum(r.lo))) {
    return `Fewer than ${MIN_INTERVAL_EVENTS} events: no intervals, so nothing is called clear`;
  }
  return "No clear difference";
}

function renderDiffGrid(
  grid: HTMLElement,
  readout: HTMLElement,
  data: StyleChart,
  kind: StyleKind,
): void {
  grid.replaceChildren();
  const a = data.driver_a;
  const kindText = KINDS.find((k) => k.kind === kind)!.text;

  // Column headings, drawn in the rows' geometry so they line up at every width.
  const headRow = htmlEl("div", "metric metric-head", undefined, grid);
  htmlEl("div", "m-label", undefined, headRow);
  const headPlot = htmlEl("div", "m-plot", undefined, headRow);
  const width = Math.max(headPlot.clientWidth, 240);
  const cells = cellLayout(width);
  const rule = cells[3].x - ALL_GAP / 2;
  const headAttrs = { viewBox: `0 0 ${width} 14`, height: 14, "aria-hidden": "true" };
  const headSvg = svgEl("svg", headAttrs, headPlot);
  COLUMNS.forEach((col, i) => {
    const x = cells[i].x + cells[i].w / 2;
    const attrs = { x, y: 10, "text-anchor": "middle", class: "col-head" };
    svgEl("text", attrs, headSvg).textContent = col.label;
  });
  const showUnits = cells[0].w >= 84;

  const idle = () => {
    readout.replaceChildren();
    htmlEl("span", undefined, idleText(data, kind), readout);
    htmlEl("span", undefined, "Hover or use the arrow keys for intervals", readout);
  };

  const svgs: SVGSVGElement[] = [];
  const crosshairs: Crosshair[] = [];
  const marks: SVGRectElement[][] = [];
  let shown: [number, number] | null = null;

  const showCell = (r: number, i: number) => {
    const m = data.metrics[r];
    const col = COLUMNS[i];
    const row = findRow(data, kind, col.type, m.name);
    readout.replaceChildren();
    const where = `${capitalise(m.label)} · ${col.text}${kind === "all" ? "" : ` · ${kindText}`}`;
    htmlEl("span", undefined, where, readout);
    if (!hasValue(row)) {
      htmlEl("strong", undefined, "No corners to compare", readout);
      return;
    }
    const text = describe(data, m, row);
    htmlEl("strong", undefined, text.main, readout);
    if (text.interval) htmlEl("span", undefined, text.interval, readout);
    const p = isNum(row.p) ? ` · ${pText(row.p)}` : "";
    htmlEl("span", undefined, `${row.corners} corners · ${eventsText(row.events)}${p}`, readout);
  };

  data.metrics.forEach((m, r) => {
    const row = htmlEl("div", "metric", undefined, grid);
    const label = htmlEl("div", "m-label", capitalise(m.label), row);
    htmlEl("span", "unit", ` ${m.unit}`, label);
    const plot = htmlEl("div", "m-plot", undefined, row);
    const svg = svgEl(
      "svg",
      {
        viewBox: `0 0 ${width} ${ROW_H}`,
        height: ROW_H,
        tabindex: r === 0 ? 0 : -1,
        role: "img",
        "aria-label":
          `${capitalise(m.label)}, ${a} minus ${data.driver_b} by corner type. Left and right ` +
          "arrow keys read each corner type; up and down move to the other measures.",
      },
      plot,
    );
    svgs.push(svg);
    const ends = htmlEl("div", "ends", undefined, plot);
    // No-break spaces keep each arrow with its words when the text wraps.
    htmlEl("span", undefined, `←\u00a0${a} ${m.less}`, ends);
    htmlEl("span", undefined, `${m.more}\u00a0→`, ends);

    // One scale per measure, the same for every session kind (switching doesn't rescale),
    // symmetric so zero sits in the middle of each cell.
    const values = data.table
      .filter((t) => t.metric === m.name && hasValue(t))
      .flatMap((t) => [t.diff, t.lo, t.hi])
      .filter(isNum)
      .map(Math.abs);
    const span = Math.max(...values, 0) * 1.08 || 1;

    svgEl("line", { x1: rule, x2: rule, y1: 2, y2: ROW_H - 2, stroke: "var(--grid)" }, svg);
    marks.push(
      cells.map((cell) =>
        svgEl("rect", {
          x: cell.x - 3,
          y: 0,
          width: cell.w + 6,
          height: ROW_H,
          rx: 4,
          class: "cell-mark",
          visibility: "hidden",
        }, svg),
      ),
    );
    COLUMNS.forEach((col, i) => {
      const cell = cells[i];
      const cx = cell.x + cell.w / 2;
      const half = cell.w / 2 - 5;
      const sx = (v: number) => cx + (v / span) * half;
      svgEl("line", { x1: cx, x2: cx, y1: 14, y2: ROW_H - 1, stroke: "var(--axis)" }, svg);
      const cellRow = findRow(data, kind, col.type, m.name);
      if (!hasValue(cellRow)) {
        const none = svgEl("text", { x: cx, y: 11, "text-anchor": "middle", class: "none" }, svg);
        none.textContent = "no corners";
        return;
      }
      const state = cellRow.clear ? "clear" : "unclear";
      if (isNum(cellRow.lo) && isNum(cellRow.hi)) {
        const [x1, x2] = [sx(cellRow.lo), sx(cellRow.hi)];
        svgEl("line", { x1, x2, y1: 21, y2: 21, class: `whisker ${state}` }, svg);
      }
      svgEl("circle", { cx: sx(cellRow.diff), cy: 21, r: 4.5, class: `dot ${state}` }, svg);
      const text = `${signed(cellRow.diff, m.digits)}${showUnits ? ` ${m.unit}` : ""}`;
      const tw = text.length * 6;
      const tx = Math.min(Math.max(sx(cellRow.diff), cell.x + tw / 2), cell.x + cell.w - tw / 2);
      const attrs = { x: tx, y: 10, "text-anchor": "middle", class: `value ${state}` };
      svgEl("text", attrs, svg).textContent = text;
    });

    const onMove = (i: number) => {
      if (i < 0) {
        if (shown?.[0] !== r) return;
        marks[r][shown[1]].setAttribute("visibility", "hidden");
        shown = null;
        idle();
        return;
      }
      if (shown) marks[shown[0]][shown[1]].setAttribute("visibility", "hidden");
      marks[r][i].setAttribute("visibility", "visible");
      shown = [r, i];
      showCell(r, i);
    };
    const column = (x: number) => {
      let best = 0;
      cells.forEach((cell, i) => {
        const off = Math.abs(cell.x + cell.w / 2 - x);
        if (off < Math.abs(cells[best].x + cells[best].w / 2 - x)) best = i;
      });
      return best;
    };
    crosshairs.push(crosshairKeys(svg, COLUMNS.length, onMove, column));

    // One tab stop for the whole grid: the focused row holds it, ↑/↓ hand it on.
    svg.addEventListener("focus", () => {
      svgs.forEach((s, k) => s.setAttribute("tabindex", k === r ? "0" : "-1"));
    });
    svg.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      const next = r + (event.key === "ArrowUp" ? -1 : 1);
      if (next < 0 || next >= svgs.length) return;
      const i = Math.max(crosshairs[r].index, 0);
      svgs[next].focus();
      crosshairs[next].show(i);
    });
  });
  idle();
}

// --- Panel 2: the style map -----------------------------------------------------------------

function mapCaption(data: StyleChart, plane: Plane): string {
  const explained = data.map?.explained?.[plane];
  const pct = explained?.every(isNum) ? Math.round((explained[0] + explained[1]) * 100) : null;
  const share = pct === null ? "" : ` The ${plane} plane explains ${pct}% of the variance.`;
  if (plane === "car") {
    return (
      "Car plane: each driver's difference from the field on the same corners, car and driver " +
      `together, so teammates sit close.${share}`
    );
  }
  const weak = (data.map?.axes ?? []).filter((x) => x.R < MIN_ARROW_R).map((x) => x.label);
  const arrows = data.map?.axes?.length
    ? " Arrows point towards more of a measure; the longer the arrow, the better the plane holds it" +
      (weak.length ? ` (not drawn: ${weak.join(", ")}).` : ".")
    : "";
  return (
    "Style plane: each driver's difference from their teammate on shared corners, the driver " +
    `alone, so teammates mirror each other through the centre.${share}${arrows}`
  );
}

function renderMap(
  box: HTMLElement,
  readout: HTMLElement,
  data: StyleChart,
  plane: Plane,
  onPointSelect?: (driver: string) => void,
): void {
  box.replaceChildren();
  const map = data.map!;
  const points = map.points
    .filter((p) => Array.isArray(p?.[plane]) && isNum(p[plane][0]) && isNum(p[plane][1]))
    .sort((p, q) => p[plane][0] - q[plane][0]); // ←/→ step left to right
  const idle = () => {
    readout.replaceChildren();
    htmlEl("span", undefined, "Hover a dot or use ← → to name the drivers", readout);
    if (onPointSelect) {
      htmlEl("span", undefined, `Select a driver to compare them with ${data.driver_a}`, readout);
    }
  };
  if (!points.length) {
    htmlEl("p", "status", `No drivers to place on the ${plane} plane.`, box);
    readout.replaceChildren();
    return;
  }

  const width = Math.max(box.clientWidth, 240);
  const height = Math.round(Math.min(340, Math.max(240, width * 0.58)));
  const pad = { x: 34, y: 20 };
  const xs = points.map((p) => p[plane][0]);
  const ys = points.map((p) => p[plane][1]);
  const [x0, x1] = [Math.min(0, ...xs), Math.max(0, ...xs)];
  const [y0, y1] = [Math.min(0, ...ys), Math.max(0, ...ys)];
  // One scale for both directions: distances and angles on the plane mean something.
  const k = Math.min((width - 2 * pad.x) / (x1 - x0 || 1), (height - 2 * pad.y) / (y1 - y0 || 1));
  const px = (x: number) => width / 2 + k * (x - (x0 + x1) / 2);
  const py = (y: number) => height / 2 - k * (y - (y0 + y1) / 2);
  const at = points.map((p) => [px(p[plane][0]), py(p[plane][1])] as const);

  const svg = svgEl(
    "svg",
    {
      viewBox: `0 0 ${width} ${height}`,
      height,
      tabindex: 0,
      role: "img",
      "aria-label":
        `Style map of the ${data.year} drivers on the ${plane} plane, ${data.driver_a} and ` +
        `${data.driver_b} highlighted. Left and right arrow keys step through the drivers from ` +
        "left to right." +
        (onPointSelect ? ` Enter selects a driver to compare with ${data.driver_a}.` : ""),
    },
    box,
  );
  if (onPointSelect) svg.classList.add("selectable");
  const [cx, cy] = [px(0), py(0)];
  svgEl("line", { x1: 0, x2: width, y1: cy, y2: cy, stroke: "var(--grid)" }, svg);
  svgEl("line", { x1: cx, x2: cx, y1: 0, y2: height, stroke: "var(--grid)" }, svg);
  // Layers, bottom to top: arrows, teammate lines, other drivers, arrow labels (over the grey
  // dots, with a halo), A and B with their labels, then the hover ring.
  const arrows = svgEl("g", {}, svg);
  const teamLines = svgEl("g", { class: "team-lines" }, svg);
  const others = svgEl("g", {}, svg);
  const arrowLabels = svgEl("g", {}, svg);
  const top = svgEl("g", {}, svg);

  type Box = [number, number, number, number]; // left, top, right, bottom
  const placed: Box[] = [];
  const overlaps = (b: Box) =>
    placed.some((o) => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]);
  const label = (
    parent: Element,
    text: string,
    x: number,
    y: number,
    side: "start" | "end" | "middle",
    cls: string,
  ) => {
    svgEl("text", { x, y, "text-anchor": side, class: cls }, parent).textContent = text;
  };
  const textBox = (x: number, y: number, w: number, side: "start" | "end" | "middle"): Box => {
    const left = side === "start" ? x : side === "end" ? x - w : x - w / 2;
    return [left - 2, y - 10, left + w + 2, y + 3];
  };

  // Thin lines join teammates: always for A's and B's teams, and for a driver under the pointer.
  const joinTeam = (parent: Element, i: number, all = false) => {
    at.forEach(([x, y], j) => {
      if (j === i || points[j].team !== points[i].team) return;
      if (all && j < i) return; // each pair once
      svgEl("line", { x1: at[i][0], y1: at[i][1], x2: x, y2: y }, parent);
    });
  };
  const highlighted = points.map((p, i) => (p.role === "other" ? -1 : i)).filter((i) => i >= 0);
  const teams = new Set(highlighted.map((i) => points[i].team));
  points.forEach((p, j) => teams.has(p.team) && joinTeam(teamLines, j, true));

  points.forEach((p, i) => {
    if (p.role !== "other") return;
    svgEl("circle", { cx: at[i][0], cy: at[i][1], r: 4, class: "pt other" }, others);
  });

  // A and B on top, each labelled where its name covers the fewest other dots and labels and
  // stays inside the plot. They're placed first: arrow labels avoid them.
  highlighted.forEach((i) => {
    const p = points[i];
    const [x, y] = at[i];
    svgEl("circle", { cx: x, cy: y, r: 5.5, class: `pt ${p.role}` }, top);
  });
  const dotsUnder = (b: Box, skip: number) =>
    at.filter(
      ([qx, qy], j) => j !== skip && qx + 5 > b[0] && qx - 5 < b[2] && qy + 5 > b[1] && qy - 5 < b[3],
    ).length;
  highlighted.forEach((i) => {
    const [x, y] = at[i];
    const w = points[i].driver.length * 7.5;
    // Right of the dot, else left, above or below: the first that covers the least.
    const spots = (
      [
        ["start", x + 8, y + 4],
        ["end", x - 8, y + 4],
        ["middle", x, y - 9],
        ["middle", x, y + 17],
      ] as const
    ).map(([side, lx, ly]) => {
      const b = textBox(lx, ly, w, side);
      const off = b[0] < 0 || b[2] > width || b[1] < 0 || b[3] > height ? 100 : 0;
      return { side, lx, ly, b, cost: off + 10 * Number(overlaps(b)) + dotsUnder(b, i) };
    });
    const best = spots.reduce((p, q) => (q.cost < p.cost ? q : p));
    label(top, points[i].driver, best.lx, best.ly, best.side, "pt-label");
    placed.push(best.b, [x - 7, y - 7, x + 7, y + 7]);
  });

  // What the style plane means: each measure as an arrow from the centre, labelled past its
  // tip, or a little further out (or nudged up or down) where that would cover another label.
  if (plane === "style") {
    const reach = 0.85 * Math.min(cx, width - cx, cy, height - cy);
    for (const axis of map.axes ?? []) {
      const len = Math.hypot(axis.r_pc1, axis.r_pc2);
      if (axis.R < MIN_ARROW_R || !len) continue;
      const [ux, uy] = [axis.r_pc1 / len, -axis.r_pc2 / len];
      const [tx, ty] = [cx + ux * axis.R * reach, cy + uy * axis.R * reach];
      svgEl("line", { x1: cx, y1: cy, x2: tx, y2: ty, class: "arrow" }, arrows);
      const [bx, by] = [tx - ux * 6, ty - uy * 6];
      const head = `${tx},${ty} ${bx - uy * 3},${by + ux * 3} ${bx + uy * 3},${by - ux * 3}`;
      svgEl("polygon", { points: head, class: "arrow-head" }, arrows);
      const side = ux > 0.35 ? "start" : ux < -0.35 ? "end" : "middle";
      const w = axis.label.length * 6; // 10 px text, generous for wide letters
      // Each spot is slid back inside the plot where the text would run off an edge.
      const inside = (x: number, y: number): readonly [number, number] => {
        const [left, upper, right, lower] = textBox(x, y, w, side);
        const dx = left < 0 ? -left : right > width ? width - right : 0;
        const dy = upper < 0 ? -upper : lower > height ? height - lower : 0;
        return [x + dx, y + dy];
      };
      const [lx, ly] = [tx + ux * 5, ty + uy * 5 + (uy > 0 ? 9 : uy < -0.35 ? -2 : 3)];
      const candidates = [0, 8, 16, 24].flatMap((out) =>
        [0, 11, -11].map((dy) => inside(lx + ux * out, ly + uy * out + dy)),
      );
      // Clear of other labels and of every dot if possible, else clear of labels only.
      const free = ([x, y]: readonly [number, number]) => !overlaps(textBox(x, y, w, side));
      const clear = (c: readonly [number, number]) =>
        free(c) && !dotsUnder(textBox(c[0], c[1], w, side), -1);
      const [fx, fy] = candidates.find(clear) ?? candidates.find(free) ?? candidates[0];
      placed.push(textBox(fx, fy, w, side));
      label(arrowLabels, axis.label, fx, fy, side, "arrow-label");
    }
  }

  // Hover and focus: a ring, the name and the driver's teammates.
  const hover = svgEl("g", { class: "hover", visibility: "hidden" }, svg);
  const show = (i: number) => {
    hover.replaceChildren();
    hover.setAttribute("visibility", "visible");
    const p = points[i];
    const [x, y] = at[i];
    const lines = svgEl("g", { class: "team-lines" }, hover);
    joinTeam(lines, i);
    svgEl("circle", { cx: x, cy: y, r: p.role === "other" ? 7 : 8.5, class: "pick-ring" }, hover);
    if (p.role === "other") {
      const side = x > width - 40 ? "end" : "start";
      label(hover, p.driver, side === "start" ? x + 9 : x - 9, y + 4, side, "pt-label");
    }
    readout.replaceChildren();
    htmlEl("strong", undefined, p.driver, readout);
    htmlEl("span", undefined, p.team, readout);
    const few = p.events < MIN_EVENTS ? " (too few to fit the map; placed on it)" : "";
    htmlEl("span", undefined, `${eventsText(p.events)}${few}`, readout);
    const mates = points
      .filter((q) => q.team === p.team && q.driver !== p.driver)
      .map((q) => q.driver);
    if (mates.length) htmlEl("span", undefined, `teammate ${mates.join(", ")}`, readout);
  };
  const hide = () => {
    hover.setAttribute("visibility", "hidden");
    idle();
  };
  const crosshair = crosshairKeys(svg, points.length, (i) => (i < 0 ? hide() : show(i)));

  // The pointer picks the nearest dot within HIT_PX, not only the painted pixels.
  const nearest = (event: MouseEvent) => {
    const rect = svg.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * width;
    const y = ((event.clientY - rect.top) / rect.height) * height;
    let best = -1;
    let bestD = HIT_PX;
    at.forEach(([qx, qy], i) => {
      const d = Math.hypot(qx - x, qy - y);
      if (d <= bestD) [best, bestD] = [i, d];
    });
    return best;
  };
  const pick = (event: PointerEvent) => {
    const best = nearest(event);
    if (best >= 0) crosshair.show(best);
    else if (document.activeElement !== svg) crosshair.hide();
  };
  const select = (i: number) => {
    if (i >= 0 && points[i].role !== "a") onPointSelect?.(points[i].driver);
  };
  if (onPointSelect) {
    svg.addEventListener("click", (event) => select(nearest(event)));
    svg.addEventListener("keydown", (event) => {
      if ((event.key !== "Enter" && event.key !== " ") || crosshair.index < 0) return;
      event.preventDefault();
      select(crosshair.index);
    });
  }
  svg.addEventListener("pointermove", pick);
  svg.addEventListener("pointerdown", pick);
  svg.addEventListener("pointerleave", () => {
    if (document.activeElement !== svg) crosshair.hide();
  });
  idle();
}

function renderLegend(parent: HTMLElement, data: StyleChart): void {
  const legend = htmlEl("div", "legend", undefined, parent);
  const item = (cls: string, text: string) => {
    const span = htmlEl("span", undefined, undefined, legend);
    htmlEl("span", cls, undefined, span);
    span.append(text);
  };
  item("swatch a", data.driver_a);
  item("swatch b", data.driver_b);
  item("swatch other", "other drivers");
  item("line-key", "teammates");
}

// --- Embedding line, notes and tables -------------------------------------------------------

function renderEmbedding(root: HTMLElement, data: StyleChart): void {
  const e = data.embedding ?? {};
  if (!isNum(e.cos_centroids)) return;
  const box = htmlEl("div", "embedding", undefined, root);
  const line = htmlEl("p", undefined, undefined, box);
  line.append("Style embedding similarity ");
  htmlEl("strong", undefined, signed(e.cos_centroids, 2), line);
  const context = [
    isNum(e.season_cos_teammate) ? `typical teammates ${signed(e.season_cos_teammate, 2)}` : "",
    isNum(e.season_cos_field) ? `drivers of different teams ${signed(e.season_cos_field, 2)}` : "",
  ].filter(Boolean);
  if (context.length) line.append(` (${context.join("; ")})`);
  line.append(data.teammates ? ": mostly the shared car." : ": car and driver together.");

  if (!data.teammates && isNum(e.cos_style)) {
    const style = htmlEl("p", undefined, undefined, box);
    style.append("How each differs from their own teammate: cosine ");
    htmlEl("strong", undefined, signed(e.cos_style, 2), style);
    style.append(
      e.cos_style > 0.3 ? " (alike)." : e.cos_style < -0.3 ? " (opposite ways)." : " (unrelated).",
    );
  }
  const c = e.style_consistency;
  const p = e.consistency_p;
  if (data.teammates && isNum(c) && isNum(p)) {
    const noise = isNum(e.consistency_null95)
      ? `; noise alone reaches ${signed(e.consistency_null95, 2)} one time in 20`
      : "";
    const verdict =
      p < 0.05
        ? "points the same way in odd and even rounds: a steady difference in style"
        : c > 0
          ? "isn't clearly steady between odd and even rounds"
          : "points different ways in odd and even rounds";
    const steady = htmlEl("p", undefined, undefined, box);
    steady.append(`Their style difference ${verdict} (cosine `);
    htmlEl("strong", undefined, signed(c, 2), steady);
    steady.append(`, ${pText(p)}${noise}).`);
  }
}

function renderTables(wrap: HTMLElement, data: StyleChart, kind: StyleKind): void {
  wrap.replaceChildren();
  const kindText = KINDS.find((k) => k.kind === kind)!.text;
  htmlEl(
    "p",
    "by-event-caption",
    `${data.driver_a} minus ${data.driver_b}, ${kindText}; 95% interval in brackets; * clear.`,
    wrap,
  );
  const scroll = htmlEl("div", "table-wrap", undefined, wrap);
  const table = htmlEl("table", undefined, undefined, scroll);
  const head = htmlEl("tr", undefined, undefined, htmlEl("thead", undefined, undefined, table));
  for (const label of ["Measure", ...COLUMNS.map((c) => c.label)]) {
    htmlEl("th", undefined, label, head);
  }
  const body = htmlEl("tbody", undefined, undefined, table);
  for (const m of data.metrics) {
    const tr = htmlEl("tr", undefined, undefined, body);
    htmlEl("td", undefined, `${capitalise(m.label)} (${m.unit})`, tr);
    for (const col of COLUMNS) {
      const row = findRow(data, kind, col.type, m.name);
      if (!hasValue(row)) {
        htmlEl("td", "muted", "–", tr);
        continue;
      }
      const td = htmlEl("td", row.clear ? "clear" : undefined, signed(row.diff, m.digits), tr);
      if (row.clear) td.append(" *");
      if (isNum(row.lo) && isNum(row.hi)) {
        const interval = `[${signed(row.lo, m.digits)}, ${signed(row.hi, m.digits)}]`;
        htmlEl("span", "ci", interval, td);
      }
    }
  }

  const byEvent = data.by_event ?? [];
  const events = [...new Map(byEvent.map((e) => [e.round, e.event])).entries()].sort(
    (p, q) => p[0] - q[0],
  );
  if (!events.length) return;
  htmlEl("p", "by-event-caption", "By event, all sessions and corner types:", wrap);
  const scroll2 = htmlEl("div", "table-wrap", undefined, wrap);
  const table2 = htmlEl("table", "by-event", undefined, scroll2);
  const head2 = htmlEl("tr", undefined, undefined, htmlEl("thead", undefined, undefined, table2));
  htmlEl("th", undefined, "Event", head2);
  for (const m of data.metrics) {
    htmlEl("th", undefined, `${capitalise(m.label)} (${m.unit})`, head2);
  }
  const body2 = htmlEl("tbody", undefined, undefined, table2);
  const byKey = new Map(byEvent.map((e) => [`${e.round}|${e.metric}`, e]));
  for (const [round, event] of events) {
    const tr = htmlEl("tr", undefined, undefined, body2);
    htmlEl("td", undefined, event, tr);
    for (const m of data.metrics) {
      const cell = byKey.get(`${round}|${m.name}`);
      const ok = cell && cell.corners > 0 && isNum(cell.diff);
      htmlEl("td", ok ? undefined : "muted", ok ? signed(cell.diff, m.digits) : "–", tr);
    }
  }
}

/** Draw the comparison into `root`, replacing what was there, at `root`'s current width. */
export function render(root: HTMLElement, data: StyleChart, opts: RenderOptions = {}): void {
  root.replaceChildren();
  renderHeader(root, data);

  const kinds = KINDS.map((k) => ({
    ...k,
    empty: !data.table.some((r) => r.kind === k.kind && hasValue(r)),
  }));
  let kind: StyleKind = opts.kind ?? "all";
  if (kinds.find((k) => k.kind === kind)?.empty) kind = "all";

  // Panel 1: differences by corner type.
  const diffs = htmlEl("section", "panel", undefined, root);
  const diffHead = htmlEl("div", "panel-head", undefined, diffs);
  const headTag = headingTag(opts.headingLevel, 2);
  htmlEl(headTag, "panel-title", "Differences by corner type", diffHead);
  const caption = htmlEl("p", "caption", undefined, diffs);
  const who = `${data.driver_a} minus ${data.driver_b}, averaged over corners`;
  if (data.table.some((r) => isNum(r.lo) && isNum(r.hi))) {
    caption.append(`${who}, with 95% intervals from the spread between events. `);
    const key = htmlEl("span", "clear-key", undefined, caption);
    htmlEl("span", "dot-key clear", undefined, key);
    key.append("clear (interval excludes zero) ");
    htmlEl("span", "dot-key unclear", undefined, key);
    key.append("not clear");
  } else {
    caption.append(
      `${who}. With fewer than ${MIN_INTERVAL_EVENTS} events there are no intervals, so no ` +
        "difference is called clear.",
    );
  }
  const grid = htmlEl("div", "diff-grid", undefined, diffs);
  const diffReadout = htmlEl("div", "readout diff-readout", undefined, diffs);
  diffReadout.setAttribute("aria-live", "polite");

  // Panel 2: the style map.
  let plane: Plane = opts.plane ?? "style";
  const mapBox = hasMap(data) ? htmlEl("section", "panel", undefined, root) : undefined;

  renderEmbedding(root, data);
  if (data.notes?.length) {
    const notes = htmlEl("ul", "notes", undefined, root);
    for (const note of data.notes) {
      // The summary's "Note: they drove ..." reads as "They drove ..." in a list of notes.
      const text = String(note).replace(/^Note:\s*/, "");
      htmlEl("li", undefined, capitalise(text), notes);
    }
  }

  const details = htmlEl("details", undefined, undefined, root);
  details.open = opts.tableOpen ?? false;
  details.addEventListener("toggle", () => opts.onTableToggle?.(details.open));
  htmlEl("summary", undefined, "Tables", details);
  const tables = htmlEl("div", undefined, undefined, details);

  radioGroup(
    diffHead,
    "Sessions",
    kinds.map((k) => ({ value: k.kind, label: k.label, disabled: k.empty })),
    kind,
    (value) => {
      kind = value;
      opts.onKindChange?.(value);
      renderDiffGrid(grid, diffReadout, data, kind);
      renderTables(tables, data, kind);
    },
  );
  renderDiffGrid(grid, diffReadout, data, kind);
  renderTables(tables, data, kind);

  if (mapBox) {
    const head = htmlEl("div", "panel-head", undefined, mapBox);
    htmlEl(headTag, "panel-title", "Style map", head);
    const plot = htmlEl("div", "map", undefined, mapBox);
    renderLegend(mapBox, data);
    const mapCaptionEl = htmlEl("p", "caption", mapCaption(data, plane), mapBox);
    const mapReadout = htmlEl("div", "readout", undefined, mapBox);
    mapReadout.setAttribute("aria-live", "polite");
    radioGroup(head, "Plane", PLANES, plane, (value) => {
      plane = value;
      opts.onPlaneChange?.(value);
      renderMap(plot, mapReadout, data, plane, opts.onPointSelect);
      mapCaptionEl.textContent = mapCaption(data, plane);
    });
    renderMap(plot, mapReadout, data, plane, opts.onPointSelect);
  }
}
