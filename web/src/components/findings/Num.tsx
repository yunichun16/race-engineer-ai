import Link from "next/link";
import { claimHref, getClaim, type ClaimId } from "@/content/claims";

export interface NumProps {
  /** The claim to print (content/claims.ts); the page shows its `value` exactly as written. */
  id: ClaimId;
  /**
   * "text" (the default): a number inside running text keeps the text's family, in medium
   * weight and tabular figures (spec d). "figure": a card's headline figure, mono at the card
   * figure size. `className` adds to either.
   */
  variant?: "text" | "figure";
  /** Make the number a link to the report section it comes from (`claimHref`). */
  link?: boolean;
  className?: string;
}

const VARIANT = {
  text: "font-medium text-fg tabular-nums",
  figure: "font-mono text-figure tracking-[-0.02em] text-fg tabular-nums",
} as const;

// A dotted underline, so a sourced number reads as a citation rather than as a call to action;
// it turns solid on hover like every other link.
const LINKED = "underline decoration-line-strong decoration-dotted decoration-1 underline-offset-4 hover:decoration-solid hover:decoration-current";

/**
 * A number the site states about the project, printed from its claim, so every figure on the
 * landing and on /report traces back to a quote in `report/` that CI checks. A server component
 * with no state, so it works inside client components too.
 *
 *   <Num id="segments" />                      1.86 million, in running text
 *   <Num id="aurocAll" variant="figure" />     a card's mono figure
 *   <Num id="carCos" link />                   linked to /report/m4-style#style-embedding
 */
export function Num({ id, variant = "text", link = false, className }: NumProps) {
  const claim = getClaim(id);
  const classes = [VARIANT[variant], link ? LINKED : null, className].filter(Boolean).join(" ");
  const value = keepIntervalsTogether(claim.value);
  if (link) {
    return (
      <Link href={claimHref(id)} className={classes} data-claim={id} title={`From report/${claim.source}`}>
        {value}
      </Link>
    );
  }
  return (
    <span className={classes} data-claim={id}>
      {value}
    </span>
  );
}

/**
 * The value with no line break inside an interval: "[0.007, 0.096]" wraps as a whole, never after
 * its comma (a card figure at 360 px would otherwise split it).
 */
function keepIntervalsTogether(value: string): string {
  return value.replace(/\[[^\]]*\]/g, (interval) => interval.replace(/ /g, " "));
}

/** A claim's value as a plain string, for text that can't hold an element (an aria-label, a title). */
export function numText(id: ClaimId): string {
  return getClaim(id).value;
}
