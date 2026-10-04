# M4: explaining the flagged corners

_Generated 2026-10-01 03:39 UTC by `race-engineer-infer explain` from `data/results/segment_scores.parquet`; one row per flag in `data/results/mistakes.parquet`._

Every one of the 18,762 flagged corners (the top 1% of each session by the combined detector score, see [m4_scoring.md](m4_scoring.md)) is compared with the same driver's usual way through that turn in that session: the median of their other clean laps there (in races the 10 nearest in lap number, because a corner gets ~0.2 s faster over a race as fuel burns off), or of the field's when they have fewer than 5 (7% of flags, nearly all in qualifying, where drivers do 3-4 push laps; car pace then counts towards the time lost). Time lost is the segment time (250 m before to 145 m after the apex) minus the reference's median, split into entry (to 50 m before the apex), apex (to 30 m after) and exit using elapsed time integrated from the speed trace and scaled to the timing clock. The lap context (a slow stretch of the lap, the field slow at the same place) and the placing of race-control incidents use the same reference for every corner of the session. Method details are in the module docstring of `inference/explain.py`.

## Mistake types and how the thresholds were set

A type is read off driver-relative robust z-scores (median and MAD of the reference laps, with the baselines' minimum spreads) of the hand-crafted features, counted only in the adverse direction. Each feature's threshold is the **99th percentile of its adverse z on ordinary corners** (clean laps that were not flagged, 24,450 sampled from 2022-2024), so evidence means a deviation bigger than on 99 of 100 ordinary laps. The strongest rule (z over threshold) is the type, the next one the secondary type. On later seasons the same thresholds fire on 0.6%-1.0% of ordinary 2025 corners and 0.6%-1.5% of ordinary 2026 ones (the offset rule aside, which almost never fires), so they hold up reasonably (2026's coasting and lifting are discussed below). These rates, like the thresholds, count only the corners where a feature is compared: a turn taken flat out has no braking point, pickup or lift, and one without braking no braking point.

| feature (adverse direction) | used for | threshold (z) | this run's calibration | fires on ordinary 2025 | ordinary 2026 | flags |
|---|---|---|---|---|---|---|
| v_start (lower) | `slow_approach` | 4.08 | 4.08 | 0.6% | 1.3% | 11% |
| braking point, see below (earlier) | `early_braking` | 5.00 | 5.00 | 0.9% | 1.0% | 3% |
| brake_len_m (higher) | supports `over_slowing` | 5.00 | 5.00 | 1.0% | 1.0% | 8% |
| decel_max (higher) | supports `over_slowing` | 6.94 | 6.94 | 1.0% | 1.2% | 5% |
| v_min (lower) | `over_slowing` | 5.30 | 5.30 | 0.7% | 0.9% | 21% |
| throttle_pickup_d (higher) | `late_throttle` | 9.00 | 9.00 | 0.9% | 0.6% | 7% |
| throttle_dip (higher) | `hesitation` | 3.83 | 3.83 | 1.0% | 1.5% | 9% |
| coast_m (higher) | `lift_and_coast` | 4.38 | 4.38 | 1.0% | 1.5% | 7% |
| v_exit (lower) | `late_throttle` | 3.81 | 3.81 | 0.8% | 1.1% | 32% |
| exit_accel (lower) | supports `late_throttle` | 3.23 | 3.23 | 1.0% | 1.1% | 25% |
| offset_exit_max (higher) | `late_throttle` | 2.00 | 2.00 | 0.1% | 0.0% | 1% |
| segment_time_s (higher) | slower than usual (95th percentile) | 2.44 | 2.44 | 4.5% | 4.9% | 38% |

**Slower than usual** is a calibrated bar too: the robust z of the segment time has to pass 2.44, its 95th percentile on ordinary corners, with the time spread floored at 0.05 s (the timing clock's own jitter, so that reference laps agreeing to the hundredth don't make a few hundredths significant). In seconds that bar is the flag's own: a median of 0.26 s over the flags (10th-90th percentile 0.13-0.53 s; median 0.26 s in races and 0.28 s in qualifying, where the reference is often the field). The 95th percentile rather than the 99th because the question is only whether the corner cost time beyond the lap-to-lap noise, and exit losses carry on past the window; the 99th (z 4.73) would need 1.9 times the loss. With a 90th-percentile bar (z 1.73), 45% of flags would count as slower instead of 38%.

Three features needed fixing before they could be calibrated at all. The hand-crafted braking point is the start of the *last* braking run before the slowest point, so a brief release of the (on/off, 4 Hz) brake moves it by 100 m or more; its adverse z passed 16 on 1% of ordinary laps. The rules use the first brake application after the driver last had full throttle instead. And the braking point, the throttle pickup and the throttle lift after it are all measured from the slowest point, which the hand-crafted features take anywhere in the 395 m window: on 53% of ordinary corners that is more than 60 m from the apex (44% at least 100 m), at the previous turn's exit or in the next one's braking zone, and measured from there a pickup 'later than usual' was the previous turn's exit and an 'earlier' braking point the next turn's. The rules take the turn's own slowest point, within 60 m of the apex. A turn usually taken flat out (24% of ordinary corners, 13% of flags) has none, so there the three aren't compared at all. Where turns come close together (Monaco, Jeddah T1-T3) the slowest point can still switch to the neighbouring turn on one lap: the normal blip of throttle between two turns then reads as a lift after the pickup. On such laps all three are measured from the usual slowest point instead, for the lap and its reference alike (7% of flags, against 2% of ordinary corners). Compared from a moved slowest point, the throttle lift's 99th percentile had been 7.0; measured like with like it is 3.8.

Three rules have exceptions. **Over-slowing** needs a braking zone (most reference laps brake for the turn after their last full throttle): in a corner usually taken without braking, a lower minimum speed comes from lifting. It is called a possible lock-up only when the braking was also longer or harder than usual. **Lift and coast** is the type only when coasting fires, nothing in the braking is adverse and the loss is no more than that much coasting costs: coasting always lowers the minimum and exit speeds and delays the throttle, and those effects are not typed as separate mistakes. The cost bar is 0.025 s per extra metre of coasting, the 99th percentile on 1,694 ordinary corners where drivers coasted more than usual with nothing adverse in the braking (up to 60 per session; this run's random sample gives 0.015 from 415 such corners). A bigger loss, or adverse braking, has another cause (a problem, traffic, a slow-down, braking early): coasting doesn't explain it, so the other rules type the flag (unclear when none fires) and it is listed. A **slow approach** (inherited from before the corner) comes after the rest, except when the car arrived slower than usual and lost most of the time on entry: then the loss was carried in, the slower speeds and later throttle after it follow from arriving slow, and the slow approach is the type (63 flags), not listed.

Before the driving rules, in this order: a **track-limits deletion or race-control incident** naming the driver alone at that turn, or else at a neighbouring turn whose apex lies inside its window (at a chicane race control names one turn, and the other's window covers the same moment; incidents are posted up to a lap late, so the posted lap or the one before, whichever deviated more; a scored lap beats an out-lap, in-lap or safety-car lap, which are slow for reasons of their own), a **data problem** (below), **traffic** (the next section: being lapped, racing another car, held up by a slower one, or in qualifying a lap that wasn't a full push lap), a **slow-down** (the two corners before and most after it were also slow, or the four before it: an abandoned lap; in the last corners of a lap, the two before it slow and the car arriving slower than usual; or in races, a third of the field slow at the same turn on the same lap: something on track), and a corner that was **not clearly slower** than usual. A race-control message naming two cars ('INCIDENT INVOLVING CARS 10 (GAS) AND 18 (STR) NOTED - FORCING ANOTHER DRIVER OFF THE TRACK') doesn't say whose mistake it was, so it is context in both drivers' sentences ('involved in an incident with ...'), not a type.

## What the flags turned out to be

| type | meaning | flags | share | median time lost (s) | qualifying | races | 2026 | listed by find_mistakes |
|---|---|---|---|---|---|---|---|---|
| `no_time_lost` | unusual but not clearly slower | 10,441 | 56% | 0.02 | 1,327 | 9,114 | 973 | no |
| `battle` | racing another car (passing, being passed or side by side) | 2,080 | 11% | 0.64 | 0 | 2,080 | 184 | no |
| `lapped` | being lapped (letting a faster car by under blue flags) | 1,359 | 7% | 0.91 | 0 | 1,359 | 262 | no |
| `data_problem` | data problem, not driving | 982 | 5% | -0.01 | 92 | 890 | 306 | no |
| `slowdown` | part of a slow-down (abandoned lap, slow stretch or something on track) | 859 | 5% | 1.66 | 36 | 823 | 59 | no |
| `impeded` | held up by a slower car ahead | 788 | 4% | 0.61 | 6 | 782 | 47 | no |
| `track_limits` | track limits / off track (race control) | 701 | 4% | 0.48 | 76 | 625 | 112 | yes |
| `late_throttle` | late throttle / wide exit | 513 | 3% | 0.44 | 127 | 386 | 62 | yes |
| `unclear` | unclear | 487 | 3% | 0.31 | 89 | 398 | 48 | yes |
| `over_slowing` | over-slowing | 284 | 2% | 0.58 | 54 | 230 | 34 | yes |
| `slow_approach` | slow approach (carried in from before the corner) | 78 | 0% | 0.59 | 18 | 60 | 6 | no |
| `lift_and_coast` | lift and coast (likely energy or fuel management) | 69 | 0% | 0.41 | 1 | 68 | 5 | no |
| `hesitation` | hesitation / traction loss | 57 | 0% | 0.32 | 16 | 41 | 7 | yes |
| `early_braking` | early braking | 38 | 0% | 0.51 | 6 | 32 | 3 | yes |
| `not_push_lap` | not a full push lap (already slow before this corner) | 16 | 0% | 0.50 | 16 | 0 | 3 | no |
| `lift_on_push_lap` | lift before braking on a push lap (nothing to save in qualifying) | 10 | 0% | 0.41 | 10 | 0 | 0 | yes |

- **56% of flags were not clearly slower than usual** (median +0.02 s; 13% of them more than 0.2 s slower, within a wide spread). The detectors find unusual corners, not costly ones: a later braking point to overtake, a tow, a drying track or a different line all look unusual. find_mistakes leaves them out; explain_corner still describes them, with the bar in seconds.

- **5% are part of a slow-down**: a slow stretch of the lap (abandoned qualifying laps, slow-downs in races), where every corner is slow and none is the mistake, or, in races, a turn where at least three other drivers and a third of the field were 0.5 s slower than usual on the same lap (something on track, or the conditions). The corners before a flag run on from the end of the previous lap when that was a racing lap too, so a slow-down that began there reaches the first turns of the next (27 flags). In races the last 3 turns of the lap a driver takes the chequered flag on are the run to the flag, where drivers ease off for the line, unless a car passed or was passed there (31 flags; on the chequered-flag laps of 2022-2025 those turns were 0.3 s slow 11% of the time, against 4-6% on the laps before). The corner where a slow stretch starts keeps its driving type, with a note that the lap may have been given up after it.

- **5% are data problems**, not driving (next section).

- **2,058 flags look like driving mistakes; they are 1,648 distinct mistakes.** 410 flags are a neighbouring turn of a listed one on the same lap whose window overlaps it (apexes less than 395 m apart): one moment seen twice, such as a spin that shows in three windows. find_mistakes lists each moment once, under the turn where it started (the one race control named, if any; else the first whose loss wasn't mostly on entry, since a later window re-counts the loss carried in from the one before), and names the others; their losses are never added up. A distinct mistake costs 0.37 s at the median (75th percentile 0.63 s, 90th 1.05 s), lost mostly on exit in 54%, on entry in 17% and through the apex in 18%. The exit share is a lower bound: a slow exit keeps costing time on the straight after the window.

- Late throttle / slow exit is the commonest driving type (513), then over-slowing (284); early braking is rare (38) because braking points are only accurate to one 4 Hz sample (15-20 m at speed), so the threshold is high. Running wide almost never shows in `offset_exit_max`: the position feed is smoothed (typical offsets 0.5 m), so a wide exit shows as a slower exit and a later throttle instead.

- Coasting fires on 1,407 flags. 69 are typed lift and coast (5 in 2026), labelled energy management (2026) or fuel and tyre saving (races), not a mistake; 10 on qualifying push laps before 2026, where there is nothing to save, are listed as a lift on a push lap. 120 others with coasting are listed as mistakes (29 in 2026): race control named the driver, they braked early, longer or harder too (43), or they lost more than coasting costs (40). The coasting rule fires on 1.5% of ordinary 2026 corners (0.8%-1.2% in each of 2022-2025; Fisher's exact test against 2022-2024 p = 0.0015), and the throttle-lift rule fires on 1.5% of ordinary 2026 corners where it is compared, not the turns taken flat out (0.9%-1.1% in each of 2022-2025; Fisher's exact test against 2022-2024 p = 0.009): the 2026 cars coast and lift more on ordinary laps (likely energy management), so some 2026 hesitation flags may be energy management too.

- Race control named the driver alone at a flagged turn 701 times (293 of them at a neighbouring turn inside the window, 0 for something other than leaving the track or track limits), and 121 flags carry a two-car message as context instead. In races, race control naming three or more drivers at one turn on one lap takes away the ranking bonus (27 flags), and when the field was slow there too (35 flags, most in the 2023 Monaco Grand Prix R (11) and the 2024 British Grand Prix R (5)) only the loss beyond the field's counts towards the ranking.

- 10% of flags are in qualifying sessions; qualifying flags are compared with the field more often than not.

## Traffic: the other cars

Without the other cars, every loss reads as driving, and in races the costliest flags are mostly traffic: backmarkers letting the leaders by lift and brake early, and a fight for a place compromises a corner. `inference/traffic.py` places every car on the track over the session (each lap's grid clock, laid end to end across the line; no position in the pit lane or the exit and entry lanes beside the track, where the feed failed or shifted the clock, or after a retirement; laps without a grid, most qualifying out-laps among them, placed from their sector times, and an out-lap ended where the next lap starts) and looks at an extended window around each flag, from 450 m before the apex (before the previous turn's apex for four turns in five, so before the loss the corner carries in) to 445 m after it (on the flagged corners of 2022-2025, 97% of the passes by a lapping car that was already close came by then). The gap to another car at a point is how much later it went past it.

**Only a car that was already close when the loss began makes the loss traffic.** The loss begins where the running time lost first reaches 0.1 s, and what counts is the closest each car came before that point (where a car has no position at the start of the window, leaving the pit lane or across a feed hole, from where it has one). Traffic comes after race control and data problems and before the slow-down check (letting two cars by is slow over several corners, and naming them says more), and only for a corner that was slower than usual. In races, in this order: **being lapped** (a car at least a lap ahead went past, after passing before the loss began or from within 1.75 s behind then; or the driver braked after the turn where the usual lap is on the power, costing time, with it within 1.6 s, and it went past from there on), **racing another car** (a car on the same lap, within 0.6 s at the start of the window, passed or was passed, or came within 0.1 s in the corner's own window; a car that went past only when it was alongside, within 0.35 s, as the loss began, past already, or slowed itself through the corner), and in any session **held up** (a car stayed ahead from the window start through the apex with the driver within 1.0 s of it, was slower through the corner than the driver usually is, and the driver dropped back no more than 0.2 s: it set their pace, or they had to get past it). In qualifying, a lap already clearly slow before the corner (3.2% slower than the driver's usual push lap where the window starts) is **not a full push lap**: a preparation lap, a lap given up or a problem; the losses are on the corners before, so a lap given up after a mistake keeps the mistake.

When a car that was further back went past, the driver lost the time some other way and the car took its chance: the flag keeps its type and the sentence says 'lost the place to X' with the gap and where it was measured. Following a car within 1 s at the start of the corner, passing a car that wasn't close and the first racing lap after a safety car or VSC are context, not types. A race-control deletion or incident keeps its type whatever the traffic (it is the driver's own); when a car that explains the loss was already at the driver as it began (alongside, past, or lapping them from within 0.5 s), the flag ranks on the race-control bonus alone, and otherwise on the full loss plus the bonus, with the cars as context. A driving flag at a neighbouring turn of the same lap whose window starts inside a traffic flag's extended window lost its time with those cars already close: it isn't listed either (the same moment as the traffic), while one whose window starts earlier keeps its type (its own check measured the gaps from further back).

The settings, each from the races (or qualifying) of 2022-2025 over every corner the detectors score, never 2026; 'ordinary' corners are clean laps that were not flagged. `race-engineer-infer explain --calibrate-traffic` prints this evidence again from the data.

| setting | value | from 2022-2025 (never 2026) |
|---|---|---|
| where the loss began | 0.1 s lost | the first point where the running time lost reaches it: twice the timing clock's point-to-point jitter |
| being lapped: gap of the lapping car when the loss began | 1.75 s | 95th percentile of that gap on ordinary and flagged race corners where a lapping car went past after the loss began (1.75 s; 2,785 passes, median 0.63 s; 1,250 more went past before it began). Ordinary corners alone (1.43 s; 1,432 passes, median 0.51 s) leave out the costly yields, which are flagged (1.95 s over those). The time lost when one goes past stays at what letting it by costs, 0.83-0.99 s, up to 1.25 s back, then 1.39 s at 1.25-1.5 s and 1.9-2.1 s from 1.5 s to 3 s back. 450 m before the apex, the old bar (1.73 s, the ordinary corners' 95th percentile there) left out yields from a car still 2 s back there |
| being lapped: braking where the usual lap is on the power | 1.60 s | 95th percentile of the lapping car's gap where the driver braked, on ordinary corners where it went past from there on (1.65 s; 40 corners, median 0.48 s). Braking after the turn's slowest point where the usual lap is on the power, costing time, comes with 0.2% of ordinary corners without a lapping car going past and 2.2% with one |
| racing: gap of a car on the same lap at the start | 0.60 s | 95th percentile over 8,036 same-lap passes (0.58 s; median 0.18 s). Over every scored corner the median time lost when one passes is 0.35-0.42 s from up to 0.75 s back, then 0.72, 1.13 and 1.71 s for each quarter second further |
| racing: a car that went past was alongside when the loss began | 0.35 s | 95th percentile of that gap on ordinary corners where a same-lap car within the battle gap went past after the loss began (0.35 s; 1,330 passes, median 0.10 s; 0.36 s with the flagged corners too). The time lost is 0.48-0.50 s up to 0.3 s apart, then 0.64, 0.79 and 0.86 s for each tenth further. Or it passed before, or was slowed itself (by the corner's bar) |
| race control's flag ranked on the bonus alone: a lapping car when the loss began | 0.50 s | the median gap from which drivers began to yield on ordinary corners (0.51 s): with race control naming the driver, only a lapping car that close explains the loss; a same-lap car has to be alongside (the battle bar above), a car ahead holding them up within the held-up gap, or any car past already |
| racing: side by side | 0.10 s | about a car length at 200 km/h; a same-lap car that close in the window costs 0.13 s at the median (20,000 ordinary corners), against 0.03 s at 0.3-0.5 s |
| held up: gap behind the car ahead | 1.00 s | behind a car that stayed ahead through the apex and was 0.3 s slower than the driver's usual, an ordinary race corner costs 0.37 s within 0.25 s of it, 0.18 s at 0.75-1 s, 0.15 s at 1-1.25 s and 0.08 s at 3-4 s: up to about 1 s the car ahead at least doubles what such a corner costs far behind it |
| held up: most the driver drops back | 0.22 s | 95th percentile (0.222 s) on those corners within 1 s (38,077) |
| held up: the car ahead slower than usual by | the corner's own bar | as for 'slower than usual' (a median of 0.26 s over the flags) |
| not a full push lap (qualifying) | 3.2% of the lap so far | 99th percentile on ordinary push-lap corners at least 15 s into the lap (3.23%, 103,237 corners; in the first 15 s it is 8%, so the rule starts there) |
| the run to the flag | the last 3 turns | of the lap a driver takes the chequered flag on, 0.3 s slower than usual 10.8%, 11.4% and 10.9% of the time, against 3.8-6.2% on the two laps before; from the fourth turn from the end 9.0%, 7.8%, 7.7% |
| following closely (context) | 1.00 s | as the held-up gap |

**4,243 flags are traffic** (23% of all flags, 61% of the race flags that were slower than usual and not a data problem), 2,689 of them listed as driving mistakes before. 23 traffic flags rely on a car placed from its sector times on a clean racing lap (0.07 s out at the median, see `inference/traffic.py`); their sentences say so. On a pit, slow or neutralised lap such a placement is too rough for the rules: checked against the raw position feed, 4 of 5 cars that the rules had holding a driver up from such positions were 1.2-6.7 s from where their sector times put them. So those cars don't count as traffic: 140 flags (31 in qualifying, where cars on out-laps have no lap grid) name one that would have, as possibly in the way, and keep their own type. Of the 280 race-control flags with traffic around, 259 had a car already at the driver as the loss began and rank on the bonus alone; 21 rank on the full loss.

|  | 2022 | 2023 | 2024 | 2025 | 2026 | all | listed before |
|---|---|---|---|---|---|---|---|
| `lapped`: being lapped (letting a faster car by under blue flags) | 244 | 194 | 433 | 226 | 262 | 1,359 | 834 |
| `battle`: racing another car (passing, being passed or side by side) | 456 | 560 | 474 | 406 | 184 | 2,080 | 1,454 |
| `impeded`: held up by a slower car ahead | 210 | 199 | 165 | 167 | 47 | 788 | 394 |
| `not_push_lap`: not a full push lap (already slow before this corner) | 4 | 3 | 5 | 1 | 3 | 16 | 7 |
| driving flag covered by traffic at a neighbouring turn | 7 | 7 | 10 | 8 | 0 | 32 | 32 |
| race control's type, a car already at the driver as the loss began (listed, ranked on the bonus alone) | 16 | 45 | 108 | 61 | 29 | 259 | 259 |
| race control's type, traffic around but not at the driver yet (listed, ranked on the full loss) | 2 | 5 | 3 | 7 | 4 | 21 | 21 |

**Each race's top three findings** in find_mistakes, before the traffic rules (read with what the flags are now) and after. After, the traffic left in the top three is race control's flags ranked on the bonus alone (in races with few other listed mistakes).

| races and sprints | sessions | top-3 findings before | traffic before | sessions with traffic in their top 3, before | top-3 findings after | traffic after | sessions with traffic in their top 3, after |
|---|---|---|---|---|---|---|---|
| 2022-2025 | 112 | 333 | 225 (68%) | 104 | 321 | 30 (9%) | 25 |
| 2026 | 18 | 52 | 34 (65%) | 15 | 51 | 4 (8%) | 4 |
| all | 130 | 385 | 259 (67%) | 119 | 372 | 34 (9%) | 29 |

**Cases from the review of the traffic rules** (gaps measured 450 m before the apex had made yields look like mistakes and the reverse), and what the explanations say now:

| flag | the review found | type before the traffic rules | type now | listed now |
|---|---|---|---|---|
| 2026 Dutch Grand Prix R, PER lap 59 T10 | blue-flag yield: LEC (2 laps ahead) 1.8 s back 450 m before the apex, 0.8-1.0 s when PER braked early; LEC went past on the way in | `over_slowing` | `lapped` (LEC) | no |
| 2023 Hungarian Grand Prix R, SAR lap 51 T1A | blue-flag yield: VER (a lap ahead) leaving the pits, no position 450 m before the apex, then 0.7-1.1 s back; SAR braked on the straight | `late_throttle` | `lapped` (VER) | no |
| 2022 French Grand Prix R, ZHO lap 41 T10 | yield: PER (a lap ahead) across a feed hole 450 m before the apex, past 390 m before it; RUS 1.9 s back went past too | `late_throttle` | `lapped` (PER, RUS) | no |
| 2023 Japanese Grand Prix R, MAG lap 48 T16 | a real mistake (a possible lock-up): NOR (a lap ahead) still 2.0 s back when the loss began, past only after it | `late_throttle` | `late_throttle` | yes (priority 3.20) |
| 2026 Spanish Grand Prix R, BOT lap 48 T5 | yield: braking on the straight after the turn with ANT (3 laps ahead) closing, about 2.0 s back when the loss began | `late_throttle` | `lapped` (ANT) | no |
| 2026 Belgian Grand Prix R, ALO lap 29 T1 | yield or a slow stretch: HAM (a lap ahead) about 2.0 s back when the loss began; ALO lifted and braked on the exit | `slowdown` | `slowdown` | no |
| 2022 Australian Grand Prix R, GAS lap 53 T13 | a real mistake: BOT (same lap) 0.6 s back when GAS's loss began, not slowed, passed because of it | `late_throttle` | `late_throttle` | yes (priority 2.31) |
| 2026 Dutch Grand Prix R, TSU lap 43 T13 | a real mistake (track limits): NOR (a lap ahead) 1.3 s back when the loss began, past only after it; had ranked on the bonus alone | `track_limits` | `track_limits` (NOR) | yes (priority 4.07) |
| 2026 Canadian Grand Prix S, HAD lap 6 T2 | a slow-down: laps 5 and 6 20 s and 16 s slow before a pit stop | `slowdown` | `slowdown` | no |
| 2026 Japanese Grand Prix R, ANT lap 53 T18 | the run to the flag: leading on the final lap, off the throttle and on the brake on the straight to the line | `slowdown` | `slowdown` | no |

Examples (a random flag of each type losing 0.3-2 s, 2026 where there is one):

- `lapped`: 2026 Barcelona Grand Prix R, PER: Lap 42, turn 6: 1.24 s lost, spread through the corner; being lapped: NOR (2 laps ahead), 0.7 s behind 190 m before the apex, went past through the corner: letting them through (blue flags), not a mistake; back on full throttle 60 m later than usual; following ANT closely (0.5 s behind at the start of the corner).
- `battle`: 2026 Miami Grand Prix R, LEC: Lap 56, turn 13: 0.43 s lost, mostly on entry; racing PIA: side by side with PIA (0.0 s apart at the closest, swapping places and back), who was 0.0 s behind 450 m before the apex: the time lost is the fight for the place, not a mistake.
- `impeded`: 2026 Belgian Grand Prix R, BOT: Lap 5, turn 18: 0.59 s lost, spread through the corner; held up by OCO (on the same lap): 0.3 s behind it at the closest on the way in, and it took 10.5 s through the corner against this driver's usual 10.1 s: it set their pace, so not a mistake; braked 80 m earlier than usual, 128 m more braking, 8 km/h slower at the slowest point; confirmed by the timing clock, but the speed channel is stuck for 30 m; following PER closely (0.4 s behind at the start of the corner); the first racing lap after a safety car.
- `not_push_lap`: 2026 Azerbaijan Grand Prix Q, LEC: Lap 8, turn 20: 0.84 s lost, mostly on entry; not a full push lap: already 3.2 s (4%) slower than their usual push lap by the start of the corner (a preparation lap, a lap given up or a problem), so not a mistake here; 47 km/h slower on the approach and 42 km/h slower at the exit; compared with the field, as they had only 4 other clean laps here.

### Development check against the pre-read (not an evaluation)

Before any human review, Claude read the top 50 flags of 2026 (`report/m4_review_preread_v1.csv`: a verdict and the reason per flag). Those readings motivated these rules, so this is a development check, not an evaluation, and no setting was tuned to it. Of the 34 judged not a driver mistake, 25 are now left out (17 `lapped`, 5 `battle`, 2 `slowdown`, 1 `not_push_lap`; 7 were left out before the traffic rules too). Of the 13 judged real, 13 are still listed (1 under the turn before, where the loss began).

Still listed: 2 race-control flags, which keep their type with the traffic in the sentence and rank on the bonus alone (#2, #6). The real mistake listed under the turn before (#3, `lapped` (NOR); its loss runs on from turn 13, listed as `track_limits` (priority 4.07)): the flag itself is traffic, but its loss carries on from there. 7 where the data shows no traffic (#16, lap 1.0% slower than usual before the corner (a full push lap below 3.2%); #27, no car close enough for a traffic type; #31, no car close enough for a traffic type; #40, lap 1.8% slower than usual before the corner (a full push lap below 3.2%); #43, no car close enough for a traffic type; #46, no car close enough for a traffic type; #48, no other push lap to compare the lap with): what the pre-read saw there (a problem or deliberate slowing with no car near, a car problem on the only timed lap, a stuck speed channel the feed checks take for a channel problem under a real loss, preparation or abandoned laps less slow before the corner than 1 ordinary push lap in 100) the positions can't show.

| # | flag | pre-read | type before | type now | listed now | why |
|---|---|---|---|---|---|---|
| 1 | R SAI lap 47 T13 | not | `slowdown` | `slowdown` | no | left out as before (`slowdown`) |
| 2 | R BOT lap 47 T2 | not | `track_limits` | `track_limits` | yes | race control's type; `lapped` (SAI): ranked on the bonus alone |
| 3 | R TSU lap 43 T14 | real | `slow_approach` | `lapped` | as turn 13 | `lapped` (NOR); its loss runs on from turn 13, listed as `track_limits` (priority 4.07) |
| 4 | S PER lap 7 T9 | not | `late_throttle` | `lapped` | no | `lapped` (RUS, PIA) |
| 5 | R BOT lap 46 T3 | not | `over_slowing` | `lapped` | no | `lapped` (LEC) |
| 6 | R ANT lap 49 T2 | not | `track_limits` | `track_limits` | yes | race control's type; `battle` (RUS): ranked on the bonus alone |
| 7 | S GAS lap 22 T5 | not | `slowdown` | `lapped` | no | `lapped` (PER, LAW) |
| 8 | R BOT lap 47 T12 | not | `late_throttle` | `lapped` | no | `lapped` (PIA) |
| 9 | S OCO lap 19 T9 | real | `track_limits` | `track_limits` | yes | no car close enough for a traffic type |
| 10 | R BOT lap 37 T5 | not | `late_throttle` | `lapped` | no | `lapped` (VER) |
| 11 | R STR lap 64 T13 | not | `late_throttle` | `lapped` | no | `lapped` (HUL) |
| 12 | R ALO lap 28 T14 | not | `over_slowing` | `lapped` | no | `lapped` (VER, NOR) |
| 13 | R STR lap 35 T13 | not | `over_slowing` | `lapped` | no | `lapped` (ANT, NOR) |
| 14 | Q LAW lap 2 T17 | unsure | `slow_approach` | `slow_approach` | as turn 16 | left out as before (`slow_approach`) |
| 15 | R BOT lap 45 T11 | not | `over_slowing` | `lapped` | no | `lapped` (ANT, NOR) |
| 16 | Q LEC lap 26 T18 | not | `unclear` | `unclear` | yes | lap 1.0% slower than usual before the corner (a full push lap below 3.2%) |
| 17 | S PER lap 12 T15 | not | `slow_approach` | `lapped` | no | `lapped` (ANT, HAM) |
| 18 | R ALO lap 36 T7 | not | `early_braking` | `lapped` | no | `lapped` (LAW, PIA) |
| 19 | R LIN lap 29 T13 | not | `late_throttle` | `lapped` | no | `lapped` (RUS) |
| 20 | R PER lap 62 T13 | not | `over_slowing` | `lapped` | no | `lapped` (PIA, GAS) |
| 21 | Q HUL lap 18 T14 | real | `track_limits` | `track_limits` | yes | lap no slower than usual before the corner (a full push lap below 3.2%) |
| 22 | Q PIA lap 10 T14 | real | `track_limits` | `track_limits` | yes | lap 0.6% slower than usual before the corner (a full push lap below 3.2%) |
| 23 | R BEA lap 56 T1 | not | `over_slowing` | `lapped` | no | `lapped` (HAD) |
| 24 | R BOR lap 39 T4 | not | `slow_approach` | `battle` | no | `battle` (PER, LAW) |
| 25 | R ALB lap 31 T10 | not | `slow_approach` | `lapped` | no | `lapped` (HAD) |
| 26 | R PER lap 31 T11 | not | `slow_approach` | `lapped` | no | `lapped` (LIN, BEA) |
| 27 | S SAI lap 12 T1 | not | `late_throttle` | `late_throttle` | yes | no car close enough for a traffic type |
| 28 | Q COL lap 2 T7 | real | `track_limits` | `track_limits` | yes | lap no slower than usual before the corner (a full push lap below 3.2%) |
| 29 | R STR lap 42 T16 | not | `early_braking` | `lapped` | no | `lapped` (HAM, PIA) |
| 30 | Q SAI lap 2 T3 | real | `track_limits` | `track_limits` | yes | 7 s into the lap, before the full-push-lap check starts (15 s) |
| 31 | S HAM lap 8 T11 | not | `track_limits` | `track_limits` | yes | no car close enough for a traffic type |
| 32 | SQ HAM lap 10 T14 | real | `track_limits` | `track_limits` | yes | lap 2.9% slower than usual before the corner (a full push lap below 3.2%) |
| 33 | SQ SAI lap 9 T14 | real | `track_limits` | `track_limits` | yes | lap 1.4% slower than usual before the corner (a full push lap below 3.2%) |
| 34 | Q LIN lap 5 T6 | real | `over_slowing` | `over_slowing` | yes | lap 1.1% slower than usual before the corner (a full push lap below 3.2%) |
| 35 | S RUS lap 8 T17 | not | `over_slowing` | `battle` | no | `battle` (VER) |
| 36 | Q VER lap 16 T10 | not | `late_throttle` | `not_push_lap` | no | `not_push_lap`: 1.4 s down before the corner |
| 37 | R COL lap 14 T9 | unsure | `over_slowing` | `over_slowing` | yes | SAI placed only roughly (sector times on a pit or slow lap) |
| 38 | R ANT lap 22 T3 | real | `track_limits` | `track_limits` | yes | no car close enough for a traffic type |
| 39 | R LAW lap 39 T4 | not | `over_slowing` | `battle` | no | `battle` (BOR) |
| 40 | SQ GAS lap 5 T15 | not | `hesitation` | `hesitation` | yes | lap 1.8% slower than usual before the corner (a full push lap below 3.2%) |
| 41 | Q ALB lap 3 T16 | real | `track_limits` | `track_limits` | yes | lap 0.9% slower than usual before the corner (a full push lap below 3.2%) |
| 42 | Q LEC lap 8 T18 | not | `slowdown` | `slowdown` | no | left out as before (`slowdown`) |
| 43 | R ALB lap 6 T12A | not | `unclear` | `unclear` | yes | no car close enough for a traffic type |
| 44 | R HAM lap 51 T16 | not | `over_slowing` | `battle` | no | `battle` (NOR) |
| 45 | R GAS lap 30 T7 | not | `unclear` | `battle` | no | `battle` (LAW) |
| 46 | S VER lap 8 T11 | not | `track_limits` | `track_limits` | yes | no car close enough for a traffic type |
| 47 | Q HUL lap 17 T14 | unsure | `late_throttle` | `late_throttle` | yes | lap 0.3% slower than usual before the corner (a full push lap below 3.2%) |
| 48 | Q BOR lap 2 T6 | not | `unclear` | `unclear` | yes | no other push lap to compare the lap with |
| 49 | R LIN lap 41 T11 | real | `over_slowing` | `over_slowing` | yes | no car close enough for a traffic type |
| 50 | Q RUS lap 10 T2 | real | `over_slowing` | `over_slowing` | yes | 8 s into the lap, before the full-push-lap check starts (15 s) |

## Data problems: flags about the feed, not the driving

Checking the top flags against their traces by eye showed that some are feed problems. Each became an automatic check (`data_issues`):

| issue | data-problem flags |
|---|---|
| the timing clock disagrees with the speed trace | 93 |
| the speed trace doesn't match the distance covered | 63 |
| speed channel stuck | 616 |
| speed trace jumps or spikes | 135 |
| brake signal on at full throttle | 225 |

The feed got worse in 2026: data problems are 3% of 2022 flags, 4% of 2023 flags, 4% of 2024 flags, 5% of 2025 flags, 14% of 2026 flags. Looking at 2026 examples by eye, they are real glitches: one-point speed spikes to 200 km/h in a 100 km/h corner (at the same turn for several drivers) and speed frozen through a braking zone.

A problem with one channel (speed, brake) doesn't hide a loss of 0.3 s or more that the timing clock confirms: the flag keeps its driving type and the sentence mentions the problem. Nor does any check override race control naming the driver at that turn, because leaving the track (cutting a chicane) or contact itself breaks the link between speed and distance along the reference line that the timing checks rely on: the misalignment check can't tell a cut chicane from misaligned position data, and some of its flags are probably real cuts or offs that race control didn't name.

Flags checked by eye while building this, and what the explanations say now:

| flag | what the trace shows | type now | time lost (s) |
|---|---|---|---|
| 2026 Canadian Grand Prix SQ, HAM lap 10 T1 | data: the clock jumps 7.5 s at the window start; speed, pedals and gear match the usual lap | `data_problem` | 7.81 |
| 2026 British Grand Prix R, OCO lap 30 T16 | data: speed frozen at 240 km/h from 130 m before the apex to 60 m after it, through the braking zone | `data_problem` | -1.12 |
| 2025 Abu Dhabi Grand Prix R, ANT lap 24 T6 | data: speed frozen at 200 km/h through the braking zone | `data_problem` | -0.34 |
| 2025 Belgian Grand Prix S, VER lap 8 T18 | data: a one-point speed spike to 310 km/h in a 90 km/h chicane | `data_problem` | -0.20 |
| 2024 Bahrain Grand Prix R, HUL lap 32 T10 | data: the brake signal stays on through the exit, 75 m of it at full throttle; the speed trace is normal | `data_problem` | 0.11 |
| 2025 Canadian Grand Prix R, BOR lap 52 T10 | data: the whole trace is shifted about 40 m along the track | `data_problem` | 0.16 |
| 2023 Italian Grand Prix R, STR lap 43 T2 | driving: braked later and carried 40 km/h more into the chicane, braked again after the apex and crawled out (a lock-up or a missed chicane) | `late_throttle` | 2.82 |
| 2025 Canadian Grand Prix Q, COL lap 2 T9 | driving: braked early, lifted before the apex and was 35 km/h slow through it, then late on the throttle | `over_slowing` | 1.34 |
| 2025 Miami Grand Prix S, HAM lap 13 T12 | not a mistake: much faster than the 10 nearest laps everywhere, as on a drying track | `no_time_lost` | -1.91 |
| 2023 Italian Grand Prix R, NOR lap 49 T1 | not a mistake: braked 60 m later with 8 km/h more from a tow, then a slow exit (probably an overtaking move) | `no_time_lost` | -1.04 |
| 2024 Monaco Grand Prix Q, GAS lap 9 T19 | not a mistake here: 20-40 km/h slower everywhere, like the rest of the lap | `slowdown` | 1.94 |

## Examples

A typical flag of each type: a random one (fixed seed) on a clean lap, compared with the driver's own laps and (for the types find_mistakes lists) losing 0.15-1.5 s with nothing else going on, one per session. The most unusual or costly flags are mostly slow-downs and oddities: the most unusual late-throttle flag in qualifying, for example, is a driver braking hard after the last corner of a push lap, most likely for a flag the telemetry can't show.

- `late_throttle`: 2022 Canadian Grand Prix R, LAT: Lap 60, turn 11: 0.29 s lost, mostly on exit; 18 km/h slower at the exit, lifted 37% of throttle after getting back on the power, 2.2 m/s² less acceleration out of the corner.
- `over_slowing`: 2022 Spanish Grand Prix R, STR: Lap 43, turn 14: 0.96 s lost, mostly on exit; 16 km/h slower at the slowest point, 1.9 m/s² less acceleration out of the corner, 21 km/h slower at the exit.
- `early_braking`: 2025 Canadian Grand Prix R, ANT: Lap 37, turn 10: 0.43 s lost, mostly on entry; braked 40 m earlier than usual; confirmed by the timing clock, but the speed trace jumps by 30 km/h.
- `hesitation`: 2023 Singapore Grand Prix Q, NOR: Lap 8, turn 18: 0.17 s lost, spread through the corner; lifted 80% of throttle after getting back on the power, 42 km/h slower at the exit, 4.3 m/s² less acceleration out of the corner.
- `track_limits`: 2023 Abu Dhabi Grand Prix R, HUL: Lap 11, turn 1: 0.15 s slower than usual, but within the spread of their laps here (a loss stands out from 0.33 s); lap time deleted for track limits at turn 1; 21 km/h slower at the exit, back on full throttle 40 m later than usual, 22 m less coasting (off both pedals).
- `unclear`: 2023 Belgian Grand Prix R, HUL: Lap 30, turn 1: 0.38 s lost, mostly on exit; 7 km/h slower at the slowest point.
- `lift_on_push_lap`: 2024 Miami Grand Prix Q, HAM: Lap 17, turn 14: 0.44 s lost, mostly on exit; 30 m more coasting (off both pedals); a lift before braking, unusual on a push lap: there is nothing to save in qualifying.
- `lift_and_coast`: 2026 Italian Grand Prix R, ANT: Lap 23, turn 4: 0.33 s lost, mostly on entry; 52 m more coasting (off both pedals); likely energy management (the 2026 cars harvest by lifting), not a mistake; following RUS closely (0.1 s behind at the start of the corner).
- `slow_approach`: 2024 Chinese Grand Prix S, VER: Lap 2, turn 14: 0.28 s lost, mostly on entry; 18 km/h slower on the approach; carried in from before the corner (the previous exit, traffic or a tow).
- `slowdown`: 2024 British Grand Prix R, ZHO: Lap 18, turn 2: 1.49 s lost, mostly on entry; 100 m more coasting (off both pedals), 62 km/h slower at the slowest point, 37 km/h slower at the exit; part of a slow stretch of the lap: the corners before it were slow too (from the end of the previous lap on) (an abandoned lap, a slow-down or a problem), so probably not a mistake here.
- `no_time_lost`: 2024 Belgian Grand Prix R, PIA: Lap 8, turn 12: 0.01 s slower than usual, but within the spread of their laps here (a loss stands out from 0.34 s); 0.7 m closer to the usual line on exit, 20 m more braking, 12% less throttle lift after the apex.
- `data_problem`: 2023 Spanish Grand Prix R, GAS: Lap 41, turn 8: probably a data problem, not driving: the speed channel is stuck for 20 m.

## find_mistakes ranking

find_mistakes ranks the distinct mistakes by time lost, discounted by up to half when the detectors found the corner only borderline unusual (the flag's percentile within the session's top 1%), plus 0.5 s when race control named the driver alone at that turn (a deleted lap or a penalty costs more than the corner itself, and it is the surest sign of a mistake). When the field was slow at that turn on that lap too, only the loss beyond the other drivers' median counts, and when traffic explains the loss of a flag race control named (a car of its own traffic, or of the traffic at a neighbouring turn covering it, was already at the driver when the loss began: alongside, past, or lapping them from within 0.5 s), none of it does: it ranks on the bonus alone. A car that was still further back then didn't cause the loss, so the flag ranks on the full loss plus the bonus. find_mistakes' ranking line says so. Time lost is what a mistake costs; the discount keeps a slow corner the detectors barely noticed below an equally slow one they are sure about.

## Known incidents

The M4 check: do the tools surface documented incidents with a plausible type? The documented cases in the data are race-control ones: the deleted lap of COL in Melbourne, Russell's cuts of the Monaco chicane (lap 48, which race control noted, and lap 47, also deleted there), a 2026 race-control incident, and one chosen because it is famous and expected to be hard (an incident at the start). The dataset has no race-control messages about spins.

**This check is partly circular**: a track-limits deletion or a single-car incident at the flagged turn becomes the type automatically once the corner is flagged, so a `track_limits` type there tests the flagging and the events join, not the driving rules. The last column runs the driving rules alone (no race control), which is the real test of the explanations; a cut chicane gains time, so 'not clearly slower' is the fair answer there.

| case | record | find_mistakes | explain_corner | plausible type | driving rules alone |
|---|---|---|---|---|---|
| 2026 Australian Grand Prix Qualifying, COL lap 2 T7 | Lap time deleted: 'TRACK LIMITS AT TURN 7' (FastF1's deleted-lap reason). | listed #1 of 1 for COL (with turn 6, 8: overlapping windows) | `track_limits` / `late_throttle`: Lap 2, turn 7: 1.00 s lost, spread through the corner; lap time deleted for track limits at turn 7; 38 km/h slower at the exit; compared with the field, as they had only 4 other clean laps here; 5 of the 7 corners after it were slow too: the lap may have been given up after this. | yes | `late_throttle` (fits) |
| 2025 Monaco Grand Prix Race, RUS lap 48 T10 | Race control, posted during lap 49: 'TURN 10 INCIDENT INVOLVING CAR 63 (RUS) NOTED - LEAVING THE TRACK AND GAINING AN ADVANTAGE'; lap 48's time deleted for track limits at turn 10 (cutting the chicane). | listed #1 of 1 for RUS | `track_limits` / `late_throttle`: Lap 48, turn 10: not slower than usual (0.73 s quicker); lap time deleted for track limits at turn 10; racing ALB: passed ALB through the corner, from 0.6 s behind 450 m before the apex; 24 km/h faster at the slowest point, not back on full throttle by 145 m after the apex, 32 km/h slower at the exit. | yes | `no_time_lost` / `late_throttle` (fits) |
| 2025 Monaco Grand Prix Race, RUS lap 47 T10 | Lap 47's time was deleted for track limits at turn 10 too, the lap before the one race control noted. | not flagged (percentile 95.5), not listed | `track_limits`: Lap 47, turn 10: 0.03 s slower than usual, but within the spread of their laps here (a loss stands out from 0.50 s); lap time deleted for track limits at turn 10; back on full throttle 35 m later than usual, braked 28 m earlier than usual, 9 km/h slower at the exit; following ALB closely (0.5 s behind at the start of the corner); 5 of the 9 corners after it were slow (damage, a problem or traffic?). | yes, but only when asked: the detectors didn't flag it | `no_time_lost` (fits) |
| 2026 Canadian Grand Prix Sprint, HAM lap 22 T13 | Race control, posted during lap 23: 'TURN 13 INCIDENT INVOLVING CAR 44 (HAM) NOTED - LEAVING THE TRACK AND GAINING AN ADVANTAGE' (the final chicane). | listed #1 of 1 for HAM (with turn 14: overlapping windows) | `track_limits` / `over_slowing`: Lap 22, turn 13: 1.34 s lost, mostly on exit; race control noted an incident at turn 13 (leaving the track and gaining an advantage); racing PIA: PIA, 0.2 s behind 450 m before the apex, went past on the way in, before the loss began, so the time lost doesn't count towards its ranking; 45 km/h slower at the slowest point, 40 km/h slower at the exit, 2.5 m/s² less acceleration out of the corner; lost the place to LEC (1.3 s behind 400 m before the apex). | yes | `over_slowing` / `late_throttle` (fits) |
| 2024 Hungarian Grand Prix R, VER lap 1 T1 | Race control, posted during lap 2: 'TURN 1 INCIDENT INVOLVING CAR 1 (VER) NOTED - LEAVING THE TRACK AND GAINING AN ADVANTAGE', i.e. at the start. | not listed (no data for the corner) | error: VER has no usable telemetry for lap 1 at turn 1 in the 2024 Hungarian Grand Prix Race (missing or rejected data, or the lap wasn't driven). | no (missed) | n/a |

find_mistakes lists 3 of the 5 cases, each as one mistake. explain_corner gives a plausible type for 4 of the 4 cases with data, but with race control that is the join at work; the driving rules alone fit 4 of them. Not listed: 2025 Monaco Grand Prix Race, RUS lap 47 T10: not flagged (percentile 95.5), not listed; 2024 Hungarian Grand Prix R, VER lap 1 T1: not listed (no data for the corner).

**Systematically**, race control noted 43 single-car 'leaving the track' incidents at a named turn. Only 5 were flagged at that turn on the posted lap or the one before (5 of them explained with the race-control message). 7 happened at the start, which isn't scored (the opening lap is excluded, and the first turns often have no window at all because it would start before the line). Of the other misses with a scored corner (26), the median was at percentile 89 of its session: unusual, but not in the top 1%. Leaving the track 'and gaining an advantage' is usually a cut that saves time, and with a smoothed position feed a cut looks much like a normal corner; the detectors look for corners that are unusual in shape, and a small cut isn't.

## Limits

- Types are rules on hand-crafted features, calibrated but not validated against labels (only track limits are labelled). They describe *how* a corner differed, not *why*: a tow, a car problem or a strategy look the same in the data. Traffic is read from where the cars were (next bullet).

- Traffic comes from positions on the reference line, a few metres apart at best: 'side by side' can't tell alongside from nose to tail, a car that pits has no position in the pit lane, and cars on qualifying out-laps (no lap grid) are placed only roughly from their sector times, seconds out at worst, so they never count as traffic: a driver really held up by one keeps a driving type, with the car named as possibly in the way. A mistake made while a car was already close counts as traffic when that car then passes: the rules can't tell the two apart. A car slowing for the pit entry is still on the track line until it turns in, so a driver passing it there reads as a pass. A stretch of a lap the feed shifted is dropped only when a later feed hole shifts it back; a shift that nothing undoes stays, as it could as well be before the hole. A yield that is only a lift on the power, with the lapping car further back than the lapping gap when the loss began, stays a driving type (a lift looks the same as a traction loss).

- In qualifying the reference is usually the field, so a slower car's corners look like time lost and a faster car's mistakes look smaller.

- Time lost stops at 145 m after the apex, so exit mistakes are undercounted.

- The data-problem checks were set by eye on a few dozen flags; they will miss short glitches and can take a real cut or off for misaligned data.

- Which lap a race-control incident belongs to is a guess when it was posted a lap late (the lap that deviated more at that turn).

- The braking point is measured from the driver's last full throttle, so on a lap that never got back to full throttle between two turns it is the previous turn's: 53 flags say they braked 150 m or more earlier than usual, most of them for that reason. Measuring from any throttle instead split the usual laps' braking zones at brief blips of throttle and did worse.

- Where race control named a neighbouring turn inside the window, the flag gets the race-control type too (293 flags): at a chicane that is the same moment, but a deletion at a turn near the window's edge may be about a different one.
