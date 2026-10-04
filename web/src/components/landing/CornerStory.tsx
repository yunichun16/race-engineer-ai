"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { CornerChart } from "@/charts/explain-corner";
import { LANDING_CORNER } from "@/content/featured";
import { ExampleCorner } from "./ExampleCorner";
import { startFeaturedCorner } from "./featured-corner";
import { SECTION, SectionHead } from "./SectionHead";
import { ILLUSTRATION, phasePhrase, storyGeometry, storyHeadline, tracesFrom, worstPhase, type StoryTraces } from "./story";
import { useFeaturedCorner, useHasObserver } from "./useFeaturedCorner";

// The band of the viewport that picks the active step: from halfway down to six tenths of the way
// down (an IntersectionObserver root margin: top and bottom insets, in percent of the viewport).
const BAND_TOP = 50;
const BAND_BOTTOM = 40;
const STEP_BAND = `-${BAND_TOP}% 0px -${BAND_BOTTOM}% 0px`;

// The story starts the example's load when it comes within this distance of the viewport.
const NEAR = "400px 0px";

interface Step {
  title: string;
  figure?: string; // step 3's time lost, from the payload only
  body: ReactNode;
}

/** The four steps' words: from the payload when it has arrived, else plain copy with no numbers. */
function stepsFor(data: CornerChart | null, traces: StoryTraces): Step[] {
  const { driver, lap, turn } = LANDING_CORNER.args;
  const field = data?.reference.kind === "field";
  const whose = field ? "the field's" : "the driver's own";
  const usualTitle = field ? `The usual way through turn ${data?.turn ?? turn}` : `${data?.driver ?? driver}'s usual way through turn ${data?.turn ?? turn}`;
  const laps = field ? "the other drivers' clean laps" : "the driver's other clean laps";
  const usualBody = traces.lo
    ? `The grey band is the middle half of ${laps} through the same corner in that session; the dashed line runs through their middle.`
    : `The dashed line runs through the middle of ${laps} through the same corner in that session.`;
  const worst = data ? worstPhase(data.phases) : null;
  const lost = data?.time_lost_s ?? null;
  return [
    { title: usualTitle, body: usualBody },
    {
      title: `Lap ${data?.lap_number ?? lap}, in blue`,
      body: data
        ? data.explanation
        : "The blue line is the flagged lap. Where it runs under the band, the car was slower through the corner than the driver's usual.",
    },
    {
      title: "Where the time went",
      figure: lost !== null && lost > 0 ? `${lost.toFixed(2)} s` : undefined,
      body: `The shaded stretch is where the time went against ${whose} usual time${worst ? `, mostly ${phasePhrase(worst.name)}` : ""}.`,
    },
    {
      title: "Then the real chart takes over",
      body: "Below: the traces, a map and a replay of the cars around the driver, with what they did differently in plain words.",
    },
  ];
}

/** Which layers each step shows: step 1 the usual, then the lap, then the time, then the markers. */
const LAYERS_SHOWN = [1, 2, 3, 4];

/**
 * "One flagged corner, explained": a scroll story over one drawing, then the real chart. The
 * drawing sticks while four steps scroll past it, and each step turns on one more layer: the
 * driver's usual, the flagged lap, where the time went, and the slowest point. Until the
 * featured corner arrives (and when it can't), the drawing is a built-in illustration and the
 * steps carry no numbers; when it arrives, the paths cross-fade to the real traces.
 */
export function CornerStory() {
  const state = useFeaturedCorner();
  const payload = state.status === "ready" ? state.payload : null;
  const data = payload?.data ?? null;
  const traces = useMemo(() => (data ? tracesFrom(data) : ILLUSTRATION), [data]);
  const geometry = useMemo(() => storyGeometry(traces), [traces]);
  const steps = stepsFor(data, traces);
  const source = payload ? payload.source : "illustration";

  const hasObserver = useHasObserver();
  const [picked, setPicked] = useState(0);
  // Without IntersectionObserver every step reads as active and the drawing is complete.
  const active = hasObserver ? picked : steps.length - 1;
  const shown = LAYERS_SHOWN[active];

  const section = useRef<HTMLElement>(null);
  const stepNodes = useRef<(HTMLDivElement | null)[]>([]);

  // Start the example's load when the story nears the viewport (if the idle start hasn't yet).
  useEffect(() => {
    const node = section.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      startFeaturedCorner();
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          startFeaturedCorner();
          observer.disconnect();
        }
      },
      { rootMargin: NEAR },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // The active step is the one crossing the band in the middle of the viewport, both ways.
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const nodes = stepNodes.current.filter((n): n is HTMLDivElement => n !== null);
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const index = Number((entry.target as HTMLElement).dataset.step);
          if (Number.isInteger(index)) setPicked(index);
        }
      },
      { rootMargin: STEP_BAND },
    );
    nodes.forEach((n) => observer.observe(n));
    return () => observer.disconnect();
  }, []);

  const { lap } = LANDING_CORNER.args;
  const label = data
    ? `Speed through turn ${data.turn} on lap ${data.lap_number} against ${data.driver}'s usual range, with the stretch where the time went`
    : "An illustration of speed through a corner: the driver's usual range, the flagged lap under it, and the stretch where the time went";
  const layer = (n: number) => ({
    className: "transition-opacity duration-(--dur-step) ease-(--ease-out)",
    style: { opacity: shown >= n ? 1 : 0 },
  });
  // Keyed on the source, so the real traces fade in over the illustration's.
  const fresh = "animate-[fade-in_0.3s_var(--ease-out)_forwards] opacity-0";

  return (
    <section ref={section} aria-labelledby="story-title" className={SECTION}>
      <SectionHead id="story-title" label="One flagged corner, explained">
        {storyHeadline(data, LANDING_CORNER.name)}
      </SectionHead>

      <div className="grid gap-6 min-[960px]:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] min-[960px]:gap-10">
        <figure className="glass sticky top-[calc(var(--header-h)+8px)] z-2 self-start rounded-panel p-3.5 pb-3 max-[959px]:bg-glass-strong min-[960px]:top-[calc(var(--header-h)+40px)] min-[960px]:p-5.5">
          <svg viewBox="0 0 420 240" role="img" aria-label={label} className="block h-auto w-full">
            <line x1={0} y1={214} x2={420} y2={214} stroke="var(--color-border-tertiary)" />
            {geometry.apexX !== null ? (
              <>
                <line x1={geometry.apexX} x2={geometry.apexX} y1={8} y2={222} stroke="var(--color-border-secondary)" strokeDasharray="3 5" />
                <text x={geometry.apexX + 6} y={22} fill="var(--color-text-secondary)" className="font-mono text-[11px] font-medium tracking-[0.1em]">
                  APEX
                </text>
              </>
            ) : null}
            <g {...layer(1)}>
              {geometry.band ? <path key={`band-${source}`} className={fresh} d={geometry.band} fill="var(--fg-muted)" fillOpacity={0.2} /> : null}
              <path
                key={`usual-${source}`}
                className={fresh}
                d={geometry.usual}
                fill="none"
                stroke="var(--fg-muted)"
                strokeWidth={1.5}
                strokeDasharray="5 4"
              />
            </g>
            <g {...layer(3)}>
              {geometry.stretch ? (
                <rect key={`stretch-${source}`} className={fresh} x={geometry.stretch.x} width={geometry.stretch.width} y={8} height={206} fill="var(--color-text-danger)" fillOpacity={0.14} />
              ) : null}
            </g>
            <g {...layer(2)}>
              <path key={`lap-${source}`} className={fresh} d={geometry.lap} fill="none" stroke="var(--a)" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
            </g>
            <g {...layer(4)}>
              {geometry.dot && geometry.label ? (
                <g key={`dot-${source}`} className={fresh}>
                  <circle cx={geometry.dot.x} cy={geometry.dot.y} r={6} fill="var(--a)" stroke="var(--color-background-primary)" strokeWidth={2} />
                  <text x={geometry.label.x} y={geometry.label.y} textAnchor={geometry.label.anchor} fill="var(--color-text-primary)" className="font-mono text-[11px] font-semibold">
                    this lap
                  </text>
                </g>
              ) : null}
            </g>
          </svg>
          {/* The legend, then where the drawing comes from. On a phone the source keeps room for two
              lines, so the drawing doesn't grow (and push the steps down) when the data arrives. */}
          <figcaption className="mt-2 grid gap-1">
            <span className="micro flex flex-wrap gap-x-3.5 gap-y-1">
              <span className="inline-flex items-center gap-1.5">
                <i aria-hidden="true" className="inline-block h-[3px] w-3.5 rounded-sm bg-muted opacity-50" />
                Usual
              </span>
              <span className="inline-flex items-center gap-1.5">
                <i aria-hidden="true" className="inline-block h-[3px] w-3.5 rounded-sm bg-series-a" />
                Lap {data?.lap_number ?? lap}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <i aria-hidden="true" className="inline-block h-[3px] w-3.5 rounded-sm bg-danger opacity-60" />
                Time lost
              </span>
            </span>
            <span className="micro max-[959px]:min-h-[3em]">
              {data ? `${data.driver} · lap ${data.lap_number} · T${data.turn} · from explain_corner` : "Illustration"}
            </span>
          </figcaption>
        </figure>

        <div className="grid">
          {steps.map((step, i) => {
            const on = hasObserver ? i === active : true;
            return (
              <div
                key={i}
                ref={(node) => {
                  stepNodes.current[i] = node;
                }}
                data-step={i}
                className="flex min-h-[58vh] flex-col justify-center gap-2.5 py-6 min-[960px]:min-h-[70vh]"
              >
                <p
                  className={`font-mono text-xs font-medium tracking-[0.1em] transition-colors duration-(--dur-step) ease-(--ease-out) ${on ? "text-accent-text" : "text-muted"}`}
                >
                  {String(i + 1).padStart(2, "0")}
                </p>
                {step.figure ? <p className="text-result-xl text-danger">{step.figure}</p> : null}
                <h3 className={`font-serif text-h3 transition-colors duration-(--dur-step) ease-(--ease-out) ${on ? "text-fg" : "text-muted"}`}>
                  {step.title}
                </h3>
                <p className="max-w-[30em] text-base/[1.7] text-muted">{step.body}</p>
              </div>
            );
          })}
        </div>
      </div>

      <ExampleCorner />
    </section>
  );
}
