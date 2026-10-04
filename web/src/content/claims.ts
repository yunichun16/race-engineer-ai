/**
 * Every number the site states about the project, each pinned to the sentence or table row of
 * `report/` it comes from (plan 9.1).
 *
 * A claim's `quote` must appear word for word in its `source` file, and must contain its
 * `value`. Both are compared after collapsing runs of whitespace, because the reports wrap their
 * lines. `claims.test.ts` checks every claim against the files, so a report that changes turns CI
 * red until the claim is updated (never by loosening the test). Pages show a number only through
 * `<Num id>` (components/findings/Num.tsx), which prints `value`, and link to `claimHref(id)`.
 *
 * The site quotes no accuracy figure (plan 13): one reviewer and 50 findings is too small a sample
 * to headline, so nothing here comes from the human review (`m4_review.md` is not published, and
 * the "Human-checked precision" sections of `m4_summary.md`, the technical report and the model
 * card are left out of their pages). The test fails if a claim quotes the review or any part of a
 * report that the manifest leaves out.
 */

/** The report files a claim may cite. Each is published at /report/<slug> (lib/report/manifest.ts). */
export const REPORT_FILES = [
  "m3_summary.md",
  "m3_results.md",
  "m3_shift.md",
  "m3_style_within_season.md",
  "m4_summary.md",
  "m4_scoring.md",
  "m4_mistakes.md",
  "m4_style.md",
  "baselines.md",
  "dataset_card.md",
] as const;

export type ReportFile = (typeof REPORT_FILES)[number];

export interface Claim {
  value: string; // what the page prints
  quote: string; // verbatim from the source, after collapsing whitespace; contains `value`
  source: ReportFile;
  anchor?: string; // a heading id on /report/<slug>, the section that holds the quote
}

// Quotes shared by several claims (one sentence, several numbers in it).
const PROBE_WITHIN_QUOTE =
  "names the team from a single corner 44% of the time and the driver 28%, against 29% and 16% for the hand-crafted features (chance 9% and 4.5%)";
const CLEAR_QUOTE = "In 95% of pair-seasons at least one of the seven metrics differs clearly, against 29% by chance";
const SIZES_QUOTE = "median sizes of 3.1 m of braking point, 0.8 km/h of minimum speed and 0.020 s per corner";
const CENTROID_QUOTE = "Teammates' centroids have a median cosine of 0.92, drivers of different teams -0.10";
const ONNX_QUOTE = "largest relative difference 1.1e-05 against a gate of 1e-04";
const TRAFFIC_QUOTE = "(67%) to 34 of 372 (9%)";

export const claims = {
  // The data (m3_summary.md's opening paragraph, before its first section, and the dataset card).
  sessions: { value: "263", source: "m3_summary.md", quote: "All 263 qualifying, sprint and race sessions of 2022-2026" },
  segments: { value: "1.86 million", source: "m3_summary.md", quote: "1.86 million eligible corner segments" },
  labels: { value: "1,609", source: "m3_summary.md", quote: "1,609 labelled mistakes" },
  split: { value: "test 2026 (15)", source: "m3_summary.md", quote: "validate 2025 (24), test 2026 (15)" },
  splitFull: {
    value: "train 2022-2024 (68 events), validate 2025 (24), test 2026 (15)",
    source: "m3_summary.md",
    quote: "train 2022-2024 (68 events), validate 2025 (24), test 2026 (15)",
  },
  variants: { value: "11 variants", source: "m3_summary.md", quote: "11 variants of the masked autoencoder" },
  sampling: {
    value: "4 samples per second",
    source: "dataset_card.md",
    anchor: "known-limitations",
    quote: "About 4 samples per second, i.e. ~20 m apart at 300 km/h",
  },
  grid: { value: "5 m", source: "dataset_card.md", anchor: "how-its-built", quote: "Each lap is resampled every **5 m**" },
  window: {
    value: "250 m before to 150 m after",
    source: "dataset_card.md",
    anchor: "how-its-built",
    quote: "a window from **250 m before to 150 m after the apex** (80 grid points)",
  },

  // Can it find mistakes? (RQ2)
  aurocAll: {
    value: "0.75 against 0.67",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "Isolation Forest has the best AUROC (0.75 against 0.67",
  },
  top50: {
    value: "7 of its 50 highest-scoring corners, against 1",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "7 of its 50 highest-scoring corners, against 1 for Isolation Forest",
  },
  apAll: {
    value: "0.012 against 0.007",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "(0.012 against 0.007; the intervals overlap)",
  },
  apTie: {
    value: "0.022",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "On the 2026 test events alone the two tie on average precision (0.022)",
  },
  nll: { value: "0.66", source: "m3_summary.md", anchor: "findings", quote: "is weak (AUROC 0.66)" },
  combined: {
    value: "0.033 [0.007, 0.096] against 0.017",
    source: "m4_summary.md",
    anchor: "findings",
    quote: "0.033 [0.007, 0.096] against 0.017",
  },
  apBaseRate: {
    value: "about 0.001",
    source: "m4_summary.md",
    anchor: "findings",
    quote: "with a base rate of about 0.001",
  },
  flagShare: {
    value: "top 1%",
    source: "m4_summary.md",
    quote: "a corner is **flagged** when the combined score is in its session's top 1%",
  },
  flags: { value: "18,762", source: "m4_scoring.md", quote: "18,762 flags over 263 sessions (median 26 per session)" },
  notCostly: {
    value: "56%",
    source: "m4_summary.md",
    anchor: "findings",
    quote: "56% were not clearly slower than the driver's usual",
  },
  distinct: { value: "1,648", source: "m4_summary.md", anchor: "findings", quote: "1,648 distinct ones" },
  cost: {
    value: "0.37 s",
    source: "m4_summary.md",
    anchor: "findings",
    quote: "0.37 s at the median (90th percentile 1.05 s)",
  },
  traffic: { value: "67%", source: "m4_summary.md", anchor: "findings", quote: TRAFFIC_QUOTE },
  trafficAfter: { value: "9%", source: "m4_summary.md", anchor: "findings", quote: TRAFFIC_QUOTE },
  devCheck: { value: "development check", source: "m4_summary.md", anchor: "findings", quote: "development check, not" },
  cuts: { value: "5 of 43", source: "m4_summary.md", anchor: "findings", quote: "Only 5 of 43 single-car" },
  ciLevel: {
    value: "95%",
    source: "m3_results.md",
    anchor: "all-events",
    quote: "| detector | AUROC [95% CI] | avg precision [95% CI] |",
  },

  // The five detectors, AUROC and average precision with 95% intervals (m3_results.md), for
  // DetectorComparison. Each quote is the start of a table row.
  detAllPlanned: {
    value: "0.659 [0.624, 0.696]",
    source: "m3_results.md",
    anchor: "all-events",
    quote: "| mae_nll (planned) | 0.659 [0.624, 0.696] | 0.002 [0.002, 0.003] |",
  },
  detAllExit: {
    value: "0.670 [0.629, 0.712]",
    source: "m3_results.md",
    anchor: "all-events",
    quote: "| mae_error_exit (chosen on validation) | 0.670 [0.629, 0.712] | 0.012 [0.007, 0.017] |",
  },
  detAllTimeLoss: {
    value: "0.637 [0.610, 0.667]",
    source: "m3_results.md",
    anchor: "all-events",
    quote: "| time_loss | 0.637 [0.610, 0.667] | 0.003 [0.002, 0.004] |",
  },
  detAllZScore: {
    value: "0.748 [0.713, 0.782]",
    source: "m3_results.md",
    anchor: "all-events",
    quote: "| z_score | 0.748 [0.713, 0.782] | 0.006 [0.004, 0.009] |",
  },
  detAllIForest: {
    value: "0.751 [0.714, 0.785]",
    source: "m3_results.md",
    anchor: "all-events",
    quote: "| isolation_forest | 0.751 [0.714, 0.785] | 0.007 [0.005, 0.012] |",
  },
  detTestPlanned: {
    value: "0.678 [0.563, 0.761]",
    source: "m3_results.md",
    anchor: "test-events",
    quote: "| mae_nll (planned) | 0.678 [0.563, 0.761] | 0.002 [0.001, 0.005] |",
  },
  detTestExit: {
    value: "0.711 [0.605, 0.793]",
    source: "m3_results.md",
    anchor: "test-events",
    quote: "| mae_error_exit (chosen on validation) | 0.711 [0.605, 0.793] | 0.022 [0.005, 0.063] |",
  },
  detTestTimeLoss: {
    value: "0.671 [0.627, 0.707]",
    source: "m3_results.md",
    anchor: "test-events",
    quote: "| time_loss | 0.671 [0.627, 0.707] | 0.007 [0.002, 0.019] |",
  },
  detTestZScore: {
    value: "0.814 [0.719, 0.888]",
    source: "m3_results.md",
    anchor: "test-events",
    quote: "| z_score | 0.814 [0.719, 0.888] | 0.017 [0.003, 0.080] |",
  },
  detTestIForest: {
    value: "0.820 [0.722, 0.893]",
    source: "m3_results.md",
    anchor: "test-events",
    quote: "| isolation_forest | 0.820 [0.722, 0.893] | 0.022 [0.003, 0.090] |",
  },

  // Can it tell drivers apart? (RQ1)
  probeWithin: { value: "44%", source: "m3_summary.md", anchor: "findings", quote: PROBE_WITHIN_QUOTE },
  probeWithinHand: { value: "29%", source: "m3_summary.md", anchor: "findings", quote: PROBE_WITHIN_QUOTE },
  probeWithinDriver: { value: "28%", source: "m3_summary.md", anchor: "findings", quote: PROBE_WITHIN_QUOTE },
  probeAcross: {
    value: "8.8% against 11%",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "the order flips: 8.8% against 11% for the driver",
  },
  teammatesApart: {
    value: "62%",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "are told apart 62% of the time (hand-crafted: 59%)",
  },
  // The probe shares as the tables print them (a share of corners), for ProbeFlip.
  probeTeamRow: {
    value: "0.443",
    source: "m3_style_within_season.md",
    quote: "| team | 11 | 0.091 | 0.443 | 0.287 |",
  },
  probeDriverRow: {
    value: "0.280",
    source: "m3_style_within_season.md",
    quote: "| driver | 22 | 0.045 | 0.280 | 0.160 |",
  },
  probeAcrossHandRow: {
    value: "0.111",
    source: "m3_results.md",
    anchor: "driver-style-probes",
    quote: "| hand-crafted features | 0.111 | 0.112 | 0.398 |",
  },
  probeAcrossEmbRow: {
    value: "0.088",
    source: "m3_results.md",
    anchor: "driver-style-probes",
    quote: "| Transformer embedding | 0.088 | 0.082 | 0.328 |",
  },
  probeAcrossChance: {
    value: "0.053",
    source: "m3_results.md",
    anchor: "driver-style-probes",
    quote: "Chance: 0.053 for 19 drivers",
  },
  carCos: { value: "0.92", source: "m4_style.md", anchor: "style-embedding", quote: CENTROID_QUOTE },
  carCosOther: { value: "-0.10", source: "m4_style.md", anchor: "style-embedding", quote: CENTROID_QUOTE },
  clearAny: { value: "95%", source: "m4_style.md", anchor: "what-the-profiles-say", quote: CLEAR_QUOTE },
  clearChance: { value: "29%", source: "m4_style.md", anchor: "what-the-profiles-say", quote: CLEAR_QUOTE },
  sizes: { value: "3.1 m", source: "m4_style.md", anchor: "what-the-profiles-say", quote: SIZES_QUOTE },
  sizesSpeed: { value: "0.8 km/h", source: "m4_style.md", anchor: "what-the-profiles-say", quote: SIZES_QUOTE },
  sizesTime: { value: "0.020 s", source: "m4_style.md", anchor: "what-the-profiles-say", quote: SIZES_QUOTE },
  persist: { value: "0.86", source: "m4_style.md", anchor: "what-the-profiles-say", quote: "r = 0.86 for the braking point" },
  consistentShare: {
    value: "50%",
    source: "m4_summary.md",
    anchor: "findings",
    quote: "holds its direction beyond noise for 50% of driver-seasons",
  },
  // The two style teasers with evidence (plan 9.3). `hamBrakesEarlier` is the table cell, A minus
  // B in metres (negative: Hamilton brakes earlier); the landing's mini bar parses it.
  hamBrakesEarlier: {
    value: "-6.1 [-9.2, -3.1]",
    source: "m4_style.md",
    anchor: "2026-teammates",
    quote: "| HAM - LEC | Ferrari | 15 | **-6.1 [-9.2, -3.1]** |",
  },
  hamBrakesEarlierText: {
    value: "6.1 m [3.1, 9.2], at 13 of 15 events",
    source: "m4_style.md",
    anchor: "2026-teammates",
    quote: "HAM brakes earlier than LEC: by 6.1 m [3.1, 9.2], at 13 of 15 events",
  },
  albCoastsMore: {
    value: "6.3 m [3.5, 9.1], at 14 of 15 events",
    source: "m4_style.md",
    anchor: "2026-teammates",
    quote: "ALB coasts more than SAI: by 6.3 m [3.5, 9.1], at 14 of 15 events",
  },
  // The interval the style tables print (/styles, "How to read it").
  styleCiLevel: {
    value: "95%",
    source: "m4_style.md",
    anchor: "2026-teammates",
    quote: "All sessions and corners, A minus B [95% interval]",
  },
  // The /styles example's mini bar (content/style-examples.ts): the coasting cell of the same
  // table row, A minus B in metres (positive: Albon coasts more). The row's eighth column.
  albCoastsMoreCell: {
    value: "+6.3 [+3.5, +9.1]",
    source: "m4_style.md",
    anchor: "2026-teammates",
    quote:
      "| ALB - SAI | Williams | 15 | -3.4 [-7.7, +0.8] | -0.68 [-2.16, +0.81] | +0.3 [-0.6, +1.1] | **+5.6 [+3.4, +7.7]** | **+6.3 [+3.5, +9.1]** |",
  },

  // What did the 2026 rules change? (RQ3)
  shiftAll: {
    value: "about 10%",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "Reconstruction error rises about 10% from 2025 to 2026",
  },
  shiftSame: { value: "about 4%", source: "m3_summary.md", anchor: "findings", quote: "only about 4%" },
  ftGain: { value: "0 to 1.2%", source: "m3_summary.md", anchor: "findings", quote: "by 0 to 1.2%" },
  ftControl: { value: "0 to +0.3%", source: "m3_summary.md", anchor: "findings", quote: "changes it by 0 to +0.3%" },
  ftNoise: {
    value: "0.7%",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "Repeat runs of the same setting moved by up to 0.7%",
  },
  ftVerdict: {
    value: "small and not firmly established",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "small and not firmly established",
  },
  shiftNoise: {
    value: "about 0.7%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "repeating the study moved single rows by up to about 0.7%",
  },
  // The fine-tuning study's rows (m3_shift.md), for ShiftCurve: races fine-tuned on, and the
  // change in reconstruction error against no fine-tuning.
  shiftNone: {
    value: "+0.0%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| nothing | 0 | 0 | - | 0.226 | +0.0% |",
  },
  shift2026x1: {
    value: "+0.0%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| 2026 | 1 | 12,270 | 5 | 0.227 | +0.0% |",
  },
  shift2026x2: {
    value: "-0.4%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| 2026 | 2 | 20,008 | 5 | 0.226 | -0.4% |",
  },
  shift2026x4: {
    value: "-1.2%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| 2026 | 4 | 58,577 | 5 | 0.224 | -1.2% |",
  },
  shift2026x8: {
    value: "-0.5%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| 2026 | 8 | 109,785 | 1 | 0.225 | -0.5% |",
  },
  shift2025x1: {
    value: "+0.3%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| 2025 (control) | 1 | 8,747 | 2 | 0.227 | +0.3% |",
  },
  shift2025x2: {
    value: "+0.0%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| 2025 (control) | 2 | 32,036 | 2 | 0.226 | +0.0% |",
  },
  shift2025x4: {
    value: "+0.1%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| 2025 (control) | 4 | 67,203 | 2 | 0.227 | +0.1% |",
  },
  shift2025x8: {
    value: "+0.3%",
    source: "m3_shift.md",
    anchor: "results-on-the-test-races",
    quote: "| 2025 (control) | 8 | 157,629 | 2 | 0.227 | +0.3% |",
  },

  // What else was tried.
  contextTokens: {
    value: "3%",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "makes reconstruction only 3% worse and leaves mistake detection unchanged",
  },
  cnnAp: {
    value: "0.007 against 0.022",
    source: "m3_summary.md",
    anchor: "findings",
    quote: "average precision 0.007 against 0.022 on 2026",
  },
  unseen: { value: "0.008", source: "m3_summary.md", anchor: "findings", quote: "drops to an average precision of 0.008" },
  cnnSlow: { value: "12 times", source: "m3_summary.md", anchor: "findings", quote: "trained 12 times slower" },
  onnxParity: { value: "1.1e-05", source: "m4_summary.md", anchor: "findings", quote: ONNX_QUOTE },
  onnxGate: { value: "1e-04", source: "m4_summary.md", anchor: "findings", quote: ONNX_QUOTE },

  // Where it falls short.
  lateral: {
    value: "0.5 m",
    source: "m3_summary.md",
    anchor: "limits",
    quote: "The position feed is smoothed (typical lateral offsets of 0.5 m)",
  },
  repeatNoise: {
    value: "about 0.7%",
    source: "m3_summary.md",
    anchor: "limits",
    quote: "noise (up to about 0.7% in reconstruction error), are not findings",
  },
  timeWindow: {
    value: "145 m",
    source: "m4_summary.md",
    anchor: "limits",
    quote: "Time lost stops 145 m after the apex, so exit mistakes are undercounted",
  },
  reviewSize: {
    value: "50",
    source: "m4_summary.md",
    anchor: "limits",
    quote: "The human check is one reviewer and 50 findings",
  },
} satisfies Record<string, Claim>;

export type ClaimId = keyof typeof claims;

/** A claim by id, typed as a plain `Claim` (the table's entries differ in which keys they have). */
export function getClaim(id: ClaimId): Claim {
  return claims[id];
}

export function collapseWhitespace(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/**
 * Null when the claim holds against its source text; otherwise what is wrong, in a sentence. The
 * value must stand on its own in the quote (see `hasToken`): "7" doesn't count as inside "17".
 */
export function checkClaim(c: Claim, text: string): string | null {
  const quote = collapseWhitespace(c.quote);
  const value = collapseWhitespace(c.value);
  if (!value) return "the value is empty";
  if (!hasToken(quote, value)) return `the quote doesn't contain the value "${value}": "${quote}"`;
  if (!collapseWhitespace(text).includes(quote)) return `the quote isn't in ${c.source}: "${quote}"`;
  return null;
}

const DIGIT = /[0-9]/;

/**
 * Whether `token` (a formatted number such as "44%", "0.751" or "7") stands on its own in
 * `text`: not part of a longer number ("7" isn't in "17", "0.7", "7%" or "1,700"), and not
 * signed when the token isn't ("0.10" isn't in "-0.10").
 */
export function hasToken(text: string, token: string): boolean {
  if (!token) return false;
  const endsInDigit = DIGIT.test(token[token.length - 1]);
  for (let i = text.indexOf(token); i !== -1; i = text.indexOf(token, i + 1)) {
    const before = text[i - 1] ?? "";
    const after = text[i + token.length] ?? "";
    const afterNext = text[i + token.length + 1] ?? "";
    if (DIGIT.test(before) || ".-+−".includes(before || "x")) continue;
    if (before === "," && DIGIT.test(text[i - 2] ?? "")) continue;
    if (endsInDigit && (DIGIT.test(after) || after === "%")) continue;
    if (endsInDigit && (after === "." || after === ",") && DIGIT.test(afterNext)) continue;
    return true;
  }
  return false;
}

/**
 * One row of a findings figure: an estimate with its interval, and where the figure has one, the
 * position the row is drawn at on the x axis (`x`, such as the number of races fine-tuned on).
 * `n` and `k` are counts behind a proportion (`k` of `n`). Every number the figure prints must be
 * in the row's claim (see `checkEstimate`).
 */
export interface Estimate {
  label: string;
  est: number;
  lo?: number;
  hi?: number;
  n?: number;
  k?: number;
  x?: number;
  claim: ClaimId;
}

/**
 * Null when every number of the row appears in its claim's quote as printed by `format` (counts
 * and `x` as plain integers); otherwise what is missing.
 *
 * Each number must sit where the reports put it, not merely somewhere in the quote, so a swapped
 * interval or an estimate typed as its own lower bound fails:
 * - with both bounds, the quote holds "est [lo, hi]" as written ("0.751 [0.714, 0.785]");
 * - with both counts, it holds "k / n", "k of n", or the two parts side by side, "| k | n - k |".
 */
export function checkEstimate(e: Estimate, format: (v: number) => string): string | null {
  const quote = collapseWhitespace(getClaim(e.claim).quote);
  const missing = (what: string, text: string) => `${e.label}: ${what} "${text}" isn't in claim ${e.claim}: "${quote}"`;
  if (e.lo !== undefined && e.hi !== undefined) {
    const interval = `${format(e.est)} [${format(e.lo)}, ${format(e.hi)}]`;
    if (!hasToken(quote, interval)) return missing("interval", interval);
  } else {
    for (const [what, v] of [["est", e.est], ["lo", e.lo], ["hi", e.hi]] as const) {
      if (v !== undefined && !hasToken(quote, format(v))) return missing(what, format(v));
    }
  }
  if (e.k !== undefined && e.n !== undefined) {
    const forms = [`${e.k} / ${e.n}`, `${e.k} of ${e.n}`, `| ${e.k} | ${e.n - e.k} |`];
    if (!forms.some((form) => hasToken(quote, form))) return missing("k of n", `${e.k} of ${e.n}`);
  } else {
    if (e.k !== undefined && !hasToken(quote, String(e.k))) return missing("k", String(e.k));
    if (e.n !== undefined && !hasToken(quote, String(e.n))) return missing("n", String(e.n));
  }
  if (e.x !== undefined && !hasToken(quote, `| ${e.x} |`)) return missing("x", `| ${e.x} |`);
  return null;
}

/** The page a report file is published at: "m4_summary.md" is /report/m4-summary. */
export function reportSlug(file: ReportFile): string {
  return file.replace(/\.md$/, "").replace(/_/g, "-");
}

/** Where a claim's "Source" link goes: its report page, at the section that holds the quote. */
export function claimHref(id: ClaimId): string {
  const c = getClaim(id);
  return `/report/${reportSlug(c.source)}${c.anchor ? `#${c.anchor}` : ""}`;
}
