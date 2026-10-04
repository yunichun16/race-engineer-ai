/**
 * The charts stay host-free, so Claude's MCP App pages and the website can both use them: every
 * module here loads under Node (nothing touches the DOM at import time, no CSS is imported),
 * none imports the MCP Apps SDK or looks up a page element, and no class name a chart draws
 * or styles is one of the Tailwind utilities the website might generate (which would restyle
 * the chart). `sr-only` is allowed: Tailwind's rule does what the chart's does.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

const dir = new URL("./", import.meta.url);
const read = (url: URL) => readFileSync(url, "utf8");
const modules = readdirSync(dir).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"));
const sheets = readdirSync(new URL("./styles/", dir)).filter((f) => f.endsWith(".css"));

// Bare Tailwind utilities that are also plausible class names.
const TAILWIND = new Set([
  "grid", "ring", "table-caption", "hidden", "flex", "block", "table", "container", "collapse",
  "border", "shadow", "outline", "truncate", "inline", "contents", "static", "fixed", "absolute",
  "relative", "sticky", "visible", "invisible", "italic", "underline", "uppercase", "lowercase",
  "capitalize", "rounded", "transition", "transform", "grow", "shrink", "isolate", "filter", "blur",
]);

/**
 * Class names a chart module puts on elements: htmlEl's class argument, svgEl's `class`, the
 * classList calls and the like.
 */
function classesInCode(source: string): Set<string> {
  const found = new Set<string>();
  const add = (text: string) => {
    // Static words only: `chip ${cls}` gives "chip"; "band band-" (from `band-${kind}`) is skipped.
    for (const word of text.replace(/\$\{[^}]*\}/g, " ").split(/\s+/)) {
      if (/^[a-z][\w-]*[a-z0-9]$/i.test(word)) found.add(word);
    }
  };
  const literal = String.raw`(?:"([^"]*)"|\x60([^\x60]*)\x60)`;
  const patterns = [
    // The tag is a literal ("div"), a name or a call (a heading's `headingTag(level, 1)`).
    new RegExp(String.raw`htmlEl\(\s*(?:"[a-z0-9]+"|[\w$]+(?:\([^()]*\))?),\s*${literal}`, "g"),
    new RegExp(String.raw`\bclass:\s*${literal}`, "g"),
    new RegExp(String.raw`classList\.(?:add|toggle|remove|contains)\(\s*${literal}`, "g"),
    new RegExp(String.raw`setAttribute\(\s*"class",\s*${literal}`, "g"),
    new RegExp(String.raw`\bclassName\s*=\s*${literal}`, "g"),
    new RegExp(String.raw`\bcls\s*=\s*${literal}`, "g"),
    new RegExp(String.raw`(?:chip|item|mark|key)\(\s*${literal}`, "g"),
  ];
  for (const re of patterns) for (const m of source.matchAll(re)) add(m[1] ?? m[2] ?? "");
  return found;
}

/** Class names in a sheet's selectors. */
function classesInSheet(css: string): Set<string> {
  const selectors = css
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{[^{}]*\}/g, "{}") // declaration blocks (one level is enough: no nesting)
    .replace(/@[^{]*\{/g, "");
  return new Set([...selectors.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)].map((m) => m[1]));
}

test("every chart module imports under Node, with no page to touch", async () => {
  assert.equal(typeof (globalThis as { document?: unknown }).document, "undefined");
  assert.ok(modules.includes("dom.ts") && modules.length >= 6, `modules: ${modules.join(", ")}`);
  for (const file of modules) {
    const mod = (await import(new URL(file, dir).href)) as Record<string, unknown>;
    if (file !== "dom.ts") assert.equal(typeof mod.render, "function", `${file} exports render`);
  }
});

test("no chart module reaches for the host", () => {
  for (const file of modules) {
    const source = read(new URL(file, dir));
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    // assert.ok rather than doesNotMatch, which would print the whole file on a failure.
    assert.ok(!/["']@modelcontextprotocol\//.test(code), `${file} imports the MCP Apps SDK`);
    assert.ok(!/\bnew App\(/.test(code), `${file} creates an App`);
    assert.ok(!/document\.getElementById/.test(code), `${file} looks up a page element`);
    assert.ok(!/^import\s+["'][^"']+\.css["']/m.test(code), `${file} imports CSS`);
    assert.ok(!/^(?![\s}])[^\n]*\bawait\b/m.test(code), `${file} has a top-level await`);
    // Relative imports carry their .ts extension, so Node can load the module for the tests.
    for (const m of code.matchAll(/from\s+["'](\.[^"']*)["']/g)) {
      assert.match(m[1], /\.ts$/, `${file} imports ${m[1]} without .ts`);
    }
  }
});

test("no chart class is also a Tailwind utility", () => {
  const used = new Map<string, string>();
  for (const file of modules) {
    for (const name of classesInCode(read(new URL(file, dir)))) used.set(name, file);
  }
  for (const file of sheets) {
    for (const name of classesInSheet(read(new URL(`./styles/${file}`, dir)))) used.set(name, file);
  }
  // The scans find what they should (a broken regex would pass everything).
  for (const name of ["gridline", "pick-ring", "by-event-caption", "re-chart", "row", "status"]) {
    assert.ok(used.has(name), `the scan missed .${name}`);
  }
  // Including the class of a heading, whose tag comes from its level.
  assert.ok(classesInCode('htmlEl(headingTag(headingLevel, 1), "title", text)').has("title"));
  assert.ok(classesInCode('htmlEl(headTag, "panel-title", "Style map", head)').has("panel-title"));
  const clashes = [...used].filter(([name]) => TAILWIND.has(name));
  assert.deepEqual(clashes, [], "chart classes that Tailwind would also style");

  // Nor one of the website's own global classes (.glass-panel, .micro, .tag and the rest): a
  // global `.panel` once put a glass box inside explain-corner's and compare-styles' panels.
  const site = classesInSheet(read(new URL("../app/globals.css", dir)));
  assert.ok(site.has("glass") && site.has("micro"), "the scan missed the site's classes");
  const shared = [...used].filter(([name]) => site.has(name) && name !== "sr-only");
  assert.deepEqual(shared, [], "chart classes that the website's globals.css also styles");
});

/** A selector list split at its top-level commas (not those inside `:where(…)`). */
function selectorList(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "(") depth++;
    else if (text[i] === ")") depth--;
    else if (text[i] === "," && depth === 0) {
      parts.push(text.slice(start, i).trim());
      start = i + 1;
    }
  }
  parts.push(text.slice(start).trim());
  return parts;
}

test("every chart sheet is scoped under its root", () => {
  for (const file of sheets.filter((f) => f !== "index.css" && f !== "tokens.css")) {
    const scope = file === "base.css" ? ".re-chart" : `.re-chart--${file.replace(/\.css$/, "")}`;
    const css = read(new URL(`./styles/${file}`, dir)).replace(/\/\*[\s\S]*?\*\//g, "");
    for (const m of css.matchAll(/(^|[{}])\s*([^{}@]+?)\s*\{/g)) {
      for (const selector of selectorList(m[2])) {
        const ok =
          selector === scope ||
          selector.startsWith(`${scope} `) ||
          selector.startsWith(`${scope}-`);
        assert.ok(ok, `${file}: "${selector}" isn't scoped under ${scope}`);
      }
    }
    assert.ok(!/@media[^{]*max-width/.test(css), `${file}: breakpoints are container queries`);
  }
});
