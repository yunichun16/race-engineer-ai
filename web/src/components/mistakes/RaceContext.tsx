"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { ChartErrorBoundary } from "@/components/charts/ChartErrorBoundary";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { TextVersion } from "@/components/charts/TextVersion";
import { ErrorNotice } from "@/components/status/ErrorNotice";
import { ChevronRightIcon } from "@/components/ui/icons";
import { useTool } from "@/lib/api/tools";
import { sessionPhrase, type Selection } from "./selection";

const LOADING = "Loading the race summary…";

const RaceSummaryChart = dynamic(() => import("@/components/charts/RaceSummaryChart").then((m) => m.RaceSummaryChart), {
  ssr: false,
  loading: () => <ChartSkeleton name="race-summary" label={LOADING} />,
});

export interface RaceContextProps {
  /** A race or a sprint (the page shows this only for those). */
  selection: Selection & { session: "R" | "S" };
  /** The list's driver, whom the summary also follows. */
  driver: string | undefined;
}

/**
 * "Race context" (plan 7.3), closed until asked for: the positions lap by lap, safety cars and
 * pit stops, fetched only when it opens (get_race_summary). Built like a chart card rather than
 * on the shared Disclosure, so the chart keeps the card's full width on a phone.
 */
export function RaceContext({ selection, driver }: RaceContextProps) {
  const [open, setOpen] = useState(false);
  const state = useTool(
    "get_race_summary",
    open ? { event: selection.event, year: selection.year, session: selection.session, driver } : null,
  );
  const shown = state.status === "ok" ? state.data : state.status === "loading" ? state.stale : undefined;

  return (
    <details
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="group/race glass-panel -mx-2 min-w-0 overflow-hidden rounded-card sm:mx-0"
    >
      {/* While closed, the summary's corners follow the card's inner ones, or the card, which clips
          what it holds, would cut the focus ring's corners off. */}
      <summary className="flex min-h-14 cursor-pointer list-none items-center gap-2.5 rounded-[calc(var(--radius-card)_-_1px)] px-4 font-medium text-fg group-open/race:rounded-b-none focus-visible:-outline-offset-2 [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon className="size-4 shrink-0 text-muted transition-transform duration-(--dur-state) group-open/race:rotate-90" />
        <span className="min-w-0 py-3">Race context: positions, safety cars and pit stops</span>
      </summary>
      {open ? (
        <div className="border-t border-edge" aria-busy={state.status === "loading" || undefined}>
          <p className="px-4 pt-3 text-sm text-muted">
            As timed on track: grid positions, penalties and the official classification aren&apos;t in the data.
          </p>
          {shown ? (
            <div className={state.status === "loading" ? "opacity-60 transition-opacity duration-(--dur-state)" : undefined}>
              <ChartErrorBoundary summary={shown.summary}>
                <RaceSummaryChart data={shown.data} label={`Race summary: positions by lap in ${sessionPhrase(selection)}`} />
              </ChartErrorBoundary>
            </div>
          ) : state.status === "error" ? (
            <div className="px-4 pt-3 pb-4">
              <ErrorNotice error={state.error} onRetry={state.retry} />
            </div>
          ) : (
            <ChartSkeleton name="race-summary" label={LOADING} />
          )}
          {shown ? <TextVersion label="Text version" summary={shown.summary} /> : null}
        </div>
      ) : null}
    </details>
  );
}
