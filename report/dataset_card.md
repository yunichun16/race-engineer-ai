# Dataset card: F1 telemetry corner segments (2022–2026)

## Summary

Lap-by-lap Formula 1 car telemetry, aligned on a common distance axis and cut into
fixed-length **corner segments**, with lap context and weak labels for driver mistakes. Built
for self-supervised representation learning, mistake (anomaly) detection and driving-style
comparison.

Rebuilt from source with `uv run race-engineer-data build`; quality numbers are in
[data_quality.md](data_quality.md).

## Source and terms

- **Data:** Formula 1 live-timing data, accessed through [FastF1](https://github.com/theOehrly/Fast-F1) 3.8.
- **Coverage:** seasons 2022–2026 (2026 up to round 15, Azerbaijan): every qualifying,
  sprint qualifying (sprint shootout in 2023), sprint and race session. 263 sessions.
- **Download limits:** FastF1 allows 500 API calls per hour (about 13 per session), so a full
  build is paced at one downloaded session every ~110 s and takes about 7 hours.
- **Terms:** the data belongs to Formula 1. It's used here for non-commercial research and
  education and is **not redistributed**; the pipeline rebuilds it locally. This project is
  unofficial and not affiliated with Formula 1 or the FIA.

## How it's built

1. **Load** each session with FastF1: laps, car telemetry (speed, throttle, brake, gear, RPM,
   DRS at ~4 Hz), car positions (X/Y at ~4 Hz), race-control messages, weather, and the
   official turn positions.
2. **Reference line.** The yardstick is the session's fastest lap with a clean position trace:
   its length agrees with the lap's integrated speed within 4%, the path through its fixes
   agrees with its speed everywhere (no glitch spikes), and no gap between fixes cuts a corner
   by more than 5 m (a gap on a straight is harmless). It starts exactly at the timing line and
   is densified to 1 m and lightly smoothed.
3. **Alignment.** Distance around the lap comes from two sources. Position fixes, matched to
   the nearest point of the reference line, say where the car is; integrated speed says how
   far it went between fixes. This beats FastF1's integrated-speed distance, which drifts, and
   plain interpolation between fixes, which breaks when the position feed stalls (it repeats
   the last fix for a second or more; for most of the 2026 Hungarian GP race it only updated
   every ~3.5 s). Only fixes that move forward are used, and where the track crosses itself
   (Suzuka) the distance driven picks the right branch. Fixes just before and after the
   timing line are shifted onto the same axis.
   Stretches are marked invalid (`valid = 0`) where the car feed is frozen (6 or more raw
   samples, ~1.5 s, with speed, RPM, throttle and gear all exactly unchanged), where car
   samples or position fixes are more than 120 m apart, or where distance from fixes and from
   speed differ by more than 40% over 1.5 s (usually a speed channel stuck on one value).
4. **Distance grid.** Each lap is resampled every **5 m**: continuous channels linearly, gear
   and DRS as step functions, position channels between fixes by distance. Laps are rejected
   if the car is off the reference line (p95 > 25 m, e.g. the pit lane), if position glitches
   exceed 60 m of backwards travel, or if more than 30% of the lap is invalid for any one of
   the reasons above.
5. **Corner segments.** For every official turn (for brand-new circuits without official turn
   data, such as Madrid in 2026, corners are detected as the reference lap's speed minima), a window from **250 m before to 150 m after
   the apex** (80 grid points), so every segment has the same physical scale. Windows that
   cross the timing line borrow from the neighbouring lap when it has a grid. Windows that
   touch invalid data are dropped.
6. **Context.** Lap class, tyre compound and age, stint, lap number (a stand-in for fuel load),
   gap to the car ahead at the line, and weather.
7. **Weak labels.** Parsed from race-control messages (details below).

## Tables

One Parquet file per session per table under `data/processed/<table>/`, with DuckDB views
over all of them (`race_engineer.data.store.connect()`). Keys: `year`, `round`, `session`
(Q, SQ, SS, S or R), `driver_number`, `lap_number`.

| Table | One row per | Main columns |
|---|---|---|
| `sessions` | session | event, date, track length, grid size, reference lap, counts |
| `tracks` | session | reference line on the grid: `x_m`, `y_m`, `curvature` |
| `corners` | turn | `number`, `letter`, `apex_m`, position, `curvature` over the 80-point window |
| `laps` | lap | lap time, sectors, speed traps, compound, tyre life, `track_status`, `lap_class`, `gap_ahead_s`, weather, `deleted`, and alignment QA (`grid_ok`, `grid_reason`, `p95_offset_m`, `max_gap_m`, `max_fix_gap_m`, and the shares of the lap invalid for each reason: `frozen_share`, `gap_share`, `sparse_share`, `mismatch_share`) |
| `lap_grids` | lap with a grid | lists on the 5 m grid: `time_s`, `speed`, `throttle`, `brake`, `gear`, `rpm`, `drs`, `x_m`, `y_m`, `offset_m`, `valid` |
| `segments` | lap × turn | 80-point arrays: `time_s`, `speed`, `throttle`, `brake`, `gear`, `rpm`, `offset_m`; `segment_time_s`; lap context; `segment_id` |
| `events` | label | `driver`, `lap_number`, `turn`, `kind`, `source`, `message`, `drivers_same_lap_turn` |

**Channels.** `speed` in km/h; `throttle` 0–100%; `brake` 0–1 (the raw signal is on/off, so
fractions only appear where interpolation straddles a change); `time_s` in seconds since the
lap (or segment window) started; `offset_m` is the signed distance from the reference line,
positive to the left of travel.

**Lap classes.** `push` (qualifying) and `race` are the clean laps used for modelling. For
evaluating mistake detection, slow race laps are kept too: going off track is itself a common
reason for a slow lap. The
others: `start` (the race's first lap), `slow` (qualifying laps slower than 107% of the
session's best, or race laps slower than 110% of the driver's median), `yellow`,
`neutralised` (safety car, virtual safety car, red flag), `in`, `out`, `no_time`.

## Weak labels

- **Track limits** (`kind = track_limits`): a lap time deleted for exceeding track limits at
  a named turn. Driver, lap and turn are exact, so it points at one segment. Sources: FastF1's
  own deleted-lap flag, plus race-control messages. Messages that carry a lap time are matched
  on it; the rest use a lap-number offset learned per session (qualifying messages count one
  extra lap).
- **Incidents** (`kind = incident`): driving-related incidents noted by race control (leaving
  the track, collisions, off-track, spins, or turn-specific incidents without a stated
  reason). Mapped to the lap in progress when the message was posted, so the lap is
  approximate and the turn is often missing (`-1`).
- **Clusters.** `drivers_same_lap_turn` counts drivers flagged at the same lap and turn. Large
  values (e.g. 11 drivers at Turn 15 on the last lap in Baku 2026, right after a crash there)
  usually mean something on track pushed everyone wide, not independent mistakes.

## Intended use and splits

- **Use:** self-supervised telemetry models, mistake detection evaluated against the weak
  labels, driving-style comparison (especially between teammates, who share a car).
- **Splits:** train on 2022–2024, validate on 2025, test on 2026 (the new regulations, as a
  distribution-shift test). Leaving whole circuits out tests generalisation to unseen tracks.

## Known limitations

- **Feed failures.** Some events have long stalls in the car data (7% of laps in the
  first 39 sessions; up to a third of the 2026 China and Japan races), stuck speed channels,
  or a sparse position feed. They're detected and dropped, but a stall shorter than ~1.5 s,
  or one affecting only some channels (e.g. a brake signal stuck on), can slip through. At
  one spot of the 2026 Chinese GP circuit every car's positions lag and then catch up, so the
  corners around it are dropped for that whole weekend.

- **Coarse sampling.** About 4 samples per second, i.e. ~20 m apart at 300 km/h. Braking
  points are only accurate to one sample, and very short events (a brief lock-up) may fall
  between samples.
- **Brake is on/off,** with no pressure information.
- **DRS** doesn't exist in 2026, so that channel is always 0 that year.
- **Lateral position carries little information.** The reference line comes from one lap, so
  `offset_m` is relative to that driver's line, not the track centre. More importantly, the
  position feed appears smoothed or snapped to a line: in every season the typical lap's 95th
  percentile offset is only about 0.5 m, far less than real differences between racing lines.
  Running wide shows up in speed and throttle (e.g. a lift mid-corner), rarely in `offset_m`.
- **Traffic** is measured only at the timing line (gap at the start of each lap).
- **Weather** is the nearest reading to each lap's start.
- **Label coverage.** Track-limits labels only exist where a lap time was deleted. Many mistakes
  (lock-ups, wide exits that stay within the limits) are unlabelled, so label-based precision
  understates real precision, and a manual review is part of the evaluation.
- **Position-data outages.** Laps with no or sparse position data are skipped (e.g. most of
  the 2026 Monaco race after the first hour, and the 2026 Hungarian GP race after lap 13), so
  a few sessions are only partly covered.
- **Lap timing.** Race first laps start from the grid, not the timing line, so their segments
  near the start are unreliable (they're classed `start` and excluded).
