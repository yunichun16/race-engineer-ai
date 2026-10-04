"use client";

import type { Ref } from "react";
import { Chart } from "@/components/charts/Chart";
import { ChartCard } from "@/components/charts/ChartCard";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { ErrorNotice } from "@/components/status/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { toApiError, type ApiError } from "@/lib/api/client";
import { describe } from "@/lib/api/describe";
import type { ToolResult } from "@/lib/api/types";
import { IS_DEV } from "@/lib/env";
import type { Plane, StyleKind } from "./query";

export type StyleResultState =
  | { status: "ready"; result: ToolResult<"compare_driving_styles"> }
  | { status: "loading"; stale?: ToolResult<"compare_driving_styles"> }
  | { status: "error"; error: ApiError; retry: () => void };

export interface StyleResultProps {
  /** The pair asked for, and its season when known; no pair while the page picks one. */
  a: string | undefined;
  b: string | undefined;
  year: number | undefined;
  state: StyleResultState;
  initialKind?: StyleKind;
  initialPlane?: Plane;
  onKindChange(kind: StyleKind): void;
  onPlaneChange(plane: Plane): void;
  onPointSelect(driver: string): void;
  /** Sends the reader to the pair list (after a 422); absent when there is no list. */
  onPickFromList?: () => void;
  /** The title's text, which the page focuses after an example or a map pick. */
  headingRef?: Ref<HTMLSpanElement>;
  /** The card, so the page can find the new map after a map pick. */
  cardRef?: Ref<HTMLDivElement>;
}

/** A failed comparison, in the words of plan 7.4's states. */
function ResultError({ error, onRetry, onPickFromList }: { error: ApiError; onRetry: () => void; onPickFromList?: () => void }) {
  const e = toApiError(error);
  if (e.code === "invalid_input") {
    // The server's message unchanged (it lists the season's drivers), and where to go next.
    return (
      <Notice
        tone="warn"
        title={describe(e).title}
        action={
          onPickFromList ? (
            <Button variant="secondary" size="sm" onClick={onPickFromList}>
              Pick teammates from the list
            </Button>
          ) : undefined
        }
      >
        <p className="whitespace-pre-line wrap-anywhere">{e.message}</p>
        {onPickFromList ? null : <p className="mt-2">Try two other codes under “Compare any two drivers”.</p>}
      </Notice>
    );
  }
  if (e.code === "results_unavailable") {
    return (
      <Notice tone="error" title="The style tables on the server need rebuilding.">
        <p className="whitespace-pre-line wrap-anywhere">{IS_DEV ? e.message : "Try again later."}</p>
      </Notice>
    );
  }
  return <ErrorNotice error={e} onRetry={onRetry} />;
}

/**
 * "{A} and {B}, {year}": the style comparison on a chart card, with the tool's summary as its
 * text version. While the next pair loads, the previous chart stays, dimmed. Kind and plane reach
 * the chart only as where to start (read when new data arrives), so the page can keep them in
 * its URL without redrawing the chart or moving focus off the radio.
 */
export function StyleResult(props: StyleResultProps) {
  const { a, b, year, state, headingRef, cardRef } = props;
  const shown = state.status === "ready" ? state.result : state.status === "loading" ? state.stale : undefined;
  const d = state.status === "ready" ? state.result.data : null;
  const title = (
    <span ref={headingRef} tabIndex={-1} className="rounded-md">
      {d
        ? `${d.driver_a} and ${d.driver_b}, ${d.year}`
        : a && b
          ? `${a} and ${b}${year ? `, ${year}` : ""}`
          : `Choosing a pair${year ? ` from ${year}` : ""}…`}
    </span>
  );

  return (
    <div ref={cardRef} className="min-w-0">
      <ChartCard
        title={title}
        // The chart's own head names the teams, events and corners; the card says only what loads.
        subtitle={state.status === "loading" && a && b ? `Comparing ${a} and ${b}…` : undefined}
        status={state.status === "loading" ? (shown ? "stale" : "loading") : "ready"}
        textVersion={shown ? { label: "Text version", text: shown.summary } : undefined}
      >
        {state.status === "error" ? (
          <div className="px-4 pt-2 pb-4">
            <ResultError error={state.error} onRetry={state.retry} onPickFromList={props.onPickFromList} />
          </div>
        ) : shown ? (
          <Chart
            bundle="compare-styles"
            data={shown.data}
            label={`Driving styles of ${shown.data.driver_a} and ${shown.data.driver_b}, ${shown.data.year}`}
            initialKind={props.initialKind}
            initialPlane={props.initialPlane}
            onKindChange={props.onKindChange}
            onPlaneChange={props.onPlaneChange}
            onPointSelect={props.onPointSelect}
          />
        ) : (
          <ChartSkeleton name="compare-styles" label={a && b ? `Comparing ${a} and ${b}…` : "Choosing a pair…"} />
        )}
      </ChartCard>
    </div>
  );
}
