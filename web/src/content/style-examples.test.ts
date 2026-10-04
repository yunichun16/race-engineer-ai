import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_STYLES, parseStylesQuery } from "../components/styles/query.ts";
import { collapseWhitespace, getClaim } from "./claims.ts";
import { exampleHref, parseCell, STYLE_EXAMPLES } from "./style-examples.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// The repository's report/ folder (web/src/content → ../../../report), as claims.test.ts reads it.
const REPORT_DIR = process.env.RE_REPORT_DIR ?? path.resolve(HERE, "../../../report");
const STYLE_REPORT = collapseWhitespace(readFileSync(path.join(REPORT_DIR, "m4_style.md"), "utf8"));

test("ids are unique and name the pair and the season", () => {
  const ids = STYLE_EXAMPLES.map((e) => e.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const e of STYLE_EXAMPLES) {
    assert.equal(e.id, `${e.a}-${e.b}-${e.year}`.toLowerCase(), e.id);
    assert.match(e.a, /^[A-Z]{3}$/);
    assert.match(e.b, /^[A-Z]{3}$/);
    assert.notEqual(e.a, e.b);
  }
});

test("each example's link opens exactly that pair and season", () => {
  for (const e of STYLE_EXAMPLES) {
    const href = exampleHref(e);
    assert.ok(href.startsWith("/styles?"), href);
    const q = parseStylesQuery(new URLSearchParams(href.slice("/styles?".length)));
    assert.deepEqual(q, { year: e.year, a: e.a, b: e.b }, e.id);
  }
});

test("the first example is the page's default pair", () => {
  const [first] = STYLE_EXAMPLES;
  assert.deepEqual({ year: first.year, a: first.a, b: first.b }, DEFAULT_STYLES);
});

test("titles and notes carry no numbers: those come from the claims", () => {
  for (const e of STYLE_EXAMPLES) {
    assert.doesNotMatch(e.title, /\d/, e.id);
    assert.doesNotMatch(e.note ?? "", /\d/, e.id);
    assert.ok(e.finding || e.note, `${e.id} needs a finding or a note`);
  }
});

test("a finding names its pair and season, and agrees with its title and its bar", () => {
  const withFindings = STYLE_EXAMPLES.filter((e) => e.finding);
  assert.ok(withFindings.length >= 2);
  for (const e of withFindings) {
    const f = e.finding!;
    const text = getClaim(f.text);
    const bar = getClaim(f.bar);
    // From the report's section for this season.
    for (const c of [text, bar]) {
      assert.equal(c.source, "m4_style.md", e.id);
      assert.ok(c.anchor?.includes(String(e.year)), `${e.id}: ${c.anchor} isn't the ${e.year} section`);
    }
    // The bar is the pair's own table row, A minus B.
    assert.ok(bar.quote.startsWith(`| ${e.a} - ${e.b} | ${e.team} |`), `${e.id}: ${bar.quote}`);
    const cell = parseCell(bar.value);
    assert.ok(cell, `${e.id}: ${bar.value} isn't a signed cell`);
    // The sentence says which way, in the report's words, and the title says the same.
    const direction = cell.est < 0 ? f.less : f.more;
    assert.ok(text.quote.startsWith(`${e.a} ${direction} than ${e.b}: by ${text.value}`), `${e.id}: ${text.quote}`);
    assert.ok(e.title.includes(direction), `${e.id}: "${e.title}" doesn't say "${direction}"`);
    // The same numbers: the sentence gives the size, the cell the signed difference.
    const [size, lo, hi] = [Math.abs(cell.est), Math.min(Math.abs(cell.lo), Math.abs(cell.hi)), Math.max(Math.abs(cell.lo), Math.abs(cell.hi))];
    assert.ok(text.value.startsWith(`${size.toFixed(1)} m [${lo.toFixed(1)}, ${hi.toFixed(1)}]`), `${e.id}: ${text.value} against ${bar.value}`);
    assert.ok(STYLE_REPORT.includes(collapseWhitespace(bar.quote)), e.id);
  }
});

test("teammate examples from the report were teammates; the cross-team one is marked", () => {
  for (const e of STYLE_EXAMPLES) {
    if (e.crossTeam) {
      assert.equal(e.finding, undefined, `${e.id}: different cars, so no style finding`);
      assert.match(e.team, / and /, e.id);
      continue;
    }
    if (e.year === 2026) {
      // The report's table lists every 2026 teammate pair as "| A - B | team |".
      assert.ok(STYLE_REPORT.includes(`| ${e.a} - ${e.b} | ${e.team} |`), `${e.id} isn't a ${e.year} pair in m4_style.md`);
    }
  }
  assert.ok(STYLE_EXAMPLES.some((e) => e.crossTeam));
});

test("signed cells parse; anything else doesn't", () => {
  assert.deepEqual(parseCell("-6.1 [-9.2, -3.1]"), { est: -6.1, lo: -9.2, hi: -3.1 });
  assert.deepEqual(parseCell("+6.3 [+3.5, +9.1]"), { est: 6.3, lo: 3.5, hi: 9.1 });
  assert.deepEqual(parseCell("0.12 [-0.86, 1.10]"), { est: 0.12, lo: -0.86, hi: 1.1 });
  assert.equal(parseCell("-1.0 [n/a]"), null);
  assert.equal(parseCell("6.3 m [3.5, 9.1]"), null);
  assert.equal(parseCell("+9.9 [+3.5, +9.1]"), null); // outside its interval
});
