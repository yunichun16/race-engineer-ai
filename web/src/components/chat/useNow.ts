"use client";

import { useSyncExternalStore } from "react";

// One clock for the page, read every 30 s while something shows a countdown ("You can ask again
// in 23 min"), so the minutes go down without a timer per component or a render per second.
// While nothing watches it the clock is unset, so the first read after a pause is fresh rather
// than a frame of a stale count.

const EVERY_MS = 30_000;

let now = 0;
let watchers = 0;

function subscribe(listener: () => void): () => void {
  watchers += 1;
  now = Date.now();
  const timer = setInterval(() => {
    now = Date.now();
    listener();
  }, EVERY_MS);
  return () => {
    clearInterval(timer);
    watchers -= 1;
    if (watchers === 0) now = 0;
  };
}

function read(): number {
  if (now === 0) now = Date.now();
  return now;
}

const idle = () => () => {};
const zero = () => 0;

/** The time, refreshed every 30 s while `active`; 0 while not (and on the server, though the
 *  chat renders only in the browser). */
export function useNow(active: boolean): number {
  return useSyncExternalStore(active ? subscribe : idle, active ? read : zero, zero);
}
