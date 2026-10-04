"use client";

import { useEffect, useId, useRef } from "react";
import { Chart } from "@/components/charts/Chart";
import { ChartCard } from "@/components/charts/ChartCard";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { chartHeading } from "@/lib/chat/format";
import { cornerQuestion, explorerLink, linkFields } from "@/lib/chat/links";
import type { AttachmentPart } from "@/lib/chat/reducer";
import { ExplorerAction } from "./ExplorerAction";

export interface AttachmentProps {
  part: AttachmentPart;
  /** The find-mistakes payload it was opened from: its session, for the links while it loads. */
  list: unknown;
  /** Puts a question in the composer, never sends it. */
  onPrefill: (question: string) => void;
  /** Just opened: take focus (and so scroll into view), as an opened corner does on /mistakes. */
  focusOnMount: boolean;
}

/**
 * A corner chart opened with "Show telemetry" inside a chat find-mistakes card. It came from the
 * REST tool, not from the model, so it uses no question and says so; "Ask about this corner" puts
 * a question about it in the composer.
 */
export function Attachment({ part, list, onPrefill, focusOnMount }: AttachmentProps) {
  const frame = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const card = frame.current;
    if (!focusOnMount || !card) return;
    // Focus alone would centre a card taller than the view (Chrome), hiding its title; "nearest"
    // shows a short card whole and puts a tall one's top at the top.
    card.focus({ preventScroll: true });
    card.scrollIntoView({ block: "nearest" });
  }, [focusOnMount]);

  const corner = part.corner;
  const loaded = part.state === "ok" && part.data !== undefined ? part.data : undefined;
  const heading = loaded !== undefined ? chartHeading("explain-corner", loaded) : null;
  const title =
    heading?.title ?? (corner ? `${corner.driver} · lap ${corner.lap_number} · turn ${corner.turn}` : "Corner telemetry");
  // The corner's own payload when it is here (cut down to its link fields after a full storage),
  // else the list's session plus the corner the row named.
  const fields = loaded ?? (corner ? { ...linkFields(list), ...corner } : null);
  const link = fields ? explorerLink({ bundle: "explain-corner", data: fields }) : null;
  const question = fields ? cornerQuestion(fields) : null;

  let body;
  if (part.state === "loading") {
    body = (
      <ChartSkeleton
        name="explain-corner"
        label={corner ? `Loading lap ${corner.lap_number}, turn ${corner.turn}…` : "Loading the corner…"}
      />
    );
  } else if (part.state === "error") {
    body = (
      <div className="px-4 pt-2 pb-4">
        <Notice tone="error" title="The corner chart couldn't load.">
          <p className="wrap-anywhere">{part.error}</p>
        </Notice>
      </div>
    );
  } else if (part.dropped || loaded === undefined) {
    body = (
      <p className="px-4 pt-2 pb-4 text-sm text-muted">
        Chart not kept after reload.{link ? " Open it in the explorer." : ""}
      </p>
    );
  } else {
    body = <Chart bundle="explain-corner" data={loaded} label={heading?.label ?? title} headingLevel={4} />;
  }

  return (
    <div ref={frame} tabIndex={-1} role="group" aria-labelledby={titleId} className="rounded-card">
      <ChartCard
        level={3}
        title={<span id={titleId}>{title}</span>}
        subtitle="Opened from the chart. Not part of the conversation."
        status={part.state === "loading" ? "loading" : "ready"}
        actions={
          <>
            {question ? (
              <Button variant="secondary" size="sm" onClick={() => onPrefill(question)}>
                Ask about this corner
              </Button>
            ) : null}
            {link ? <ExplorerAction link={link} /> : null}
          </>
        }
        textVersion={
          part.state === "ok" && part.summary ? { label: "Text version", text: part.summary } : undefined
        }
      >
        {body}
      </ChartCard>
    </div>
  );
}
