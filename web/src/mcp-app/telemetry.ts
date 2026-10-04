/**
 * Telemetry chart rendered inside Claude as an MCP App.
 *
 * The Python tool returns the lap as `structuredContent`; the host forwards it here via
 * `ontoolresult`. This file is bundled into one HTML file (`npm run build:mcp-app`) that
 * the Python MCP server serves as a `ui://` resource.
 */
import { App } from "@modelcontextprotocol/ext-apps";
import "./frame.css";
import "../charts/styles/tokens.css";
import "../charts/styles/base.css";
import "./telemetry.css";
import { monotonePath, svgEl as el } from "../charts/dom.ts";
import { connect } from "./host.ts";

interface Telemetry {
  title: string;
  distance_m: number[];
  speed_kph: number[];
  throttle_pct: number[];
  brake: boolean[];
  gear: number[];
  corners: { number: number; distance_m: number }[];
}

const PAD = { left: 40, right: 10, top: 8, bottom: 22 };
const PANELS = { speed: 150, gap: 10, throttle: 48, brake: 14 };
const HEIGHT =
  PAD.top + PANELS.speed + PANELS.gap + PANELS.throttle + PANELS.gap + PANELS.brake + PAD.bottom;

const root = document.getElementById("app")!;
let current: Telemetry | undefined;

function isTelemetry(value: unknown): value is Telemetry {
  const v = value as Partial<Telemetry> | undefined;
  return Array.isArray(v?.distance_m) && Array.isArray(v?.speed_kph);
}

function render(data: Telemetry): void {
  const width = Math.max(root.clientWidth - 28, 280);
  const n = data.distance_m.length;
  const maxDist = data.distance_m[n - 1];
  const maxSpeed = Math.ceil(Math.max(...data.speed_kph) / 50) * 50;
  const plotW = width - PAD.left - PAD.right;
  const x = (d: number) => PAD.left + (d / maxDist) * plotW;
  const speedTop = PAD.top;
  const throttleTop = speedTop + PANELS.speed + PANELS.gap;
  const brakeTop = throttleTop + PANELS.throttle + PANELS.gap;
  const ySpeed = (v: number) => speedTop + PANELS.speed * (1 - v / maxSpeed);
  const yThrottle = (t: number) => throttleTop + PANELS.throttle * (1 - t / 100);

  const topSpeed = Math.max(...data.speed_kph);
  const minSpeed = Math.min(...data.speed_kph);
  const summary = `top ${topSpeed.toFixed(0)} km/h · min ${minSpeed.toFixed(0)} km/h`;

  root.replaceChildren();
  const header = document.createElement("div");
  header.className = "header";
  const title = document.createElement("div");
  title.className = "title";
  title.textContent = data.title;
  const readout = document.createElement("div");
  readout.className = "readout";
  readout.textContent = summary;
  header.append(title, readout);

  const svg = el("svg", { viewBox: `0 0 ${width} ${HEIGHT}`, height: HEIGHT });

  // Speed grid and axis labels.
  for (let v = 0; v <= maxSpeed; v += 100) {
    el("line", { x1: PAD.left, x2: width - PAD.right, y1: ySpeed(v), y2: ySpeed(v), stroke: "var(--grid)" }, svg);
    el("text", { x: PAD.left - 6, y: ySpeed(v) + 3, "text-anchor": "end" }, svg).textContent = String(v);
  }
  el("text", { x: PAD.left - 6, y: throttleTop + 9, "text-anchor": "end" }, svg).textContent = "thr";
  el("text", { x: PAD.left - 6, y: brakeTop + 10, "text-anchor": "end" }, svg).textContent = "brk";

  // Distance axis in km.
  for (let km = 0; km * 1000 <= maxDist; km++) {
    const px = x(km * 1000);
    el("text", { x: px, y: HEIGHT - 6, "text-anchor": "middle" }, svg).textContent = `${km} km`;
  }

  // Corner markers.
  for (const c of data.corners) {
    const px = x(c.distance_m);
    el("line", {
      x1: px, x2: px, y1: speedTop, y2: brakeTop + PANELS.brake,
      stroke: "var(--corner)", "stroke-dasharray": "2 3",
    }, svg);
    el("text", { x: px + 3, y: speedTop + 10 }, svg).textContent = `T${c.number}`;
  }

  // Brake bands.
  let start = -1;
  data.brake.forEach((on, i) => {
    if (on && start < 0) start = i;
    if ((!on || i === n - 1) && start >= 0) {
      const x0 = x(data.distance_m[start]);
      const x1 = x(data.distance_m[i]);
      el("rect", { x: x0, y: brakeTop, width: Math.max(x1 - x0, 1), height: PANELS.brake, fill: "var(--brake)", rx: 2 }, svg);
      start = -1;
    }
  });

  // Speed and throttle as smooth curves through every sample (monotone: no overshoot).
  const xs = data.distance_m.map(x);
  const path = (ys: number[]) => monotonePath(xs, ys);
  el("path", { d: path(data.throttle_pct.map(yThrottle)), fill: "none", stroke: "var(--throttle)", "stroke-width": 1.5 }, svg);
  el("path", { d: path(data.speed_kph.map(ySpeed)), fill: "none", stroke: "var(--speed)", "stroke-width": 2 }, svg);

  // Hover crosshair and readout.
  const cursor = el("line", { y1: speedTop, y2: brakeTop + PANELS.brake, stroke: "var(--fg)", "stroke-opacity": 0.5, visibility: "hidden" }, svg);
  const dot = el("circle", { r: 3.5, fill: "var(--speed)", visibility: "hidden" }, svg);
  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const d = Math.min(Math.max((px - PAD.left) / plotW, 0), 1) * maxDist;
    const i = Math.min(Math.round((d / maxDist) * (n - 1)), n - 1);
    const cx = x(data.distance_m[i]);
    cursor.setAttribute("x1", String(cx));
    cursor.setAttribute("x2", String(cx));
    dot.setAttribute("cx", String(cx));
    dot.setAttribute("cy", String(ySpeed(data.speed_kph[i])));
    cursor.setAttribute("visibility", "visible");
    dot.setAttribute("visibility", "visible");
    readout.textContent =
      `${(data.distance_m[i] / 1000).toFixed(2)} km · ${data.speed_kph[i].toFixed(0)} km/h · ` +
      `thr ${data.throttle_pct[i].toFixed(0)}% · gear ${data.gear[i]}${data.brake[i] ? " · braking" : ""}`;
  });
  svg.addEventListener("pointerleave", () => {
    cursor.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    readout.textContent = summary;
  });

  const legend = document.createElement("div");
  legend.className = "legend";
  for (const [label, color] of [["Speed (km/h)", "var(--speed)"], ["Throttle", "var(--throttle)"], ["Brake", "var(--brake)"]]) {
    const item = document.createElement("span");
    item.style.setProperty("--swatch", color);
    item.textContent = label;
    legend.append(item);
  }

  root.append(header, svg, legend);
}

const app = new App({ name: "race-engineer-telemetry", version: "0.1.0" });

// Register handlers before connecting: the host may send the result right after the handshake.
app.ontoolresult = (result) => {
  if (isTelemetry(result.structuredContent)) {
    current = result.structuredContent;
    render(current);
  } else {
    root.innerHTML = '<p class="status">No telemetry in this result.</p>';
  }
};

new ResizeObserver(() => current && render(current)).observe(root);

await connect(app);
