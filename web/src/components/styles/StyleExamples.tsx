"use client";

import type { MouseEvent } from "react";
import { Num } from "@/components/findings/Num";
import { getClaim } from "@/content/claims";
import { exampleHref, parseCell, STYLE_EXAMPLES, type StyleExample } from "@/content/style-examples";
import { MiniBar } from "./MiniBar";

export interface StyleExamplesProps {
  /** The pair on show, to mark its example. */
  current: { year?: number; a?: string; b?: string };
  /** Opens an example (the page writes it into its URL). */
  onSelect(example: StyleExample): void;
}

// A plain left click opens the example in place; a modified or middle click keeps the link's
// own behaviour (a new tab, a copied link).
function inPlace(event: MouseEvent<HTMLAnchorElement>): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

function ExampleBody({ e }: { e: StyleExample }) {
  const f = e.finding;
  const cell = f ? parseCell(getClaim(f.bar).value) : null;
  if (!f || !cell) return <p className="text-sm/[1.6] text-muted">{e.note}</p>;
  return (
    <>
      <MiniBar {...cell} metric={f.metric} a={e.a} b={e.b} less={f.less} more={f.more} />
      <p className="text-sm/[1.6] text-muted">
        By <Num id={f.text} />.
      </p>
    </>
  );
}

/**
 * The pairs worth a look (content/style-examples.ts), as cards in a strip that scrolls sideways on
 * a phone and a grid from 640 px. Each card's title is its link (stretched over the card), so a
 * screen reader hears the teaser, not the whole card; the mini bar and its sentence read after.
 */
export function StyleExamples({ current, onSelect }: StyleExamplesProps) {
  return (
    <section aria-labelledby="style-examples-title">
      <h2 id="style-examples-title" className="micro">
        Pairs worth a look
      </h2>
      <ul className="-mx-4 mt-3 flex snap-x snap-mandatory scroll-px-4 gap-3 overflow-x-auto px-4 pt-1 pb-4 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 sm:pb-0 lg:grid-cols-4">
        {STYLE_EXAMPLES.map((e) => {
          const showing = current.year === e.year && current.a === e.a && current.b === e.b;
          return (
            <li
              key={e.id}
              className={[
                "glass glass-sm relative grid shrink-0 basis-[min(85%,300px)] snap-start content-start gap-2 rounded-[20px] p-4",
                "transition-transform duration-(--dur-hover) hover:-translate-y-0.5",
                "has-[a:focus-visible]:outline-2 has-[a:focus-visible]:outline-offset-2 has-[a:focus-visible]:outline-focus",
                showing ? "shadow-[var(--shadow-glass-sm),inset_0_0_0_1px_var(--color-border-secondary)]" : "",
              ].join(" ")}
            >
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="micro">
                  {e.year} · {e.team}
                </span>
                {showing ? <span className="tag">Showing</span> : null}
              </p>
              <a
                href={exampleHref(e)}
                aria-current={showing ? "true" : undefined}
                onClick={(event) => {
                  if (!inPlace(event)) return;
                  event.preventDefault();
                  onSelect(e);
                }}
                className="text-title text-balance text-fg after:absolute after:inset-0 after:rounded-[20px] focus-visible:outline-none"
              >
                {e.title}
              </a>
              <ExampleBody e={e} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
