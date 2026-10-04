# M4 summary: from flagged corners to explanations

The inference-and-explanations milestone, in one page. Details and every number are in the linked reports.

**Setup.** The model kept in M3 and Isolation Forest score all 1.86 million eligible corner
segments of the 263 sessions of 2022-2026. Each score becomes a percentile within its session,
and a corner is **flagged** when the combined score is in its session's top 1%: 18,762 flags, a
median of 26 per session ([m4_scoring.md](m4_scoring.md)). Each flag is compared with the same
driver's usual way through that turn, costed in time and given a type
([m4_mistakes.md](m4_mistakes.md)). Teammates are compared corner by corner
([m4_style.md](m4_style.md)), and the scorer is exported to ONNX ([m4_onnx.md](m4_onnx.md)).

## Findings

**1. The two detectors together beat either alone, within wide intervals.** Their mean was
chosen on the validation events only (average precision 0.023, against 0.013 for the Transformer
and 0.011 for Isolation Forest). On the test events it reaches 0.033 [0.007, 0.096] against 0.017
for each, with a base rate of about 0.001. The intervals overlap. Of the flags on labelled
segments, 384 of 18,076 are labelled track-limits mistakes: most real mistakes carry no label.

**2. Most flags are unusual, not costly.** 56% were not clearly slower than the driver's usual
(median +0.02 s). 5% are part of a slow-down and 5% are data problems, which rise from 3% of 2022
flags to 14% of 2026 flags. Type thresholds sit at the 99th percentile of ordinary 2022-2024
corners. 2,058 flags look like driving mistakes, 1,648 distinct ones. A distinct mistake costs
0.37 s at the median (90th percentile 1.05 s), lost mostly on exit in 54% of cases. Late throttle
/ wide exit is the commonest type (513 flags), then over-slowing (284).

**3. In races, the biggest losses were mostly other cars.** Before any human review, Claude read
the top 50 findings of 2026 (m4_review_preread_v1.csv) and judged 34
not driver mistakes, mostly backmarkers being lapped. That led to the traffic rule: a loss that
began with another car already close is typed as being lapped, racing another car, held up by a
slower car, or (in qualifying) not a full push lap. Every setting comes from 2022-2025, never
2026. Traffic among each race's top-3 findings fell from 259 of 385 (67%) to 34 of 372 (9%), and
in 2026 from 65% to 8%. Of the pre-read's 34, 25 are now left out (7 were already), and all 13
judged real are still listed. Those readings shaped the rule, so this is a development check, not
an evaluation.

**4. Known incidents (the M4 check): found when the detectors flag them.** `find_mistakes`, which
ranks by time lost plus 0.5 s when race control named the driver alone, lists 3 of the 5
documented cases, and `explain_corner` gives a plausible type for all 4 with data. Russell's lap
47 at Monaco was not flagged (percentile 95.5); Verstappen's start in Hungary 2024 has no usable
telemetry. Only 5 of 43 single-car 'leaving the track' incidents were flagged at that turn: a cut
usually saves time and, in a smoothed position feed, looks normal. Race-control and
telemetry-only findings are different evidence. A race-control record sets the type once the
corner is flagged, so that check is partly circular (the driving rules alone fit all 4), and such
flags keep their type whatever the traffic. The other types are calibrated but not validated
against labels.

**5. Teammates drive measurably differently, by small amounts.** In 95% of pair-seasons at least
one of seven metrics differs clearly, against 29% by chance. Median sizes: 3.1 m of braking
point, 0.8 km/h of minimum speed, 0.020 s per corner. Style persists into the next season
(r = 0.86 for the braking point, over 25 pairs). The embedding is mostly the car (teammates'
median cosine 0.92, different teams -0.10), so style is the within-team difference, which holds
its direction beyond noise for 50% of driver-seasons. 2026 coasting differences can be energy
management, not style.

**6. ONNX gives the same answers without PyTorch, a bit slower.** The export passed its parity
gate (largest relative difference 1.1e-05 against a gate of 1e-04; none of the 140 flags of the
2026 Azerbaijan Grand Prix changes). It runs at 0.75 times PyTorch's speed on all CPU cores, a
whole race in 7 s.

## Human-checked precision

**7. About 7 in 10 of the top findings are real driver mistakes; 85% when race control named
the moment, about half when only the telemetry did.** A human reviewer judged all 50 findings of the
queue, 42 of them checked against the session replay on F1 TV: 35 real, 14 not, 1 can't tell
(m4_review.md).

| findings | real / judged | precision [95% CI] |
|---|---|---|
| all 50 | 35 / 49 | 71% [58%, 82%] |
| top 10 | 8 / 9 | 89% [56%, 98%] |
| not inspected while building the rules | 17 / 22 | 77% [57%, 90%] |
| track limits named by race control | 23 / 27 | 85% [68%, 94%] |
| telemetry only | 12 / 22 | 55% [35%, 73%] |

The queue ([m4_review_queue.csv](m4_review_queue.csv), built by `engine/scripts/review_queue.py`)
is the top 50 listed mistakes of 2026 in `find_mistakes`' ranking, one per moment, at most 2 per
session and one per driver per session: 50 findings from 29 sessions. Precision is
real / (real + not), so "can't tell" counts as neither. 28 findings are on laps inspected during
development (`DEVELOPMENT_LAPS` and the pre-read's), so the 22 without them are the cleaner
estimate. The order holds up: precision falls from the top 10 to the top 50.

- **By type:** over-slowing holds up (7 of 7). Late throttle / wide exit, the commonest type, is a
  coin toss (4 of 9). "Unclear" findings were never real (0 of 3).
- **Second reader:** Claude's reads, shown to the reviewer only after each answer, agreed on 84%
  of the 50 (Cohen's kappa 0.67).
- **Why the 14 were not mistakes.** The verdicts are the reviewer's; the reasons combine their
  notes with the second reader's reads.
  - **Other cars (6), still the largest class.** Four wheel-to-wheel battles: all four rejected
    track-limits findings, which keep their type whatever the traffic (finding 4). Also a car
    rejoining from the pits, and a slow car ahead in qualifying.
  - **Laps given up in qualifying (3).**
  - **Data glitches (3).** Frozen speed, and the brake reading on at full throttle.
  - **A car problem or deliberate lift (1).**
  - **A pack on lap 4 (1).** Traffic or a glitch.
- **One answer changed after the review.** A qualifying finding first marked as an out-lap
  turned out, in the lap data, to be on the push lap after the out-lap, so the replay watched was
  likely the wrong lap. On the reviewer's instruction it counts as real, judged from the traces.
  Kept as first answered, precision would be 34 of 49, 69% [55%, 80%].

These were found after the rules were fixed, and nothing was tuned on them. Any fix they suggest
(the battle check on race-control findings, catching abandoned qualifying laps and frozen
telemetry) needs a fresh sample to measure.

## What it means

The detectors find unusual corners; the explanations decide which cost time and why, and context
removes most of the costly-looking ones that aren't driving. What is left is mostly real: about 7
in 10 of the top findings, with race-control findings far more reliable than telemetry-only ones.
The tools should say which kind each finding is. Each output records the scoring run it was built
from, and readers refuse files from another run; `make m4` runs the chain (score, mistakes, style,
onnx).

Next:
- Apply the battle check to race-control findings and catch abandoned qualifying laps and frozen
  telemetry, then measure on a fresh 2026 sample, not this one.
- Tighten late throttle / wide exit.
- Recheck M3's smaller model and higher mask ratio against the human verdicts.

## Limits

- Types say how a corner differed, not why. Only track limits are labelled.
- The human check is one reviewer and 50 findings. The intervals are wide, and some verdicts come
  from the traces alone (7 of 50).
- Time lost stops 145 m after the apex, so exit mistakes are undercounted.
