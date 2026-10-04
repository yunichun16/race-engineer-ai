"use client";

import { useSyncExternalStore } from "react";
import { currentTheme, subscribeTheme, type Theme } from "@/lib/theme";

/**
 * The reader's theme choice, kept current across this tab and others. The server (and the first
 * client render, during hydration) sees "system", so the markup always matches; the stored
 * choice follows straight after hydration. Change it with `setTheme` from `@/lib/theme`.
 */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribeTheme, currentTheme, () => "system");
}
