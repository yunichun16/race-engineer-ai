import Link from "next/link";
import { Num } from "@/components/findings/Num";
import { AskBox } from "./AskBox";
import { HeroTrack } from "./HeroTrack";

const DOORS = [
  { href: "/mistakes", label: "Explore mistakes" },
  { href: "/styles", label: "Compare teammates" },
  { href: "/report", label: "Read the findings" },
];

/**
 * The first screen: what it is in one sentence, the Ask box with three suggestions, the three
 * doors into the site, and the card of real flagged corners. On a 360 × 740 phone the headline,
 * lead, Ask box and chips fit above the fold; the card comes after them.
 */
export function Hero() {
  return (
    <section
      aria-labelledby="hero-title"
      className="mx-auto grid max-w-[1120px] items-center gap-10 px-4 pt-7 pb-10 sm:px-6 min-[960px]:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] min-[960px]:gap-12 min-[960px]:pt-18 min-[960px]:pb-14"
    >
      <div>
        <p className="micro">Race Engineer AI · deep learning on F1 telemetry · unofficial fan project</p>
        <h1 id="hero-title" className="mt-3.5 mb-4 text-display">
          Find the corner where the lap <em>went wrong.</em>
        </h1>
        <p className="mb-6 max-w-[34em] text-lead text-muted">
          Deep learning on <Num id="segments" /> corners of F1 telemetry flags what a driver did differently, explains
          it and costs it in seconds, for every qualifying, sprint and race since 2022.
        </p>
        <AskBox />
        <p className="flex flex-wrap gap-x-5.5 text-[15px]">
          {DOORS.map((door) => (
            <Link key={door.href} href={door.href} className="link inline-flex min-h-11 items-center">
              {door.label}&nbsp;<span aria-hidden="true">→</span>
            </Link>
          ))}
        </p>
      </div>
      <HeroTrack />
    </section>
  );
}
