# Data quality report

_Generated 2026-09-30 09:19 UTC by `race-engineer-data qa`._

## Coverage

263 sessions, 168,709 laps, 144,373 lap grids, 2,314,096 corner segments. 2,907 corner windows skipped (the neighbouring lap had no grid) and 40,698 dropped for touching invalid data (below).

| year | session | sessions | laps | grids | segments | track_limits |
|---|---|---|---|---|---|---|
| 2022 | Q | 22 | 6,985 | 4,484 | 73,129 | 89 |
| 2022 | R | 22 | 23,577 | 22,507 | 367,168 | 259 |
| 2022 | S | 3 | 1,305 | 1,243 | 17,927 | 34 |
| 2023 | Q | 22 | 7,419 | 4,716 | 75,804 | 182 |
| 2023 | R | 22 | 24,420 | 23,278 | 380,234 | 412 |
| 2023 | S | 6 | 2,176 | 1,939 | 30,415 | 48 |
| 2023 | SS | 6 | 1,476 | 962 | 14,717 | 44 |
| 2024 | Q | 24 | 7,590 | 4,502 | 73,778 | 165 |
| 2024 | R | 24 | 26,604 | 25,866 | 424,442 | 346 |
| 2024 | S | 6 | 2,418 | 2,271 | 35,433 | 45 |
| 2024 | SQ | 6 | 1,353 | 874 | 13,755 | 38 |
| 2025 | Q | 24 | 7,456 | 4,294 | 69,599 | 193 |
| 2025 | R | 24 | 26,689 | 25,676 | 422,902 | 329 |
| 2025 | S | 6 | 2,152 | 1,929 | 32,581 | 48 |
| 2025 | SQ | 6 | 1,369 | 892 | 14,960 | 26 |
| 2026 | Q | 15 | 5,054 | 2,974 | 43,582 | 130 |
| 2026 | R | 15 | 17,236 | 13,409 | 186,761 | 272 |
| 2026 | S | 5 | 2,146 | 1,800 | 26,224 | 45 |
| 2026 | SQ | 5 | 1,284 | 757 | 10,685 | 30 |

## Failed sessions

None.

## Laps by class

Only `push` (qualifying) and `race` laps are used for modelling.

| lap_class | laps | grid_ok_share |
|---|---|---|
| race | 105,983 | 0.972 |
| no_time | 15,125 | 0 |
| in | 12,262 | 0.722 |
| push | 11,271 | 0.989 |
| slow | 6,370 | 0.983 |
| neutralised | 6,213 | 0.929 |
| out | 5,223 | 0.805 |
| yellow | 5,165 | 0.914 |
| start | 1,097 | 0.319 |

### Why laps have no grid

| reason | laps |
|---|---|
| no lap time | 15,127 |
| lap not fully covered | 4,009 |
| no position data | 1,407 |
| off the reference line | 1,180 |
| sparse position data; speed disagrees with position | 1,018 |
| position glitches | 578 |
| off the reference line; position glitches | 254 |
| speed disagrees with position | 238 |
| frozen car data | 148 |
| frozen car data; speed disagrees with position | 106 |
| position glitches; gap in samples; sparse position data; speed disagrees with position | 93 |
| sparse position data | 86 |
| position glitches; sparse position data; speed disagrees with position | 22 |
| gap in samples; sparse position data | 14 |
| position glitches; frozen car data; gap in samples; sparse position data; speed disagrees with position | 14 |
| off the reference line; position glitches; gap in samples; sparse position data; speed disagrees with position | 10 |
| off the reference line; position glitches; sparse position data | 9 |
| position glitches; speed disagrees with position | 6 |
| position glitches; frozen car data; sparse position data; speed disagrees with position | 4 |
| frozen car data; sparse position data; speed disagrees with position | 2 |
| position glitches; sparse position data | 2 |
| position glitches; frozen car data; speed disagrees with position | 2 |
| gap in samples; sparse position data; speed disagrees with position | 2 |
| position glitches; frozen car data | 2 |
| off the reference line; position glitches; sparse position data; speed disagrees with position | 2 |
| off the reference line; position glitches; frozen car data; sparse position data; speed disagrees with position | 1 |

## Alignment quality (clean laps)

Distance from the reference line (p95 per lap), the largest gap between car-data samples and the largest gap between position fixes.

| median_p95_offset_m | p95_p95_offset_m | median_max_gap_m | p95_max_gap_m | median_max_fix_gap_m | p95_max_fix_gap_m |
|---|---|---|---|---|---|
| 0.54 | 0.72 | 62.3 | 98.9 | 60 | 96 |

Grid time at the end of the lap vs the official lap time:

| laps | median_abs_s | p99_abs_s |
|---|---|---|
| 114,208 | 0.064 | 0.233 |

## Corner segments

| session | segments | clean | stitched |
|---|---|---|---|
| Q | 335,892 | 156,530 | 3,429 |
| R | 1,781,507 | 1,528,758 | 26,946 |
| S | 142,580 | 129,116 | 2,220 |
| SQ | 39,400 | 18,985 | 158 |
| SS | 14,717 | 5,438 | 0 |

## Weak labels

Track-limits labels by the class of the lap they fall on. Only labels on `push` and `race` laps that match a segment are used to evaluate mistake detection.

| lap_class | track_limit_events | matched_to_a_segment | in_clusters_of_3_plus |
|---|---|---|---|
| race | 1,500 | 1,395 | 128 |
| push | 313 | 304 | 25 |
| slow | 292 | 235 | 33 |
| in | 215 | 103 | 6 |
| yellow | 173 | 131 | 20 |
| no_time | 138 | 0 | 5 |
| out | 66 | 51 | 4 |
| neutralised | 19 | 15 | 0 |
| start | 19 | 4 | 3 |

Driving-related incidents (lap-level, approximate): 776.

## Frozen feeds and data gaps

The live-timing feeds sometimes fail while the car keeps moving. The car feed can freeze (speed, RPM, throttle and gear repeat exactly) or drop samples; the position feed can stall, repeating the last fix. Between position fixes, distance comes from integrated speed, so a short stall costs nothing. The speed channel can also stick on one value while the car moves on. Stretches that are frozen, longer than 120 m between car samples or between position fixes, or where distance from speed and from position fixes differs by more than 40% over 1.5 s, are marked invalid: corner windows touching them are dropped, and laps more than 30% affected by any one of them get no grid. Sessions with the most windows dropped (`mean_share`: the average share of a lap affected by the worst of the four):

| year | round | session | event | laps_frozen | laps_car_gaps | laps_position_gaps | laps_speed_mismatch | mean_share | windows_dropped |
|---|---|---|---|---|---|---|---|---|---|
| 2026 | 2 | R | Chinese Grand Prix | 536 | 25 | 36 | 866 | 0.128 | 6,183 |
| 2026 | 2 | S | Chinese Grand Prix | 318 | 14 | 17 | 331 | 0.279 | 1,905 |
| 2026 | 3 | R | Japanese Grand Prix | 501 | 3 | 7 | 452 | 0.075 | 1,762 |
| 2026 | 2 | Q | Chinese Grand Prix | 148 | 93 | 104 | 218 | 0.247 | 1,595 |
| 2023 | 6 | R | Monaco Grand Prix | 4 | 0 | 1 | 315 | 0.003 | 1,051 |
| 2026 | 2 | SQ | Chinese Grand Prix | 145 | 50 | 53 | 194 | 0.317 | 998 |
| 2023 | 21 | R | Las Vegas Grand Prix | 40 | 4 | 3 | 273 | 0.006 | 905 |
| 2025 | 22 | R | Las Vegas Grand Prix | 32 | 13 | 11 | 192 | 0.004 | 576 |
| 2026 | 1 | R | Australian Grand Prix | 128 | 26 | 23 | 210 | 0.013 | 519 |
| 2026 | 7 | R | Barcelona Grand Prix | 35 | 3 | 4 | 210 | 0.004 | 498 |
| 2026 | 4 | R | Miami Grand Prix | 77 | 8 | 10 | 181 | 0.009 | 488 |
| 2026 | 3 | Q | Japanese Grand Prix | 138 | 1 | 0 | 124 | 0.083 | 486 |

## DRS signal by season

DRS was removed for 2026, so the channel should be empty that year.

| year | laps_with_drs |
|---|---|
| 2022 | 0.547 |
| 2023 | 0.515 |
| 2024 | 0.508 |
| 2025 | 0.506 |
| 2026 | 0 |
