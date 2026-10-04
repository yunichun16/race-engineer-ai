"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { ChartCard } from "@/components/charts/ChartCard";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { Reveal } from "@/components/motion/Reveal";
import { LinkButton } from "@/components/ui/LinkButton";
import { featuredHref, LANDING_CORNER, typeTag } from "@/content/featured";
import { sessionName } from "@/lib/format";
import { useFeaturedCorner } from "./useFeaturedCorner";

const LOADING_LABEL = "Loading the corner chart…";

// The chart's code loads only when the example has data to draw, behind a skeleton of its height.
const ExplainCornerChart = dynamic(() => import("@/components/charts/ExplainCornerChart").then((m) => m.ExplainCornerChart), {
  ssr: false,
  loading: () => <ChartSkeleton name="explain-corner" label={LOADING_LABEL} />,
});

/** "Over-slowing" for "over-slowing". */
function capitalised(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * The hand-off from the story to the real chart: the featured corner drawn by the same
 * explain-corner chart as the explorer, with its traces, map and replay (which plays once when it
 * comes into view, never with reduced motion). Its words come from the tool's answer. Without
 * the answer (no snapshot and no server) the card keeps the chart's height and says, without
 * numbers, what it would show. No reviewer sentence, here or anywhere (plan 13).
 */
export function ExampleCorner() {
  const state = useFeaturedCorner();
  const payload = state.status === "ready" ? state.payload : null;
  const data = payload?.data ?? null;
  const { driver, event, year, session, lap, turn } = LANDING_CORNER.args;

  const title = data
    ? `${data.driver} · ${data.year} ${data.event} · ${data.session} · lap ${data.lap_number}, turn ${data.turn}`
    : `${driver} · ${year} ${event} · ${sessionName(session)} · lap ${lap}, turn ${turn}`;
  const chartLabel = `Corner chart: ${data?.driver ?? driver}, lap ${data?.lap_number ?? lap}, turn ${data?.turn ?? turn}, ${year} ${event} ${sessionName(session).toLowerCase()}`;
  const typeText = data?.type_text || LANDING_CORNER.typeLabel || "flagged";
  const missing = state.status === "missing";
  // What the chart would show, in words and without numbers, for when it can't load.
  const offline =
    `The live example couldn't load. Here's what it shows: ${title}. ${typeTag(typeText)}, against the driver's ` +
    "usual way through the corner, with the traces, a map and a replay of the cars around them.";

  return (
    <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,4fr)_minmax(0,8fr)] lg:items-start lg:gap-8">
      <div className="lg:sticky lg:top-[calc(var(--header-h)+40px)]">
        <Reveal className="grid content-start gap-3">
          <p className="micro">The real chart</p>
          <p className="text-title text-fg">{title}</p>
          {/* Three lines kept for the body, the blurb's or the payload's, so the chart below doesn't
              move when the data arrives. */}
          {data && data.time_lost_s !== null && data.time_lost_s > 0 ? (
            <p className="min-h-[5.1em] text-base/[1.7] text-muted">
              {capitalised(typeText)}:{" "}
              <span className="font-medium text-fg tabular-nums">{data.time_lost_s.toFixed(2)}&nbsp;s</span> lost against the
              driver&apos;s usual.
            </p>
          ) : (
            <p className="min-h-[5.1em] text-base/[1.7] text-muted">{LANDING_CORNER.blurb}</p>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <LinkButton href={featuredHref(LANDING_CORNER)} variant="secondary" size="sm">
              Open in the explorer <span aria-hidden="true">→</span>
            </LinkButton>
            <Link href="/mistakes#featured" className="link inline-flex min-h-11 items-center">
              More flagged corners&nbsp;<span aria-hidden="true">→</span>
            </Link>
          </div>
        </Reveal>
      </div>

      <ChartCard
        level={3}
        title={`Turn ${data?.turn ?? turn}, lap ${data?.lap_number ?? lap}`}
        status={state.status === "idle" || state.status === "loading" ? "loading" : "ready"}
        // Always there, so the card's height doesn't change when the chart (or the note) arrives.
        textVersion={{ label: "Text version", text: payload ? payload.summary : missing ? offline : "Loading the tool's summary of this corner…" }}
      >
        {payload ? (
          <ExplainCornerChart data={payload.data} autoplay headingLevel={4} label={chartLabel} />
        ) : missing ? (
          // The note sits over an invisible skeleton, so it keeps the chart's room and nothing
          // below moves when the load gives up.
          <div className="relative">
            <div aria-hidden="true" className="invisible">
              <ChartSkeleton name="explain-corner" label="" />
            </div>
            <p className="absolute inset-x-0 top-0 max-w-[60ch] px-4 py-3 text-base/[1.7] text-muted">{offline}</p>
          </div>
        ) : (
          <ChartSkeleton name="explain-corner" label={LOADING_LABEL} />
        )}
      </ChartCard>
    </div>
  );
}
