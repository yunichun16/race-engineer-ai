/**
 * The API's JSON, as the browser receives it.
 *
 * - The envelope and health types mirror engine/src/race_engineer/api/models.py.
 * - The catalog types mirror api/routes/catalog.py (GET /api/catalog/{sessions,drivers,styles}).
 * - `ToolParams` mirrors each tool's Params model (tools/params.py, find_session.py,
 *   race_summary.py, styles.py): the REST query names, in field order. Unknown keys are a 422
 *   (`extra="forbid"`), so nothing may add a key a tool doesn't declare.
 * - The chart payloads are the chart modules' own types, imported type-only, so the site, the MCP
 *   bundles and the engine's TypedDicts describe one shape.
 */

import type { Comparison } from "../../charts/compare-laps.ts";
import type { StyleChart } from "../../charts/compare-styles.ts";
import type { CornerChart } from "../../charts/explain-corner.ts";
import type { MistakeList } from "../../charts/find-mistakes.ts";
import type { RaceSummary } from "../../charts/race-summary.ts";
import type { SessionCode } from "../format.ts";

export type { SessionCode };
export type { Comparison, CornerChart, MistakeList, RaceSummary, StyleChart };

// --- The envelope -------------------------------------------------------------------------------

/** Every error the API answers with, except inside a chat's event stream. */
export interface ErrorBody {
  error: {
    code: string;
    message: string;
    tool?: string | null;
    // The limits' refusals (M7: 429 rate_limited, 503 daily_cap, chat_paused, chat_unavailable)
    // carry when to try again in the body, since a page can't read Retry-After over CORS unless
    // the server exposes it; `limit` says which per-visitor limit it was ("hour" or "day").
    retry_after_s?: number | null;
    limit?: string;
  };
}

/** The chart bundle that draws a tool's data (web/src/mcp-app/<bundle>.html for MCP hosts). */
export interface ChartRef {
  bundle: string;
  resource_uri: string;
}

/** GET /api/tools/<name>: the summary the model would read, and the chart data. */
export interface ToolResponse<T> {
  tool: string;
  summary: string;
  data: T | null;
  chart: ChartRef | null;
}

// --- Health -------------------------------------------------------------------------------------

/** GET /api/health. Always 200; `degraded` with the reasons in `problems`. */
export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  data: {
    sessions: number; // processed sessions
    latest: string | null; // e.g. "2026 Azerbaijan Grand Prix Race"
    results_run: string | null; // the current M4 scoring run
    results_current: boolean; // mistakes.parquet comes from that run
    mistakes: number | null; // rows in mistakes.parquet
    style: boolean; // the driving-style tables are built and current
    circuits: boolean; // circuits.json (track map rotations) is built
  };
  // "fake" is the scripted chat (RACE_ENGINEER_CHAT=fake); any other value is shown as given.
  chat: { mode: "anthropic" | "fake" | "off" | (string & {}); model: string; base_url_host: string };
  mcp: { mounted: boolean; path: string; tools: number };
  warm_s: Record<string, number>; // seconds per warm-up step at startup
  problems: string[];
}

// --- The catalog (site-support routes, not tools) -------------------------------------------------

export interface CatalogSession {
  key: string; // e.g. "2025_16_Q"
  code: SessionCode;
  name: string; // FastF1's session name, e.g. "Qualifying"
  date: string; // UTC date, YYYY-MM-DD
  scored: boolean; // the M4 scores cover it (find_mistakes, explain_corner's verdict)
}

export interface CatalogWeekend {
  round: number;
  event: string; // unique within a season, so event + year + session names a session exactly
  location: string;
  date: string; // the date of its last session
  sessions: CatalogSession[]; // in the order they ran (by date: 2023 sprint weekends are Q, SS, S, R)
}

export interface CatalogSeason {
  year: number;
  style: boolean; // the season is in the driving-style tables (compare_driving_styles)
  weekends: CatalogWeekend[]; // newest first
}

/** GET /api/catalog/sessions: every processed session, by season and weekend, newest first. */
export interface SessionCatalog {
  coverage: { first: string; last: string; sessions: number }; // "2022 Bahrain Grand Prix (Q)"
  // False when the M4 results are missing or from another scoring run: then no session is
  // `scored` and find_mistakes answers 503.
  results_current: boolean;
  seasons: CatalogSeason[]; // newest first
}

/** GET /api/catalog/drivers?event&year&session: one session and its drivers. */
export interface SessionDrivers {
  session: {
    key: string;
    year: number;
    round: number;
    event: string;
    location: string;
    session_code: SessionCode;
    session_name: string;
    date: string;
  };
  drivers: { driver: string; team: string; laps: number }[]; // sorted by team, then code
}

export interface StylePair {
  team: string;
  a: string; // a < b
  b: string;
  events: number; // events they shared the car
  // Whether the style difference held between the season's odd and even rounds, for a then b.
  // The test runs once per pair, so the two values are always equal. False also means it
  // couldn't be tested (fewer than 2 events in either half): in today's data every untested
  // pair has fewer than 4 events, but that isn't guaranteed in general.
  consistent: [boolean, boolean];
}

/** GET /api/catalog/styles?year: one season of the driving-style tables (default the newest). */
export interface StyleCatalog {
  years: number[]; // newest first
  year: number;
  drivers: { driver: string; team: string; events: number }[]; // a mid-season move gives two rows
  pairs: StylePair[]; // sorted by team, then a, then b
}

// --- find_session's structured answer (it has no chart) -------------------------------------------

export interface SessionRef {
  key: string; // e.g. "2025_16_Q"
  year: number;
  round: number;
  session_code: SessionCode;
  session_name: string;
  event: string;
  location: string;
  date: string; // YYYY-MM-DD
  scored: boolean; // the M4 scores cover it
  style: boolean; // its season is in the driving-style tables
}

export interface FindSessionData {
  match: SessionRef | null;
  candidates: SessionRef[]; // best match first, when the fields name no single session
  weekend: SessionRef[]; // every processed session of the matched weekend
  coverage: { first: string; last: string; newest_season: number; sessions: number };
}

// --- The tools ------------------------------------------------------------------------------------

/**
 * Each tool's REST query parameters, in its Params model's field order. Optional fields may be
 * left out (`buildQuery` drops undefined, null and ""), and the site sends session codes, though
 * the tools also read words ("qualifying").
 */
export interface ToolParams {
  // Fields, not free text: event is only the event's name, location, country or session key.
  // session also reads words here, and scopes round (default: the race) and recent.
  find_session: { event?: string; year?: number; session?: string; round?: number; recent?: number };
  list_sessions: { year?: number };
  get_race_summary: { event: string; year?: number; session?: "R" | "S"; driver?: string };
  find_mistakes: { event: string; driver?: string; year?: number; session?: SessionCode; limit?: number }; // limit 1–50
  explain_corner: {
    event: string;
    driver: string;
    lap: number;
    corner: string | number; // 7, "9a" or "T9a"
    year?: number;
    session?: SessionCode;
  };
  compare_laps: {
    event: string;
    driver_a: string;
    driver_b: string;
    year?: number;
    session?: SessionCode;
    lap_a?: number;
    lap_b?: number;
  };
  compare_driving_styles: { driver_a: string; driver_b: string; year?: number };
}

export type ToolName = keyof ToolParams;

/** The `data` each tool answers with: the chart payload, find_session's match, or nothing. */
export interface ToolData {
  find_session: FindSessionData;
  list_sessions: null; // text only
  get_race_summary: RaceSummary;
  find_mistakes: MistakeList;
  explain_corner: CornerChart;
  compare_laps: Comparison;
  compare_driving_styles: StyleChart;
}

/** What `callTool` and `useTool` give: the tool's summary (the chart's full text version) and data. */
export interface ToolResult<N extends ToolName> {
  summary: string;
  data: ToolData[N];
}

// --- The chat's limits (M7) ------------------------------------------------------------------------

/**
 * GET /api/chat/status: whether the chat would take a question from this visitor now, so /chat
 * can offer saved answers before anyone types. It reads the limits store but never counts a
 * question (api/routes/chat.py; plan 13.3).
 */
export interface ChatStatus {
  available: boolean;
  mode: "anthropic" | "fake" | "off";
  // `daily_cap` means a new question couldn't be admitted now (today's spend and reservations,
  // or the global backstop), so a tiny cap shows it before any question.
  reason: null | "off" | "paused" | "daily_cap" | "rate_limited" | "unavailable";
  retry_after_s: number | null;
  quota: { hour_left: number; day_left: number; per_hour: number; per_day: number } | null; // null: limits off
  visitor: string | null; // the caller's own daily 6-hex tag, for the deploy check; never shown
  saved: { id: string; recorded_at: string }[]; // the API's saved answers
}
