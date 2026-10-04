"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiNotice } from "@/components/status/ApiNotice";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Notice } from "@/components/ui/Notice";
import { Skeleton } from "@/components/ui/Skeleton";
import type { StyleExample } from "@/content/style-examples";
import { useStyleCatalog } from "@/lib/api/catalog";
import type { StyleCatalog, ToolResult } from "@/lib/api/types";
import { useTool } from "@/lib/api/tools";
import { setQuery } from "@/lib/url";
import { CustomPair } from "./CustomPair";
import { LapsHeadToHead } from "./LapsHeadToHead";
import { PairList } from "./PairList";
import { areTeammates, driverChoices, pickPair, type PairChoice } from "./pairs";
import {
  FALLBACK_YEARS,
  parseStylesQuery,
  serializeStylesQuery,
  stylesUpdate,
  withStylesDefaults,
  type StylesQuery,
} from "./query";
import { SeasonPicker } from "./SeasonPicker";
import { StyleExamples } from "./StyleExamples";
import { StyleResult, type StyleResultState } from "./StyleResult";

type StyleResultData = ToolResult<"compare_driving_styles">;

// Moves focus into the pair list: the phone's select, or the rail's pressed (else first) button.
function focusPairList(section: HTMLElement | null): void {
  if (!section) return;
  const visible = (el: HTMLElement | null) => el && el.getClientRects().length > 0;
  const select = section.querySelector("select");
  const target = [select, section.querySelector<HTMLElement>('button[aria-pressed="true"]'), section.querySelector<HTMLElement>("button")].find(
    (el): el is HTMLElement => !!visible(el),
  );
  (target ?? section.querySelector<HTMLElement>("h2"))?.focus();
}

/**
 * The /styles explorer (plan 7.4): pick a season and a pair of teammates (or any two drivers) and
 * see how they take corners, with the season's style map. Its state lives in the URL (query.ts):
 * the season and the pair push a history entry, the chart's kind and plane and the lap-by-lap
 * weekend replace it. Data comes from the style catalog (the season's pairs) and
 * compare_driving_styles; with no catalog the seasons fall back to a fixed list and drivers are
 * typed as codes.
 */
export function StylesExplorer() {
  const params = useSearchParams();
  const q = useMemo(() => withStylesDefaults(parseStylesQuery(params)), [params]);

  const tool = useTool("compare_driving_styles", q.a && q.b ? { driver_a: q.a, driver_b: q.b, year: q.year } : null);
  // A pair named without a season: the tool picks the latest season both drove, and the list
  // follows the season it picked.
  const toolYear = tool.status === "ok" ? tool.data.data.year : undefined;
  const catalogState = useStyleCatalog(q.year ?? toolYear ?? null);
  const catalog: StyleCatalog | null = catalogState.status === "ok" ? catalogState.data : null;
  const catalogDown = catalogState.status === "error";
  const year = q.year ?? toolYear ?? catalog?.year;
  const years = catalog?.years ?? (catalogState.status === "loading" ? catalogState.stale?.years : undefined) ?? FALLBACK_YEARS;
  // A season the style data doesn't have (a hand-edited `year=2019`): the catalog says so (422),
  // and the season is dropped from the URL like any other bad value, keeping the rest.
  const unknownYear = q.year !== undefined && catalogState.status === "error" && catalogState.error.code === "invalid_input";
  useEffect(() => {
    if (unknownYear) setQuery(serializeStylesQuery({ ...q, year: undefined }), { push: false });
  }, [unknownYear, q]);

  /** Writes `next` into the URL, pushing or replacing as query.ts says; false when nothing changed. */
  function update(next: StylesQuery): boolean {
    const how = stylesUpdate(q, next);
    if (!how) return false;
    return setQuery(serializeStylesQuery(next), { push: how === "push" });
  }

  // The drivers on show before a season change, kept until the new season's pair is picked.
  const carry = useRef<Partial<PairChoice>>({});
  // A season with no pair in the URL (the season picker writes one): once its list is here, pick
  // the pair that keeps what the reader had, and write it in place of the URL (no extra entry).
  useEffect(() => {
    if (q.a && q.b) {
      carry.current = {};
      return;
    }
    if (!catalog || (q.year !== undefined && catalog.year !== q.year)) return;
    const pick = pickPair(catalog, { a: q.a ?? carry.current.a, b: q.b ?? carry.current.b });
    if (pick) setQuery(serializeStylesQuery({ ...q, year: catalog.year, ...pick }), { push: false });
  }, [q, catalog]);

  // The last comparison shown, kept while a season change picks its pair (the tool is idle then).
  const [kept, setKept] = useState<StyleResultData | null>(null);
  if (tool.status === "ok" && kept !== tool.data) setKept(tool.data);

  // Focus after a pick: the new map after a map point (a keyboard reader carries on picking), or
  // the result's title after an example (it may be a long way down on a phone).
  const pending = useRef<"map" | "heading" | null>(null);
  const headingRef = useRef<HTMLSpanElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const pairsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (tool.status !== "ok" && tool.status !== "error") return;
    const want = pending.current;
    pending.current = null;
    if (want === "map" && tool.status === "ok") {
      // The chart draws in its own effect, which runs before this one.
      const map = cardRef.current?.querySelector<SVGElement>("svg.selectable");
      if (map) map.focus({ preventScroll: true });
      else headingRef.current?.focus();
    } else if (want) {
      headingRef.current?.focus();
    }
  }, [tool]);

  function selectYear(next: number) {
    if (next === year) return;
    // With no list to pick from, the pair stays: the tool says if they didn't race that season.
    if (catalogDown) {
      update({ ...q, year: next, laps: undefined });
      return;
    }
    // Keep the drivers carried from before when a second season is picked before the first
    // season's pair was (the URL has no pair then).
    carry.current = { a: q.a ?? carry.current.a, b: q.b ?? carry.current.b };
    update({ year: next, kind: q.kind, plane: q.plane });
  }

  // Try again after a failure: the comparison, and the season's list if it failed too (the
  // server was down for both), so the page comes back whole.
  function retryAll() {
    if (tool.status === "error") tool.retry();
    if (catalogState.status === "error") catalogState.retry();
  }

  function selectPair(a: string, b: string) {
    update({ ...q, year, a, b });
  }

  function selectPoint(driver: string) {
    if (update({ ...q, year, b: driver })) pending.current = "map";
  }

  function selectExample(e: StyleExample) {
    if (update({ year: e.year, a: e.a, b: e.b })) pending.current = "heading";
    else headingRef.current?.focus();
  }

  // What the result shows. With no pair yet, the page is picking one (the last chart stays,
  // dimmed) unless there is nothing to pick from.
  let state: StyleResultState | null = null;
  if (tool.status === "ok") state = { status: "ready", result: tool.data };
  else if (tool.status === "loading") state = { status: "loading", stale: tool.stale };
  else if (tool.status === "error") state = { status: "error", error: tool.error, retry: retryAll };
  else if (!catalogDown && !(catalog && catalog.pairs.length === 0)) state = { status: "loading", stale: kept ?? undefined };

  // Drivers of different teams: the catalog knows before the request; else the answer says.
  const fromCatalog = catalog && q.a && q.b ? areTeammates(catalog, q.a, q.b) : null;
  const fromAnswer = tool.status === "ok" ? !tool.data.data.teammates : null;
  const differentCars = fromCatalog === false || (fromCatalog === null && fromAnswer === true);
  const choices = catalog ? driverChoices(catalog) : [];
  const answer = tool.status === "ok" ? tool.data.data : null;
  const teamOf = (code: string | undefined): string | null => {
    if (answer && code === answer.driver_a) return answer.teams[0];
    if (answer && code === answer.driver_b) return answer.teams[1];
    return choices.find((c) => c.driver === code)?.teams.join(" and ") ?? null;
  };
  const teamA = teamOf(q.a);
  const teamB = teamOf(q.b);
  // The lap-by-lap comparison needs the pair's own season, not the newest one shown meanwhile.
  const pairYear = q.year ?? toolYear;

  const announce =
    tool.status === "ok" ? `Compared ${tool.data.data.driver_a} and ${tool.data.data.driver_b}, ${tool.data.data.year}.` : "";

  return (
    <div className="mt-10 grid grid-cols-[minmax(0,1fr)] gap-8 sm:mt-12">
      <ApiNotice />

      <StyleExamples current={{ year, a: q.a, b: q.b }} onSelect={selectExample} />

      <div className="grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[20rem_minmax(0,1fr)] lg:items-start lg:gap-10">
        <Card className="grid-cols-[minmax(0,1fr)] max-sm:p-4 lg:p-5">
          <SeasonPicker years={years} value={year} onChange={selectYear} />
          {catalog ? (
            <PairList ref={pairsRef} catalog={catalog} current={{ a: q.a, b: q.b }} onSelect={(p) => selectPair(p.a, p.b)} />
          ) : catalogDown ? null : (
            <div className="grid gap-3">
              <Skeleton height={26} label="" className="w-2/3" />
              <Skeleton height={320} label="Loading the season's teammates…" />
            </div>
          )}
          <CustomPair
            catalog={catalog}
            loading={!catalog && !catalogDown}
            current={{ a: q.a, b: q.b }}
            onCompare={(a, b) => selectPair(a, b)}
            defaultOpen={catalogDown}
            key={catalogDown ? "typed" : "listed"}
          />
        </Card>

        <div className="grid min-w-0 content-start gap-4">
          <p role="status" className="sr-only">
            {announce}
          </p>
          {differentCars && q.a && q.b ? (
            <Notice tone="warn" title="Different cars: these differences mix car and driver.">
              <p>
                {teamA && teamB ? `${q.a} drove for ${teamA} and ${q.b} for ${teamB}. ` : ""}Only teammates share a car, so only their
                differences isolate driving style.
              </p>
            </Notice>
          ) : null}

          {q.a && q.b && state ? (
            <StyleResult
              a={q.a}
              b={q.b}
              // The pair's own season: not the newest one listed while the tool picks it.
              year={pairYear}
              state={state}
              initialKind={q.kind}
              initialPlane={q.plane}
              onKindChange={(kind) => update({ ...q, kind })}
              onPlaneChange={(plane) => update({ ...q, plane })}
              onPointSelect={selectPoint}
              onPickFromList={catalog && catalog.pairs.length > 0 ? () => focusPairList(pairsRef.current) : undefined}
              headingRef={headingRef}
              cardRef={cardRef}
            />
          ) : state ? (
            // The season changed and its pair is being picked: the last chart stays, dimmed.
            <StyleResult
              a={undefined}
              b={undefined}
              year={year}
              state={state}
              onKindChange={() => {}}
              onPlaneChange={() => {}}
              onPointSelect={() => {}}
              headingRef={headingRef}
              cardRef={cardRef}
            />
          ) : catalog && catalog.pairs.length === 0 ? (
            <EmptyState title={`No teammate pairs in ${catalog.year}`}>
              The style data has no pairs for this season. Pick another season, or compare any two drivers.
            </EmptyState>
          ) : (
            <EmptyState
              title="Name two drivers to compare"
              action={
                catalogState.status === "error" ? (
                  <Button variant="secondary" size="sm" onClick={() => catalogState.retry()}>
                    Try again
                  </Button>
                ) : undefined
              }
            >
              The list of teammates couldn&apos;t be loaded. Type two three-letter codes under “Compare any two drivers”, or try
              another season.
            </EmptyState>
          )}

          {q.a && q.b && pairYear !== undefined ? (
            <LapsHeadToHead year={pairYear} a={q.a} b={q.b} laps={q.laps} onLapsChange={(laps) => update({ ...q, laps })} />
          ) : null}
        </div>
      </div>
    </div>
  );
}
