"use client";

import Link from "next/link";
import type { MouseEvent } from "react";
import { FEATURED, featuredHref, featuredTags, type FeaturedCorner } from "@/content/featured";
import type { SessionCatalog } from "@/lib/api/types";
import { featuredLine, locationOf } from "./selection";

export interface FeaturedStripProps {
  /** For the place names on the cards ("Baku"); the events' short names stand in until it's here. */
  catalog: SessionCatalog | null;
  /** Opens an entry in the explorer (its corner, or its whole session). */
  onOpen(f: FeaturedCorner): void;
  className?: string;
}

// A plain click opens the entry in place (a history entry, no page load); a click with a
// modifier, or a middle click, opens the link as a link (a new tab).
function plainClick(event: MouseEvent): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

/**
 * Examples to start from (`content/featured.ts`): flagged corners of several kinds, a lift that is
 * likely energy management, a cut that saved time, and a session where nothing was flagged. A row
 * that scrolls sideways on a phone, four columns from `lg`. Each card names the driver, the place,
 * season, session and corner, and its tags: the mistake's type, and what the example shows for
 * the other kinds. No numbers, no verdict and no "checked" badge (plan 13).
 */
export function FeaturedStrip({ catalog, onOpen, className }: FeaturedStripProps) {
  return (
    <section
      id="featured"
      aria-labelledby="featured-title"
      className={["min-w-0 max-lg:scroll-mt-20", className].filter(Boolean).join(" ")}
    >
      <h2 id="featured-title" className="micro">
        Examples to start from
      </h2>
      <ul className="-mx-4 mt-2 flex snap-x snap-mandatory scroll-px-4 gap-3 overflow-x-auto px-4 pt-1 pb-4 sm:-mx-6 sm:scroll-px-6 sm:px-6 lg:mx-0 lg:grid lg:grid-cols-4 lg:overflow-visible lg:px-0 lg:pb-0">
        {FEATURED.map((f) => (
          <li key={f.id} className="flex shrink-0 basis-[min(78%,260px)] snap-start lg:basis-auto">
            <Link
              href={featuredHref(f)}
              prefetch={false}
              onClick={(event) => {
                if (!plainClick(event)) return;
                event.preventDefault();
                onOpen(f);
              }}
              className="glass glass-sm grid w-full content-start gap-2 rounded-[20px] p-4 text-fg transition-transform duration-(--dur-hover) hover:-translate-y-0.5"
            >
              <span className="font-mono font-semibold tracking-[0.04em]">{f.args.driver}</span>
              <span className="text-sm text-muted">{featuredLine(f, locationOf(catalog, f.args.year, f.args.event))}</span>
              <span className="flex flex-wrap gap-1.5">
                {featuredTags(f).map((tag) => (
                  <span key={tag} className="tag">
                    {tag}
                  </span>
                ))}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
