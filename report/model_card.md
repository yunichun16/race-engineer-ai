# Model card: the Telemetry Transformer and its mistake detector

This card follows the headings of Mitchell et al., [Model Cards for Model Reporting](https://arxiv.org/abs/1810.03993), with three additions: the human check, the ONNX interface and how to get the weights. The [technical report](technical_report.md) has the method and the results in full; every number here is copied from the report it links, and a test checks that each one still appears there.

## Model details

- **What it is.** The Telemetry Transformer, a masked autoencoder trained on F1 car telemetry without labels, combined with an Isolation Forest on hand-crafted features into one mistake detector. Its [CLS] embedding describes how a corner was driven and is used to compare driving styles. The explanations of a flagged corner (its type, and where the time went) come from rules over the telemetry, not from the network.
- **Architecture.** A Transformer encoder with 4 layers, `d_model` 128 and 4 attention heads: 576k parameters. The input is one corner, 5 signal channels by 80 points (speed, throttle, brake, gear and line offset, from 250 m before to 150 m after the apex, every 5 m), normalised per corner and session, plus context tokens (tyres, fuel, traffic, weather, session). 40 m patches are hidden at a mask ratio of 0.5 and predicted with their uncertainty; the objective is the negative log-likelihood (`nll`). It outputs four mistake scores and a 64-dimensional embedding.
- **The mistake detector.** The Transformer's reconstruction error in speed and line offset from the apex to 150 m after it (`mae_error_exit`) and Isolation Forest's score each become a percentile within the session; their mean is the combined score, and a corner is flagged when it is in its session's top 1%.
- **Training.** 20 epochs (best 15) on 1,211,573 corner segments of 2022-2024, in 13.0 min on one cloud GPU (Modal, `engine/modal_app.py`).
- **Version.** The served checkpoint is `models/selected/mae.pt`, copied from `models/ablations/ablations-cloud-20260930-091907/baseline.pt` (recorded in `models/selected/SOURCE`; `models/` isn't in the repository). Every corner was scored in the run `2026-10-01T00:27:18+00:00/6b0d044ee6dd`; each output records its run, and readers refuse files from another.
- **Developed by** Yuchun Wu ([GitHub](https://github.com/yunichun16), [LinkedIn](https://www.linkedin.com/in/yuchun-wu)) as an unofficial fan project, not affiliated with Formula 1 or the FIA. The code is MIT-licensed ([LICENSE](../LICENSE)).

_Sources: [m3_ablations.md](m3_ablations.md), [m3_results.md](m3_results.md), [m4_onnx.md](m4_onnx.md), [m4_scoring.md](m4_scoring.md), [m4_summary.md](m4_summary.md), [dataset_card.md](dataset_card.md), [`ablations.toml`](../engine/configs/ablations.toml)_

## Intended use

- **What it is for.** Leads for F1 fans, people learning to read telemetry, and sports-analytics researchers: which corners of a session look like driver mistakes, how a corner differed from the driver's usual and what it cost, and how two teammates take corners differently. Every finding is a lead to check against the session replay, not a verdict.
- **How it is used.** Through the website, the chat, the MCP tools inside Claude and the REST API, which all answer from the same scoring run.
- **Non-commercial only.** The data belongs to Formula 1 and is used for non-commercial research and education ([dataset card](dataset_card.md)).

### Out of scope

- Officiating, stewarding or any decision about a penalty.
- Betting.
- Judging a driver's ability or ranking drivers: style is measured against one teammate, and between teams car and driver are mixed.
- Safety decisions of any kind.
- Anything that needs the findings to be right: they haven't been checked at scale.

## Factors

- **Session type.** In qualifying the reference for a corner is usually the field, not the driver's own laps, so a slower car's corners look like time lost; in races the costliest flags are mostly other cars.
- **Season and cars.** 2026 is a new generation of cars. Reconstruction error rises about 10% from 2025 to 2026, but only about 4% on the same circuits.
- **Circuit.** Circuits the model never saw are a bigger shift than new cars: with whole circuits held out, average precision on them drops to 0.008.
- **Data quality.** The live-timing feed fails at times, more often in 2026: data problems are 3% of 2022 flags and 14% of 2026 flags.
- **Energy management.** 2026 cars lift and coast to recharge, so coasting can be strategy rather than driving.

_Sources: [m3_summary.md](m3_summary.md), [m4_summary.md](m4_summary.md), [m4_mistakes.md](m4_mistakes.md), [m4_style.md](m4_style.md)_

## Metrics

Mistake detection is scored against the only labels there are, track-limits violations named by race control: AUROC, average precision (AP; the base rate is about 0.001), precision in the top 50 corners (P@50) and recall in the top 5%. Most real mistakes carry no label, so every score against the labels understates how often a flag is real, and detectors are compared with each other rather than with a perfect score. Intervals are 95% intervals from resampling whole events. Every choice (the score, the combination) is made on the 2025 validation events and reported on the 2026 test events. Style is measured with linear probes on the embedding (top-1 accuracy over teams or drivers) and with teammate differences, which are clear when their 95% interval excludes zero.

_Sources: [baselines.md](baselines.md), [m4_scoring.md](m4_scoring.md), [m4_style.md](m4_style.md)_

## Evaluation data

- **The 2026 season**, the first of the new cars: 15 events, 208,916 segments, 179 labelled mistakes.
- **All events**, since no detector sees a label: scoring every event is also fair, and it gives tighter intervals.
- **The rule-change study** scores later 2026 races that no run trains on (61,395 segments).
- **The style probes within 2026** train on the first 8 events and test on the other 7.

_Sources: [baselines.md](baselines.md), [m3_shift.md](m3_shift.md), [m3_style_within_season.md](m3_style_within_season.md)_

## Training data

Formula 1 live-timing data read with FastF1 3.8: all 263 sessions of 2022-2026, cut into 2,314,096 corner segments, of which 1.86 million are eligible ([dataset card](dataset_card.md), [data quality](data_quality.md)). The model trains on 2022-2024 (68 events, 1,211,573 segments) and is validated on 2025 (24 events, 442,570 segments). It never sees a label. The data isn't redistributed: the pipeline rebuilds it locally.

_Sources: [dataset_card.md](dataset_card.md), [data_quality.md](data_quality.md), [m3_summary.md](m3_summary.md), [m3_ablations.md](m3_ablations.md)_

## Quantitative analyses

**The deployed detector**, on the 2026 test events, each score a percentile within its session:

| candidate | AP, test [95% CI] | AUROC, test | P@50, test | recall@5%, test |
|---|---|---|---|---|
| Transformer (`mae_error_exit`) | 0.017 [0.005, 0.042] | 0.721 | 0.06 | 0.39 |
| Isolation Forest | 0.017 [0.004, 0.059] | 0.819 | 0.04 | 0.46 |
| **mean of the two (deployed)** | 0.033 [0.007, 0.096] | 0.794 | 0.14 | 0.49 |

The intervals overlap. On all events, the Transformer's score has an AUROC of 0.670 [0.629, 0.712] and an AP of 0.012 [0.007, 0.017], against 0.751 [0.714, 0.785] and 0.007 [0.005, 0.012] for Isolation Forest; the planned score, the model's negative log-likelihood, is weak (AUROC 0.659 [0.624, 0.696]).

**The flags.** 18,762 flags over 263 sessions. 56% were not clearly slower than the driver's usual; 2,058 look like driving mistakes, 1,648 distinct ones, costing 0.37 s at the median. Of 5 documented incidents, `find_mistakes` lists 3; only 5 of 43 single-car 'leaving the track' incidents were flagged at that turn.

**Style.** Within 2026 a linear probe on the embedding names the team from a single corner 44% of the time and the driver 28%, against 29% and 16% for the hand-crafted features (chance 9% and 4.5%); across seasons, 8.8% against 11% for the driver. In 95% of teammate pair-seasons at least one of seven metrics differs clearly, against 29% by chance, and the braking-point difference carries into the next season (r = 0.86).

**The rule change.** Fine-tuning on 1 to 8 races of 2026 lowers error on later 2026 races by 0 to 1.2%, with no steady trend; repeat runs of the same setting moved by up to 0.7%, so any benefit is small and not firmly established.

**ONNX.** The exported scorer matches PyTorch: the largest relative difference was 1.1e-05 against a gate of 1e-04, and none of the 140 flags of the 2026 Azerbaijan Grand Prix changes.

_Sources: [m4_scoring.md](m4_scoring.md), [m3_results.md](m3_results.md), [m4_summary.md](m4_summary.md), [m3_summary.md](m3_summary.md), [m4_onnx.md](m4_onnx.md)_

## Human-checked precision

One reviewer judged the 50 top-ranked findings of 2026, 42 of them against the session replay on F1 TV: 35 real, 14 not, 1 can't tell, a precision of 71% [58%, 82%] (35 / 49). When race control named the moment it was 85% [68%, 94%]; when only the telemetry did, 55% [35%, 73%]. On the 22 findings not inspected while building the rules it was 77% [57%, 90%]. A second reader, Claude, agreed on 84% of the 50 (Cohen's kappa 0.67).

This is one reviewer and 50 findings: the intervals are wide, and some verdicts come from the traces alone (7 of 50). The site quotes no accuracy figure until a larger review exists, and leaves this section out of its copy of this card. The review is in m4_review.md, in the repository.

_Sources: [m4_summary.md](m4_summary.md)_

## Ethical considerations

- **Named drivers.** The findings name real drivers and say they made a mistake, and a finding can be wrong: traffic, data glitches and strategy can look like mistakes. So the site calls itself an unofficial fan project, says that a finding can be wrong, and quotes no accuracy figure; every finding comes with the evidence it rests on.
- **One reviewer.** The only human check is one person's judgement of 50 findings.
- **Coarse data.** About 4 samples per second and a brake that is on/off: small differences are at the limit of what the data can show.
- **Data rights.** The data belongs to Formula 1; it is used for non-commercial research and education and is not redistributed. It holds nothing personal beyond what Formula 1 publishes about its drivers.
- **Visitors.** The chat counts questions per connection with a salted hash of the IP address that changes every UTC day; no IP address, cookie or question is stored.

_Sources: [m4_summary.md](m4_summary.md), [dataset_card.md](dataset_card.md), [m7_ship.md](m7_ship.md)_

## Caveats and recommendations

Known failure modes:

- **Traffic.** In races the costliest flags were mostly other cars. A loss counts as traffic only when another car was already close as it began; a mistake made while a car was close counts as traffic when that car then passes, and cars on qualifying out-laps are placed only roughly, so they never count ([m4_mistakes.md](m4_mistakes.md)).
- **Data glitches.** Frozen or spiking speed and a brake signal on at full throttle: 5% of flags are data problems, and the checks, set by eye, miss short glitches ([m4_summary.md](m4_summary.md), [m4_mistakes.md](m4_mistakes.md)).
- **Late throttle / wide exit**, the commonest driving type (513 flags), is the one to tighten next ([m4_summary.md](m4_summary.md)).
- **Track cuts that save time.** Only 5 of 43 single-car 'leaving the track' incidents were flagged: a cut usually saves time and, in a smoothed position feed, looks normal ([m4_summary.md](m4_summary.md)).
- **2026 energy management.** Even in qualifying, coasting per corner was 26.7 m in 2026 against 18.3 m in 2025, so a lift or a coast in 2026 can be energy management ([m4_style.md](m4_style.md)).
- **Exit losses.** Time lost stops 145 m after the apex, so exit mistakes are undercounted ([m4_summary.md](m4_summary.md)).

Recommendations: check a finding against the replay before repeating it; read a type as how the corner differed, not why; tell race-control findings from telemetry-only ones, which are different evidence; compare driving styles between teammates, not across teams; and treat a new circuit's findings with extra care.

## The ONNX interface

The whole scoring step is one ONNX graph (opset 23): a batch of model inputs goes in, the four mistake scores and the embedding come out, with the six model passes unrolled inside. `OnnxScorer` (`race_engineer/inference/onnx_scorer.py`) runs it with onnxruntime alone, without PyTorch, and refuses a file whose recorded checkpoint SHA-256 differs from the selected checkpoint's. The inputs are float32 (`context_cat` is int64), the batch dimension is dynamic, and the signals are z-scores against the field at that corner, so scoring still needs the session's other laps, and flags are percentiles within the session.

|  | name | shape | type |
|---|---|---|---|
| input | `signal` | `['batch', 5, 80]` | `tensor(float)` |
| input | `context_channels` | `['batch', 2, 80]` | `tensor(float)` |
| input | `context_cat` | `['batch', 2]` | `tensor(int64)` |
| input | `context_num` | `['batch', 4]` | `tensor(float)` |
| output | `mae_nll` | `['batch']` | `tensor(float)` |
| output | `mae_error` | `['batch']` | `tensor(float)` |
| output | `mae_error_exit` | `['batch']` | `tensor(float)` |
| output | `mae_error_speed_patch` | `['batch']` | `tensor(float)` |
| output | `cls_embedding` | `['batch', 64]` | `tensor(float)` |

On 8 CPU cores it scores a whole race (13,945 segments) in 7 s, at 0.75 times PyTorch's speed.

_Sources: [m4_onnx.md](m4_onnx.md)_

## Getting the weights

The weights are published with the code, as assets of the GitHub Release [v1.0](https://github.com/yunichun16/race-engineer-ai/releases/tag/v1.0) of the public repository: `mae.pt` (the PyTorch checkpoint, 2.33 MB) and `mae_scorer.onnx` (the scorer, 2.61 MB), with their SHA-256 checksums in the release notes. Put them in `models/selected/` to score with them. The repository rebuilds the data and retrains the model (`make data`, `make experiments`, `make select-model`, `make m4`), though a retrained model differs a little: repeat runs moved reconstruction error by up to about 0.7%.

_Sources: [m4_onnx.md](m4_onnx.md), [m3_summary.md](m3_summary.md)_
