"use client";

import type { ReactNode } from "react";

export interface SegmentedOption<T extends string = string> {
  value: T;
  label: string;
  disabled?: boolean;
  /** Shown beside the label, always visible: "(not scored)". */
  note?: string;
}

export interface SegmentedProps<T extends string = string> {
  legend: ReactNode;
  name: string;
  value: T;
  onChange(value: T): void;
  options: readonly SegmentedOption<T>[];
  className?: string;
}

/**
 * A few mutually exclusive choices shown side by side: a fieldset of native radios, so the arrow
 * keys move between them and a screen reader hears "Session, radio group". The choices sit in a
 * recessed track; the chosen one is a raised glass segment in bold with a 3:1 ring (an outline in
 * forced colours). A disabled one keeps a faint edge and its note readable, with no strikethrough.
 */
export function Segmented<T extends string = string>({ legend, name, value, onChange, options, className }: SegmentedProps<T>) {
  return (
    <fieldset className={["min-w-0", className].filter(Boolean).join(" ")}>
      <legend className="mb-1.5 text-sm font-medium text-fg">{legend}</legend>
      <div className="inline-flex max-w-full flex-wrap gap-0.5 rounded-[14px] bg-hairline p-[3px]">
        {options.map((option) => (
          <label
            key={option.value}
            className={
              "relative inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-[11px] px-3.5 text-[15px] text-muted " +
              "transition-colors duration-(--dur-hover) has-enabled:hover:text-fg " +
              "has-checked:bg-glass-strong has-checked:font-semibold has-checked:text-fg " +
              "has-checked:shadow-[inset_0_1px_0_var(--highlight),inset_0_0_0_1px_var(--color-border-secondary)] " +
              "has-disabled:cursor-not-allowed has-disabled:shadow-[inset_0_0_0_1px_var(--edge)] " +
              "has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-focus " +
              "forced-colors:has-checked:outline-2 forced-colors:has-checked:outline-[Highlight]"
            }
          >
            <input
              type="radio"
              className="sr-only"
              name={name}
              value={option.value}
              checked={value === option.value}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
            {option.note ? <span className="text-[13px]">{option.note}</span> : null}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
