# Telemetry Transformer: first results

_Generated 2026-09-30 10:29 UTC by `race-engineer-eval deep`._

**Checkpoint:** `baseline.pt` (epoch 15, validation NLL -0.9111). **Split:** train 2022-2024, validate 2025, test 2026 (the new regulations).

## Mistake detection

The planned score, `mae_nll`, is the model's surprise at what actually happened (negative log-likelihood, weighted by its own predicted uncertainty). It turned out to be a weak detector: the model learns that corner exits are naturally variable, so it discounts exactly where most mistakes happen. Plain reconstruction error works better. Several variants were tried; `mae_error_exit` was **chosen on the validation events only**, and is reported here on test and all events next to the planned score and the best baselines.

### All events

| detector | AUROC [95% CI] | avg precision [95% CI] | P@50 | P@200 | recall@1% | recall@5% |
|---|---|---|---|---|---|---|
| mae_nll (planned) | 0.659 [0.624, 0.696] | 0.002 [0.002, 0.003] | 0 | 0.005 | 0.043 | 0.198 |
| mae_error_exit (chosen on validation) | 0.670 [0.629, 0.712] | 0.012 [0.007, 0.017] | 0.14 | 0.08 | 0.182 | 0.276 |
| time_loss | 0.637 [0.610, 0.667] | 0.003 [0.002, 0.004] | 0.02 | 0.02 | 0.084 | 0.236 |
| z_score | 0.748 [0.713, 0.782] | 0.006 [0.004, 0.009] | 0.04 | 0.025 | 0.107 | 0.306 |
| isolation_forest | 0.751 [0.714, 0.785] | 0.007 [0.005, 0.012] | 0.02 | 0.05 | 0.157 | 0.342 |

### Test events

| detector | AUROC [95% CI] | avg precision [95% CI] | P@50 | P@200 | recall@1% | recall@5% |
|---|---|---|---|---|---|---|
| mae_nll (planned) | 0.678 [0.563, 0.761] | 0.002 [0.001, 0.005] | 0 | 0.005 | 0.039 | 0.162 |
| mae_error_exit (chosen on validation) | 0.711 [0.605, 0.793] | 0.022 [0.005, 0.063] | 0.18 | 0.085 | 0.229 | 0.363 |
| time_loss | 0.671 [0.627, 0.707] | 0.007 [0.002, 0.019] | 0.04 | 0.02 | 0.145 | 0.285 |
| z_score | 0.814 [0.719, 0.888] | 0.017 [0.003, 0.080] | 0.1 | 0.08 | 0.184 | 0.408 |
| isolation_forest | 0.820 [0.722, 0.893] | 0.022 [0.003, 0.090] | 0.1 | 0.085 | 0.263 | 0.441 |

## Driver style probes

Chance: 0.053 for 19 drivers, 0.5 for teammates. The embedding comes from a model that never saw driver or team labels.

| features | driver top-1 (logreg) | driver top-1 (gbm) | driver top-5 (gbm) | driver, lap votes (gbm) | teammate (segment) | teammate (lap) |
|---|---|---|---|---|---|---|
| hand-crafted features | 0.111 | 0.112 | 0.398 | 0.18 | 0.594 | 0.684 |
| Transformer embedding | 0.088 | 0.082 | 0.328 | 0.109 | 0.616 | 0.68 |

## All score variants (all events)

`mae_error`: mean squared reconstruction error. `mae_error_exit`: speed and line offset from the apex to 150 m after it. `mae_error_speed_patch`: the worst 40 m of speed. `_vs_driver`: relative to the driver's own laps at that corner.

| detector | AUROC [95% CI] | avg precision [95% CI] | P@50 | P@200 | recall@1% | recall@5% |
|---|---|---|---|---|---|---|
| mae_nll (planned) | 0.659 [0.624, 0.696] | 0.002 [0.002, 0.003] | 0 | 0.005 | 0.043 | 0.198 |
| mae_error | 0.710 [0.675, 0.747] | 0.006 [0.004, 0.008] | 0.06 | 0.015 | 0.155 | 0.345 |
| mae_error_exit (chosen on validation) | 0.670 [0.629, 0.712] | 0.012 [0.007, 0.017] | 0.14 | 0.08 | 0.182 | 0.276 |
| mae_error_speed_patch | 0.648 [0.602, 0.701] | 0.006 [0.004, 0.009] | 0.06 | 0.05 | 0.155 | 0.26 |
| mae_nll_vs_driver | 0.726 [0.695, 0.760] | 0.004 [0.003, 0.004] | 0 | 0.005 | 0.069 | 0.319 |
| mae_error_vs_driver | 0.735 [0.706, 0.769] | 0.005 [0.004, 0.006] | 0 | 0.02 | 0.129 | 0.362 |
| mae_error_exit_vs_driver | 0.685 [0.655, 0.716] | 0.006 [0.005, 0.008] | 0 | 0.025 | 0.198 | 0.314 |
| mae_error_speed_patch_vs_driver | 0.678 [0.646, 0.717] | 0.004 [0.003, 0.005] | 0 | 0.025 | 0.148 | 0.284 |
| time_loss | 0.637 [0.610, 0.667] | 0.003 [0.002, 0.004] | 0.02 | 0.02 | 0.084 | 0.236 |
| z_score | 0.748 [0.713, 0.782] | 0.006 [0.004, 0.009] | 0.04 | 0.025 | 0.107 | 0.306 |
| isolation_forest | 0.751 [0.714, 0.785] | 0.007 [0.005, 0.012] | 0.02 | 0.05 | 0.157 | 0.342 |
| pca_trace | 0.728 [0.698, 0.759] | 0.005 [0.004, 0.006] | 0.02 | 0.005 | 0.126 | 0.311 |
