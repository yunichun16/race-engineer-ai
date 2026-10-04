# Style probes within 2026

_Generated 2026-09-30 10:34 UTC by `scripts/probe_within_season.py` from `baseline.pt`._

Logistic regression on one corner at a time, trained on the first 8 2026 events (Australian, Chinese, Japanese, Miami, Canadian, Monaco, Barcelona, Austrian) and tested on the other 7. Unlike the main probes (train 2022-2024, test 2026), the cars stay the same between training and test, so this measures how much of a team's and a driver's signature is in one corner. The embedding never saw team or driver labels.

| predict | classes | chance | Transformer embedding | hand-crafted |
|---|---|---|---|---|
| team | 11 | 0.091 | 0.443 | 0.287 |
| driver | 22 | 0.045 | 0.280 | 0.160 |
