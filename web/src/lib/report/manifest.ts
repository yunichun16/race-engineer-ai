/**
 * Which files in `report/` the site publishes at `/report/<slug>`, grouped as `/report` lists them.
 * The slug is the file name without `.md`, with `_` turned into `-`. A test fails if a report file
 * is in neither `REPORTS` nor `EXCLUDED`, so a new report (M7's technical report and model card)
 * can't be forgotten.
 *
 * The site quotes no accuracy figure (plan 13), so the human review (`m4_review.md`) is not
 * published, and `m4_summary.md`, the technical report and the model card are published without
 * their human-checked precision: each entry lists what to leave out, and `omit.ts` takes it out of
 * the parsed page. `manifest.test.ts` fails when an omitted heading or sentence stops appearing
 * word for word in its file, so a changed report gets looked at instead of being shown with the
 * figure back in.
 */

export type ReportGroup = "Summaries" | "Mistakes" | "Driving style" | "The 2026 rule change" | "Data" | "Engineering";

/** Parts of a report the site leaves out. Each must appear in the file exactly as written here. */
export interface Omission {
  /** Headings, as written after the `#`s: the heading and everything under it, up to the next heading of the same or a higher level. */
  sections?: readonly string[];
  /** Whole sentences, with single spaces where the file wraps its lines. */
  sentences?: readonly string[];
}

export interface ReportEntry {
  file: string; // in report/
  slug: string;
  group: ReportGroup;
  label?: string; // a shorter name for lists; otherwise the file's `# ` title is used
  omit?: Omission;
}

export const GROUPS: readonly ReportGroup[] = [
  "Summaries",
  "Mistakes",
  "Driving style",
  "The 2026 rule change",
  "Data",
  "Engineering",
];

/** Report files the site leaves out on purpose, with why. A link to one shows as a file in the repository. */
export const EXCLUDED: readonly string[] = [
  "m3_ablations_smoke.md", // a smoke run of the ablation grid, not a result
  "m7_chat_accuracy.md", // the chat accuracy check; the site quotes no accuracy figure until the user decides (plan 13)
];

export function slugForFile(file: string): string {
  return file.replace(/\.md$/, "").replaceAll("_", "-");
}

function entry(file: string, group: ReportGroup, label?: string, omit?: Omission): ReportEntry {
  const out: ReportEntry = { file, slug: slugForFile(file), group };
  if (label !== undefined) out.label = label;
  if (omit !== undefined) out.omit = omit;
  return out;
}

/** The human review's section, which the technical report and the model card keep only in the repository (M7 decision 12). */
const HUMAN_CHECK: Omission = { sections: ["Human-checked precision"] };

/** In the order `/report` lists them. */
export const REPORTS: readonly ReportEntry[] = [
  entry("technical_report.md", "Summaries", "Technical report", HUMAN_CHECK),
  entry("model_card.md", "Summaries", "Model card", HUMAN_CHECK),
  entry("m4_summary.md", "Summaries", "From flagged corners to explanations", {
    sections: ["Human-checked precision"],
    sentences: [
      "What is left is mostly real: about 7 in 10 of the top findings, with race-control findings far more reliable than telemetry-only ones.",
      "The intervals are wide, and some verdicts come from the traces alone (7 of 50).",
    ],
  }),
  entry("m3_summary.md", "Summaries", "What the Transformer learned"),
  entry("m4_mistakes.md", "Mistakes"),
  entry("m4_scoring.md", "Mistakes"),
  entry("m3_results.md", "Mistakes"),
  entry("baselines.md", "Mistakes"),
  entry("m3_ablations.md", "Mistakes"),
  entry("m4_style.md", "Driving style"),
  entry("m3_style_within_season.md", "Driving style"),
  entry("m3_shift.md", "The 2026 rule change"),
  entry("dataset_card.md", "Data"),
  entry("data_quality.md", "Data"),
  entry("m4_onnx.md", "Engineering"),
  entry("m5_backend.md", "Engineering"),
  entry("m6_website.md", "Engineering", "M6: the website"),
  entry("m7_ship.md", "Engineering"),
];

/** The published report with this slug. An exact match only: `M4-SUMMARY` or `constructor` find nothing. */
export function reportBySlug(slug: string): ReportEntry | undefined {
  return REPORTS.find((r) => r.slug === slug);
}

export function reportByFile(file: string): ReportEntry | undefined {
  return REPORTS.find((r) => r.file === file);
}

/** The groups in order, each with its reports; empty groups are left out. */
export function reportsByGroup(): { group: ReportGroup; reports: ReportEntry[] }[] {
  return GROUPS.map((group) => ({ group, reports: REPORTS.filter((r) => r.group === group) })).filter(
    (g) => g.reports.length > 0,
  );
}

/** The reports before and after this one in the list, for the links at the foot of a page. */
export function neighbours(slug: string): { previous: ReportEntry | null; next: ReportEntry | null } {
  const at = REPORTS.findIndex((r) => r.slug === slug);
  if (at < 0) return { previous: null, next: null };
  return { previous: REPORTS[at - 1] ?? null, next: REPORTS[at + 1] ?? null };
}
