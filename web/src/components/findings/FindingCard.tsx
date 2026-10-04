import Link from "next/link";
import type { ReactNode } from "react";

export interface FindingCardProps {
  /** A micro-label naming the question ("Mistakes", "Style", "2026"). */
  tag: string;
  /** The headline number, usually `<Num id variant="figure" />`. */
  figure: ReactNode;
  /** A micro-label under the figure saying what it measures. */
  figureLabel?: ReactNode;
  /** The claim in plain words, a serif h3 (spec d: number, then serif sentence, then small body). */
  title: string;
  /** Small body: the interval and the caveat. */
  children: ReactNode;
  /** Where the figure comes from: a /report/<slug> page or a section of this page. */
  source: { href: string; label: string };
  className?: string;
}

/**
 * One finding as the landing's cards show it: a glass card with the question as a micro tag, the
 * number in mono, the plain-words claim as a serif h3, a small body with the interval and caveat,
 * and a link to where the number comes from.
 */
export function FindingCard({ tag, figure, figureLabel, title, children, source, className }: FindingCardProps) {
  return (
    <article className={["glass flex flex-col gap-3 rounded-card p-5 sm:p-6.5", className].filter(Boolean).join(" ")}>
      <p className="micro">{tag}</p>
      <div className="grid gap-1">
        <p className="text-figure text-fg">{figure}</p>
        {figureLabel ? <p className="micro">{figureLabel}</p> : null}
      </div>
      <h3 className="font-serif text-h3 text-balance text-fg">{title}</h3>
      <p className="text-sm/[1.6] text-muted">{children}</p>
      <p className="mt-auto text-sm">
        <Link href={source.href} className="link inline-flex min-h-11 items-center gap-1 pointer-fine:min-h-0">
          {source.label}
          <span aria-hidden="true">→</span>
        </Link>
      </p>
    </article>
  );
}
