# Baselines

_Generated 2026-09-30 09:29 UTC by `race-engineer-eval baselines`._

**Split:** train 2022-2024, validate 2025, test 2026 (the new regulations).

| split | events | segments | labelled_mistakes |
|---|---|---|---|
| train | 68 | 1,211,573 | 1,083 |
| val | 24 | 442,570 | 347 |
| test | 15 | 208,916 | 179 |

## Mistake detection

Ranking corner segments by how unusual they are for that driver at that corner, scored against track-limits violations named by race control (single-corner, not clustered). Mistakes are rare and many are unlabelled, so compare detectors with each other rather than against a perfect score. Intervals come from resampling whole events.

### Test events

| detector | AUROC [95% CI] | avg precision [95% CI] | P@50 | P@200 | recall@1% | recall@5% |
|---|---|---|---|---|---|---|
| time_loss | 0.671 [0.627, 0.707] | 0.007 [0.002, 0.019] | 0.04 | 0.035 | 0.145 | 0.285 |
| z_score | 0.814 [0.719, 0.888] | 0.017 [0.003, 0.080] | 0.1 | 0.08 | 0.184 | 0.408 |
| isolation_forest | 0.821 [0.727, 0.893] | 0.022 [0.003, 0.096] | 0.1 | 0.08 | 0.257 | 0.447 |
| pca_trace | 0.781 [0.689, 0.852] | 0.006 [0.002, 0.014] | 0 | 0.015 | 0.123 | 0.358 |

### All events

Detectors never see labels, so scoring every event is also fair; it gives tighter intervals. (Isolation Forest and PCA were fit on the training events only.)

| detector | AUROC [95% CI] | avg precision [95% CI] | P@50 | P@200 | recall@1% | recall@5% |
|---|---|---|---|---|---|---|
| time_loss | 0.637 [0.610, 0.667] | 0.003 [0.002, 0.004] | 0 | 0.015 | 0.084 | 0.236 |
| z_score | 0.748 [0.713, 0.782] | 0.006 [0.004, 0.009] | 0.04 | 0.025 | 0.107 | 0.306 |
| isolation_forest | 0.753 [0.716, 0.786] | 0.008 [0.005, 0.012] | 0.04 | 0.055 | 0.163 | 0.344 |
| pca_trace | 0.728 [0.698, 0.759] | 0.005 [0.004, 0.006] | 0.02 | 0.005 | 0.126 | 0.311 |

Base rate: 0.0009 (about 1 labelled mistake per 1,156 segments).

## Driver style probes

**Which driver took this corner?** 19 drivers, chance 0.053. Features are normalised per session and corner, so track and corner differences are removed.

| model | top-1 | top-5 | top-1 (whole lap votes) |
|---|---|---|---|
| logreg | 0.11 | 0.387 | 0.153 |
| gbm | 0.109 | 0.399 | 0.176 |

**Which teammate took this corner?** Same car, so only the driver differs. 7 teammate pairs, chance 0.5: segment accuracy 0.594 (sd 0.055 across pairs), whole-lap accuracy 0.684.
