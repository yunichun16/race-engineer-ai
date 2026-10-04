/**
 * Class strings the report components share, on the design pass's tokens and globals (design spec
 * sections d and g): `.link` and `.micro` from globals.css, `border-edge` (the glass edge) and
 * `border-hairline` (the rule between rows) from its `@theme`.
 */

/** A link inside text: ink, underlined, the underline darkening on hover. */
export const LINK = "link";

/** A micro-label (spec d): mono caps, 11 px, spaced out, muted. Names a thing; never a control's only label. */
export const MICRO = "micro";

/** Inline code in running text. */
export const CODE = "rounded-md bg-raised px-1.5 py-0.5 font-mono text-[0.85em]";

/** The glass edge round cards, tables and code blocks. */
export const EDGE = "border border-edge";

/** The rule between table rows and list rows (with a `border-t`). */
export const HAIRLINE = "border-hairline";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}
