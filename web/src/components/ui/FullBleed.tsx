import type { ReactNode } from "react";

/**
 * Under 640 px, takes 8 px of the page container's 16 px side padding, so a chart card is
 * near-bleed on a phone: 8 px gutters, its rounded corners kept, and a 360 px phone gives the
 * chart 314 px to draw (above its 300 px floor). From `sm` up it sits inside the padding.
 */
export function FullBleed({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={["-mx-2 sm:mx-0", className].filter(Boolean).join(" ")}>{children}</div>;
}
