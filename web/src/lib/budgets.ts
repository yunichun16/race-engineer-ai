/**
 * The size budgets CI enforces (plan 7.6, decision 11): each page's initial JavaScript, read from
 * the production build with no browser and no server.
 *
 * The measure is M6's (`report/m6_website.md` section 4): the `<script src>` chunks a prerendered
 * page references, `noModule` ones left out (modern browsers skip the polyfill), each gzipped at
 * level 9, summed in KiB of 1,024 bytes. M6 fetched the page and its chunks from `next start`;
 * `scripts/budgets.ts` reads the same files from `.next/` instead, and gives the same table on
 * the same build.
 *
 * The limits are "no regression": the M6 baseline plus 5%, rounded up to a whole KiB. Four pages
 * were already over M6's own targets (160 and 190), and trimming toward those is not M7 work.
 *
 * Pure: the script does the reading and the gzipping.
 */

export interface Budget {
  /** The page as a visitor opens it; "/report/<slug>" stands for the first published report. */
  page: string;
  /** M6's measurement, in KiB to one decimal. */
  baseline: number;
  /** The limit, in KiB: `limitFor(baseline)`. */
  limit: number;
}

/** "/report/<slug>" in `BUDGETS`: the script measures the first slug of the report manifest. */
export const SLUG_PAGE = "/report/<slug>";

/** The baseline plus 5%, rounded up to a whole KiB (counted in tenths, so no float lands on a whole number by accident). */
export function limitFor(baseline: number): number {
  const tenths = Math.round(baseline * 10);
  return Math.ceil((tenths * 105) / 1000);
}

function budget(page: string, baseline: number): Budget {
  return { page, baseline, limit: limitFor(baseline) };
}

/** M6's table as merged (`report/m6_website.md` section 4; Gate M): 196, 205, 201, 202, 159 and 147 KiB. */
export const BUDGETS: readonly Budget[] = [
  budget("/", 185.8),
  budget("/chat", 194.3),
  budget("/mistakes", 190.7),
  budget("/styles", 191.5),
  budget("/report", 151.1),
  budget(SLUG_PAGE, 139.1),
];

/** Where `next build` writes a prerendered page, relative to `.next/server/app/`: "/" is "index.html". */
export function htmlFileFor(page: string): string {
  const trimmed = page.replace(/^\/+|\/+$/g, "");
  return `${trimmed || "index"}.html`;
}

function decodeAttribute(value: string): string {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&quot;", '"')
    .replaceAll("&#x27;", "'")
    .replaceAll("&#39;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">");
}

/**
 * The `src` of every `<script src>` in the page that a modern browser runs, in order and each
 * once: tags with `noModule` are left out, and attribute entities are decoded.
 */
export function scriptChunks(html: string): string[] {
  const out: string[] = [];
  for (const match of html.matchAll(/<script\b[^>]*>/gi)) {
    const tag = match[0];
    if (/\bnomodule\b/i.test(tag)) continue;
    const src = /\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(tag);
    if (!src) continue; // an inline script: not a chunk
    const value = decodeAttribute(src[1] ?? src[2] ?? "");
    if (value && !out.includes(value)) out.push(value);
  }
  return out;
}

/**
 * A chunk's file relative to the build directory: "/_next/static/chunks/a.js?dpl=x" is
 * "static/chunks/a.js". Null for anything that isn't one of the build's own files (another
 * origin, or a path outside `/_next/`), which the script reports instead of skipping.
 */
export function chunkFile(src: string): string | null {
  const path = src.split(/[?#]/, 1)[0];
  if (!path.startsWith("/_next/")) return null;
  const rest = path.slice("/_next/".length);
  if (!rest || rest.split("/").some((part) => part === "" || part === "." || part === "..")) return null;
  return rest;
}

export interface Measured {
  page: string;
  /** The page measured: the slug's real path for `SLUG_PAGE`. */
  path: string;
  bytes: number;
  chunks: number;
}

export interface Row extends Budget {
  path: string;
  kib: number;
  chunks: number;
  ok: boolean;
}

/** Bytes as KiB to one decimal, the unit of the M6 table. */
export function kib(bytes: number): number {
  return Math.round((bytes / 1024) * 10) / 10;
}

/** Each budget with its measurement; a page that wasn't measured is a failure. */
export function evaluate(measured: readonly Measured[], budgets: readonly Budget[] = BUDGETS): Row[] {
  return budgets.map((b) => {
    const m = measured.find((candidate) => candidate.page === b.page);
    if (!m) return { ...b, path: b.page, kib: Number.NaN, chunks: 0, ok: false };
    const size = kib(m.bytes);
    return { ...b, path: m.path, kib: size, chunks: m.chunks, ok: m.bytes <= b.limit * 1024 };
  });
}

/** The table the script prints: page, measured, baseline, limit, chunks, result. */
export function formatTable(rows: readonly Row[]): string {
  const head = ["page", "KiB", "M6", "limit", "chunks", ""];
  const body = rows.map((r) => [
    r.path,
    Number.isNaN(r.kib) ? "-" : r.kib.toFixed(1),
    r.baseline.toFixed(1),
    String(r.limit),
    String(r.chunks),
    r.ok ? "ok" : Number.isNaN(r.kib) ? "NOT MEASURED" : "OVER",
  ]);
  const widths = head.map((h, k) => Math.max(h.length, ...body.map((cells) => cells[k].length)));
  const line = (cells: string[]) =>
    cells
      .map((c, k) => (k >= 1 && k <= 4 ? c.padStart(widths[k]) : c.padEnd(widths[k])))
      .join("  ")
      .trimEnd();
  return [line(head), ...body.map(line)].join("\n");
}
