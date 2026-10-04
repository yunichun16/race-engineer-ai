# Telemetry Transformer ablations (ablations)

_Generated 2026-09-30 12:28 UTC by `race-engineer-experiments` (grid `ablations`, run prefix `ablations-cloud-20260930-091907`); 11 of 11 variants finished so far._

**Splits:**

- `temporal` (all variants except `unseen_tracks`): train 2022-2024, validate 2025, test 2026 (the new regulations). Train / validation / test: 68 / 24 / 15 events, 1,211,573 / 442,570 / 208,916 segments.
- `leave_tracks_out` (`unseen_tracks`): whole circuits held out (60/20/20). Train / validation / test: 68 / 20 / 19 events, 1,134,337 / 379,465 / 349,257 segments.

Evaluated on every event.

## Variants

| variant | model | objective | context | channels | d_model | layers | mask | params | epochs (best) | split | train segments |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | transformer | nll | yes | all | 128 | 4 | 0.5 | 576k | 20 (15) | temporal | 1,211,573 |
| objective_mse | transformer | mse | yes | all | 128 | 4 | 0.5 | 576k | 20 (19) | temporal | 1,211,573 |
| no_context | transformer | nll | no | all | 128 | 4 | 0.5 | 576k | 20 (13) | temporal | 1,211,573 |
| mask_0.3 | transformer | nll | yes | all | 128 | 4 | 0.3 | 576k | 20 (16) | temporal | 1,211,573 |
| mask_0.7 | transformer | nll | yes | all | 128 | 4 | 0.7 | 576k | 20 (11) | temporal | 1,211,573 |
| small_d64 | transformer | nll | yes | all | 64 | 4 | 0.5 | 153k | 20 (16) | temporal | 1,211,573 |
| large_d256 | transformer | nll | yes | all | 256 | 4 | 0.5 | 2233k | 20 (14) | temporal | 1,211,573 |
| no_offset_channel | transformer | nll | yes | no offset_m | 128 | 4 | 0.5 | 573k | 20 (6) | temporal | 1,211,573 |
| no_gear_channel | transformer | nll | yes | no gear | 128 | 4 | 0.5 | 573k | 20 (15) | temporal | 1,211,573 |
| cnn | cnn | nll | yes | all | 128 | - | 0.5 | 805k | 20 (15) | temporal | 1,211,573 |
| unseen_tracks | transformer | nll | yes | all | 128 | 4 | 0.5 | 576k | 20 (9) | leave_tracks_out | 1,134,337 |

## Results

Mistake scores: for each variant the reported score is **chosen on the validation events only** (by average precision), and the planned score `mae_nll` is shown next to it. AP = average precision (base rate is about 0.001). Held-out recon MSE: squared error on hidden points under the five complementary masks, on validation and test events; only comparable between variants that reconstruct the same channels. Probes use logistic regression on the embedding: driver top-1 over 19 drivers (chance 0.053), a whole lap's corners voting, and teammates (chance 0.5). Under the `mse` objective (objective_mse) the variance head is untrained, so `mae_nll` assumes unit variance: it is half of `mae_error` and ranks the same.

| variant | held-out recon MSE | chosen score | AP test [95% CI] | AUROC test | AP all | AUROC all | mae_nll AP all | mae_nll AUROC all | driver top-1 | driver lap votes | teammate | train | eval |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.205 | `mae_error_exit` | 0.022 [0.004, 0.063] | 0.711 | 0.012 | 0.670 | 0.002 | 0.659 | 0.087 | 0.104 | 0.616 | 13.0 min | 2.2 min |
| objective_mse | 0.153 | `mae_nll_vs_driver` | 0.005 [0.002, 0.012] | 0.747 | 0.004 | 0.722 | 0.004 | 0.688 | 0.084 | 0.108 | 0.607 | 13.3 min | 2.1 min |
| no_context | 0.212 | `mae_error_exit` | 0.022 [0.004, 0.063] | 0.712 | 0.012 | 0.672 | 0.002 | 0.652 | 0.092 | 0.134 | 0.618 | 12.8 min | 2.0 min |
| mask_0.3 | 0.198 | `mae_error_exit` | 0.023 [0.004, 0.066] | 0.711 | 0.012 | 0.670 | 0.002 | 0.658 | 0.083 | 0.098 | 0.626 | 13.0 min | 2.2 min |
| mask_0.7 | 0.223 | `mae_error_exit` | 0.026 [0.005, 0.073] | 0.732 | 0.014 | 0.688 | 0.003 | 0.678 | 0.088 | 0.115 | 0.617 | 13.4 min | 2.0 min |
| small_d64 | 0.219 | `mae_error_exit` | 0.027 [0.005, 0.075] | 0.728 | 0.014 | 0.688 | 0.002 | 0.665 | 0.083 | 0.105 | 0.617 | 12.7 min | 2.2 min |
| large_d256 | 0.198 | `mae_error_exit` | 0.020 [0.003, 0.056] | 0.701 | 0.010 | 0.668 | 0.002 | 0.656 | 0.082 | 0.099 | 0.608 | 13.8 min | 2.8 min |
| no_offset_channel | 0.260 | `mae_error_exit` | 0.025 [0.005, 0.069] | 0.714 | 0.014 | 0.686 | 0.002 | 0.668 | 0.081 | 0.104 | 0.608 | 13.6 min | 2.2 min |
| no_gear_channel | 0.184 | `mae_error_exit` | 0.024 [0.004, 0.067] | 0.715 | 0.012 | 0.675 | 0.002 | 0.683 | 0.120 | 0.149 | 0.637 | 13.2 min | 2.1 min |
| cnn | 0.213 | `mae_error_exit` | 0.007 [0.002, 0.023] | 0.731 | 0.006 | 0.691 | 0.001 | 0.568 | 0.093 | 0.117 | 0.622 | 160.3 min | 4.0 min |
| unseen_tracks | 0.203 | `mae_error_exit` | 0.008 [0.004, 0.017] | 0.729 | 0.013 | 0.684 | 0.002 | 0.664 | 0.176 | 0.326 | 0.663 | 12.6 min | 2.7 min |

## Validation vs test events

Under the temporal split validation is the 2025 season and test is 2026, the first year of the new cars: the change from one to the other is the model's drop under the rule change (RQ3). Reconstruction error on each split, and the planned score (fixed before seeing any results, so its validation numbers are not tuned).

| variant | recon MSE val | recon MSE test | change | mae_nll AUROC val | mae_nll AUROC test | mae_nll AP val | mae_nll AP test |
|---|---|---|---|---|---|---|---|
| baseline | 0.198 | 0.219 | 0.10 | 0.679 | 0.678 | 0.002 | 0.002 |
| objective_mse | 0.149 | 0.161 | 0.08 | 0.721 | 0.705 | 0.005 | 0.004 |
| no_context | 0.206 | 0.224 | 0.09 | 0.672 | 0.674 | 0.002 | 0.002 |
| mask_0.3 | 0.192 | 0.211 | 0.10 | 0.682 | 0.665 | 0.002 | 0.002 |
| mask_0.7 | 0.216 | 0.237 | 0.10 | 0.693 | 0.713 | 0.003 | 0.003 |
| small_d64 | 0.213 | 0.233 | 0.10 | 0.683 | 0.689 | 0.002 | 0.002 |
| large_d256 | 0.191 | 0.212 | 0.11 | 0.679 | 0.660 | 0.002 | 0.002 |
| no_offset_channel | 0.251 | 0.278 | 0.11 | 0.695 | 0.697 | 0.003 | 0.002 |
| no_gear_channel | 0.177 | 0.197 | 0.11 | 0.703 | 0.701 | 0.002 | 0.002 |
| cnn | 0.207 | 0.228 | 0.10 | 0.531 | 0.592 | 0.001 | 0.001 |
| unseen_tracks | 0.186 | 0.221 | 0.18 | 0.769 | 0.727 | 0.003 | 0.001 |
