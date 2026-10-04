# M3 summary: what the Telemetry Transformer learned

The deep-learning milestone, in one page. Details and every number are in the linked reports.

**Data.** All 263 qualifying, sprint and race sessions of 2022-2026: 1.86 million eligible
corner segments with 1,609 labelled mistakes (track-limits violations named by race control).
**Split:** train 2022-2024 (68 events), validate 2025 (24), test 2026 (15), the first year of
the new cars. **Models:** 11 variants of the masked autoencoder, each trained for 20 epochs on
its own cloud GPU ([m3_ablations.md](m3_ablations.md)), the main one evaluated against the
baselines ([m3_results.md](m3_results.md)).

## Findings

**1. Mistakes (RQ2): the baselines rank better overall, the Transformer is sharper at the top.**
On all events, Isolation Forest has the best AUROC (0.75 against 0.67 for the Transformer's
chosen score). But the Transformer's reconstruction error after the apex puts more labelled
mistakes at the very top of its list: 7 of its 50 highest-scoring corners, against 1 for
Isolation Forest, and a higher average precision (0.012 against 0.007; the intervals overlap).
On the 2026 test events alone the two tie on average precision (0.022). The planned score, the
model's negative log-likelihood, is weak (AUROC 0.66): the model learns that corner exits vary,
so it discounts exactly where most mistakes happen. Labels are sparse (179 in the test year),
so every interval is wide; the manual review in M4 is the real check.

**2. Style (RQ1): the embedding learns a car-and-driver signature that holds within a season.**
Within 2026 (train on the first 8 races, test on the other 7), a linear probe on the embedding
names the team from a single corner 44% of the time and the driver 28%, against 29% and 16% for
the hand-crafted features (chance 9% and 4.5%) ([m3_style_within_season.md](m3_style_within_season.md)).
Across seasons (train 2022-2024, test 2026) the order flips: 8.8% against 11% for the driver.
The map of the embedding shows why ([style_map.png](figures/style_map.png)): drivers group by
team, not by event. A model that never saw team labels picked up how each car is driven, and
that signature changes with new cars and with drivers changing teams. Teammates, who share a
car, are told apart 62% of the time (hand-crafted: 59%).

**3. The 2026 rule change (RQ3): a small shift, and fine-tuning barely moves it.**
Reconstruction error rises about 10% from 2025 to 2026 in every variant, but on the same four
circuits in both years only about 4%: most of the gap is the tracks, not the cars. Fine-tuning
the model on 1 to 8 races of 2026 lowers error on later 2026 races by 0 to 1.2%, with no steady
trend as races are added; the control, the same number of 2025 races, changes it by 0 to +0.3%
([m3_shift.md](m3_shift.md)). Repeat runs of the same setting moved by up to 0.7%, so any
benefit from 2026 races is small and not firmly established. The
per-session, per-corner normalisation of the inputs probably removes most of what changed.

**4. Ablations: little matters, the context tokens barely.**
Removing the context tokens (tyres, fuel, traffic, weather, session) makes reconstruction only
3% worse and leaves mistake detection unchanged, so the model makes little use of them. A higher masking ratio (0.7) and a 4x smaller model detect mistakes
slightly better; a 4x larger one slightly worse. Dropping the gear channel gives the best driver
identification (12% across seasons). Training on plain squared error reconstructs best
and ranks mistakes better overall (AUROC 0.72 against 0.67 on all events), but finds fewer at
the top (average precision 0.004 against 0.012). Differences between the Transformer variants are within the
confidence intervals.

**5. Attention earns its place at the top of the list.** The CNN autoencoder (a small U-Net
with the same inputs, masks and outputs) reconstructs about as well (error 0.213 against 0.205)
and ranks mistakes slightly better overall (AUROC 0.73 against 0.71 on 2026), but its
highest-scoring corners hold far fewer labelled mistakes: average precision 0.007 against 0.022
on 2026 and 0.006 against 0.012 on all events. It also trained 12 times slower on the cloud
GPU. Holding out whole circuits instead of seasons (the `unseen_tracks` variant), detection on
circuits the model never saw drops to an average precision of 0.008, and reconstruction error
rises 18% from validation to test: new tracks are a bigger shift than new cars. With seasons
mixed between training and test, the driver probe reaches 18%, in line with finding 2.

## Which model we keep

The baseline configuration (Transformer, d_model 128, 4 layers, mask ratio 0.5), with its
reconstruction error after the apex (`mae_error_exit`) as the mistake score and the [CLS]
embedding for style. No variant beats it clearly; the smaller model and the higher mask ratio
are worth another look once the manual review gives a sharper measure than 179 labels. For
mistakes, M4 will combine it with Isolation Forest: the Transformer to rank the top of the
list, where precision matters to a user, and Isolation Forest as a second opinion.

## Limits

- Labels cover only track-limits violations, a fraction of real mistakes; precision against
  them understates real precision.
- The position feed is smoothed (typical lateral offsets of 0.5 m), so running wide shows up in
  speed and throttle rather than in the line.
- One run per variant: differences smaller than the bootstrap intervals, or than repeat-run
  noise (up to about 0.7% in reconstruction error), are not findings.
