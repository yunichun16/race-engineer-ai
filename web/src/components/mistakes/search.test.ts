import { test } from "node:test";
import assert from "node:assert/strict";
import { parseSessionSearch } from "./search.ts";

test("the box's examples", () => {
  assert.deepEqual(parseSessionSearch("monza quali 2025"), { event: "monza", year: 2025, session: "Q" });
  assert.deepEqual(parseSessionSearch("last race"), { session: "R", recent: 0 });
});

test("a season: four digits, anywhere in the text", () => {
  assert.deepEqual(parseSessionSearch("2025 Monza"), { event: "Monza", year: 2025 });
  assert.deepEqual(parseSessionSearch("Monaco Grand Prix 2023 race"), { event: "Monaco Grand Prix", year: 2023, session: "R" });
  // Not a season: three or five digits, or one glued to a word, stays in the event text for the
  // tool to read (it says when that isn't a name).
  assert.deepEqual(parseSessionSearch("monza 202"), { event: "monza 202" });
  assert.deepEqual(parseSessionSearch("monza2025"), { event: "monza2025" });
});

test("session words, longest phrase first", () => {
  const cases: [string, string][] = [
    ["baku quali", "Q"],
    ["baku qualifying", "Q"],
    ["Baku Qualy", "Q"],
    ["baku q", "Q"],
    ["baku race", "R"],
    ["baku R", "R"],
    ["baku sprint", "S"],
    ["baku sprint race", "S"],
    ["miami sprint quali", "SQ"],
    ["miami sprint qualifying", "SQ"],
    ["Spa Sprint Shootout", "SQ"],
    ["spa shootout", "SQ"],
    ["spa SQ", "SQ"],
    ["spa ss", "SQ"],
  ];
  for (const [text, session] of cases) {
    const fields = parseSessionSearch(text);
    assert.equal(fields?.session, session, text);
    assert.ok(fields?.event && !/sprint|quali|race|shootout|\bq\b|\bss\b|\bsq\b/i.test(fields.event), `${text} -> ${fields?.event}`);
  }
  assert.deepEqual(parseSessionSearch("Sprint qualifying Miami 2024"), { event: "Miami", year: 2024, session: "SQ" });
});

test("last, latest and most recent count back from the newest session", () => {
  assert.deepEqual(parseSessionSearch("latest"), { recent: 0 });
  assert.deepEqual(parseSessionSearch("most recent"), { recent: 0 });
  assert.deepEqual(parseSessionSearch("the most recent qualifying"), { session: "Q", recent: 0 });
  assert.deepEqual(parseSessionSearch("Latest sprint"), { session: "S", recent: 0 });
  assert.deepEqual(parseSessionSearch("the last race of 2024"), { year: 2024, session: "R", recent: 0 });
  // With an event, the newest of that event's sessions.
  assert.deepEqual(parseSessionSearch("last monza race"), { event: "monza", session: "R", recent: 0 });
});

test("a season or a session alone asks for the newest such session", () => {
  assert.deepEqual(parseSessionSearch("2024"), { year: 2024, recent: 0 });
  assert.deepEqual(parseSessionSearch("quali"), { session: "Q", recent: 0 });
  assert.deepEqual(parseSessionSearch("race 2024"), { year: 2024, session: "R", recent: 0 });
});

test("a round of a season", () => {
  assert.deepEqual(parseSessionSearch("round 5 2024"), { year: 2024, round: 5 });
  assert.deepEqual(parseSessionSearch("Round 12 sprint"), { round: 12, session: "S" });
  // Never round and recent together: the tool refuses both.
  assert.deepEqual(parseSessionSearch("last race round 3"), { round: 3, session: "R" });
  // "round" without a number is just a word.
  assert.deepEqual(parseSessionSearch("round"), { event: "round" });
});

test("names keep their words, case and accents; filler and punctuation go", () => {
  assert.deepEqual(parseSessionSearch("São Paulo sprint"), { event: "São Paulo", session: "S" });
  assert.deepEqual(parseSessionSearch("Abu Dhabi GP 2025"), { event: "Abu Dhabi GP", year: 2025 });
  assert.deepEqual(parseSessionSearch("  Monza,   quali. "), { event: "Monza", session: "Q" });
  assert.deepEqual(parseSessionSearch("the race at Baku"), { event: "Baku", session: "R" });
  assert.deepEqual(parseSessionSearch("“Las Vegas” 2024?"), { event: "Las Vegas", year: 2024 });
  // One-letter "s" isn't the sprint here: it starts "S Paulo".
  assert.deepEqual(parseSessionSearch("S Paulo"), { event: "S Paulo" });
  // A question with a driver in it goes to the tool as it is: its answer says what event takes.
  assert.deepEqual(parseSessionSearch("max at monza"), { event: "max monza" });
});

test("nothing to look for", () => {
  for (const text of ["", "   ", "the", "at the", ",.;"]) assert.equal(parseSessionSearch(text), null, JSON.stringify(text));
});

test("the event text fits the tool's 100 characters", () => {
  const fields = parseSessionSearch(`${"monza ".repeat(40)}2025`);
  assert.equal(fields?.year, 2025);
  assert.ok((fields?.event?.length ?? 0) <= 100);
});
