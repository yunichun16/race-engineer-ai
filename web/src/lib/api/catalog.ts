"use client";

/**
 * The three catalog routes the pickers list from (read-only site-support routes, not tools):
 * every processed session, one session's drivers, and one season's driving-style pairs. Each
 * answer is checked for its outline (a wrong server or a proxy page is "bad_payload"), and the
 * hooks share one request and one answer per URL through the resource store.
 */

import { ApiError, apiUrl, getJSON, type ApiQuery, type RequestOptions } from "./client.ts";
import { recheckOnUnreachable } from "./health.ts";
import { useResource, type ResourceState } from "./resource.ts";
import type { SessionCatalog, SessionCode, SessionDrivers, StyleCatalog } from "./types.ts";

export const CATALOG_PATHS = {
  sessions: "/api/catalog/sessions",
  drivers: "/api/catalog/drivers",
  styles: "/api/catalog/styles",
} as const;

/** A session as the tools name it. */
export interface SessionSelection {
  event: string;
  year: number;
  session: SessionCode;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function isSessionCatalog(value: unknown): value is SessionCatalog {
  const v = record(value);
  return !!v && !!record(v.coverage) && typeof v.results_current === "boolean" && Array.isArray(v.seasons);
}

function isSessionDrivers(value: unknown): value is SessionDrivers {
  const v = record(value);
  return !!v && !!record(v.session) && Array.isArray(v.drivers);
}

function isStyleCatalog(value: unknown): value is StyleCatalog {
  const v = record(value);
  return !!v && Array.isArray(v.years) && typeof v.year === "number" && Array.isArray(v.drivers) && Array.isArray(v.pairs);
}

async function fetchChecked<T>(
  path: string,
  query: ApiQuery,
  guard: (value: unknown) => value is T,
  init: RequestOptions,
): Promise<T> {
  const body = await getJSON<unknown>(path, query, init);
  if (!guard(body)) throw new ApiError(200, "bad_payload", "The server's list couldn't be read.");
  return body;
}

function driversQuery(sel: SessionSelection): ApiQuery {
  return { event: sel.event, year: sel.year, session: sel.session };
}

// A null year asks for the newest season with style data.
function stylesQuery(year: number | null): ApiQuery {
  return { year: year ?? undefined };
}

/** GET /api/catalog/sessions. */
export function getSessionCatalog(init: RequestOptions = {}): Promise<SessionCatalog> {
  return fetchChecked(CATALOG_PATHS.sessions, {}, isSessionCatalog, init);
}

/** GET /api/catalog/drivers?event&year&session. */
export function getSessionDrivers(sel: SessionSelection, init: RequestOptions = {}): Promise<SessionDrivers> {
  return fetchChecked(CATALOG_PATHS.drivers, driversQuery(sel), isSessionDrivers, init);
}

/** GET /api/catalog/styles?year (null: the newest season with style data). */
export function getStyleCatalog(year: number | null, init: RequestOptions = {}): Promise<StyleCatalog> {
  return fetchChecked(CATALOG_PATHS.styles, stylesQuery(year), isStyleCatalog, init);
}

const SESSIONS_KEY = apiUrl(CATALOG_PATHS.sessions);

/** Every processed session, by season and weekend, newest first. */
export function useSessionCatalog(): ResourceState<SessionCatalog> {
  return useResource(SESSIONS_KEY, (signal) => recheckOnUnreachable(getSessionCatalog({ signal })));
}

/** The drivers of one session; idle while `sel` is null. */
export function useSessionDrivers(sel: SessionSelection | null): ResourceState<SessionDrivers> {
  const key = sel ? apiUrl(CATALOG_PATHS.drivers, driversQuery(sel)) : null;
  return useResource(key, (signal) => recheckOnUnreachable(getSessionDrivers(sel as SessionSelection, { signal })));
}

/** One season's style drivers and teammate pairs; null asks for the newest season. */
export function useStyleCatalog(year: number | null): ResourceState<StyleCatalog> {
  return useResource(apiUrl(CATALOG_PATHS.styles, stylesQuery(year)), (signal) =>
    recheckOnUnreachable(getStyleCatalog(year, { signal })),
  );
}
