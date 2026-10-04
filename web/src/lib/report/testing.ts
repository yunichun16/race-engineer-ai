/**
 * Helpers for the report tests (only tests import this): where the repository's `report/` folder
 * is, and reading it. The tests read the real files, which are in the repository, so CI needs no
 * data and no API.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// This file is web/src/lib/report/testing.ts, so the repository root is four folders up.
// RACE_ENGINEER_REPO overrides it when the tests run from a copy outside the repository.
export const REPO_ROOT = process.env.RACE_ENGINEER_REPO ?? fileURLToPath(new URL("../../../../", import.meta.url));
export const REPORT_DIR = path.join(REPO_ROOT, "report");
export const FIGURES_DIR = path.join(REPORT_DIR, "figures");

export function readReport(file: string): string {
  return readFileSync(path.join(REPORT_DIR, file), "utf8");
}

/** Every Markdown file in report/, sorted. */
export function reportFiles(): string[] {
  return readdirSync(REPORT_DIR)
    .filter((f) => f.endsWith(".md"))
    .sort();
}

/** Every PNG in report/figures, sorted. */
export function figureFiles(): string[] {
  return readdirSync(FIGURES_DIR)
    .filter((f) => f.endsWith(".png"))
    .sort();
}

export function repoFileExists(relative: string): boolean {
  return existsSync(path.join(REPO_ROOT, relative));
}
