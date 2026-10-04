"use client";

import type { Mistake } from "@/charts/find-mistakes";
import { Chart } from "@/components/charts/Chart";
import { ChartCard } from "@/components/charts/ChartCard";
import { chartHeading } from "@/lib/chat/format";
import { explorerLink } from "@/lib/chat/links";
import { attachmentsFor, chartParts, type ToolPart, type Turn } from "@/lib/chat/reducer";
import { Attachment } from "./Attachment";
import { ExplorerAction } from "./ExplorerAction";

export interface ChartCardsProps {
  turn: Turn;
  /** "Show telemetry" on a find-mistakes row: the tool part, its list payload and the row. */
  onExplain: (turnId: string, toolId: string, list: unknown, mistake: Mistake) => void;
  onPrefill: (question: string) => void;
  /** The attachment opened last on this page, which takes focus as it appears. */
  openedId: string | null;
}

function ToolChart({ turn, part, onExplain, onPrefill, openedId }: Omit<ChartCardsProps, "turn"> & { turn: Turn; part: ToolPart }) {
  const result = part.result;
  const chart = result?.chart;
  if (!result || !chart) return null;
  const heading = chartHeading(chart.bundle, chart.data);
  const link = explorerLink(chart);
  const attachments = attachmentsFor(turn, part.id);
  const mistakes = chart.bundle === "find-mistakes";
  // The row whose corner was opened last is marked as open in the list.
  const open = mistakes ? (attachments.findLast((a) => a.corner !== undefined)?.corner ?? null) : undefined;

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <ChartCard
        level={3}
        title={heading.title}
        subtitle={heading.subtitle}
        actions={link ? <ExplorerAction link={link} /> : undefined}
        textVersion={result.summary ? { label: "What Claude saw", text: result.summary } : undefined}
      >
        {chart.dropped ? (
          <p className="px-4 pt-2 pb-4 text-sm text-muted">
            Chart not kept after reload.{link ? " Open it in the explorer." : ""}
          </p>
        ) : (
          <Chart
            bundle={chart.bundle}
            data={chart.data}
            label={heading.label}
            headingLevel={4}
            onExplain={mistakes ? (mistake) => onExplain(turn.id, part.id, chart.data, mistake) : undefined}
            selected={open}
          />
        )}
      </ChartCard>
      {attachments.map((attachment) => (
        <Attachment
          key={attachment.id}
          part={attachment}
          list={chart.data}
          onPrefill={onPrefill}
          focusOnMount={attachment.id === openedId}
        />
      ))}
    </div>
  );
}

/**
 * The charts of one answer, under its text and in tool order (plan decision 7), each with "What
 * Claude saw" (the exact summary the model got) and a link into the explorer. Corner charts opened
 * from a find-mistakes card sit under that card.
 */
export function ChartCards({ turn, onExplain, onPrefill, openedId }: ChartCardsProps) {
  const parts = chartParts(turn);
  if (parts.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {parts.map((part) => (
        <ToolChart
          key={part.id}
          turn={turn}
          part={part}
          onExplain={onExplain}
          onPrefill={onPrefill}
          openedId={openedId}
        />
      ))}
    </div>
  );
}
