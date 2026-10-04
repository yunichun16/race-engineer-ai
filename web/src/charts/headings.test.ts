/**
 * Chart headings fit the page they sit in. Claude's MCP App page is the chart alone, so there
 * explain-corner's and find-mistakes' titles are h1s and compare-styles' section heads h2s. On the
 * website the page has its own h1, so each chart that draws a heading takes its level as a render
 * option (`headingLevel`) and the site passes the level under its own heading. The defaults keep
 * Claude's pages as they were, and no chart sheet styles a heading by its tag, so a heading looks
 * the same at any level.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { headingTag } from "./dom.ts";

const dir = new URL("./", import.meta.url);
const read = (url: URL) => readFileSync(url, "utf8");
const modules = readdirSync(dir).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"));
const sheets = readdirSync(new URL("./styles/", dir)).filter((f) => f.endsWith(".css"));
const withoutComments = (code: string) => code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

// The charts that draw headings, and the level each draws inside Claude (its default).
const DEFAULTS: Record<string, number> = { "explain-corner.ts": 1, "find-mistakes.ts": 1, "compare-styles.ts": 2 };

test("headingTag gives the level asked for, or the default, within h1 to h6", () => {
  assert.equal(headingTag(undefined, 1), "h1");
  assert.equal(headingTag(undefined, 2), "h2");
  for (const level of [1, 2, 3, 4, 5, 6] as const) {
    assert.equal(headingTag(level, 1), `h${level}`);
    assert.equal(headingTag(level, 2), `h${level}`);
  }
  // A sub-heading one below an h6 stays an h6; nothing goes above h1.
  assert.equal(headingTag(6 + 1, 1), "h6");
  assert.equal(headingTag(0, 3), "h1");
  assert.equal(headingTag(-2, 3), "h1");
  // Not a level at all (from a caller without the types): the default.
  for (const junk of [Number.NaN, Infinity, 2.5]) assert.equal(headingTag(junk, 2), "h2");
  assert.equal(headingTag("4" as unknown as number, 1), "h1");
});

test("every heading a chart draws takes its level from the render options", () => {
  for (const file of modules) {
    const code = withoutComments(read(new URL(file, dir)));
    // No heading at a fixed level, whichever way it is made.
    assert.ok(!/htmlEl\(\s*["'`]h[1-6]["'`]/.test(code), `${file} draws a heading at a fixed level`);
    assert.ok(!/createElement\(\s*["'`]h[1-6]["'`]/.test(code), `${file} creates a heading at a fixed level`);

    // The calls, not dom.ts's definition.
    const calls = [...code.matchAll(/(?<!function )headingTag\(\s*([^,()]+?)\s*,\s*([^)]+?)\s*\)/g)];
    if (!(file in DEFAULTS)) {
      assert.equal(calls.length, 0, `${file} draws a heading: add it to DEFAULTS here`);
      continue;
    }
    assert.ok(calls.length > 0, `${file} draws no heading`);
    for (const [, level, fallback] of calls) {
      assert.match(level, /\bheadingLevel$/, `${file}: a heading whose level isn't the headingLevel option`);
      assert.equal(Number(fallback), DEFAULTS[file], `${file}: Claude's page draws h${DEFAULTS[file]}`);
    }
    const options = code.match(/export interface RenderOptions \{([\s\S]*?)\n\}/)?.[1] ?? "";
    assert.match(options, /^\s*headingLevel\?: HeadingLevel;$/m, `${file}: RenderOptions has no headingLevel`);
    // The option reaches the drawing: render passes it on (or reads it itself).
    assert.match(code, /opts\.headingLevel/, `${file}: render never reads opts.headingLevel`);
  }
});

test("no chart sheet styles a heading by its tag", () => {
  for (const file of sheets) {
    const selectors = read(new URL(`./styles/${file}`, dir))
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\{[^{}]*\}/g, "{}"); // declaration blocks: what is left is selectors and at-rules
    const tag = selectors.match(/(?<![\w.#-])h[1-6](?![\w-])/);
    assert.equal(tag, null, `${file} selects ${tag?.[0]} by its tag, which breaks at another level`);
  }
});
