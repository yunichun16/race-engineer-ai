"use client";

import { useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Disclosure } from "@/components/ui/Disclosure";
import { CONTROL_CLASS, Field, hintId } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { Select } from "@/components/ui/Select";
import { Loading } from "@/components/status/Loading";
import type { StyleCatalog } from "@/lib/api/types";
import { areTeammates, driverChoices, teammateOf, type DriverChoice } from "./pairs";
import { driverCode } from "./query";

export interface CustomPairProps {
  /** The season's drivers; null when the catalog can't be read, and the codes are typed instead. */
  catalog: StyleCatalog | null;
  /** The catalog is still on its way: the form waits for it rather than asking for codes. */
  loading?: boolean;
  /** The pair on show: the form starts from it. */
  current: { a?: string; b?: string };
  onCompare(a: string, b: string): void;
  defaultOpen?: boolean;
}

/**
 * "Compare any two drivers": two drivers of the season, from different teams if the reader wants.
 * When their teams differ the warning shows as soon as they're picked, before anything is asked
 * of the server. With no catalog, two three-letter codes are typed instead.
 */
export function CustomPair({ catalog, loading = false, current, onCompare, defaultOpen = false }: CustomPairProps) {
  return (
    <Disclosure summary="Compare any two drivers" defaultOpen={defaultOpen}>
      {loading ? (
        <Loading label="Loading the season's drivers…" />
      ) : (
        // A new pair on show, or another season, starts the form again from it.
        <PairForm key={`${catalog?.year ?? "typed"}:${current.a ?? ""}:${current.b ?? ""}`} catalog={catalog} current={current} onCompare={onCompare} />
      )}
    </Disclosure>
  );
}

function teamsText(choice: DriverChoice | undefined): string {
  return choice ? choice.teams.join(" and ") : "";
}

function PairForm({ catalog, current, onCompare }: Omit<CustomPairProps, "defaultOpen" | "loading">) {
  const choices = catalog ? driverChoices(catalog) : [];
  const known = (code: string | undefined) => (code && choices.some((c) => c.driver === code) ? code : undefined);
  const [a, setA] = useState(() => (catalog ? (known(current.a) ?? choices[0]?.driver ?? "") : (current.a ?? "")));
  // B starts as the pair's B, else A's teammate (so the form opens on no warning nobody asked
  // for), else the first other driver.
  const [b, setB] = useState(() =>
    catalog
      ? (known(current.b) ?? teammateOf(catalog, a) ?? choices.find((c) => c.driver !== a)?.driver ?? "")
      : (current.b ?? ""),
  );
  const id = useId();

  const codeA = driverCode(a);
  const codeB = driverCode(b);
  const same = codeA !== undefined && codeA === codeB;
  const ready = codeA !== undefined && codeB !== undefined && !same;
  // Warned here before the request; once this pair is on show, the result carries the warning.
  const onShow = codeA === current.a && codeB === current.b;
  const differentCars = ready && !onShow && catalog ? areTeammates(catalog, codeA, codeB) === false : false;
  const choiceA = choices.find((c) => c.driver === codeA);
  const choiceB = choices.find((c) => c.driver === codeB);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (ready) onCompare(codeA, codeB);
  }

  const options = choices.map((c) => ({ value: c.driver, label: c.label }));
  return (
    // The two fields sit side by side only where the form is wide enough for "VER · Red Bull
    // Racing" (a container query: the form is narrow in the desktop side column, wide on a tablet).
    <form onSubmit={submit} className="@container grid gap-3" noValidate>
      {catalog ? (
        <div className="grid gap-3 @md:grid-cols-2">
          <Select label="Driver A" value={a} onChange={setA} options={options} />
          <Select label="Driver B" value={b} onChange={setB} options={options} />
        </div>
      ) : (
        <div className="grid gap-3 @md:grid-cols-2">
          {(
            [
              ["a", "Driver A", a, setA],
              ["b", "Driver B", b, setB],
            ] as const
          ).map(([key, label, value, set]) => (
            <Field key={key} label={label} hint="A three-letter code, such as HAM" htmlFor={`${id}-${key}`}>
              <input
                id={`${id}-${key}`}
                value={value}
                onChange={(event) => set(event.target.value)}
                aria-describedby={hintId(`${id}-${key}`)}
                aria-invalid={value.trim() !== "" && !driverCode(value) ? true : undefined}
                maxLength={3}
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
                className={`${CONTROL_CLASS} font-mono uppercase`}
              />
            </Field>
          ))}
        </div>
      )}

      {differentCars ? (
        <Notice tone="warn" title="Different cars: these differences mix car and driver.">
          <p>
            {codeA} drove for {teamsText(choiceA)} and {codeB} for {teamsText(choiceB)}, never in the same car together. Only teammates share
            a car, so only their differences isolate driving style.
          </p>
        </Notice>
      ) : null}
      {same ? <p className="text-sm text-muted">Pick two different drivers.</p> : null}
      {!catalog ? (
        <p className="text-sm text-muted">The list of drivers couldn&apos;t be loaded. If the two drove for different teams, their differences mix car and driver.</p>
      ) : null}

      <div>
        <Button type="submit" variant="secondary" disabled={!ready}>
          Compare {ready ? `${codeA} and ${codeB}` : "them"}
        </Button>
      </div>
    </form>
  );
}
