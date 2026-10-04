/**
 * The reader's theme: Dark (the first-visit default), Light, or System (follow the operating
 * system).
 *
 * The choice lives in `localStorage["re-theme"]` and shows as `<html data-theme>`, which
 * tokens.css (and the charts' own tokens) read. `THEME_SCRIPT` runs in the document head before
 * the first paint, so the page never flashes the other theme. Nothing stored (a first visit),
 * blocked storage or junk all mean Dark; a stored "system" leaves the attribute off, and the CSS
 * then follows the operating system.
 *
 * Two readings, kept apart on purpose:
 * - the stored value (`parseStored`) maps missing, blocked or junk to Dark;
 * - the attribute (`parseAttribute`) maps no attribute to System, because the head script leaves
 *   it off only for a stored "system".
 *
 * No React here: the root layout (a server component) imports `THEME_SCRIPT` from this file. The
 * hook that reads the store, `useTheme`, is in `components/layout/useTheme.ts`, a client module.
 */

export type Theme = "system" | "light" | "dark";

export const THEMES: readonly Theme[] = ["system", "light", "dark"];

export const THEME_KEY = "re-theme";

/**
 * Inlined in the root layout's <head>. It is a constant, the only inline script the app writes
 * itself; the production Content Security Policy allows it with 'unsafe-inline' (src/lib/csp.ts),
 * since Next's own inline payload scripts change with every build.
 */
export const THEME_SCRIPT = `(function(){var t;try{t=localStorage.getItem("${THEME_KEY}")}catch(e){}if(t!=="light"&&t!=="system")t="dark";if(t!=="system")document.documentElement.setAttribute("data-theme",t)})()`;

/** A stored value as a theme: "light" and "system" as themselves, anything else (nothing stored included) as Dark. */
export function parseStored(value: string | null | undefined): Theme {
  return value === "light" || value === "system" ? value : "dark";
}

/** The `data-theme` attribute as a theme: "light" or "dark" as themselves, no attribute (or anything else) as System. */
export function parseAttribute(value: string | null | undefined): Theme {
  return value === "light" || value === "dark" ? value : "system";
}

function readStored(): Theme {
  try {
    return parseStored(window.localStorage.getItem(THEME_KEY));
  } catch {
    return "dark"; // storage blocked: the default, as the head script does
  }
}

/**
 * Points the browser's bar colour (`<meta name="theme-color">`) at the page colour now showing,
 * read from the computed `--page`. The server sends the dark page's colour, the first-visit theme.
 */
function syncThemeColor(root: HTMLElement): void {
  if (typeof document === "undefined" || typeof document.querySelectorAll !== "function") return;
  if (typeof getComputedStyle !== "function") return;
  const page = getComputedStyle(root).getPropertyValue("--page").trim();
  if (!page) return;
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    if (meta.getAttribute("content") !== page) meta.setAttribute("content", page);
  }
}

/**
 * Sets or removes `data-theme` on the root element, and the browser bar colour with it. The
 * switch is instant: hover transitions are held off while the new colours apply (the
 * `theme-switching` class, globals.css), so nothing fades between the themes.
 */
export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  const hold = typeof root.classList !== "undefined";
  if (hold) root.classList.add("theme-switching");
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  syncThemeColor(root);
  if (hold) {
    // Reading a layout size makes the browser restyle the whole page now, while transitions are
    // off; putting them back afterwards starts none, because nothing changes any more.
    void root.offsetWidth;
    root.classList.remove("theme-switching");
  }
}

const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

// Another tab changed the theme: follow it here too. A cleared key means Dark, the default.
function onStorage(event: StorageEvent): void {
  if (event.key !== THEME_KEY && event.key !== null) return;
  applyTheme(readStored());
  notify();
}

// The operating system switched between light and dark: under System the page follows by CSS
// alone, but the browser bar colour has to be set again.
function onSystemChange(): void {
  if (currentTheme() === "system") syncThemeColor(document.documentElement);
}

let systemQuery: MediaQueryList | null = null;
let headWatch: MutationObserver | null = null;

// Next re-renders the head's metadata on client navigation, which puts the server's dark bar
// colour back. Watch the head and point it at the page again (a no-op when it already matches,
// so setting it doesn't loop).
function watchHead(): void {
  if (typeof MutationObserver === "undefined" || !document.head) return;
  headWatch = new MutationObserver(() => syncThemeColor(document.documentElement));
  headWatch.observe(document.head, { childList: true, subtree: true, attributes: true, attributeFilter: ["content"] });
}

/** For `useSyncExternalStore`: calls `listener` on every change, here or in another tab. */
export function subscribeTheme(listener: () => void): () => void {
  if (listeners.size === 0) {
    window.addEventListener("storage", onStorage);
    if (typeof window.matchMedia === "function") {
      systemQuery = window.matchMedia("(prefers-color-scheme: dark)");
      systemQuery.addEventListener("change", onSystemChange);
    }
    // First subscriber, straight after hydration. If a render error made React rebuild the page
    // from the root, it rebuilt <html> without the attribute the head script set: put the saved
    // choice back. Then the bar colour: the head script may have chosen Light, or System on a
    // light system, while the server sent the dark one.
    const root = document.documentElement;
    const stored = readStored();
    if (parseAttribute(root.getAttribute("data-theme")) !== stored) applyTheme(stored, root);
    syncThemeColor(root);
    watchHead();
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      window.removeEventListener("storage", onStorage);
      systemQuery?.removeEventListener("change", onSystemChange);
      systemQuery = null;
      headWatch?.disconnect();
      headWatch = null;
    }
  };
}

/** Saves the choice (when storage allows; "system" is saved too, since nothing saved means Dark), applies it at once and tells every `useTheme`. */
export function setTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Storage blocked or full: the theme still applies until the page is left.
  }
  applyTheme(theme);
  notify();
}

/**
 * The current choice in the browser. The attribute is the truth: it is what the head script and
 * `setTheme` wrote, and it still holds when storage is blocked.
 */
export function currentTheme(): Theme {
  return parseAttribute(document.documentElement.getAttribute("data-theme"));
}
