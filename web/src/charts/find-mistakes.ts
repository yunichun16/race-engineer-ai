/**
 * Mistake list and track map (the `find_mistakes` tool), shared by Claude's MCP App and the
 * website.
 *
 * The session's mistakes in the tool's ranking, each with its driver, lap, turn, type, time
 * lost and explanation, next to a track map that marks where they happened: one marker per
 * turn, sized by the time lost there and badged with the best rank at that turn. Hovering or
 * focusing a row rings its marker; hovering a marker (or stepping through them with ← →)
 * highlights its rows, and clicking it (or Enter) lists only that turn. The Python tool sends
 * the data as `structuredContent`.
 *
 * `render(root, data, opts)` draws the page and knows nothing of the host:
 * `mcp-app/find-mistakes.ts` feeds it tool results inside Claude and sends the Explain
 * button's message, and the website wraps it in a React component that draws the corner
 * instead. `markSelected` marks the row whose corner the website has open.
 */
import { crosshairKeys, headingTag, htmlEl, svgEl, type HeadingLevel, type TrackPayload } from "./dom.ts";

export interface Mistake {
  driver: string;
  team: string;
  lap_number: number;
  turn: string; // "7" or "9a", as the map's turn labels
  also_turns: string[]; // neighbouring turns flagged on the same lap (the same moment)
  type: string;
  type_text: string;
  secondary_type: string | null; // "" or null: none
  time_lost_s: number | null;
  phase: string; // entry, apex, exit or ""
  score_pct: number;
  lap_class: string;
  explanation: string;
  apex_m?: number;
}

export interface MistakeList {
  event: string;
  location: string;
  year: number;
  round: number;
  session: string;
  session_code: string;
  driver: string | null; // null: the whole field
  ranking: string;
  scored: number;
  flagged: number;
  distinct_mistakes: number;
  mistakes: Mistake[];
  left_out: Record<string, number>;
  coverage: {
    laps: unknown[];
    scored_driver_laps: number;
    eligible_driver_laps: number;
    session_laps: number;
    low: boolean;
  };
  note: string;
  track: TrackPayload | null;
}

/** What the reader changed on the page; the caller keeps it so a redraw doesn't reset it. */
export interface ViewState {
  /** The turn the list is narrowed to, or null for every mistake. */
  turn: string | null;
  /** Rows (indices into `mistakes`) whose explanation is open in full. */
  expanded: number[];
  /** Whether "How these are ranked" is open. */
  rankingOpen: boolean;
}

export interface RenderOptions {
  /**
   * Ask for an explanation of one mistake; without it there is no Explain button. It may
   * resolve to whether the request went through, which the button then shows.
   */
  onExplain?: (mistake: Mistake) => void | Promise<boolean>;
  /** The view to start from (the whole list, explanations closed, when missing). */
  view?: ViewState;
  /** Called whenever the reader changes the view. */
  onViewChange?: (view: ViewState) => void;
  /** The Explain button's words ("Explain" when missing). */
  explainLabel?: string;
  /**
   * The level of the list's heading (1 when missing: the title of Claude's page). A page that
   * has its own h1 passes the level below the heading the chart sits under.
   */
  headingLevel?: HeadingLevel;
}

export function isMistakeList(value: unknown): value is MistakeList {
  const v = value as Partial<MistakeList> | undefined;
  return (
    typeof v?.event === "string" &&
    Array.isArray(v.mistakes) &&
    typeof v.coverage === "object" &&
    v.coverage !== null &&
    typeof v.left_out === "object" &&
    v.left_out !== null
  );
}

// The map sits left of the list from this frame width (px), above it below that, and is never
// taller than MAP_MAX_H.
const WIDE_PX = 640;
const MAP_MAX_H = 260;
const MAP_COLUMN = { min: 220, max: 300, share: 0.4 };
// Sizes on the map in screen px (the map scales, so they are converted to its units). A
// marker's radius grows with the square root of the time lost at its turn.
const MARKER = { min: 5, max: 12, grow: 2, ring: 4, hit: 12, faint: 3.5 };
const ROAD_PX = 8;
const LABEL_PX = 10;
const BADGE_PX = { height: 13, pad: 7, digit: 5.5, font: 9 };

// Secondary types in find_mistakes' words (inference/explain.py TYPE_TEXT, short forms).
const TYPE_SHORT: Record<string, string> = {
  early_braking: "early braking",
  over_slowing: "over-slowing",
  late_throttle: "late throttle / wide exit",
  hesitation: "hesitation / traction loss",
  lift_and_coast: "lift and coast",
  lift_on_push_lap: "lift before braking on a push lap",
  slow_approach: "slow approach",
  track_limits: "track limits / off track",
  incident: "race-control incident",
  lapped: "being lapped",
  battle: "racing another car",
  impeded: "held up by a slower car",
  not_push_lap: "not a full push lap",
  unclear: "unclear",
};

// Why flagged corners are left out of the list, as the footer counts them: [one, many]. Short
// forms of tools/mistakes.py LEFT_OUT_TEXT, worded to follow a count ("3 with too few…").
const LEFT_OUT: Record<string, [string, string]> = {
  no_time_lost: ["not clearly slower", "not clearly slower"],
  no_reference: ["with too few clean laps to compare", "with too few clean laps to compare"],
  slowdown: ["slow-down (abandoned lap or something on track)", "slow-downs"],
  data_problem: ["data problem", "data problems"],
  lift_and_coast: ["lift and coast", "lifts and coasts"],
  slow_approach: ["slow approach", "slow approaches"],
  lapped: ["being lapped", "being lapped"],
  battle: ["fight with another car", "fights with other cars"],
  impeded: ["held up by a slower car", "held up by slower cars"],
  not_push_lap: ["not a full push lap", "not full push laps"],
  traffic_moment: [
    "tied to traffic at a neighbouring turn",
    "tied to traffic at a neighbouring turn",
  ],
};

const count = new Intl.NumberFormat("en-US");

function plural(n: number, one: string, many = `${one}s`): string {
  return `${count.format(n)} ${n === 1 ? one : many}`;
}

/** "a, b and c". */
function listText(parts: string[]): string {
  if (parts.length < 2) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** A type's words without the aside: "track limits / off track (race control)" -> "track
 * limits / off track". */
function shortType(text: string): string {
  const cut = text.indexOf(" (");
  return cut > 0 ? text.slice(0, cut) : text;
}

function lostText(m: Mistake): string {
  return m.time_lost_s === null ? "–" : `${m.time_lost_s.toFixed(2)} s`;
}

/** Sorted lap numbers as ranges, "2–11, 13, 15–57" (the span when there are many gaps). */
export function lapRanges(laps: number[]): string {
  const runs: [number, number][] = [];
  for (const n of laps) {
    const last = runs[runs.length - 1];
    if (last && n === last[1] + 1) last[1] = n;
    else runs.push([n, n]);
  }
  if (runs.length > 5) return `${laps[0]}–${laps[laps.length - 1]} (with gaps)`;
  return runs.map(([a, b]) => (a === b ? String(a) : `${a}–${b}`)).join(", ");
}

/** The footer's left-out sentence, biggest count first ("" when nothing was left out). */
export function leftOutText(leftOut: Record<string, number>): string {
  const parts = Object.entries(leftOut)
    .filter(([, n]) => n > 0)
    .sort((p, q) => q[1] - p[1])
    .map(([kind, n]) => {
      const words = kind.split("_").join(" ");
      const [one, many] = LEFT_OUT[kind] ?? [words, words];
      return `${count.format(n)} ${n === 1 ? one : many}`;
    });
  return parts.length ? `Left out: ${listText(parts)}.` : "";
}

/** All the mistakes at one turn: one marker on the map. */
interface Spot {
  turn: string;
  x: number;
  y: number;
  rows: number[]; // indices into data.mistakes, best rank first
  lost: number; // total time lost, s (mistakes without a time count 0)
  radius: number; // px
}

/** Something on the map that labels and badges keep clear of: a centre and half sizes in map
 * units, or a circle of radius `hw` (a marker) when `round`. */
interface Box {
  x: number;
  y: number;
  hw: number;
  hh: number;
  round?: boolean;
}

/** How far apart two boxes are; negative when they overlap. */
function gap(a: Box, b: Box): number {
  if (a.round && b.round) return Math.hypot(a.x - b.x, a.y - b.y) - a.hw - b.hw;
  if (a.round || b.round) {
    const [c, box] = a.round ? [a, b] : [b, a];
    const dx = Math.max(Math.abs(c.x - box.x) - box.hw, 0);
    const dy = Math.max(Math.abs(c.y - box.y) - box.hh, 0);
    return Math.hypot(dx, dy) - c.hw;
  }
  const gx = Math.abs(a.x - b.x) - (a.hw + b.hw);
  const gy = Math.abs(a.y - b.y) - (a.hh + b.hh);
  return gx < 0 && gy < 0 ? Math.max(gx, gy) : Math.hypot(Math.max(gx, 0), Math.max(gy, 0));
}

/** The gap from `box` to the nearest of `others` (Infinity when there are none). */
function clearance(box: Box, others: Box[]): number {
  return others.reduce((least, o) => Math.min(least, gap(box, o)), Infinity);
}

/** Where a turn sits on the map: its marker point, else its apex on the outline. */
function turnPoint(track: TrackPayload, turn: string, apexM?: number): [number, number] | null {
  const hit = track.turns.find((t) => t.label === turn);
  if (hit) return [hit.x, hit.y];
  const apex = track.corners.find((c) => c.label === turn)?.apex_m ?? apexM;
  const n = track.outline.length;
  if (apex === undefined || !n || !(track.step_m > 0)) return null;
  return track.outline[((Math.round(apex / track.step_m) % n) + n) % n];
}

function buildSpots(data: MistakeList, track: TrackPayload): { spots: Spot[]; faint: Spot[] } {
  const byTurn = new Map<string, Spot>();
  data.mistakes.forEach((m, i) => {
    let spot = byTurn.get(m.turn);
    if (!spot) {
      const at = turnPoint(track, m.turn, m.apex_m);
      if (!at) return;
      spot = { turn: m.turn, x: at[0], y: at[1], rows: [], lost: 0, radius: MARKER.min };
      byTurn.set(m.turn, spot);
    }
    spot.rows.push(i);
    // A corner where the driver gained time (a cut that saved time) counts as nothing lost: a
    // negative total would give the marker a NaN radius and stop the map from drawing.
    spot.lost += Math.max(0, m.time_lost_s ?? 0);
  });
  const spots = [...byTurn.values()];
  const most = Math.max(0, ...spots.map((s) => s.lost));
  for (const s of spots) {
    if (most > 0) s.radius += (MARKER.max - MARKER.min) * Math.sqrt(s.lost / most);
  }
  const faint = new Map<string, Spot>();
  for (const m of data.mistakes) {
    for (const turn of m.also_turns) {
      if (byTurn.has(turn) || faint.has(turn)) continue;
      const at = turnPoint(track, turn);
      if (at) faint.set(turn, { turn, x: at[0], y: at[1], rows: [], lost: 0, radius: 0 });
    }
  }
  return { spots, faint: [...faint.values()] };
}

function renderHeader(root: HTMLElement, data: MistakeList, headingLevel?: HeadingLevel): void {
  const header = htmlEl("header", "fm-header", undefined, root);
  const titleRow = htmlEl("div", "title-row", undefined, header);
  htmlEl(headingTag(headingLevel, 1), "title", `${data.event} ${data.year} · ${data.session}`, titleRow);
  htmlEl("span", "chip", data.driver ?? "Whole field", titleRow);
  const [one, many] = data.driver ? ["lap", "laps"] : ["driver-lap", "driver-laps"];
  const { scored_driver_laps: scored, eligible_driver_laps: eligible } = data.coverage;
  const meta = data.scored
    ? `${plural(data.distinct_mistakes, "mistake")} from ` +
      `${plural(data.flagged, "flagged corner")} · ${plural(scored, one, many)} scored`
    : `No scored corners${data.driver ? ` for ${data.driver}` : ""}`;
  htmlEl("p", "meta", meta, header);
  if (data.coverage.low && eligible > 0) {
    const whose = data.driver ? `${data.driver}'s ` : "the ";
    const warn = htmlEl("p", "warn", undefined, header);
    htmlEl("span", "warn-icon", "⚠", warn).setAttribute("aria-hidden", "true");
    htmlEl("strong", undefined, "Low coverage: ", warn);
    warn.append(
      `only ${Math.round((100 * scored) / eligible)}% of ${whose}${plural(eligible, one, many)} ` +
        "the detectors score have usable telemetry, so few flags here doesn't mean few mistakes.",
    );
  }
}

function renderFooter(
  root: HTMLElement,
  data: MistakeList,
  view: ViewState,
  changed: () => void,
): void {
  const footer = htmlEl("footer", "fm-footer", undefined, root);
  const leftOut = leftOutText(data.left_out);
  if (leftOut) htmlEl("p", undefined, leftOut, footer);
  const laps = data.coverage.laps
    .filter((n): n is number => typeof n === "number" && Number.isFinite(n))
    .sort((p, q) => p - q);
  if (laps.length) {
    const of = data.coverage.session_laps ? ` of ${data.coverage.session_laps}` : "";
    htmlEl("p", undefined, `Laps scored: ${lapRanges(laps)}${of}.`, footer);
  }
  if (data.note && data.mistakes.length) htmlEl("p", undefined, data.note, footer);
  if (data.ranking && data.mistakes.length) {
    const details = htmlEl("details", undefined, undefined, footer);
    details.open = view.rankingOpen;
    details.addEventListener("toggle", () => {
      view.rankingOpen = details.open;
      changed();
    });
    htmlEl("summary", undefined, "How these are ranked", details);
    const text = data.ranking.charAt(0).toUpperCase() + data.ranking.slice(1);
    htmlEl("p", undefined, text.endsWith(".") ? text : `${text}.`, details);
  }
}

/**
 * One row of the list: rank, driver (left out when the list is one driver's), time lost, where
 * and what, Explain, and the explanation.
 */
function renderRow(
  list: HTMLElement,
  m: Mistake,
  rank: number,
  oneDriver: boolean,
  onExplain: RenderOptions["onExplain"],
  explainLabel: string,
): HTMLLIElement {
  const row = htmlEl("li", "row", undefined, list);
  // Who, which lap and which turn, for markSelected.
  row.dataset.driver = m.driver;
  row.dataset.lap = String(m.lap_number);
  row.dataset.turn = m.turn;
  htmlEl("span", "rank", String(rank), row);
  // The driver leads the row, with the lap and turn on the next line; in one driver's list the
  // lap and turn lead instead.
  const who = htmlEl("div", "who", undefined, row);
  if (!oneDriver) {
    htmlEl("strong", undefined, m.driver, who);
    if (m.team) htmlEl("span", "team", m.team, who);
  }
  const where = htmlEl("div", "where", undefined, row);
  const place = oneDriver ? who : where;
  htmlEl(oneDriver ? "strong" : "span", undefined, `Lap ${m.lap_number} · T${m.turn}`, place);
  if (m.also_turns.length) {
    htmlEl("span", "also", `also ${m.also_turns.map((t) => `T${t}`).join(", ")}`, place);
  }
  // Screen readers get the words the column head gives sighted readers (aria-label on a plain
  // span isn't read reliably, so the words are visually hidden text).
  const lost = htmlEl("span", "lost", undefined, row);
  if (m.time_lost_s === null) {
    htmlEl("span", undefined, lostText(m), lost).setAttribute("aria-hidden", "true");
    htmlEl("span", "vh", "time lost not measured", lost);
    lost.title = "No usual laps to measure the time lost against";
  } else {
    lost.append(lostText(m));
    htmlEl("span", "vh", " lost", lost);
  }

  const typeText = m.type_text || m.type;
  const type = htmlEl("span", "type", shortType(typeText), where);
  if (shortType(typeText) !== typeText) type.title = typeText;
  if (m.secondary_type) {
    const second = TYPE_SHORT[m.secondary_type] ?? m.secondary_type.split("_").join(" ");
    htmlEl("span", "type secondary", `+ ${second}`, where);
  }

  if (onExplain) {
    const button = htmlEl("button", "explain", undefined, row);
    button.type = "button";
    const what = `${m.driver}'s lap ${m.lap_number}, turn ${m.turn}`;
    // "Explain AAA's lap 9, turn 3"; other words read "Show telemetry for AAA's lap 9, turn 3".
    const name = explainLabel === "Explain" ? `Explain ${what}` : `${explainLabel} for ${what}`;
    const show = (text: string, label: string, title = "") => {
      button.textContent = text;
      button.setAttribute("aria-label", label);
      button.title = title;
    };
    show(explainLabel, name);
    // aria-disabled rather than disabled while the message goes out: a disabled button drops
    // the keyboard focus.
    button.addEventListener("click", () => {
      if (button.getAttribute("aria-disabled") === "true") return;
      const sent = onExplain(m);
      if (!sent) return;
      show("Asking…", `Asking about ${what}…`);
      button.setAttribute("aria-disabled", "true");
      void sent
        .catch(() => false)
        .then((ok) => {
          button.removeAttribute("aria-disabled");
          if (ok) show("Asked ✓", `Asked about ${what}`, "Sent to the conversation");
          else show(explainLabel, `${name}: sending failed, try again`, "Couldn't send it");
        });
    });
  } else {
    row.classList.add("no-explain");
  }
  htmlEl("p", "why", m.explanation, row);
  return row;
}

interface MapHandlers {
  /** The marker under the pointer or the arrow keys (index into `order`), or -1. */
  onActive: (i: number) => void;
  /** A marker was clicked or Enter pressed on it. */
  onPick: (turn: string) => void;
  /** Esc on the map. */
  onClear: () => void;
}

interface MapView {
  /** Show the state: the marker that's active, the turns of the row in hand, the filter. */
  paint: (active: string | null, rowTurns: Set<string>, selected: string | null) => void;
}

/** The track map with a marker per spot into `panel`, `boxW` px wide at most. */
function renderMap(
  panel: HTMLElement,
  data: MistakeList,
  track: TrackPayload,
  spots: Spot[],
  faint: Spot[],
  order: Spot[],
  boxW: number,
  on: MapHandlers,
): MapView {
  const scale = Math.min(boxW / track.width, MAP_MAX_H / track.height);
  const u = 1 / scale; // map units per screen px
  const svg = svgEl("svg", {
    class: "map",
    viewBox: `0 0 ${track.width} ${track.height}`,
    width: Math.round(track.width * scale),
    height: Math.round(track.height * scale),
    tabindex: 0,
    role: "img",
    "aria-label":
      `Track map of ${data.location} with the mistakes marked at ` +
      `${order.map((s) => `turn ${s.turn} (${plural(s.rows.length, "mistake")})`).join(", ")}. ` +
      "Use the left and right arrow keys to step through them, and Enter to list only one turn.",
  });
  panel.append(svg);

  // The road: a wide recessive band with a thin surface line down the middle.
  const d = `M${track.outline.map(([x, y]) => `${x},${y}`).join("L")}Z`;
  const road = { d, fill: "none", "stroke-linejoin": "round" };
  svgEl("path", { ...road, stroke: "var(--grid)", "stroke-width": ROAD_PX * u }, svg);
  svgEl("path", { ...road, stroke: "var(--surface)", "stroke-width": 1.5 * u }, svg);

  // Start/finish: a tick across the road, and an arrow for the direction of travel. Both are
  // kept clear of the rank badges.
  const fixed: Box[] = [];
  if (track.start) {
    const { x, y } = track.start;
    const len = Math.hypot(track.start.dx, track.start.dy) || 1;
    const [ux, uy] = [track.start.dx / len, track.start.dy / len];
    const half = (ROAD_PX / 2 + 3) * u;
    const tick = { x1: x - uy * half, y1: y + ux * half, x2: x + uy * half, y2: y - ux * half };
    svgEl("line", { ...tick, class: "start", "stroke-width": 2 * u }, svg);
    const [ax, ay] = [x + ux * 14 * u, y + uy * 14 * u];
    const tip = [
      [ax + ux * 5 * u, ay + uy * 5 * u],
      [ax - uy * 3.5 * u, ay + ux * 3.5 * u],
      [ax + uy * 3.5 * u, ay - ux * 3.5 * u],
    ];
    svgEl("path", { d: `M${tip.map((p) => p.join(",")).join("L")}Z`, class: "arrow" }, svg);
    fixed.push({ x, y, hw: half, hh: half }, { x: ax, y: ay, hw: 5 * u, hh: 5 * u });
  }

  // Turn numbers where the TV graphics put them, the turns with a mistake in ink. A number with
  // a mistake that its own (grown) marker would cover, or that runs into another such number or
  // another marker, moves just clear of its marker: the same way out when that spot is free of
  // those, else the nearest free direction around the marker, a little farther out if need be.
  // The numbers without a mistake give way (below).
  const spotAt = new Map(spots.map((s) => [s.turn, s]));
  const markerBox = (s: Spot, grow = 0): Box => {
    const r = (s.radius + grow) * u;
    return { x: s.x, y: s.y, hw: r, hh: r, round: true };
  };
  const within = (b: Box): Box => ({
    ...b,
    x: Math.min(Math.max(b.x, b.hw), track.width - b.hw),
    y: Math.min(Math.max(b.y, b.hh), track.height - b.hh),
  });
  const pad = 1.5 * u;
  const labels = track.turns.map((t) => ({
    label: t.label,
    hit: spotAt.has(t.label),
    shown: true,
    box: within({
      x: t.tx,
      y: t.ty,
      hw: (t.label.length * 0.3 + 0.1) * LABEL_PX * u,
      hh: 0.55 * LABEL_PX * u,
    }),
  }));
  const hits = labels.filter((l) => l.hit);
  const othersOf = (l: (typeof labels)[number], s: Spot): Box[] => [
    ...hits.filter((o) => o !== l).map((o) => o.box),
    ...spots.filter((o) => o !== s).map((o) => markerBox(o)),
  ];
  const covered = hits
    .filter((l) => {
      const s = spotAt.get(l.label)!;
      return gap(l.box, markerBox(s, MARKER.grow)) < pad || clearance(l.box, othersOf(l, s)) < 0;
    })
    .sort((p, q) => spotAt.get(p.label)!.rows[0] - spotAt.get(q.label)!.rows[0]);
  for (const l of covered) {
    const s = spotAt.get(l.label)!;
    const own = markerBox(s, MARKER.grow);
    const others = othersOf(l, s);
    const away = Math.atan2(l.box.y - s.y, l.box.x - s.x);
    let room = -Infinity;
    search: for (const farther of [0, 6]) {
      for (const turn of [0, 35, -35, 70, -70, 105, -105, 140, -140, 180]) {
        const angle = away + (turn * Math.PI) / 180;
        const [cx, cy] = [Math.cos(angle), Math.sin(angle)];
        const reach =
          own.hw + pad + farther * u + Math.abs(cx) * l.box.hw + Math.abs(cy) * l.box.hh;
        const box = within({ ...l.box, x: s.x + cx * reach, y: s.y + cy * reach });
        // Free: clear of its own marker (not pushed back onto it at the map's edge) and a few
        // px from every other number with a mistake and every other marker.
        const free = Math.min(clearance(box, others) - 4 * u, gap(box, own) - pad / 2);
        if (free > room) [l.box, room] = [box, free];
        if (free >= 0) break search;
      }
    }
  }
  // A number without a mistake that still runs into a number with one, a marker or a number
  // already kept is left out: in a tight section (Baku's castle) neighbouring numbers would
  // read as one ("1110").
  const kept = labels.filter((l) => l.hit).map((l) => l.box);
  const markers = spots.map((s) => markerBox(s));
  for (const l of labels.filter((o) => !o.hit)) {
    l.shown = clearance(l.box, [...kept, ...markers]) >= 0;
    if (l.shown) kept.push(l.box);
  }
  fixed.push(...kept);

  // Neighbouring turns showing the same moment as a listed mistake: faint rings, under the
  // markers (a copy above them shows while their row is in hand, see `lit` below).
  const faintRings = faint.map((s) => {
    const attrs = { cx: s.x, cy: s.y, r: MARKER.faint * u, "stroke-width": 1.5 * u };
    return svgEl("circle", { ...attrs, class: "faint" }, svg);
  });

  // The badges (the best rank at each turn), best rank first, each on whichever shoulder of
  // its marker is farthest from the turn numbers, the start line, the other markers and the
  // badges already placed, and inside the map.
  const badgeAt = new Map<string, { box: Box; rank: string }>();
  for (const s of order) {
    const rank = String(s.rows[0] + 1);
    const [bw, bh] = [(BADGE_PX.pad + BADGE_PX.digit * rank.length) * u, BADGE_PX.height * u];
    const off = (s.radius * 0.7 + 4) * u;
    const others = [
      ...fixed,
      ...spots.filter((o) => o !== s).map((o) => markerBox(o)),
      ...[...badgeAt.values()].map((b) => b.box),
    ];
    let best: Box | undefined;
    let room = -Infinity;
    for (const [sx, sy] of [[1, -1], [-1, -1], [1, 1], [-1, 1]]) {
      const box = { x: s.x + sx * (off + bw / 4), y: s.y + sy * off, hw: bw / 2, hh: bh / 2 };
      const out = within(box);
      const inside = out.x === box.x && out.y === box.y;
      const free = clearance(box, others) - (inside ? 0 : track.width);
      if (free > room + 0.5 * u) [best, room] = [box, free];
    }
    badgeAt.set(s.turn, { box: best!, rank });
  }

  // A marker per turn with a mistake, the biggest drawn first so the small ones stay on top;
  // the turn numbers above them (so a marker never hides a number), and above those the rings
  // that mark what is in hand. Nothing is ever moved or raised: moving the node under the
  // pointer would swallow its click.
  const layer = svgEl("g", {}, svg);
  const labelLayer = svgEl("g", { class: "labels" }, svg);
  const ringLayer = svgEl("g", { class: "rings" }, svg);
  for (const t of labels.filter((l) => l.shown)) {
    svgEl(
      "text",
      {
        x: t.box.x,
        y: t.box.y,
        "text-anchor": "middle",
        "dominant-baseline": "central",
        "font-size": LABEL_PX * u,
        "stroke-width": 3 * u,
        class: t.hit ? "turn hit" : "turn",
      },
      labelLayer,
    ).textContent = t.label;
  }
  const lit = faint.map((s) => {
    const g = svgEl("g", { class: "faint-lit" }, ringLayer);
    svgEl("circle", { cx: s.x, cy: s.y, r: (MARKER.faint + 2) * u, class: "halo" }, g);
    const attrs = { cx: s.x, cy: s.y, r: MARKER.faint * u, "stroke-width": 1.5 * u };
    svgEl("circle", { ...attrs, class: "faint" }, g);
    return g;
  });

  type Marker = { g: SVGGElement; dot: SVGCircleElement; ring: SVGCircleElement };
  const nodes = new Map<string, Marker>();
  for (const s of [...spots].sort((p, q) => q.radius - p.radius)) {
    const g = svgEl("g", { class: "spot" }, layer);
    const at = { cx: s.x, cy: s.y };
    const hitR = Math.max(MARKER.hit, s.radius + MARKER.ring) * u;
    svgEl("circle", { ...at, r: hitR, class: "hit" }, g);
    const dot = svgEl("circle", { ...at, r: s.radius * u, "stroke-width": 2 * u, class: "dot" }, g);
    const ringR = (s.radius + MARKER.ring) * u;
    const ring = svgEl("circle", { ...at, r: ringR, "stroke-width": 1.5 * u, class: "pick-ring" });
    ringLayer.append(ring);

    const { box, rank } = badgeAt.get(s.turn)!;
    const badge = svgEl("g", { class: "badge" }, g);
    const rect = { x: box.x - box.hw, y: box.y - box.hh, width: 2 * box.hw, height: 2 * box.hh };
    svgEl("rect", { ...rect, rx: box.hh }, badge);
    svgEl(
      "text",
      {
        x: box.x,
        y: box.y,
        "text-anchor": "middle",
        "dominant-baseline": "central",
        "font-size": BADGE_PX.font * u,
      },
      badge,
    ).textContent = rank;
    nodes.set(s.turn, { g, dot, ring });

    g.addEventListener("pointerenter", () => crosshair.show(order.indexOf(s)));
    g.addEventListener("pointerleave", () => {
      if (document.activeElement !== svg) crosshair.hide();
    });
    g.addEventListener("click", () => on.onPick(s.turn));
  }

  const crosshair = crosshairKeys(svg, order.length, on.onActive);
  svg.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && crosshair.index >= 0) {
      event.preventDefault();
      on.onPick(order[crosshair.index].turn);
    } else if (event.key === "Escape") {
      on.onClear();
    }
  });

  return {
    paint(active, rowTurns, selected) {
      for (const s of spots) {
        const node = nodes.get(s.turn)!;
        const hot = s.turn === active || rowTurns.has(s.turn);
        node.dot.setAttribute("r", String((s.radius + (hot ? MARKER.grow : 0)) * u));
        node.ring.classList.toggle("on", hot || s.turn === selected);
        node.g.classList.toggle("dim", selected !== null && s.turn !== selected && !hot);
      }
      faint.forEach((s, k) => {
        faintRings[k].classList.toggle("on", rowTurns.has(s.turn));
        lit[k].classList.toggle("on", rowTurns.has(s.turn));
      });
    },
  };
}

/** Draw the page into `root`, replacing what was there, at `root`'s current width. */
export function render(root: HTMLElement, data: MistakeList, opts: RenderOptions = {}): void {
  const view: ViewState = {
    turn: opts.view?.turn ?? null,
    expanded: [...(opts.view?.expanded ?? [])],
    rankingOpen: opts.view?.rankingOpen ?? false,
  };
  const changed = () => opts.onViewChange?.({ ...view, expanded: [...view.expanded] });

  root.replaceChildren();
  renderHeader(root, data, opts.headingLevel);

  const frameW = root.clientWidth;
  const wide = frameW >= WIDE_PX;
  const track = data.mistakes.length ? data.track : null;
  const { spots, faint } = track ? buildSpots(data, track) : { spots: [], faint: [] };
  const order = [...spots].sort((p, q) => p.rows[0] - q.rows[0]); // best rank first
  if (!spots.some((s) => s.turn === view.turn)) view.turn = null;

  const body = htmlEl("div", "fm-body", undefined, root);
  body.classList.toggle("wide", wide && spots.length > 0);
  const mapPanel = spots.length ? htmlEl("figure", "map-panel", undefined, body) : undefined;
  const listPanel = htmlEl("section", "list-panel", undefined, body);
  listPanel.setAttribute("aria-label", "Mistakes");

  if (!data.mistakes.length) {
    const fallback =
      data.flagged === 0
        ? `Nothing flagged: no corner stood out among the ${count.format(data.scored)} scored.`
        : "No flagged corner looks like a driving mistake.";
    htmlEl("p", "empty", data.note || fallback, listPanel);
    renderFooter(root, data, view, changed);
    return;
  }

  // ---- The list.
  const head = htmlEl("div", "list-head", undefined, listPanel);
  const scope = htmlEl("span", "scope", undefined, head);
  const showAll = htmlEl("button", "show-all", "Show all", head);
  showAll.type = "button";
  htmlEl("span", "unit", "Time lost", head);
  const list = htmlEl("ol", "mistakes", undefined, listPanel);
  list.setAttribute("role", "list"); // Safari drops list semantics with list-style: none
  const oneDriver = data.driver !== null;
  const explainLabel = opts.explainLabel ?? "Explain";
  const rows = data.mistakes.map((m, i) =>
    renderRow(list, m, i + 1, oneDriver, opts.onExplain, explainLabel),
  );

  // ---- The map, and the state the list and the map share.
  let hotRow = -1; // the row under the pointer or holding focus
  let active = -1; // the marker under the pointer or the arrow keys (index into `order`)
  let map: MapView | undefined;
  let readout: HTMLElement | undefined;

  const setFilter = (turn: string | null) => {
    view.turn = turn;
    applyFilter();
    paint();
    changed();
  };

  if (mapPanel && track) {
    const content = Math.max(body.clientWidth, 240);
    const share = Math.max(MAP_COLUMN.min, content * MAP_COLUMN.share);
    const boxW = wide ? Math.round(Math.min(MAP_COLUMN.max, share)) : content;
    if (wide) mapPanel.style.width = `${boxW}px`;
    map = renderMap(mapPanel, data, track, spots, faint, order, boxW, {
      onActive: (i) => {
        active = i;
        paint();
      },
      onPick: (turn) => setFilter(view.turn === turn ? null : turn),
      onClear: () => setFilter(null),
    });
    const legend = htmlEl("p", "legend", undefined, mapPanel);
    const key = htmlEl("span", "key", undefined, legend);
    htmlEl("span", "key-dot", undefined, key).setAttribute("aria-hidden", "true");
    key.append("size: time lost at the turn; number: best rank there");
    if (faint.length) {
      const also = htmlEl("span", "key", undefined, legend);
      htmlEl("span", "key-faint", undefined, also).setAttribute("aria-hidden", "true");
      also.append("same moment at a neighbouring turn");
    }
    readout = htmlEl("p", "readout", undefined, mapPanel);
    readout.setAttribute("aria-live", "polite");
  }

  function applyFilter() {
    let shown = 0;
    rows.forEach((row, i) => {
      row.hidden = view.turn !== null && data.mistakes[i].turn !== view.turn;
      if (!row.hidden) shown += 1;
    });
    const [n, all] = [data.mistakes.length, data.distinct_mistakes];
    if (view.turn !== null) scope.textContent = `T${view.turn}: ${shown} of ${n}`;
    else if (n < all) scope.textContent = `Top ${n} of ${all}`;
    else scope.textContent = n === 1 ? "The only one" : `All ${n}`;
    showAll.hidden = view.turn === null;
  }
  showAll.addEventListener("click", () => {
    const focused = document.activeElement === showAll;
    setFilter(null);
    // The button hides itself; hand the focus to the map, where the filter is set.
    if (focused) root.querySelector<SVGSVGElement>("svg.map")?.focus();
  });

  function spotText(s: Spot): string {
    const lost = s.lost > 0 ? ` · ${s.lost.toFixed(2)} s lost` : "";
    const best = `best ranked ${s.rows[0] + 1}`;
    return `T${s.turn} · ${plural(s.rows.length, "mistake")}${lost} · ${best}`;
  }

  function paint() {
    const rowTurns = new Set<string>();
    const m = hotRow >= 0 ? data.mistakes[hotRow] : undefined;
    if (m) [m.turn, ...m.also_turns].forEach((t) => rowTurns.add(t));
    const spot = active >= 0 ? order[active] : undefined;
    rows.forEach((row, i) => row.classList.toggle("hl", data.mistakes[i].turn === spot?.turn));
    map?.paint(spot?.turn ?? null, rowTurns, view.turn);
    if (!readout) return;
    let lines: [string, string];
    if (spot) {
      const action = view.turn === spot.turn ? "show all again" : "list only these";
      lines = [spotText(spot), `Click or Enter: ${action}`];
    } else if (view.turn !== null) {
      lines = [`Listing T${view.turn} only`, "Click it again or Esc: show all"];
    } else {
      lines = ["", "Hover a marker or use ← → for details"];
    }
    // Rewrite the live region only when its words change: hovering or tabbing through rows
    // repaints, and a rewrite with the same words is read out again.
    if (lines.join("\n") === readout.dataset.text) return;
    readout.dataset.text = lines.join("\n");
    readout.replaceChildren();
    if (lines[0]) htmlEl("strong", undefined, lines[0], readout);
    htmlEl("span", undefined, lines[1], readout);
  }

  rows.forEach((row, i) => {
    row.addEventListener("pointerenter", () => {
      hotRow = i;
      paint();
    });
    row.addEventListener("pointerleave", () => {
      if (hotRow !== i || row.contains(document.activeElement)) return;
      hotRow = -1;
      paint();
    });
    row.addEventListener("focusin", () => {
      hotRow = i;
      paint();
    });
    row.addEventListener("focusout", (event) => {
      if (row.contains(event.relatedTarget as Node | null) || row.matches(":hover")) return;
      hotRow = -1;
      paint();
    });
  });

  // Explanations clamp to two lines; one that overflows opens in full on click or Enter.
  // Measured now, with the map in place and before the filter hides rows (which measure 0).
  rows.forEach((row, i) => {
    const why = row.querySelector<HTMLElement>(".why")!;
    const isOpen = view.expanded.includes(i);
    why.classList.toggle("open", isOpen);
    if (!isOpen && why.scrollHeight <= why.clientHeight + 1) return;
    why.classList.add("toggle");
    why.tabIndex = 0;
    why.setAttribute("role", "button");
    why.setAttribute("aria-expanded", String(isOpen));
    const toggle = () => {
      const open = !why.classList.contains("open");
      why.classList.toggle("open", open);
      why.setAttribute("aria-expanded", String(open));
      view.expanded = open ? [...view.expanded, i] : view.expanded.filter((k) => k !== i);
      changed();
    };
    why.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      toggle();
    });
    row.addEventListener("click", (event) => {
      if ((event.target as Element).closest("button")) return;
      if (window.getSelection()?.toString()) return; // selecting text, not opening
      toggle();
    });
  });

  applyFilter();
  paint();
  renderFooter(root, data, view, changed);
}

/** A turn as the rows carry it: "9a" for "T9a" or "9A". */
function turnKey(turn: string): string {
  return turn.trim().replace(/^T/i, "").toLowerCase();
}

/**
 * Mark one mistake's row as the one in hand (the website's open corner): `aria-current` and a
 * highlight, without redrawing, so the focus and the reader's view stay where they are. Null
 * clears the mark. Call it again after each `render`, which rebuilds the rows.
 */
export function markSelected(
  root: HTMLElement,
  selected: { driver: string; lap_number: number; turn: string } | null,
): void {
  for (const row of root.querySelectorAll<HTMLLIElement>("li.row")) {
    const on =
      selected !== null &&
      row.dataset.driver?.toUpperCase() === selected.driver.toUpperCase() &&
      row.dataset.lap === String(selected.lap_number) &&
      turnKey(row.dataset.turn ?? "") === turnKey(selected.turn);
    row.classList.toggle("selected", on);
    if (on) row.setAttribute("aria-current", "true");
    else row.removeAttribute("aria-current");
  }
}
