import { test } from "node:test";
import assert from "node:assert/strict";
import { THEME_KEY, THEME_SCRIPT, applyTheme, currentTheme, parseAttribute, parseStored, setTheme, subscribeTheme } from "./theme.ts";

/** Just enough of `<html>` for the theme code: attributes. */
class FakeRoot {
  attrs = new Map<string, string>();
  setAttribute(name: string, value: string) {
    this.attrs.set(name, value);
  }
  removeAttribute(name: string) {
    this.attrs.delete(name);
  }
  getAttribute(name: string) {
    return this.attrs.get(name) ?? null;
  }
}

function fakeStorage(start: Record<string, string> = {}, blocked = false) {
  const data = new Map(Object.entries(start));
  const check = () => {
    if (blocked) throw new Error("SecurityError: storage is blocked");
  };
  return {
    data,
    getItem: (k: string) => (check(), data.get(k) ?? null),
    setItem: (k: string, v: string) => (check(), void data.set(k, v)),
    removeItem: (k: string) => (check(), void data.delete(k)),
  };
}

/** Runs the head script as the browser would, against a fake storage and document. */
function runScript(storage: ReturnType<typeof fakeStorage>): FakeRoot {
  const root = new FakeRoot();
  new Function("localStorage", "document", THEME_SCRIPT)(storage, { documentElement: root });
  return root;
}

test("the head script makes Dark the default: nothing stored, junk and blocked storage all mean Dark", () => {
  assert.equal(runScript(fakeStorage()).getAttribute("data-theme"), "dark");
  for (const junk of ["Dark", "", "<script>", "LIGHT", "blue"]) {
    assert.equal(runScript(fakeStorage({ [THEME_KEY]: junk })).getAttribute("data-theme"), "dark", junk);
  }
});

test("the head script applies a saved Light or Dark, and leaves the attribute off for System", () => {
  assert.equal(runScript(fakeStorage({ [THEME_KEY]: "dark" })).getAttribute("data-theme"), "dark");
  assert.equal(runScript(fakeStorage({ [THEME_KEY]: "light" })).getAttribute("data-theme"), "light");
  assert.equal(runScript(fakeStorage({ [THEME_KEY]: "system" })).getAttribute("data-theme"), null);
});

test("the head script survives blocked storage, with the default Dark", () => {
  assert.equal(runScript(fakeStorage({ [THEME_KEY]: "light" }, true)).getAttribute("data-theme"), "dark");
});

test("parseStored reads light and system as themselves and anything else, nothing included, as Dark", () => {
  assert.equal(parseStored("light"), "light");
  assert.equal(parseStored("system"), "system");
  for (const other of [null, undefined, "", "dark", "LIGHT", "System", "blue"]) assert.equal(parseStored(other), "dark");
});

test("parseAttribute reads light and dark as themselves and no attribute as System", () => {
  // Kept apart from parseStored: with System active there is no attribute, and the store must
  // report System, not the stored-value default.
  assert.equal(parseAttribute("light"), "light");
  assert.equal(parseAttribute("dark"), "dark");
  for (const other of [null, undefined, "", "system", "LIGHT", "blue"]) assert.equal(parseAttribute(other), "system");
});

test("applyTheme sets and removes data-theme", () => {
  const root = new FakeRoot();
  applyTheme("dark", root as unknown as HTMLElement);
  assert.equal(root.getAttribute("data-theme"), "dark");
  applyTheme("system", root as unknown as HTMLElement);
  assert.equal(root.getAttribute("data-theme"), null);
});

test("setTheme saves, applies, and still applies when storage is blocked", () => {
  const g = globalThis as Record<string, unknown>;
  const before = { window: g.window, document: g.document };
  try {
    const root = new FakeRoot();
    const storage = fakeStorage();
    g.document = { documentElement: root };
    g.window = { localStorage: storage };
    setTheme("dark");
    assert.equal(storage.data.get(THEME_KEY), "dark");
    assert.equal(root.getAttribute("data-theme"), "dark");
    // System is saved as the word: nothing saved now means Dark.
    setTheme("system");
    assert.equal(storage.data.get(THEME_KEY), "system");
    assert.equal(root.getAttribute("data-theme"), null);
    assert.equal(currentTheme(), "system");

    g.window = { localStorage: fakeStorage({}, true) };
    setTheme("light");
    assert.equal(root.getAttribute("data-theme"), "light");
  } finally {
    g.window = before.window;
    g.document = before.document;
  }
});

test("subscribers hear changes from this tab and from other tabs, and the listener comes off", () => {
  const g = globalThis as Record<string, unknown>;
  const before = { window: g.window, document: g.document };
  try {
    const root = new FakeRoot();
    const storage = fakeStorage();
    const handlers = new Map<string, (event: { key: string | null }) => void>();
    g.document = { documentElement: root };
    g.window = {
      localStorage: storage,
      addEventListener: (type: string, fn: (event: { key: string | null }) => void) => handlers.set(type, fn),
      removeEventListener: (type: string) => handlers.delete(type),
    };
    let heard = 0;
    const off = subscribeTheme(() => heard++);
    assert.ok(handlers.has("storage"));

    setTheme("light");
    assert.equal(heard, 1);
    assert.equal(currentTheme(), "light");

    // Another tab saved Dark: this one follows.
    storage.data.set(THEME_KEY, "dark");
    handlers.get("storage")?.({ key: THEME_KEY });
    assert.equal(heard, 2);
    assert.equal(currentTheme(), "dark");
    // Another key changing is none of our business.
    handlers.get("storage")?.({ key: "re.chat" });
    assert.equal(heard, 2);
    // Another tab chose System: the attribute comes off.
    storage.data.set(THEME_KEY, "system");
    handlers.get("storage")?.({ key: THEME_KEY });
    assert.equal(currentTheme(), "system");
    // Another tab cleared storage: back to the default, Dark.
    storage.data.clear();
    handlers.get("storage")?.({ key: null });
    assert.equal(currentTheme(), "dark");

    off();
    assert.equal(handlers.has("storage"), false);
    setTheme("dark");
    assert.equal(heard, 4);
  } finally {
    g.window = before.window;
    g.document = before.document;
  }
});

test("the browser bar colour follows the page: on the first subscribe, on each change, on a system switch under System and after a client navigation", () => {
  const g = globalThis as Record<string, unknown>;
  const before = { window: g.window, document: g.document, getComputedStyle: g.getComputedStyle, MutationObserver: g.MutationObserver };
  try {
    const root = new FakeRoot();
    // What tokens.css gives --page: Dark on the attribute, Light on the attribute, and under
    // System whatever the operating system says.
    let systemDark = false;
    const page = () => {
      const theme = root.getAttribute("data-theme");
      return theme === "dark" || (theme === null && systemDark) ? " #070a12" : " #edf1f5";
    };
    const meta = {
      content: "#070a12",
      getAttribute: () => meta.content,
      setAttribute: (_name: string, value: string) => void (meta.content = value),
    };
    const mediaHandlers = new Set<() => void>();
    // Next re-renders the head on client navigation; the store watches it.
    const watchers: { fn: () => void; on: boolean }[] = [];
    g.MutationObserver = class {
      watcher: { fn: () => void; on: boolean };
      constructor(fn: () => void) {
        this.watcher = { fn, on: false };
        watchers.push(this.watcher);
      }
      observe() {
        this.watcher.on = true;
      }
      disconnect() {
        this.watcher.on = false;
      }
    };
    g.document = { documentElement: root, head: {}, querySelectorAll: () => [meta] };
    g.getComputedStyle = () => ({ getPropertyValue: (name: string) => (name === "--page" ? page() : "") });
    g.window = {
      localStorage: fakeStorage({ [THEME_KEY]: "light" }),
      addEventListener: () => {},
      removeEventListener: () => {},
      matchMedia: () => ({
        addEventListener: (_type: string, fn: () => void) => mediaHandlers.add(fn),
        removeEventListener: (_type: string, fn: () => void) => mediaHandlers.delete(fn),
      }),
    };

    // The head script chose Light; the server sent the dark bar colour.
    root.setAttribute("data-theme", "light");
    const off = subscribeTheme(() => {});
    assert.equal(meta.content, "#edf1f5");
    assert.equal(mediaHandlers.size, 1);

    setTheme("dark");
    assert.equal(meta.content, "#070a12");

    // System on a light system, then the system turns dark.
    setTheme("system");
    assert.equal(meta.content, "#edf1f5");
    systemDark = true;
    for (const fn of mediaHandlers) fn();
    assert.equal(meta.content, "#070a12");

    // Under a chosen theme a system switch changes nothing.
    setTheme("light");
    systemDark = false;
    for (const fn of mediaHandlers) fn();
    assert.equal(meta.content, "#edf1f5");

    // A client navigation puts the server's dark colour back in the head: it is set again.
    meta.content = "#070a12";
    for (const w of watchers) if (w.on) w.fn();
    assert.equal(meta.content, "#edf1f5");

    off();
    assert.equal(mediaHandlers.size, 0);
    assert.ok(watchers.every((w) => !w.on));
  } finally {
    g.window = before.window;
    g.document = before.document;
    g.getComputedStyle = before.getComputedStyle;
    g.MutationObserver = before.MutationObserver;
  }
});

test("a theme switch holds transitions off while the page restyles, then lets them back", () => {
  // The order matters: the class goes on, the attribute changes, a layout read restyles the page
  // with transitions off, and only then does the class come off.
  const steps: string[] = [];
  const classes = new Set<string>();
  class Root extends FakeRoot {
    classList = {
      add: (c: string) => void (classes.add(c), steps.push(`add ${c}`)),
      remove: (c: string) => void (classes.delete(c), steps.push(`remove ${c}`)),
    };
    get offsetWidth() {
      steps.push(`restyle with ${classes.has("theme-switching") ? "transitions off" : "transitions on"}, theme ${this.getAttribute("data-theme")}`);
      return 1280;
    }
  }
  const root = new Root();
  applyTheme("light", root as unknown as HTMLElement);
  assert.deepEqual(steps, ["add theme-switching", "restyle with transitions off, theme light", "remove theme-switching"]);
  assert.equal(classes.size, 0);
});

test("the first subscriber puts back a saved theme that a rebuilt <html> lost", () => {
  // A render error during hydration makes React rebuild the page from the root, and the new
  // <html> has no data-theme: the page would fall back to System while Light is saved.
  const g = globalThis as Record<string, unknown>;
  const before = { window: g.window, document: g.document };
  try {
    const root = new FakeRoot();
    g.document = { documentElement: root };
    g.window = { localStorage: fakeStorage({ [THEME_KEY]: "light" }), addEventListener: () => {}, removeEventListener: () => {} };
    const off = subscribeTheme(() => {});
    assert.equal(root.getAttribute("data-theme"), "light");
    assert.equal(currentTheme(), "light");
    off();

    // System saved and no attribute: nothing to put back.
    const plain = new FakeRoot();
    g.document = { documentElement: plain };
    g.window = { localStorage: fakeStorage({ [THEME_KEY]: "system" }), addEventListener: () => {}, removeEventListener: () => {} };
    subscribeTheme(() => {})();
    assert.equal(plain.getAttribute("data-theme"), null);
  } finally {
    g.window = before.window;
    g.document = before.document;
  }
});
