// Development-only counters for the gallery's lifecycle panel (plan 4.6): what the charts leave
// running, and how often each chart draws.
//
// `installProbe` wraps document.addEventListener / removeEventListener, the ResizeObserver and
// IntersectionObserver constructors and requestAnimationFrame / cancelAnimationFrame, and keeps
// the live ones. It runs when the gallery's module loads, before any chart's code has loaded
// (the charts are lazy), so everything a chart starts is seen. Listeners removed through `once`
// or by garbage collection aren't seen; the charts use neither.
//
// A draw is counted when a chart root goes from empty to having children (or has all its
// children replaced at once), read from a MutationObserver on the whole document. The chart
// clearing itself on unmount isn't a draw; a StrictMode mount (draw, clean up, draw) is two.

export interface Counts {
  listeners: number;
  /** Live document listeners by event type: "keydown: 1". */
  listenerTypes: Record<string, number>;
  resizeObservers: number;
  intersectionObservers: number;
  frames: number;
}

interface DocListener {
  type: string;
  listener: EventListenerOrEventListenerObject;
  capture: boolean;
}

interface Probe {
  listeners: DocListener[];
  resize: Set<ResizeObserver>;
  intersection: Set<IntersectionObserver>;
  frames: Set<number>;
  /** Draws per gallery item (the closest [data-gallery-item]). */
  draws: Map<string, number>;
  /** Children per chart root, as replayed from the mutation records. */
  children: WeakMap<Node, number>;
  version: number;
  subscribers: Set<() => void>;
  pending: ReturnType<typeof setTimeout> | undefined;
}

type ProbeWindow = Window & { __reChartProbe?: Probe };

// Tell subscribers at most every 100 ms: a playing replay asks for a frame 60 times a second.
function changed(probe: Probe): void {
  probe.version++;
  if (probe.pending !== undefined) return;
  probe.pending = setTimeout(() => {
    probe.pending = undefined;
    for (const subscriber of probe.subscribers) subscriber();
  }, 100);
}

function captureOf(options: boolean | AddEventListenerOptions | EventListenerOptions | undefined): boolean {
  return typeof options === "boolean" ? options : Boolean(options?.capture);
}

function wrapDocumentListeners(probe: Probe): void {
  const add = document.addEventListener;
  const remove = document.removeEventListener;
  const drop = (type: string, listener: EventListenerOrEventListenerObject, capture: boolean) => {
    const i = probe.listeners.findIndex((l) => l.type === type && l.listener === listener && l.capture === capture);
    if (i >= 0) probe.listeners.splice(i, 1);
    changed(probe);
  };
  document.addEventListener = function (
    this: Document,
    type: string,
    listener: EventListenerOrEventListenerObject | null,
    options?: boolean | AddEventListenerOptions,
  ) {
    if (listener) {
      const capture = captureOf(options);
      const known = probe.listeners.some((l) => l.type === type && l.listener === listener && l.capture === capture);
      if (!known) probe.listeners.push({ type, listener, capture });
      if (typeof options === "object") options.signal?.addEventListener("abort", () => drop(type, listener, capture));
      changed(probe);
    }
    // A null listener is allowed (and ignored) by the DOM; the overloads leave it out.
    add.call(this, type, listener as EventListenerOrEventListenerObject, options);
  } as Document["addEventListener"];
  document.removeEventListener = function (
    this: Document,
    type: string,
    listener: EventListenerOrEventListenerObject | null,
    options?: boolean | EventListenerOptions,
  ) {
    if (listener) drop(type, listener, captureOf(options));
    remove.call(this, type, listener as EventListenerOrEventListenerObject, options);
  } as Document["removeEventListener"];
}

function wrapObservers(probe: Probe): void {
  const Resize = window.ResizeObserver;
  class CountedResizeObserver extends Resize {
    targets: Set<Element> = new Set();
    observe(target: Element, options?: ResizeObserverOptions): void {
      super.observe(target, options);
      this.targets.add(target);
      probe.resize.add(this);
      changed(probe);
    }
    unobserve(target: Element): void {
      super.unobserve(target);
      this.targets.delete(target);
      if (this.targets.size === 0) probe.resize.delete(this);
      changed(probe);
    }
    disconnect(): void {
      super.disconnect();
      this.targets.clear();
      probe.resize.delete(this);
      changed(probe);
    }
  }
  window.ResizeObserver = CountedResizeObserver;

  const Intersection = window.IntersectionObserver;
  class CountedIntersectionObserver extends Intersection {
    targets: Set<Element> = new Set();
    observe(target: Element): void {
      super.observe(target);
      this.targets.add(target);
      probe.intersection.add(this);
      changed(probe);
    }
    unobserve(target: Element): void {
      super.unobserve(target);
      this.targets.delete(target);
      if (this.targets.size === 0) probe.intersection.delete(this);
      changed(probe);
    }
    disconnect(): void {
      super.disconnect();
      this.targets.clear();
      probe.intersection.delete(this);
      changed(probe);
    }
  }
  window.IntersectionObserver = CountedIntersectionObserver;
}

function wrapFrames(probe: Probe): void {
  const request = window.requestAnimationFrame.bind(window);
  const cancel = window.cancelAnimationFrame.bind(window);
  window.requestAnimationFrame = (callback: FrameRequestCallback): number => {
    const id = request((time) => {
      probe.frames.delete(id);
      changed(probe);
      callback(time);
    });
    probe.frames.add(id);
    changed(probe);
    return id;
  };
  window.cancelAnimationFrame = (id: number): void => {
    probe.frames.delete(id);
    changed(probe);
    cancel(id);
  };
}

function isChartRoot(node: Node): node is HTMLElement {
  return node instanceof HTMLElement && node.classList.contains("re-chart");
}

function watchDraws(probe: Probe): void {
  new MutationObserver((records) => {
    let counted = false;
    for (const record of records) {
      const root = record.target;
      if (record.type !== "childList" || !isChartRoot(root)) continue;
      const before = probe.children.get(root) ?? 0;
      const removed = record.removedNodes.length;
      const added = record.addedNodes.length;
      probe.children.set(root, Math.max(before - removed + added, 0));
      if (added > 0 && (before === 0 || removed >= before)) {
        const item = root.closest("[data-gallery-item]")?.getAttribute("data-gallery-item") ?? "(outside the gallery)";
        probe.draws.set(item, (probe.draws.get(item) ?? 0) + 1);
        counted = true;
      }
    }
    if (counted) changed(probe);
  }).observe(document.documentElement, { childList: true, subtree: true });
}

/** Starts counting (once per page load; a hot reload keeps the first probe). */
export function installProbe(): void {
  const w = window as ProbeWindow;
  if (w.__reChartProbe) return;
  const probe: Probe = {
    listeners: [],
    resize: new Set(),
    intersection: new Set(),
    frames: new Set(),
    draws: new Map(),
    children: new WeakMap(),
    version: 0,
    subscribers: new Set(),
    pending: undefined,
  };
  w.__reChartProbe = probe;
  wrapDocumentListeners(probe);
  wrapObservers(probe);
  wrapFrames(probe);
  watchDraws(probe);
}

function probe(): Probe | undefined {
  return typeof window === "undefined" ? undefined : (window as ProbeWindow).__reChartProbe;
}

/** For `useSyncExternalStore`. */
export function subscribeProbe(callback: () => void): () => void {
  const p = probe();
  p?.subscribers.add(callback);
  return () => {
    p?.subscribers.delete(callback);
  };
}

/** For `useSyncExternalStore`: a number that changes whenever a count does (-1 before install). */
export function probeVersion(): number {
  return probe()?.version ?? -1;
}

export function readCounts(): Counts | null {
  const p = probe();
  if (!p) return null;
  const listenerTypes: Record<string, number> = {};
  for (const l of p.listeners) listenerTypes[l.type] = (listenerTypes[l.type] ?? 0) + 1;
  return {
    listeners: p.listeners.length,
    listenerTypes,
    resizeObservers: p.resize.size,
    intersectionObservers: p.intersection.size,
    frames: p.frames.size,
  };
}

/** How many times the chart in gallery item `item` has drawn since the page loaded. */
export function readDraws(item: string): number {
  return probe()?.draws.get(item) ?? 0;
}

export function totalDraws(): number {
  let total = 0;
  for (const n of probe()?.draws.values() ?? []) total += n;
  return total;
}

/** Whether two counts agree on everything the panel checks. */
export function sameCounts(a: Counts, b: Counts): boolean {
  const types = new Set([...Object.keys(a.listenerTypes), ...Object.keys(b.listenerTypes)]);
  return (
    a.listeners === b.listeners &&
    a.resizeObservers === b.resizeObservers &&
    a.intersectionObservers === b.intersectionObservers &&
    a.frames === b.frames &&
    [...types].every((t) => (a.listenerTypes[t] ?? 0) === (b.listenerTypes[t] ?? 0))
  );
}
