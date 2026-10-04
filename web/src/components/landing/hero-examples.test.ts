import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { CornerChart } from "../../charts/explain-corner.ts";
import { FEATURED, featuredHref, LANDING_CORNER, type FeaturedCorner } from "../../content/featured.ts";
import {
  entryCaption,
  fitInto,
  HALO_R,
  HERO_IDS,
  HERO_URL,
  heroCaption,
  heroEntries,
  heroExample,
  heroFile,
  heroGeometry,
  heroSnapshot,
  labelRect,
  linkLabel,
  loadHero,
  parseHeroFile,
  pickAnnouncement,
  pickLabel,
  project,
  readoutLabel,
  resetHero,
  ROAD_WIDTH,
  shortType,
  SIMPLIFY_PX,
  simplifyLine,
  startHero,
  VIEW,
  type HeroExample,
  type Point,
} from "./hero-examples.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// The MCP dev host's synthetic explain_corner payload: the tool's own shapes, no real positions.
const sample = JSON.parse(readFileSync(path.resolve(HERE, "../../mcp-app/sample-explain-corner.json"), "utf8")) as {
  structuredContent: CornerChart;
};
const corner = sample.structuredContent;

/** A featured entry for the sample corner (its driver, lap and turn). */
const SAMPLE_ENTRY: FeaturedCorner = {
  id: "sample_q_10_t3",
  kind: "flagged",
  args: { event: corner.event, year: corner.year, session: "Q", driver: corner.driver, lap: corner.lap_number, turn: corner.turn },
  name: "Sample",
  typeLabel: corner.type_text,
  blurb: "A synthetic corner.",
};

const example = heroExample(corner, SAMPLE_ENTRY) as HeroExample;

const oneDecimal = (v: number) => Math.abs(v * 10 - Math.round(v * 10)) < 1e-9;

// --- What the file keeps --------------------------------------------------------------------

test("the hero examples are flagged featured corners, starting on the landing example", () => {
  assert.equal(HERO_IDS[0], LANDING_CORNER.id);
  assert.ok(HERO_IDS.length >= 3 && HERO_IDS.length <= 5, `${HERO_IDS.length} examples`);
  assert.equal(new Set(HERO_IDS).size, HERO_IDS.length);
  const entries = heroEntries();
  assert.equal(entries.length, HERO_IDS.length, "every id is in FEATURED");
  for (const entry of entries) {
    assert.equal(entry.kind, "flagged", entry.id);
    assert.ok(entry.name, `${entry.id} has a name for the caption and the picker`);
    assert.ok(FEATURED.includes(entry));
  }
});

test("heroExample keeps what the card draws, from the payload and the entry", () => {
  assert.ok(example, "the sample is drawable");
  assert.deepEqual(Object.keys(example).sort(), [
    "driver",
    "event",
    "href",
    "id",
    "lap",
    "location",
    "map",
    "name",
    "session_code",
    "session_name",
    "time_lost_s",
    "turn",
    "type_text",
    "year",
  ]);
  assert.equal(example.id, SAMPLE_ENTRY.id);
  assert.equal(example.driver, corner.driver);
  assert.equal(example.name, "Sample");
  assert.equal(example.event, corner.event);
  assert.equal(example.location, corner.location);
  assert.equal(example.year, corner.year);
  assert.equal(example.session_code, corner.session_code);
  assert.equal(example.session_name, corner.session);
  assert.equal(example.lap, corner.lap_number);
  assert.equal(example.turn, corner.turn);
  assert.equal(example.time_lost_s, corner.time_lost_s);
  assert.equal(example.type_text, corner.type_text);
  assert.equal(example.href, featuredHref(SAMPLE_ENTRY));
  assert.deepEqual(Object.keys(example.map).sort(), ["corner", "height", "label", "outline", "start", "width"]);
  assert.equal(example.map.width, corner.map?.width);
  assert.equal(example.map.height, corner.map?.height);
});

test("heroExample rounds coordinates to 0.1 px, drops repeats and the closing point", () => {
  const map = corner.map!;
  const { outline, corner: apex, start } = example.map;
  for (const [x, y] of outline) assert.ok(oneDecimal(x) && oneDecimal(y), `${x},${y}`);
  assert.ok(outline.length >= 3 && outline.length <= map.outline.length);
  for (let i = 1; i < outline.length; i++) assert.notDeepEqual(outline[i], outline[i - 1], `repeat at ${i}`);
  assert.notDeepEqual(outline[0], outline[outline.length - 1], "the path closes itself");
  assert.deepEqual(apex, [Math.round(map.apex.x * 10) / 10, Math.round(map.apex.y * 10) / 10]);
  assert.ok(start && oneDecimal(start.x) && oneDecimal(start.y));
  assert.ok(Math.abs(Math.hypot(start.dx, start.dy) - 1) < 0.01, "the direction stays a unit vector");

  // A noisy outline: values off the 0.1 grid and a point repeated once rounded.
  const noisy = structuredClone(corner);
  noisy.map!.outline = [
    [10.04, 20.06],
    [10.01, 20.09],
    [30.26, 40.44],
    [50, 10],
    [10.04, 20.06],
  ];
  const got = heroExample(noisy, SAMPLE_ENTRY)!;
  assert.deepEqual(got.map.outline, [
    [10, 20.1],
    [30.3, 40.4],
    [50, 10],
  ]);
});

/** How far `p` is from the closed line through `line`. */
function toLine(p: Point, line: Point[]): number {
  let best = Infinity;
  for (let i = 0; i < line.length; i++) {
    const [a, b] = [line[i], line[(i + 1) % line.length]];
    const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
    const len2 = dx * dx + dy * dy;
    const t = len2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2)) : 0;
    best = Math.min(best, Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy));
  }
  return best;
}

test("heroExample leaves out only points the line doesn't need", () => {
  const map = corner.map!;
  // Every point of the tool's outline stays within the tolerance (plus the 0.05 px rounding).
  for (const p of map.outline) assert.ok(toLine(p as Point, example.map.outline) <= SIMPLIFY_PX + 0.05 + 1e-9, `${p}`);
  // The kept points are the tool's own points, rounded, in the tool's order.
  const rounded = map.outline.map(([x, y]) => `${Math.round(x * 10) / 10},${Math.round(y * 10) / 10}`);
  let at = -1;
  for (const [x, y] of example.map.outline) {
    const next = rounded.indexOf(`${x},${y}`, at + 1);
    assert.ok(next > at, `${x},${y} in order`);
    at = next;
  }
});

test("simplifyLine drops what lies on the line and keeps every bend", () => {
  // A straight run sampled every unit, then a right angle: only the ends and the bend stay.
  const straight: Point[] = [
    ...Array.from({ length: 11 }, (_, i): Point => [i, 0]),
    ...Array.from({ length: 10 }, (_, i): Point => [10, i + 1]),
  ];
  assert.deepEqual(simplifyLine(straight), [
    [0, 0],
    [10, 0],
    [10, 10],
  ]);
  // A wobble above the tolerance stays; one below it goes.
  assert.equal(simplifyLine([[0, 0], [5, 0.3], [10, 0]]).length, 3);
  assert.equal(simplifyLine([[0, 0], [5, 0.05], [10, 0]]).length, 2);
  // Short lines come back as they are, as a copy.
  const two: Point[] = [[0, 0], [1, 1]];
  assert.deepEqual(simplifyLine(two), two);
  assert.notEqual(simplifyLine(two), two);
  // Whatever is dropped lies within the tolerance of what is kept (a noisy circle).
  const circle = Array.from({ length: 200 }, (_, i): Point => {
    const a = (i / 200) * 2 * Math.PI;
    const r = 100 + Math.sin(i * 1.7) * 0.04;
    return [Math.round((150 + r * Math.cos(a)) * 10) / 10, Math.round((150 + r * Math.sin(a)) * 10) / 10];
  });
  const kept = simplifyLine(circle);
  assert.ok(kept.length < circle.length && kept.length > 20, `${kept.length} of ${circle.length}`);
  for (const p of circle) assert.ok(toLine(p, kept) <= SIMPLIFY_PX + 1e-9);
});

test("the turn's point is the payload's apex, and its label spot the tool's own turn label", () => {
  const map = corner.map!;
  const turn = map.turns.find((t) => t.label === corner.turn)!;
  assert.ok(turn, "the sample has its turn on the map");
  assert.deepEqual(example.map.corner, [map.apex.x, map.apex.y]);
  assert.deepEqual(example.map.label, [turn.tx, turn.ty]);

  const noTurn = structuredClone(corner);
  noTurn.map!.turns = noTurn.map!.turns.filter((t) => t.label !== corner.turn);
  assert.equal(heroExample(noTurn, SAMPLE_ENTRY)?.map.label, null);
});

test("only flagged corners with time lost and a map become examples", () => {
  assert.equal(heroExample({ ...corner, flagged: false }, SAMPLE_ENTRY), null);
  assert.equal(heroExample({ ...corner, time_lost_s: null }, SAMPLE_ENTRY), null);
  assert.equal(heroExample({ ...corner, time_lost_s: -0.2 }, SAMPLE_ENTRY), null);
  assert.equal(heroExample({ ...corner, map: null }, SAMPLE_ENTRY), null);
  assert.equal(heroExample(corner, { ...SAMPLE_ENTRY, kind: "energy" }), null);
});

// --- The guard ------------------------------------------------------------------------------

test("the guard accepts a good file and keeps its order", () => {
  const second = { ...example, id: "second" };
  const body = JSON.parse(JSON.stringify(heroFile([example, second])));
  assert.deepEqual(
    parseHeroFile(body)?.map((e) => e.id),
    [example.id, "second"],
  );
  // A driver without a name, and a map without a start line or turn label, still draw.
  const plain = { ...example, name: null, map: { ...example.map, start: null, label: null } };
  assert.equal(parseHeroFile(heroFile([plain]))?.length, 1);
});

test("the guard rejects missing fields and bad numbers", () => {
  assert.equal(parseHeroFile(null), null);
  assert.equal(parseHeroFile([]), null);
  assert.equal(parseHeroFile({ examples: [example] }), null, "no version");
  assert.equal(parseHeroFile({ version: 2, examples: [example] }), null, "another version");
  assert.equal(parseHeroFile(heroFile([])), null, "nothing to show");

  const broken: [string, unknown][] = [
    ["no driver", { ...example, driver: undefined }],
    ["empty turn", { ...example, turn: "" }],
    ["no map", { ...example, map: undefined }],
    ["no outline", { ...example, map: { ...example.map, outline: undefined } }],
    ["a two-point outline", { ...example, map: { ...example.map, outline: example.map.outline.slice(0, 2) } }],
    ["a string coordinate", { ...example, map: { ...example.map, outline: [["1", 2], ...example.map.outline.slice(1)] } }],
    ["a null coordinate", { ...example, map: { ...example.map, corner: [null, 3] } }],
    ["no corner", { ...example, map: { ...example.map, corner: undefined } }],
    ["a bad label", { ...example, map: { ...example.map, label: [1] } }],
    ["a bad start", { ...example, map: { ...example.map, start: { x: 1, y: 2, dx: "a", dy: 0 } } }],
    ["zero width", { ...example, map: { ...example.map, width: 0 } }],
    ["time lost as text", { ...example, time_lost_s: "0.79" }],
    ["no time lost", { ...example, time_lost_s: 0 }],
    ["a fractional lap", { ...example, lap: 16.5 }],
    ["a string year", { ...example, year: "2026" }],
    ["a link off the site", { ...example, href: "https://example.com/" }],
  ];
  for (const [why, bad] of broken) {
    assert.equal(parseHeroFile(heroFile([bad as HeroExample])), null, why);
    // One bad example among good ones is dropped; the rest still show.
    assert.deepEqual(
      parseHeroFile(heroFile([bad as HeroExample, example]))?.map((e) => e.id),
      [example.id],
      why,
    );
  }
  // JSON has no NaN or Infinity, but a hand-built body might.
  assert.equal(parseHeroFile(heroFile([{ ...example, time_lost_s: Number.NaN }])), null);
  assert.equal(parseHeroFile(heroFile([{ ...example, map: { ...example.map, width: Infinity } }])), null);
});

// --- Fitting --------------------------------------------------------------------------------

function bounds(points: Point[]): [number, number, number, number] {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

test("fitting keeps the aspect and stays inside the box", () => {
  const shapes: Point[][] = [
    example.map.outline, // the sample, square-ish
    [
      [0, 0],
      [300, 0],
      [300, 170],
      [0, 170],
    ], // wide, like Melbourne
    [
      [10, 10],
      [212, 10],
      [212, 310],
      [10, 310],
    ], // tall, like a north-up map
    [
      [5, 5],
      [50, 5],
      [50, 6],
    ], // a sliver
  ];
  const pad = 28;
  for (const shape of shapes) {
    const fit = fitInto(shape, VIEW, pad);
    const [x0, y0, x1, y1] = bounds(shape);
    const [px0, py0, px1, py1] = bounds(shape.map((p) => project(p, fit)));
    const eps = 1e-6;
    // Aspect kept: one scale for both axes.
    assert.ok(Math.abs((px1 - px0) / (x1 - x0) - (py1 - py0) / (y1 - y0)) < eps, "same scale on both axes");
    // Inside the box, the padding kept, and centred.
    assert.ok(px0 >= pad - eps && py0 >= pad - eps && px1 <= VIEW.w - pad + eps && py1 <= VIEW.h - pad + eps, "inside");
    assert.ok(Math.abs(px0 - (VIEW.w - px1)) < eps && Math.abs(py0 - (VIEW.h - py1)) < eps, "centred");
    // The tighter side touches its padding: the shape is as big as it can be.
    assert.ok(Math.abs(px0 - pad) < eps || Math.abs(py0 - pad) < eps, "as large as it fits");
  }
});

test("the drawing: a closed path in the box, the corner on it, the label clear of the road and the halo", () => {
  const g = heroGeometry(example);
  assert.match(g.path, /^M[\d.]+,[\d.]+(L[\d.]+,[\d.]+)+Z$/);
  const pts = g.path
    .slice(1, -1)
    .split("L")
    .map((p) => p.split(",").map(Number) as Point);
  assert.equal(pts.length, example.map.outline.length);
  for (const [x, y] of pts) assert.ok(x >= 0 && x <= VIEW.w && y >= 0 && y <= VIEW.h);
  // The apex is a point of the lap, so it sits on (or right by) the line.
  const near = Math.min(...pts.map(([x, y]) => Math.hypot(x - g.corner[0], y - g.corner[1])));
  assert.ok(near < 4, `corner ${near.toFixed(1)} from the line`);
  // The label: inside the box, clear of the road's edge and outside the halo.
  const rect = labelRect(g.label.x, g.label.y, g.label.text);
  assert.equal(g.label.text, `T${example.turn}`);
  assert.ok(rect[0] >= 0 && rect[1] >= 0 && rect[2] <= VIEW.w && rect[3] <= VIEW.h, "label inside");
  assert.ok(g.label.clearance >= ROAD_WIDTH / 2 + 4, `label ${g.label.clearance} from the line`);
  const dx = Math.max(rect[0] - g.corner[0], 0, g.corner[0] - rect[2]);
  const dy = Math.max(rect[1] - g.corner[1], 0, g.corner[1] - rect[3]);
  assert.ok(Math.hypot(dx, dy) >= HALO_R, "label outside the halo");
  // It leans the way the tool's own map put the number (the same side of the apex).
  const fit = fitInto(example.map.outline);
  const want = project(example.map.label!, fit);
  const side = (want[0] - g.corner[0]) * (g.label.x - g.corner[0]) + (want[1] - g.corner[1]) * (g.label.y - g.corner[1]);
  assert.ok(side > 0, "label on the tool's side of the apex");
  // The start line crosses the road at the outline's first point.
  assert.ok(g.start);
  const mid: Point = [(g.start.x1 + g.start.x2) / 2, (g.start.y1 + g.start.y2) / 2];
  assert.ok(Math.hypot(mid[0] - pts[0][0], mid[1] - pts[0][1]) < 0.2);
});

// --- Words ----------------------------------------------------------------------------------

test("the readout, caption and labels use the example's own words and numbers", () => {
  assert.equal(readoutLabel(example), `${example.driver} · ${example.location} ${example.year} · Lap ${example.lap}`);
  assert.equal(
    readoutLabel({ ...example, driver: "NOR", location: "Baku", year: 2026, lap: 16 }),
    "NOR · Baku 2026 · Lap 16",
  );
  assert.equal(readoutLabel({ ...example, location: " ", event: "Azerbaijan Grand Prix" }), `${example.driver} · Azerbaijan GP ${example.year} · Lap ${example.lap}`);
  assert.equal(shortType("track limits / off track (race control)"), "track limits");
  assert.equal(shortType("over-slowing"), "over-slowing");
  assert.equal(shortType("late braking (lock-up)"), "late braking");
  assert.equal(shortType(" (odd) "), "(odd)", "never empty");
  const nor = { name: "Norris", driver: "NOR", year: 2026, event: "Azerbaijan Grand Prix", session_name: "Race" };
  assert.equal(heroCaption(nor), "Norris · 2026 Azerbaijan GP · Race · real circuit");
  assert.equal(heroCaption({ ...nor, name: null }), "NOR · 2026 Azerbaijan GP · Race · real circuit");
  assert.equal(entryCaption(LANDING_CORNER), "Norris · 2026 Azerbaijan GP · Race · real circuit");
  assert.equal(pickLabel({ ...example, name: "Sainz", location: "Melbourne", year: 2026 }), "Show Sainz, Melbourne 2026");
  assert.equal(pickLabel({ ...example, name: null, location: "", event: "Italian Grand Prix" }), `Show ${example.driver}, Italian GP ${example.year}`);
  assert.match(linkLabel(example), /Open this corner in the mistake explorer\.$/);
  assert.match(pickAnnouncement(example), new RegExp(`turn ${example.turn}, ${example.time_lost_s.toFixed(2)} s lost\\.$`));
  // Never a verdict or a reviewer.
  for (const text of [linkLabel(example), pickAnnouncement(example), heroCaption(example)])
    assert.doesNotMatch(text, /review|verdict|checked|accura/i);
});

// --- Loading --------------------------------------------------------------------------------

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

test("the file loads from its static path; a missing or broken file gives null, never a throw", async () => {
  const asked: string[] = [];
  const ok = (async (input: RequestInfo | URL) => {
    asked.push(String(input));
    return json(heroFile([example]));
  }) as typeof fetch;
  assert.deepEqual((await loadHero(ok))?.map((e) => e.id), [example.id]);
  assert.deepEqual(asked, [HERO_URL]);
  assert.equal(await loadHero((async () => json({ error: "nope" }, 404)) as typeof fetch), null);
  assert.equal(await loadHero((async () => new Response("<html>", { status: 200 })) as typeof fetch), null);
  assert.equal(await loadHero((async () => json({ version: 1, examples: [{ id: "x" }] })) as typeof fetch), null);
  assert.equal(
    await loadHero((async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof fetch),
    null,
  );
});

test("the store loads once, then holds the examples or says the file is missing", async () => {
  resetHero();
  let calls = 0;
  startHero(async () => {
    calls++;
    return [example];
  });
  startHero(async () => {
    calls++;
    return null;
  });
  assert.equal(heroSnapshot().status, "loading");
  await new Promise((r) => setTimeout(r, 0));
  const ready = heroSnapshot();
  assert.equal(calls, 1);
  assert.equal(ready.status, "ready");
  assert.deepEqual(ready.status === "ready" ? ready.examples.map((e) => e.id) : [], [example.id]);

  resetHero();
  startHero(async () => null);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(heroSnapshot().status, "missing");
  resetHero();
});

test("the real snapshots, when this checkout has them, draw every example in the box", (t) => {
  let text: string;
  try {
    text = readFileSync(path.resolve(HERE, "../../../public/snapshots/hero.json"), "utf8");
  } catch {
    t.skip("no public/snapshots/hero.json (written by scripts/snapshots.ts from a running API)");
    return;
  }
  const examples = parseHeroFile(JSON.parse(text));
  assert.ok(examples, "hero.json passes the guard");
  assert.deepEqual(
    examples.map((e) => e.id),
    [...HERO_IDS],
  );
  assert.ok(Buffer.byteLength(text) < 20 * 1024, `${(Buffer.byteLength(text) / 1024).toFixed(1)} KB`);
  for (const e of examples) {
    const g = heroGeometry(e);
    const rect = labelRect(g.label.x, g.label.y, g.label.text);
    assert.ok(rect[0] >= 0 && rect[1] >= 0 && rect[2] <= VIEW.w && rect[3] <= VIEW.h, `${e.id} label inside`);
    assert.ok(g.label.clearance >= ROAD_WIDTH / 2 + 4, `${e.id} label ${g.label.clearance} from the line`);
  }
});
