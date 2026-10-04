import { test } from "node:test";
import assert from "node:assert/strict";
import type { StyleCatalog, StylePair } from "../../lib/api/types.ts";
import {
  areTeammates,
  driverChoices,
  findPair,
  pairEventsText,
  pairKey,
  pairLabel,
  pairOptions,
  pickPair,
  teammateOf,
} from "./pairs.ts";

const pair = (team: string, a: string, b: string, events: number): StylePair => ({
  team,
  a,
  b,
  events,
  consistent: [events >= 4, events >= 4],
});

// A small synthetic season, shaped like the catalog's answer: two drivers who swapped seats
// mid-season (SWA and SWB), a short pair and a default pair.
const SEASON: StyleCatalog = {
  years: [2026, 2025, 2024],
  year: 2025,
  drivers: [
    { driver: "AAA", team: "Alpha", events: 20 },
    { driver: "BBB", team: "Alpha", events: 20 },
    { driver: "HAM", team: "Ferrari", events: 24 },
    { driver: "LEC", team: "Ferrari", events: 24 },
    { driver: "SWA", team: "Junior", events: 22 },
    { driver: "SWB", team: "Junior", events: 2 },
    { driver: "JRA", team: "Junior", events: 24 },
    { driver: "SWA", team: "Senior", events: 2 },
    { driver: "SWB", team: "Senior", events: 22 },
    { driver: "TOP", team: "Senior", events: 24 },
  ],
  pairs: [
    pair("Alpha", "AAA", "BBB", 20),
    pair("Ferrari", "HAM", "LEC", 24),
    pair("Junior", "JRA", "SWA", 22),
    pair("Junior", "JRA", "SWB", 2),
    pair("Senior", "SWA", "TOP", 2),
    pair("Senior", "SWB", "TOP", 22),
  ],
};

test("a pair has one key, in either order", () => {
  assert.equal(pairKey("HAM", "LEC"), "HAM-LEC");
  assert.equal(pairKey("LEC", "HAM"), "HAM-LEC");
  assert.equal(findPair(SEASON, "LEC", "HAM")?.team, "Ferrari");
  assert.equal(findPair(SEASON, "HAM", "AAA"), undefined);
});

test("teammates means a listed pair: seat swappers who never drove together aren't", () => {
  assert.equal(areTeammates(SEASON, "HAM", "LEC"), true);
  assert.equal(areTeammates(SEASON, "LEC", "HAM"), true);
  assert.equal(areTeammates(SEASON, "HAM", "AAA"), false);
  // SWA and SWB each drove both cars, but at different events.
  assert.equal(areTeammates(SEASON, "SWA", "SWB"), false);
  assert.equal(areTeammates(SEASON, "SWB", "TOP"), true);
  // A code the season doesn't have: unknown, not "different cars".
  assert.equal(areTeammates(SEASON, "HAM", "XYZ"), null);
});

test("pair labels give the team, the codes and the events, and say when there are too few", () => {
  assert.equal(pairLabel(SEASON.pairs[1]), "Ferrari · HAM and LEC · 24 events");
  assert.equal(pairLabel(SEASON.pairs[3]), "Junior · JRA and SWB · 2 events, too few for intervals");
  assert.equal(pairEventsText(pair("X", "AAA", "BBB", 1)), "1 event, too few for intervals");
  assert.equal(pairEventsText(pair("X", "AAA", "BBB", 3)), "3 events, too few for intervals");
  assert.equal(pairEventsText(pair("X", "AAA", "BBB", 4)), "4 events");
});

test("the phone list groups the pairs by team, in the catalog's order", () => {
  const options = pairOptions(SEASON);
  assert.deepEqual(
    options.map((o) => [o.group, o.value, o.label]),
    [
      ["Alpha", "AAA-BBB", "AAA and BBB · 20 events"],
      ["Ferrari", "HAM-LEC", "HAM and LEC · 24 events"],
      ["Junior", "JRA-SWA", "JRA and SWA · 22 events"],
      ["Junior", "JRA-SWB", "JRA and SWB · 2 events, too few for intervals"],
      ["Senior", "SWA-TOP", "SWA and TOP · 2 events, too few for intervals"],
      ["Senior", "SWB-TOP", "SWB and TOP · 22 events"],
    ],
  );
});

test("each driver once, with every team they drove for", () => {
  const choices = driverChoices(SEASON);
  assert.deepEqual(
    choices.map((c) => c.driver),
    ["AAA", "BBB", "HAM", "JRA", "LEC", "SWA", "SWB", "TOP"],
  );
  assert.equal(choices.find((c) => c.driver === "SWA")?.label, "SWA · Junior, Senior");
  assert.deepEqual(choices.find((c) => c.driver === "HAM")?.teams, ["Ferrari"]);
});

test("a driver's teammate is the one they shared the most events with", () => {
  assert.equal(teammateOf(SEASON, "HAM"), "LEC");
  assert.equal(teammateOf(SEASON, "JRA"), "SWA");
  assert.equal(teammateOf(SEASON, "TOP"), "SWB");
  assert.equal(teammateOf(SEASON, "XYZ"), undefined);
});

test("picking a pair for a season keeps what the reader had", () => {
  // 1. The same two, kept in their order.
  assert.deepEqual(pickPair(SEASON, { a: "LEC", b: "HAM" }), { a: "LEC", b: "HAM" });
  // 2. Driver A with their busiest teammate, A first.
  assert.deepEqual(pickPair(SEASON, { a: "TOP", b: "NOR" }), { a: "TOP", b: "SWB" });
  assert.deepEqual(pickPair(SEASON, { a: "SWA" }), { a: "SWA", b: "JRA" });
  // 3. Else driver B with theirs, B second.
  assert.deepEqual(pickPair(SEASON, { a: "NOR", b: "BBB" }), { a: "AAA", b: "BBB" });
  assert.deepEqual(pickPair(SEASON, { b: "AAA" }), { a: "BBB", b: "AAA" });
  // 4. The default pair, when the season has it.
  assert.deepEqual(pickPair(SEASON), { a: "HAM", b: "LEC" });
  assert.deepEqual(pickPair(SEASON, { a: "NOR", b: "PIA" }), { a: "HAM", b: "LEC" });
  // 5. Else the most events, the catalog's order breaking ties.
  const noFerrari: StyleCatalog = { ...SEASON, pairs: SEASON.pairs.filter((p) => p.team !== "Ferrari") };
  assert.deepEqual(pickPair(noFerrari), { a: "JRA", b: "SWA" });
  // No pairs at all.
  assert.equal(pickPair({ ...SEASON, pairs: [] }), null);
});
