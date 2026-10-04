import { test } from "node:test";
import assert from "node:assert/strict";
import { buildQuery } from "../../lib/url.ts";
import {
  DEFAULT_STYLES,
  driverCode,
  FALLBACK_YEARS,
  parseStylesQuery,
  serializeStylesQuery,
  stylesHref,
  stylesUpdate,
  withStylesDefaults,
  type StylesQuery,
} from "./query.ts";

const parse = (search: string) => parseStylesQuery(new URLSearchParams(search));
const roundTrip = (q: StylesQuery) => parse(buildQuery(serializeStylesQuery(q)));

test("a full state survives serialize and parse", () => {
  const states: StylesQuery[] = [
    {},
    { year: 2025, a: "HAM", b: "LEC" },
    { year: 2026, a: "ALB", b: "SAI", kind: "race", plane: "car" },
    { year: 2025, a: "NOR", b: "PIA", kind: "quali", plane: "style", laps: "Abu Dhabi Grand Prix" },
    { a: "VER" },
    { laps: "São Paulo Grand Prix", kind: "all" },
  ];
  for (const q of states) assert.deepEqual(roundTrip(q), q);
});

test("the canonical query is stable", () => {
  const once = buildQuery(serializeStylesQuery(parse("plane=CAR&b=lec&a=ham&year=2025&kind=Race&laps=Abu%20Dhabi%20Grand%20Prix")));
  assert.equal(once, "a=HAM&b=LEC&kind=race&laps=Abu+Dhabi+Grand+Prix&plane=car&year=2025");
  assert.equal(buildQuery(serializeStylesQuery(parse(once))), once);
});

test("invalid values are dropped key by key", () => {
  assert.deepEqual(parse("year=1999&a=HAMM&b=L&kind=sprint&plane=x&laps="), {});
  assert.deepEqual(parse("year=2031&a=H4M&b=%20%20"), {});
  assert.deepEqual(parse(`laps=${"x".repeat(101)}`), {});
  assert.deepEqual(parse("year=2025&a=HAM&kind=both"), { year: 2025, a: "HAM" });
  // Letters that only upper-case into ASCII aren't driver codes, and kinds match ASCII only.
  assert.deepEqual(parse("a=%C3%9Fa&b=%C4%B1ec&kind=QUAL%C4%B0"), {});
});

test("a pair of the same driver is rejected: b is dropped, a kept", () => {
  assert.deepEqual(parse("year=2025&a=HAM&b=HAM"), { year: 2025, a: "HAM" });
  assert.deepEqual(parse("a=ham&b=HAM"), { a: "HAM" });
  assert.equal(serializeStylesQuery({ a: "HAM", b: "HAM" }).b, undefined);
  // b on its own is kept (the page asks for A).
  assert.deepEqual(parse("b=LEC"), { b: "LEC" });
});

test("links to the explorer", () => {
  assert.equal(stylesHref({ year: 2025, a: "HAM", b: "LEC" }), "/styles?a=HAM&b=LEC&year=2025");
  assert.equal(
    stylesHref({ year: 2025, a: "NOR", b: "PIA", laps: "Abu Dhabi Grand Prix" }),
    "/styles?a=NOR&b=PIA&laps=Abu+Dhabi+Grand+Prix&year=2025",
  );
  assert.equal(stylesHref({}), "/styles");
});

test("the season and the pair push; kind, plane and laps replace", () => {
  const base: StylesQuery = { year: 2025, a: "HAM", b: "LEC" };
  assert.equal(stylesUpdate(base, { ...base }), null);
  assert.equal(stylesUpdate(base, { ...base, kind: "quali" }), "replace");
  assert.equal(stylesUpdate(base, { ...base, plane: "car" }), "replace");
  assert.equal(stylesUpdate(base, { ...base, laps: "Monaco Grand Prix", kind: "race" }), "replace");
  assert.equal(stylesUpdate(base, { ...base, b: "SAI" }), "push");
  assert.equal(stylesUpdate(base, { ...base, a: "SAI", kind: "race" }), "push");
  assert.equal(stylesUpdate(base, { ...base, year: 2026 }), "push");
});

test("defaults: HAM and LEC in 2025 when the URL names neither a season nor a driver", () => {
  assert.deepEqual(DEFAULT_STYLES, { year: 2025, a: "HAM", b: "LEC" });
  assert.deepEqual(withStylesDefaults({}), { year: 2025, a: "HAM", b: "LEC" });
  assert.deepEqual(withStylesDefaults({ kind: "race" }), { year: 2025, a: "HAM", b: "LEC", kind: "race" });
  assert.deepEqual(withStylesDefaults({ plane: "car", laps: "Monaco Grand Prix" }), {
    year: 2025,
    a: "HAM",
    b: "LEC",
    plane: "car",
    laps: "Monaco Grand Prix",
  });
  // A season (even the default one) or a driver already named: the page picks the pair from
  // that season's list, keeping what it can, so the defaults stay out.
  assert.deepEqual(withStylesDefaults({ year: 2025 }), { year: 2025 });
  assert.deepEqual(withStylesDefaults({ year: 2023, kind: "quali" }), { year: 2023, kind: "quali" });
  assert.deepEqual(withStylesDefaults({ a: "VER" }), { a: "VER" });
  assert.deepEqual(withStylesDefaults({ b: "LEC" }), { b: "LEC" });
  // A full pair without a season is kept as it is: the tool picks the latest season both drove.
  assert.deepEqual(withStylesDefaults({ a: "VER", b: "PER" }), { a: "VER", b: "PER" });
});

test("typed driver codes", () => {
  assert.equal(driverCode(" ham "), "HAM");
  assert.equal(driverCode("Lec"), "LEC");
  assert.equal(driverCode("HAMM"), undefined);
  assert.equal(driverCode("H4M"), undefined);
  assert.equal(driverCode(""), undefined);
  assert.equal(driverCode(undefined), undefined);
});

test("the fallback seasons are newest first and the tools accept them", () => {
  assert.deepEqual([...FALLBACK_YEARS], [2026, 2025, 2024, 2023, 2022]);
  for (const year of FALLBACK_YEARS) assert.equal(parseStylesQuery(new URLSearchParams(`year=${year}`)).year, year);
});
