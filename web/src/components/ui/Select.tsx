"use client";

import { useId, type ReactNode } from "react";
import { CONTROL_CLASS, Field, hintId } from "./Field";
import { ChevronDownIcon } from "./icons";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
  /** Consecutive options with the same group sit under one <optgroup>. */
  group?: string;
}

export interface SelectProps {
  label: ReactNode;
  value: string;
  onChange(value: string): void;
  options: readonly SelectOption[];
  id?: string;
  hint?: ReactNode;
  disabled?: boolean;
  className?: string;
}

type Run = { group: string | undefined; options: SelectOption[] };

// Splits the options into runs that share a group, keeping their order.
function runs(options: readonly SelectOption[]): Run[] {
  const out: Run[] = [];
  for (const option of options) {
    const last = out[out.length - 1];
    if (last && last.group === option.group) last.options.push(option);
    else out.push({ group: option.group, options: [option] });
  }
  return out;
}

function renderOption(option: SelectOption) {
  return (
    <option key={option.value} value={option.value} disabled={option.disabled}>
      {option.label}
    </option>
  );
}

/** A native <select> with a visible label: the platform's own picker on phones, full keyboard support. */
export function Select({ label, value, onChange, options, id, hint, disabled, className }: SelectProps) {
  const autoId = useId();
  const selectId = id ?? autoId;
  return (
    <Field label={label} hint={hint} htmlFor={selectId} className={className}>
      <div className="relative">
        <select
          id={selectId}
          value={value}
          disabled={disabled}
          aria-describedby={hint ? hintId(selectId) : undefined}
          onChange={(event) => onChange(event.target.value)}
          className={`${CONTROL_CLASS} cursor-pointer appearance-none pr-10 [&_option]:bg-(--glass-solid) [&_option]:text-fg`}
        >
          {runs(options).map((run, i) =>
            run.group === undefined ? (
              run.options.map(renderOption)
            ) : (
              <optgroup key={`${run.group}-${i}`} label={run.group}>
                {run.options.map(renderOption)}
              </optgroup>
            ),
          )}
        </select>
        <ChevronDownIcon className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted" />
      </div>
    </Field>
  );
}
