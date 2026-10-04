"use client";

import { useState } from "react";
import { Chart } from "@/components/charts/Chart";
import { ChartCard } from "@/components/charts/ChartCard";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { ErrorNotice } from "@/components/status/ErrorNotice";
import { Loading } from "@/components/status/Loading";
import { Button } from "@/components/ui/Button";
import { Disclosure } from "@/components/ui/Disclosure";
import { Select } from "@/components/ui/Select";
import { useSessionCatalog } from "@/lib/api/catalog";
import { useTool } from "@/lib/api/tools";
import { eventShort } from "@/lib/format";

export interface LapsHeadToHeadProps {
  year: number;
  a: string;
  b: string;
  /** The weekend in the URL (`laps`): its comparison shows, and the disclosure starts open. */
  laps: string | undefined;
  /** Writes the weekend into the URL (it replaces, never pushes). */
  onLapsChange(event: string): void;
}

/**
 * "Lap by lap: where did one gain on the other?": the pair's fastest clean qualifying laps at one
 * weekend of the season, corner by corner (compare_laps, session Q). The weekend list loads the
 * first time the disclosure opens, so a visit that never opens it makes no extra request. A pair
 * that didn't both drive that qualifying gets the tool's own message, unchanged.
 *
 * The chart card sits under the disclosure rather than inside it, at the column's full width: the
 * disclosure's indent would leave a phone's chart below its 300 px floor. It shows while the
 * disclosure is open.
 */
export function LapsHeadToHead(props: LapsHeadToHeadProps) {
  // Read once: the reader owns the disclosure after that.
  const [startOpen] = useState(props.laps !== undefined);
  const [open, setOpen] = useState(startOpen);
  return (
    <div className="grid gap-4">
      <Disclosure summary="Lap by lap: where did one gain on the other?" defaultOpen={startOpen} onToggle={setOpen}>
        {open ? <LapsForm key={props.year} {...props} /> : null}
      </Disclosure>
      {open && props.laps ? <LapsResult {...props} laps={props.laps} /> : null}
    </div>
  );
}

function LapsForm({ year, a, b, laps, onLapsChange }: LapsHeadToHeadProps) {
  const catalog = useSessionCatalog();
  const season = catalog.status === "ok" ? catalog.data.seasons.find((s) => s.year === year) : undefined;
  const weekends = season?.weekends.filter((w) => w.sessions.some((s) => s.code === "Q")) ?? [];
  const [draft, setDraft] = useState<string | null>(null);
  const fallback = laps !== undefined && weekends.some((w) => w.event === laps) ? laps : (weekends[0]?.event ?? "");
  const event = draft ?? fallback;

  return (
    <div className="grid gap-3">
      <p className="text-[15px]/[1.6] text-muted">
        {a} and {b}&apos;s fastest clean qualifying laps at one {year} weekend: where each gained, turn by turn.
      </p>
      {catalog.status === "error" ? (
        <ErrorNotice error={catalog.error} onRetry={catalog.retry} />
      ) : catalog.status !== "ok" ? (
        <Loading label="Listing the weekends…" />
      ) : weekends.length === 0 ? (
        <p className="text-sm text-muted">No qualifying session of {year} is processed.</p>
      ) : (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (event) onLapsChange(event);
          }}
        >
          <Select
            label="Weekend"
            value={event}
            onChange={setDraft}
            options={weekends.map((w) => ({ value: w.event, label: `Round ${w.round} · ${w.event}` }))}
            className="min-w-0 flex-[1_1_15rem]"
          />
          <Button type="submit" variant="secondary">
            Compare qualifying laps
          </Button>
        </form>
      )}
    </div>
  );
}

function LapsResult({ year, a, b, laps }: LapsHeadToHeadProps & { laps: string }) {
  const tool = useTool("compare_laps", { event: laps, driver_a: a, driver_b: b, year, session: "Q" });
  const shown = tool.status === "ok" ? tool.data : tool.status === "loading" ? tool.stale : undefined;
  const title = `${a} and ${b}, ${eventShort(laps)} qualifying`;

  if (tool.status === "error") {
    return (
      <section aria-label={title}>
        <ErrorNotice error={tool.error} onRetry={tool.retry} />
      </section>
    );
  }
  const data = tool.status === "ok" ? tool.data.data : null;
  return (
    <ChartCard
      level={3}
      title={title}
      subtitle={data ? `${data.year} · ${data.location} · fastest clean laps` : `Comparing ${a} and ${b}'s qualifying laps…`}
      status={tool.status === "ok" ? "ready" : shown ? "stale" : "loading"}
      textVersion={shown ? { label: "Text version", text: shown.summary } : undefined}
    >
      {shown ? (
        <Chart bundle="compare-laps" data={shown.data} label={`Qualifying laps of ${a} and ${b}, ${year} ${laps}`} />
      ) : (
        <ChartSkeleton name="compare-laps" label={`Comparing ${a} and ${b}'s qualifying laps…`} />
      )}
    </ChartCard>
  );
}
