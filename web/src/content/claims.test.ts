import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FIGURES } from "../components/findings/figure-data.ts";
import { EXCLUDED, REPORTS, reportByFile } from "../lib/report/manifest.ts";
import { parseReport } from "../lib/report/markdown.ts";
import {
  REPORT_FILES,
  checkClaim,
  checkEstimate,
  claimHref,
  claims,
  collapseWhitespace,
  getClaim,
  hasToken,
  reportSlug,
  type ClaimId,
  type ReportFile,
} from "./claims.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(HERE, "..");
// The repository's report/ folder (web/src/content → ../../../report). RE_REPORT_DIR points
// elsewhere, for a copy of web/ that lives outside the repository.
const REPORT_DIR = process.env.RE_REPORT_DIR ?? path.resolve(HERE, "../../../report");

const texts = new Map<string, string>();
function report(file: string): string {
  let text = texts.get(file);
  if (text === undefined) {
    text = readFileSync(path.join(REPORT_DIR, file), "utf8");
    texts.set(file, text);
  }
  return text;
}

const IDS = Object.keys(claims) as ClaimId[];

// The ids pages code against (plan 9.1's starter set without the human review, plus the ones FD
// and LD added). Renaming one breaks a page, so they are frozen; their values follow the reports.
const FROZEN_IDS = [
  "sessions", "segments", "labels", "split", "aurocAll", "top50", "apAll", "nll", "combined", "flags",
  "notCostly", "distinct", "cost", "probeWithin", "probeAcross", "teammatesApart", "carCos", "clearAny",
  "sizes", "persist", "consistentShare", "shiftAll", "shiftSame", "ftGain", "ftNoise", "ftVerdict",
  "unseen", "cnnSlow", "traffic", "devCheck", "cuts", "onnxParity", "clearChance", "hamBrakesEarlier",
  "albCoastsMore", "hamBrakesEarlierText", "grid", "flagShare", "timeWindow", "sampling",
];

// Plan 13: no accuracy figure anywhere, so none of the human review's numbers may come back.
const REVIEW_IDS = [
  "precisionAll", "precisionFirst", "precisionTop10", "precisionTop25", "precisionClean", "precisionRc",
  "precisionTel", "precisionSprint", "video", "judged", "overSlowing", "lateThrottle", "secondReader",
  "typeEarlyBraking", "typeHesitation", "typeLateThrottle", "typeOverSlowing", "typeTrackLimits", "typeUnclear",
];

test("the report folder holds every file a claim may cite", () => {
  assert.ok(existsSync(REPORT_DIR), `no report folder at ${REPORT_DIR} (set RE_REPORT_DIR to the repository's report/)`);
  for (const file of REPORT_FILES) assert.ok(existsSync(path.join(REPORT_DIR, file)), `missing report/${file}`);
});

test("every claim's quote is word for word in its source and contains its value", () => {
  const failures = IDS.flatMap((id) => {
    const c = getClaim(id);
    const problem = checkClaim(c, report(c.source));
    return problem ? [`${id}: ${problem}`] : [];
  });
  assert.deepEqual(failures, []);
});

test("the frozen claim ids are all there", () => {
  assert.deepEqual(FROZEN_IDS.filter((id) => !(id in claims)), []);
});

test("the landing's style bar can parse hamBrakesEarlier: a signed estimate and its interval", () => {
  assert.equal(claims.hamBrakesEarlier.value, "-6.1 [-9.2, -3.1]");
  const m = /^(-?\d+(?:\.\d+)?) \[(-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\]$/.exec(claims.hamBrakesEarlier.value);
  assert.ok(m, "the value isn't 'est [lo, hi]'");
  const [est, lo, hi] = m.slice(1).map(Number);
  assert.ok(lo < est && est < hi && hi < 0, "an estimate inside its interval, all below zero (brakes earlier)");
  assert.equal(claims.hamBrakesEarlier.quote, "| HAM - LEC | Ferrari | 15 | **-6.1 [-9.2, -3.1]** |");
});

test("no claim comes from the human review (plan 13: the site quotes no accuracy)", () => {
  assert.deepEqual(REVIEW_IDS.filter((id) => id in claims), []);
  assert.ok(!(REPORT_FILES as readonly string[]).includes("m4_review.md"), "m4_review.md may not be cited");
  const banned = /real \/ judged|\| precision \[95% CI\] \||real, \d+ not|Cohen's kappa|precision would be|\d+ \/ 49|\b7 in 10\b|reviewer judged/i;
  const failures = IDS.flatMap((id) => {
    const c = getClaim(id);
    return banned.test(c.quote) || banned.test(c.value) ? [`${id}: "${c.quote}"`] : [];
  });
  assert.deepEqual(failures, []);
});

test("every claim's source is a published report page, never an excluded one", () => {
  for (const file of REPORT_FILES) {
    assert.ok(!EXCLUDED.includes(file), `${file} is excluded from the site`);
    const entry = reportByFile(file);
    assert.ok(entry, `${file} isn't in lib/report/manifest.ts, so /report/${reportSlug(file)} doesn't exist`);
    assert.equal(entry.slug, reportSlug(file));
  }
  const slugs = new Set(REPORTS.map((r) => r.slug));
  for (const id of IDS) {
    const href = claimHref(id);
    const slug = /^\/report\/([a-z0-9-]+)(?:#[a-z0-9-]+)?$/.exec(href)?.[1];
    assert.ok(slug && slugs.has(slug), `${id}: ${href} isn't a published report page`);
    assert.ok(!href.includes("m4-review"), `${id} links the unpublished review`);
  }
});

// Plan 13 and M7 decision 12: the human review is left out of every page that holds it
// (m4_summary.md, the technical report, the model card), and the same text in another report is
// no better a source, so a quote may come from no omitted part of any published report.
test("no claim quotes any part of a report the site leaves out", () => {
  const omitted = REPORTS.flatMap((r) => [
    ...(r.omit?.sections ?? []).map((heading) => {
      const found = sections(r.file).find((s) => s.title === heading);
      assert.ok(found, `${r.file} has no heading "${heading}" to omit`);
      return { file: r.file, what: `the omitted section "${heading}"`, id: found.id as string | null, text: found.text };
    }),
    ...(r.omit?.sentences ?? []).map((sentence) => ({
      file: r.file,
      what: "an omitted sentence",
      id: null as string | null,
      text: collapseWhitespace(sentence),
    })),
  ]);
  assert.ok(omitted.filter((o) => o.id !== null).length >= 3, "m4_summary.md, the technical report and the model card each omit a section");
  const failures: string[] = [];
  for (const id of IDS) {
    const c = getClaim(id);
    const quote = collapseWhitespace(c.quote);
    for (const part of omitted) {
      if (part.text.includes(quote)) failures.push(`${id}: the quote is in ${part.what} of ${part.file}`);
      if (part.file === c.source && part.id !== null && c.anchor === part.id) failures.push(`${id}: #${c.anchor} is ${part.what}`);
    }
  }
  assert.deepEqual(failures, []);
});

/** Each heading's id (from RP's parser) and the text of its section, up to the next heading of the same or a higher level. */
function sections(file: string): { id: string; title: string; level: number; text: string }[] {
  const source = report(file);
  const doc = parseReport(source);
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const found: { line: number; level: number }[] = [];
  let fence = "";
  lines.forEach((line, i) => {
    const f = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (f) {
      if (!fence) fence = f[1];
      else if (f[1][0] === fence[0] && f[1].length >= fence.length) fence = "";
      return;
    }
    const h = fence ? null : /^ {0,3}(#{1,6})[ \t]+\S/.exec(line);
    if (h) found.push({ line: i, level: h[1].length });
  });
  assert.equal(found.length, doc.headings.length, `${file}: the parser and the line scan disagree on the headings`);
  return found.map((h, j) => {
    const next = found.slice(j + 1).find((later) => later.level <= h.level);
    const text = lines.slice(h.line + 1, next ? next.line : lines.length).join("\n");
    return { id: doc.headings[j].id, title: doc.headings[j].text, level: h.level, text: collapseWhitespace(text) };
  });
}

test("every anchor is a heading of its file, and its section holds the quote", () => {
  const failures: string[] = [];
  for (const id of IDS) {
    const c = getClaim(id);
    if (!c.anchor) continue;
    const found = sections(c.source).find((s) => s.id === c.anchor);
    if (!found) failures.push(`${id}: no heading #${c.anchor} in ${c.source}`);
    else if (found.level === 1) failures.push(`${id}: #${c.anchor} is the page title, not a section`);
    else if (!found.text.includes(collapseWhitespace(c.quote))) failures.push(`${id}: the quote isn't under #${c.anchor}`);
  }
  assert.deepEqual(failures, []);
});

test("every figure row's numbers are in its claim's quote", () => {
  let rows = 0;
  const failures = FIGURES.flatMap((figure) =>
    figure.rows.flatMap((row) => {
      rows++;
      const problem = checkEstimate(row, figure.format);
      return problem ? [`${figure.name}: ${problem}`] : [];
    }),
  );
  assert.deepEqual(failures, []);
  assert.ok(rows >= 30, `only ${rows} figure rows`);
});

/** The .ts and .tsx files under a folder (or the file itself), tests left out; none if it doesn't exist yet. */
function sourceFiles(target: string): string[] {
  if (!existsSync(target)) return [];
  if (!statSync(target).isDirectory()) return [target];
  return readdirSync(target)
    .flatMap((name) => sourceFiles(path.join(target, name)))
    .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f));
}

/**
 * The percentages written into a line of source that read as figures. Layout is not a figure, so
 * two forms are left out: a geometry attribute or style key whose whole value is a percentage
 * (`y="50%"`, `width="100%"`, `left: "50%"`), and a Tailwind arbitrary value, a `[…]` with no
 * space in it (`w-[calc(100%-24px)]`, `border-[color-mix(in_srgb,…_45%,transparent)]`). Anything
 * else counts: JSX text, a worded string, a bare string (`{"71%"}`, `label: "71%"`) or a comment.
 */
const GEOMETRY =
  "x|y|x1|x2|y1|y2|cx|cy|r|rx|ry|fx|fy|dx|dy|width|height|offset|startOffset|left|right|top|bottom|inset|minWidth|maxWidth|minHeight|maxHeight|flexBasis|translate|backgroundPosition|backgroundSize";
const GEOMETRY_VALUE = new RegExp(`\\b(?:${GEOMETRY})(?:=|:\\s*)(["'\`])-?\\d+(?:\\.\\d+)?%\\1`, "g");

function hardCodedPercentages(line: string): string[] {
  const layout = line.replace(/\[[^\]\s]*\]/g, "").replace(GEOMETRY_VALUE, "");
  return layout.match(/\d+(?:\.\d+)?%/g) ?? [];
}

test("the percentage scanner tells figures from layout", () => {
  for (const figure of [
    "<p>names the team 44% of the time</p>",
    "<span>{x} of 50 (71%)</span>",
    'title: "Isolation Forest 75% of the time"',
    "label={`${n} corners, 9% of them`}",
    "// each with its 95% interval",
    ">8.8%<",
    '<span>{"71%"}</span>',
    "const share = '71%';",
    'label: "71%",',
    'title="71%"',
  ]) {
    assert.ok(hardCodedPercentages(figure).length > 0, `not caught: ${figure}`);
  }
  for (const layout of [
    '<line y1="50%" y2="100%" x1="0%" />',
    '<rect x={0} width="100%" y={yPx(noise)} />',
    "x={`${pos(v)}%`} y='50%'",
    'style={{ left: "50%", top: "0%" }}',
    'className="border-[color-mix(in_srgb,var(--color-text-warning)_45%,transparent)]"',
    'className="w-[calc(100%-24px)] translate-x-[-50%]"',
  ]) {
    assert.deepEqual(hardCodedPercentages(layout), [], `flagged: ${layout}`);
  }
});

test("no hard-coded percentage in the landing, the findings components or the report page", (t) => {
  const targets = ["components/landing", "components/findings", "app/(site)/report/page.tsx"].map((p) => path.join(SRC, p));
  const files = targets.flatMap(sourceFiles);
  const hits: string[] = [];
  for (const file of files) {
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => {
        for (const m of hardCodedPercentages(line)) hits.push(`${path.relative(SRC, file)}:${i + 1}: "${m}" (use <Num id> and claims.ts)`);
      });
  }
  t.diagnostic(`scanned ${files.length} file(s)`);
  assert.ok(files.length > 0, "nothing to scan");
  assert.deepEqual(hits, []);
});

test("checkClaim: whitespace collapsed, value inside quote, quote inside text", () => {
  const text = "Reconstruction error rises about 10% from\n  2025 to 2026 in every variant";
  const ok = { value: "about 10%", quote: "rises about 10% from 2025 to 2026", source: "m3_summary.md" as ReportFile };
  assert.equal(checkClaim(ok, text), null);
  assert.equal(checkClaim({ ...ok, quote: "rises  about 10%\nfrom 2025" }, text), null);
  assert.match(checkClaim({ ...ok, value: "about 11%" }, text) ?? "", /doesn't contain the value/);
  assert.match(checkClaim({ ...ok, quote: "rises about 10% from 2024" }, text) ?? "", /isn't in m3_summary\.md/);
  assert.match(checkClaim({ ...ok, value: " " }, text) ?? "", /empty/);
  // The value must be a whole number in the quote, not part of a longer one.
  const row = "| team | 11 | 0.091 | 0.443 | 0.287 |";
  const team = { value: "1", quote: row, source: "m3_style_within_season.md" as ReportFile };
  assert.match(checkClaim(team, row) ?? "", /doesn't contain the value "1"/);
  assert.match(checkClaim({ ...team, value: "0.10", quote: "teams -0.10" }, "teams -0.10") ?? "", /doesn't contain/);
});

test("hasToken matches whole numbers only", () => {
  const row = "| 2026 | 4 | 58,577 | 5 | 0.224 | -1.2% |";
  assert.ok(hasToken(row, "5"));
  assert.ok(hasToken(row, "-1.2%"));
  assert.ok(hasToken(row, "| 4 |"));
  assert.ok(!hasToken(row, "1.2%"));
  assert.ok(!hasToken(row, "8"));
  assert.ok(!hasToken(row, "577"));
  assert.ok(!hasToken("0.7% and 17 and 7% and 1,700", "7"));
  assert.ok(hasToken("0.7% and 17 and 7 and 1,700", "7"));
  assert.ok(!hasToken("drivers of different teams -0.10", "0.10"));
  assert.ok(hasToken("drivers of different teams -0.10", "-0.10"));
  assert.ok(hasToken("0.751 [0.714, 0.785]", "0.785"));
  assert.ok(!hasToken("0.7514", "0.751"));
  assert.ok(hasToken("ends with 7.", "7"));
  assert.ok(!hasToken("anything", ""));
});

test("checkEstimate wants each number in its place, not just somewhere in the quote", () => {
  const three = (v: number) => v.toFixed(3);
  const iForest = { label: "Isolation Forest", est: 0.751, lo: 0.714, hi: 0.785, claim: "detAllIForest" as ClaimId };
  assert.equal(checkEstimate(iForest, three), null);
  assert.match(checkEstimate({ ...iForest, lo: 0.785, hi: 0.714 }, three) ?? "", /interval/, "bounds swapped");
  assert.match(checkEstimate({ ...iForest, est: 0.714 }, three) ?? "", /interval/, "estimate typed as its lower bound");
  assert.match(checkEstimate({ ...iForest, hi: 0.786 }, three) ?? "", /interval "0\.751 \[0\.714, 0\.786\]"/);
  // Without both bounds, each number is looked up on its own.
  assert.equal(checkEstimate({ label: "Team", est: 0.443, claim: "probeTeamRow" }, three), null);
  assert.match(checkEstimate({ label: "Team", est: 0.444, claim: "probeTeamRow" }, three) ?? "", /est "0\.444"/);
  // The x position must be a cell of the row.
  const pct = (v: number) => `${v < 0 ? "-" : "+"}${Math.abs(v).toFixed(1)}%`;
  const four = { label: "2026, 4 races", est: -1.2, x: 4, claim: "shift2026x4" as ClaimId };
  assert.equal(checkEstimate(four, pct), null);
  assert.match(checkEstimate({ ...four, x: 8 }, pct) ?? "", /x "\| 8 \|"/);
  assert.match(checkEstimate({ ...four, est: 1.2 }, pct) ?? "", /est "\+1\.2%"/);
  // Counts, for a figure that prints k of n.
  const counts = { label: "Cuts", est: 5, k: 5, n: 43, claim: "cuts" as ClaimId };
  assert.equal(checkEstimate(counts, String), null);
  assert.match(checkEstimate({ ...counts, n: 44 }, String) ?? "", /k of n "5 of 44"/);
});

test("report slugs and source links", () => {
  assert.equal(reportSlug("m3_style_within_season.md"), "m3-style-within-season");
  assert.equal(claimHref("flags"), "/report/m4-scoring");
  assert.equal(claimHref("carCos"), "/report/m4-style#style-embedding");
  assert.equal(claimHref("sampling"), "/report/dataset-card#known-limitations");
});
