# Telemetry Transformer ablations (smoke)

_Generated 2026-09-30 04:52 UTC by `race-engineer-experiments` (grid `smoke`, run prefix `smoke-cloud-20260930-045047`); 3 of 3 variants finished so far._

**Split:** whole events assigned at random (60/20/20). Provisional: used until older seasons are processed and the temporal split is possible. Train / validation / test: 28 / 9 / 10 events, 488,362 / 137,794 / 194,823 segments. Evaluated on up to 2 whole events per split (a smoke setting): expect noisy numbers.

## Variants

| variant | model | objective | context | channels | d_model | layers | mask | params | epochs (best) | train segments |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | transformer | nll | yes | all | 64 | 2 | 0.5 | 86k | 1 (1) | 5,000 |
| mse_no_context_no_gear | transformer | mse | no | no gear | 64 | 2 | 0.5 | 84k | 1 (1) | 5,000 |
| cnn | cnn | nll | yes | all | 64 | - | 0.5 | 206k | 1 (1) | 5,000 |

## Results

Mistake scores: for each variant the reported score is **chosen on the validation events only** (by average precision), and the planned score `mae_nll` is shown next to it. AP = average precision (base rate is about 0.001). Held-out recon MSE: squared error on hidden points under the five complementary masks, on validation and test events; only comparable between variants that reconstruct the same channels. Probes use logistic regression on the embedding: driver top-1 over 23 drivers (chance 0.043), a whole lap's corners voting, and teammates (chance 0.5). Under the `mse` objective (mse_no_context_no_gear) the variance head is untrained, so `mae_nll` assumes unit variance: it is half of `mae_error` and ranks the same.

| variant | held-out recon MSE | chosen score | AP test [95% CI] | AUROC test | AP all | AUROC all | mae_nll AP all | mae_nll AUROC all | driver top-1 | driver lap votes | teammate | train | eval |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.719 | `mae_nll` | 0.010 [0.008, 0.018] | 0.878 | 0.009 | 0.756 | 0.009 | 0.756 | 0.110 | 0.165 | 0.540 | 7 s | 7 s |
| mse_no_context_no_gear | 0.581 | `mae_error` | 0.011 [0.008, 0.018] | 0.887 | 0.010 | 0.777 | 0.010 | 0.777 | 0.124 | 0.207 | 0.525 | 2 s | 8 s |
| cnn | 0.666 | `mae_nll` | 0.010 [0.008, 0.015] | 0.871 | 0.009 | 0.762 | 0.009 | 0.762 | 0.091 | 0.113 | 0.554 | 3 s | 11 s |
