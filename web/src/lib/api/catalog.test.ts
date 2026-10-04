/** The catalog calls and tool keys: their URLs, the outline checks and errors passed through. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { getSessionCatalog, getSessionDrivers, getStyleCatalog } from "./catalog.ts";
import { toolKey } from "./tools.ts";
import { errorBody, fakeFetch, json } from "./test-support.ts";
import type { SessionCatalog, SessionDrivers, StyleCatalog } from "./types.ts";

const BASE = "http://api.test";

const CATALOG: SessionCatalog = {
  coverage: { first: "2022 Bahrain Grand Prix (Q)", last: "2026 Azerbaijan Grand Prix (R)", sessions: 263 },
  results_current: true,
  seasons: [
    {
      year: 2026,
      style: true,
      weekends: [
        {
          round: 15,
          event: "Azerbaijan Grand Prix",
          location: "Baku",
          date: "2026-09-26",
          sessions: [
            { key: "2026_15_Q", code: "Q", name: "Qualifying", date: "2026-09-25", scored: true },
            { key: "2026_15_R", code: "R", name: "Race", date: "2026-09-26", scored: true },
          ],
        },
      ],
    },
  ],
};

const DRIVERS: SessionDrivers = {
  session: {
    key: "2026_15_R",
    year: 2026,
    round: 15,
    event: "Azerbaijan Grand Prix",
    location: "Baku",
    session_code: "R",
    session_name: "Race",
    date: "2026-09-26",
  },
  drivers: [{ driver: "HAM", team: "Ferrari", laps: 51 }],
};

const STYLES: StyleCatalog = {
  years: [2026, 2025],
  year: 2025,
  drivers: [{ driver: "HAM", team: "Ferrari", events: 24 }],
  pairs: [{ team: "Ferrari", a: "HAM", b: "LEC", events: 24, consistent: [true, true] }],
};

test("the session catalog is GET /api/catalog/sessions with no query", async () => {
  const fetch = fakeFetch(() => json(CATALOG));
  assert.deepEqual(await getSessionCatalog({ fetch, baseUrl: BASE }), CATALOG);
  assert.equal(fetch.calls[0].url, `${BASE}/api/catalog/sessions`);
});

test("a session's drivers are asked by event, year and session, keys sorted", async () => {
  const fetch = fakeFetch(() => json(DRIVERS));
  const sel = { event: "Azerbaijan Grand Prix", year: 2026, session: "R" } as const;
  assert.deepEqual(await getSessionDrivers(sel, { fetch, baseUrl: BASE }), DRIVERS);
  assert.equal(fetch.calls[0].url, `${BASE}/api/catalog/drivers?event=Azerbaijan+Grand+Prix&session=R&year=2026`);
});

test("the style catalog leaves the year out for the newest season", async () => {
  const fetch = fakeFetch(() => json(STYLES));
  await getStyleCatalog(null, { fetch, baseUrl: BASE });
  await getStyleCatalog(2025, { fetch, baseUrl: BASE });
  assert.deepEqual(
    fetch.calls.map((c) => c.url),
    [`${BASE}/api/catalog/styles`, `${BASE}/api/catalog/styles?year=2025`],
  );
});

test("an answer without the catalog's outline is bad_payload", async () => {
  const cases: [() => Promise<unknown>, unknown][] = [];
  for (const body of [{ seasons: "all" }, { coverage: {}, seasons: [] }, DRIVERS, null]) {
    const fetch = fakeFetch(() => json(body));
    cases.push([() => getSessionCatalog({ fetch, baseUrl: BASE }), body]);
  }
  for (const body of [{ session: null, drivers: [] }, { drivers: [] }, CATALOG]) {
    const fetch = fakeFetch(() => json(body));
    cases.push([() => getSessionDrivers({ event: "Baku", year: 2026, session: "R" }, { fetch, baseUrl: BASE }), body]);
  }
  for (const body of [{ ...STYLES, year: "2025" }, { years: [], year: 2025, drivers: [] }, CATALOG]) {
    const fetch = fakeFetch(() => json(body));
    cases.push([() => getStyleCatalog(2025, { fetch, baseUrl: BASE }), body]);
  }
  for (const [call, body] of cases) {
    await assert.rejects(call(), { name: "ApiError", code: "bad_payload", status: 200 }, JSON.stringify(body));
  }
});

test("the catalog's errors pass through: 422 for an unknown event, 503 for missing style tables", async () => {
  const unknown = fakeFetch(() => json(errorBody("invalid_input", "No processed event matches 'Atlantis'."), 422));
  await assert.rejects(getSessionDrivers({ event: "Atlantis", year: 2026, session: "R" }, { fetch: unknown, baseUrl: BASE }), {
    status: 422,
    code: "invalid_input",
    message: "No processed event matches 'Atlantis'.",
    tool: undefined,
  });
  const missing = fakeFetch(() => json(errorBody("results_unavailable", "Driving-style data hasn't been built yet."), 503));
  await assert.rejects(getStyleCatalog(null, { fetch: missing, baseUrl: BASE }), { status: 503, code: "results_unavailable" });
});

test("a tool's store key is its URL, the same for equal parameters in any order", () => {
  const a = toolKey("explain_corner", { event: "Azerbaijan Grand Prix", driver: "ALB", lap: 51, corner: "11", year: 2026, session: "R" });
  const b = toolKey("explain_corner", { session: "R", year: 2026, corner: "11", lap: 51, driver: "ALB", event: "Azerbaijan Grand Prix" });
  assert.equal(a, b);
  assert.equal(
    a,
    "http://127.0.0.1:8000/api/tools/explain_corner?corner=11&driver=ALB&event=Azerbaijan+Grand+Prix&lap=51&session=R&year=2026",
  );
  assert.equal(
    toolKey("find_mistakes", { event: "Baku", driver: undefined, limit: 10 }),
    "http://127.0.0.1:8000/api/tools/find_mistakes?event=Baku&limit=10",
  );
});
