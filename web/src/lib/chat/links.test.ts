import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  chatPrefillHref,
  compareLapsHref,
  compareStylesHref,
  cornerQuestion,
  explainCornerArgs,
  explainCornerHref,
  explorerLink,
  findMistakesHref,
  linkFields,
  normaliseTurn,
  raceSummaryHref,
} from "./links.ts";

/** A chart fixture's payload (the `structuredContent` of web/src/mcp-app/sample-<name>.json). */
function sample(name: string): Record<string, unknown> {
  const url = new URL(`../../mcp-app/sample-${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(url, "utf8")).structuredContent;
}

/** The query of an href as a plain object, decoded the way the explorers read it. */
function query(href: string | null): Record<string, string> {
  assert.ok(href);
  return Object.fromEntries(new URLSearchParams(href.split("?")[1] ?? ""));
}

const CHARTS: [string, string][] = [
  ["find-mistakes", "find-mistakes"],
  ["find-mistakes-driver", "find-mistakes"],
  ["explain-corner", "explain-corner"],
  ["race-summary", "race-summary"],
  ["compare-styles", "compare-styles"],
  ["compare-laps", "compare-laps"],
];

test("each chart payload gives the right explorer URL, in the canonical query form", () => {
  const expected: Record<string, string> = {
    "find-mistakes": "/mistakes?event=Sample+Grand+Prix&session=Q&year=2026",
    "find-mistakes-driver": "/mistakes?driver=AAA&event=Sample+Grand+Prix&session=Q&year=2026",
    "explain-corner": "/mistakes?driver=AAA&event=Sample+Grand+Prix&explain=AAA:10:3&session=Q&year=2026",
    "race-summary": "/mistakes?event=Sample+Grand+Prix&session=R&year=2026",
    "compare-styles": "/styles?a=AAA&b=BBB&year=2025",
    "compare-laps": "/styles?a=AAA&b=BBB&laps=Sample+Grand+Prix&year=2026",
  };
  const labels: Record<string, string> = {
    "find-mistakes": "Open in Mistakes",
    "explain-corner": "Open in Mistakes",
    "race-summary": "Open in Mistakes",
    "compare-styles": "Open in Styles",
    "compare-laps": "Open in Styles",
  };
  for (const [name, bundle] of CHARTS) {
    const link = explorerLink({ bundle, data: sample(name) });
    assert.deepEqual(link, { href: expected[name], label: labels[bundle] }, name);
  }
  assert.deepEqual(query(expected["explain-corner"]), {
    year: "2026",
    event: "Sample Grand Prix",
    session: "Q",
    driver: "AAA",
    explain: "AAA:10:3",
  });
});

test("charts without an explorer, and missing charts, get no link", () => {
  assert.equal(explorerLink(null), null);
  assert.equal(explorerLink(undefined), null);
  assert.equal(explorerLink({ bundle: "telemetry", data: sample("find-mistakes") }), null);
  assert.equal(explorerLink({ bundle: "toString", data: {} }), null);
  assert.equal(explorerLink({ bundle: "find-mistakes", data: null }), null);
  assert.equal(explorerLink({ bundle: "find-mistakes", data: [] }), null);
});

test("values the explorers would drop are left out, or the link is", () => {
  const base = { year: 2025, event: "Italian Grand Prix", session_code: "Q" };
  assert.equal(findMistakesHref({ ...base, year: 1999 }), null);
  assert.equal(findMistakesHref({ ...base, year: 2025.5 }), null);
  assert.equal(findMistakesHref({ ...base, year: "2025" }), null);
  assert.equal(findMistakesHref({ ...base, session_code: "X" }), null);
  assert.equal(findMistakesHref({ ...base, event: "" }), null);
  assert.equal(findMistakesHref({ ...base, event: "x".repeat(101) }), null);
  assert.deepEqual(query(findMistakesHref({ ...base, driver: "lec" })), {
    year: "2025",
    event: "Italian Grand Prix",
    session: "Q",
  });
  assert.equal(raceSummaryHref({ ...base, session_code: "Q" }), null);
  assert.deepEqual(query(raceSummaryHref({ ...base, session_code: "S", focus: "LEC" })), {
    year: "2025",
    event: "Italian Grand Prix",
    session: "S",
  });
  assert.equal(compareStylesHref({ year: 2025, driver_a: "HAM", driver_b: "HAM" }), null);
  assert.equal(compareStylesHref({ year: 2025, driver_a: "HAM", driver_b: "lec" }), null);
  // A lap comparison without a usable pair still opens the weekend.
  assert.deepEqual(query(compareLapsHref({ year: 2025, event: "Abu Dhabi Grand Prix", drivers: [] })), {
    year: "2025",
    laps: "Abu Dhabi Grand Prix",
  });
});

test("the explain key: turns upper-cased, a leading T dropped, bad parts leave it out", () => {
  assert.equal(normaliseTurn("9a"), "9A");
  assert.equal(normaliseTurn("T9a"), "9A");
  assert.equal(normaliseTurn(" t11 "), "11");
  assert.equal(normaliseTurn(4), "4");
  assert.equal(normaliseTurn("9/10"), null);
  assert.equal(normaliseTurn(""), null);
  assert.equal(normaliseTurn(null), null);
  const corner = { year: 2025, event: "Monaco Grand Prix", session_code: "R", driver: "LEC", lap_number: 10, turn: "t9a" };
  assert.equal(query(explainCornerHref(corner)).explain, "LEC:10:9A");
  assert.equal(query(explainCornerHref({ ...corner, turn: "hairpin" })).explain, undefined);
  assert.equal(query(explainCornerHref({ ...corner, lap_number: 0 })).explain, undefined);
});

test("the link fields kept for a dropped chart give the same links", () => {
  for (const [name, bundle] of CHARTS) {
    const data = sample(name);
    const kept = linkFields(data);
    assert.ok(JSON.stringify(kept).length < 300, name);
    assert.deepEqual(explorerLink({ bundle, data: kept }), explorerLink({ bundle, data }), name);
  }
  assert.deepEqual(linkFields(null), {});
});

test("Show telemetry: explain_corner arguments from a find-mistakes row", () => {
  const list = sample("find-mistakes");
  const rows = list.mistakes as Record<string, unknown>[];
  assert.deepEqual(explainCornerArgs(list, rows[0]), {
    event: "Sample Grand Prix",
    driver: "AAA",
    lap: 9,
    corner: "3",
    year: 2026,
    session: "Q",
  });
  assert.deepEqual(Object.keys(explainCornerArgs(list, rows[0]) ?? {}), ["event", "driver", "lap", "corner", "year", "session"]);
  assert.equal(explainCornerArgs(list, { ...rows[0], lap_number: "9" }), null);
  assert.equal(explainCornerArgs(list, { ...rows[0], turn: "" }), null);
  assert.equal(explainCornerArgs({ ...list, event: 3 }, rows[0]), null);
  assert.deepEqual(explainCornerArgs({ event: "Monza", year: 1990, session_code: "X" }, rows[0]), {
    event: "Monza",
    driver: "AAA",
    lap: 9,
    corner: "3",
  });
});

test("Ask about this corner: a question built from the payload, and a prefill link", () => {
  const question = cornerQuestion(sample("explain-corner"));
  assert.equal(question, "Explain AAA's turn 3 on lap 10 at the 2026 Sample Grand Prix (Qualifying).");
  assert.equal(
    cornerQuestion({ driver: "NOR", turn: "9a", lap_number: 16, year: 2025, event: "Abu Dhabi Grand Prix", session_code: "Q" }),
    "Explain NOR's turn 9A on lap 16 at the 2025 Abu Dhabi Grand Prix (Q).",
  );
  assert.equal(cornerQuestion({ driver: "NOR" }), null);
  const href = chatPrefillHref(question ?? "");
  assert.ok(href.startsWith("/chat?q="));
  assert.deepEqual(query(href), { q: question });
  assert.deepEqual(query(chatPrefillHref("a&b=c #d")), { q: "a&b=c #d" });
});

test("the explorers read every chat link back as it was built (their own URL codecs)", async () => {
  const { mistakesHref, parseMistakesQuery } = await import("../../components/mistakes/query.ts");
  const { parseStylesQuery, stylesHref } = await import("../../components/styles/query.ts");
  for (const [name, bundle] of CHARTS) {
    const link = explorerLink({ bundle, data: sample(name) });
    assert.ok(link, name);
    const params = new URLSearchParams(link.href.split("?")[1]);
    if (link.href.startsWith("/mistakes?")) {
      const q = parseMistakesQuery(params);
      assert.equal(mistakesHref(q), link.href, name);
    } else {
      const q = parseStylesQuery(params);
      assert.equal(stylesHref(q), link.href, name);
    }
  }
  const corner = parseMistakesQuery(new URLSearchParams(explainCornerHref(sample("explain-corner"))?.split("?")[1]));
  assert.deepEqual(corner.explain, { driver: "AAA", lap: 10, turn: "3" });
});
