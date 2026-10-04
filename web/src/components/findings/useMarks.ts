"use client";

import { useRef, useState, type FocusEvent, type KeyboardEvent } from "react";

export interface MarkProps {
  ref: (el: HTMLElement | null) => void;
  tabIndex: 0 | -1;
  "data-active": boolean;
  onFocus(): void;
  onBlur(event: FocusEvent<HTMLElement>): void;
  onPointerEnter(): void;
  onPointerLeave(): void;
  onKeyDown(event: KeyboardEvent<HTMLElement>): void;
}

/**
 * Keyboard and pointer reading for a figure's marks. The figure is one stop in the Tab order
 * (a roving tab stop: the last mark read keeps tabIndex 0); the arrow keys move between marks,
 * Home and End jump to the ends, Escape clears the readout. `active` is the mark the readout
 * describes: the one focused, or else the one under the pointer; null when neither.
 */
export function useMarks(count: number) {
  const [stop, setStop] = useState(0);
  const [focused, setFocused] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const nodes = useRef<(HTMLElement | null)[]>([]);

  const last = Math.max(0, count - 1);
  const current = Math.min(stop, last);

  function moveTo(i: number) {
    const next = Math.min(Math.max(i, 0), last);
    setStop(next);
    // Set here too: after Escape, a key that lands on the mark already focused (End on the last
    // one) fires no focus event, and the readout would stay empty.
    setFocused(next);
    nodes.current[next]?.focus();
  }

  function markProps(i: number): MarkProps {
    return {
      ref: (el) => {
        nodes.current[i] = el;
      },
      tabIndex: i === current ? 0 : -1,
      "data-active": (focused ?? hovered) === i,
      onFocus: () => {
        setStop(i);
        setFocused(i);
      },
      onBlur: (event) => {
        // Moving to another mark of the same figure focuses it straight away; leaving clears.
        if (!nodes.current.includes(event.relatedTarget as HTMLElement | null)) setFocused(null);
      },
      onPointerEnter: () => setHovered(i),
      onPointerLeave: () => setHovered((h) => (h === i ? null : h)),
      onKeyDown: (event) => {
        const keys: Record<string, number> = {
          ArrowDown: i + 1,
          ArrowRight: i + 1,
          ArrowUp: i - 1,
          ArrowLeft: i - 1,
          Home: 0,
          End: last,
        };
        if (event.key in keys) {
          event.preventDefault();
          moveTo(keys[event.key]);
        } else if (event.key === "Escape") {
          setFocused(null);
          setHovered(null);
        }
      },
    };
  }

  return { active: focused ?? hovered, markProps };
}
