"use client";

/**
 * A small store for API answers, and the hook that reads it.
 *
 * Keyed by URL, the store holds one entry per key: loading (with the AbortController of its one
 * request), ok (the data) or error. It lives at module level, so a result survives client
 * navigation and every component asking for the same URL shares one request and one answer.
 *
 * - In-flight loads are shared: a second `ensure` for a loading key waits for the same request.
 * - Answers are kept, up to `max` keys (64), least recently used first out. A key someone is
 *   watching, or that is loading, is never evicted.
 * - Errors and aborts aren't cached. An error stays while someone watches the key (the page
 *   shows it, with Try again calling `reload`) and is dropped when nobody does, so the next visit
 *   asks again. An aborted load leaves nothing behind.
 * - A load that nobody watches any more is aborted. The check waits a microtask, so React's
 *   StrictMode (which unsubscribes and resubscribes in one go) and a component handing a key to
 *   another in the same commit don't cancel the request.
 *
 * `useResource` reads an entry with `useSyncExternalStore` and starts the load in an effect; it
 * never sets state in an effect. While a new key loads (or after its error), it still offers the
 * last data it showed as `stale`, so a page can dim the old chart rather than blank it.
 *
 * The store is only ever filled in the browser: the server renders with no entries (the hook's
 * server snapshot is "nothing yet"), so no answer is shared between visitors.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ApiError, toApiError } from "./client.ts";

export type Loader<T> = (signal: AbortSignal) => Promise<T>;

export type Entry<T = unknown> =
  | { readonly status: "loading"; readonly controller: AbortController }
  | { readonly status: "ok"; readonly data: T }
  | { readonly status: "error"; readonly error: ApiError };

export type ResourceState<T> =
  | { status: "idle" }
  | { status: "loading"; stale?: T }
  | { status: "ok"; data: T }
  | { status: "error"; error: ApiError; retry: () => void; stale?: T };

export interface ResourceStore {
  /** The key's entry; the same object until it changes (a `useSyncExternalStore` snapshot). */
  get<T>(key: string): Entry<T> | undefined;
  /** Calls `listener` whenever the key's entry changes. While anyone listens, the key is watched. */
  subscribe(key: string, listener: () => void): () => void;
  /** Starts loading the key unless it is loading, loaded or showing an error. */
  ensure<T>(key: string, loader: Loader<T>): void;
  /** Loads the key again (Try again), unless it is already loading. Without `loader`, the last
   *  one `ensure` or `reload` was given. */
  reload<T>(key: string, loader?: Loader<T>): void;
  /** The keys held, least recently used first (for tests and debugging). */
  keys(): string[];
}

export interface StoreOptions {
  max?: number; // keys kept, 64 by default
  defer?: (fn: () => void) => void; // when to check for unwatched keys; a microtask by default
}

interface Slot {
  entry: Entry | undefined;
  loader: Loader<unknown> | undefined;
  listeners: Set<() => void>;
}

export function createResourceStore({ max = 64, defer = queueMicrotask }: StoreOptions = {}): ResourceStore {
  const slots = new Map<string, Slot>(); // insertion order is use order: oldest first

  function slotFor(key: string): Slot {
    let slot = slots.get(key);
    if (slot) {
      slots.delete(key); // move to the end: most recently used
    } else {
      slot = { entry: undefined, loader: undefined, listeners: new Set() };
    }
    slots.set(key, slot);
    return slot;
  }

  function notify(slot: Slot): void {
    for (const listener of [...slot.listeners]) listener();
  }

  function set(slot: Slot, entry: Entry | undefined): void {
    slot.entry = entry;
    notify(slot);
  }

  // Drop the least recently used keys nobody watches, until at most `max` are left. Loading keys
  // stay (they are watched, or about to be aborted), and so do watched ones.
  function prune(): void {
    if (slots.size <= max) return;
    for (const [key, slot] of slots) {
      if (slots.size <= max) break;
      if (slot.listeners.size === 0 && slot.entry?.status !== "loading") slots.delete(key);
    }
  }

  function start(key: string, slot: Slot): void {
    const loader = slot.loader;
    if (!loader) return;
    const controller = new AbortController();
    const entry: Entry = { status: "loading", controller };
    set(slot, entry);
    let promise: Promise<unknown>;
    try {
      promise = loader(controller.signal);
    } catch (error) {
      promise = Promise.reject(error);
    }
    promise.then(
      (data) => {
        if (slot.entry !== entry || slots.get(key) !== slot) return; // aborted or replaced meanwhile
        set(slot, { status: "ok", data });
        prune();
      },
      (error: unknown) => {
        if (slot.entry !== entry || slots.get(key) !== slot) return;
        // An error nobody watches leaves nothing behind (an aborted load, or one started with no
        // watcher, such as a health recheck after the page that showed it went away), so the
        // next watcher asks again rather than inheriting an old failure.
        if (slot.listeners.size === 0) {
          slots.delete(key);
          return;
        }
        set(slot, { status: "error", error: toApiError(error) });
      },
    );
  }

  // Runs a microtask after the last listener left: abort a load nobody waits for, forget an
  // error nobody sees. A loaded answer stays, for the next visit.
  function release(key: string): void {
    const slot = slots.get(key);
    if (!slot || slot.listeners.size > 0) return;
    const entry = slot.entry;
    if (entry?.status === "loading") {
      slots.delete(key);
      entry.controller.abort();
    } else if (entry?.status !== "ok") {
      slots.delete(key);
    }
  }

  return {
    get<T>(key: string): Entry<T> | undefined {
      return slots.get(key)?.entry as Entry<T> | undefined;
    },

    subscribe(key, listener) {
      const slot = slots.get(key) ?? slotFor(key);
      slot.listeners.add(listener);
      return () => {
        slot.listeners.delete(listener);
        if (slot.listeners.size === 0) defer(() => release(key));
      };
    },

    ensure<T>(key: string, loader: Loader<T>): void {
      const slot = slotFor(key);
      slot.loader = loader as Loader<unknown>;
      if (slot.entry === undefined) start(key, slot);
      prune();
    },

    reload<T>(key: string, loader?: Loader<T>): void {
      const slot = slotFor(key);
      if (loader) slot.loader = loader as Loader<unknown>;
      if (slot.entry?.status !== "loading") start(key, slot);
      prune();
    },

    keys() {
      return [...slots.keys()];
    },
  };
}

/** The page's store: every hook in lib/api reads and fills this one. */
export const resources: ResourceStore = createResourceStore();

/**
 * What a hook returns for a key's entry. `kept` is the last data the hook showed (for this key
 * or an earlier one), offered as `stale` while the key loads or after it failed.
 */
export function resourceState<T>(
  key: string | null,
  entry: Entry<T> | undefined,
  kept: { data: T } | null,
  retry: () => void,
): ResourceState<T> {
  if (key === null) return { status: "idle" };
  const stale = kept ? { stale: kept.data } : {};
  if (entry === undefined || entry.status === "loading") return { status: "loading", ...stale };
  if (entry.status === "ok") return { status: "ok", data: entry.data };
  return { status: "error", error: entry.error, retry, ...stale };
}

const noop = () => {};
const nothing = () => undefined;

/**
 * Reads one API answer by key (its URL), loading it with `loader` when nobody has yet. A null key
 * is "nothing to load" (`idle`). The loader must depend only on the key: the store keeps the
 * first answer for a key, and Try again calls the loader the key was last given.
 */
export function useResource<T>(
  key: string | null,
  loader: Loader<T>,
  store: ResourceStore = resources,
): ResourceState<T> {
  const subscribe = useCallback(
    (listener: () => void) => (key === null ? noop : store.subscribe(key, listener)),
    [key, store],
  );
  const getSnapshot = useCallback(() => (key === null ? undefined : store.get<T>(key)), [key, store]);
  const entry = useSyncExternalStore(subscribe, getSnapshot, nothing);

  // The latest loader, read only inside the effect, so a new function each render never
  // restarts a load (a ref written in a layout effect: the react-hooks rules allow it). The
  // effect hands the store the loader of the render that brought this key, so the key's Try
  // again always loads that key, whatever this component asks for later.
  const latest = useRef(loader);
  useLayoutEffect(() => {
    latest.current = loader;
  });
  useEffect(() => {
    if (key !== null) store.ensure<T>(key, latest.current);
  }, [key, store]);

  // The last data shown, kept across keys: React's "storing information from previous
  // renders", a state update during render that settles at once.
  const [kept, setKept] = useState<{ data: T } | null>(null);
  if (entry?.status === "ok" && kept?.data !== entry.data) setKept({ data: entry.data });

  const retry = useCallback(() => {
    if (key !== null) store.reload(key);
  }, [key, store]);
  return useMemo(() => resourceState(key, entry, kept, retry), [key, entry, kept, retry]);
}
