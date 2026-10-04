"use client";

import { useEffect, useSyncExternalStore } from "react";
import {
  featuredCornerServerSnapshot,
  featuredCornerSnapshot,
  startFeaturedCorner,
  startWhenIdle,
  subscribeFeaturedCorner,
  type FeaturedState,
  type IdleHost,
} from "./featured-corner";

/** The landing example's state, shared by the hero readout, the story and the real chart. */
export function useFeaturedCorner(): FeaturedState {
  return useSyncExternalStore(subscribeFeaturedCorner, featuredCornerSnapshot, featuredCornerServerSnapshot);
}

/** Starts the example's load once the page has loaded and the browser is idle (spec h.2). */
export function useStartFeaturedWhenIdle(): void {
  useEffect(() => startWhenIdle(window as unknown as IdleHost, () => startFeaturedCorner()), []);
}

const noop = () => () => {};

/** Whether this browser has IntersectionObserver (assumed on the server, so hydration matches). */
export function useHasObserver(): boolean {
  return useSyncExternalStore(
    noop,
    () => typeof IntersectionObserver !== "undefined",
    () => true,
  );
}
