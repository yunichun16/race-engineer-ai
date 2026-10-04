import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SESSION_ORDER,
  compareSessions,
  eventShort,
  formatDate,
  formatDateRange,
  isSessionCode,
  lapTime,
  seconds,
  sessionName,
  utcDateParts,
  weekendOrder,
} from "./format.ts";

// Zones on both sides of UTC, out to its extremes (UTC+14 and UTC-11), plus a half-hour one.
const ZONES = ["UTC", "America/Los_Angeles", "Pacific/Kiritimati", "Pacific/Pago_Pago", "Asia/Kolkata", "Europe/London"];

function inEachZone(fn: (zone: string) => void): void {
  const before = process.env.TZ;
  try {
    for (const zone of ZONES) {
      process.env.TZ = zone; // Node picks a new TZ up at once
      fn(zone);
    }
  } finally {
    if (before === undefined) delete process.env.TZ;
    else process.env.TZ = before;
  }
}

test("switching TZ really changes the local zone, so the zone tests below mean something", () => {
  const offsets = new Set<number>();
  inEachZone(() => offsets.add(new Date(Date.UTC(2025, 8, 6, 12)).getTimezoneOffset()));
  assert.ok(offsets.size >= 4, `only ${offsets.size} distinct offsets`);
});

test("a plain date stays the same day in every zone", () => {
  inEachZone((zone) => {
    assert.equal(formatDate("2025-09-06"), "6 Sep", zone);
    assert.equal(formatDate("2025-09-06", { year: true }), "6 Sep 2025", zone);
    assert.equal(formatDate("2026-01-01"), "1 Jan", zone);
    assert.equal(formatDate("2025-12-31", { year: true }), "31 Dec 2025", zone);
    // A timestamp without a zone is taken as written, not as local time.
    assert.equal(formatDate("2025-09-06T23:30:00"), "6 Sep", zone);
    assert.equal(formatDate("2025-09-06 00:15"), "6 Sep", zone);
  });
});

test("a timestamp with a zone is shown as its UTC date in every zone", () => {
  inEachZone((zone) => {
    assert.equal(formatDate("2025-09-06T13:00:00Z"), "6 Sep", zone);
    assert.equal(formatDate("2025-09-06T23:30:00-04:00"), "7 Sep", zone);
    assert.equal(formatDate("2025-09-06T01:30:00+05:30"), "5 Sep", zone);
    assert.equal(formatDate("2025-09-06T12:00:00.250+0000"), "6 Sep", zone);
  });
});

test("every month has its short English name", () => {
  const names = Array.from({ length: 12 }, (_, m) => formatDate(`2025-${String(m + 1).padStart(2, "0")}-15`));
  assert.deepEqual(names, [
    "15 Jan", "15 Feb", "15 Mar", "15 Apr", "15 May", "15 Jun",
    "15 Jul", "15 Aug", "15 Sep", "15 Oct", "15 Nov", "15 Dec",
  ]);
});

test("text that isn't a real date comes back unchanged", () => {
  for (const bad of ["", "soon", "2025-02-30", "2025-13-01", "2025-9-6", "06/09/2025", "2025-09-06T25:00"]) {
    assert.equal(formatDate(bad), bad);
    assert.equal(utcDateParts(bad), null, bad);
  }
  assert.deepEqual(utcDateParts("2024-02-29"), { year: 2024, month: 2, day: 29 });
});

test("date ranges", () => {
  assert.equal(formatDateRange("2025-09-05", "2025-09-07"), "5–7 Sep");
  assert.equal(formatDateRange("2025-09-05", "2025-09-07", { year: true }), "5–7 Sep 2025");
  assert.equal(formatDateRange("2025-08-30", "2025-09-01"), "30 Aug – 1 Sep");
  assert.equal(formatDateRange("2025-08-30", "2025-09-01", { year: true }), "30 Aug – 1 Sep 2025");
  assert.equal(formatDateRange("2025-12-30", "2026-01-01"), "30 Dec 2025 – 1 Jan 2026");
  assert.equal(formatDateRange("2025-09-07", "2025-09-07"), "7 Sep");
  assert.equal(formatDateRange("2025-09-07", "2025-09-07T18:00:00Z", { year: true }), "7 Sep 2025");
  assert.equal(formatDateRange("bad", "2025-09-07"), "bad – 7 Sep");
  inEachZone((zone) => assert.equal(formatDateRange("2025-09-05", "2025-09-07"), "5–7 Sep", zone));
});

test("seconds", () => {
  assert.equal(seconds(0.37), "0.37 s");
  assert.equal(seconds(0.374, 1), "0.4 s");
  assert.equal(seconds(1.05, 2), "1.05 s");
  assert.equal(seconds(-0.73), "-0.73 s");
  assert.equal(seconds(-0.001), "0.00 s");
  assert.equal(seconds(-0.004, 2), "0.00 s");
  assert.equal(seconds(3, 0), "3 s");
  assert.equal(seconds(null), "–");
  assert.equal(seconds(undefined), "–");
  assert.equal(seconds(Number.NaN), "–");
  assert.equal(seconds(Number.POSITIVE_INFINITY), "–");
});

test("lap times", () => {
  assert.equal(lapTime(81.345), "1:21.345");
  assert.equal(lapTime(61), "1:01.000");
  assert.equal(lapTime(60), "1:00.000");
  assert.equal(lapTime(59.9996), "1:00.000");
  assert.equal(lapTime(59.123), "59.123");
  assert.equal(lapTime(5.2), "5.200");
  assert.equal(lapTime(0), "0.000");
  assert.equal(lapTime(125.0004), "2:05.000");
  assert.equal(lapTime(-1), "–");
  assert.equal(lapTime(null), "–");
  assert.equal(lapTime(Number.NaN), "–");
});

test("session names, codes and order", () => {
  assert.deepEqual(
    SESSION_ORDER.map((c) => [c, sessionName(c)]),
    [["SQ", "Sprint Qualifying"], ["SS", "Sprint Shootout"], ["S", "Sprint"], ["Q", "Qualifying"], ["R", "Race"]],
  );
  assert.equal(sessionName("FP1"), "FP1");
  assert.equal(sessionName(""), "");
  assert.ok(isSessionCode("SQ"));
  assert.ok(!isSessionCode("q"));
  assert.ok(!isSessionCode(5));
  assert.deepEqual(["R", "FP1", "Q", "S", "SQ"].sort(compareSessions), ["SQ", "S", "Q", "R", "FP1"]);
});

test("weekend order follows the season's format", () => {
  // The processed sessions' dates: 2022 round 4 ran Q, S, R; 2023 round 4 Q, SS, S, R;
  // 2024 round 5 SQ, S, Q, R.
  const sorted = (codes: string[], year?: number) => [...codes].sort((a, b) => compareSessions(a, b, year));
  assert.deepEqual(sorted(["R", "S", "Q"], 2022), ["Q", "S", "R"]);
  assert.deepEqual(sorted(["R", "S", "SS", "Q"], 2023), ["Q", "SS", "S", "R"]);
  assert.deepEqual(sorted(["R", "Q", "S", "SQ"], 2024), ["SQ", "S", "Q", "R"]);
  assert.deepEqual(sorted(["R", "Q", "S", "SQ"], 2026), ["SQ", "S", "Q", "R"]);
  assert.deepEqual(weekendOrder(), SESSION_ORDER);
  assert.deepEqual([...weekendOrder(2023)].sort(), [...SESSION_ORDER].sort());
});

test("short event names", () => {
  assert.equal(eventShort("Azerbaijan Grand Prix"), "Azerbaijan GP");
  assert.equal(eventShort("Emilia Romagna Grand Prix"), "Emilia Romagna GP");
  assert.equal(eventShort("Pre-Season Testing"), "Pre-Season Testing");
  assert.equal(eventShort("Grand Prix of Nowhere"), "Grand Prix of Nowhere");
});
