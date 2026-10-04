/**
 * The 7 figures in `report/figures`, served at `/report/figures/<file>`: a short title for a
 * caption, alt text that says what the figure shows, the published report that discusses it (if
 * any) and the script that draws it. `/report`'s figures grid reads this; a test checks it lists
 * exactly the files in the folder. The PNGs are matplotlib's, on white, so they always sit on a
 * white paper card.
 */

export interface ReportFigureInfo {
  file: string; // in report/figures
  title: string;
  alt: string;
  /** The published report that discusses it, by slug. */
  report?: string;
  /** Where it is drawn, from the repository root. */
  madeBy: string;
}

export const FIGURES: readonly ReportFigureInfo[] = [
  {
    file: "mae_reconstruction.png",
    title: "The Transformer filling in a hidden stretch of a corner",
    alt: "Speed, throttle and line offset through turn 5 for Zhou in 2022 Bahrain Grand Prix qualifying, as z-scores against the field. The model sees the lap with 120 m around the apex hidden and predicts that stretch with a band of two standard deviations. Left, a normal lap (lap 2): what happened stays mostly inside the band. Right, lap 11, labelled for track limits: the speed dips and recovers in a way the prediction doesn't follow.",
    madeBy: "engine/scripts/plot_reconstruction.py",
  },
  {
    file: "style_map.png",
    title: "The style embedding, by team and by event",
    alt: "Two t-SNE maps of the style embedding, one point per driver per event, 328 points from the test events of the temporal split. Coloured by team (left, labels at each driver's centre), the points group by team. Coloured by event (right), the same points mix.",
    report: "m3-summary",
    madeBy: "engine/scripts/plot_style_map.py",
  },
  {
    file: "shift_finetune.png",
    title: "How fast the model adapts to the 2026 cars",
    alt: "Reconstruction error on held-out 2026 races against the number of races fine-tuned on, 0 to 8 in calendar order. Fine-tuning on 2026 races (blue circles) and on 2025 control races (orange squares) both stay flat at about 0.23. A dotted line at about 0.20 marks the error with no fine-tuning on later 2025 events at other tracks.",
    report: "m3-shift",
    madeBy: "engine/src/race_engineer/models/finetune.py",
  },
  {
    file: "labelled_mistake.png",
    title: "A lap deleted for track limits, against clean laps",
    alt: "Speed, offset from the racing line and throttle through turn 7 for Colapinto in 2026 Australian Grand Prix qualifying: lap 2, deleted for track limits, in red against four other clean laps in grey. On lap 2 the throttle falls back to about 15% before the apex, and from the apex on the speed stays about 70 km/h below the clean laps.",
    madeBy: "engine/notebooks/01_explore_telemetry.ipynb",
  },
  {
    file: "track_map_speed.png",
    title: "A qualifying lap coloured by speed",
    alt: "The circuit of the 2026 Australian Grand Prix, coloured by Russell's speed on his fastest qualifying lap, from about 100 km/h (dark purple) to over 300 km/h (yellow), with the 14 turns numbered.",
    madeBy: "engine/notebooks/01_explore_telemetry.ipynb",
  },
  {
    file: "teammates_lap_delta.png",
    title: "Two teammates along one lap",
    alt: "Russell (blue) and Antonelli (orange) on their fastest laps of 2026 Australian Grand Prix qualifying: speed against distance from the timing line with turns 1 to 14 marked, and below it the time gap, with Antonelli up to about 0.5 s behind around turns 11 and 12.",
    madeBy: "engine/notebooks/01_explore_telemetry.ipynb",
  },
  {
    file: "teammates_corner.png",
    title: "Two teammates through one corner",
    alt: "Speed, throttle and brake through turn 4 on the same two laps: Russell (blue, 8.822 s through the window) and Antonelli (orange, 8.774 s). Antonelli arrives faster and comes off the brake a little earlier, then touches it again about 30 m before the apex.",
    madeBy: "engine/notebooks/01_explore_telemetry.ipynb",
  },
];

export function figureInfo(file: string): ReportFigureInfo | undefined {
  return FIGURES.find((f) => f.file === file);
}
