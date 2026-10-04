"use client";

import { Segmented } from "@/components/ui/Segmented";

export interface SeasonPickerProps {
  /** The seasons with style data (StyleCatalog.years, or the fallback list), in any order. */
  years: readonly number[];
  value: number | undefined;
  onChange(year: number): void;
}

/**
 * "Season": the style data's seasons as one row of radios, oldest first, stretched to the width
 * it has with equal segments (five seasons fit a 360 px phone and the explorer's side column).
 */
export function SeasonPicker({ years, value, onChange }: SeasonPickerProps) {
  const options = [...years].sort((x, y) => x - y).map((year) => ({ value: String(year), label: String(year) }));
  return (
    <Segmented
      legend="Season"
      name="styles-season"
      value={value === undefined ? "" : String(value)}
      onChange={(next) => onChange(Number(next))}
      options={options}
      className="[&_label]:flex-1 [&_label]:justify-center [&_label]:px-1 [&_label]:tabular-nums [&>div]:flex [&>div]:w-full [&>div]:flex-nowrap"
    />
  );
}
