"use client";

/**
 * The analysis server's health, shared by every component that shows the API's state.
 *
 * The API answers /api/health about 3 s after it starts, so a page opened together with the
 * server would call it down too soon: the check retries after 1, 2 and 4 s when the server can't
 * be reached (or answers 5xx) before it reports "down". Any other error is final at once.
 *
 * `recheckHealth()` (the Try again buttons) checks again with the same retries, since the usual
 * reason to press it is having just started the server. A tool or catalog call that can't reach
 * the server rechecks too (`noteUnreachable`), so the page's banner follows what the page saw.
 */

import { API_URL } from "../env.ts";
import { ApiError, apiUrl, getJSON, type RequestOptions } from "./client.ts";
import { resources, useResource, type ResourceState, type ResourceStore } from "./resource.ts";
import type { HealthResponse } from "./types.ts";

export const HEALTH_PATH = "/api/health";

/** The waits before the second, third and fourth attempts. */
export const HEALTH_RETRY_MS: readonly number[] = [1000, 2000, 4000];

export interface HealthOptions extends RequestOptions {
  retryMs?: readonly number[];
  sleep?: (ms: number, signal: AbortSignal | undefined) => Promise<void>;
}

/** Waits `ms`, or rejects with an "aborted" ApiError as soon as the signal fires. */
export function sleep(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    const aborted = () => {
      clearTimeout(timer);
      reject(new ApiError(0, "aborted", "The request was cancelled."));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", aborted);
      resolve();
    }, ms);
    if (signal?.aborted) aborted();
    else signal?.addEventListener("abort", aborted, { once: true });
  });
}

function isHealth(value: unknown): value is HealthResponse {
  const v = value as Partial<HealthResponse> | null;
  return (
    typeof v === "object" &&
    v !== null &&
    (v.status === "ok" || v.status === "degraded") &&
    typeof v.data === "object" &&
    v.data !== null &&
    typeof v.chat === "object" &&
    v.chat !== null &&
    Array.isArray(v.problems)
  );
}

function worthRetrying(error: ApiError): boolean {
  return error.code === "unreachable" || error.status >= 500;
}

/** GET /api/health, retrying while the server may still be starting. */
export async function loadHealth(signal?: AbortSignal, opts: HealthOptions = {}): Promise<HealthResponse> {
  const { retryMs = HEALTH_RETRY_MS, sleep: wait = sleep, ...init } = opts;
  for (let attempt = 0; ; attempt++) {
    try {
      const body = await getJSON<unknown>(HEALTH_PATH, {}, { ...init, signal });
      if (!isHealth(body)) throw new ApiError(200, "bad_payload", "The health check's answer couldn't be read.");
      return body;
    } catch (error) {
      if (!(error instanceof ApiError) || !worthRetrying(error) || attempt >= retryMs.length) throw error;
      await wait(retryMs[attempt], signal);
    }
  }
}

/** The store key of the page's health check. */
export const HEALTH_KEY = apiUrl(HEALTH_PATH, {}, API_URL);

const load = (signal: AbortSignal) => loadHealth(signal);

/** The server's health, checked once per page visit and shared by every caller. */
export function useHealth(): ResourceState<HealthResponse> {
  return useResource(HEALTH_KEY, load);
}

/** Checks the server's health again, unless a check is already running. */
export function recheckHealth(store: ResourceStore = resources): void {
  store.reload(HEALTH_KEY, load);
}

/**
 * A call couldn't reach the server: if the health check last said it was up, check again, so
 * the banner shows the server is down. Nothing happens while a check runs or after it failed.
 */
export function noteUnreachable(store: ResourceStore = resources): void {
  if (store.get(HEALTH_KEY)?.status === "ok") recheckHealth(store);
}

/** `load`, rechecking health (`noteUnreachable`) when it fails because the server can't be reached. */
export async function recheckOnUnreachable<T>(load: Promise<T>, store: ResourceStore = resources): Promise<T> {
  try {
    return await load;
  } catch (error) {
    if (error instanceof ApiError && error.code === "unreachable") noteUnreachable(store);
    throw error;
  }
}

export type ApiStatus = "checking" | "up" | "degraded" | "down";

/** The four states the page shows: checking (or rechecking), up, degraded (some analyses can't
 *  run; `problems` says which) and down (unreachable, or the check failed in any other way). */
export function apiStatus(health: ResourceState<HealthResponse>): ApiStatus {
  switch (health.status) {
    case "ok":
      return health.data.status === "ok" ? "up" : "degraded";
    case "error":
      return "down";
    default:
      return "checking";
  }
}
