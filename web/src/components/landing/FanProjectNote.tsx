import Link from "next/link";
import { Reveal } from "@/components/motion/Reveal";
import { Card } from "@/components/ui/Card";
import { WarningIcon } from "@/components/ui/icons";
import { SECTION } from "./SectionHead";

/**
 * Where the plan once had an accuracy figure: the site says plainly that it is a fan project and
 * can be wrong (plan 13). One reviewer and 50 findings is too small a sample to quote, so there is
 * no number here; a larger review is planned.
 */
export function FanProjectNote() {
  return (
    <section aria-labelledby="fan-title" className={SECTION}>
      <Reveal className="grid">
        <Card className="border-[color-mix(in_srgb,var(--color-text-warning)_45%,transparent)]">
          <div className="grid gap-4 min-[860px]:grid-cols-[1.1fr_1fr] min-[860px]:items-end min-[860px]:gap-10">
            <div className="grid gap-3.5">
              <h2 id="fan-title" className="micro inline-flex items-center gap-2 text-caution">
                <WarningIcon className="size-[15px] shrink-0" />A fan project, so it can be wrong
              </h2>
              <p className="font-serif text-[clamp(22px,2.6vw,30px)] leading-[1.3] tracking-[-0.005em] text-balance text-fg">
                Treat a finding as a lead to check against the replay, not a verdict.
              </p>
            </div>
            <div className="grid gap-2">
              <p className="text-base/[1.7] text-muted">
                Race Engineer AI is an unofficial fan project. The model flags corners a driver took unusually and simple
                rules explain them, but both make mistakes, and the data is coarse (about 4 samples a second).
              </p>
              <Link href="/report#limits" className="link inline-flex min-h-11 items-center justify-self-start">
                Read the limits&nbsp;<span aria-hidden="true">→</span>
              </Link>
            </div>
          </div>
        </Card>
      </Reveal>
    </section>
  );
}
