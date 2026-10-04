/**
 * Race or sprint summary (the `get_race_summary` tool), shared by Claude's MCP App and the
 * website.
 *
 * Every driver's position at the end of each lap (a position chart), with the safety-car,
 * virtual-safety-car and red-flag periods shaded, pit stops as rings and cars that stopped
 * ending in ×. The reader hovers, or uses ←/→ for the order on a lap and ↑/↓ to pick a
 * driver; a collapsible table repeats the finishing order as timed. The Python tool sends the
 * data as `structuredContent`.
 *
 * `render(root, data, opts)` draws the summary and knows nothing of the host:
 * `mcp-app/race-summary.ts` feeds it tool results inside Claude, and the website wraps it in a
 * React component.
 */
import { contentWidth, crosshairKeys, htmlEl, lapTime, linePath, svgEl } from "./dom.ts";

export interface RaceDriver {
  code: string;
  team: string;
  finish: number | null;
  status: "finished" | "lapped" | "stopped";
  laps_completed: number;
  laps_down: number;
  gap_s: number | null;
  start_pos: number | null; // at the end of lap 1 (there is no grid in the data)
  positions: (number | null)[]; // at the end of each lap
  pit_laps: number[]; // in-laps
  stints: { compound: string; from_lap: number; to_lap: number }[];
  best_lap_s: number | null;
}

export interface RaceSummary {
  event: string;
  location: string;
  year: number;
  round: number;
  session: string;
  session_code: "R" | "S";
  date: string;
  laps: number;
  focus: string | null; // the driver the call asked about
  drivers: RaceDriver[];
  neutralisations: { kind: "SC" | "VSC" | "RED"; from_lap: number; to_lap: number }[];
  fastest_lap: { driver: string; lap: number; time_s: number } | null;
  notes: string[];
}

type Kind = RaceSummary["neutralisations"][number]["kind"];
// How a driver's line is drawn: the driver the call asked about, the winner, or the field.
type Role = "focus" | "winner" | "other";

export interface RenderOptions {
  /** Whether the results table starts open (the caller keeps it across redraws). */
  tableOpen?: boolean;
  /** Called when the reader opens or closes the results table. */
  onTableToggle?: (open: boolean) => void;
}

const KIND_TEXT: Record<Kind, string> = {
  SC: "Safety car",
  VSC: "Virtual safety car",
  RED: "Red flag",
};
const KIND_SHORT: Record<Kind, string> = { SC: "SC", VSC: "VSC", RED: "Red flag" };
const COMPOUND: Record<string, string> = {
  SOFT: "S",
  MEDIUM: "M",
  HARD: "H",
  INTERMEDIATE: "I",
  WET: "W",
};
// A driver's key in the readout; a picked driver from the field is drawn in --fg.
const KEY_COLOR: Record<Role, string> = {
  focus: "var(--a)",
  winner: "var(--b)",
  other: "var(--fg)",
};
// Left: position numbers. Right: finishing position and code.
const PAD = { left: 26, right: 60, top: 18, bottom: 22 };
const NARROW = 452; // chart width at the page's 480 px breakpoint
const LIST_MAX = 4; // codes in a readout list before it is cut short ("Pit: RUS, VER, HAD +13")

let uid = 0; // keeps pattern ids unique if several summaries share a document

export function isRaceSummary(value: unknown): value is RaceSummary {
  const v = value as Partial<RaceSummary> | undefined;
  return (
    Array.isArray(v?.drivers) &&
    // render walks every driver's arrays
    v.drivers.every(
      (d: Partial<RaceDriver> | null) =>
        typeof d?.code === "string" &&
        Array.isArray(d.positions) &&
        Array.isArray(d.pit_laps) &&
        Array.isArray(d.stints),
    ) &&
    Array.isArray(v?.neutralisations) &&
    Array.isArray(v?.notes) &&
    typeof v?.laps === "number"
  );
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function lapsText(from: number, to: number): string {
  return from === to ? `lap ${from}` : `laps ${from}–${to}`;
}

/** "stopped after 40 laps" (the tool's wording: laps completed), or "stopped on lap 1". */
function stoppedText(d: RaceDriver): string {
  const n = d.laps_completed;
  return n > 0 ? `stopped after ${plural(n, "lap")}` : "stopped on lap 1";
}

function gapText(d: RaceDriver): string {
  if (d.status === "stopped") {
    const text = stoppedText(d);
    return text.charAt(0).toUpperCase() + text.slice(1);
  }
  if (d.status === "lapped") return `+${plural(d.laps_down, "lap")}`;
  if (d.finish === 1) return "Winner";
  return d.gap_s === null ? "–" : `+${d.gap_s.toFixed(3)} s`;
}

function tyresText(d: RaceDriver): string {
  if (!d.stints.length) return "–";
  return d.stints.map((s) => COMPOUND[s.compound.toUpperCase()] ?? "?").join("→");
}

function stopsText(d: RaceDriver): string {
  if (!d.pit_laps.length) return "no stops";
  const laps = d.pit_laps.length === 1 ? "lap" : "laps";
  return `${plural(d.pit_laps.length, "stop")}, ${laps} ${d.pit_laps.join(", ")}`;
}

/** "P9 after lap 1 → P5, up 4", or how the race ended for a car that stopped. */
function raceText(d: RaceDriver): string {
  const start = d.start_pos === null ? "" : `P${d.start_pos} after lap 1 → `;
  if (d.status === "stopped") return `${start}${stoppedText(d)}`;
  if (d.finish === null) return `${start}finished`;
  const moved = d.start_pos === null ? 0 : d.start_pos - d.finish;
  const change = moved ? `, ${moved > 0 ? "up" : "down"} ${Math.abs(moved)}` : "";
  return `${start}P${d.finish}${change}`;
}

/** Classified as timed first, then by laps completed. */
function finishOrder(drivers: RaceDriver[]): RaceDriver[] {
  const rank = (d: RaceDriver) => d.finish ?? Infinity;
  return [...drivers].sort((p, q) => rank(p) - rank(q) || q.laps_completed - p.laps_completed);
}

interface Context {
  order: RaceDriver[];
  winner?: RaceDriver;
  focus?: RaceDriver;
}

function roleOf(ctx: Context, d: RaceDriver): Role {
  if (d === ctx.focus) return "focus";
  return d === ctx.winner ? "winner" : "other";
}

function renderHeader(root: HTMLElement, data: RaceSummary, ctx: Context): void {
  const header = htmlEl("header", "rs-header", undefined, root);
  const titleRow = htmlEl("div", "title-row", undefined, header);
  htmlEl("div", "title", `${data.event} ${data.year} · ${data.session}`, titleRow);
  const where = [data.location, data.date, plural(data.laps, "lap")].filter(Boolean);
  htmlEl("div", "subtitle", where.join(" · "), titleRow);

  const meta = htmlEl("div", "meta", undefined, header);
  if (ctx.winner) {
    const second = ctx.order.find((d) => d.finish === 2);
    let margin = "";
    if (second?.status === "finished" && second.gap_s) {
      margin = ` by ${second.gap_s.toFixed(3)} s`;
    } else if (second?.status === "lapped") {
      margin = ` by ${plural(second.laps_down, "lap")}`;
    }
    htmlEl("strong", undefined, `Won by ${ctx.winner.code}${margin}`, meta);
  }
  const fast = data.fastest_lap;
  if (fast) {
    const text = `Fastest lap ${fast.driver}, lap ${fast.lap}, ${lapTime(fast.time_s)}`;
    htmlEl("span", undefined, text, meta);
  }
  if (ctx.focus) {
    const d = ctx.focus;
    const text = `${d.code} (${d.team}): ${raceText(d)} · ${stopsText(d)} (${tyresText(d)})`;
    htmlEl("div", "meta", text, header);
  }

  const chips = htmlEl("div", "chips", undefined, header);
  if (!data.neutralisations.length) {
    htmlEl("span", "chip none", "No safety car, VSC or red flag", chips);
  }
  for (const n of data.neutralisations) {
    const chip = htmlEl("span", "chip", undefined, chips);
    const swatch = htmlEl("span", `sw sw-${n.kind.toLowerCase()}`, undefined, chip);
    swatch.setAttribute("aria-hidden", "true");
    chip.append(`${KIND_SHORT[n.kind]} ${lapsText(n.from_lap, n.to_lap)}`);
  }
}

function renderLegend(root: HTMLElement, data: RaceSummary, ctx: Context): void {
  const legend = htmlEl("div", "legend", undefined, root);
  const item = (key: string, text: string, glyph?: string) => {
    const node = htmlEl("span", "item", undefined, legend);
    htmlEl("span", `key ${key}`, glyph, node).setAttribute("aria-hidden", "true");
    node.append(text);
  };
  const { focus, winner } = ctx;
  if (focus) item("key-focus", focus === winner ? `${focus.code}, winner` : focus.code);
  if (winner && winner !== focus) item("key-winner", `${winner.code}, winner`);
  item("key-other", focus || winner ? "other drivers" : "drivers");
  if (data.drivers.some((d) => d.pit_laps.length)) item("key-pit", "pit stop");
  if (data.drivers.some((d) => d.status === "stopped")) item("key-stop", "stopped", "×");
}

/** Each driver's row at the right edge: its finishing position, else the next free row. */
function labelRows(order: RaceDriver[], rows: number): Map<RaceDriver, number> {
  const taken = new Set<number>();
  const rowOf = new Map<RaceDriver, number>();
  for (const d of order) {
    if (d.finish !== null && d.finish >= 1 && d.finish <= rows && !taken.has(d.finish)) {
      rowOf.set(d, d.finish);
      taken.add(d.finish);
    }
  }
  let next = 1;
  for (const d of order) {
    if (rowOf.has(d)) continue;
    while (taken.has(next)) next++;
    rowOf.set(d, next);
    taken.add(next);
  }
  return rowOf;
}

function renderChart(root: HTMLElement, data: RaceSummary, ctx: Context): void {
  const width = contentWidth(root, 300);
  const narrow = width < NARROW;
  const laps = Math.max(data.laps, ...data.drivers.map((d) => d.positions.length));
  const seen = data.drivers.flatMap((d) => d.positions.filter((p): p is number => p !== null));
  const rows = Math.max(data.drivers.length, ...seen);
  // 13-15 px rows fit a full grid; a small field gets taller rows (up to 28 px).
  const rowH = Math.max(narrow ? 13 : 15, Math.min(28, Math.floor(240 / rows)));
  const plotW = width - PAD.left - PAD.right;
  const plotH = rows * rowH;
  const top = PAD.top;
  const bottom = top + plotH;
  const right = PAD.left + plotW;
  const height = bottom + PAD.bottom;
  const x = (lap: number) => PAD.left + ((lap - 0.5) / laps) * plotW; // each lap is a column
  const y = (pos: number) => top + (pos - 0.5) * rowH;
  const xs = Array.from({ length: laps }, (_, i) => x(i + 1));
  const at = (d: RaceDriver, i: number) => d.positions[i] ?? null;
  const ys = (d: RaceDriver) =>
    xs.map((_, i) => {
      const p = at(d, i);
      return p === null ? null : y(p);
    });

  if (data.drivers.length > 1) renderLegend(root, data, ctx); // one line needs no legend
  const readout = htmlEl("div", "readout", undefined, root);
  readout.setAttribute("aria-live", "polite");

  const periods = data.neutralisations.map(
    (n) => `${KIND_TEXT[n.kind].toLowerCase()} ${lapsText(n.from_lap, n.to_lap)}`,
  );
  const svg = svgEl("svg", {
    class: "positions",
    viewBox: `0 0 ${width} ${height}`,
    height,
    tabindex: 0,
    role: "img",
    "aria-label":
      `Position chart: the position of each of ${plural(data.drivers.length, "driver")} at ` +
      `the end of every lap, laps 1 to ${laps}` +
      `${periods.length ? `, with ${periods.join(", ")}` : ""}. ` +
      "Use the left and right arrow keys for the order on each lap, up and down to pick a driver.",
  });
  root.append(svg);
  const label = (text: string, attrs: Record<string, string | number>, parent: Element = svg) => {
    const node = svgEl("text", attrs, parent);
    node.textContent = text;
    return node;
  };

  // Neutralised laps: SC a wash, VSC hatched, red flag solid; each labelled where there's room.
  const hatchId = `rs-vsc-${++uid}`;
  const pattern = svgEl(
    "pattern",
    { id: hatchId, width: 5, height: 5, patternUnits: "userSpaceOnUse" },
    svgEl("defs", {}, svg),
  );
  pattern.setAttribute("patternTransform", "rotate(45)");
  svgEl("line", { x1: 0, y1: 0, x2: 0, y2: 5, class: "hatch" }, pattern);
  const bandLabels: { kind: Kind; cx: number; half: number }[] = [];
  for (const n of data.neutralisations) {
    const x0 = x(Math.max(n.from_lap, 1) - 0.5);
    const x1 = x(Math.min(n.to_lap, laps) + 0.5);
    if (x1 <= x0) continue;
    const band = svgEl("rect", { x: x0, y: top, width: x1 - x0, height: plotH }, svg);
    band.setAttribute("class", `band band-${n.kind.toLowerCase()}`);
    if (n.kind === "VSC") band.setAttribute("fill", `url(#${hatchId})`);
    const half = KIND_SHORT[n.kind].length * 2.8;
    const cx = Math.min(Math.max((x0 + x1) / 2, PAD.left + half), right - half);
    bandLabels.push({ kind: n.kind, cx, half });
  }
  // Where labels would collide (a red flag inside a safety-car period, say), the red flag's
  // wins, then the SC's; the chips above still list every period.
  const placed: [number, number][] = [];
  for (const kind of ["RED", "SC", "VSC"] as const) {
    for (const { cx, half } of bandLabels.filter((b) => b.kind === kind)) {
      if (placed.some(([a, b]) => cx - half < b + 4 && cx + half > a - 4)) continue;
      label(KIND_SHORT[kind], { x: cx, y: top - 6, "text-anchor": "middle", class: "band-label" });
      placed.push([cx - half, cx + half]);
    }
  }

  // Lap axis with a hairline at each tick, and the position axis.
  const step = [1, 2, 5, 10, 20, 25, 50].find((s) => (plotW / laps) * s >= 30) ?? 100;
  const ticks = [1];
  for (let lap = step; lap <= laps; lap += step) if (lap > 1) ticks.push(lap);
  for (const lap of ticks) {
    svgEl("line", { x1: x(lap), x2: x(lap), y1: top, y2: bottom, class: "gridline" }, svg);
    label(String(lap), { x: x(lap), y: height - 6, "text-anchor": "middle" });
  }
  label("Lap", { x: PAD.left - 8, y: height - 6, "text-anchor": "end" });
  for (let pos = 1; pos <= rows; pos++) {
    const attrs = { x: PAD.left - 8, y: y(pos) + 3, "text-anchor": "end", class: "pos-label" };
    label(String(pos), attrs);
  }

  // One driver's line with its pit stops (rings) and, for a car that stopped, a final ×.
  const drawDriver = (d: RaceDriver, cls: string, parent: Element) => {
    const g = svgEl("g", { class: `drv ${cls}` }, parent);
    const pts = ys(d);
    svgEl("path", { d: linePath(xs, pts), class: "line" }, g);
    const big = cls !== "other";
    for (const lap of d.pit_laps) {
      const py = pts[lap - 1];
      if (py !== null && py !== undefined) {
        svgEl("circle", { cx: xs[lap - 1], cy: py, r: big ? 3.5 : 2.5, class: "pit" }, g);
      }
    }
    if (d.status === "stopped") {
      const last = pts.findLastIndex((p) => p !== null);
      const py = pts[last];
      if (last >= 0 && py !== null) {
        const [cx, s] = [xs[last], big ? 4 : 3];
        const cross =
          `M${cx - s},${py - s}L${cx + s},${py + s}` + `M${cx - s},${py + s}L${cx + s},${py - s}`;
        svgEl("path", { d: cross, class: "stop-halo" }, g); // legible over lines and bands
        svgEl("path", { d: cross, class: "stop" }, g);
      }
    }
  };
  const field = svgEl("g", {}, svg);
  const highlighted = [ctx.winner, ctx.focus].filter((d): d is RaceDriver => d !== undefined);
  for (const d of data.drivers) if (!highlighted.includes(d)) drawDriver(d, "other", field);
  for (const d of highlighted) drawDriver(d, roleOf(ctx, d), field);

  // Finishing position and code at the right edge; the podium and the focus driver stand out.
  const rowOf = labelRows(ctx.order, rows);
  const endLabels = new Map<RaceDriver, SVGTextElement[]>();
  for (const d of ctx.order) {
    const row = rowOf.get(d) ?? rows;
    const strong = (d.finish !== null && d.finish <= 3) || d === ctx.focus ? " strong" : "";
    const ly = y(row) + 3.5;
    endLabels.set(d, [
      label(d.finish === null ? "–" : String(d.finish), {
        x: right + 24,
        y: ly,
        "text-anchor": "end",
        class: `end-label${strong}`,
      }),
      label(`${d.code}${d.status === "stopped" ? " ×" : ""}`, {
        x: right + 29,
        y: ly,
        class: `end-label${strong}`,
      }),
    ]);
  }

  // Interaction: the crosshair picks a lap, the pointer or ↑/↓ a driver.
  const cursor = svgEl("line", { y1: top, y2: bottom, class: "cursor" }, svg);
  cursor.setAttribute("visibility", "hidden");
  const overlay = svgEl("g", {}, svg);
  const column = svgEl("g", {}, svg);
  let lap = -1; // lap index on the crosshair, -1 when hidden
  let picked: RaceDriver | undefined;

  const orderAt = (i: number) =>
    data.drivers
      .filter((d) => at(d, i) !== null)
      .sort((p, q) => (at(p, i) ?? 0) - (at(q, i) ?? 0));
  const neutralAt = (l: number) =>
    data.neutralisations.find((n) => n.from_lap <= l && l <= n.to_lap);

  const drawOverlay = () => {
    overlay.replaceChildren();
    for (const [d, nodes] of endLabels) {
      for (const node of nodes) node.classList.toggle("on", d === picked);
    }
    if (!picked) return;
    const role = roleOf(ctx, picked);
    drawDriver(picked, role === "other" ? "picked" : `${role} lift`, overlay);
  };

  const drawColumn = () => {
    column.replaceChildren();
    if (lap < 0) {
      cursor.setAttribute("visibility", "hidden");
      return;
    }
    const cx = xs[lap];
    cursor.setAttribute("x1", String(cx));
    cursor.setAttribute("x2", String(cx));
    cursor.setAttribute("visibility", "visible");
    const flip = cx > right - 40;
    for (const d of orderAt(lap)) {
      const p = at(d, lap) ?? 0;
      const role = roleOf(ctx, d);
      if (role !== "other" || d === picked) {
        const g = svgEl("g", { class: `drv ${role === "other" ? "picked" : role}` }, column);
        svgEl("circle", { cx, cy: y(p), r: 4, class: "dot" }, g);
      }
      const cls = `col-label${role !== "other" ? " strong" : ""}${d === picked ? " on" : ""}`;
      const anchor = flip ? "end" : "start";
      const attrs = { x: flip ? cx - 7 : cx + 7, y: y(p) + 3, "text-anchor": anchor, class: cls };
      label(d.code, attrs, column);
    }
  };

  const climber = data.drivers
    .filter((d) => d.status !== "stopped" && d.start_pos !== null && d.finish !== null)
    .map((d) => ({ d, gain: (d.start_pos ?? 0) - (d.finish ?? 0) }))
    .sort((p, q) => q.gain - p.gain)[0];

  const keyed = (parent: HTMLElement, d: RaceDriver, text: string) => {
    const item = htmlEl("span", undefined, undefined, parent);
    htmlEl("span", "key", undefined, item).style.setProperty("--c", KEY_COLOR[roleOf(ctx, d)]);
    htmlEl("strong", undefined, text, item);
  };

  // Where a driver was on lap `l` when it has no position there.
  const absentText = (d: RaceDriver, l: number) => {
    if (l <= d.laps_completed) return "–"; // a position missing from the timing data
    if (d.status === "stopped") return "out";
    return d.status === "lapped" ? `finished ${plural(d.laps_down, "lap")} down` : "finished";
  };

  const fillReadout = () => {
    const say = (text: string, cls?: string) => htmlEl("span", cls, text, readout);
    const codes = (drivers: RaceDriver[]) => drivers.map((d) => d.code).join(", ");
    // A long list (16 cars pitting under a safety car) is cut short on screen, so the readout
    // keeps its reserved height and the chart doesn't move under the pointer; screen readers
    // get the whole list.
    const sayList = (name: string, drivers: RaceDriver[]) => {
      if (!drivers.length) return;
      if (drivers.length <= LIST_MAX) {
        say(`${name}: ${codes(drivers)}`);
        return;
      }
      const shown = drivers.slice(0, LIST_MAX - 1);
      const brief = `${name}: ${codes(shown)} +${drivers.length - shown.length}`;
      say(brief).setAttribute("aria-hidden", "true");
      say(`${name}: ${codes(drivers)}`, "sr-only");
    };
    if (lap < 0 && !picked) {
      if (climber && climber.gain > 0) {
        const { d } = climber;
        say(`Biggest climb: ${d.code}, P${d.start_pos} → P${d.finish}`);
      }
      say("Hover, or use ← → for laps and ↑ ↓ for drivers");
      return;
    }
    if (lap < 0 && picked) {
      keyed(readout, picked, picked.code);
      say(raceText(picked));
      say(`${stopsText(picked)} (${tyresText(picked)})`);
      return;
    }
    const l = lap + 1;
    const running = orderAt(lap);
    htmlEl("strong", undefined, `Lap ${l} of ${laps}`, readout);
    const n = neutralAt(l);
    if (n) say(KIND_TEXT[n.kind]);
    if (running[0] && running[0] !== picked) say(`Leader ${running[0].code}`);
    // In that lap's order, as the labels by the crosshair.
    const byPlace = [...running, ...data.drivers.filter((d) => !running.includes(d))];
    sayList("Pit", byPlace.filter((d) => d.pit_laps.includes(l)));
    // The × sits on a stopped car's last completed lap.
    sayList("Stopped", byPlace.filter((d) => d.status === "stopped" && d.laps_completed === l));
    if (picked) {
      const p = at(picked, lap);
      const where = p === null ? absentText(picked, l) : `P${p}`;
      const pit = picked.pit_laps.includes(l) ? " · pit stop" : "";
      keyed(readout, picked, `${where} ${picked.code}${pit}`);
    }
    // The order is on the chart as labels by the crosshair; screen readers get it here.
    say(`Order: ${codes(running)}`, "sr-only");
  };

  const drawReadout = () => {
    readout.replaceChildren();
    fillReadout();
    // The flex gap separates the items on screen; a screen reader needs a pause between them.
    for (const item of [...readout.children].slice(1)) {
      if (!item.hasAttribute("aria-hidden")) item.before(htmlEl("span", "sr-only", "; "));
    }
  };

  const update = () => {
    drawOverlay();
    drawColumn();
    drawReadout();
  };

  const lapIndex = (px: number) =>
    Math.min(Math.max(Math.round(((px - PAD.left) / plotW) * laps + 0.5) - 1, 0), laps - 1);
  const toView = (event: PointerEvent): [number, number] => {
    const box = svg.getBoundingClientRect();
    return [
      ((event.clientX - box.left) / box.width) * width,
      ((event.clientY - box.top) / box.height) * height,
    ];
  };
  // The driver under the pointer: on the labels, that row; on the plot, the car nearest in
  // position on the crosshair's lap (no need to land on a 1 px line).
  const driverAt = (px: number, py: number): RaceDriver | undefined => {
    if (px > right) {
      const row = Math.round((py - top) / rowH + 0.5);
      return ctx.order.find((d) => rowOf.get(d) === row);
    }
    const i = lapIndex(px);
    let best: RaceDriver | undefined;
    let bestOff = rowH * 1.5;
    for (const d of data.drivers) {
      const p = at(d, i);
      if (p === null) continue;
      const off = Math.abs(y(p) - py);
      if (off < bestOff) [best, bestOff] = [d, off];
    }
    return best;
  };

  // Registered before crosshairKeys, whose own listeners redraw, so they see the new driver.
  // On the labels the crosshair hides and the readout gives that driver's race, as ↑/↓ do
  // without a lap (crosshairKeys would put the crosshair on the last lap).
  svg.addEventListener("pointermove", (event) => {
    const [px, py] = toView(event);
    picked = driverAt(px, py);
    if (px > right) {
      event.stopImmediatePropagation();
      crosshair.hide();
    }
  });
  svg.addEventListener("pointerleave", () => {
    if (document.activeElement !== svg) picked = undefined;
  });
  svg.addEventListener("blur", () => {
    picked = undefined;
  });
  const crosshair = crosshairKeys(
    svg,
    laps,
    (i) => {
      lap = i;
      update();
    },
    lapIndex,
  );
  // A tap (touch has no hover) picks the lap and driver under the finger.
  svg.addEventListener("pointerdown", (event) => {
    const [px, py] = toView(event);
    picked = driverAt(px, py);
    if (px > right) crosshair.hide();
    else crosshair.show(lapIndex(px));
  });
  svg.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      picked = undefined;
      update();
      return;
    }
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    // With a lap on the crosshair, step through that lap's order; otherwise the finishing order.
    const list = lap >= 0 ? orderAt(lap) : ctx.order;
    if (!list.length) return;
    const i = picked ? list.indexOf(picked) : -1;
    const down = event.key === "ArrowDown";
    const n = list.length;
    picked = list[i < 0 ? (down ? 0 : n - 1) : (i + (down ? 1 : -1) + n) % n];
    update();
  });
  update();
}

/** "2 (laps 18, 40)" */
function pitCell(d: RaceDriver): string {
  const n = d.pit_laps.length;
  return n ? `${n} (${n === 1 ? "lap" : "laps"} ${d.pit_laps.join(", ")})` : "0";
}

function renderTable(root: HTMLElement, ctx: Context, opts: RenderOptions): void {
  if (!ctx.order.length) return;
  const details = htmlEl("details", undefined, undefined, root);
  details.open = opts.tableOpen ?? false;
  details.addEventListener("toggle", () => opts.onTableToggle?.(details.open));
  htmlEl("summary", undefined, "Results as timed", details);
  const wrap = htmlEl("div", "table-wrap", undefined, details);
  const table = htmlEl("table", undefined, undefined, wrap);
  htmlEl("caption", "sr-only", "Finishing order as timed on track", table);
  const head = htmlEl("tr", undefined, undefined, htmlEl("thead", undefined, undefined, table));
  const columns: [string, string?][] = [
    ["Pos"],
    ["Driver", "text"],
    ["Team", "text"],
    ["Gap"],
    ["Laps"],
    ["After lap 1"],
    ["Stops", "text"],
    ["Tyres", "text"],
    ["Best lap"],
  ];
  for (const [name, cls] of columns) htmlEl("th", cls, name, head).setAttribute("scope", "col");
  const body = htmlEl("tbody", undefined, undefined, table);
  for (const d of ctx.order) {
    const row = htmlEl("tr", d === ctx.focus ? "focus-row" : undefined, undefined, body);
    const cells = [
      d.finish === null ? "–" : String(d.finish),
      d.code,
      d.team,
      gapText(d),
      String(d.laps_completed),
      d.start_pos === null ? "–" : `P${d.start_pos}`,
      pitCell(d),
      tyresText(d),
      d.best_lap_s === null ? "–" : lapTime(d.best_lap_s),
    ];
    cells.forEach((cell, k) => htmlEl("td", columns[k][1], cell, row));
  }
}

/** Draw the summary into `root`, replacing what was there, at `root`'s current width. */
export function render(root: HTMLElement, data: RaceSummary, opts: RenderOptions = {}): void {
  const order = finishOrder(data.drivers);
  const winner = order[0]?.finish === 1 ? order[0] : undefined;
  const focusCode = data.focus?.toUpperCase();
  const focus = data.drivers.find((d) => d.code.toUpperCase() === focusCode);
  const ctx: Context = { order, winner, focus };

  root.replaceChildren();
  renderHeader(root, data, ctx);
  const hasPositions = data.drivers.some((d) => d.positions.some((p) => p !== null));
  if (hasPositions && data.laps > 0) renderChart(root, data, ctx);
  else htmlEl("p", "status", "No lap-by-lap positions in this result.", root);
  renderTable(root, ctx, opts);
  if (data.notes.length) htmlEl("p", "notes", data.notes.join(" "), root);
}
