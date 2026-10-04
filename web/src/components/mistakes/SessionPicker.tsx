"use client";

import { Segmented } from "@/components/ui/Segmented";
import { Select } from "@/components/ui/Select";
import type { SessionCatalog, SessionCode } from "@/lib/api/types";
import { isSessionCode } from "@/lib/format";
import { defaultWeekend, findSeason, sessionOptions, weekendOptions, weekendSelection, type Selection } from "./selection";

export type PickerLayout = "row" | "stack";

export interface SessionPickerProps {
  catalog: SessionCatalog | null;
  selection: Selection;
  /**
   * A new session. A new season or weekend drops the driver filter (`keepDriver` false); a new
   * session of the same weekend keeps it. Either way the open corner closes.
   */
  onSelect(selection: Selection, keepDriver: boolean): void;
  /** Gives the radio group a name of its own: the page holds two pickers (inline and in the sheet). */
  instance: string;
  layout: PickerLayout;
}

/**
 * Season, Grand Prix and Session (plan 7.3). A new season opens on its newest weekend with a
 * scored race (else qualifying); a new weekend on its race (else qualifying). Sessions are in the
 * order the weekend ran them; one the model didn't score says "(not scored)" and can't be picked.
 */
export function SessionPicker({ catalog, selection, onSelect, instance, layout }: SessionPickerProps) {
  const row = layout === "row";
  const seasons = catalog?.seasons.map((s) => s.year) ?? [];
  if (!seasons.includes(selection.year)) seasons.unshift(selection.year);

  const pickSeason = (value: string) => {
    const season = catalog ? findSeason(catalog, Number(value)) : undefined;
    const weekend = season ? defaultWeekend(season) : undefined;
    if (season && weekend) onSelect(weekendSelection(season.year, weekend), false);
  };

  const pickWeekend = (event: string) => {
    const weekend = catalog ? findSeason(catalog, selection.year)?.weekends.find((w) => w.event === event) : undefined;
    if (weekend) onSelect(weekendSelection(selection.year, weekend), false);
  };

  const pickSession = (code: SessionCode) => {
    if (isSessionCode(code)) onSelect({ ...selection, session: code }, true);
  };

  return (
    <>
      <Select
        label="Season"
        value={String(selection.year)}
        onChange={pickSeason}
        disabled={!catalog}
        options={seasons.map((year) => ({ value: String(year), label: String(year) }))}
        className={row ? "w-28 shrink-0" : undefined}
      />
      <Select
        label="Grand Prix"
        value={selection.event}
        onChange={pickWeekend}
        disabled={!catalog}
        options={weekendOptions(catalog, selection.year, selection.event)}
        className={row ? "min-w-0 flex-[2_1_19rem]" : undefined}
      />
      <Segmented<SessionCode>
        legend="Session"
        name={`session-${instance}`}
        value={selection.session}
        onChange={pickSession}
        options={sessionOptions(catalog, selection).map((o) => ({ ...o, value: o.value as SessionCode }))}
        className={row ? "shrink-0" : undefined}
      />
    </>
  );
}
