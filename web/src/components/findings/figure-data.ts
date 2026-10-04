/**
 * The rows the findings figures draw (plan 7.5), each tied to a claim whose quote holds every
 * number the row prints. `claims.test.ts` checks them with `checkEstimate`, using each figure's
 * own `format`. Values are plain numbers here and get their unit only when printed, so no
 * percentage is ever typed into this folder by hand.
 *
 * Pure data and formatting (no React), so the claims test can load it under Node.
 */
import type { ClaimId, Estimate } from "../../content/claims.ts";

/** AUROC, average precision and probe shares, to three decimals as the reports' tables print them. */
export const threeDecimals = (v: number): string => v.toFixed(3);

/** A signed change in percent with one decimal and its sign, as m3_shift.md's "vs none" column prints it. */
export const signedPercent = (v: number): string => `${v < 0 ? "-" : "+"}${Math.abs(v).toFixed(1)}%`;

// ---------------------------------------------------------------------------------------------
// DetectorComparison: the five detectors of m3_results.md, AUROC and average precision with
// their intervals, on all events or on the 2026 test events.

export type Detector = "exit" | "iForest" | "zScore" | "timeLoss" | "planned";

/** How a detector is drawn: the Transformer's chosen score in series A, the best baseline in B. */
export type DetectorRole = "transformer" | "baseline" | "other";

export interface DetectorInfo {
  id: Detector;
  label: string; // in the figure
  name: string; // as m3_results.md names it, for the table
  role: DetectorRole;
}

/** In the order the figure lists them: the two that matter first. */
export const DETECTORS: readonly DetectorInfo[] = [
  { id: "exit", label: "Transformer, error after the apex", name: "mae_error_exit", role: "transformer" },
  { id: "iForest", label: "Isolation Forest", name: "isolation_forest", role: "baseline" },
  { id: "zScore", label: "z-score", name: "z_score", role: "other" },
  { id: "timeLoss", label: "Time lost", name: "time_loss", role: "other" },
  { id: "planned", label: "Transformer, planned score", name: "mae_nll", role: "other" },
];

export type EventSet = "all" | "test";
export type Metric = "auroc" | "ap";

// [AUROC, lo, hi, average precision, lo, hi] per detector, from m3_results.md.
const DETECTOR_ROWS: Record<EventSet, Record<Detector, readonly number[]>> = {
  all: {
    exit: [0.67, 0.629, 0.712, 0.012, 0.007, 0.017],
    iForest: [0.751, 0.714, 0.785, 0.007, 0.005, 0.012],
    zScore: [0.748, 0.713, 0.782, 0.006, 0.004, 0.009],
    timeLoss: [0.637, 0.61, 0.667, 0.003, 0.002, 0.004],
    planned: [0.659, 0.624, 0.696, 0.002, 0.002, 0.003],
  },
  test: {
    exit: [0.711, 0.605, 0.793, 0.022, 0.005, 0.063],
    iForest: [0.82, 0.722, 0.893, 0.022, 0.003, 0.09],
    zScore: [0.814, 0.719, 0.888, 0.017, 0.003, 0.08],
    timeLoss: [0.671, 0.627, 0.707, 0.007, 0.002, 0.019],
    planned: [0.678, 0.563, 0.761, 0.002, 0.001, 0.005],
  },
};

const DETECTOR_CLAIMS: Record<EventSet, Record<Detector, ClaimId>> = {
  all: { exit: "detAllExit", iForest: "detAllIForest", zScore: "detAllZScore", timeLoss: "detAllTimeLoss", planned: "detAllPlanned" },
  test: { exit: "detTestExit", iForest: "detTestIForest", zScore: "detTestZScore", timeLoss: "detTestTimeLoss", planned: "detTestPlanned" },
};

function detectorRows(events: EventSet, metric: Metric): Estimate[] {
  const offset = metric === "auroc" ? 0 : 3;
  return DETECTORS.map((d) => {
    const row = DETECTOR_ROWS[events][d.id];
    return { label: d.label, est: row[offset], lo: row[offset + 1], hi: row[offset + 2], claim: DETECTOR_CLAIMS[events][d.id] };
  });
}

/** Rows in the order of `DETECTORS`, per event set and metric. */
export const DETECTOR_DATA: Record<EventSet, Record<Metric, readonly Estimate[]>> = {
  all: { auroc: detectorRows("all", "auroc"), ap: detectorRows("all", "ap") },
  test: { auroc: detectorRows("test", "auroc"), ap: detectorRows("test", "ap") },
};

// ---------------------------------------------------------------------------------------------
// ProbeFlip: how often a linear probe names the team or the driver from one corner, embedding
// against hand-crafted features, within 2026 and across seasons, with chance. Shares of corners
// as the tables print them.

export type ProbeFeature = "embedding" | "handCrafted" | "chance";

export interface ProbeGroup {
  id: "withinTeam" | "withinDriver" | "acrossDriver";
  label: string;
  detail: string; // what was trained and tested on
  rows: Record<ProbeFeature, Estimate>;
}

export const PROBE_GROUPS: readonly ProbeGroup[] = [
  {
    id: "withinTeam",
    label: "Team, within 2026",
    detail: "trained on the early 2026 races, tested on the later ones",
    rows: {
      embedding: { label: "Team, within 2026: embedding", est: 0.443, claim: "probeTeamRow" },
      handCrafted: { label: "Team, within 2026: hand-crafted", est: 0.287, claim: "probeTeamRow" },
      chance: { label: "Team, within 2026: chance", est: 0.091, claim: "probeTeamRow" },
    },
  },
  {
    id: "withinDriver",
    label: "Driver, within 2026",
    detail: "the same races",
    rows: {
      embedding: { label: "Driver, within 2026: embedding", est: 0.28, claim: "probeDriverRow" },
      handCrafted: { label: "Driver, within 2026: hand-crafted", est: 0.16, claim: "probeDriverRow" },
      chance: { label: "Driver, within 2026: chance", est: 0.045, claim: "probeDriverRow" },
    },
  },
  {
    id: "acrossDriver",
    label: "Driver, across seasons",
    detail: "trained on 2022-2024, tested on 2026",
    rows: {
      embedding: { label: "Driver, across seasons: embedding", est: 0.088, claim: "probeAcrossEmbRow" },
      handCrafted: { label: "Driver, across seasons: hand-crafted", est: 0.111, claim: "probeAcrossHandRow" },
      chance: { label: "Driver, across seasons: chance", est: 0.053, claim: "probeAcrossChance" },
    },
  },
];

// ---------------------------------------------------------------------------------------------
// ShiftCurve: m3_shift.md's fine-tuning study. The change in reconstruction error on the held-out
// 2026 races against no fine-tuning, by the number of races fine-tuned on (`x`), for 2026 races
// and for the control (2025 races).

export interface ShiftSeries {
  id: "2026" | "control";
  label: string;
  rows: readonly Estimate[];
}

export const SHIFT_SERIES: readonly ShiftSeries[] = [
  {
    id: "2026",
    label: "Fine-tuned on 2026 races",
    rows: [
      { label: "No fine-tuning", est: 0, x: 0, claim: "shiftNone" },
      { label: "2026, 1 race", est: 0, x: 1, claim: "shift2026x1" },
      { label: "2026, 2 races", est: -0.4, x: 2, claim: "shift2026x2" },
      { label: "2026, 4 races", est: -1.2, x: 4, claim: "shift2026x4" },
      { label: "2026, 8 races", est: -0.5, x: 8, claim: "shift2026x8" },
    ],
  },
  {
    id: "control",
    label: "Control: fine-tuned on 2025 races",
    rows: [
      { label: "No fine-tuning", est: 0, x: 0, claim: "shiftNone" },
      { label: "2025, 1 race", est: 0.3, x: 1, claim: "shift2025x1" },
      { label: "2025, 2 races", est: 0, x: 2, claim: "shift2025x2" },
      { label: "2025, 4 races", est: 0.1, x: 4, claim: "shift2025x4" },
      { label: "2025, 8 races", est: 0.3, x: 8, claim: "shift2025x8" },
    ],
  },
];

/** The repeat-run noise the figure shades, as a plus-or-minus band in percent (m3_shift.md's "moved single rows by up to about …"). */
export const SHIFT_NOISE: Estimate = { label: "Repeat-run noise", est: 0.7, claim: "shiftNoise" };
const plainPercent = (v: number): string => `${v.toFixed(1)}%`;

// ---------------------------------------------------------------------------------------------

/** Every figure's rows with the format it prints them in, for the claims test. */
export const FIGURES: readonly { name: string; format: (v: number) => string; rows: readonly Estimate[] }[] = [
  { name: "DetectorComparison, all events, AUROC", format: threeDecimals, rows: DETECTOR_DATA.all.auroc },
  { name: "DetectorComparison, all events, average precision", format: threeDecimals, rows: DETECTOR_DATA.all.ap },
  { name: "DetectorComparison, test events, AUROC", format: threeDecimals, rows: DETECTOR_DATA.test.auroc },
  { name: "DetectorComparison, test events, average precision", format: threeDecimals, rows: DETECTOR_DATA.test.ap },
  { name: "ProbeFlip", format: threeDecimals, rows: PROBE_GROUPS.flatMap((g) => Object.values(g.rows)) },
  { name: "ShiftCurve", format: signedPercent, rows: SHIFT_SERIES.flatMap((s) => s.rows) },
  { name: "ShiftCurve, noise band", format: plainPercent, rows: [SHIFT_NOISE] },
];
