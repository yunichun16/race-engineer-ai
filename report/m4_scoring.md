# M4: scoring every corner

_Generated 2026-10-01 00:27 UTC by `race-engineer-infer score` from `mae.pt` (temporal split)._

Every eligible corner segment gets two scores, each turned into a percentile within its session: the Telemetry Transformer's reconstruction error after the apex (`mae_error_exit`, chosen in M3) and Isolation Forest on the hand-crafted features (the best baseline). `mean` needs both to find a corner unusual, `max` needs either. The combination is **chosen by average precision on the validation events only** and reported on the test events. AP base rate is about 0.001.

| candidate | AP val | AP test [95% CI] | AUROC val | AUROC test | P@50 test | recall@5% test |
|---|---|---|---|---|---|---|
| transformer | 0.013 | 0.017 [0.005, 0.042] | 0.725 | 0.721 | 0.06 | 0.39 |
| isolation_forest | 0.011 | 0.017 [0.004, 0.059] | 0.771 | 0.819 | 0.04 | 0.46 |
| **mean** | 0.023 | 0.033 [0.007, 0.096] | 0.769 | 0.794 | 0.14 | 0.49 |
| max | 0.012 | 0.017 [0.005, 0.047] | 0.768 | 0.799 | 0.06 | 0.46 |

**Chosen:** `mean`. A corner is flagged when its combined score is in the top 1% of its session: 18,762 flags over 263 sessions (median 26 per session). Of the flags on labelled segments, 384 of 18,076 are labelled track-limits mistakes; most real mistakes carry no label, so the manual review of the top findings is the real check.
