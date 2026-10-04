import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildQuery,
  historyMode,
  intParam,
  pickParam,
  readQuery,
  setQuery,
  withQuery,
  type HistoryHost,
} from "./url.ts";

/** A stand-in for `window` that records history calls and moves the location like a browser. */
function fakeWindow(start: string): HistoryHost & { calls: [string, string][] } {
  const url = new URL(start, "http://localhost:3000");
  const calls: [string, string][] = [];
  const go = (kind: string, next: string) => {
    calls.push([kind, next]);
    const moved = new URL(next, url);
    url.pathname = moved.pathname;
    url.search = moved.search;
    url.hash = moved.hash;
  };
  return {
    calls,
    location: {
      get pathname() {
        return url.pathname;
      },
      get search() {
        return url.search;
      },
      get hash() {
        return url.hash;
      },
    },
    history: {
      pushState: (_data, _unused, next) => go("push", String(next)),
      replaceState: (_data, _unused, next) => go("replace", String(next)),
    },
  };
}

test("buildQuery drops empty values and sorts the keys", () => {
  assert.equal(
    buildQuery({ year: 2026, session: "R", driver: undefined, event: "Azerbaijan Grand Prix", limit: null, explain: "" }),
    "event=Azerbaijan+Grand+Prix&session=R&year=2026",
  );
  assert.equal(buildQuery({}), "");
  assert.equal(buildQuery({ a: undefined, b: null, c: "" }), "");
  assert.equal(buildQuery({ n: Number.NaN, m: Number.POSITIVE_INFINITY, z: 0 }), "z=0");
  assert.equal(buildQuery({ ok: true, no: false }), "no=false&ok=true");
  // The order of the keys given never matters.
  assert.equal(buildQuery({ b: "2", a: "1", B: "3" }), buildQuery({ B: "3", a: "1", b: "2" }));
  assert.equal(buildQuery({ b: "2", a: "1", B: "3" }), "B=3&a=1&b=2");
});

test("buildQuery keeps ':' and spaces readable and escapes the rest", () => {
  assert.equal(buildQuery({ explain: "ALB:51:11" }), "explain=ALB:51:11");
  assert.equal(buildQuery({ q: "a&b=c#d+e%f/g?h" }), "q=a%26b%3Dc%23d%2Be%25f%2Fg%3Fh");
  assert.equal(buildQuery({ event: "São Paulo Grand Prix" }), "event=S%C3%A3o+Paulo+Grand+Prix");
});

test("every value survives buildQuery and URLSearchParams unchanged", () => {
  const values = ["Azerbaijan Grand Prix", "LEC:10:9A", "a+b", "100%", "x&y=z", "São Paulo", " lead", "🏁", "a#b"];
  for (const value of values) {
    const back = new URLSearchParams(buildQuery({ v: value })).get("v");
    assert.equal(back, value);
  }
});

test("withQuery and readQuery", () => {
  assert.equal(withQuery("/mistakes", { year: 2026, event: "Italian Grand Prix" }), "/mistakes?event=Italian+Grand+Prix&year=2026");
  assert.equal(withQuery("/mistakes", { year: undefined }), "/mistakes");
  assert.deepEqual(readQuery("?b=2&a=1&a=3&c="), { b: "2", a: "1", c: "" });
  assert.deepEqual(readQuery("event=Azerbaijan%20Grand%20Prix"), { event: "Azerbaijan Grand Prix" });
  assert.deepEqual(readQuery(""), {});
});

test("setQuery pushes a selection and replaces a toggle", () => {
  const win = fakeWindow("/mistakes#featured");
  assert.equal(setQuery({ year: "2026", event: "Azerbaijan Grand Prix", session: "R" }, { push: true }, win), true);
  assert.equal(setQuery({ year: "2026", event: "Azerbaijan Grand Prix", session: "R", limit: "25" }, { push: false }, win), true);
  assert.deepEqual(win.calls, [
    ["push", "/mistakes?event=Azerbaijan+Grand+Prix&session=R&year=2026#featured"],
    ["replace", "/mistakes?event=Azerbaijan+Grand+Prix&limit=25&session=R&year=2026#featured"],
  ]);
});

test("setQuery does nothing when the query wouldn't change, however the URL was written", () => {
  const win = fakeWindow("/styles?b=LEC&a=HAM&year=2025&kind=");
  assert.equal(setQuery({ year: "2025", a: "HAM", b: "LEC", plane: undefined }, { push: true }, win), false);
  const spaced = fakeWindow("/mistakes?event=Azerbaijan%20Grand%20Prix&year=2026");
  assert.equal(setQuery({ event: "Azerbaijan Grand Prix", year: "2026" }, { push: true }, spaced), false);
  assert.deepEqual([...win.calls, ...spaced.calls], []);
});

test("setQuery can clear the query back to the bare path", () => {
  const win = fakeWindow("/styles?year=2025");
  assert.equal(setQuery({ year: undefined }, { push: false }, win), true);
  assert.deepEqual(win.calls, [["replace", "/styles"]]);
  assert.equal(win.location.search, "");
});

test("historyMode: push for selections, replace for toggles, null for no change", () => {
  const push = ["year", "event", "session", "explain"];
  const base = { year: "2026", event: "Azerbaijan Grand Prix", session: "R" };
  assert.equal(historyMode(base, { ...base }, push), null);
  assert.equal(historyMode(base, { ...base, driver: "" }, push), null);
  assert.equal(historyMode(base, { ...base, limit: "25" }, push), "replace");
  assert.equal(historyMode(base, { ...base, driver: "LEC", limit: "25" }, push), "replace");
  assert.equal(historyMode(base, { ...base, session: "Q" }, push), "push");
  assert.equal(historyMode(base, { ...base, explain: "NOR:16:5", limit: "50" }, push), "push");
  assert.equal(historyMode({ ...base, explain: "NOR:16:5" }, base, push), "push");
  assert.equal(historyMode({ year: 2026 }, { year: "2026" }, push), null);
});

test("intParam and pickParam", () => {
  assert.equal(intParam("2026", 2018, 2030), 2026);
  assert.equal(intParam(" 25 ", 1, 50), 25);
  for (const bad of [null, undefined, "", "1999", "2031", "20.5", "-1", "1e3", "+5", "two", "2026x"]) {
    assert.equal(intParam(bad, 2018, 2030), undefined, String(bad));
  }
  assert.equal(pickParam("r", ["Q", "R"] as const), "R");
  assert.equal(pickParam(" Quali ", ["all", "quali", "race"] as const), "quali");
  assert.equal(pickParam("X", ["Q", "R"] as const), undefined);
  assert.equal(pickParam(null, ["Q", "R"] as const), undefined);
});
