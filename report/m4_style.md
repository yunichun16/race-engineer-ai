# M4: driving-style profiles

_Generated 2026-10-01 00:31 UTC by `uv run python -m race_engineer.inference.style`._

Teammates share a car, so the difference between two teammates on the same corner, in the same session, on the same tyre compound is the cleanest measure of driving style this data allows. Each driver's median over their clean laps is taken per corner, the teammates' medians are subtracted, and the differences are averaged over the season. Clean laps are push laps in qualifying, matched by attempt (each driver's first k push laps of a session, k being the teammate's count, so the one who reached Q3 doesn't add laps on a faster track and new tyres), and green-flag race laps started at least 1 s behind the car ahead; lap time not deleted and tyre compound known. A weekend's corners share track, weather and set-up, so the evidence is in how much a difference varies between weekends: the 95% interval is a t-interval on the event-clustered standard error with events - 1 degrees of freedom, and there is none below 4 events. A difference is **clear** when its interval excludes zero.

**How often chance says clear.** Every pair's differences were centred and given a random sign per weekend (20 times per pair: the pair's own noise, no difference) and summarised again: 6% of cells come out clear by chance (5% with 4 to 7 events, 6% with 8 or more), close to the nominal 5%. Across the seven metrics, a pair-season with no real difference still shows at least one clear one 29% of the time, and the breakdowns by corner type and session multiply the chances: read single starred cells with that in mind.

**Data:** 1,344,063 clean segments, 118,664 driver-corner medians, 65 teammate pairs over 5 seasons, 56 of them sharing a car at 5+ events (the rest are substitutes and mid-season swaps: kept in the tables, left out of the rankings and checks). **Corner types** come from each corner's speed over the event: 571 slow (< 120 km/h), 575 medium and 630 fast (> 200 km/h) event-corners. Corner windows (250 m before to 150 m after the marker) overlap, so braking point, peak braking deceleration, minimum speed and the full-throttle point are counted only at the corner that owns the window's slowest point (40% of corners; the rest are flat kinks and parts of complexes whose slowest point belongs to a neighbour), and only from laps whose own slowest point is within 50 m of the corner's (11% of laps at fast owned corners are left out: their minimum is at the window's start or the next corner). Peak braking deceleration is the peak before the lap's slowest point, from the speed trace (the whole-window peak is often the braking for the next corner), without values above 80 m/s², which are sampling spikes. A lap not back on full throttle within the window counts as later than any that is (7% of laps at owned corners, 10% at slow ones); a driver's median at a corner is kept while fewer than half their laps are like that. Coasting, exit speed and time through the corner count everywhere.

## What the profiles say

- **Teammates drive measurably differently.** In 95% of pair-seasons at least one of the seven metrics differs clearly, against 29% by chance; per metric the share is 23% to 52%, against 5% to 6% by chance. The differences are small: median sizes of 3.1 m of braking point, 0.8 km/h of minimum speed and 0.020 s per corner.

- **Sanity check.** The difference in time through the corner in qualifying ranks the pairs like their qualifying gap (median over sessions of the gap between their best push laps over the same attempts): Spearman 0.89 over 56 pair-seasons, same sign in 84%. The corner windows cover most of a lap, so this mainly confirms that segment times and their alignment add up; it doesn't validate the braking point or the other metrics.

- **Style persists.** For the 25 pairs who stayed teammates into the next season, one season's difference predicts the next: r = 0.86 for the braking point (the most stable) down to 0.47 for the exit speed.

- **What goes with pace.** Across pairs, the difference most tied to time through the corner is the exit speed (median r over seasons -0.87; negative: more of it, quicker). Coasting correlates -0.52 with the time difference in 2026 (11 pairs), against -0.23 to +0.14 in earlier seasons: with the new cars the teammate who coasts more tends to be the quicker one, consistent with energy management being part of pace. With this few pairs it is a hint, not a finding.

| metric | positive means | pair-seasons with a clear difference | by chance | median size | median standard error |
|---|---|---|---|---|---|
| braking point (m) | brakes later | 39% | 5% | 3.1 | 1.7 |
| peak braking deceleration (m/s²) | brakes harder | 23% | 5% | 0.37 | 0.35 |
| minimum speed (km/h) | carries more minimum speed | 50% | 5% | 0.8 | 0.3 |
| full-throttle point (m) | is back on full throttle later | 52% | 6% | 2.1 | 0.9 |
| coasting (m) | coasts more | 48% | 5% | 2.5 | 1.2 |
| exit speed (km/h) | exits faster | 36% | 6% | 0.4 | 0.3 |
| time through the corner (s) | is slower through the corner | 38% | 6% | 0.020 | 0.012 |

Season to season, for pairs who stayed together (r between one season's difference and the next's):

| metric | pairs | r | same_sign |
|---|---|---|---|
| braking point | 25 | 0.86 | 80% |
| peak braking deceleration | 25 | 0.54 | 68% |
| minimum speed | 25 | 0.55 | 72% |
| full-throttle point | 25 | 0.54 | 64% |
| coasting | 25 | 0.63 | 64% |
| exit speed | 25 | 0.47 | 72% |
| time through the corner | 25 | 0.57 | 80% |

Correlation across pairs between each difference and the difference in time through the corner, per season (pairs with at least 5 events):

| metric | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| braking point | -0.32 | -0.01 | -0.49 | -0.32 | 0.01 |
| peak braking deceleration | -0.28 | 0.33 | 0.15 | 0.14 | 0.53 |
| minimum speed | -0.58 | -0.87 | -0.77 | -0.89 | -0.83 |
| full-throttle point | 0.76 | 0.69 | 0.16 | 0.29 | 0.20 |
| coasting | 0.09 | -0.23 | -0.13 | 0.14 | -0.52 |
| exit speed | -0.92 | -0.81 | -0.87 | -0.89 | -0.87 |

## 2026 teammates

All sessions and corners, A minus B [95% interval]; bold where clear. Signs: braking point positive = A brakes later; full-throttle point positive = A is on full throttle later; time positive = A is slower. Pairs with fewer than 4 events (mid-season swaps) get no interval (n/a) and nothing is called clear for them.

| A - B | team | events | braking point (m) | peak braking deceleration (m/s²) | minimum speed (km/h) | full-throttle point (m) | coasting (m) | exit speed (km/h) | time through the corner (s) |
|---|---|---|---|---|---|---|---|---|---|
| ALB - SAI | Williams | 15 | -3.4 [-7.7, +0.8] | -0.68 [-2.16, +0.81] | +0.3 [-0.6, +1.1] | **+5.6 [+3.4, +7.7]** | **+6.3 [+3.5, +9.1]** | **-0.8 [-1.4, -0.1]** | +0.016 [-0.006, +0.038] |
| ALO - STR | Aston Martin | 15 | +5.0 [-2.9, +12.9] | -0.31 [-2.02, +1.41] | **+0.9 [+0.1, +1.6]** | -0.8 [-2.5, +1.0] | +3.6 [-0.2, +7.3] | **+1.0 [+0.5, +1.5]** | **-0.043 [-0.068, -0.017]** |
| ANT - RUS | Mercedes | 15 | +0.0 [-5.4, +5.5] | +0.13 [-0.73, +0.98] | +0.6 [-0.3, +1.4] | **+2.1 [+0.1, +4.1]** | +0.1 [-3.3, +3.5] | +0.3 [-0.2, +0.9] | **-0.034 [-0.066, -0.002]** |
| BEA - OCO | Haas F1 Team | 15 | -4.0 [-8.4, +0.5] | +0.73 [-0.33, +1.79] | **+1.8 [+1.0, +2.5]** | **+3.3 [+0.9, +5.7]** | +2.0 [-0.6, +4.6] | +0.3 [-0.2, +0.8] | **-0.022 [-0.041, -0.003]** |
| BOR - HUL | Audi | 15 | -2.7 [-7.0, +1.5] | -0.05 [-1.02, +0.92] | -0.2 [-1.1, +0.8] | **+3.6 [+1.3, +6.0]** | -1.9 [-5.3, +1.4] | -0.3 [-1.5, +0.9] | -0.011 [-0.050, +0.028] |
| BOT - PER | Cadillac | 15 | +2.0 [-3.0, +7.0] | +0.61 [-0.10, +1.33] | **-1.6 [-2.5, -0.7]** | +1.0 [-1.7, +3.7] | **-3.9 [-7.4, -0.4]** | **-0.8 [-1.5, -0.1]** | **+0.053 [+0.020, +0.086]** |
| COL - GAS | Alpine | 15 | +3.7 [-0.8, +8.3] | +0.96 [-0.49, +2.40] | -0.5 [-1.6, +0.7] | -0.3 [-2.9, +2.3] | -1.4 [-5.0, +2.2] | -0.3 [-1.1, +0.6] | +0.027 [-0.001, +0.056] |
| HAD - VER | Red Bull Racing | 12 | -0.4 [-4.3, +3.4] | **+2.39 [+1.00, +3.78]** | **-1.3 [-1.8, -0.9]** | **+6.0 [+2.2, +9.8]** | **-6.8 [-9.2, -4.3]** | -0.8 [-1.7, +0.1] | +0.035 [-0.001, +0.071] |
| HAM - LEC | Ferrari | 15 | **-6.1 [-9.2, -3.1]** | +0.12 [-0.86, +1.10] | +0.4 [-0.5, +1.2] | **-4.1 [-7.4, -0.8]** | +3.9 [-0.3, +8.1] | -0.2 [-0.8, +0.4] | +0.013 [-0.024, +0.051] |
| LAW - LIN | Racing Bulls | 12 | -3.9 [-9.1, +1.3] | -0.76 [-2.09, +0.56] | +1.1 [-0.0, +2.3] | -0.2 [-2.2, +1.9] | **+6.2 [+2.7, +9.8]** | **+0.6 [+0.0, +1.1]** | -0.019 [-0.044, +0.007] |
| LAW - VER | Red Bull Racing | 3 | -1.0 [n/a] | +1.01 [n/a] | -2.5 [n/a] | +6.1 [n/a] | +0.5 [n/a] | -1.9 [n/a] | +0.096 [n/a] |
| LIN - TSU | Racing Bulls | 3 | -1.0 [n/a] | -1.38 [n/a] | +0.7 [n/a] | +1.2 [n/a] | -7.2 [n/a] | -1.1 [n/a] | -0.002 [n/a] |
| NOR - PIA | McLaren | 15 | +0.8 [-5.1, +6.6] | -0.32 [-1.56, +0.92] | +0.4 [-0.2, +1.1] | **-2.6 [-4.9, -0.3]** | **+2.3 [+0.8, +3.8]** | -0.1 [-0.8, +0.6] | -0.021 [-0.042, +0.000] |

The strongest and most reliable differences, per pair (in brackets: A minus B by corner type and by session, * = clear):

- **ALB vs SAI** (Williams): ALB is back on full throttle later than SAI: by 5.6 m [3.4, 7.7], at 12 of 15 events (slow +4.3*, medium +5.4*, fast +12.7*; qualifying +5.3*, races +5.7*); ALB coasts more than SAI: by 6.3 m [3.5, 9.1], at 14 of 15 events (slow +6.8*, medium +7.1*, fast +5.0*; qualifying +3.8*, races +8.0*).
- **HAD vs VER** (Red Bull Racing): HAD carries less minimum speed than VER: by 1.3 km/h [0.9, 1.8], at 11 of 12 events (slow -1.0*, medium -1.7*, fast -1.8; qualifying -0.6, races -2.1*); HAD coasts less than VER: by 6.8 m [4.3, 9.2], at 12 of 12 events (slow -9.4*, medium -9.4*, fast -2.0; qualifying -7.0*, races -6.6*).
- **BEA vs OCO** (Haas F1 Team): BEA carries more minimum speed than OCO: by 1.8 km/h [1.0, 2.5], at 14 of 15 events (slow +1.6*, medium +2.3*, fast +1.1; qualifying +1.5*, races +2.0*); BEA is back on full throttle later than OCO: by 3.3 m [0.9, 5.7], at 11 of 15 events (slow +3.7*, medium +3.2*, fast +1.5; qualifying +2.4*, races +4.0).
- **ALO vs STR** (Aston Martin): ALO exits faster than STR: by 1.0 km/h [0.5, 1.5], at 12 of 15 events (slow +0.7*, medium +1.5*, fast +0.8; qualifying +1.0*, races +1.0*); ALO is quicker through the corner than STR: by 0.043 s [0.017, 0.068], at 12 of 15 events (slow -0.053*, medium -0.049*, fast -0.028*; qualifying -0.035*, races -0.048*).
- **HAM vs LEC** (Ferrari): HAM brakes earlier than LEC: by 6.1 m [3.1, 9.2], at 13 of 15 events (slow -10.2*, medium +0.9, fast -1.4; qualifying -5.2*, races -6.9*); HAM is back on full throttle earlier than LEC: by 4.1 m [0.8, 7.4], at 10 of 15 events (slow -4.5*, medium -4.1, fast -1.8; qualifying -3.6, races -4.6*).
- **BOT vs PER** (Cadillac): BOT carries less minimum speed than PER: by 1.6 km/h [0.7, 2.5], at 12 of 15 events (slow -0.7, medium -2.8*, fast -2.9; qualifying -1.2*, races -1.9*); BOT is slower through the corner than PER: by 0.053 s [0.020, 0.086], at 11 of 15 events (slow +0.052, medium +0.067*, fast +0.040*; qualifying +0.020, races +0.077*).
- **LAW vs LIN** (Racing Bulls): LAW coasts more than LIN: by 6.2 m [2.7, 9.8], at 12 of 12 events (slow +6.4, medium +7.9*, fast +4.7*; qualifying +6.7*, races +5.8*); LAW exits faster than LIN: by 0.6 km/h [0.0, 1.1], at 7 of 12 events (slow +1.8*, medium +0.3, fast -0.1; qualifying +0.3, races +0.9*).
- **NOR vs PIA** (McLaren): NOR coasts more than PIA: by 2.3 m [0.8, 3.8], at 12 of 15 events (slow +2.7*, medium +2.5, fast +1.8; qualifying +4.3*, races +0.5); NOR is back on full throttle earlier than PIA: by 2.6 m [0.3, 4.9], at 9 of 15 events (slow -1.8, medium -5.1*, fast +0.6; qualifying -2.8*, races -2.5).
- **BOR vs HUL** (Audi): BOR is back on full throttle later than HUL: by 3.6 m [1.3, 6.0], at 10 of 15 events (slow +2.9*, medium +4.1, fast +6.5*; qualifying +1.7, races +5.2*).
- **ANT vs RUS** (Mercedes): ANT is back on full throttle later than RUS: by 2.1 m [0.1, 4.1], at 9 of 15 events (slow +2.1*, medium +3.0, fast -1.4; qualifying +2.2, races +2.0*); ANT is quicker through the corner than RUS: by 0.034 s [0.002, 0.066], at 9 of 15 events (slow -0.054*, medium -0.035, fast -0.013; qualifying -0.032*, races -0.036).

What a tool gets from `compare_drivers('ALB', 'SAI', 2026)`:

```text
2026: ALB and SAI, Williams teammates, compared on 611 corners that both took in the same session on the same tyre compound, over 15 events. Differences are ALB minus SAI, with 95% intervals from the spread between events.
Clear differences, strongest first (by corner type and session; * = clear):
- ALB is back on full throttle later than SAI: by 5.6 m [3.4, 7.7], at 12 of 15 events (slow +4.3*, medium +5.4*, fast +12.7*; qualifying +5.3*, races +5.7*).
- ALB coasts more than SAI: by 6.3 m [3.5, 9.1], at 14 of 15 events (slow +6.8*, medium +7.1*, fast +5.0*; qualifying +3.8*, races +8.0*).
- ALB exits slower than SAI: by 0.8 km/h [0.1, 1.4], at 12 of 15 events (slow -0.9, medium -0.5, fast -0.9; qualifying -0.4, races -1.0*).
No clear difference in braking point, peak braking deceleration, minimum speed, time through the corner.
Style embedding: their field-relative centroids have cosine +0.80 (median for 2026 teammates +0.94, for drivers of different teams -0.05): as for most teammates, the shared car dominates. The embedding difference between ALB and SAI points the same way in odd and even rounds (cosine +0.87, p = 0.002; noise alone reaches +0.68 one time in 20): a steady difference in style.
Note: ALB wasn't back on full throttle within 145 m of the corner on 15% of laps at the corners that own their slowest point; those count as later than the window.
Note: SAI wasn't back on full throttle within 145 m of the corner on 13% of laps at the corners that own their slowest point; those count as later than the window.
Note: in 2026 drivers lift and coast to manage energy, often on team instructions, so coasting and braking-point differences can be strategy rather than style.
```

## Style embedding

Each segment's 64-dim [CLS] embedding is standardised per dimension and averaged per driver and corner. A driver's **centroid** is their mean difference from the field on the same corners (car and driver together); their **style vector** is their mean difference from their teammate on shared corners (the driver alone, relative to that teammate).

**The embedding is mostly the car.** Teammates' centroids have a median cosine of 0.92, drivers of different teams -0.10, and the nearest driver is the teammate for 83% of driver-seasons. (The plain mean embeddings say the same: 0.94 against 0.16.) That is why style here is the within-team difference, not the centroid.

**The within-team difference holds its direction for about half the driver-seasons.** Its cosine between the odd and the even rounds of a season (`style_consistency`, per teammate pair; a driver-season shows its main teammate) has a median of 0.74. Noise alone gives wide cosines here, because the teammate differences vary along only about 6 independent directions (participation ratio), not 64, so the reference is measured per pair: each event's deviation from the pair's mean difference gets a random sign (2,000 times), keeping the noise and removing any steady direction. The 95th percentile of those cosines is about 0.71 (median over driver-seasons), and 50% of driver-seasons beat theirs (`consistent`, p < 0.05).

| year | driver_seasons | cos_teammate | cos_field | teammate_nearest | style_consistency | noise_95th | consistent |
|---|---|---|---|---|---|---|---|
| 2022 | 20 | 0.922 | -0.0906 | 85% | 0.749 | 0.725 | 60% |
| 2023 | 22 | 0.928 | -0.118 | 82% | 0.854 | 0.71 | 64% |
| 2024 | 22 | 0.904 | -0.0923 | 82% | 0.643 | 0.72 | 32% |
| 2025 | 21 | 0.817 | -0.0541 | 76% | 0.706 | 0.733 | 48% |
| 2026 | 22 | 0.938 | -0.054 | 91% | 0.672 | 0.688 | 45% |

**What the style map means.** The two principal axes of the style vectors (`style_pc1/2`) explain 34% and 30% of their variance: so nearly the same that which comes first, and how the two are turned within their plane, isn't stable from one sample of teams to another, and neither axis should be labelled on its own. The plane as a whole is readable. Over the 56 teammate pairs where at least one side had no other teammate that season (each pair once; correlations through the origin, since a difference has no natural sign), it holds exit speed (R = 0.90; 0.68 to 0.92), time through the corner (R = 0.87; 0.64 to 0.89), minimum speed (R = 0.69; 0.44 to 0.77), coasting (R = 0.66; 0.59 to 0.72) (R: multiple correlation with the plane; the range is over 200 refits on resampled teams). To label a chart, draw each metric as an arrow along (r_pc1, r_pc2) (`style_axes.parquet`).

| metric | r_pc1 | r_pc2 | R (plane) | R, 5-95% over resamples |
|---|---|---|---|---|
| braking point | -0.20 | 0.28 | 0.34 | 0.31 to 0.36 |
| peak braking deceleration | -0.11 | -0.16 | 0.19 | 0.17 to 0.20 |
| minimum speed | 0.22 | 0.66 | 0.69 | 0.44 to 0.77 |
| full-throttle point | 0.05 | -0.45 | 0.45 | 0.36 to 0.50 |
| coasting | 0.66 | -0.02 | 0.66 | 0.59 to 0.72 |
| exit speed | 0.06 | 0.89 | 0.90 | 0.68 to 0.92 |
| time through the corner | -0.11 | -0.86 | 0.87 | 0.64 to 0.89 |

2026 driver-seasons (`style_consistency` against the main teammate, with its p-value against noise; `style_twin`: the driver of another team whose difference from their own teammate points most the same way; teammates mirror each other, since one's style vector is minus the other's):

| driver | team | teammates | events | cos_teammate | cos_field | style_consistency | p | style_twin |
|---|---|---|---|---|---|---|---|---|
| COL | Alpine | GAS | 15 | 0.92 | 0.03 | 0.51 | 0.214 | BOT (0.73) |
| GAS | Alpine | COL | 15 | 0.92 | 0.02 | 0.51 | 0.214 | PER (0.73) |
| ALO | Aston Martin | STR | 15 | 0.99 | -0.10 | 0.67 | 0.096 | ANT (0.74) |
| STR | Aston Martin | ALO | 15 | 0.99 | -0.10 | 0.67 | 0.096 | RUS (0.74) |
| BOR | Audi | HUL | 15 | 0.97 | 0.02 | 0.86 | 0.005 | COL (0.70) |
| HUL | Audi | BOR | 15 | 0.97 | 0.01 | 0.86 | 0.005 | GAS (0.70) |
| BOT | Cadillac | PER | 15 | 0.94 | -0.14 | 0.54 | 0.104 | OCO (0.76) |
| PER | Cadillac | BOT | 15 | 0.94 | -0.13 | 0.54 | 0.104 | BEA (0.76) |
| HAM | Ferrari | LEC | 15 | 0.97 | -0.05 | 0.27 | 0.305 | ALB (0.76) |
| LEC | Ferrari | HAM | 15 | 0.97 | -0.03 | 0.27 | 0.305 | SAI (0.76) |
| BEA | Haas F1 Team | OCO | 15 | 0.83 | 0.00 | 0.91 | 0.000 | ANT (0.85) |
| OCO | Haas F1 Team | BEA | 15 | 0.83 | -0.05 | 0.91 | 0.000 | RUS (0.85) |
| NOR | McLaren | PIA | 15 | 0.92 | -0.22 | 0.95 | 0.000 | GAS (0.72) |
| PIA | McLaren | NOR | 15 | 0.92 | -0.23 | 0.95 | 0.000 | COL (0.72) |
| ANT | Mercedes | RUS | 15 | 0.99 | -0.03 | 0.74 | 0.024 | BEA (0.85) |
| RUS | Mercedes | ANT | 15 | 0.99 | -0.03 | 0.74 | 0.024 | OCO (0.85) |
| LAW | Racing Bulls | LIN | 12 | 0.92 | -0.07 | 0.65 | 0.069 | BEA (0.72) |
| LIN | Racing Bulls | LAW, TSU | 15 | 0.92 | -0.10 | 0.65 | 0.069 | OCO (0.61) |
| TSU | Racing Bulls | LIN | 3 | 0.93 | -0.14 | n/a | n/a | RUS (0.45) |
| HAD | Red Bull Racing | VER | 12 | 0.99 | -0.18 | 0.29 | 0.315 | PIA (0.52) |
| LAW | Red Bull Racing | VER | 3 | 0.97 | -0.22 | n/a | n/a | STR (0.42) |
| VER | Red Bull Racing | HAD, LAW | 15 | 0.99 | -0.17 | 0.29 | 0.315 | NOR (0.45) |
| ALB | Williams | SAI | 15 | 0.80 | -0.06 | 0.87 | 0.002 | HAM (0.76) |
| SAI | Williams | ALB | 15 | 0.80 | 0.01 | 0.87 | 0.002 | LEC (0.76) |

## Caveats

- **Sampling.** The car data comes at about 4 Hz, roughly 20 m apart at 300 km/h, and brake is on/off. A single lap's braking point is only good to a sample; the medians and season means get below that, but differences of a metre or two are at the limit. Qualifying gives few laps per corner, so its medians are noisier than the races'.

- **Smoothed position feed.** Lateral offsets are about 0.5 m in every season, far less than real differences between lines, so the line through a corner isn't compared at all. Running wide shows up as a lift or a lower exit speed instead.

- **2026 energy management.** Drivers lift and coast to recharge, often on team instructions, so coasting and braking-point differences can be strategy, not style. Even in qualifying, where nothing is saved for later laps, coasting per corner was 26.7 m in 2026 against 18.3 m in 2025 (table): the cars harvest energy within the lap. Coasting is a clear difference for 45% of 2026 pairs. Read 2026 coasting differences as how the pair managed energy until radio or deployment data can separate the two.

| year | qualifying (m per corner) | races (m per corner) |
|---|---|---|
| 2022 | 22.8 | 30.5 |
| 2023 | 19.6 | 31.4 |
| 2024 | 20.4 | 30.2 |
| 2025 | 18.3 | 27.4 |
| 2026 | 26.7 | 32.5 |

- **Full-throttle point.** Laps not back on full throttle within the window count as later than it, so the metric is a median over partly censored laps; in 2026 the censored share at owned corners runs from 2% (HUL) to 14% (ALB) over driver-seasons with 5+ events. `pickup_censored` in style_corners.parquet has it per corner.

- **Race conditions.** Race laps are paired on session, corner and compound, but not on tyre age, fuel or strategy, and the free-air filter only knows the gap at the timing line. Qualifying laps are matched by attempt, not by run or tyre age. The session-kind breakdown in the tables separates qualifying from races.

- **Relative, not absolute.** Every profile is against one teammate: a driver's numbers change when the teammate does, and drivers of different teams can't be ranked from them. Chaining teammates across seasons (a teammate network) would be the way to absolute ratings.

- **Few events.** Substitutes and mid-season swaps have one to three events with a teammate: below 4 events there is no interval and nothing is called clear, and with four or five the intervals are wide (`events` and `pair_events` say how many).

## Outputs

Under `data/results/`: `style_corners.parquet` (each driver's medians per corner, session and compound, with corner type, `own_laps` and `pickup_censored`: what `compare_drivers` pairs), `style_profiles.parquet` (one row per pair, both ways round, session kind (all, quali, race), corner type (all, slow, medium, fast) and metric: `diff` = driver minus teammate, `lo`/`hi` 95% interval, `se`, `p`, `corners`, `events`, `same_sign`, `clear`), `style_events.parquet` (the same per event), `style_embeddings.parquet` (one row per driver, season and team: cosines, `style_consistency` and its `consistency_p`, `style_twin`, the 2-D projections `car_pc1/2` and `style_pc1/2` for charts, and the 64-dim `centroid` and `style` vectors), `style_embedding_pairs.parquet` (consistency per teammate pair) and `style_axes.parquet` (how to label the style map). `race_engineer.inference.style.compare_drivers(a, b, year)` compares any two drivers of a season and returns the table and a summary for a tool to relay.
