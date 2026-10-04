import Link from "next/link";
import type { ReactNode } from "react";
import { ChevronRightIcon } from "@/components/ui/icons";

export interface FigureSource {
  href: string; // a /report/<slug> page, usually at the section that holds the numbers
  label: string; // the file, "report/m3_results.md"
}

export interface FigureFrameProps {
  /** The figure's heading (an h3 under the section's h2). */
  title: string;
  /** A micro-label above the title, naming the kind of figure. */
  kicker?: string;
  /** What the figure shows and how to read it, under the title. */
  caption: ReactNode;
  /** Above the drawing: a toggle, then the legend. */
  controls?: ReactNode;
  legend?: ReactNode;
  /** The reports the numbers come from. */
  sources: readonly FigureSource[];
  /** The table inside "Show the numbers": every value the figure draws. */
  numbers: ReactNode;
  className?: string;
  children: ReactNode;
}

/**
 * The frame every findings figure sits in: a glass panel (spec b; never a second blur inside it)
 * with the caption first, the drawing, the sources, and a "Show the numbers" disclosure with an
 * HTML table. On phones it is near-bleed (8 px gutters), as chart cards are.
 */
export function FigureFrame({ title, kicker, caption, controls, legend, sources, numbers, className, children }: FigureFrameProps) {
  return (
    <figure className={["glass-panel -mx-2 overflow-hidden rounded-card sm:mx-0", className].filter(Boolean).join(" ")}>
      <figcaption className="grid gap-1.5 px-4 pt-4 sm:px-6 sm:pt-5">
        {kicker ? <span className="micro">{kicker}</span> : null}
        <h3 className="text-title text-balance text-fg">{title}</h3>
        <span className="block max-w-[68ch] text-sm/[1.6] text-muted">{caption}</span>
      </figcaption>
      {controls || legend ? (
        <div className="grid gap-3 px-4 pt-4 sm:px-6">
          {controls}
          {legend}
        </div>
      ) : null}
      <div className="px-2 pt-3 pb-4 sm:px-4">{children}</div>
      <p className="border-t border-edge px-4 py-3 text-sm/[1.6] text-muted sm:px-6">
        Source:{" "}
        {sources.map((s, k) => (
          <span key={s.href}>
            {k > 0 ? <span aria-hidden="true"> · </span> : null}
            <Link href={s.href} className="link inline-flex min-h-11 items-center pointer-fine:min-h-0">
              {s.label}
            </Link>
          </span>
        ))}
      </p>
      <details className="group/numbers border-t border-edge">
        <summary className="flex min-h-12 cursor-pointer list-none items-center gap-2.5 px-4 text-sm font-medium text-fg sm:px-6 [&::-webkit-details-marker]:hidden">
          <ChevronRightIcon className="size-4 shrink-0 text-muted transition-transform duration-(--dur-state) group-open/numbers:rotate-90" />
          Show the numbers
        </summary>
        <div className="px-4 pb-4 sm:px-6">{numbers}</div>
      </details>
    </figure>
  );
}

export interface NumbersTableProps {
  /** Names the table for screen readers ("Table: …"). */
  label: string;
  head: readonly string[];
  /** Cell text; the columns listed in `numeric` set in mono, right-aligned. */
  rows: readonly (readonly ReactNode[])[];
  numeric?: readonly number[];
}

/** A figure's numbers as a table in its own scroll region (it never widens the page), styled like the report tables. */
export function NumbersTable({ label, head, rows, numeric = [] }: NumbersTableProps) {
  const align = (c: number) => (numeric.includes(c) ? "text-right" : "text-left");
  return (
    <div role="region" aria-label={`Table: ${label}`} tabIndex={0} className="overflow-x-auto rounded-2xl border border-edge">
      <table className="w-full border-collapse text-[15px]/[1.5]">
        <thead>
          <tr>
            {head.map((h, c) => (
              <th key={c} scope="col" className={`micro bg-raised px-3.5 py-2.5 align-bottom whitespace-nowrap ${align(c)}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) =>
                c === 0 ? (
                  <th key={c} scope="row" className="min-w-40 border-t border-hairline px-3.5 py-2.5 text-left align-top font-normal text-fg">
                    {cell}
                  </th>
                ) : (
                  <td
                    key={c}
                    className={`border-t border-hairline px-3.5 py-2.5 align-top whitespace-nowrap text-fg ${align(c)} ${numeric.includes(c) ? "font-mono text-[14px] tabular-nums" : ""}`}
                  >
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
