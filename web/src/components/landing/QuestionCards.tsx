import Link from "next/link";
import type { ReactNode } from "react";
import { Num, numText } from "@/components/findings/Num";
import { Reveal } from "@/components/motion/Reveal";
import { Card } from "@/components/ui/Card";
import { SECTION, SectionHead } from "./SectionHead";
import { minus, parseInterval } from "./story";

// The mini diverging bar: a 300 × 54 drawing, zero at x = 150, ±12 m across.
const BAR = { width: 300, zero: 150, domain: 12 } as const;

/**
 * One teammate gap as a bar: Hamilton's braking point minus Leclerc's in 2026, in metres, with
 * its interval as a whisker. The numbers come from the `hamBrakesEarlier` claim (the m4_style.md
 * table cell), and the caption repeats them in words. Blue is the chart palette's series colour,
 * used here inside a figure only.
 */
function StyleBar() {
  const v = parseInterval(numText("hamBrakesEarlier"));
  if (!v) return null;
  const x = (m: number) => BAR.zero + (Math.max(-BAR.domain, Math.min(BAR.domain, m)) / BAR.domain) * BAR.zero;
  const [barFrom, barTo] = [Math.min(x(v.est), BAR.zero), Math.max(x(v.est), BAR.zero)];
  const label =
    `Braking point, Hamilton minus Leclerc, 2026: ${minus(String(v.est))} m, interval ${minus(String(v.lo))} to ` +
    `${minus(String(v.hi))} m: Hamilton brakes ${v.est < 0 ? "earlier" : "later"}`;
  const axis = "fill-muted font-mono text-[11px]";
  return (
    <figure className="mt-1 grid gap-2">
      <svg viewBox={`0 0 ${BAR.width} 54`} role="img" aria-label={label} className="block h-auto w-full overflow-visible">
        <line x1={BAR.zero} y1={4} x2={BAR.zero} y2={34} stroke="var(--color-border-secondary)" />
        <rect x={barFrom} y={13} width={barTo - barFrom} height={12} rx={3} fill="var(--a)" />
        <line x1={x(v.lo)} y1={19} x2={x(v.hi)} y2={19} stroke="var(--color-text-primary)" strokeWidth={1.5} />
        <line x1={x(v.lo)} y1={13} x2={x(v.lo)} y2={25} stroke="var(--color-text-primary)" strokeWidth={1.5} />
        <line x1={x(v.hi)} y1={13} x2={x(v.hi)} y2={25} stroke="var(--color-text-primary)" strokeWidth={1.5} />
        <text x={BAR.zero} y={50} textAnchor="middle" className={axis}>
          0 m
        </text>
        <text x={2} y={50} className={axis}>
          ← brakes earlier
        </text>
        <text x={BAR.width - 2} y={50} textAnchor="end" className={axis}>
          later →
        </text>
      </svg>
      <figcaption className="text-sm/[1.6] text-muted">
        Braking point, 2026: Hamilton brakes earlier than Leclerc, by <Num id="hamBrakesEarlierText" />.
      </figcaption>
    </figure>
  );
}

interface QuestionCardProps {
  tag: string;
  claim: string;
  figure: ReactNode;
  children: ReactNode;
  href: string;
  more: string;
}

function QuestionCard({ tag, claim, figure, children, href, more }: QuestionCardProps) {
  return (
    <Reveal className="grid">
      <Card as="article">
        <p className="micro">{tag}</p>
        <h3 className="font-serif text-h3 text-fg">{claim}</h3>
        {figure}
        <p className="text-sm/[1.6] text-muted">{children}</p>
        <Link href={href} className="link inline-flex min-h-11 items-center justify-self-start">
          {more}&nbsp;<span aria-hidden="true">→</span>
        </Link>
      </Card>
    </Reveal>
  );
}

/**
 * "Three questions, honest answers": what the model learned about mistakes, style and the 2026
 * rules, each as a figure, a plain claim in the report's own direction, and the body with its
 * interval and caveat. Every number is a claim (content/claims.ts) printed by <Num>.
 */
export function QuestionCards() {
  return (
    <section aria-labelledby="questions-title" className={SECTION}>
      <SectionHead id="questions-title" label="Three questions, honest answers">
        What the model learned, intervals included.
      </SectionHead>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 lg:gap-5">
        <QuestionCard
          tag="Mistakes"
          claim="The simple baseline ranks mistakes better overall."
          figure={
            <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
              <Num id="aurocAll" variant="figure" />
              <span className="micro">AUROC, Isolation Forest against the Transformer</span>
            </div>
          }
          href="/report#rq2"
          more="Can it find mistakes?"
        >
          Isolation Forest ranks labelled mistakes better overall; the Transformer is sharper at the very top, with a
          labelled mistake in <Num id="top50" /> for Isolation Forest. Combined, they reach an average precision of{" "}
          <Num id="combined" /> for each alone on the 2026 test events, and the intervals overlap.
        </QuestionCard>
        <QuestionCard
          tag="Style"
          claim="Style is the gap between teammates."
          figure={<StyleBar />}
          href="/report#rq1"
          more="Can it tell drivers apart?"
        >
          Within a season, a probe on the embedding names the driver from one corner more often than hand-crafted
          features; across seasons the order flips (<Num id="probeAcross" />). The embedding mostly learns the car
          (teammates&apos; centroids: median cosine <Num id="carCos" />), so style here means the difference between
          teammates.
        </QuestionCard>
        <QuestionCard
          tag="The 2026 rules"
          claim="Most of the 2026 jump is new tracks, not new cars."
          figure={
            <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
              <Num id="shiftAll" variant="figure" />
              <span className="micro">more reconstruction error, 2025 to 2026</span>
            </div>
          }
          href="/report#rq3"
          more="What did the 2026 rules change?"
        >
          Reconstruction error rises <Num id="shiftAll" />, but <Num id="shiftSame" /> on the same circuits. Fine-tuning on
          2026 races helps by <Num id="ftGain" />: <Num id="ftVerdict" />.
        </QuestionCard>
      </div>
    </section>
  );
}
