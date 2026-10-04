"use client";

import { setTheme, type Theme } from "@/lib/theme";
import { MoonIcon, SunIcon, SystemIcon } from "@/components/ui/icons";
import { useTheme } from "./useTheme";

const OPTIONS: { value: Theme; label: string; Icon: typeof SunIcon }[] = [
  { value: "system", label: "System", Icon: SystemIcon },
  { value: "light", label: "Light", Icon: SunIcon },
  { value: "dark", label: "Dark", Icon: MoonIcon },
];

export interface ThemeToggleProps {
  /** The radio group's name; each toggle on the page needs its own. */
  name?: string;
  /** Show the words next to the icons (the phone menu has room); otherwise they are for screen readers only. */
  showLabels?: boolean;
  className?: string;
}

/**
 * System, Light or Dark, as a radio group with the legend "Theme". Each choice is an icon with
 * its name visible or visually hidden, and a 44 px target. The chosen one is a raised glass
 * segment with a 3:1 ring (an outline in forced colours, where fills are dropped).
 */
export function ThemeToggle({ name = "theme", showLabels = false, className }: ThemeToggleProps) {
  const theme = useTheme();
  return (
    <fieldset className={["min-w-0", className].filter(Boolean).join(" ")}>
      <legend className={showLabels ? "mb-2 text-sm font-medium text-fg" : "sr-only"}>Theme</legend>
      <div className={["flex rounded-full bg-hairline", showLabels ? "w-full" : ""].join(" ")}>
        {OPTIONS.map(({ value, label, Icon }) => (
          <label
            key={value}
            title={showLabels ? undefined : label}
            className={
              "relative inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center gap-2 rounded-full text-muted hover:text-fg " +
              "has-checked:bg-glass-strong has-checked:text-fg " +
              "has-checked:shadow-[inset_0_1px_0_var(--highlight),inset_0_0_0_1px_var(--color-border-secondary)] " +
              "has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-focus " +
              "forced-colors:has-checked:outline-2 forced-colors:has-checked:outline-[Highlight] " +
              (showLabels ? "flex-1 px-3 text-[15px] font-medium" : "")
            }
          >
            <input
              type="radio"
              className="sr-only"
              name={name}
              value={value}
              checked={theme === value}
              onChange={() => setTheme(value)}
            />
            <Icon className="size-[18px] shrink-0" />
            <span className={showLabels ? "" : "sr-only"}>{label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
