"use client";

import { useSearchParams } from "next/navigation";
import { StrictMode, Suspense, use, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { flushSync } from "react-dom";
import type { Mistake } from "@/charts/find-mistakes";
import { Chart } from "@/components/charts/Chart";
import { ChartCard } from "@/components/charts/ChartCard";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { CHART_NAMES } from "@/components/charts/chart-names";
import type { MistakeSelection } from "@/components/charts/types";
import { ErrorNotice } from "@/components/status/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { ApiError } from "@/lib/api/client";
import { applyTheme, THEMES } from "@/lib/theme";
import { intParam, pickParam, readQuery, setQuery } from "@/lib/url";
import { FIXTURES, fixturePromise, fixtureText, type FixtureEntry } from "./fixtures";
import {
  installProbe,
  probeVersion,
  readCounts,
  readDraws,
  sameCounts,
  subscribeProbe,
  totalDraws,
  type Counts,
} from "./lifecycle";

// Before any chart code loads (the charts are lazy), so the counters see everything they start.
if (typeof window !== "undefined") installProbe();

const WIDTHS = ["full", "760", "640", "600", "560", "480", "420", "360", "320"];
const REMOUNTS = 20;

type Width = "full" | number;

function parseWidth(text: string | null): Width {
  if (text === null || text === "full") return "full";
  return intParam(text, 240, 1600) ?? "full";
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Change some of the gallery's query keys, keeping the others (no history entry). */
function updateQuery(changes: Record<string, string | undefined>): void {
  setQuery({ ...readQuery(window.location.search), ...changes }, { push: false });
}

function argsText(args: Record<string, unknown> | undefined): string {
  return Object.entries(args ?? {})
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
}

interface RemountResult {
  before: Counts;
  after: Counts;
  charts: number;
  draws: number;
}

export function Gallery() {
  const params = useSearchParams();
  const chart = pickParam(params.get("chart"), CHART_NAMES);
  const fixture = params.get("fixture");
  const width = parseWidth(params.get("width"));
  const count = intParam(params.get("count"), 1, 3) ?? 1;
  const theme = pickParam(params.get("theme"), THEMES);
  const autoplay = params.get("autoplay") === "1";
  const checks = params.get("checks") === "1";
  // ?glass=off: the cards opaque on a flat page, as a chart sits in Claude's frame. Otherwise the
  // real surface: the translucent glass panel over the page's ambient light.
  const glass = params.get("glass") !== "off";

  const entries = FIXTURES.filter((e) => (!chart || e.chart === chart) && (!fixture || e.name === fixture));
  const items = entries.flatMap((entry) =>
    Array.from({ length: count }, (_, i) => ({ entry, key: count > 1 ? `${entry.name}#${i + 1}` : entry.name })),
  );
  const drawable = items.filter((item) => !item.entry.error).length;

  const [mounted, setMounted] = useState(true);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Record<string, MistakeSelection | null>>({});
  const [log, setLog] = useState<string[]>([]);
  const [remount, setRemount] = useState<RemountResult | null>(null);
  const [check, setCheck] = useState<string | null>(null);

  // ?theme= applies that theme while the gallery is open, without saving it.
  useEffect(() => {
    if (!theme) return;
    const root = document.documentElement;
    const before = root.getAttribute("data-theme");
    applyTheme(theme);
    return () => {
      if (before === null) root.removeAttribute("data-theme");
      else root.setAttribute("data-theme", before);
    };
  }, [theme]);

  const note = (text: string) => setLog((entries) => [text, ...entries].slice(0, 8));

  const explain = (key: string, m: Mistake) => {
    note(`${key}: Show telemetry for ${m.driver} lap ${m.lap_number} turn ${m.turn}`);
    setSelected((all) => ({ ...all, [key]: { driver: m.driver, lap_number: m.lap_number, turn: m.turn } }));
  };

  // Mount and unmount every chart REMOUNTS times; the counters must come back to where they were.
  const remountAll = async () => {
    setBusy(true);
    setRemount(null);
    await wait(300);
    const before = readCounts();
    const drawsBefore = totalDraws();
    for (let i = 0; i < REMOUNTS; i++) {
      flushSync(() => setMounted(false));
      await wait(0);
      flushSync(() => setMounted(true));
      await wait(0);
    }
    await wait(500);
    const after = readCounts();
    if (before && after) setRemount({ before, after, charts: drawable, draws: totalDraws() - drawsBefore });
    setBusy(false);
  };

  // Change the frame width once: every chart should redraw exactly once.
  const widthCheck = async () => {
    setBusy(true);
    const next = width === 480 ? "560" : "480";
    const before = new Map(items.map((item) => [item.key, readDraws(item.key)]));
    updateQuery({ width: next });
    await wait(600);
    const deltas = items.filter((item) => !item.entry.error).map((item) => readDraws(item.key) - (before.get(item.key) ?? 0));
    updateQuery({ width: width === "full" ? undefined : String(width) });
    await wait(600);
    const once = deltas.filter((d) => d === 1).length;
    setCheck(`Width ${width} → ${next}: ${once} of ${deltas.length} charts redrew exactly once (redraws: ${deltas.join(", ")}).`);
    setBusy(false);
  };

  // Switch the theme: no chart should redraw.
  const themeCheck = async () => {
    setBusy(true);
    const root = document.documentElement;
    const before = root.getAttribute("data-theme");
    const drawsBefore = totalDraws();
    const fg = () => getComputedStyle(document.querySelector(".re-chart") ?? root).color;
    const colourBefore = fg();
    applyTheme(before === "dark" ? "light" : "dark");
    await wait(600);
    const colourAfter = fg();
    const redraws = totalDraws() - drawsBefore;
    if (before === null) root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", before);
    setCheck(`Theme switched: ${redraws} redraws; chart text colour ${colourBefore} → ${colourAfter}.`);
    setBusy(false);
  };

  // A trusted key press is better (the browser's own), but this shows whether anything listens.
  const pressP = () => {
    const buttons = [...document.querySelectorAll<HTMLButtonElement>(".re-chart--explain-corner button.play")];
    const playing = () => buttons.filter((b) => b.getAttribute("aria-label") === "Pause the replay").length;
    const before = playing();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "p", bubbles: true }));
    const after = playing();
    setCheck(`P pressed on the page: ${buttons.length} replays, playing before ${before}, after ${after}.`);
  };

  return (
    <StrictMode>
      {/* Covers the ambient light (fixed at z-index -1) with the flat page colour. */}
      {glass ? null : <div aria-hidden="true" className="fixed inset-0 -z-1 bg-page" />}
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Select
          label="Chart"
          value={chart ?? ""}
          onChange={(value) => updateQuery({ chart: value || undefined, fixture: undefined })}
          options={[{ value: "", label: "All charts" }, ...CHART_NAMES.map((name) => ({ value: name, label: name }))]}
        />
        <Select
          label="Fixture"
          value={fixture ?? ""}
          onChange={(value) => updateQuery({ fixture: value || undefined })}
          options={[
            { value: "", label: "All fixtures" },
            ...FIXTURES.filter((e) => !chart || e.chart === chart).map((e) => ({ value: e.name, label: e.name })),
          ]}
        />
        <Select
          label="Frame width"
          value={String(width)}
          onChange={(value) => updateQuery({ width: value === "full" ? undefined : value })}
          options={[...new Set([...WIDTHS, String(width)])].map((w) => ({ value: w, label: w === "full" ? "Full" : `${w} px` }))}
        />
        <Select
          label="Copies"
          value={String(count)}
          onChange={(value) => updateQuery({ count: value === "1" ? undefined : value })}
          options={["1", "2", "3"].map((n) => ({ value: n, label: n }))}
        />
        <Select
          label="Theme"
          value={theme ?? ""}
          onChange={(value) => updateQuery({ theme: value || undefined })}
          options={[
            { value: "", label: "As the site" },
            ...THEMES.map((t) => ({ value: t, label: t[0].toUpperCase() + t.slice(1) })),
          ]}
        />
        <Select
          label="Glass"
          value={glass ? "" : "off"}
          onChange={(value) => updateQuery({ glass: value || undefined })}
          options={[
            { value: "", label: "On, as the site" },
            { value: "off", label: "Off, opaque" },
          ]}
        />
        <Select
          label="Dispatcher checks"
          value={checks ? "1" : ""}
          onChange={(value) => updateQuery({ checks: value || undefined })}
          options={[
            { value: "", label: "Hidden" },
            { value: "1", label: "Shown" },
          ]}
        />
        <Select
          label="Replay autoplay"
          value={autoplay ? "1" : ""}
          onChange={(value) => updateQuery({ autoplay: value || undefined })}
          options={[
            { value: "", label: "Off" },
            { value: "1", label: "On" },
          ]}
        />
      </div>

      <LifecyclePanel
        busy={busy}
        mounted={mounted}
        onToggleMounted={() => setMounted((m) => !m)}
        onRemount={remountAll}
        onWidthCheck={widthCheck}
        onThemeCheck={themeCheck}
        onPressP={pressP}
        remount={remount}
        check={check}
        log={log}
      />

      <h2 className="mt-10 text-xl font-semibold text-fg">
        Charts <span className="text-base font-normal text-muted">({entries.length} fixtures)</span>
      </h2>
      <ul className={["mt-4 flex flex-col gap-8", glass ? "" : "[&_.glass-panel]:bg-(--glass-solid)"].filter(Boolean).join(" ")}>
        {items.map(({ entry, key }) => (
          <li key={key} data-gallery-item={key}>
            <Suspense fallback={<ChartSkeleton name={entry.chart} label={`Loading ${entry.name}`} />}>
              <GalleryItem
                entry={entry}
                itemKey={key}
                width={width}
                mounted={mounted}
                autoplay={autoplay}
                selected={selected[key] ?? null}
                onExplain={(m) => explain(key, m)}
                note={note}
              />
            </Suspense>
          </li>
        ))}
      </ul>

      {checks ? <DispatcherChecks width={width} /> : null}
    </StrictMode>
  );
}

interface GalleryItemProps {
  entry: FixtureEntry;
  itemKey: string;
  width: Width;
  mounted: boolean;
  autoplay: boolean;
  selected: MistakeSelection | null;
  onExplain: (mistake: Mistake) => void;
  note: (text: string) => void;
}

function GalleryItem({ entry, itemKey, width, mounted, autoplay, selected, onExplain, note }: GalleryItemProps) {
  const fixture = use(fixturePromise(entry));
  const text = fixtureText(fixture);
  const draws = useSyncExternalStore(subscribeProbe, () => readDraws(itemKey), () => 0);

  let body: ReactNode;
  if (fixture.isError) {
    // As the site shows the same failure from the REST API.
    const { status, code, tool } = entry.error ?? { status: 422, code: "invalid_input", tool: undefined };
    // Padded, since the card's chart area has none.
    body = (
      <div className="px-4 pt-3 pb-4">
        <ErrorNotice error={new ApiError(status, code, text, tool)} />
      </div>
    );
  } else if (!mounted) {
    // With the data, as the dispatcher shows it while the chart's code loads.
    body = <ChartSkeleton name={entry.chart} label="Unmounted" data={fixture.structuredContent} />;
  } else {
    body = (
      <Chart
        bundle={entry.chart}
        data={fixture.structuredContent}
        summary={text}
        label={`${entry.name} (${entry.chart})`}
        headingLevel={4} // under the card's h3 title
        onExplain={onExplain}
        selected={selected}
        autoplay={autoplay}
        onKindChange={(kind) => note(`${itemKey}: kind ${kind}`)}
        onPlaneChange={(plane) => note(`${itemKey}: plane ${plane}`)}
        onPointSelect={(driver) => note(`${itemKey}: point ${driver}`)}
      />
    );
  }

  return (
    <ChartCard
      level={3}
      title={itemKey}
      subtitle={`${entry.chart}${fixture.arguments ? ` · ${argsText(fixture.arguments)}` : ""}`}
      actions={fixture.isError ? null : <span className="text-sm text-muted tabular-nums">draws {draws}</span>}
      textVersion={{ label: "Text version", text }}
    >
      <FrameWidth width={width}>{body}</FrameWidth>
    </ChartCard>
  );
}

/** The chart frame at a set width (for the breakpoints and the dev host comparison), or full. */
function FrameWidth({ width, children }: { width: Width; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <div style={width === "full" ? undefined : { width }}>{children}</div>
    </div>
  );
}

const ROW = "flex flex-wrap items-baseline justify-between gap-x-4 border-b border-line py-1.5 last:border-b-0";

interface LifecyclePanelProps {
  busy: boolean;
  mounted: boolean;
  onToggleMounted: () => void;
  onRemount: () => void;
  onWidthCheck: () => void;
  onThemeCheck: () => void;
  onPressP: () => void;
  remount: RemountResult | null;
  check: string | null;
  log: string[];
}

function LifecyclePanel(props: LifecyclePanelProps) {
  const { busy, mounted, remount, check, log } = props;
  // -1 on the server and while hydrating, so the first client render matches the server's.
  const version = useSyncExternalStore(subscribeProbe, probeVersion, () => -1);
  const counts = version < 0 ? null : readCounts();
  const types = counts
    ? Object.entries(counts.listenerTypes)
        .map(([type, n]) => `${type} ${n}`)
        .join(", ")
    : "";

  return (
    <Card as="section" className="mt-8">
      <h2 className="text-xl font-semibold text-fg">Lifecycle</h2>
      <p className="mt-1 text-sm text-muted">
        Live now, page-wide (Next.js and React count too, so compare against a baseline). Under StrictMode, so each
        mount draws twice.
      </p>
      <dl className="mt-3 text-sm">
        <div className={ROW}>
          <dt className="text-muted">Document listeners</dt>
          <dd className="text-fg tabular-nums">
            {counts ? `${counts.listeners}${types ? ` (${types})` : ""}` : "–"}
          </dd>
        </div>
        <div className={ROW}>
          <dt className="text-muted">ResizeObservers</dt>
          <dd className="text-fg tabular-nums">{counts?.resizeObservers ?? "–"}</dd>
        </div>
        <div className={ROW}>
          <dt className="text-muted">IntersectionObservers</dt>
          <dd className="text-fg tabular-nums">{counts?.intersectionObservers ?? "–"}</dd>
        </div>
        <div className={ROW}>
          <dt className="text-muted">Animation frames pending</dt>
          <dd className="text-fg tabular-nums">{counts?.frames ?? "–"}</dd>
        </div>
        <div className={ROW}>
          <dt className="text-muted">Chart draws since load</dt>
          <dd className="text-fg tabular-nums">{counts ? totalDraws() : "–"}</dd>
        </div>
      </dl>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" size="sm" disabled={busy} onClick={props.onRemount}>
          Remount {REMOUNTS}×
        </Button>
        <Button variant="secondary" size="sm" disabled={busy} onClick={props.onWidthCheck}>
          Width check
        </Button>
        <Button variant="secondary" size="sm" disabled={busy} onClick={props.onThemeCheck}>
          Theme check
        </Button>
        <Button variant="secondary" size="sm" disabled={busy} onClick={props.onPressP}>
          Press P
        </Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={props.onToggleMounted}>
          {mounted ? "Unmount charts" : "Mount charts"}
        </Button>
      </div>
      <div role="status" className="mt-3 text-sm text-fg">
        {busy ? <p>Running…</p> : null}
        {remount ? <RemountReport result={remount} /> : null}
        {check ? <p className="mt-1">{check}</p> : null}
      </div>
      {log.length > 0 ? (
        <div className="mt-4">
          <h3 className="text-sm font-semibold text-fg">Events from the charts</h3>
          <ol className="mt-1 list-decimal pl-5 text-sm text-muted">
            {log.map((entry, i) => (
              <li key={`${i}-${entry}`}>{entry}</li>
            ))}
          </ol>
        </div>
      ) : null}
    </Card>
  );
}

function describe(c: Counts): string {
  return `listeners ${c.listeners}, ResizeObservers ${c.resizeObservers}, IntersectionObservers ${c.intersectionObservers}, frames ${c.frames}`;
}

function RemountReport({ result }: { result: RemountResult }) {
  const pass = sameCounts(result.before, result.after);
  const perMount = result.charts ? result.draws / (result.charts * REMOUNTS) : 0;
  return (
    <div>
      <p className="font-semibold">
        {pass ? "Pass" : "Fail"}: after {REMOUNTS} remounts the counters {pass ? "are back at" : "differ from"} the
        baseline.
      </p>
      <p className="text-muted">Before: {describe(result.before)}.</p>
      <p className="text-muted">After: {describe(result.after)}.</p>
      <p className="text-muted">
        {result.draws} draws for {result.charts} charts: {perMount} per mount.
      </p>
    </div>
  );
}

const SAMPLE_SUMMARY =
  "Sample lap (synthetic test data, 5.0 km, not a real lap): a summary standing in for a chart the site has no wrapper for.";

// Passes compare-laps' type guard (two drivers), then breaks its drawing (a driver is null).
const BREAKS_DRAWING = { distance_m: [], delta_s: [], drivers: [null, null] };

/**
 * The fallbacks (?checks=1): data the chart's check refuses, data that passes the check but
 * breaks the drawing (caught in useChartRoot, which logs the error to the console, twice under
 * StrictMode), and a bundle the site doesn't draw. Off by default, so the gallery's console stays
 * clean.
 */
function DispatcherChecks({ width }: { width: Width }) {
  const cases: { key: string; bundle: string; data: unknown; title: string }[] = [
    { key: "guard-failure", bundle: "find-mistakes", data: { event: 1 }, title: "find-mistakes given data it can't take" },
    {
      key: "draw-failure",
      bundle: "compare-laps",
      data: BREAKS_DRAWING,
      title: "compare-laps given data that passes its check but breaks the drawing",
    },
    { key: "unknown-bundle", bundle: "telemetry", data: null, title: "a bundle the site has no chart for (telemetry)" },
  ];
  return (
    <>
      <h2 className="mt-10 text-xl font-semibold text-fg">Dispatcher checks</h2>
      <ul className="mt-4 flex flex-col gap-8">
        {cases.map((c) => (
          <li key={c.key} data-gallery-item={c.key}>
            <ChartCard level={3} title={c.title}>
              <FrameWidth width={width}>
                <Chart bundle={c.bundle} data={c.data} summary={SAMPLE_SUMMARY} label={c.title} headingLevel={4} />
              </FrameWidth>
            </ChartCard>
          </li>
        ))}
      </ul>
    </>
  );
}

