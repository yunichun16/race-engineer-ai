import { test } from "node:test";
import assert from "node:assert/strict";
import { FEATURED, featuredHref } from "../../content/featured.ts";
import type { CatalogWeekend, SessionCatalog, SessionCode, SessionDrivers } from "../../lib/api/types.ts";
import { buildQuery } from "../../lib/url.ts";
import { mistakesHref, parseMistakesQuery, serializeMistakesQuery } from "./query.ts";
import {
  askInChatHref,
  defaultSession,
  defaultWeekend,
  driverOptions,
  evidenceText,
  featuredFor,
  featuredLine,
  featuredQuery,
  loadedMessage,
  needsCoverageNote,
  resolveSelection,
  selectionSummary,
  sessionOptions,
  weekendLabel,
  weekendOptions,
} from "./selection.ts";

// A small synthetic catalog in the API's shape: newest season first, newest weekend first.
function weekend(round: number, event: string, location: string, date: string, sessions: [SessionCode, boolean][]): CatalogWeekend {
  return {
    round,
    event,
    location,
    date,
    sessions: sessions.map(([code, scored]) => ({ key: `x_${round}_${code}`, code, name: code, date, scored })),
  };
}

const CATALOG: SessionCatalog = {
  coverage: { first: "2025 Bahrain Grand Prix (Q)", last: "2026 Azerbaijan Grand Prix (R)", sessions: 13 },
  results_current: true,
  seasons: [
    {
      year: 2026,
      style: true,
      weekends: [
        // The newest weekend has no scored race yet: the default skips it.
        weekend(16, "Singapore Grand Prix", "Marina Bay", "2026-10-04", [["Q", true], ["R", false]]),
        weekend(15, "Azerbaijan Grand Prix", "Baku", "2026-09-26", [["Q", true], ["R", true]]),
        weekend(12, "Dutch Grand Prix", "Zandvoort", "2026-08-23", [["SQ", true], ["S", true], ["Q", true], ["R", true]]),
      ],
    },
    {
      year: 2025,
      style: true,
      weekends: [
        weekend(24, "Abu Dhabi Grand Prix", "Yas Island", "2025-12-07", [["Q", false], ["R", false]]),
        weekend(16, "Italian Grand Prix", "Monza", "2025-09-07", [["Q", true], ["R", true]]),
        weekend(6, "Miami Grand Prix", "Miami Gardens", "2025-05-04", [["SQ", true], ["S", false], ["Q", true], ["R", false]]),
      ],
    },
  ],
};

const q = (search: string) => parseMistakesQuery(new URLSearchParams(search));

test("defaults with no URL: the newest season's newest weekend with a scored race, and its race", () => {
  assert.deepEqual(resolveSelection({}, CATALOG), { year: 2026, event: "Azerbaijan Grand Prix", session: "R" });
});

test("a full link is taken as it is, without waiting for the catalog", () => {
  const link = q("year=2026&event=Azerbaijan+Grand+Prix&session=R&explain=NOR:16:5");
  assert.deepEqual(resolveSelection(link, null), { year: 2026, event: "Azerbaijan Grand Prix", session: "R" });
  // Even one the catalog doesn't list: the tool's own message then says what there is.
  assert.deepEqual(resolveSelection(q("year=2026&event=Nowhere&session=Q"), CATALOG), { year: 2026, event: "Nowhere", session: "Q" });
});

test("a partial link waits for the catalog, which fills in the rest", () => {
  assert.equal(resolveSelection(q("year=2025"), null), null);
  assert.equal(resolveSelection({}, null), null);
  assert.equal(resolveSelection({}, { ...CATALOG, seasons: [] }), null);
  // A season alone: its newest weekend with a scored race (Abu Dhabi has none).
  assert.deepEqual(resolveSelection(q("year=2025"), CATALOG), { year: 2025, event: "Italian Grand Prix", session: "R" });
  // An event alone: the newest season with it.
  assert.deepEqual(resolveSelection(q("event=Miami+Grand+Prix"), CATALOG), { year: 2025, event: "Miami Grand Prix", session: "Q" });
  // A season and an event: that weekend's race, else its qualifying.
  assert.deepEqual(resolveSelection(q("year=2026&event=Dutch+Grand+Prix"), CATALOG), { year: 2026, event: "Dutch Grand Prix", session: "R" });
  // A session the weekend had is kept; one it didn't have gives the weekend's default.
  assert.deepEqual(resolveSelection(q("year=2026&event=Dutch+Grand+Prix&session=SQ"), CATALOG)?.session, "SQ");
  assert.deepEqual(resolveSelection(q("event=Italian+Grand+Prix&session=S"), CATALOG), { year: 2025, event: "Italian Grand Prix", session: "R" });
  // A season the data doesn't have gives the newest; an event that season didn't have, its default weekend.
  assert.deepEqual(resolveSelection(q("year=2019"), CATALOG), { year: 2026, event: "Azerbaijan Grand Prix", session: "R" });
  assert.deepEqual(resolveSelection(q("year=2025&event=Dutch+Grand+Prix"), CATALOG), { year: 2025, event: "Italian Grand Prix", session: "R" });
});

test("a weekend's default session: race, else qualifying, else its first scored", () => {
  const w = (sessions: [SessionCode, boolean][]) => weekend(1, "X", "X", "2025-01-01", sessions);
  assert.equal(defaultSession(w([["Q", true], ["R", true]])), "R");
  assert.equal(defaultSession(w([["Q", true], ["R", false]])), "Q");
  assert.equal(defaultSession(w([["SQ", true], ["S", false], ["Q", false], ["R", false]])), "SQ");
  assert.equal(defaultSession(w([["Q", false], ["R", false]])), "R");
  assert.equal(defaultSession(w([["Q", false]])), "Q");
  // No scored race anywhere in the season: the newest with a scored qualifying.
  const season = { year: 2025, style: true, weekends: [w([["Q", false], ["R", false]]), { ...w([["Q", true], ["R", false]]), event: "Y" }] };
  assert.equal(defaultWeekend(season)?.event, "Y");
});

test("the Grand Prix picker: newest first, unscored weekends marked and disabled", () => {
  const options = weekendOptions(CATALOG, 2025);
  assert.deepEqual(
    options.map((o) => o.label),
    ["R24 · Abu Dhabi Grand Prix · 7 Dec (not scored)", "R16 · Italian Grand Prix · 7 Sep", "R6 · Miami Grand Prix · 4 May"],
  );
  assert.deepEqual(options.map((o) => !!o.disabled), [true, false, false]);
  assert.equal(weekendLabel(CATALOG.seasons[0].weekends[1]), "R15 · Azerbaijan Grand Prix · 26 Sep");
  // With the results out of date nothing is scored, so nothing is disabled (the page says why).
  assert.ok(weekendOptions({ ...CATALOG, results_current: false }, 2025).every((o) => !o.disabled));
  // An event the season doesn't list stays visible.
  assert.equal(weekendOptions(CATALOG, 2026, "Nowhere")[0].value, "Nowhere");
  assert.deepEqual(weekendOptions(null, 2026, "Azerbaijan Grand Prix"), [{ value: "Azerbaijan Grand Prix", label: "Azerbaijan Grand Prix" }]);
});

test("the Session picker: weekend order, unscored ones noted and disabled", () => {
  const options = sessionOptions(CATALOG, { year: 2025, event: "Miami Grand Prix", session: "Q" });
  assert.deepEqual(options.map((o) => o.value), ["SQ", "S", "Q", "R"]);
  assert.deepEqual(options.map((o) => o.label), ["Sprint Qualifying", "Sprint", "Qualifying", "Race"]);
  assert.deepEqual(options.map((o) => o.note ?? ""), ["", "(not scored)", "", "(not scored)"]);
  assert.deepEqual(options.map((o) => !!o.disabled), [false, true, false, true]);
  assert.deepEqual(sessionOptions(null, { year: 2026, event: "X", session: "R" }), [{ value: "R", label: "Race" }]);
});

test("the Driver picker: the whole field, then drivers by code with their team", () => {
  const drivers = {
    session: {} as SessionDrivers["session"],
    drivers: [
      { driver: "NOR", team: "McLaren", laps: 51 },
      { driver: "HAM", team: "Ferrari", laps: 51 },
      { driver: "LEC", team: "Ferrari", laps: 51 },
    ],
  };
  assert.deepEqual(
    driverOptions(drivers).map((o) => o.label),
    ["Whole field", "HAM · Ferrari", "LEC · Ferrari", "NOR · McLaren"],
  );
  assert.equal(driverOptions(drivers)[0].value, "");
  // Before the list arrives, or for a driver it doesn't have, the chosen code stays a row.
  assert.deepEqual(driverOptions(null, "LEC").map((o) => o.value), ["", "LEC"]);
  assert.deepEqual(driverOptions(drivers, "VER").at(-1), { value: "VER", label: "VER" });
});

test("the selection bar's words", () => {
  const sel = { year: 2026, event: "Azerbaijan Grand Prix", session: "R" as const };
  assert.equal(selectionSummary(sel), "2026 Azerbaijan GP · Race · All drivers");
  assert.equal(selectionSummary(sel, "LEC"), "2026 Azerbaijan GP · Race · LEC");
});

test("every featured card opens its corner or its session, and its link says the same", () => {
  for (const f of FEATURED) {
    const state = featuredQuery(f);
    assert.equal(state.event, f.args.event, f.id);
    if (f.args.lap !== undefined) {
      assert.deepEqual(state.explain, { driver: f.args.driver, lap: f.args.lap, turn: f.args.turn }, f.id);
      assert.equal(state.driver, undefined, f.id);
    } else {
      assert.equal(state.driver, f.args.driver, f.id);
    }
    // The landing's links (featuredHref) and the card's state agree, and survive the URL.
    assert.deepEqual(parseMistakesQuery(new URL(featuredHref(f), "http://x").searchParams), state, f.id);
    assert.deepEqual(parseMistakesQuery(new URL(mistakesHref(state), "http://x").searchParams), state, f.id);
    assert.equal(buildQuery(serializeMistakesQuery(state)), new URL(mistakesHref(state), "http://x").search.slice(1));
    // An open corner finds its entry again, for its blurb.
    if (state.explain) {
      const sel = { year: f.args.year, event: f.args.event, session: f.args.session };
      assert.equal(featuredFor(sel, state.explain), f, f.id);
    }
  }
});

test("a featured card's line: the place, season, session and corner, no numbers or verdict", () => {
  const [nor] = FEATURED;
  assert.equal(featuredLine(nor, "Baku"), "Baku 2026 · Race · lap 16, T5");
  assert.equal(featuredLine(nor), "Azerbaijan GP 2026 · Race · lap 16, T5");
  const lec = FEATURED.find((f) => f.kind === "nothing-flagged");
  assert.ok(lec);
  assert.equal(featuredLine(lec, "Monza"), "Monza 2025 · Qualifying · whole session");
});

test("the evidence line: a featured blurb, else keyed on the corner's answer, never a verdict or a figure", () => {
  const [nor] = FEATURED;
  assert.equal(evidenceText(nor, null), nor.blurb);
  assert.equal(evidenceText(undefined, null), null);
  assert.equal(evidenceText(undefined, { flagged: false, type: "over_slowing" }), "Not flagged: shown for comparison.");
  assert.equal(evidenceText(undefined, { flagged: false, type: "track_limits" }), "Not flagged: shown for comparison.");
  // A lap the detectors don't score (SAI's opening lap at Baku 2026 answers flagged null, type
  // "battle"): nothing found it, so it isn't "from the telemetry", nor "not flagged".
  assert.equal(evidenceText(undefined, { flagged: null, type: "battle" }), "Not scored by the model: shown for comparison.");
  assert.equal(evidenceText(undefined, { flagged: null, type: "track_limits" }), "Race control named this moment.");
  assert.equal(evidenceText(undefined, { flagged: true, type: "track_limits" }), "Race control named this moment.");
  assert.equal(evidenceText(undefined, { flagged: true, type: "over_slowing" }), "Found from the telemetry alone.");
  for (const f of FEATURED) assert.doesNotMatch(evidenceText(f, null) ?? "", /\d|review|verdict|checked|accura|precision/i, f.id);
});

test("ask in chat prefills a question about the corner", () => {
  const href = askInChatHref({ year: 2026, event: "Azerbaijan Grand Prix", session: "R" }, { driver: "NOR", lap: 16, turn: "5" });
  assert.equal(new URL(href, "http://x").pathname, "/chat");
  assert.equal(new URL(href, "http://x").searchParams.get("q"), "Explain NOR's turn 5 on lap 16 at the 2026 Azerbaijan Grand Prix (Race).");
});

test("the load announcement and the coverage note", () => {
  const list = { mistakes: new Array(10), driver: null, year: 2026, event: "Azerbaijan Grand Prix", session: "Race" };
  assert.equal(loadedMessage(list), "Loaded 10 mistakes for the 2026 Azerbaijan Grand Prix race");
  assert.equal(loadedMessage({ ...list, mistakes: [], driver: "LEC" }), "Loaded 0 mistakes by LEC for the 2026 Azerbaijan Grand Prix race");
  assert.equal(loadedMessage({ ...list, mistakes: new Array(1) }), "Loaded 1 mistake for the 2026 Azerbaijan Grand Prix race");
  const coverage = { laps: [], scored_driver_laps: 6, eligible_driver_laps: 6, session_laps: 22, low: false };
  assert.equal(needsCoverageNote({ driver: "LEC", coverage }), false);
  assert.equal(needsCoverageNote({ driver: "LEC", coverage: { ...coverage, scored_driver_laps: 0 } }), true);
  assert.equal(needsCoverageNote({ driver: null, coverage: { ...coverage, low: true } }), true);
});
