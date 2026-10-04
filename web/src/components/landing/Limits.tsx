import Link from "next/link";
import type { ReactNode } from "react";
import { Num } from "@/components/findings/Num";
import { Reveal } from "@/components/motion/Reveal";
import { SECTION, SectionHead } from "./SectionHead";

// Plan 13's list: the review line is replaced by "not checked at scale yet".
const LIMITS: ReactNode[] = [
  "Labels cover only track limits.",
  "Telemetry is about 4 samples a second and the brake is on or off.",
  <>
    Time lost stops <Num id="timeWindow" /> after the apex, so exit mistakes are undercounted.
  </>,
  "In 2026, lifting and coasting can be energy management, not a mistake.",
  "Drivers of different teams mix car and driver.",
  "The findings haven't been checked at scale yet: a larger human review is planned.",
];

/** "What it can't tell you": the limits that matter most, with the full list on /report. */
export function Limits() {
  return (
    <section id="limits" aria-labelledby="limits-title" className={SECTION}>
      <SectionHead id="limits-title" label="Limits">
        What it can&apos;t tell you.
      </SectionHead>
      <Reveal>
        <ul className="grid md:grid-cols-2 md:gap-x-8">
          {LIMITS.map((item, i) => (
            <li
              key={i}
              className="grid grid-cols-[22px_minmax(0,1fr)] gap-2.5 border-t border-hairline py-3.5 text-base/[1.6] text-fg before:font-mono before:text-muted before:content-['—']"
            >
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </Reveal>
      <p className="mt-3">
        <Link href="/report#limits" className="link inline-flex min-h-11 items-center">
          Read the limits in full&nbsp;<span aria-hidden="true">→</span>
        </Link>
      </p>
    </section>
  );
}
