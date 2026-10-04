import type { ReactNode } from "react";

/** Text for screen readers only: an icon button's name, a table's caption. */
export function VisuallyHidden({ children }: { children: ReactNode }) {
  return <span className="sr-only">{children}</span>;
}
