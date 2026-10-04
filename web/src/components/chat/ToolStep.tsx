import { WarningIcon } from "@/components/ui/icons";
import { toolStepLabel } from "@/lib/chat/format";
import type { ToolPart } from "@/lib/chat/reducer";

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <path d="m5 12 4 4 10-10" />
    </svg>
  );
}

/**
 * One tool call, where it happened in the answer: "Finding mistakes · monza · LEC · 2025 · Q",
 * with a spinner while it runs, then an ink check, or an amber icon and "didn't work" ("didn't
 * finish" when the answer stopped before its result came). The chip sits on the bare page, so its
 * words stay muted (amber small text isn't readable there).
 */
export function ToolStep({ part, answering }: { part: ToolPart; answering: boolean }) {
  const label = toolStepLabel(part.name, part.input);
  const result = part.result;
  // A call with no result in an answer that has ended (stopped, cut off) never finished.
  const running = result === undefined && answering;
  const failed = result === undefined ? !answering : result.is_error;
  return (
    <li className="inline-flex max-w-full items-center gap-2 rounded-full border border-edge py-1 pr-3 pl-2 font-mono text-xs/[1.5] wrap-anywhere text-muted">
      {running ? (
        <span className="spinner size-3.5 border-[1.5px]" aria-hidden="true" />
      ) : failed ? (
        <WarningIcon className="size-4 shrink-0 text-caution" />
      ) : (
        <CheckIcon className="size-4 shrink-0 text-fg" />
      )}
      <span className="min-w-0">
        {label}
        {running ? <span className="sr-only"> (running)</span> : null}
        {failed ? (result === undefined ? " · didn't finish" : " · didn't work") : null}
        {!running && !failed ? <span className="sr-only"> (done)</span> : null}
      </span>
    </li>
  );
}
