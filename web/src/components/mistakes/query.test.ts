import { test } from "node:test";
import assert from "node:assert/strict";
import { buildQuery } from "../../lib/url.ts";
import {
  explainRef,
  explainSelection,
  formatExplain,
  mistakesHref,
  mistakesUpdate,
  normalizeTurn,
  parseExplain,
  parseMistakesQuery,
  sameCorner,
  serializeMistakesQuery,
  type MistakesQuery,
} from "./query.ts";

const parse = (search: string) => parseMistakesQuery(new URLSearchParams(search));
const roundTrip = (q: MistakesQuery) => parse(buildQuery(serializeMistakesQuery(q)));

test("a full state survives serialize and parse", () => {
  const states: MistakesQuery[] = [
    {},
    { year: 2026 },
    { year: 2026, event: "Azerbaijan Grand Prix", session: "R" },
    { year: 2026, event: "Azerbaijan Grand Prix", session: "R", driver: "NOR", limit: 25, explain: { driver: "NOR", lap: 16, turn: "5" } },
    { year: 2023, event: "Monaco Grand Prix", session: "R", explain: { driver: "ALB", lap: 51, turn: "11" } },
    { year: 2026, event: "Hungarian Grand Prix", session: "R", explain: { driver: "ALB", lap: 6, turn: "12A" } },
    { year: 2023, event: "Belgian Grand Prix", session: "SS", limit: 50 },
    { event: "São Paulo Grand Prix", session: "SQ", driver: "LEC", limit: 10 },
  ];
  for (const q of states) assert.deepEqual(roundTrip(q), q);
});

test("the canonical query is stable: parse then serialize gives the same string", () => {
  const search = "year=2026&session=R&explain=LEC%3A10%3A9a&event=Italian%20Grand%20Prix&limit=25&driver=lec";
  const once = buildQuery(serializeMistakesQuery(parse(search)));
  assert.equal(once, "driver=LEC&event=Italian+Grand+Prix&explain=LEC:10:9A&limit=25&session=R&year=2026");
  assert.equal(buildQuery(serializeMistakesQuery(parse(once))), once);
});

test("invalid values are dropped key by key, so a hand-edited link never gives a 422", () => {
  assert.deepEqual(parse("year=1999&session=X&explain=bad"), {});
  assert.deepEqual(parse("year=2031&event=&session=FP1&driver=LECL&limit=20&explain=LEC:10:9"), {});
  assert.deepEqual(parse("year=2026.5&driver=L3C&limit=abc"), {});
  assert.deepEqual(parse(`event=${"x".repeat(101)}`), {});
  assert.deepEqual(parse("event=Bad%0AName"), {});
  // The good keys of a partly bad link still load.
  assert.deepEqual(parse("year=2026&event=Azerbaijan+Grand+Prix&session=X&limit=7&explain=NOR:999:5"), {
    year: 2026,
    event: "Azerbaijan Grand Prix",
  });
  assert.deepEqual(parse(`event=${"x".repeat(100)}`), { event: "x".repeat(100) });
});

test("an open corner needs a named session", () => {
  assert.deepEqual(parse("explain=ALB:51:11"), {});
  assert.deepEqual(parse("year=2023&explain=ALB:51:11"), { year: 2023 });
  assert.deepEqual(parse("event=Monaco+Grand+Prix&explain=ALB:51:11"), {
    event: "Monaco Grand Prix",
    explain: { driver: "ALB", lap: 51, turn: "11" },
  });
  assert.equal(serializeMistakesQuery({ explain: { driver: "ALB", lap: 51, turn: "11" } }).explain, undefined);
});

test("session and driver codes are read in any case and written upper-case", () => {
  assert.deepEqual(parse("session=sq&driver=nor"), { session: "SQ", driver: "NOR" });
  for (const code of ["Q", "SQ", "SS", "S", "R"]) assert.equal(parse(`session=${code}`).session, code);
  // Letters that only upper-case into ASCII ("ß" → "SS", "ı" → "I", "ﬀ" → "FF") aren't codes.
  for (const bad of ["%C3%9Fa", "%C4%B1ec", "%EF%AC%80i"]) assert.deepEqual(parse(`driver=${bad}`), {}, bad);
  assert.deepEqual(parse("session=%C5%BF"), {});
});

test("only 10, 25 and 50 rows", () => {
  assert.equal(parse("limit=10").limit, 10);
  assert.equal(parse("limit=25").limit, 25);
  assert.equal(parse("limit=50").limit, 50);
  for (const bad of ["1", "11", "49", "51", "100", "0", "-10", "ten"]) assert.equal(parse(`limit=${bad}`).limit, undefined, bad);
});

test("the explain codec: LEC:10:9a and T9a are normalised", () => {
  assert.deepEqual(parseExplain("LEC:10:9a"), { driver: "LEC", lap: 10, turn: "9A" });
  assert.deepEqual(parseExplain("LEC:10:T9a"), { driver: "LEC", lap: 10, turn: "9A" });
  assert.deepEqual(parseExplain("lec:10:t9A"), { driver: "LEC", lap: 10, turn: "9A" });
  assert.deepEqual(parseExplain("ALB:51:11"), { driver: "ALB", lap: 51, turn: "11" });
  assert.deepEqual(parseExplain("ALB:051:011"), { driver: "ALB", lap: 51, turn: "11" });
  assert.deepEqual(parseExplain(" NOR : 16 : T5 "), { driver: "NOR", lap: 16, turn: "5" });
  assert.equal(formatExplain({ driver: "LEC", lap: 10, turn: "9A" }), "LEC:10:9A");
  for (const bad of [
    null, "", "bad", "LEC:10", "LEC:10:9:1", "LE:10:9", "LEC:0:9", "LEC:101:9", "LEC:1.5:9",
    "LEC:10:0", "LEC:10:100", "LEC:10:9ab", "LEC:10:TURN9", "LEC:10:", "LEC::9", "LEC:-1:9",
  ]) {
    assert.equal(parseExplain(bad), null, String(bad));
  }
});

test("turn labels", () => {
  assert.equal(normalizeTurn(7), "7");
  assert.equal(normalizeTurn("7"), "7");
  assert.equal(normalizeTurn("T7"), "7");
  assert.equal(normalizeTurn("9a"), "9A");
  assert.equal(normalizeTurn("12A"), "12A");
  assert.equal(normalizeTurn("t09a"), "9A");
  assert.equal(normalizeTurn(99), "99");
  for (const bad of [0, 100, "", "T", "A", "9-a", "turn 9", "1.5", "9ı", "9ß", "９"]) assert.equal(normalizeTurn(bad), null, String(bad));
});

test("a payload row and the open corner", () => {
  const ref = explainRef("ALB", 6, "12a");
  assert.deepEqual(ref, { driver: "ALB", lap: 6, turn: "12A" });
  assert.ok(sameCorner(ref, { driver: "ALB", lap_number: 6, turn: "12a" }));
  assert.ok(!sameCorner(ref, { driver: "ALB", lap_number: 6, turn: "12" }));
  assert.ok(!sameCorner(ref, { driver: "SAI", lap_number: 6, turn: "12a" }));
  assert.ok(!sameCorner(null, { driver: "ALB", lap_number: 6, turn: "12a" }));
  assert.deepEqual(explainSelection(ref), { driver: "ALB", lap_number: 6, turn: "12a" });
  assert.equal(explainSelection(undefined), null);
  assert.equal(explainRef("ALB", 0, "12a"), null);
});

test("the chat's deep link opens its corner", () => {
  assert.deepEqual(parse("year=2026&event=Azerbaijan%20Grand%20Prix&session=R&explain=NOR:16:5"), {
    year: 2026,
    event: "Azerbaijan Grand Prix",
    session: "R",
    explain: { driver: "NOR", lap: 16, turn: "5" },
  });
  // An encoded colon reads the same.
  assert.deepEqual(parse("year=2026&event=Azerbaijan+Grand+Prix&session=R&explain=NOR%3A16%3AT5").explain, { driver: "NOR", lap: 16, turn: "5" });
});

test("links to the explorer", () => {
  assert.equal(
    mistakesHref({ year: 2026, event: "Azerbaijan Grand Prix", session: "R", explain: { driver: "NOR", lap: 16, turn: "5" } }),
    "/mistakes?event=Azerbaijan+Grand+Prix&explain=NOR:16:5&session=R&year=2026",
  );
  assert.equal(mistakesHref({}), "/mistakes");
});

test("selections push, the filter and the row count replace", () => {
  const base: MistakesQuery = { year: 2026, event: "Azerbaijan Grand Prix", session: "R" };
  const corner = { driver: "NOR", lap: 16, turn: "5" };
  assert.equal(mistakesUpdate(base, { ...base }), null);
  assert.equal(mistakesUpdate(base, { ...base, driver: "LEC" }), "replace");
  assert.equal(mistakesUpdate(base, { ...base, limit: 25 }), "replace");
  assert.equal(mistakesUpdate(base, { ...base, session: "Q" }), "push");
  assert.equal(mistakesUpdate(base, { ...base, year: 2025 }), "push");
  assert.equal(mistakesUpdate(base, { ...base, event: "Italian Grand Prix" }), "push");
  assert.equal(mistakesUpdate(base, { ...base, explain: corner }), "push");
  assert.equal(mistakesUpdate({ ...base, explain: corner }, base), "push");
  assert.equal(mistakesUpdate({ ...base, explain: corner }, { ...base, explain: { ...corner } }), null);
});
