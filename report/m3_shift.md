# The 2026 rule change: how fast does the model adapt?

_Generated 2026-09-30 09:55 UTC by `race_engineer.models.finetune` from `baseline.pt`._

Research question 3, part two. The base model trained on 2022-2024 only. Each run fine-tunes it for up to 5 epochs (learning rate 0.0002) on the first N 2026 races, keeps the epoch with the lowest validation loss on the validation races, and scores the test races, which no run trains on. The control runs fine-tune on the same number of 2025 races instead: if 2026 races help more than the same amount of older data, the gain is adaptation to the new cars.

- **Fine-tuning pool (2026):** Australian, Chinese, Japanese, Miami, Canadian, Monaco, Barcelona, Austrian

- **Validation (2026):** British, Belgian

- **Test (2026, 61,395 segments):** Hungarian, Dutch, Italian, Spanish, Azerbaijan

- **Control pool (2025):** Australian, Chinese, Japanese, Bahrain, Saudi Arabian, Miami, Emilia Romagna, Monaco

Inputs are normalised per session and corner, so level shifts (every car faster or slower through a corner) are removed before the model sees them; what remains is the shape of the traces, such as lifting and coasting to save energy.

## Results on the test races

For reference, the base model's reconstruction error on the later 2025 events (old cars, not trained on) is 0.197. Tracks differ between those and the test races, so the same circuits in both years are the fairer comparison (Baku, Budapest, Monza, Zandvoort): 0.204 in 2025 vs 0.212 in 2026, before any fine-tuning. Training on the Mac GPU is not bit-for-bit repeatable: repeating the study moved single rows by up to about 0.7%, so smaller differences are noise. AP base rate is about 0.001.

| fine-tuned on | races | segments | best epoch | recon MSE | vs none | mae_nll AUROC | mae_error AUROC | mae_error_exit AP [95% CI] |
|---|---|---|---|---|---|---|---|---|
| nothing | 0 | 0 | - | 0.226 | +0.0% | 0.793 | 0.799 | 0.035 [0.003, 0.143] |
| 2026 | 1 | 12,270 | 5 | 0.227 | +0.0% | 0.790 | 0.801 | 0.036 [0.003, 0.150] |
| 2026 | 2 | 20,008 | 5 | 0.226 | -0.4% | 0.795 | 0.801 | 0.036 [0.003, 0.149] |
| 2026 | 4 | 58,577 | 5 | 0.224 | -1.2% | 0.770 | 0.802 | 0.036 [0.003, 0.150] |
| 2026 | 8 | 109,785 | 1 | 0.225 | -0.5% | 0.772 | 0.806 | 0.036 [0.003, 0.151] |
| 2025 (control) | 1 | 8,747 | 2 | 0.227 | +0.3% | 0.788 | 0.799 | 0.035 [0.003, 0.146] |
| 2025 (control) | 2 | 32,036 | 2 | 0.226 | +0.0% | 0.791 | 0.802 | 0.035 [0.003, 0.146] |
| 2025 (control) | 4 | 67,203 | 2 | 0.227 | +0.1% | 0.783 | 0.803 | 0.035 [0.003, 0.145] |
| 2025 (control) | 8 | 157,629 | 2 | 0.227 | +0.3% | 0.789 | 0.800 | 0.035 [0.003, 0.146] |

![Reconstruction error against races fine-tuned on](figures/shift_finetune.png)
