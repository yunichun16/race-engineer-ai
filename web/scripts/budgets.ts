/**
 * Checks each page's initial JavaScript against its budget, after `next build` (plan 7.6,
 * decision 11; CI's web job runs it).
 *
 *   node scripts/budgets.ts [--dist .next]      (npm run budgets, or make budgets)
 *
 * For `/`, `/chat`, `/mistakes`, `/styles`, `/report` and the first published report, it reads
 * the prerendered page from `.next/server/app/`, gzips (level 9) each `<script src>` chunk it
 * references that a modern browser runs, and sums them in KiB, as `report/m6_website.md`
 * measured them (the logic and the limits are in `src/lib/budgets.ts`). It prints the table and
 * exits with 1 when a page is over its limit or can't be measured (no build, a missing chunk).
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { BUDGETS, chunkFile, evaluate, formatTable, htmlFileFor, SLUG_PAGE, scriptChunks, type Measured } from "../src/lib/budgets.ts";
import { REPORTS } from "../src/lib/report/manifest.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function distDir(argv: string[]): string {
  const i = argv.indexOf("--dist");
  return i >= 0 && argv[i + 1] ? path.resolve(argv[i + 1]) : path.resolve(HERE, "../.next");
}

/** The page's chunks, gzipped and summed; or why it couldn't be measured. */
async function measure(dist: string, page: string, at: string): Promise<Measured | { page: string; error: string }> {
  const htmlPath = path.join(dist, "server", "app", htmlFileFor(at));
  let html: string;
  try {
    html = await readFile(htmlPath, "utf8");
  } catch {
    return { page, error: `no prerendered page at ${path.relative(process.cwd(), htmlPath)} (run next build first)` };
  }
  const srcs = scriptChunks(html);
  let bytes = 0;
  for (const src of srcs) {
    const file = chunkFile(src);
    if (!file) return { page, error: `a script outside the build: ${src}` };
    try {
      bytes += gzipSync(await readFile(path.join(dist, file)), { level: 9 }).length;
    } catch {
      return { page, error: `missing chunk ${src}` };
    }
  }
  return { page, path: at, bytes, chunks: srcs.length };
}

async function main(): Promise<number> {
  const dist = distDir(process.argv.slice(2));
  const slug = REPORTS[0]?.slug;
  const measured: Measured[] = [];
  const problems: string[] = [];
  for (const { page } of BUDGETS) {
    const at = page === SLUG_PAGE ? (slug ? `/report/${slug}` : null) : page;
    if (!at) {
      problems.push(`${page}: the report manifest is empty`);
      continue;
    }
    const got = await measure(dist, page, at);
    if ("error" in got) problems.push(`${page}: ${got.error}`);
    else measured.push(got);
  }
  const rows = evaluate(measured);
  console.log(`Initial JS per page (gzip -9, KiB = 1,024 bytes) from ${path.relative(process.cwd(), dist) || dist}`);
  console.log(formatTable(rows));
  for (const problem of problems) console.log(`  ${problem}`);
  const over = rows.filter((r) => !r.ok);
  if (over.length) console.log(`${over.length} of ${rows.length} pages over budget or not measured.`);
  return over.length ? 1 : 0;
}

process.exitCode = await main();
