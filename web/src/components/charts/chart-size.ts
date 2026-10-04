// The room each chart takes before it is drawn, so it arrives without moving the page. A chart's
// height follows its data (a find-mistakes list grows a row per mistake, a race summary a row
// per position), so the reserved height does too: a small model per chart, its constants
// measured in /dev/charts and on real API payloads, with the counts read from the data when the
// data is already here (a chat answer, the dispatcher loading a chart's code). Without data (an
// explorer waiting for the API) it reserves for the typical real payload: ten mistakes with the
// track map, a full field of twenty, a corner with its replay.
//
// Measured in chart cards, frame widths 302 to 1070 px: the real payloads (find-mistakes at 1,
// 5 and 10 rows, a corner, a race, a season of styles, two laps) and the twelve fixtures each
// draw within about 10% of what is reserved for them, the fixtures' empty and filtered lists
// aside (their notes and rows wrap by text length, which no count predicts).

import type { ChartName } from "./chart-names.ts";

/**
 * The chart frame widths (px) at which each chart's layout changes: one reserved height below
 * the first, then one from each. They match the `@min-[…]` steps of `CHART_SIZE` in
 * chart-names.ts, which a test keeps in step.
 */
export const SIZE_BREAKS: Record<ChartName, readonly number[]> = {
  // WIDE_PX in charts/find-mistakes.ts: the map moves beside the list.
  "find-mistakes": [640],
  // Its max-width 560 query: the corner map and the replay sit side by side.
  "explain-corner": [561],
  // Its max-width 480 query.
  "compare-styles": [481],
  // The header and legend fit on fewer lines from 420; from 481 the rows are 15 px, not 13
  // (NARROW in charts/race-summary.ts is the content width at a 480 px frame).
  "race-summary": [420, 481],
  // Its max-width 480 query.
  "compare-laps": [481],
};

type Fields = Record<string, unknown>;

function fields(value: unknown): Fields | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Fields) : null;
}

// find-mistakes: the header, list head and footer, then the track map (when there are mistakes
// and a track), then about 90 px a row. Below 640 px they stack; from 640 the map sits beside
// the list, so the taller of the two counts. With no mistakes it shows only its note.
function findMistakes(data: Fields | null): number[] {
  const rows = data && Array.isArray(data.mistakes) ? data.mistakes.length : 10;
  if (rows === 0) return [260, 190];
  const map = data ? fields(data.track) !== null : true;
  return [190 + (map ? 300 : 0) + 92 * rows, 140 + Math.max(map ? 260 : 0, 18 + 90 * rows)];
}

// explain-corner: the replay is drawn under the corner map until the frame is 561 px wide, then
// beside it.
function explainCorner(data: Fields | null): number[] {
  const replay = data ? fields(data.replay) !== null : true;
  return [replay ? 1310 : 1100, 940];
}

// race-summary: the position chart has a row per position (the field, or the lowest position
// anyone held), as charts/race-summary.ts sizes it: 13 px rows in a narrow frame and 15 px from
// 481, taller for a small field (240 px shared, at most 28 px a row), 40 px of axes. The header,
// legend, readout and notes above and below it take less room as the frame widens.
function raceSummary(data: Fields | null): number[] {
  let rows = 20;
  if (data && Array.isArray(data.drivers)) {
    rows = Math.max(1, data.drivers.length);
    for (const driver of data.drivers) {
      const positions = fields(driver)?.positions;
      if (!Array.isArray(positions)) continue;
      for (const p of positions) if (typeof p === "number" && Number.isFinite(p) && p > rows) rows = p;
    }
  }
  const plot = (least: number) => 40 + rows * Math.max(least, Math.min(28, Math.floor(240 / rows)));
  return [275 + plot(13), 230 + plot(13), 195 + plot(15)];
}

/**
 * The height (px, the chart root's padding included) to keep for a chart: one for each of its
 * layouts, below the first of its `SIZE_BREAKS` and then from each.
 */
export function reservedHeights(name: ChartName, data?: unknown): number[] {
  const known = data === undefined ? null : fields(data);
  switch (name) {
    case "find-mistakes":
      return findMistakes(known);
    case "explain-corner":
      return explainCorner(known);
    case "race-summary":
      return raceSummary(known);
    // Neither grows with its data in a way a count would tell. Below 481 px compare-styles grows as
    // its text wraps: real seasons draw about 1,360-1,570 px in 302-372 px frames and 1,280-1,380
    // at 412, so 1,400 sits between them.
    case "compare-styles":
      return [1400, 1120];
    case "compare-laps":
      return [575, 520];
  }
}

/**
 * The reserved heights as the custom properties `CHART_SIZE`'s classes read (`--chart-h0`,
 * `--chart-h1`, …), for the `style` of the element that carries those classes.
 */
export function chartSizeStyle(name: ChartName, data?: unknown): Record<string, string> {
  return Object.fromEntries(reservedHeights(name, data).map((h, i) => [`--chart-h${i}`, `${Math.round(h)}px`]));
}
