/**
 * Query strings for the site's own URLs and the API's.
 *
 * `buildQuery` gives one canonical form: empty values dropped, keys sorted, so equal parameters
 * always make the same URL (and the same browser cache entry). `setQuery` writes the explorers'
 * state into the address bar with `history.pushState` or `replaceState`, which Next.js syncs with
 * `useSearchParams` without asking the server for anything.
 */

export type QueryValue = string | number | boolean | null | undefined;

export type QueryInput = Readonly<Record<string, QueryValue>>;

/** What the codecs read from: `URLSearchParams` and Next's `ReadonlyURLSearchParams` both fit. */
export interface QueryLike {
  get(name: string): string | null;
}

/** The parts of `window` that `setQuery` uses, so a test can pass a fake one. */
export interface HistoryHost {
  location: { pathname: string; search: string; hash: string };
  history: {
    pushState(data: unknown, unused: string, url?: string | null): void;
    replaceState(data: unknown, unused: string, url?: string | null): void;
  };
}

// encodeURIComponent, except that ":" stays as it is (it is allowed in a query, and the open
// corner reads better as "ALB:51:11") and a space is written "+", as forms do. Both decode the
// same in URLSearchParams and in the API.
function encodePart(text: string): string {
  return encodeURIComponent(text).replace(/%3A/gi, ":").replace(/%20/g, "+");
}

function present(value: QueryValue): value is string | number | boolean {
  if (value === undefined || value === null || value === "") return false;
  return typeof value !== "number" || Number.isFinite(value);
}

/** The non-empty values as strings, keys sorted. */
function canonical(params: QueryInput): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of Object.keys(params).sort()) {
    const value = params[key];
    if (present(value)) out[key] = String(value);
  }
  return out;
}

/**
 * "a=1&event=Azerbaijan+Grand+Prix", with no leading "?". Drops `undefined`, `null`, `""` and
 * numbers that aren't finite, and sorts the keys. It never adds a key.
 */
export function buildQuery(params: QueryInput): string {
  return Object.entries(canonical(params))
    .map(([key, value]) => `${encodePart(key)}=${encodePart(value)}`)
    .join("&");
}

/** The path with its query, or the bare path when every value is empty. */
export function withQuery(path: string, params: QueryInput): string {
  const query = buildQuery(params);
  return query ? `${path}?${query}` : path;
}

/** A search string ("?a=1&b=2" or "a=1") as an object; the first value wins for a repeated key. */
export function readQuery(search: string): Record<string, string> {
  const out: Record<string, string> = {};
  new URLSearchParams(search).forEach((value, key) => {
    if (!(key in out)) out[key] = value;
  });
  return out;
}

/**
 * Make `next` the page's whole query, keeping the path and the hash. `push` adds a history entry
 * (Back undoes it), otherwise the current entry is replaced. Nothing happens when the query
 * wouldn't change, so Back never has to step through duplicates. Returns whether it changed.
 */
export function setQuery(
  next: Record<string, string | undefined>,
  opts: { push: boolean },
  host: HistoryHost = window,
): boolean {
  const query = buildQuery(next);
  if (query === buildQuery(readQuery(host.location.search))) return false;
  const url = `${host.location.pathname}${query ? `?${query}` : ""}${host.location.hash}`;
  if (opts.push) host.history.pushState(null, "", url);
  else host.history.replaceState(null, "", url);
  return true;
}

/**
 * How to record a change of state: "push" when any of `pushKeys` changed (a new selection Back
 * should undo), "replace" when only other keys did (a toggle), null when nothing did.
 */
export function historyMode(
  prev: QueryInput,
  next: QueryInput,
  pushKeys: readonly string[],
): "push" | "replace" | null {
  const a = canonical(prev);
  const b = canonical(next);
  let changed = false;
  for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (a[key] === b[key]) continue;
    if (pushKeys.includes(key)) return "push";
    changed = true;
  }
  return changed ? "replace" : null;
}

/** A whole number from a query value, if it is one between `min` and `max`. */
export function intParam(text: string | null | undefined, min: number, max: number): number | undefined {
  if (text === null || text === undefined || !/^\d{1,9}$/.test(text.trim())) return undefined;
  const n = Number(text.trim());
  return n >= min && n <= max ? n : undefined;
}

/** One of `values`, matched without regard to case and returned as listed. */
export function pickParam<T extends string>(
  text: string | null | undefined,
  values: readonly T[],
): T | undefined {
  if (text === null || text === undefined) return undefined;
  const wanted = text.trim().toLowerCase();
  return values.find((v) => v.toLowerCase() === wanted);
}
