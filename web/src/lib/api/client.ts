/**
 * The browser's client for the FastAPI server, called directly over CORS (no Next.js proxy).
 *
 * Every failure becomes an `ApiError` with a code the pages can switch on:
 * - a non-2xx answer with the API's ErrorBody: its own code ("invalid_input", "results_unavailable",
 *   "internal_error", ...), message and tool;
 * - any other non-2xx answer (a proxy's HTML page, say): "http_<status>";
 * - fetch failing (server stopped, CORS refused, offline): "unreachable", or "aborted" when the
 *   caller's signal fired;
 * - a 2xx answer that isn't the JSON we expect (invalid JSON, a chart payload its chart's guard
 *   rejects): "bad_payload".
 *
 * Requests carry no custom header (the API's CORS rules allow only `content-type`) and use the
 * default cache mode, so the browser keeps answers for their `Cache-Control: private, max-age=60`
 * and revalidates them with `If-None-Match` itself; a 304 reaches this code as the cached 200.
 */

import { isComparison } from "../../charts/compare-laps.ts";
import { isStyleChart } from "../../charts/compare-styles.ts";
import { isCornerChart } from "../../charts/explain-corner.ts";
import { isMistakeList } from "../../charts/find-mistakes.ts";
import { isRaceSummary } from "../../charts/race-summary.ts";
import { API_URL } from "../env.ts";
import { buildQuery as canonicalQuery, withQuery } from "../url.ts";
import type { ErrorBody, FindSessionData, ToolData, ToolName, ToolParams, ToolResult } from "./types.ts";

/** What a limit's refusal adds to an error (M7): when to try again, and which limit it was. */
export interface RetryDetail {
  retryAfterS?: number | null;
  limit?: string;
}

export class ApiError extends Error {
  readonly status: number; // 0 for network errors
  readonly code: string; // the API's error.code, or "unreachable" | "aborted" | "bad_payload" | "http_<status>"
  readonly tool: string | undefined;
  /** Seconds until the request may succeed (the body's retry_after_s, else Retry-After); null
   *  when the server didn't say. */
  readonly retryAfterS: number | null;
  /** The per-visitor limit that refused it ("hour" or "day"), when one did. */
  readonly limit: string | undefined;
  constructor(status: number, code: string, message: string, tool?: string, detail: RetryDetail = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.tool = tool;
    this.retryAfterS = detail.retryAfterS ?? null;
    this.limit = detail.limit;
  }
}

export type ApiQuery = Readonly<Record<string, string | number | null | undefined>>;

export interface RequestOptions {
  signal?: AbortSignal;
  /** A stand-in for the global fetch (tests). It is called as a plain function. */
  fetch?: typeof fetch;
  /** Another server than NEXT_PUBLIC_API_URL, without a trailing slash. */
  baseUrl?: string;
}

/** The messages for the client's own codes; describe() words them for the page. */
const UNREACHABLE = "Can't reach the analysis server.";
const ABORTED = "The request was cancelled.";
const BAD_PAYLOAD = "The server's answer couldn't be read.";

/**
 * The query string for an API call, with no leading "?": undefined, null and "" are dropped and
 * the keys sorted, so equal parameters always give the same URL (and the same browser cache
 * entry). It never adds a key. The same canonical form as the site's own URLs (lib/url.ts).
 */
export function buildQuery(params: ApiQuery): string {
  return canonicalQuery(params);
}

/** The full URL of an API path ("/api/health") with its query. */
export function apiUrl(path: string, query: ApiQuery = {}, baseUrl: string = API_URL): string {
  return withQuery(`${baseUrl}${path}`, query);
}

/**
 * Any thrown value as an ApiError: an ApiError as it is, an object with its fields (the chat's
 * pre-stream errors) rebuilt, an abort as "aborted", and anything else as "client_error".
 */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (isAbort(error)) return new ApiError(0, "aborted", ABORTED);
  if (isRecord(error) && typeof error.status === "number" && typeof error.code === "string" && typeof error.message === "string") {
    const tool = typeof error.tool === "string" ? error.tool : undefined;
    const retryAfterS = seconds(error.retryAfterS);
    const limit = typeof error.limit === "string" ? error.limit : undefined;
    return new ApiError(error.status, error.code, error.message, tool, { retryAfterS, limit });
  }
  const message = error instanceof Error ? error.message : String(error);
  return new ApiError(0, "client_error", message || "Something went wrong in this page.");
}

function isAbort(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A count of seconds (zero or more), or null for anything else. */
function seconds(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** The ErrorBody's fields, when `body` is one. */
function errorDetail(body: unknown): { code: string; message: string; tool?: string; retryAfterS: number | null; limit?: string } | null {
  const error = isRecord(body) ? (body as Partial<ErrorBody>).error : undefined;
  if (!isRecord(error) || typeof error.code !== "string" || typeof error.message !== "string") return null;
  return {
    code: error.code,
    message: error.message,
    tool: typeof error.tool === "string" ? error.tool : undefined,
    retryAfterS: seconds(error.retry_after_s),
    limit: typeof error.limit === "string" ? error.limit : undefined,
  };
}

/** Retry-After as seconds, when the server sent it as a number and the page may read it (CORS
 *  exposes it only when the API lists it). The date form isn't used by the API. */
function retryAfterHeader(response: Response): number | null {
  const value = response.headers.get("retry-after");
  return value !== null && /^\d+$/.test(value.trim()) ? Number(value.trim()) : null;
}

/**
 * The ApiError for a non-2xx response: the API's ErrorBody when it sent one, else "http_<status>"
 * with the status text. Exported for the chat client, whose pre-stream errors are the same.
 */
export async function errorFromResponse(response: Response, tool?: string): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = JSON.parse(await response.text());
  } catch {
    body = null; // not JSON, or the body couldn't be read: the status alone says it
  }
  const detail = errorDetail(body);
  const retryAfterS = detail?.retryAfterS ?? retryAfterHeader(response);
  if (detail) {
    return new ApiError(response.status, detail.code, detail.message, detail.tool ?? tool, { retryAfterS, limit: detail.limit });
  }
  const text = response.statusText || `HTTP ${response.status}`;
  return new ApiError(response.status, `http_${response.status}`, text, tool, { retryAfterS });
}

/** "unreachable", or "aborted" when the signal fired, for a fetch or body read that threw. */
export function networkError(error: unknown, signal: AbortSignal | undefined, tool?: string): ApiError {
  if (signal?.aborted || isAbort(error)) return new ApiError(0, "aborted", ABORTED, tool);
  return new ApiError(0, "unreachable", UNREACHABLE, tool);
}

/** GET `url` and parse its JSON body; any failure is thrown as an ApiError. */
async function request(url: string, init: RequestOptions, tool?: string): Promise<{ status: number; body: unknown }> {
  const { signal } = init;
  // Called as a plain function, never as init.fetch(...): a browser's fetch throws "Illegal
  // invocation" when `this` is some other object.
  const send = init.fetch ?? globalThis.fetch;
  let response: Response;
  try {
    response = await send(url, { signal });
  } catch (error) {
    throw networkError(error, signal, tool);
  }
  if (!response.ok) {
    const error = await errorFromResponse(response, tool);
    // An abort before or while the error body was read is still an abort.
    throw signal?.aborted ? networkError(null, signal, tool) : error;
  }
  let text: string;
  try {
    text = await response.text();
  } catch (error) {
    throw networkError(error, signal, tool); // the connection dropped mid-body, or an abort
  }
  try {
    return { status: response.status, body: JSON.parse(text) as unknown };
  } catch {
    throw new ApiError(response.status, "bad_payload", BAD_PAYLOAD, tool);
  }
}

/**
 * GET an API path ("/api/catalog/sessions") with its query and return the parsed JSON. The type
 * is the caller's claim: check the shape before trusting it (callTool does, with the guards).
 */
export async function getJSON<T>(path: string, query: ApiQuery = {}, init: RequestOptions = {}): Promise<T> {
  const { body } = await request(apiUrl(path, query, init.baseUrl), init);
  return body as T;
}

// --- The tools ------------------------------------------------------------------------------------

function isFindSessionData(value: unknown): value is FindSessionData {
  return (
    isRecord(value) &&
    (value.match === null || isRecord(value.match)) &&
    Array.isArray(value.candidates) &&
    Array.isArray(value.weekend) &&
    isRecord(value.coverage)
  );
}

/** Each tool's data check: the chart's own guard, the same one the chart runs before drawing. */
export const TOOL_GUARDS: { readonly [N in ToolName]: (value: unknown) => value is ToolData[N] } = {
  find_session: isFindSessionData,
  list_sessions: (value): value is null => value === null,
  get_race_summary: isRaceSummary,
  find_mistakes: isMistakeList,
  explain_corner: isCornerChart,
  compare_laps: isComparison,
  compare_driving_styles: isStyleChart,
};

/** The path of a tool's REST route. */
export function toolPath(name: ToolName): string {
  return `/api/tools/${name}`;
}

/**
 * Call one tool over REST: GET /api/tools/<name>?<params>. Resolves to its summary and its data,
 * checked by the tool's guard; a payload that fails the guard (or a body that isn't a
 * ToolResponse) is a "bad_payload" ApiError naming the tool.
 */
export async function callTool<N extends ToolName>(
  name: N,
  params: ToolParams[N],
  init: RequestOptions = {},
): Promise<ToolResult<N>> {
  const url = apiUrl(toolPath(name), params as ApiQuery, init.baseUrl);
  const { status, body } = await request(url, init, name);
  const guard = TOOL_GUARDS[name] as (value: unknown) => value is ToolData[N];
  if (!isRecord(body) || typeof body.summary !== "string" || !guard(body.data)) {
    throw new ApiError(status, "bad_payload", "The server's answer couldn't be drawn.", name);
  }
  return { summary: body.summary, data: body.data };
}
