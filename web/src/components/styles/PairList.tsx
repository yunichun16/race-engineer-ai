"use client";

import type { Ref } from "react";
import { Select } from "@/components/ui/Select";
import type { StyleCatalog, StylePair } from "@/lib/api/types";
import { pairEventsText, pairKey, pairOptions } from "./pairs";

export interface PairListProps {
  catalog: StyleCatalog;
  /** The pair on show, in either order. */
  current: { a?: string; b?: string };
  onSelect(pair: StylePair): void;
  ref?: Ref<HTMLElement>;
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className={className} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3.5 8.5l3 3 6-7" />
    </svg>
  );
}

// A rail of pair buttons on the recessed track the segmented controls use. The pair on show is
// drawn like a checked segment: raised glass with a 3:1 ring, its codes in bold and a check mark,
// so the colour never carries it alone. Neutral throughout: never lime, never the series blue.
const RAIL_BUTTON =
  "group grid w-full min-h-11 cursor-pointer content-center gap-0.5 rounded-[11px] px-3 py-2 text-left text-fg " +
  "transition-colors duration-(--dur-hover) hover:bg-hairline focus-visible:outline-offset-0 " +
  "aria-pressed:bg-glass-strong aria-pressed:shadow-[inset_0_1px_0_var(--highlight),inset_0_0_0_1px_var(--color-border-secondary)] " +
  "aria-pressed:hover:bg-glass-strong forced-colors:aria-pressed:outline-2 forced-colors:aria-pressed:outline-[Highlight]";

/**
 * "Teammates in {year}": the season's teammate pairs, each with the events they shared. On a
 * phone a native select grouped by team; from 640 px a rail of buttons with `aria-pressed`. A
 * short mid-season pair says it has too few events for intervals. The heading takes focus when
 * the page sends the reader back to the list (`ref` is the section).
 */
export function PairList({ catalog, current, onSelect, ref }: PairListProps) {
  const currentKey = current.a && current.b ? pairKey(current.a, current.b) : null;
  const byKey = new Map(catalog.pairs.map((p) => [pairKey(p.a, p.b), p]));
  const listed = currentKey !== null && byKey.has(currentKey);
  const options = pairOptions(catalog);

  return (
    <section ref={ref} aria-labelledby="style-pairs-title" className="grid gap-3">
      <h2 id="style-pairs-title" tabIndex={-1} className="font-serif text-[22px] leading-[1.2] text-balance text-fg">
        Teammates in {catalog.year}
      </h2>
      {catalog.pairs.length === 0 ? (
        <p className="text-sm text-muted">The style data has no teammate pairs for {catalog.year}. Compare any two drivers below.</p>
      ) : (
        <>
          <div className="sm:hidden">
            <Select
              label="Pair"
              value={listed ? currentKey : ""}
              onChange={(value) => {
                const pair = byKey.get(value);
                if (pair) onSelect(pair);
              }}
              options={listed ? options : [{ value: "", label: "Choose a pair of teammates", disabled: true }, ...options]}
            />
          </div>
          <ul className="hidden gap-0.5 rounded-[14px] bg-hairline p-[3px] sm:grid sm:grid-cols-2 lg:grid-cols-1">
            {catalog.pairs.map((p) => {
              const key = pairKey(p.a, p.b);
              const pressed = key === currentKey;
              return (
                <li key={key} className="min-w-0">
                  <button type="button" aria-pressed={pressed} onClick={() => onSelect(p)} className={RAIL_BUTTON}>
                    <span className="flex items-center justify-between gap-2 text-xs text-muted">
                      {p.team}
                      {pressed ? <CheckIcon className="size-3.5 shrink-0 text-fg" /> : null}
                    </span>
                    <span className="sr-only"> · </span>
                    <span className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <span className="font-mono text-[15px] tracking-[0.04em] group-aria-pressed:font-semibold">
                        {p.a} and {p.b}
                      </span>
                      <span className="sr-only"> · </span>
                      <span className="text-[13px] text-muted tabular-nums">{pairEventsText(p)}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
