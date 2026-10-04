/**
 * Reads the report files at build time, for `/report/[slug]`, `/report/figures/[file]` and the
 * lists on `/report`. `next build` runs in web/, so the repository's `report/` folder is one level
 * up. This is a file read, not an import, so Turbopack's project-root limit doesn't apply. Not
 * covered by `node --test` (it imports "server-only"); the modules it calls are.
 */
import "server-only";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";
import { REPORTS, reportBySlug, type ReportEntry } from "./manifest.ts";
import { parseReport, walkBlocks, type Block } from "./markdown.ts";
import { applyOmission, type PageDoc } from "./omit.ts";
import { resolveImage } from "./links.ts";
import { pngSize, type PngSize } from "./png.ts";

export const REPORT_DIR = path.join(process.cwd(), "..", "report");
const FIGURES_DIR = path.join(REPORT_DIR, "figures");

export interface LoadedReport {
  entry: ReportEntry;
  doc: PageDoc;
  /** The size of every figure the report shows, by file name, so each `<img>` has its width and height. */
  figures: Record<string, PngSize>;
}

/**
 * The entry for a slug. Anything outside the manifest throws, and the page must never call
 * `notFound()` for it instead: on a case-insensitive disk (macOS) `next start` serves
 * `/report/M4-SUMMARY` from the prerendered `m4-summary.html` and re-renders it in the
 * background, and a 404 from that render would overwrite the good page (P0 spike 6).
 */
export function entryForSlug(slug: string): ReportEntry {
  const entry = reportBySlug(slug);
  if (!entry) throw new Error(`Not a report slug: "${slug}" is not in lib/report/manifest.ts`);
  return entry;
}

async function readSource(entry: ReportEntry): Promise<string> {
  try {
    return await readFile(path.join(REPORT_DIR, entry.file), "utf8");
  } catch (cause) {
    throw new Error(`report/${entry.file} is listed in lib/report/manifest.ts but can't be read from ${REPORT_DIR}`, {
      cause,
    });
  }
}

/** A published report, parsed, with its omitted parts taken out. Fails the build with a clear message if its file is missing. */
export const loadReport = cache(async (slug: string): Promise<LoadedReport> => {
  const entry = entryForSlug(slug);
  const source = await readSource(entry);
  let doc: PageDoc;
  try {
    doc = applyOmission(parseReport(source), entry.omit);
  } catch (cause) {
    throw new Error(`report/${entry.file}: ${(cause as Error).message} (see its omit entry in lib/report/manifest.ts)`, {
      cause,
    });
  }
  const figures: Record<string, PngSize> = {};
  const images: string[] = [];
  walkBlocks(
    doc.blocks.filter((b): b is Block => b.type !== "omitted"),
    (b) => {
      if (b.type === "image") images.push(b.src);
    },
  );
  for (const src of images) {
    const target = resolveImage(src);
    if (target.kind === "figure") figures[target.file] = await figureSize(target.file);
  }
  return { entry, doc, figures };
});

/** Every published report's `# ` title, by slug, for lists and the links between pages. */
export const reportTitles = cache(async (): Promise<Record<string, string>> => {
  const out: Record<string, string> = {};
  for (const entry of REPORTS) {
    out[entry.slug] = parseReport(await readSource(entry)).title?.text ?? entry.label ?? entry.slug;
  }
  return out;
});

/** The PNG file names in report/figures, for the figure route's static params. */
export const listFigures = cache(async (): Promise<string[]> => {
  return (await readdir(FIGURES_DIR)).filter((f) => f.endsWith(".png") && !f.startsWith(".")).sort();
});

/**
 * One figure's bytes. Only a name from the folder's own listing is read, compared exactly, so a
 * path, a dotfile or a wrong-case name (which a case-insensitive disk would open) never is.
 */
export async function readFigure(file: string): Promise<Buffer> {
  if (!(await listFigures()).includes(file)) throw new Error(`Not a report figure: "${file}" is not in report/figures`);
  return readFile(path.join(FIGURES_DIR, file));
}

/** A figure's width and height, from its PNG header, for the `<img>` in a FigureCard. */
export async function figureSize(file: string): Promise<PngSize> {
  return pngSize(await readFigure(file));
}
