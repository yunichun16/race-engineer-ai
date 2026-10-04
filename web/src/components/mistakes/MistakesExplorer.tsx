"use client";

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import type { Mistake } from "@/charts/find-mistakes";
import { ChartCard } from "@/components/charts/ChartCard";
import { ChartErrorBoundary } from "@/components/charts/ChartErrorBoundary";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { ApiDown } from "@/components/status/ApiDown";
import { ErrorNotice } from "@/components/status/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { Skeleton } from "@/components/ui/Skeleton";
import type { FeaturedCorner } from "@/content/featured";
import { useSessionCatalog, useSessionDrivers } from "@/lib/api/catalog";
import type { ApiError } from "@/lib/api/client";
import { describe } from "@/lib/api/describe";
import type { ResourceState } from "@/lib/api/resource";
import { useTool } from "@/lib/api/tools";
import type { SessionRef, ToolResult } from "@/lib/api/types";
import { buildQuery, setQuery } from "@/lib/url";
import { DriverPicker } from "./DriverPicker";
import { ExplainPanel, ExplainPlaceholder } from "./ExplainPanel";
import { FeaturedStrip } from "./FeaturedStrip";
import {
  DEFAULT_LIMIT,
  explainRef,
  explainSelection,
  formatExplain,
  mistakesUpdate,
  parseMistakesQuery,
  serializeMistakesQuery,
  type Limit,
  type MistakesQuery,
} from "./query";
import { RaceContext } from "./RaceContext";
import {
  featuredQuery,
  loadedMessage,
  needsCoverageNote,
  resolveSelection,
  selectionKey,
  selectionSummary,
  sessionPhrase,
  type Selection,
} from "./selection";
import { SelectionBar } from "./SelectionBar";
import { SessionPicker } from "./SessionPicker";
import { SessionSearch } from "./SessionSearch";

const FINDING = "Finding the flagged corners…";

// The list's code loads with the page's first answer, behind a skeleton of its height.
const FindMistakesChart = dynamic(() => import("@/components/charts/FindMistakesChart").then((m) => m.FindMistakesChart), {
  ssr: false,
  loading: () => <ChartSkeleton name="find-mistakes" label={FINDING} />,
});

const NO_VERDICT =
  "Nothing flagged isn't the same as no mistakes: a driver with no scored laps hasn't been checked by the model.";

type FocusTarget = "panel" | "results";

interface FocusRefs {
  panel: RefObject<HTMLElement | null>;
  panelHeading: RefObject<HTMLHeadingElement | null>;
  resultsHeading: RefObject<HTMLHeadingElement | null>;
}

/**
 * Moves focus as plan 6.5 asks: to the open corner's heading (scrolled into view on a phone, or
 * wherever it is out of sight), or back to the list's heading when the corner closes.
 */
function moveFocus(target: FocusTarget, refs: FocusRefs): void {
  if (target === "results") {
    refs.resultsHeading.current?.focus();
    return;
  }
  const heading = refs.panelHeading.current;
  if (!heading) return;
  const wide = window.matchMedia("(min-width: 1024px)").matches;
  const box = heading.getBoundingClientRect();
  if (!wide || box.top < 0 || box.bottom > window.innerHeight) {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    refs.panel.current?.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
  }
  heading.focus({ preventScroll: true });
}

/** The selection when it is a race or a sprint, the sessions with a race summary. */
function raceSelection(sel: Selection | null): (Selection & { session: "R" | "S" }) | null {
  if (!sel) return null;
  const { session } = sel;
  return session === "R" || session === "S" ? { ...sel, session } : null;
}

function unreachable(state: ResourceState<unknown>): boolean {
  return state.status === "error" && state.error.code === "unreachable";
}

interface ResultsErrorProps {
  error: ApiError;
  onRetry(): void;
  /** For a driver the session doesn't have: back to the whole field. */
  onWholeField?: () => void;
}

/** A list that failed, in the states catalogue's words (6.6), with the way on that fits. */
function ResultsError({ error, onRetry, onWholeField }: ResultsErrorProps) {
  if (error.code === "unreachable") return <ApiDown onRetry={onRetry} />;
  const copy = describe(error);
  const actions = (
    <>
      {copy.retry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
      {onWholeField ? (
        <Button variant="secondary" size="sm" onClick={onWholeField}>
          Show the whole field
        </Button>
      ) : null}
    </>
  );
  return (
    <Notice tone={copy.tone} title={copy.title} action={copy.retry || onWholeField ? actions : undefined}>
      {copy.body ? <p className="whitespace-pre-line wrap-anywhere">{copy.body}</p> : null}
    </Notice>
  );
}

interface ResultsProps {
  selection: Selection;
  driver: string | undefined;
  limit: Limit;
  state: ResourceState<ToolResult<"find_mistakes">>;
  selected: ReturnType<typeof explainSelection>;
  onExplain(mistake: Mistake): void;
  onWholeField(): void;
  /** Try again when the server couldn't be reached: everything that failed, not only the list. */
  onRetryAll(): void;
}

/** The list (FindMistakesChart) in its card, or the state it is in. */
function Results({ selection, driver, limit, state, selected, onExplain, onWholeField, onRetryAll }: ResultsProps) {
  if (state.status === "error") {
    const wrongDriver = driver !== undefined && state.error.code === "invalid_input";
    const retry = state.error.code === "unreachable" ? onRetryAll : state.retry;
    return <ResultsError error={state.error} onRetry={retry} onWholeField={wrongDriver ? onWholeField : undefined} />;
  }
  // While a new list loads, the last one stays, dimmed (6.6).
  const shown = state.status === "ok" ? state.data : state.status === "loading" ? state.stale : undefined;
  const title = `Top ${limit}, ranked by time lost`;
  if (!shown) {
    return (
      <ChartCard level={3} title={title} subtitle={FINDING} status="loading">
        <ChartSkeleton name="find-mistakes" label={FINDING} />
      </ChartCard>
    );
  }
  const list = shown.data;
  const empty = list.mistakes.length === 0;
  return (
    <ChartCard
      level={3}
      title={title}
      status={state.status === "ok" ? "ready" : "stale"}
      textVersion={{ label: "Text version", text: shown.summary }}
    >
      <ChartErrorBoundary summary={shown.summary}>
        <FindMistakesChart
          data={list}
          onExplain={onExplain}
          selected={selected}
          headingLevel={4}
          label={`Flagged mistakes in ${sessionPhrase(selection)}${list.driver ? `, ${list.driver}` : ""}`}
        />
      </ChartErrorBoundary>
      {empty || needsCoverageNote(list) ? (
        <div className="grid justify-items-start gap-2 px-4 pb-4 text-sm text-muted">
          {empty ? <p>{list.driver ? "Try the whole field, or another session." : "Try another session."}</p> : null}
          {needsCoverageNote(list) ? <p>{NO_VERDICT}</p> : null}
          {empty && list.driver ? (
            <Button variant="secondary" size="sm" onClick={onWholeField}>
              Show the whole field
            </Button>
          ) : null}
        </div>
      ) : null}
    </ChartCard>
  );
}

/**
 * The mistake explorer (plan 7.3): a session's flagged corners, ranked by time lost, and one
 * corner's telemetry against the driver's usual. Its state is the URL (`query.ts`): a session,
 * a corner or a featured card is a new history entry, so Back closes a corner and Forward opens
 * it again; the driver filter and the row count only replace the entry.
 *
 * The first calls go out together: the session catalog, and, when the link names a session, its
 * drivers and its list (with no link, the catalog picks the session first). The corner loads
 * only when one is opened, the race summary only when Race context opens, and the store cancels
 * a request nobody waits for any more.
 */
export function MistakesExplorer() {
  const params = useSearchParams();
  const search = params.toString();
  const query = useMemo(() => parseMistakesQuery(new URLSearchParams(search)), [search]);

  const catalogState = useSessionCatalog();
  const catalog = catalogState.status === "ok" ? catalogState.data : null;
  const selection = useMemo(() => resolveSelection(query, catalog), [query, catalog]);
  const limit = query.limit ?? DEFAULT_LIMIT;
  // A corner belongs to its session: one from a link whose event the page couldn't show is dropped.
  const explain = selection && query.explain && query.event === selection.event ? query.explain : null;

  const mistakes = useTool(
    "find_mistakes",
    selection
      ? { event: selection.event, year: selection.year, session: selection.session, driver: query.driver, limit }
      : null,
  );
  const drivers = useSessionDrivers(selection);
  const corner = useTool(
    "explain_corner",
    selection && explain
      ? {
          event: selection.event,
          driver: explain.driver,
          lap: explain.lap,
          corner: explain.turn,
          year: selection.year,
          session: selection.session,
        }
      : null,
  );

  // The state the page shows, with the catalog's choices filled in: what every change starts from.
  const current: MistakesQuery = selection
    ? { ...query, year: selection.year, event: selection.event, session: selection.session, explain: explain ?? undefined }
    : query;

  // --- Focus (plan 6.5) -------------------------------------------------------------------------

  const panelRef = useRef<HTMLElement>(null);
  const panelHeadingRef = useRef<HTMLHeadingElement>(null);
  const resultsHeadingRef = useRef<HTMLHeadingElement>(null);
  const refs = useMemo<FocusRefs>(
    () => ({ panel: panelRef, panelHeading: panelHeadingRef, resultsHeading: resultsHeadingRef }),
    [],
  );
  // The URL the page itself last asked for, and where focus goes once it shows (null: stay put).
  // A URL the page didn't ask for is Back, Forward or a link: a corner that opened or closed
  // with it moves focus as a click would. (Next renders a traversal before the window's
  // popstate event reaches a listener, so the event can't tell which renders those are.)
  const expected = useRef<{ query: string; target: FocusTarget | null } | null>(null);
  const shownBefore = useRef<{ query: string; explain: string } | null>(null);

  const shownQuery = buildQuery(serializeMistakesQuery(query));
  const explainKey = explain ? formatExplain(explain) : "";

  useEffect(() => {
    const before = shownBefore.current;
    shownBefore.current = { query: shownQuery, explain: explainKey };
    // The first render, or the catalog filling in the session of the same URL: nothing moves.
    if (!before || before.query === shownQuery) return;
    const asked = expected.current;
    expected.current = null;
    if (asked && asked.query === shownQuery) {
      if (asked.target) moveFocus(asked.target, refs);
    } else if (before.explain !== explainKey) {
      // Back or Forward: the browser restores the entry's scroll position after this render (it
      // follows the popstate event), which would leave the focused heading off screen. So the
      // focus waits for the next task, after the restore.
      const target: FocusTarget = explainKey ? "panel" : "results";
      const timer = window.setTimeout(() => moveFocus(target, refs), 0);
      return () => window.clearTimeout(timer);
    }
  }, [shownQuery, explainKey, refs]);

  // From the landing's "More flagged corners": the strip isn't on the page until it renders here.
  useEffect(() => {
    if (window.location.hash === "#featured") document.getElementById("featured")?.scrollIntoView({ block: "start" });
  }, []);

  // A link that opens a corner (the chat's "Open in Mistakes"): once the corner has loaded, it is
  // brought into view, as an open by click would be, unless the reader has scrolled meanwhile (or
  // the browser restored a position on reload). Focus stays where the page load put it.
  const [linkedCorner] = useState(() => (query.explain ? formatExplain(query.explain) : ""));
  const linkHandled = useRef(false);
  const cornerReady = corner.status === "ok";
  useEffect(() => {
    if (linkHandled.current || !linkedCorner) return;
    if (explainKey !== linkedCorner) {
      // The page dropped the link's corner, or the reader moved on before it loaded.
      if (explainKey) linkHandled.current = true;
      return;
    }
    if (!cornerReady) return;
    linkHandled.current = true;
    if (window.scrollY > 0 || window.location.hash) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    panelRef.current?.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
  }, [linkedCorner, explainKey, cornerReady]);

  // --- Changes --------------------------------------------------------------------------------

  /** Shows `next`, as a new history entry or in place of this one, then moves focus if asked. */
  const update = (next: MistakesQuery, focus?: FocusTarget) => {
    const mode = mistakesUpdate(current, next);
    if (mode === null) {
      if (focus) moveFocus(focus, refs);
      return;
    }
    expected.current = { query: buildQuery(serializeMistakesQuery(next)), target: focus ?? null };
    setQuery(serializeMistakesQuery(next), { push: mode === "push" });
  };

  const onSelect = (sel: Selection, keepDriver: boolean) =>
    update({ ...sel, driver: keepDriver ? query.driver : undefined, limit: query.limit });
  const onDriver = (driver: string | undefined) => update({ ...current, driver });
  const onLimit = (n: Limit) => update({ ...current, limit: n });
  const onFound = (s: SessionRef) => update({ year: s.year, event: s.event, session: s.session_code, limit: query.limit });
  const onExplain = (m: Mistake) => {
    const ref = explainRef(m.driver, m.lap_number, m.turn);
    if (ref) update({ ...current, explain: ref }, "panel");
  };
  const onClose = () => update({ ...current, explain: undefined }, "results");
  const onFeatured = (f: FeaturedCorner) => {
    const next = { ...featuredQuery(f), limit: query.limit };
    update(next, next.explain ? "panel" : "results");
  };
  // Try again on a "Can't reach the analysis server" notice: everything that failed, so one
  // press brings back the list, the pickers' driver list and the open corner together.
  const retryAll = () => {
    if (catalogState.status === "error") catalogState.retry();
    if (mistakes.status === "error") mistakes.retry();
    if (drivers.status === "error") drivers.retry();
    if (corner.status === "error") corner.retry();
  };

  // --- What shows -------------------------------------------------------------------------------

  const pickers = (instance: string, layout: "row" | "stack"): ReactNode =>
    selection ? (
      <>
        <SessionPicker catalog={catalog} selection={selection} onSelect={onSelect} instance={instance} layout={layout} />
        <DriverPicker drivers={drivers} driver={query.driver} limit={limit} onDriver={onDriver} onLimit={onLimit} layout={layout} />
      </>
    ) : catalogState.status === "error" ? (
      <p className="text-sm text-muted">The list of sessions didn&apos;t load: see the notice under Flagged mistakes.</p>
    ) : (
      <Skeleton height={layout === "row" ? 74 : 320} label="Loading the sessions…" className="w-full" />
    );

  let results: ReactNode;
  if (selection) {
    results = (
      <Results
        selection={selection}
        driver={query.driver}
        limit={limit}
        state={mistakes}
        selected={explainSelection(explain)}
        onExplain={onExplain}
        onWholeField={() => onDriver(undefined)}
        onRetryAll={retryAll}
      />
    );
  } else if (catalogState.status === "error") {
    results = unreachable(catalogState) ? (
      <ApiDown onRetry={retryAll} />
    ) : (
      <ErrorNotice error={catalogState.error} onRetry={catalogState.retry} />
    );
  } else {
    results = (
      <ChartCard level={3} title="Ranked by time lost" subtitle={FINDING} status="loading">
        <ChartSkeleton name="find-mistakes" label={FINDING} />
      </ChartCard>
    );
  }

  const race = raceSelection(selection);

  return (
    <div className="mt-6 grid gap-6 sm:mt-8 lg:gap-8">
      <SelectionBar summary={selection ? selectionSummary(selection, query.driver) : "Choose a session"}>
        {pickers("sheet", "stack")}
      </SelectionBar>

      <div className="glass grid gap-5 rounded-card p-4 sm:p-5">
        <div className="flex flex-wrap items-end gap-x-4 gap-y-4 max-lg:hidden">{pickers("inline", "row")}</div>
        <SessionSearch onPick={onFound} className="lg:border-t lg:border-hairline lg:pt-5" />
      </div>

      {/* On a phone the strip gives way to the open corner, which sits below the list. */}
      <FeaturedStrip catalog={catalog} onOpen={onFeatured} className={explain ? "max-lg:hidden" : undefined} />

      {/* The list and the open corner under one heading: on a wide screen the corner sits beside
          the list and sticks while the list scrolls; on a phone it follows the list. */}
      <section aria-labelledby="results-title" className="grid min-w-0 gap-3">
        <h2
          id="results-title"
          ref={resultsHeadingRef}
          tabIndex={-1}
          className="font-serif text-h3 text-balance text-fg max-lg:scroll-mt-20"
        >
          Flagged mistakes
        </h2>
        <div className="grid gap-6 lg:grid-cols-12 lg:items-start lg:gap-8">
          <div className="grid min-w-0 content-start gap-3 lg:col-span-5">{results}</div>
          <div className="min-w-0 lg:sticky lg:top-[calc(var(--header-h)+16px)] lg:col-span-7">
            {selection && explain ? (
              <ExplainPanel
                key={`${selectionKey(selection)}|${explainKey}`}
                selection={selection}
                corner={explain}
                state={corner}
                onRetryAll={retryAll}
                onClose={onClose}
                panelRef={panelRef}
                headingRef={panelHeadingRef}
              />
            ) : (
              <ExplainPlaceholder />
            )}
          </div>
        </div>
      </section>

      {race ? <RaceContext key={selectionKey(race)} selection={race} driver={query.driver} /> : null}

      {/* Each list that arrives, said once to screen readers (6.5). */}
      <p role="status" className="sr-only">
        {mistakes.status === "ok" ? loadedMessage(mistakes.data.data) : ""}
      </p>
    </div>
  );
}
