"use client";

import dynamic from "next/dynamic";
import type { Ref } from "react";
import { ChartErrorBoundary } from "@/components/charts/ChartErrorBoundary";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { TextVersion } from "@/components/charts/TextVersion";
import { ErrorNotice } from "@/components/status/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { CloseIcon } from "@/components/ui/icons";
import { LinkButton } from "@/components/ui/LinkButton";
import type { ResourceState } from "@/lib/api/resource";
import type { ToolResult } from "@/lib/api/types";
import { EvidenceNote } from "./EvidenceNote";
import type { ExplainRef } from "./query";
import { askInChatHref, cornerHeading, featuredFor, sessionPhrase, type Selection } from "./selection";

// The corner chart's code loads the first time a corner opens, behind a skeleton of its height.
const ExplainCornerChart = dynamic(() => import("@/components/charts/ExplainCornerChart").then((m) => m.ExplainCornerChart), {
  ssr: false,
  loading: () => <ChartSkeleton name="explain-corner" label="Loading the corner chart…" />,
});

export const EXPLAIN_HEADING_ID = "explain-title";

// The panel's frame: glass without a blur (spec b), near-bleed on a phone like a chart card,
// and from `lg` no taller than the window, scrolling inside itself, since it sticks beside a
// list that can be much longer.
const PANEL =
  "glass-panel -mx-2 min-w-0 overflow-hidden rounded-card sm:mx-0 max-lg:scroll-mt-20 " +
  "lg:max-h-[calc(100dvh-var(--header-h)-32px)] lg:overflow-y-auto lg:overscroll-contain";

export interface ExplainPanelProps {
  selection: Selection;
  corner: ExplainRef;
  state: ResourceState<ToolResult<"explain_corner">>;
  onClose(): void;
  /** Try again when the server couldn't be reached: everything on the page that failed. */
  onRetryAll?: () => void;
  panelRef?: Ref<HTMLElement>;
  headingRef?: Ref<HTMLHeadingElement>;
}

/**
 * The open corner (plan 7.3): its heading (focused when a corner opens), one line saying where
 * the finding comes from, the corner chart with its traces, map and replay (which plays once when
 * it comes into view, never with reduced motion), and the tool's text version. "Ask about this
 * corner in chat" fills in the question on /chat without sending it; Close goes back to the list.
 */
export function ExplainPanel({ selection, corner, state, onClose, onRetryAll, panelRef, headingRef }: ExplainPanelProps) {
  const payload = state.status === "ok" ? state.data : null;
  const featured = featuredFor(selection, corner);
  const loadingText = `Loading lap ${corner.lap}, turn ${corner.turn}…`;
  const label = `Corner chart: ${corner.driver}, lap ${corner.lap}, turn ${corner.turn}, ${sessionPhrase(selection)}`;

  return (
    <section ref={panelRef} aria-labelledby={EXPLAIN_HEADING_ID} aria-busy={payload ? undefined : true} className={PANEL}>
      <div className="grid gap-2 px-4 pt-4 pb-3">
        <h2 id={EXPLAIN_HEADING_ID} ref={headingRef} tabIndex={-1} className="text-title text-fg">
          {cornerHeading(corner)}
        </h2>
        <EvidenceNote featured={featured} corner={payload?.data ?? null} pending={state.status === "error" ? "" : loadingText} />
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <LinkButton href={askInChatHref(selection, corner)} variant="secondary" size="sm" prefetch={false}>
            Ask about this corner in chat <span aria-hidden="true">→</span>
          </LinkButton>
          <Button variant="secondary" size="sm" onClick={onClose}>
            <CloseIcon className="size-4" />
            Close
          </Button>
        </div>
      </div>

      {payload ? (
        <ChartErrorBoundary summary={payload.summary}>
          <ExplainCornerChart data={payload.data} autoplay label={label} />
        </ChartErrorBoundary>
      ) : state.status === "error" ? (
        <div className="px-4 pb-4">
          <ErrorNotice
            error={state.error}
            onRetry={state.error.code === "unreachable" && onRetryAll ? onRetryAll : state.retry}
          />
        </div>
      ) : (
        <ChartSkeleton name="explain-corner" label={loadingText} />
      )}

      {payload ? <TextVersion label="Text version" summary={payload.summary} /> : null}
    </section>
  );
}

/** Beside the list on a wide screen, before any corner is open. */
export function ExplainPlaceholder() {
  return (
    <div className="glass-panel grid gap-2 rounded-card px-5 py-6 max-lg:hidden">
      <p className="text-title text-fg">Pick a corner to see the telemetry</p>
      <p className="text-sm text-muted">
        Show telemetry on a row opens that lap through the corner against the driver&apos;s usual: the speed, throttle and brake
        traces, a map of the corner and a replay of the cars around them.
      </p>
    </div>
  );
}
