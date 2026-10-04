"use client";

import { catchError } from "next/error";
import { Button } from "@/components/ui/Button";
import { ChartUndrawable } from "./ChartUndrawable";

interface FallbackProps {
  /** The tool's summary, offered instead of the chart. */
  summary?: string;
}

// A lazy chart chunk that failed to load stays failed (React keeps the rejected import), so the
// way back is a fresh page, not a re-render.
function ChartLoadFailed({ summary }: FallbackProps) {
  return (
    <ChartUndrawable
      title="This chart couldn't be loaded."
      summary={summary}
      action={
        <Button variant="secondary" size="sm" onClick={() => window.location.reload()}>
          Reload the page
        </Button>
      }
    />
  );
}

/**
 * Catches a chart chunk that fails to load (or a wrapper that throws while rendering). A chart
 * that throws while drawing is caught inside the draw instead (`useChartRoot`), since that runs
 * in an effect, where no boundary can see it.
 */
export const ChartErrorBoundary = catchError(ChartLoadFailed);
