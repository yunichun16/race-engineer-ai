import type { ReactNode } from "react";
import { TextVersion, type TextVersionLabel } from "./TextVersion";

export type ChartCardStatus = "ready" | "loading" | "stale";

export interface ChartCardProps {
  title: ReactNode;
  /** The title's heading level (2 when missing). A level-3 card gives its chart `headingLevel={4}`. */
  level?: 2 | 3;
  subtitle?: ReactNode;
  /** Links or buttons beside the title: "Open in Mistakes" (a secondary sm LinkButton or a .link). */
  actions?: ReactNode;
  /** The tool's summary under the chart, as a closed disclosure. */
  textVersion?: { label: TextVersionLabel; text: string };
  /**
   * "loading" while the first chart is on its way (pass a ChartSkeleton as the children);
   * "stale" while the next one loads over the old one, which stays dimmed. Both set aria-busy.
   */
  status?: ChartCardStatus;
  children: ReactNode;
  className?: string;
}

/**
 * A chart with its title, actions and text version, on a glass panel (spec g): the glass look
 * without a blur, so a page or a chat answer can show many of them. On a phone it takes 8 px of
 * the page's 16 px gutter on each side and keeps its rounded corners.
 *
 * The chart area has no padding, so the chart spans the card from edge to edge: the chart frame
 * is the card's width less its 1 px borders, and the chart pads itself (14 px a side). A 360 px
 * phone gives a 342 px frame and 314 px to draw, above every chart's floor. Anything else put in
 * the chart area brings its own padding, as ChartSkeleton and ChartUndrawable do. Like the chart
 * frame, the card takes its width from where it sits, never from its content: put it in a
 * full-width place (a block, or a stretched flex or grid item), not one that shrinks to fit.
 *
 * Headings nest: the page's h1, then the card's title (`level`, 2 or 3), then the chart's own
 * heading one level below the title. Give the chart `headingLevel={level + 1}`: nothing for a
 * level-2 card (the wrappers' default is 3), `headingLevel={4}` for a level-3 card.
 */
export function ChartCard({
  title,
  level = 2,
  subtitle,
  actions,
  textVersion,
  status = "ready",
  children,
  className,
}: ChartCardProps) {
  const Heading = level === 3 ? "h3" : "h2";
  return (
    <section
      aria-busy={status === "ready" ? undefined : true}
      className={[
        "glass-panel -mx-2 min-w-0 overflow-hidden rounded-card sm:mx-0",
        // The old chart stays readable but dimmed while the next one loads.
        "[&_.re-chart-frame]:transition-opacity [&_.re-chart-frame]:duration-(--dur-state)",
        status === "stale" ? "[&_.re-chart-frame]:opacity-60" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 pb-1">
        <div className="min-w-0">
          <Heading className="text-title text-fg">{title}</Heading>
          {subtitle ? <p className="mt-0.5 text-sm text-muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
      {textVersion ? <TextVersion label={textVersion.label} summary={textVersion.text} /> : null}
    </section>
  );
}
