import type { ReactNode } from "react";
import { Notice } from "@/components/ui/Notice";
import { TextVersion } from "./TextVersion";

export interface ChartUndrawableProps {
  /** "This chart couldn't be drawn." when missing. */
  title?: string;
  /** The tool's summary, offered instead of the chart. */
  summary?: string;
  action?: ReactNode;
}

/**
 * In place of a chart whose data or code didn't arrive in a shape it can draw. It pads itself,
 * since a chart card's chart area has no padding of its own.
 */
export function ChartUndrawable({ title = "This chart couldn't be drawn.", summary, action }: ChartUndrawableProps) {
  return (
    <div className="flex flex-col gap-3 px-4 pt-3 pb-4">
      <Notice tone="error" title={title} action={action}>
        {summary ? "The text version below has the same content." : null}
      </Notice>
      {summary ? (
        <div className="rounded-2xl border border-edge [--text-version-radius:calc(var(--radius-2xl)_-_1px)]">
          <TextVersion label="Text version" summary={summary} className="border-t-0" />
        </div>
      ) : null}
    </div>
  );
}
