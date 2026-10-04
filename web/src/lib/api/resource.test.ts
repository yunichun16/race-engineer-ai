/**
 * The resource store without React: shared in-flight loads, the LRU, errors and aborts never
 * cached, unwatched loads aborted (but not by StrictMode's unsubscribe-and-resubscribe), late
 * answers ignored, and the states the hook derives, stale data included.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "./client.ts";
import { createResourceStore, resourceState, type Entry, type Loader } from "./resource.ts";
import { deferred, settle } from "./test-support.ts";

/** A store whose unwatched-key checks run only when the test says so. */
function testStore(max = 64) {
  const queue: (() => void)[] = [];
  const store = createResourceStore({ max, defer: (fn) => queue.push(fn) });
  const flush = () => {
    while (queue.length > 0) queue.shift()?.();
  };
  return { store, flush };
}

/** A loader whose calls the test answers one by one. */
function controlled<T>() {
  const calls: { signal: AbortSignal; resolve: (value: T) => void; reject: (error: unknown) => void }[] = [];
  const loader: Loader<T> = (signal) => {
    const d = deferred<T>();
    calls.push({ signal, resolve: d.resolve, reject: d.reject });
    return d.promise;
  };
  return { loader, calls };
}

/** A loader that answers at once. */
function answering<T>(value: T) {
  let count = 0;
  const loader: Loader<T> = async () => {
    count += 1;
    return value;
  };
  return { loader, count: () => count };
}

function listener() {
  let calls = 0;
  const fn = () => {
    calls += 1;
  };
  return Object.assign(fn, { calls: () => calls });
}

test("two watchers of one key share one request and one answer", async () => {
  const { store } = testStore();
  const first = controlled<{ n: number }>();
  const second = controlled<{ n: number }>();
  const a = listener();
  const b = listener();
  store.subscribe("k", a);
  store.subscribe("k", b);
  store.ensure("k", first.loader);
  store.ensure("k", second.loader);
  assert.equal(first.calls.length, 1);
  assert.equal(second.calls.length, 0);
  assert.equal(store.get("k")?.status, "loading");

  const data = { n: 1 };
  first.calls[0].resolve(data);
  await settle();
  const entry = store.get<{ n: number }>("k");
  assert.deepEqual(entry, { status: "ok", data });
  assert.equal(entry?.status === "ok" && entry.data, data);
  assert.equal(store.get("k"), entry, "the snapshot keeps its identity until it changes");
  assert.equal(a.calls(), 2); // loading, then ok
  assert.equal(b.calls(), 2);
});

test("an answer is kept: a later ensure doesn't ask again, even after everyone left", async () => {
  const { store, flush } = testStore();
  const load = answering("lap");
  const unsubscribe = store.subscribe("k", listener());
  store.ensure("k", load.loader);
  await settle();
  unsubscribe();
  flush();
  store.ensure("k", load.loader);
  assert.equal(load.count(), 1);
  assert.deepEqual(store.get("k"), { status: "ok", data: "lap" });
});

test("past the limit, the least recently used unwatched answers go first", async () => {
  const { store } = testStore(3);
  for (const key of ["k1", "k2", "k3"]) store.ensure(key, answering(key).loader);
  await settle();
  store.ensure("k1", answering("again").loader); // used again: now the newest
  store.ensure("k4", answering("k4").loader);
  await settle();
  assert.deepEqual(store.keys(), ["k3", "k1", "k4"]);
  assert.deepEqual(store.get("k1"), { status: "ok", data: "k1" }, "using a key doesn't reload it");

  // A watched key is never evicted, however old.
  store.subscribe("k3", listener());
  store.ensure("k5", answering("k5").loader);
  store.ensure("k6", answering("k6").loader);
  await settle();
  assert.deepEqual(store.keys(), ["k3", "k5", "k6"]);
  assert.equal(store.get("k3")?.status, "ok");
});

test("a loading key is never evicted, even by a newer one past the limit", async () => {
  const { store } = testStore(1);
  const one = controlled<string>();
  const two = controlled<string>();
  store.ensure("k1", one.loader);
  store.ensure("k2", two.loader);
  assert.deepEqual(store.keys(), ["k1", "k2"]);
  assert.equal(one.calls[0].signal.aborted, false);
  one.calls[0].resolve("one");
  two.calls[0].resolve("two");
  await settle();
  // Once both have answered, the older answer (nobody watches it) makes room.
  assert.deepEqual(store.keys(), ["k2"]);
  assert.deepEqual(store.get("k2"), { status: "ok", data: "two" });
});

test("errors aren't cached: Try again asks again, and an error nobody sees is forgotten", async () => {
  const { store, flush } = testStore();
  const load = controlled<string>();
  const unsubscribe = store.subscribe("k", listener());
  store.ensure("k", load.loader);
  load.calls[0].reject(new ApiError(503, "results_unavailable", "Rebuild the results."));
  await settle();
  const entry = store.get("k");
  assert.equal(entry?.status, "error");
  assert.equal(entry?.status === "error" && entry.error.code, "results_unavailable");

  store.ensure("k", load.loader); // another component mounting: shows the same error
  assert.equal(load.calls.length, 1);
  store.reload("k"); // Try again
  assert.equal(load.calls.length, 2);
  assert.equal(store.get("k")?.status, "loading");
  load.calls[1].resolve("fixed");
  await settle();
  assert.deepEqual(store.get("k"), { status: "ok", data: "fixed" });
  unsubscribe();
  flush();

  // An error left behind by everyone is dropped, so the next visit asks again.
  const other = controlled<string>();
  const leave = store.subscribe("e", listener());
  store.ensure("e", other.loader);
  other.calls[0].reject(new ApiError(0, "unreachable", "Can't reach the analysis server."));
  await settle();
  leave();
  flush();
  assert.equal(store.get("e"), undefined);
  assert.ok(!store.keys().includes("e"));
  store.ensure("e", other.loader);
  assert.equal(other.calls.length, 2);
});

test("an error from a load nobody watches isn't kept for the next watcher", async () => {
  // A health recheck (reload with its loader) after the page that showed health went away.
  const { store } = testStore();
  const load = controlled<string>();
  store.reload("k", load.loader);
  load.calls[0].reject(new ApiError(0, "unreachable", "Can't reach the analysis server."));
  await settle();
  assert.equal(store.get("k"), undefined);
  assert.ok(!store.keys().includes("k"));

  // The next watcher asks again rather than inheriting the old failure.
  store.subscribe("k", listener());
  store.ensure("k", load.loader);
  assert.equal(load.calls.length, 2);
  assert.equal(store.get("k")?.status, "loading");

  // The same for a load started by ensure with nobody subscribed (a prefetch).
  const other = controlled<string>();
  store.ensure("p", other.loader);
  other.calls[0].reject(new ApiError(503, "results_unavailable", "Rebuild the results."));
  await settle();
  assert.equal(store.get("p"), undefined);
});

test("a load nobody watches any more is aborted, and leaves nothing behind", async () => {
  const { store, flush } = testStore();
  const load = controlled<string>();
  const unsubscribe = store.subscribe("k", listener());
  store.ensure("k", load.loader);
  unsubscribe();
  assert.equal(load.calls[0].signal.aborted, false, "not before the deferred check");
  flush();
  assert.equal(load.calls[0].signal.aborted, true);
  assert.equal(store.get("k"), undefined);

  // The aborted request then rejects (getJSON says "aborted"): no error is stored.
  load.calls[0].reject(new ApiError(0, "aborted", "The request was cancelled."));
  await settle();
  assert.equal(store.get("k"), undefined);
  assert.ok(!store.keys().includes("k"));

  store.subscribe("k", listener());
  store.ensure("k", load.loader);
  assert.equal(load.calls.length, 2, "the next visit asks again");
});

test("StrictMode's unsubscribe and resubscribe in one go doesn't abort the request", () => {
  const { store, flush } = testStore();
  const load = controlled<string>();
  const watcher = listener();
  const unsubscribe = store.subscribe("k", watcher);
  store.ensure("k", load.loader);
  unsubscribe(); // StrictMode's simulated unmount...
  store.subscribe("k", watcher); // ...and remount, in the same commit
  store.ensure("k", load.loader);
  flush();
  assert.equal(load.calls.length, 1);
  assert.equal(load.calls[0].signal.aborted, false);
});

test("moving to another key aborts the old key's request, not the new one's", () => {
  const { store, flush } = testStore();
  const load = controlled<string>();
  const watcher = listener();
  const leaveA = store.subscribe("a", watcher);
  store.ensure("a", load.loader);
  leaveA();
  store.subscribe("b", watcher);
  store.ensure("b", load.loader);
  flush();
  assert.equal(load.calls[0].signal.aborted, true);
  assert.equal(load.calls[1].signal.aborted, false);
  assert.equal(store.get("a"), undefined);
  assert.equal(store.get("b")?.status, "loading");
});

test("a late answer from an aborted load can't overwrite the newer one", async () => {
  const { store, flush } = testStore();
  const load = controlled<string>();
  const leave = store.subscribe("k", listener());
  store.ensure("k", load.loader);
  leave();
  flush(); // aborted
  store.subscribe("k", listener());
  store.ensure("k", load.loader);
  load.calls[0].resolve("old"); // a loader that ignored its signal
  await settle();
  assert.equal(store.get("k")?.status, "loading");
  load.calls[1].resolve("new");
  await settle();
  assert.deepEqual(store.get("k"), { status: "ok", data: "new" });
});

test("reload while loading doesn't start a second request; reload of an answer does", async () => {
  const { store } = testStore();
  const load = controlled<string>();
  store.subscribe("k", listener());
  store.ensure("k", load.loader);
  store.reload("k");
  assert.equal(load.calls.length, 1);
  load.calls[0].resolve("first");
  await settle();
  store.reload("k");
  assert.equal(load.calls.length, 2);
  assert.equal(store.get("k")?.status, "loading");
  // reload can bring the loader itself (recheckHealth before any component asked).
  const fresh = answering("fresh");
  store.reload("new", fresh.loader);
  await settle();
  assert.deepEqual(store.get("new"), { status: "ok", data: "fresh" });
});

test("a loader that throws, or rejects with something else than an ApiError, gives an ApiError", async () => {
  const { store } = testStore();
  store.subscribe("sync", listener());
  store.ensure("sync", () => {
    throw new Error("bug in the loader");
  });
  store.subscribe("async", listener());
  store.ensure("async", async () => {
    throw "not an error";
  });
  await settle();
  for (const key of ["sync", "async"]) {
    const entry = store.get(key);
    assert.equal(entry?.status, "error");
    assert.ok(entry?.status === "error" && entry.error instanceof ApiError);
    assert.equal(entry?.status === "error" && entry.error.code, "client_error");
  }
});

test("the hook's states: idle, loading, ok and error, with the last data kept as stale", () => {
  const retry = () => {};
  const loading: Entry<string> = { status: "loading", controller: new AbortController() };
  const failed: Entry<string> = { status: "error", error: new ApiError(422, "invalid_input", "No such lap.") };

  assert.deepEqual(resourceState(null, undefined, null, retry), { status: "idle" });
  assert.deepEqual(resourceState("k", undefined, null, retry), { status: "loading" });
  assert.deepEqual(resourceState("k", loading, null, retry), { status: "loading" });
  assert.deepEqual(resourceState("k", { status: "ok", data: "B" }, { data: "A" }, retry), { status: "ok", data: "B" });

  // Key A showed "A"; key B is loading (or not even started), then fails: "A" stays on offer.
  assert.deepEqual(resourceState("b", undefined, { data: "A" }, retry), { status: "loading", stale: "A" });
  assert.deepEqual(resourceState("b", loading, { data: "A" }, retry), { status: "loading", stale: "A" });
  const error = resourceState("b", failed, { data: "A" }, retry);
  assert.equal(error.status, "error");
  assert.ok(error.status === "error" && error.retry === retry && error.stale === "A");
  assert.ok(error.status === "error" && error.error.code === "invalid_input");
  assert.ok(!("stale" in resourceState("b", failed, null, retry)), "no stale key without kept data");
});
