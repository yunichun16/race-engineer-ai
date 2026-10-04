"use client";

import { useId, useState } from "react";
import { Button } from "@/components/ui/Button";
import { CONTROL_CLASS, Field, hintId } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import type { ResourceState } from "@/lib/api/resource";
import type { SessionDrivers } from "@/lib/api/types";
import { LIMITS, type Limit } from "./query";
import { driverOptions } from "./selection";
import type { PickerLayout } from "./SessionPicker";

export interface DriverPickerProps {
  /** The session's drivers (/api/catalog/drivers). */
  drivers: ResourceState<SessionDrivers>;
  /** The list filter; undefined is the whole field. */
  driver: string | undefined;
  limit: Limit;
  onDriver(driver: string | undefined): void;
  onLimit(limit: Limit): void;
  layout: PickerLayout;
}

const CODE = /^[A-Za-z]{3}$/;

/**
 * The fallback when the session's drivers can't be listed: a three-letter code typed in, applied
 * with Enter or Apply (empty for the whole field). The tool's answer names the session's drivers
 * when a code isn't one of them.
 */
function DriverCode({ driver, onDriver, className }: Pick<DriverPickerProps, "driver" | "onDriver"> & { className?: string }) {
  const id = useId();
  const [invalid, setInvalid] = useState(false);
  return (
    <form
      className={className}
      onSubmit={(event) => {
        event.preventDefault();
        const text = String(new FormData(event.currentTarget).get("driver") ?? "").trim();
        if (text && !CODE.test(text)) {
          setInvalid(true);
          return;
        }
        setInvalid(false);
        onDriver(text ? text.toUpperCase() : undefined);
      }}
    >
      <Field
        label="Driver code"
        hint={invalid ? "Three letters, such as LEC." : "The driver list didn't load: type a code, such as LEC, or leave it empty for the whole field."}
        htmlFor={id}
      >
        <div className="flex gap-2">
          <input
            key={driver ?? ""}
            id={id}
            name="driver"
            defaultValue={driver ?? ""}
            maxLength={3}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            aria-invalid={invalid || undefined}
            aria-describedby={hintId(id)}
            className={`${CONTROL_CLASS} uppercase`}
          />
          <Button type="submit" variant="secondary">
            Apply
          </Button>
        </div>
      </Field>
    </form>
  );
}

/**
 * Driver (the whole field, or one driver's list: "LEC · Ferrari") and how many rows to show
 * (plan 7.3). Both only replace the current history entry.
 */
export function DriverPicker({ drivers, driver, limit, onDriver, onLimit, layout }: DriverPickerProps) {
  const row = layout === "row";
  const list = drivers.status === "ok" ? drivers.data : drivers.status === "idle" ? null : (drivers.stale ?? null);
  return (
    <>
      {drivers.status === "error" ? (
        <DriverCode driver={driver} onDriver={onDriver} className={row ? "min-w-0 flex-[1_1_14rem]" : undefined} />
      ) : (
        <Select
          label="Driver"
          value={driver ?? ""}
          onChange={(value) => onDriver(value || undefined)}
          options={driverOptions(list, driver)}
          className={row ? "min-w-0 flex-[1_1_11rem]" : undefined}
        />
      )}
      <Select
        label="Show"
        value={String(limit)}
        onChange={(value) => {
          const n = LIMITS.find((l) => String(l) === value);
          if (n) onLimit(n);
        }}
        options={LIMITS.map((n) => ({ value: String(n), label: `Top ${n}` }))}
        className={row ? "w-32 shrink-0" : undefined}
      />
    </>
  );
}
