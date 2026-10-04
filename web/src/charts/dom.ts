/**
 * Pieces shared by the charts: building SVG/HTML nodes, scales and paths, the crosshair keys
 * and redrawing on width changes. Nothing here knows the host (Claude's MCP App frame or the
 * website), and nothing touches the DOM until a function is called, so the module also loads
 * under Node for the tests.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

export type Series = (number | null)[]; // null where a value is missing (e.g. frozen car data)

/**
 * A circuit drawn in px (longer side 300 px), as `find_mistakes` (`track`) and `explain_corner`
 * (`map`) send it.
 */
export interface TrackPayload {
  width: number;
  height: number;
  length_m: number;
  step_m: number;
  outline: [number, number][]; // px, every 20 m, closed
  turns: { label: string; x: number; y: number; tx: number; ty: number }[];
  corners: { label: string; apex_m: number }[];
  start: { x: number; y: number; dx: number; dy: number } | null;
  rotation_source: "fastf1" | "default";
}

export function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
  parent?: Element,
): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  parent?.appendChild(node);
  return node;
}

/** An HTML element; text goes in via textContent because tool output is untrusted. */
export function htmlEl<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
  parent?: Element,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  parent?.appendChild(node);
  return node;
}

/** A heading level, h1 to h6. */
export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;

/**
 * The tag for a heading at `level`, or at `fallback` when no level is given (or it isn't a whole
 * number). Levels stay within h1 to h6, so a sub-heading one below an h6 is still an h6.
 */
export function headingTag(level: number | undefined, fallback: HeadingLevel): `h${HeadingLevel}` {
  const n = Number.isInteger(level) ? Math.min(6, Math.max(1, level as number)) : fallback;
  return `h${n as HeadingLevel}`;
}

/** Replace the page with one muted line (waiting, cancelled, or the tool's error text). */
export function showStatus(root: HTMLElement, text: string): void {
  root.replaceChildren();
  htmlEl("p", "status", text, root);
}

/**
 * A tool argument for a status line: text with each word capitalised ("abu dhabi" ->
 * "Abu Dhabi"), a number as written, or "" when the argument is missing.
 */
export function argText(value: unknown): string {
  if (typeof value === "number") return String(value);
  if (typeof value !== "string") return "";
  return value.trim().replace(/(^|\s)\S/g, (start) => start.toUpperCase());
}

/** A lap time as m:ss.sss (or s.sss under a minute). */
export function lapTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return minutes ? `${minutes}:${rest.toFixed(3).padStart(6, "0")}` : rest.toFixed(3);
}

/** A round tick step for an axis spanning `span` with at most `maxTicks` ticks. */
export function niceStep(span: number, maxTicks: number): number {
  const steps = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10];
  return steps.find((s) => span / s <= maxTicks) ?? steps[steps.length - 1];
}

export function finite(values: Series): number[] {
  return values.filter((v): v is number => v !== null);
}

/**
 * Smooth SVG path through the points, lifting the pen where a value is missing: each run of
 * values is drawn by `monotonePath`.
 */
export function linePath(xs: number[], ys: Series): string {
  let d = "";
  let runX: number[] = [];
  let runY: number[] = [];
  const end = () => {
    d += monotonePath(runX, runY);
    runX = [];
    runY = [];
  };
  ys.forEach((y, i) => {
    if (y === null) return end();
    runX.push(xs[i]);
    runY.push(y);
  });
  end();
  return d;
}

/**
 * The smooth line through the points as SVG path text: a move to the first point (or a line
 * to it, with `start` "L", to carry on from a path already drawn), then one cubic Bezier per
 * pair of neighbours. One point is only the move, and two points a straight line.
 *
 * The curve is monotone cubic interpolation along x, the same curve as d3's curveMonotoneX
 * (its tangents are Steffen's, 1990): it passes through every point, never overshoots between
 * two neighbours (a speed trace never dips below the slowest sample), and stays flat where
 * the values hold. Run backwards (x falling) it traces the same curve, so a band's lower edge
 * can be drawn back along its upper one. A point equal to the one before it is skipped, as d3
 * does. Coordinates are rounded to 0.1 px. Readouts, markers and crosshairs keep using the
 * data points, which the curve passes through.
 */
export function monotonePath(xs: number[], ys: number[], start: "M" | "L" = "M"): string {
  const px: number[] = [];
  const py: number[] = [];
  xs.forEach((x, i) => {
    const k = px.length - 1;
    if (k < 0 || x !== px[k] || ys[i] !== py[k]) {
      px.push(x);
      py.push(ys[i]);
    }
  });
  const n = px.length;
  if (n === 0) return "";
  const f = (v: number) => v.toFixed(1);
  let d = `${start}${f(px[0])},${f(py[0])}`;
  if (n === 2) d += `L${f(px[1])},${f(py[1])}`;
  if (n < 3) return d;

  // The tangent's slope at each point: at an inner point the weighted mean of the two
  // neighbouring slopes, capped at twice the smaller one, and 0 where they differ in sign or
  // one is flat; at the two ends, from the end piece's slope and its other point's tangent.
  // The arithmetic follows d3's line for line, so the two give the same numbers (zero-width
  // steps included, where the signed zero picks the side).
  const slope = new Array<number>(n);
  for (let i = 1; i < n - 1; i++) {
    const h0 = px[i] - px[i - 1];
    const h1 = px[i + 1] - px[i];
    const s0 = (py[i] - py[i - 1]) / (h0 || (h1 < 0 ? -0 : 0));
    const s1 = (py[i + 1] - py[i]) / (h1 || (h0 < 0 ? -0 : 0));
    const p = (s0 * h1 + s1 * h0) / (h0 + h1);
    const sign = (s0 < 0 ? -1 : 1) + (s1 < 0 ? -1 : 1);
    slope[i] = sign * Math.min(Math.abs(s0), Math.abs(s1), 0.5 * Math.abs(p)) || 0;
  }
  const end = (a: number, b: number, t: number) => {
    const h = px[b] - px[a];
    return h ? ((3 * (py[b] - py[a])) / h - t) / 2 : t;
  };
  slope[0] = end(0, 1, slope[1]);
  slope[n - 1] = end(n - 2, n - 1, slope[n - 2]);

  // Each piece as a Bezier: the control points sit a third of the way along, on the tangents.
  for (let i = 0; i < n - 1; i++) {
    const dx = (px[i + 1] - px[i]) / 3;
    d +=
      `C${f(px[i] + dx)},${f(py[i] + dx * slope[i])},` +
      `${f(px[i + 1] - dx)},${f(py[i + 1] - dx * slope[i + 1])},${f(px[i + 1])},${f(py[i + 1])}`;
  }
  return d;
}

/** Index of the value in `sorted` (ascending) closest to `value`. */
export function nearestIndex(sorted: number[], value: number): number {
  let lo = 0;
  let hi = sorted.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] <= value) lo = mid;
    else hi = mid;
  }
  return value - sorted[lo] <= sorted[hi] - value ? lo : hi;
}

/** Whether the reader asked for less motion: no autoplay, no transitions. */
export function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * The width a chart draws at: the root's width inside its padding, and never less than
 * `floor` px. In Claude's frame the root is padded 14 px each side, so this is the old
 * `clientWidth - 28`; on the website it follows whatever padding the root has there.
 */
export function contentWidth(root: HTMLElement, floor: number): number {
  const style = getComputedStyle(root);
  const padding = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
  return Math.max(root.clientWidth - padding, floor);
}

let ids = 0;

/**
 * A document-unique id ("gap-3") for clip paths and patterns: SVG ids are global to the page,
 * and a page (a chat, the website) may show the same chart twice.
 */
export function uid(prefix: string): string {
  return `${prefix}-${++ids}`;
}

export interface Crosshair {
  /** The position shown, or -1 while the crosshair is hidden. */
  readonly index: number;
  /** Show position `i` (clamped to the data). */
  show(i: number): void;
  hide(): void;
}

/**
 * Pointer and keyboard control of a chart's crosshair over `n` positions. ←/→ step through
 * them (Shift for bigger steps), Home/End jump to the ends and Esc hides the crosshair; it
 * also hides when the chart loses focus, and when the pointer leaves unless the chart has
 * focus. `onMove(i)` draws position `i`, or the idle state when `i` is -1. `pointerIndex`
 * maps a pointer's x (in viewBox units, or px without a viewBox) to a position; without it
 * the pointer is ignored.
 */
export function crosshairKeys(
  svg: SVGSVGElement,
  n: number,
  onMove: (i: number) => void,
  pointerIndex?: (x: number) => number,
): Crosshair {
  let index = -1;
  const crosshair: Crosshair = {
    get index() {
      return index;
    },
    show(i) {
      index = Math.min(Math.max(i, 0), n - 1);
      onMove(index);
    },
    hide() {
      index = -1;
      onMove(-1);
    },
  };

  if (pointerIndex) {
    svg.addEventListener("pointermove", (event) => {
      const box = svg.getBoundingClientRect();
      // Without a viewBox (baseVal is null in some browsers, empty in others) x is in px.
      const view = svg.viewBox.baseVal as DOMRect | null;
      const [x0, width] = view?.width ? [view.x, view.width] : [0, box.width];
      crosshair.show(pointerIndex(x0 + ((event.clientX - box.left) / box.width) * width));
    });
    svg.addEventListener("pointerleave", () => {
      if (document.activeElement !== svg) crosshair.hide();
    });
  }
  svg.addEventListener("blur", () => crosshair.hide());
  svg.addEventListener("keydown", (event) => {
    const step = Math.max(1, Math.round(n / (event.shiftKey ? 20 : 100)));
    const moves: Record<string, number> = {
      ArrowRight: index + step,
      ArrowLeft: index < 0 ? 0 : index - step,
      Home: 0,
      End: n - 1,
    };
    if (event.key in moves) {
      event.preventDefault();
      crosshair.show(moves[event.key]);
    } else if (event.key === "Escape") {
      crosshair.hide();
    }
  });
  return crosshair;
}

/**
 * Call `redraw` when the element's width changes. Height changes (a table opening, the host
 * catching up on the size) don't redraw. The redraw runs on the next frame, not inside the
 * observer's callback: a redraw that resizes the element there (find-mistakes' list moving
 * under its map) makes the browser report "ResizeObserver loop completed with undelivered
 * notifications" as a window error. The pending frame isn't cancelled by `disconnect()`, so a
 * caller that stops observing guards its `redraw` itself.
 */
export function widthObserver(el: HTMLElement, redraw: (width: number) => void): ResizeObserver {
  let width = el.clientWidth;
  let frame = 0;
  const observer = new ResizeObserver(() => {
    if (el.clientWidth === width) return;
    width = el.clientWidth;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => redraw(el.clientWidth));
  });
  observer.observe(el);
  return observer;
}
