"use client";

import { useEffect, useEffectEvent, useRef, type RefObject } from "react";
import { showStatus, widthObserver } from "@/charts/dom";

/**
 * Draws a chart into the returned ref's element, and keeps it drawn.
 *
 * - `init(data)` makes the reader's state for new data (an open table, a turn filter, the
 *   replay's position). The chart's callbacks write into it, so a redraw starts from there.
 * - `draw(root, data, state)` calls the chart's `render`. It runs once per new `data`, and again
 *   only when the element's width changes: `widthObserver` coalesces a resize into one frame.
 *   Height changes (a table opening) don't redraw. `data` counts as new by identity, so keep
 *   passing the same object until the data really changes: a fresh copy each render would
 *   redraw the chart every time and reset the reader's state.
 * - `teardown(root)` stops what a draw left running (explain-corner's `unmount`).
 *
 * The chart owns the element's children; React renders the element empty and never touches
 * them, so the two never fight over the DOM. New callback identities never redraw: `draw` is
 * read through an effect event, which always sees the latest props. A draw that throws leaves one
 * line of text in the element instead of half a chart (an error boundary can't catch it, because
 * it runs in an effect and in the observer's frame).
 */
export function useChartRoot<D, S extends object>(
  data: D,
  init: (data: D) => S,
  draw: (root: HTMLElement, data: D, state: S) => void,
  teardown?: (root: HTMLElement) => void,
): RefObject<HTMLDivElement | null> {
  const ref = useRef<HTMLDivElement | null>(null);

  // Each takes the effect's own `data`, so a draw always pairs data with the state made for it,
  // even from a frame that was asked for before newer data rendered and this effect re-ran.
  const makeState = useEffectEvent((d: D) => init(d));
  const tearDown = useEffectEvent((root: HTMLElement) => teardown?.(root));
  const drawNow = useEffectEvent((root: HTMLElement, d: D, state: S) => {
    try {
      draw(root, d, state);
    } catch (error) {
      console.error(error);
      teardown?.(root);
      showStatus(root, "This chart couldn't be drawn.");
    }
  });

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const state = makeState(data);
    drawNow(root, data, state);
    // widthObserver redraws on the next frame, and disconnecting it doesn't cancel a frame
    // already asked for: `live` stops that frame drawing into a root this cleanup emptied.
    let live = true;
    const observer = widthObserver(root, () => {
      if (live) drawNow(root, data, state);
    });
    return () => {
      live = false;
      observer.disconnect();
      tearDown(root);
      root.replaceChildren();
    };
  }, [data]);

  return ref;
}
