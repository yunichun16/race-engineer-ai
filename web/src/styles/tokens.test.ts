import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

// The contrast table of the design spec (section a), checked on the values tokens.css really
// declares, in both themes. The page is never a flat colour: it sits under three pools of light
// and a grid (the fixed ambient layer, globals.css), and the glass surfaces are translucent. So
// every pair is checked against the backdrops it can really sit on, composited the way the
// browser paints them:
// - the page alone, and under each pool at its brightest with a grid line on top ("the lit page");
// - each glass fill over each of those pages, and the opaque fallback (reduced transparency);
// - the sticky header's glass over the worst thing that scrolls under it;
// - the menu sheet over the dimmed page, and over a dimmed white report figure.
// A control's outline is checked against both sides: the fill inside it and the page outside it.

const CSS = readFileSync(new URL("./tokens.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const CHART_CSS = readFileSync(new URL("../charts/styles/tokens.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const GLOBALS = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

type Tokens = Record<string, string>;

/** The declarations of the first rule whose selector is exactly `selector`. */
function block(selector: string, css = CSS): Tokens {
  const at = css.indexOf(`${selector} {`);
  assert.notEqual(at, -1, `no ${selector} rule`);
  const body = css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
  const out: Tokens = {};
  for (const decl of body.split(";")) {
    const colon = decl.indexOf(":");
    if (colon === -1) continue;
    out[decl.slice(0, colon).trim()] = decl.slice(colon + 1).trim();
  }
  return out;
}

const LIGHT = block(":root");
const DARK = block(':root[data-theme="dark"]');
const DARK_SYSTEM = block(':root:not([data-theme="light"])');

type Rgba = [number, number, number, number];

function parseColor(text: string): Rgba {
  if (text === "transparent") return [0, 0, 0, 0];
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text);
  if (hex) {
    const h = hex[1].length === 3 ? [...hex[1]].map((c) => c + c).join("") : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)).concat(1) as Rgba;
  }
  const rgb = /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+)\s*)?\)$/.exec(text);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), rgb[4] === undefined ? 1 : Number(rgb[4])];
  throw new Error(`can't read the colour ${text}`);
}

/** `fg` drawn over the opaque `bg`. */
function over(fg: Rgba, bg: Rgba): Rgba {
  const a = fg[3];
  return [0, 1, 2].map((i) => fg[i] * a + bg[i] * (1 - a)).concat(1) as Rgba;
}

function luminance([r, g, b]: Rgba): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** WCAG contrast of `fg` (laid over `bg` first when translucent) against the opaque `bg`. */
function ratio(fg: Rgba, bg: Rgba): number {
  assert.equal(bg[3], 1, "a backdrop must be opaque");
  const [hi, lo] = [luminance(over(fg, bg)), luminance(bg)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function contrast(tokens: Tokens, fgName: string, bgName: string): number {
  return ratio(parseColor(tokens[fgName]), parseColor(tokens[bgName]));
}

type Backdrops = Record<string, Rgba>;

/** Every backdrop a theme's colours can sit on, by name. */
function backdrops(t: Tokens): Record<"page" | "glass" | "strong" | "opaque" | "nav" | "sheet", Backdrops> {
  const c = (name: string) => {
    assert.ok(t[name], `missing ${name}`);
    return parseColor(t[name]);
  };
  const page: Backdrops = { page: c("--page") };
  for (const n of [1, 2, 3]) page[`page under pool ${n} + grid`] = over(c("--bg-grid"), over(c(`--pool-${n}`), c("--page")));
  const glass: Backdrops = {};
  const strong: Backdrops = {};
  const nav: Backdrops = {};
  const sheet: Backdrops = {};
  for (const [name, bg] of Object.entries(page)) {
    glass[`glass on ${name}`] = over(c("--glass"), bg);
    strong[`glass-strong on ${name}`] = over(c("--glass-strong"), bg);
    nav[`nav on ${name}`] = over(c("--glass-nav"), bg);
    sheet[`sheet on ${name}`] = over(c("--glass-strong"), over(c("--backdrop"), bg));
  }
  // Content scrolls under the sticky header: a white report figure, a lime button, solid ink.
  for (const name of ["--paper", "--accent", "--color-text-primary", "--color-background-secondary", "--glass-solid"]) {
    nav[`nav over ${name}`] = over(c("--glass-nav"), c(name));
  }
  sheet["sheet over a dimmed figure"] = over(c("--glass-strong"), over(c("--backdrop"), c("--paper")));
  const opaque: Backdrops = { "glass-solid": c("--glass-solid"), raised: c("--color-background-secondary") };
  return { page, glass, strong, opaque, nav, sheet };
}

/** The lowest ratio of `fg` over `bgs`, with the backdrop that gave it. */
function worst(fg: Rgba, bgs: Backdrops): [number, string] {
  let low: [number, string] = [Infinity, ""];
  for (const [name, bg] of Object.entries(bgs)) {
    const r = ratio(fg, bg);
    if (r < low[0]) low = [r, name];
  }
  return low;
}

// The site's own colours on top of the host tokens, all declared in both themes.
const COLOURS = [
  "--color-background-primary",
  "--color-background-secondary",
  "--color-text-primary",
  "--color-text-secondary",
  "--color-border-tertiary",
  "--color-border-secondary",
  "--color-ring-primary",
  "--color-text-warning",
  "--color-text-danger",
  "--page",
  "--faint",
  "--glass",
  "--glass-strong",
  "--glass-nav",
  "--glass-solid",
  "--edge",
  "--hairline",
  "--highlight",
  "--backdrop",
  "--paper",
  "--accent",
  "--accent-hover",
  "--accent-ink",
  "--accent-edge",
  "--accent-line",
  "--accent-text",
  "--pool-1",
  "--pool-2",
  "--pool-3",
  "--bg-grid",
  "--track",
  "--trace",
];

// The charts' series colours (chart-owned, charts/styles/tokens.css): driver A and driver B.
const SERIES = {
  light: block(":root", CHART_CSS),
  dark: block('[data-theme="dark"]', CHART_CSS),
};

for (const [theme, own] of [["light", LIGHT], ["dark", DARK]] as const) {
  // The dark block declares every colour itself; the merge only supplies the shared non-colour tokens.
  const tokens: Tokens = { ...LIGHT, ...own };
  const c = (name: string) => parseColor(tokens[name]);
  const b = backdrops(tokens);
  const glassy = { ...b.glass, ...b.strong };

  /** Asserts the lowest ratio of `fgName` over `bgs` reaches `min`. */
  const atLeast = (fgName: string, bgs: Backdrops, min: number, what: string) => {
    const [r, where] = worst(c(fgName), bgs);
    assert.ok(r >= min, `${theme}: ${fgName} ${what}: ${r.toFixed(2)}:1 on ${where}, needs ${min}:1`);
  };

  test(`${theme} theme: text-primary reads everywhere, the header included (4.5:1)`, () => {
    atLeast("--color-text-primary", { ...b.page, ...glassy, ...b.opaque, ...b.sheet }, 4.5, "on the page, glass and sheet");
    atLeast("--color-text-primary", b.nav, 4.5, "in the header, over whatever scrolls under it");
  });

  test(`${theme} theme: text-secondary reads on the lit page, glass, raised and opaque surfaces and the menu sheet (4.5:1)`, () => {
    atLeast("--color-text-secondary", { ...b.page, ...glassy, ...b.opaque, ...b.sheet }, 4.5, "as text");
  });

  test(`${theme} theme: text-secondary as the header's icons (3:1); header text is always ink`, () => {
    atLeast("--color-text-secondary", b.nav, 3, "as icons in the header");
  });

  test(`${theme} theme: danger is small text on glass and opaque surfaces (4.5:1) and large text on the page (3:1)`, () => {
    atLeast("--color-text-danger", { ...glassy, ...b.opaque }, 4.5, "as small text on glass");
    atLeast("--color-text-danger", b.page, 3, "as large text on the page");
  });

  test(`${theme} theme: warning is small text on glass and opaque surfaces (4.5:1), an icon on the page (3:1)`, () => {
    atLeast("--color-text-warning", { ...glassy, "glass-solid": b.opaque["glass-solid"] }, 4.5, "as small text on glass");
    atLeast("--color-text-warning", b.page, 3, "as an icon on the page");
  });

  test(`${theme} theme: accent-text is small text on the page, glass and opaque surfaces (4.5:1)`, () => {
    atLeast("--accent-text", { ...b.page, ...b.glass, "glass-solid": b.opaque["glass-solid"] }, 4.5, "as small text");
  });

  test(`${theme} theme: the focus ring and accent-line strokes show on every surface (3:1)`, () => {
    for (const name of ["--color-ring-primary", "--accent-line"]) {
      atLeast(name, { ...b.page, ...glassy, "glass-solid": b.opaque["glass-solid"], ...b.sheet }, 3, "as a ring or stroke");
    }
    atLeast("--color-ring-primary", b.nav, 3, "as a ring in the header");
  });

  test(`${theme} theme: a control's outline is 3:1 against its own fill and against the page around it`, () => {
    // WCAG 1.4.11 compares the outline with the colours next to it: inside and outside.
    const line = c("--color-border-secondary");
    for (const [pageName, pageBg] of Object.entries(b.page)) {
      for (const fillName of ["--glass", "--glass-strong", "--glass-solid"]) {
        const inside = over(c(fillName), pageBg);
        const drawn = over(line, inside);
        const vsInside = ratio(drawn, inside);
        const vsOutside = ratio(drawn, pageBg);
        assert.ok(vsInside >= 3, `${theme}: outline on ${fillName} over ${pageName}, against the fill: ${vsInside.toFixed(2)}:1`);
        assert.ok(vsOutside >= 3, `${theme}: outline on ${fillName} over ${pageName}, against the page: ${vsOutside.toFixed(2)}:1`);
      }
    }
  });

  test(`${theme} theme: lime fills carry their ink (4.5:1), at rest, on hover and as the text selection`, () => {
    assert.ok(contrast(tokens, "--accent-ink", "--accent") >= 4.5);
    assert.ok(contrast(tokens, "--accent-ink", "--accent-hover") >= 4.5);
  });

  test(`${theme} theme: the series colours show on chart cards (3:1 for graphics)`, () => {
    for (const name of ["--a", "--b"]) {
      const colour = parseColor(SERIES[theme][name]);
      const [r, where] = worst(colour, { ...b.strong, "glass-solid": b.opaque["glass-solid"] });
      assert.ok(r >= 3, `${theme}: ${name} ${SERIES[theme][name]} on ${where}: ${r.toFixed(2)}:1`);
    }
  });

  test(`${theme} theme: the host pairs the charts read hold on the opaque surface`, () => {
    for (const [fg, bg, min] of [
      ["--color-text-primary", "--color-background-primary", 4.5],
      ["--color-text-primary", "--color-background-secondary", 4.5],
      ["--color-text-secondary", "--color-background-primary", 4.5],
      ["--color-text-secondary", "--color-background-secondary", 4.5],
      ["--color-text-warning", "--color-background-primary", 4.5],
      ["--color-text-danger", "--color-background-primary", 4.5],
      ["--color-border-secondary", "--color-background-primary", 3],
      ["--color-ring-primary", "--color-background-primary", 3],
    ] as const) {
      const r = contrast(tokens, fg, bg);
      assert.ok(r >= min, `${theme}: ${fg} on ${bg}: ${r.toFixed(2)}:1, needs ${min}:1`);
    }
  });

  test(`${theme} theme: faint really is too faint for text, so it stays decoration`, () => {
    // If this ever passed 4.5:1 the rule would be pointless; the rule itself is checked below.
    const [r] = worst(c("--faint"), b.page);
    assert.ok(r < 4.5, `${theme}: --faint reaches ${r.toFixed(2)}:1`);
  });
}

test("both themes declare every colour token", () => {
  for (const name of COLOURS) {
    assert.ok(LIGHT[name], `light is missing ${name}`);
    assert.ok(DARK[name], `dark is missing ${name}`);
  }
});

test("the system-dark block repeats the Dark block exactly", () => {
  assert.deepEqual(DARK_SYSTEM, DARK);
  assert.match(CSS, /@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{/);
});

test("each theme sets its own color-scheme, and the Light block repeats only that", () => {
  assert.equal(LIGHT["color-scheme"], "light");
  assert.deepEqual(block(':root[data-theme="light"]'), { "color-scheme": "light" });
  assert.equal(DARK["color-scheme"], "dark");
});

test("the site never defines the charts' own token names", () => {
  // The chart tokens bridge to the host names (--fg: var(--color-text-primary, ...)); defining
  // these too would make a custom-property cycle (plan decision 12).
  for (const name of ["--fg", "--fg-muted", "--grid", "--corner", "--a", "--b", "--surface", "--axis", "--warn", "--loss", "--line"]) {
    assert.doesNotMatch(CSS, new RegExp(`(^|[\\s;{])${name}\\s*:`), name);
    assert.doesNotMatch(GLOBALS, new RegExp(`(^|[\\s;{])${name}\\s*:`), `globals.css defines ${name}`);
  }
});

test("--faint is never a text colour: not in globals.css, not in any component", () => {
  for (const decl of GLOBALS.matchAll(/(?:^|[\s;{])color\s*:([^;}]*)/g)) {
    assert.doesNotMatch(decl[1], /--faint|--color-faint/, `globals.css: color:${decl[1]}`);
  }
  // Tailwind's text-faint (or text-(--faint)) would make it text.
  const roots = ["../components", "../app"].map((dir) => new URL(dir, import.meta.url).pathname);
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.tsx?$/.test(entry)) files.push(path);
    }
  };
  roots.forEach(walk);
  for (const file of files) {
    assert.doesNotMatch(readFileSync(file, "utf8"), /\btext-(?:faint|\(--faint\)|\[var\(--faint\)\])/, file);
  }
});
