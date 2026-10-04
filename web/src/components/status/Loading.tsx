"use client";

import { useEffect, useState } from "react";

export interface LoadingProps {
  /** What is loading: "Finding the flagged corners…". */
  label: string;
  /** How long to wait before saying so (ms): a quick answer never flashes a message. */
  delayMs?: number;
  className?: string;
}

/**
 * Polite live text for a load in progress, shown only after `delayMs` (150 ms). The live region
 * is there from the start, empty, so screen readers announce the text once when it appears.
 * The space is reserved, so the text arriving doesn't move the page.
 */
export function Loading({ label, delayMs = 150, className }: LoadingProps) {
  const [shown, setShown] = useState(delayMs <= 0);
  useEffect(() => {
    if (shown) return;
    const timer = setTimeout(() => setShown(true), delayMs);
    return () => clearTimeout(timer);
  }, [shown, delayMs]);
  return (
    <p role="status" className={["min-h-6 text-sm text-muted", className].filter(Boolean).join(" ")}>
      {shown ? label : null}
    </p>
  );
}
