import type { ReactNode } from "react";
import { ChevronRightIcon } from "./icons";

export interface DisclosureProps {
  summary: ReactNode;
  defaultOpen?: boolean;
  onToggle?(open: boolean): void;
  children: ReactNode;
  className?: string;
}

/**
 * A native <details>: opens with Enter or Space, works before the page's script loads, and the
 * browser's find-in-page can open it. `defaultOpen` sets the first state only; after that the
 * reader owns it, and `onToggle` reports each change (for loading content on first open).
 */
export function Disclosure({ summary, defaultOpen = false, onToggle, children, className }: DisclosureProps) {
  return (
    <details
      open={defaultOpen}
      onToggle={onToggle ? (event) => onToggle(event.currentTarget.open) : undefined}
      className={[
        "group/disclosure rounded-2xl border border-edge bg-glass-strong shadow-[inset_0_1px_0_var(--highlight)]",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <summary className="flex min-h-12 cursor-pointer list-none items-center gap-2.5 rounded-2xl px-4 font-medium text-fg [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon className="size-4 shrink-0 text-muted transition-transform duration-(--dur-state) group-open/disclosure:rotate-90" />
        <span className="min-w-0 py-2">{summary}</span>
      </summary>
      <div className="px-4 pb-4 pl-[42px]">{children}</div>
    </details>
  );
}
