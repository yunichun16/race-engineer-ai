import { ChevronRightIcon } from "@/components/ui/icons";

export type TextVersionLabel = "Text version" | "What Claude saw";

export interface TextVersionProps {
  /** "Text version" on the explorers, "What Claude saw" in the chat. */
  label: TextVersionLabel;
  /** The tool's summary, line breaks kept: the chart's complete text alternative. */
  summary: string;
  className?: string;
}

/**
 * The tool's own summary of a chart, closed until asked for (a native <details>), word for word
 * in monospace. It is drawn as the foot of a chart card: a hairline above, the full width of the
 * card, its bottom corners following the card's. To box it elsewhere, put it in a rounded,
 * bordered wrapper, set `--text-version-radius` on the wrapper to its inner radius, and pass
 * `className="border-t-0"`.
 */
export function TextVersion({ label, summary, className }: TextVersionProps) {
  return (
    <details className={["group/text border-t border-edge", className].filter(Boolean).join(" ")}>
      {/* The focus ring sits inside the summary, and while it is closed the summary's bottom
          corners follow the card's inner corners (its radius less the 1 px border), or the card,
          which clips what it holds, would cut the ring's corners off. The radius is set, not
          inherited: the browser slots a summary into the details' shadow tree, so `inherit`
          reads the slot's radius, which is 0. */}
      <summary className="flex min-h-12 cursor-pointer list-none items-center gap-2 rounded-b-[var(--text-version-radius,calc(var(--radius-card)_-_1px))] px-4 text-sm font-medium text-fg group-open/text:rounded-b-none focus-visible:-outline-offset-2 [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon className="size-4 shrink-0 text-muted transition-transform duration-(--dur-state) group-open/text:rotate-90" />
        {label}
      </summary>
      <pre className="px-4 pb-4 font-mono text-[12.5px] leading-relaxed wrap-break-word whitespace-pre-wrap text-muted">
        {summary}
      </pre>
    </details>
  );
}
