/**
 * The health check: the 1/2/4 s retries while the server may still be starting, which errors
 * end it at once, the abortable wait, the four page states, and the recheck after a call that
 * couldn't reach the server.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "./client.ts";
import {
  apiStatus,
  HEALTH_KEY,
  HEALTH_RETRY_MS,
  loadHealth,
  noteUnreachable,
  recheckOnUnreachable,
  sleep,
} from "./health.ts";
import type { Entry, ResourceStore } from "./resource.ts";
import { errorBody, fakeFetch, json } from "./test-support.ts";
import type { HealthResponse } from "./types.ts";

const BASE = "http://api.test";

const HEALTH: HealthResponse = {
  status: "ok",
  version: "0.1.0",
  data: {
    sessions: 263,
    latest: "2026 Azerbaijan Grand Prix Race",
    results_run: "run",
    results_current: true,
    mistakes: 18762,
    style: true,
    circuits: true,
  },
  chat: { mode: "fake", model: "scripted", base_url_host: "scripted.invalid" },
  mcp: { mounted: true, path: "/mcp", tools: 8 },
  warm_s: { catalog: 0.3 },
  problems: [],
};

/** A fetch that fails `failures` times (with `fail`), then answers with the health body. */
function flaky(failures: number, fail: () => Response = () => {
  throw new TypeError("Failed to fetch");
}) {
  let n = 0;
  return fakeFetch(() => {
    n += 1;
    return n <= failures ? fail() : json(HEALTH);
  });
}

function recordingSleep() {
  const waits: number[] = [];
  const wait = async (ms: number) => {
    waits.push(ms);
  };
  return { waits, wait };
}

test("the retries wait 1, 2 and 4 seconds", () => {
  assert.deepEqual(HEALTH_RETRY_MS, [1000, 2000, 4000]);
});

test("a server that answers at once gives its health, with no wait", async () => {
  const fetch = flaky(0);
  const { waits, wait } = recordingSleep();
  assert.deepEqual(await loadHealth(undefined, { fetch, baseUrl: BASE, sleep: wait }), HEALTH);
  assert.equal(fetch.calls[0].url, `${BASE}/api/health`);
  assert.deepEqual(waits, []);
});

test("a server still starting is retried after 1, 2 and 4 s until it answers", async () => {
  const fetch = flaky(3);
  const { waits, wait } = recordingSleep();
  assert.deepEqual(await loadHealth(undefined, { fetch, baseUrl: BASE, sleep: wait }), HEALTH);
  assert.equal(fetch.calls.length, 4);
  assert.deepEqual(waits, [1000, 2000, 4000]);
});

test("after four failed attempts it reports the server unreachable", async () => {
  const fetch = flaky(99);
  const { waits, wait } = recordingSleep();
  await assert.rejects(loadHealth(undefined, { fetch, baseUrl: BASE, sleep: wait }), { code: "unreachable", status: 0 });
  assert.equal(fetch.calls.length, 4);
  assert.deepEqual(waits, [1000, 2000, 4000]);
});

test("a 5xx is retried; any other error is final at once", async () => {
  const proxy = flaky(2, () => new Response("busy", { status: 503, statusText: "Service Unavailable" }));
  const { waits, wait } = recordingSleep();
  assert.deepEqual(await loadHealth(undefined, { fetch: proxy, baseUrl: BASE, sleep: wait }), HEALTH);
  assert.deepEqual(waits, [1000, 2000]);

  const wrongServer = fakeFetch(() => json(errorBody("not_found", "Not Found"), 404));
  await assert.rejects(loadHealth(undefined, { fetch: wrongServer, baseUrl: BASE, sleep: recordingSleep().wait }), {
    code: "not_found",
  });
  assert.equal(wrongServer.calls.length, 1);

  for (const body of [{ status: "fine" }, { ...HEALTH, problems: undefined }, ["ok"]]) {
    const odd = fakeFetch(() => json(body));
    await assert.rejects(loadHealth(undefined, { fetch: odd, baseUrl: BASE, sleep: recordingSleep().wait }), {
      code: "bad_payload",
    });
    assert.equal(odd.calls.length, 1);
  }
});

test("an abort during the wait ends the check at once, with no further attempt", async () => {
  const fetch = flaky(99);
  const controller = new AbortController();
  const pending = loadHealth(controller.signal, { fetch, baseUrl: BASE, retryMs: [60_000] });
  setTimeout(() => controller.abort(), 5);
  const started = Date.now();
  await assert.rejects(pending, { code: "aborted" });
  assert.ok(Date.now() - started < 1000);
  assert.equal(fetch.calls.length, 1);
});

test("sleep waits, and rejects with 'aborted' when the signal fires or already has", async () => {
  const started = Date.now();
  await sleep(20, undefined);
  assert.ok(Date.now() - started >= 15);
  const done = new AbortController();
  done.abort();
  await assert.rejects(sleep(60_000, done.signal), { name: "ApiError", code: "aborted" });
});

test("apiStatus: checking, up, degraded and down", () => {
  assert.equal(apiStatus({ status: "idle" }), "checking");
  assert.equal(apiStatus({ status: "loading" }), "checking");
  assert.equal(apiStatus({ status: "loading", stale: HEALTH }), "checking");
  assert.equal(apiStatus({ status: "ok", data: HEALTH }), "up");
  const degraded: HealthResponse = { ...HEALTH, status: "degraded", problems: ["style tables not built (race-engineer-infer style)"] };
  assert.equal(apiStatus({ status: "ok", data: degraded }), "degraded");
  const error = new ApiError(0, "unreachable", "Can't reach the analysis server.");
  assert.equal(apiStatus({ status: "error", error, retry: () => {} }), "down");
  assert.equal(apiStatus({ status: "error", error: new ApiError(404, "not_found", "Not Found"), retry: () => {} }), "down");
});

/** A store that records reloads, with the health entry the test sets. */
function stubStore(health: Entry | undefined) {
  const reloads: string[] = [];
  const store: ResourceStore = {
    get: <T>(key: string) => (key === HEALTH_KEY ? (health as Entry<T> | undefined) : undefined),
    subscribe: () => () => {},
    ensure: () => {},
    reload: (key, loader) => {
      assert.equal(typeof loader, "function", "a recheck brings its loader");
      reloads.push(key);
    },
    keys: () => [],
  };
  return { store, reloads };
}

test("a call that can't reach the server rechecks health, but only if health said it was up", async () => {
  const up = stubStore({ status: "ok", data: HEALTH });
  noteUnreachable(up.store);
  assert.deepEqual(up.reloads, [HEALTH_KEY]);

  for (const entry of [
    { status: "loading", controller: new AbortController() } as const,
    { status: "error", error: new ApiError(0, "unreachable", "x") } as const,
    undefined,
  ]) {
    const other = stubStore(entry);
    noteUnreachable(other.store);
    assert.deepEqual(other.reloads, []);
  }

  const wrapped = stubStore({ status: "ok", data: HEALTH });
  await assert.rejects(
    recheckOnUnreachable(Promise.reject(new ApiError(0, "unreachable", "x")), wrapped.store),
    { code: "unreachable" },
  );
  await assert.rejects(
    recheckOnUnreachable(Promise.reject(new ApiError(422, "invalid_input", "x")), wrapped.store),
    { code: "invalid_input" },
  );
  assert.equal(await recheckOnUnreachable(Promise.resolve(7), wrapped.store), 7);
  assert.deepEqual(wrapped.reloads, [HEALTH_KEY], "only the unreachable call rechecked");
});
