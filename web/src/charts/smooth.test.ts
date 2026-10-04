/**
 * The smooth chart lines (`monotonePath`, and `linePath` built on it, in dom.ts): monotone
 * cubic interpolation along x, the curve of d3's curveMonotoneX. Everything is checked on the
 * path text the charts draw, after its rounding to 0.1 px: the line passes through every
 * point, never overshoots between two points, keeps flat runs flat, has no kinks, lifts the
 * pen at a gap, and traces the same curve drawn backwards (a band's lower edge is).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { linePath, monotonePath, type Series } from "./dom.ts";

type Pt = [number, number];
interface Segment {
  from: Pt;
  type: "L" | "C";
  pts: number[]; // L: x, y; C: c1x, c1y, c2x, c2y, x, y
}

/** Split a path into its subpaths (one per move), each a start point and its segments. */
function parse(d: string): { start: Pt; segs: Segment[] }[] {
  const subpaths: { start: Pt; segs: Segment[] }[] = [];
  let at: Pt = [NaN, NaN];
  for (const [, cmd, args] of d.matchAll(/([MLCZ])([^MLCZ]*)/g)) {
    const v = args.split(/[ ,]/).filter(Boolean).map(Number);
    assert.ok(v.every(Number.isFinite), `finite numbers in ${cmd}${args}`);
    if (cmd === "M") {
      assert.equal(v.length, 2);
      at = [v[0], v[1]];
      subpaths.push({ start: at, segs: [] });
    } else if (cmd === "L" || cmd === "C") {
      assert.equal(v.length, cmd === "L" ? 2 : 6);
      subpaths[subpaths.length - 1].segs.push({ from: at, type: cmd, pts: v });
      at = [v[v.length - 2], v[v.length - 1]];
    }
  }
  return subpaths;
}

/** The points a subpath passes through: its start and each segment's end. */
function onCurve(sub: { start: Pt; segs: Segment[] }): Pt[] {
  const end = (s: Segment): Pt => [s.pts[s.pts.length - 2], s.pts[s.pts.length - 1]];
  return [sub.start, ...sub.segs.map(end)];
}

/** A point on a segment at t in [0, 1]. */
function at(seg: Segment, t: number): Pt {
  const [x0, y0] = seg.from;
  if (seg.type === "L") return [x0 + (seg.pts[0] - x0) * t, y0 + (seg.pts[1] - y0) * t];
  const [ax, ay, bx, by, x1, y1] = seg.pts;
  const u = 1 - t;
  const b = (p0: number, p1: number, p2: number, p3: number) =>
    u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
  return [b(x0, ax, bx, x1), b(y0, ay, by, y1)];
}

const round = (p: Pt): Pt => [Number(p[0].toFixed(1)), Number(p[1].toFixed(1))];

/**
 * d3-shape's curveMonotoneX (src/curve/monotone.js, ISC licence), transcribed with its state
 * machine as it is, to check that the charts draw the same curve. Formatted like dom.ts.
 */
function d3MonotoneX(xs: number[], ys: number[]): string {
  let out = "";
  const f = (v: number) => v.toFixed(1);
  let [x0, y0, x1, y1, t0, n] = [NaN, NaN, NaN, NaN, NaN, 0];
  const sign = (v: number) => (v < 0 ? -1 : 1);
  const slope3 = (x2: number, y2: number) => {
    const h0 = x1 - x0;
    const h1 = x2 - x1;
    const s0 = (y1 - y0) / (h0 || (h1 < 0 ? -0 : 0));
    const s1 = (y2 - y1) / (h1 || (h0 < 0 ? -0 : 0));
    const p = (s0 * h1 + s1 * h0) / (h0 + h1);
    return (sign(s0) + sign(s1)) * Math.min(Math.abs(s0), Math.abs(s1), 0.5 * Math.abs(p)) || 0;
  };
  const slope2 = (t: number) => {
    const h = x1 - x0;
    return h ? ((3 * (y1 - y0)) / h - t) / 2 : t;
  };
  const point = (ta: number, tb: number) => {
    const dx = (x1 - x0) / 3;
    out += `C${f(x0 + dx)},${f(y0 + dx * ta)},${f(x1 - dx)},${f(y1 - dx * tb)},${f(x1)},${f(y1)}`;
  };
  for (let i = 0; i < xs.length; i++) {
    const [x, y] = [xs[i], ys[i]];
    let t1 = NaN;
    if (x === x1 && y === y1) continue; // coincident points are ignored
    if (n === 0) {
      n = 1;
      out += `M${f(x)},${f(y)}`;
    } else if (n === 1) {
      n = 2;
    } else if (n === 2) {
      n = 3;
      t1 = slope3(x, y);
      point(slope2(t1), t1);
    } else {
      t1 = slope3(x, y);
      point(t0, t1);
    }
    [x0, x1, y0, y1, t0] = [x1, x, y1, y, t1];
  }
  if (n === 2) out += `L${f(x1)},${f(y1)}`;
  else if (n === 3) point(t0, slope2(t0));
  return out;
}

/** A small seeded random generator (mulberry32), so every run checks the same series. */
function random(seed: number): () => number {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Series shaped like the charts' data, in px: a speed trace through a tight corner, a throttle
 * trace with flat 0 and 100 runs, a time gap, race positions by lap (whole numbers that hold
 * for laps), spikes, and uneven spacing along x.
 */
function series(rand: () => number, kind: number): { xs: number[]; ys: number[] } {
  const n = 3 + Math.floor(rand() * 60);
  const xs: number[] = [];
  const ys: number[] = [];
  let x = 40 + rand() * 10;
  let y = 50 + rand() * 100;
  for (let i = 0; i < n; i++) {
    xs.push(x);
    x += kind === 5 ? 0.2 + rand() * 30 : 4 + rand() * 2;
    if (kind === 0) {
      // Speed: a V-shaped trough with a little noise (y grows downwards, so the trough is high).
      const k = i / (n - 1);
      y = 20 + 140 * (1 - Math.abs(2 * k - 1)) ** 0.7 + rand() * 3;
    } else if (kind === 1) {
      // Throttle: held flat at 0 or 100 for a while, then ramped.
      y = rand() < 0.6 ? y : rand() < 0.5 ? 10 : 54;
    } else if (kind === 2) {
      y += (rand() - 0.5) * 6; // a time gap drifting
    } else if (kind === 3) {
      y = rand() < 0.7 ? y : 8 + 15 * Math.floor(rand() * 20); // positions: rows 15 px apart
    } else if (kind === 4) {
      y = rand() < 0.5 ? 100 : 100 + (rand() - 0.5) * 160; // spikes
    } else {
      y = 50 + rand() * 120;
    }
    ys.push(y);
  }
  return { xs, ys };
}

function eachSeries(count: number, check: (xs: number[], ys: number[], label: string) => void) {
  const rand = random(20261003);
  for (let i = 0; i < count; i++) {
    const kind = i % 6;
    const { xs, ys } = series(rand, kind);
    check(xs, ys, `series ${i} (kind ${kind}, ${xs.length} points)`);
  }
}

test("the line passes through every point, in order", () => {
  eachSeries(300, (xs, ys, label) => {
    const subs = parse(monotonePath(xs, ys));
    assert.equal(subs.length, 1, label);
    assert.deepEqual(onCurve(subs[0]), xs.map((x, i) => round([x, ys[i]])), label);
  });
});

test("the curve never overshoots between two points, and never runs backwards in x", () => {
  let segments = 0;
  eachSeries(300, (xs, ys, label) => {
    for (const seg of parse(monotonePath(xs, ys))[0].segs) {
      const [x0, y0] = seg.from;
      const [x1, y1] = seg.pts.slice(-2);
      const [lo, hi] = [Math.min(y0, y1) - 1e-9, Math.max(y0, y1) + 1e-9];
      let lastX = x0;
      for (let k = 0; k <= 64; k++) {
        const [x, y] = at(seg, k / 64);
        assert.ok(y >= lo && y <= hi, `${label}: y ${y} outside [${y0}, ${y1}]`);
        assert.ok(x >= lastX - 1e-9 && x <= x1 + 1e-9, `${label}: x ${x} runs back`);
        lastX = x;
      }
      segments++;
    }
  });
  assert.ok(segments > 5000, `checked ${segments} segments`);
});

test("a tight trough keeps its lowest point where the data has it", () => {
  // Speed through a hairpin in px (y grows downwards): the apex sample is the slowest.
  const xs = [0, 5, 10, 15, 20, 25, 30, 35, 40];
  const ys = [20, 40, 90, 150, 172, 150, 95, 45, 22];
  const segs = parse(monotonePath(xs, ys))[0].segs;
  let lowest = -Infinity;
  for (const seg of segs) {
    for (let k = 0; k <= 64; k++) lowest = Math.max(lowest, at(seg, k / 64)[1]);
  }
  assert.equal(lowest, 172);
});

test("flat runs stay flat", () => {
  const xs = [0, 10, 20, 30, 40, 50, 60, 70, 80];
  const ys = [10, 20, 20, 20, 20, 5, 5, 30, 30];
  const segs = parse(monotonePath(xs, ys))[0].segs;
  assert.equal(segs.length, 8);
  for (const seg of segs) {
    const [y0, y1] = [seg.from[1], seg.pts[5]];
    if (y0 === y1) assert.deepEqual([seg.pts[1], seg.pts[3]], [y0, y0], `flat at ${y0}`);
  }
  // Race positions that hold for laps: every lap between two equal positions is flat.
  eachSeries(120, (xs, ys, label) => {
    for (const seg of parse(monotonePath(xs, ys))[0].segs) {
      if (seg.type === "C" && seg.from[1] === seg.pts[5]) {
        assert.deepEqual([seg.pts[1], seg.pts[3]], [seg.from[1], seg.from[1]], label);
      }
    }
  });
});

test("no kinks: the curve's direction carries straight on through every point", () => {
  // Scaled up 1000 times (the curve scales with its points), so the 0.1 px rounding is
  // negligible next to the control arms on either side of a point.
  eachSeries(300, (xs, ys, label) => {
    const big = (v: number) => v * 1000;
    const segs = parse(monotonePath(xs.map(big), ys.map(big)))[0].segs;
    for (let i = 1; i < segs.length; i++) {
      const [px, py] = segs[i].from;
      const [ix, iy] = [px - segs[i - 1].pts[2], py - segs[i - 1].pts[3]]; // arriving
      const [ox, oy] = [segs[i].pts[0] - px, segs[i].pts[1] - py]; // leaving
      const sine = (ix * oy - iy * ox) / (Math.hypot(ix, iy) * Math.hypot(ox, oy));
      assert.ok(Math.abs(sine) < 5e-3 && ix * ox + iy * oy > 0, `${label}: kink at point ${i}`);
    }
  });
});

test("a missing value lifts the pen, and each run is smoothed on its own", () => {
  const xs = [0, 10, 20, 30, 40, 50, 60, 70, 80];
  const ys: Series = [1, 2, null, 3, null, 4, 5, 9, null];
  const d = linePath(xs, ys);
  assert.equal(
    d,
    monotonePath([0, 10], [1, 2]) + monotonePath([30], [3]) + monotonePath([50, 60, 70], [4, 5, 9]),
  );
  const subs = parse(d);
  assert.deepEqual(
    subs.map((s) => [s.start, s.segs.map((g) => g.type).join("")]),
    [
      [[0, 1], "L"],
      [[30, 3], ""],
      [[50, 4], "CC"],
    ],
  );
  assert.equal(linePath(xs, xs.map(() => null)), "");
  assert.equal(linePath([], []), "");
  // Without gaps, linePath is one smooth run: compare-laps' gap area reuses it for its edge.
  eachSeries(30, (xs, ys, label) => assert.equal(linePath(xs, ys), monotonePath(xs, ys), label));
});

test("one point is a move, two points a straight line", () => {
  assert.equal(monotonePath([], []), "");
  assert.equal(monotonePath([5], [7]), "M5.0,7.0");
  assert.equal(monotonePath([5], [7], "L"), "L5.0,7.0");
  assert.equal(monotonePath([0, 10], [1, 2.25]), "M0.0,1.0L10.0,2.3");
  assert.equal(monotonePath([0, 10], [1, 2], "L"), "L0.0,1.0L10.0,2.0");
  assert.equal(linePath([3], [4]), "M3.0,4.0");
  assert.equal(linePath([0, 1, 2], [null, 4, null]), "M1.0,4.0");
  // A repeated point is drawn once (as d3 does): two equal points are one point.
  assert.equal(monotonePath([1, 1], [2, 2]), "M1.0,2.0");
});

test("drawn backwards, the curve is the same curve (a band's lower edge)", () => {
  /** A path's pieces as [type, ...numbers], from its start. */
  const pieces = (d: string) => {
    const [sub] = parse(d);
    return [sub.start, ...sub.segs.map((s) => [s.type, ...s.pts])];
  };
  /** The same pieces turned around: from the last point to the first, controls swapped. */
  const turned = (d: string) => {
    const [sub] = parse(d);
    const pts = onCurve(sub);
    const segs = sub.segs.map((s, i) =>
      s.type === "L" ? ["L", ...pts[i]] : ["C", s.pts[2], s.pts[3], s.pts[0], s.pts[1], ...pts[i]],
    );
    return [pts[pts.length - 1], ...segs.reverse()];
  };
  const check = (xs: number[], ys: number[], label: string) =>
    assert.deepEqual(
      pieces(monotonePath([...xs].reverse(), [...ys].reverse())),
      turned(monotonePath(xs, ys)),
      label,
    );
  eachSeries(300, check);
  // Uneven spacing, flats and a vertical step (two values at one distance).
  check([0, 3, 3, 10, 31, 32, 60], [5, 9, 20, 20, 20, 2, 40], "edge cases");
  check([0, 10], [1, 2], "two points");
});

test("the curve is d3's curveMonotoneX", () => {
  eachSeries(300, (xs, ys, label) =>
    assert.equal(monotonePath(xs, ys), d3MonotoneX(xs, ys), label),
  );
  // Edge cases: repeated points, a vertical step, a value repeated at one distance.
  const cases: [number[], number[]][] = [
    [[0, 0, 10, 20], [1, 1, 5, 2]],
    [[0, 10, 10, 20, 30], [1, 5, 9, 9, 0]],
    [[0, 10, 10, 10, 20], [1, 5, 9, 2, 0]],
    [[0, 10, 20, 20], [1, 5, 9, 9]],
    [[0, 5, 10], [3, 3, 3]],
  ];
  for (const [xs, ys] of cases) {
    const d = monotonePath(xs, ys);
    assert.equal(d, d3MonotoneX(xs, ys), `${xs} / ${ys}`);
    assert.doesNotMatch(d, /NaN|Infinity/);
  }
});

test("a fixed input always gives the same path", () => {
  // Worked by hand: secant slopes 1, 0, 2 give tangents 1.5, 0, 0, 3 (flat at both ends of
  // the flat run, one-sided at the ends of the line).
  assert.equal(
    monotonePath([0, 10, 20, 30], [0, 10, 10, 30]),
    "M0.0,0.0C3.3,5.0,6.7,10.0,10.0,10.0C13.3,10.0,16.7,10.0,20.0,10.0C23.3,10.0,26.7,20.0,30.0,30.0",
  );
  // Secant slopes 1 and 2: the inner tangent is 1.5 (Steffen's), the ends 0.75 and 2.25.
  assert.equal(
    monotonePath([0, 10, 20], [0, 10, 30]),
    "M0.0,0.0C3.3,2.5,6.7,5.0,10.0,10.0C13.3,15.0,16.7,22.5,20.0,30.0",
  );
  assert.equal(
    linePath([0, 10, 20, 30, 40], [0, 10, null, 30, 25]),
    "M0.0,0.0L10.0,10.0M30.0,30.0L40.0,25.0",
  );
});
