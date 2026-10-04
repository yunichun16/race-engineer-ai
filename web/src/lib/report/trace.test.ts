import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, test } from "node:test";
import { reportByFile } from "./manifest.ts";
import { parseReport } from "./markdown.ts";
import { applyOmission } from "./omit.ts";
import { REPO_ROOT, readReport } from "./testing.ts";
import {
  MAX_EXEMPT,
  TRACED,
  TRACE_EXEMPT,
  applyExemptions,
  describeUntraced,
  isYear,
  numberTokens,
  pageText,
  sourcePath,
  traceUnits,
  untraced,
  type TraceUnit,
} from "./trace.ts";

describe("numberTokens", () => {
  test("numbers as written: separators, decimals, signs, percentages, exponents", () => {
    assert.deepEqual(numberTokens("263 sessions, 2,314,096 segments, 0.37 s and 44% (chance 4.5%)"), [
      "263",
      "2,314,096",
      "0.37",
      "44%",
      "4.5%",
    ]);
    assert.deepEqual(numberTokens("teams -0.10, +0.3% and 1.1e-05 against 1e-04"), ["-0.10", "+0.3%", "1.1e-05", "1e-04"]);
    assert.deepEqual(numberTokens("0.033 [0.007, 0.096] against 0.017"), ["0.033", "0.007", "0.096", "0.017"]);
    assert.deepEqual(numberTokens("Next.js 16.3.7 and $3 a day, 640 KiB, 120/600/240 a minute"), ["16.3.7", "3", "640", "120", "600", "240"]);
  });

  test("a hyphen after a word, a number or a percentage joins; elsewhere it signs", () => {
    assert.deepEqual(numberTokens("2022-2024"), ["2022", "2024"]);
    assert.deepEqual(numberTokens("driver top-1"), ["1"]);
    assert.deepEqual(numberTokens("0.6%-1.0%"), ["0.6%", "1.0%"]);
    assert.deepEqual(numberTokens("(-6.1 [-9.2, -3.1])"), ["-6.1", "-9.2", "-3.1"]);
  });

  test("digits that are part of a name don't count", () => {
    assert.deepEqual(numberTokens("F1, RQ1, M7, d64, mae_scorer, T5 and P@50"), ["50"]);
    assert.deepEqual(numberTokens("576k parameters, 99th percentile, 12x slower"), ["576", "99", "12"]);
  });

  test("a sentence's full stop isn't a decimal point", () => {
    assert.deepEqual(numberTokens("in 7 s. Then 0.75."), ["7", "0.75"]);
  });

  test("the years of the data and the project need no source", () => {
    for (const year of ["2022", "2026", "2027"]) assert.ok(isYear(year), year);
    for (const other of ["2021", "2028", "2,022", "2022.5", "-2022", "2022%"]) assert.ok(!isYear(other), other);
  });
});

describe("sourcePath", () => {
  test("published reports and repository files are sources", () => {
    assert.equal(sourcePath("m3_summary.md"), "report/m3_summary.md");
    assert.equal(sourcePath("m3_summary.md#findings"), "report/m3_summary.md");
    assert.equal(sourcePath("../README.md"), "README.md");
    assert.equal(sourcePath("../engine/configs/ablations.toml"), "engine/configs/ablations.toml");
  });

  test("excluded reports, the review's files, anchors, figures and web addresses never are", () => {
    assert.equal(sourcePath("m4_review.md"), null);
    assert.equal(sourcePath("m3_ablations_smoke.md"), null);
    assert.equal(sourcePath("m4_review_queue.csv"), null);
    assert.equal(sourcePath("m7_chat_accuracy.md"), null);
    assert.equal(sourcePath("#limits"), null);
    assert.equal(sourcePath("figures/style_map.png"), null);
    assert.equal(sourcePath("https://github.com/theOehrly/Fast-F1"), null);
  });
});

const DOC = `# Title

Before any section: 12.

Sources: [m3_summary.md](m3_summary.md)

## A

Text with 0.75 and \`code 99\` and 2026.

Sources: [m4_summary.md](m4_summary.md)

### A.1

- An item with 1,609. [baselines](baselines.md)
  - Nested, with 0.0009.
- Plain 0.75.

| detector | AUROC |
|---|---|
| iforest [link](m3_results.md) | 0.751 |

## B

![A caption with 4.5%](figures/style_map.png)

No source: 42.
`;

describe("traceUnits", () => {
  const units = traceUnits(parseReport(DOC).blocks);
  const unit = (numbers: string[]) => units.find((u) => u.numbers.join() === numbers.join()) as TraceUnit;

  test("every block with a number, with its sources: its own links, then its sections' Sources lines", () => {
    assert.deepEqual(
      units.map((u) => [u.section, u.numbers, u.sources]),
      [
        ["", ["12"], ["report/m3_summary.md"]],
        ["A", ["0.75"], ["report/m4_summary.md"]],
        ["A.1", ["1,609"], ["report/baselines.md", "report/m4_summary.md"]],
        ["A.1", ["0.0009"], ["report/baselines.md", "report/m4_summary.md"]],
        ["A.1", ["0.75"], ["report/m4_summary.md"]],
        ["A.1", ["0.751"], ["report/m3_results.md", "report/m4_summary.md"]],
        ["B", ["4.5%"], []],
        ["B", ["42"], []],
      ],
    );
  });

  test("code and years are left out; a subsection inherits its section's line, never the title's", () => {
    assert.equal(unit(["0.75"]).text, "Text with 0.75 and and 2026.");
    assert.ok(!units.some((u) => u.numbers.includes("99") || u.numbers.includes("2026")));
    assert.ok(!units.some((u) => u.section !== "" && u.sources.includes("report/m3_summary.md")));
  });

  test("untraced numbers are listed with where they are and what was searched", () => {
    const tokens = new Map<string, ReadonlySet<string>>([
      ["report/m3_summary.md", new Set(["12"])],
      ["report/m4_summary.md", new Set(["0.75", "0.0009"])],
      ["report/baselines.md", new Set(["1,609"])],
    ]);
    const found = untraced(units, (p) => tokens.get(p) ?? null);
    assert.deepEqual(
      found.map((u) => [u.section, u.token]),
      [
        ["A.1", "0.751"],
        ["B", "4.5%"],
        ["B", "42"],
      ],
    );
    assert.match(describeUntraced("x.md", found[0]), /^x\.md › A\.1: "0\.751" in "iforest link \| 0\.751": not in report\/m3_results\.md, report\/m4_summary\.md$/);
    assert.match(describeUntraced("x.md", found[2]), /no source linked/);
  });

  test("a fabricated number is caught, a copied one isn't", () => {
    const sources = new Map([["report/m4_summary.md", new Set(numberTokens("A distinct mistake costs 0.37 s at the median"))]]);
    const check = (md: string) =>
      untraced(traceUnits(parseReport(`# T\n\n${md} [m4_summary.md](m4_summary.md)`).blocks), (p) => sources.get(p) ?? null).map((u) => u.token);
    assert.deepEqual(check("A mistake costs 0.37 s."), []);
    assert.deepEqual(check("A mistake costs 0.73 s."), ["0.73"]);
    assert.deepEqual(check("A mistake costs 0.370 s."), ["0.370"]);
    assert.deepEqual(check("A mistake costs 3 s."), ["3"]);
  });

  test("an exemption drops its number in its file only, and an unused one is reported", () => {
    const found = [
      { section: "S", text: "t 7", token: "7", sources: [] },
      { section: "S", text: "t 8", token: "8", sources: [] },
    ];
    const exempt = [
      { file: "a.md", token: "7", reason: "r" },
      { file: "a.md", token: "9", reason: "r" },
      { file: "b.md", token: "8", reason: "r" },
    ];
    const { remaining, unused } = applyExemptions("a.md", found, exempt);
    assert.deepEqual(
      remaining.map((u) => u.token),
      ["8"],
    );
    assert.deepEqual(
      unused.map((e) => e.token),
      ["9"],
    );
  });
});

describe("pageText", () => {
  test("what the site shows: omitted sections gone, code and tables kept", () => {
    const doc = parseReport("# T 1\n\nKeep 2 `c3`.\n\n## Gone\n\nSecret 71%.\n\n## Kept\n\n| a | b |\n|---|---|\n| 4 | 5 |\n\n```\n6\n```\n");
    const text = pageText(applyOmission(doc, { sections: ["Gone"] }));
    assert.deepEqual(numberTokens(text), ["1", "2", "4", "5", "6"]);
    assert.ok(text.includes("c3"));
  });
});

/** A repository file, or null when it doesn't exist. */
function readRepoFile(relative: string): string | null {
  const full = path.join(REPO_ROOT, relative);
  return existsSync(full) ? readFileSync(full, "utf8") : null;
}

/** A source's numbers: as the repository holds it, or as the site shows it (a report without its omitted parts). */
function tokenReader(site: boolean): (relative: string) => ReadonlySet<string> | null {
  const cache = new Map<string, ReadonlySet<string> | null>();
  return (relative) => {
    if (!cache.has(relative)) {
      const raw = readRepoFile(relative);
      const report = /^report\/([^/]+\.md)$/.exec(relative);
      const entry = report ? reportByFile(report[1]) : undefined;
      const text = raw !== null && site && entry ? pageText(applyOmission(parseReport(raw), entry.omit)) : raw;
      cache.set(relative, text === null ? null : new Set(numberTokens(text)));
    }
    return cache.get(relative)!;
  };
}

describe("every number in the technical report and the model card is in a source", () => {
  test(`the exemptions: at most ${MAX_EXEMPT}, each in a traced file and with its reason`, () => {
    assert.ok(TRACE_EXEMPT.length <= MAX_EXEMPT, `${TRACE_EXEMPT.length} exemptions`);
    for (const e of TRACE_EXEMPT) {
      assert.ok(TRACED.includes(e.file), `${e.file} isn't traced`);
      assert.ok(e.reason.trim().length > 10, `${e.file} "${e.token}" needs a reason`);
      assert.deepEqual(numberTokens(e.token), [e.token], `"${e.token}" isn't one number token`);
    }
  });

  for (const file of TRACED) {
    const entry = reportByFile(file);

    test(`${file} is published, without its human-checked precision`, () => {
      assert.ok(entry, `${file} isn't in lib/report/manifest.ts`);
      assert.deepEqual(entry.omit?.sections, ["Human-checked precision"]);
    });

    test(`${file}: every number, as the repository holds it, is in a source its block or section links`, () => {
      const doc = parseReport(readReport(file));
      const units = traceUnits(doc.blocks);
      assert.ok(units.length > 20, `only ${units.length} blocks with numbers`);
      const read = tokenReader(false);
      const unreadable = [...new Set(units.flatMap((u) => u.sources))].filter((s) => read(s) === null);
      assert.deepEqual(unreadable, [], "linked sources that can't be read");
      const { remaining, unused } = applyExemptions(file, untraced(units, read));
      assert.deepEqual(remaining.map((u) => describeUntraced(file, u)), []);
      assert.deepEqual(unused, [], "exemptions that match nothing: remove them");
    });

    test(`${file}: every number on its page is in a part of a source the site shows`, () => {
      const page = applyOmission(parseReport(readReport(file)), entry?.omit);
      const { remaining } = applyExemptions(file, untraced(traceUnits(page.blocks), tokenReader(true)));
      assert.deepEqual(remaining.map((u) => describeUntraced(file, u)), []);
    });
  }
});
