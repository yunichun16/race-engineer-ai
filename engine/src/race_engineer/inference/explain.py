"""M4 mistake explanations: what went wrong at a flagged corner, and what it cost.

The detectors say a corner was unusual; a race engineer wants to know how, and whether it
mattered. Each segment is compared with the same driver's usual way through that corner in the
same session (the median of their other clean laps there), feature by feature and point by
point along the trace:

- **Reference.** In qualifying, all of the driver's other push laps at that turn. In races, the
  RACE_REFERENCE_LAPS clean laps nearest in lap number: a corner gets about 0.2 s faster over a
  race as fuel burns off, and a local median halves the spread against it (median absolute
  deviation 0.056 s against 0.095 s for the whole race, 2024-2026 races). With fewer than
  MIN_REFERENCE such laps (most qualifying sessions: drivers do 3-4 push laps), the session's
  field is the reference instead, so car pace then counts towards the time lost.
- **Time lost** is the segment time (`segment_time_s`, from the timing clock) minus the
  reference's median. Where it was lost comes from a smoother clock: elapsed time integrated
  from the speed trace, scaled to end at the lap's own segment time. Between laps of the same
  driver it spreads less than the raw grid clock, which jitters by ~0.05 s from point to point
  (lap-to-lap spread of the time to the apex 0.050 s against 0.072 s in 2026 Australia
  qualifying). Scaling keeps the phases adding up to the time lost.
- **Phases.** Entry is the approach and braking zone (250 m to 50 m before the apex), apex is
  turn-in to the start of the exit (50 m before to 30 m after), exit is the rest of the window
  (30 m to 145 m after). A slow exit keeps costing time on the following straight, beyond the
  window, so exit losses are a lower bound.
- **Mistake type** comes from driver-relative robust z-scores (median and MAD of the reference,
  with the MIN_SCALE floors of the baselines) of the hand-crafted features, counted only in the
  adverse direction (braking earlier, slower, later on the throttle). Each feature's threshold
  is the 99th percentile of its adverse z on ordinary corners of 2022-2024 (clean laps that
  were not flagged; `calibrate_thresholds`), so a rule fires only when the deviation is bigger
  than on 99 of 100 ordinary laps. The strongest rule (z over threshold) gives the type, the
  next one the secondary type, with three exceptions. Over-slowing needs a braking zone (a
  corner usually taken without braking is slowed by lifting, not over-braking). When coasting
  fires with nothing adverse in the braking and a loss no bigger than that much coasting costs,
  lift-and-coast is the type and the slower speeds and later throttle that coasting causes are
  not typed as separate mistakes; otherwise coasting doesn't explain the loss and isn't a type
  (on a qualifying push lap before 2026, where there is nothing to save, a lift is listed as a
  mistake). And when the car arrived slower than usual and lost most of the time on entry, the
  loss was carried in from before the corner: a slow approach is the type. Before the rules
  come, in this order: a track-limits deletion or race-control incident naming the driver
  alone at that turn, or at a neighbouring turn inside its window (a message naming two cars
  is context, not the driver's mistake), a data problem (the flag is about the feed, not the
  driving), traffic (below), a corner in the middle of a slow stretch of the lap (an abandoned
  lap or a slow-down, reaching back into the previous lap when it was a racing lap too) or on
  the run to the flag, and a corner that was not clearly slower than usual.
- **Traffic** comes from where every car was (inference/traffic.py), in an extended window from
  450 m before the apex to 445 m after it, for a corner that was slower than usual
  (`traffic_finding`). Only a car already close when the driver's loss began makes the loss
  traffic: where it began is the first point where the running time lost reaches ONSET_S =
  0.1 s (`loss_onset`), and how close each car was then the closest it came before it. In
  races: **lapped** (a car at least a lap ahead went past, after passing before the loss began
  or from within LAPPED_GAP_S = 1.75 s behind then; or the driver braked where the usual lap is
  on the power with it within YIELD_GAP_S = 1.6 s, `yield_point`: letting it by under blue
  flags), **battle** (a car on the same lap within BATTLE_GAP_S = 0.6 s at the start of the
  window passed or was passed, or came within SIDE_BY_SIDE_S = 0.1 s in the corner's window; a
  car that went past only when it was alongside, within ALONGSIDE_S = 0.35 s, as the loss
  began, past already, or slowed itself), and in any session **impeded** (a car stayed ahead
  through the apex with the driver within IMPEDE_GAP_S = 1 s of it, slower through the corner
  than the driver usually is, the driver no slower than it). In qualifying, a lap already
  PREP_SHARE = 3.2% slower than the driver's usual push lap where the window starts is **not a
  full push lap**. All come from 2022-2025 (the constants have the evidence): the alongside
  and battle gaps are the 95th percentile of the gaps from which such cars passed on ordinary
  corners (when the loss began, or for the battle bar at the start), the lapped gap the same
  over ordinary and flagged corners (a costly yield is flagged), the yield gap the ordinary
  corners' one where the driver braked, the side-by-side and impeded ones what running
  alongside or behind a slower car costs there, and the push-lap share the 99th percentile of
  how slow ordinary push laps are at that point. A car from further back that got by because
  of a mistake leaves the mistake typed, with "lost the place to X" as context (so are
  following a car within 1 s, passing one, and the first racing lap after a safety car). A
  race-control type stays; when a car that explains its loss was already at the driver as it
  began (alongside, past, or lapping them from within YIELD_START_S = 0.5 s), it ranks on the
  bonus alone, otherwise on the full loss plus the bonus (priority). A driving flag whose
  window starts inside a traffic flag's extended window on the same lap is the same moment and
  isn't listed either (traffic_cover). The last FLAG_RUN_CORNERS = 3 turns of the lap a driver
  takes the chequered flag on are the run to the flag, a slow-down, unless a car passed or was
  passed there (`run_to_flag`).
- **Slower than usual** means the robust z of the segment time is above its 95th percentile on
  ordinary corners (the time spread floored at TIME_MIN_SCALE, the timing clock's own
  jitter): only then did the corner cost time beyond the lap-to-lap noise. 95, not 99: the
  question is whether it cost time at all, not whether the loss was extreme, and exit losses
  carry on past the window.
- **Slowest point.** The minimum speed is the turn's own, looked for within APEX_BAND_M of the
  apex: the window reaches the neighbouring turns, whose slowest points are often lower. The
  braking point, the throttle pickup and the throttle lift after it are measured from there.
  Where turns come close together that point can switch to the neighbouring turn on one lap;
  then all three are measured from the usual slowest point instead (for the lap and its
  reference alike), so they compare like with like. A turn usually taken flat out has no
  slowest point of its own, so there they aren't compared at all.

    uv run race-engineer-infer explain [--allow-unrecorded] [--no-report] [--calibrate-traffic]

Reads data/results/segment_scores.parquet, which must be the current scoring run's (see
inference/provenance.py; `--allow-unrecorded` accepts scores from before runs were recorded),
and writes data/results/mistakes.parquet (one row per flagged segment, recording that run) and
report/m4_mistakes.md. `--calibrate-traffic` prints the evidence for the traffic settings from
every scored corner of 2022-2025 instead (a few minutes; it writes nothing).
"""

from __future__ import annotations

import argparse
import logging
import math
import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.ndimage import median_filter
from scipy.stats import fisher_exact

from race_engineer.config import GRID_STEP_M, PROCESSED_DIR, QUALI_CODES, REPORT_DIR, RESULTS_DIR
from race_engineer.data import store
from race_engineer.data.labels import CAR_RE
from race_engineer.data.qa import markdown_table
from race_engineer.features.handcrafted import (
    BRAKE_ON,
    DISTANCE,
    FEATURES,
    THROTTLE_FULL,
    corner_features,
)
from race_engineer.inference import provenance, traffic
from race_engineer.inference.traffic import Encounter, SessionTracks, Surroundings
from race_engineer.models.baselines import MIN_SCALE
from race_engineer.tools.lap_comparison import LAP_CLASS_TEXT, stale_speed

log = logging.getLogger(__name__)

NAMES: tuple[str, ...] = FEATURES  # the hand-crafted feature names, typed as plain strings

TRACE_CHANNELS = ("time_s", "speed", "throttle", "brake", "gear", "offset_m")
META_COLUMNS = (
    "segment_id", "lap_id", "year", "round", "session", "driver", "driver_number", "team",
    "lap_number", "corner", "corner_letter", "apex_m", "lap_class", "compound", "tyre_life",
    "gap_ahead_s", "rainfall", "deleted",
)  # fmt: skip
CLEAN_CLASSES = ("push", "race")  # the laps a reference is built from (not deleted)
RACE_CODES = ("R", "S")
# The laps the detectors score (evaluation.data.ELIGIBLE_SQL): push laps in qualifying, race
# and slow laps in races. Out- and in-laps, safety-car and yellow-flag laps are slow for
# reasons of their own, so their deviations can't be weighed against a racing lap's.
ELIGIBLE_QUALI = ("push",)
ELIGIBLE_RACE = ("race", "slow")

MIN_REFERENCE = 5  # fewer of the driver's own laps than this: compare with the field
RACE_REFERENCE_LAPS = 10  # in races, the driver's clean laps nearest in lap number
FIELD_REFERENCE_LAPS = 40  # in races, the field's clean laps nearest in lap number
SAME_APEX_M = 40.0  # see compare
# A turn's own slowest point is looked for within this distance of its apex. The window reaches
# the neighbouring turns (250 m back, 145 m on), whose slowest points are often lower: the
# previous turn's exit or the next one's braking zone. The window's slowest point lies outside
# this band on about half of ordinary corners (at least 100 m from the apex on 44%, 2022-2026),
# and measured from there the braking point and the throttle pickup describe another turn.
APEX_BAND_M = 60.0
APEX_BAND = np.abs(DISTANCE) <= APEX_BAND_M
APEX_RELATIVE = ("brake_start_d", "throttle_pickup_d", "throttle_dip")  # see apex_relative
# The timing clock jitters by ~0.05 s (see the module docstring), so a reference whose laps
# happen to agree to the hundredth doesn't make a few hundredths look like time lost.
TIME_MIN_SCALE = 0.05
SCALE_FLOOR = np.array(
    [TIME_MIN_SCALE if f == "segment_time_s" else MIN_SCALE[f] for f in FEATURES]
)

# Phase edges, metres from the apex (see the module docstring).
PHASE_EDGES_M = (-50.0, 30.0)
PHASES = ("entry", "apex", "exit")
PHASE_TEXT = {"entry": "on entry", "apex": "through the apex", "exit": "on exit"}
MOSTLY = 0.5  # a phase holding more than this share of the loss is "mostly" where it went
EVEN_S = 0.005  # a difference this small is "no slower" in the sentence
# Two corners' windows overlap when their apexes are closer than a window's length: a flag at
# each is usually one moment seen twice (find_mistakes lists it once, see merge_overlapping).
WINDOW_SPAN_M = float(DISTANCE[-1] - DISTANCE[0])

# Features whose deviation from the reference is read, and which direction is adverse:
# +1 = higher is worse (braking longer, later on the throttle), -1 = lower is worse.
ADVERSE = {
    "v_start": -1,
    "brake_start_d": -1,  # more negative = braking further before the apex
    "brake_len_m": +1,
    "decel_max": +1,
    "v_min": -1,
    "throttle_pickup_d": +1,
    "throttle_dip": +1,
    "coast_m": +1,
    "v_exit": -1,
    "exit_accel": -1,
    "offset_exit_max": +1,
    "segment_time_s": +1,  # the outcome: the bar for "slower than usual", not a type
}
DEVIATIONS = tuple(ADVERSE)
# Peak deceleration and exit acceleration come from the jittery grid clock, so a big z on its
# own is often noise: they are only quoted as evidence behind a rule, not on their own. The
# segment time is what the flag cost, not how.
QUOTABLE = tuple(f for f in DEVIATIONS if f not in ("decel_max", "exit_accel", "segment_time_s"))
BRAKING = ("brake_start_d", "brake_len_m", "decel_max")

# Types read off the driving, and the features that are evidence for each. Over-slowing is
# called a possible lock-up when the braking was also longer or harder than usual (SUPPORTING).
TYPE_RULES: dict[str, tuple[str, ...]] = {
    "early_braking": ("brake_start_d",),
    "over_slowing": ("v_min",),
    "late_throttle": ("throttle_pickup_d", "v_exit", "offset_exit_max"),
    "hesitation": ("throttle_dip",),
    "lift_and_coast": ("coast_m",),
    "slow_approach": ("v_start",),
}
SUPPORTING = {"over_slowing": ("brake_len_m", "decel_max"), "late_throttle": ("exit_accel",)}
# What coasting does to the rest of the corner: slower through it and out of it, later back on
# full throttle, and a lift after the pickup when the coast starts after the apex.
COAST_EFFECTS = ("over_slowing", "late_throttle", "hesitation", "slow_approach")
# What coasting costs: on ordinary corners where a driver coasted more than usual (the rule
# fired) with nothing adverse in the braking, the time lost per extra metre of coasting stayed
# below this on 99 of 100 (COAST_COST_CORNERS such corners of 2022-2026, up to 60 per
# session). A bigger loss has another cause (a problem, traffic, a slow-down), which coasting
# doesn't explain, so the flag isn't typed lift and coast (see driving_types).
COAST_COST_S_PER_M = 0.025
COAST_COST_CORNERS = 1694
TYPE_TEXT = {
    "early_braking": "early braking",
    "over_slowing": "over-slowing",
    "late_throttle": "late throttle / wide exit",
    "hesitation": "hesitation / traction loss",
    "lift_and_coast": "lift and coast (likely energy or fuel management)",
    "lift_on_push_lap": "lift before braking on a push lap (nothing to save in qualifying)",
    "slow_approach": "slow approach (carried in from before the corner)",
    "track_limits": "track limits / off track (race control)",
    "incident": "race-control incident",
    "lapped": "being lapped (letting a faster car by under blue flags)",
    "battle": "racing another car (passing, being passed or side by side)",
    "impeded": "held up by a slower car ahead",
    "not_push_lap": "not a full push lap (already slow before this corner)",
    "no_time_lost": "unusual but not clearly slower",
    "slowdown": "part of a slow-down (abandoned lap, slow stretch or something on track)",
    "data_problem": "data problem, not driving",
    "no_reference": "too few clean laps to compare",
    "unclear": "unclear",
}
NAMED = ("track_limits", "incident")  # types race control gives
TRAFFIC = ("lapped", "battle", "impeded")  # types the other cars give (see traffic_finding)
# Flags that are not driving mistakes at that corner; find_mistakes leaves them out. A lift and
# coast is energy management in 2026 and fuel or tyre saving in races; on a qualifying push lap
# before 2026 there is nothing to save, so it is `lift_on_push_lap` there, and listed.
NOT_MISTAKES = (
    "lift_and_coast", "slow_approach", "slowdown", "no_time_lost", "data_problem", "no_reference",
    *TRAFFIC, "not_push_lap",
)  # fmt: skip

# Adverse z above which a feature counts as evidence: the 99th percentile of adverse z on
# ordinary corners of 2022-2024, floored at MIN_THRESHOLD; for the segment time, the 95th
# (TIME_QUANTILE, see the module docstring). main() recalibrates them on every run and warns
# when they drift; see report/m4_mistakes.md.
CALIBRATION_QUANTILE = 0.99
TIME_QUANTILE = 0.95
QUANTILES = {f: TIME_QUANTILE if f == "segment_time_s" else CALIBRATION_QUANTILE for f in ADVERSE}
MIN_THRESHOLD = 2.0
THRESHOLDS = {
    "v_start": 4.08,
    "brake_start_d": 5.00,
    "brake_len_m": 5.00,
    "decel_max": 6.94,
    "v_min": 5.30,
    "throttle_pickup_d": 9.00,
    "throttle_dip": 3.83,
    "coast_m": 4.38,
    "v_exit": 3.81,
    "exit_accel": 3.23,
    "offset_exit_max": 2.00,
    "segment_time_s": 2.44,
}
CALIBRATION_PER_SESSION = 150  # ordinary corners sampled per session for the calibration

# Data checks (see data_issues). Values were set by looking at flagged corners by eye.
CLOCK_TOL_S = 0.4  # clock and speed trace disagree on the time lost by more than this...
CLOCK_SHARE = 0.5  # ...and the speed trace shows less than this share of the clock's loss
MAX_ACCEL = 50.0  # m/s^2 from the speed trace alone; an F1 car manages about 15
SPIKE_KPH = 25.0  # a single point this far above its neighbours' median
STUCK_SPEED_M = 20.0  # speed unchanged over this distance with the brake fully on
BRAKE_FULL = 0.99  # both raw samples around a grid point say braking (not the interpolated ramp)
REAL_LOSS_S = 0.3  # a channel problem doesn't hide a loss this big that the clock confirms
STUCK_BRAKE_M = 50.0  # brake on at full throttle over this distance
FALLBACK_S = 0.5  # time lost that is won back within the window: driving can't do that

FLAG_SHARE = 0.01  # as in inference.score (not imported: it pulls in torch)
RACE_CONTROL_BONUS_S = 0.5  # see priority
LAP_SLOW_S = 0.3  # a corner of the same lap this much slower than usual counts as slow
SLOW_RUN = 4  # this many slow corners in a row before a flag: a slow stretch on its own
# In races, at least this many other drivers, and this share of those at the same turn on the
# same lap, this much slower than usual: something on track or the conditions (see lap_context).
CLUSTER_OTHERS = 3
CLUSTER_SHARE = 1 / 3
CLUSTER_SLOW_S = 0.5
# In races, race control naming this many drivers at one turn on one lap: something on track,
# not independent mistakes (evaluation.data.CLUSTER_SIZE leaves those labels out the same way).
CLUSTER_NAMED = 3
BIG_LOSS_S = 2.0  # more lost than this in one corner: usually a spin, an off or a problem
# In races without lap grids to place the cars (see traffic_finding), this close to the car
# ahead at the line is worth a mention.
TRAFFIC_GAP_S = 1.0
# Traffic (traffic_finding, not_push_lap, run_to_flag), calibrated on 2022-2025 only, never
# 2026, over every corner the detectors score (`--calibrate-traffic` prints the evidence;
# `summarise_traffic`). 'Ordinary' corners are on clean laps and not flagged. Cars placed too
# roughly for the rules (traffic.Encounter.rough) are left out of the evidence as they are out
# of the rules. A gap 'at the start' is at the first point of the extended window where both
# cars have a position, before the corner's own window (traffic.Encounter.gap_start_s); a gap
# 'when the loss began' is the closest the car came from there up to where the driver's loss
# began (loss_onset; traffic.Encounter.gap_onset_s).
# - Where the driver's loss began: the first point where the running time lost reaches
#   ONSET_S, twice the timing clock's point-to-point jitter (the running loss comes from the
#   smoother speed clock, see the module docstring).
ONSET_S = 0.1
# - A car lapping the driver. Blue flags are shown when the faster car is close, and a driver
#   yields from about that far, so what matters is how close it was when the driver began to
#   lose time, not 450 m before the apex, where a car about to lap them can still be 2 s back
#   (the old start-gap bar, the 95th percentile of 1.73 s over 3,123 ordinary passes, left
#   out exactly the costly yields). On ordinary race corners with a loss of ONSET_S or more
#   (2,682), the lapping car went past before the loss began 1,250 times; the other 1,432
#   times it was within 1.43 s when it began 95 times in 100 (median 0.51 s, 90th percentile
#   1.11 s, 99th 2.21 s); on flagged corners (1,353) within 1.95 s (median 0.73 s). The bar is
#   the 95th percentile over both (2,785 passes, 1.75 s): ordinary corners alone leave out the
#   costly yields again, because a yield that costs a lot is flagged (letting a car by from
#   1.5 s back costs more than from 0.5 s), and their 1.43 s typed backmarkers lifting for
#   300 m with the lapping car 1.4-1.6 s back as mistakes (VET, 2022 Austrian sprint, lap 19;
#   STR, 2025 Singapore GP, lap 41). Over every scored corner, the time lost when it went past
#   after the loss began stays at what letting it by costs, 0.83-0.99 s at the median, up to
#   1.25 s back; then 1.39 s at 1.25-1.5 s and 1.9-2.1 s from 1.5 s to 3 s back. From further
#   than the bar the driver more likely lost the time some other way and the car took its
#   chance (a lock-up with the lapping car still 2 s back: MAG, 2023 Japanese GP, lap 48). A
#   car that went past before the loss began counts too.
LAPPED_GAP_S = 1.75
# - A car on the same lap that passed or was passed: within this at the start (the 95th
#   percentile over 8,036 ordinary same-lap passes, 0.58 s; median 0.18 s, 99th 0.90 s). A pass
#   from further back needs the driver to lose unusual time: over every scored corner the
#   median time lost when one passes is 0.35-0.42 s from up to 0.75 s back and 0.72 s, 1.13 s,
#   1.71 s from each quarter second further.
BATTLE_GAP_S = 0.6
# - A same-lap car that went past was in the fight before the loss only when it was alongside
#   by then: on ordinary corners where one within BATTLE_GAP_S at the start went past after
#   the loss began (1,330), it was within 0.35 s when the loss began 95 times in 100 (median
#   0.10 s, 90th percentile 0.29 s, 99th 0.44 s; with the flagged corners too, 1,770, 0.36 s:
#   unlike the lapping gap, the bar hardly moves). Over every scored corner the time lost is
#   0.48-0.50 s at the median up to 0.3 s apart, then 0.64 s, 0.79 s and 0.86 s for each tenth
#   further: a car further back passed because the driver lost the time. The same bar says
#   whether a same-lap car was alongside when a race-control flag's loss began (priority).
ALONGSIDE_S = 0.35
# - A race-control flag's loss is the traffic's (priority) only when the traffic was already at
#   the driver as it began: race control naming the driver is the surest sign of a mistake,
#   so a car merely within the lapping gap isn't enough. A lapping car counts when it was
#   within the median gap from which drivers began to yield on ordinary corners (0.51 s, of
#   the 1,432 above); half of all yields start with the car that close, while the lapping
#   car of a deleted lap 1.2 s back (TSU, 2026 Dutch GP, lap 43) was still a second from
#   the driver when the off began.
YIELD_START_S = 0.5
# - Braking where the usual lap is on the power, after the turn's usual slowest point
#   (yield_point; the usual lap at YIELD_USUAL_THROTTLE, the full-throttle bar of the
#   hand-crafted features, or more and not braking), when it cost ONSET_S or more: on ordinary
#   race corners 0.2% of 147,913 sampled corners without a lapping car going past, against
#   2.2% of the 2,907 with one (flagged corners: 6.2% and 19.1%). A lapping car that went past
#   from there on was within 1.65 s at that point 95 times in 100 over the 40 ordinary such
#   corners (median 0.48 s, 90th percentile 1.09 s; over every scored corner, 347, median
#   0.66 s, 95th 2.20 s): a driver lets a car by from about as close as when they yield in a
#   corner, but the braking says so when the loss began further back (a slow corner first,
#   then braking on the straight). A lift on the power (yield_lift) comes with a lapping car
#   about as often (0.1% against 1.0% on ordinary corners; on flagged ones 4.0% against
#   13.7%), but it is also what a hesitation or a traction loss looks like, which the
#   throttle-lift rule types as a mistake, so it isn't a rule; braking where the usual lap is
#   on the power is no driving type's signature.
YIELD_GAP_S = 1.6
YIELD_USUAL_THROTTLE = THROTTLE_FULL
YIELD_LIFT = 50.0  # percentage points of throttle below the usual lap (yield_lift)
# - The run to the flag: on the lap a driver takes the chequered flag, a corner is 0.3 s
#   slower than usual (LAP_SLOW_S) 10.8%, 11.4% and 10.9% of the time at the last three turns
#   of the lap, against 4.4-6.2% on the lap before and 3.8-4.7% on the one before that; from
#   the fourth turn from the end it falls to 9.0%, 7.8% and 7.7%. Drivers ease off for the
#   line over the last three turns (and somewhat over the whole lap).
FLAG_RUN_CORNERS = 3
# About a car length at 200 km/h: alongside, or nose to tail within a car length. On ordinary
# corners a same-lap car that close in the corner's window (no pass, within BATTLE_GAP_S at the
# start; 20,000 corners) costs 0.13 s at the median, against 0.03 s at 0.3-0.5 s apart.
SIDE_BY_SIDE_S = 0.1
# Behind a car that stayed ahead through the apex and was at least 0.3 s slower through the
# corner than the driver usually is, an ordinary race corner costs 0.37 s at the median within
# 0.25 s of it, 0.27 s at 0.25-0.5 s, 0.20 s at 0.5-0.75 s, 0.18 s at 0.75-1 s, 0.15 s at
# 1-1.25 s, and 0.08 s at 3-4 s: up to about 1 s behind, the car ahead at least doubles what
# such a corner costs far behind it (half of the extra is gone by about 0.55 s, but the rest
# tapers off slowly). Held up that closely, the driver dropped back through the window by no
# more than 0.22 s on 95 of 100 such corners (0.222 s, 38,077 corners); dropping back more,
# they lost time of their own.
IMPEDE_GAP_S = 1.0
IMPEDE_DROP_S = 0.22
FOLLOW_GAP_S = 1.0  # following a car this closely at the start of the corner is worth a mention
# Qualifying laps: how much slower than the driver's usual push lap (the median of their other
# push laps) the lap was where a corner's window starts. On ordinary push-lap corners (laps
# with no flag) it passed 3.2% of the time to get there 1 time in 100 (3.23%; 103,237 corners
# at least 15 s into the lap). Earlier in the lap a share is noisy (its 99th percentile is 8%
# in the first 15 s), so the rule starts there.
PREP_SHARE = 0.032
PREP_MIN_ELAPSED_S = 15.0
DEPLOYMENT_KPH = 30.0  # a 2026 approach this much slower is more than running out of energy
LEFT_TRACK = ("LEAVING THE TRACK", "OFF TRACK", "TRACK LIMITS", "SPUN")
G = 9.81


# ---------------------------------------------------------------------------------------------
# Loading one session's segments


@dataclass(frozen=True)
class SessionSegments:
    """Every corner segment of one session: meta and hand-crafted features, plus the traces."""

    frame: pd.DataFrame  # one row per segment, index 0..n-1
    traces: dict[str, np.ndarray]  # channel -> (n, 80), rows aligned with frame
    features: np.ndarray  # (n, len(FEATURES)) as compared (see comparable_features)
    clock: np.ndarray  # (n, 80) elapsed time from the speed trace, scaled to the segment time
    timing_ratio: np.ndarray  # (n,) segment clock time over speed-integrated time

    @property
    def is_race(self) -> bool:
        return bool(len(self.frame)) and str(self.frame["session"].iloc[0]) in RACE_CODES

    def eligible(self) -> np.ndarray:
        """Laps the detectors score (see ELIGIBLE_RACE)."""
        classes = ELIGIBLE_RACE if self.is_race else ELIGIBLE_QUALI
        return self.frame["lap_class"].isin(classes).to_numpy()


def speed_clock(time_s: np.ndarray, speed_kph: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Elapsed time along each segment integrated from speed, scaled to end at the segment's
    clock time, and the scale (clock time over integrated time). (n, 80) arrays."""
    mps = np.maximum(np.asarray(speed_kph, dtype=float) / 3.6, 1.0)
    dt = GRID_STEP_M / ((mps[:, 1:] + mps[:, :-1]) / 2)
    t = np.concatenate([np.zeros((len(mps), 1)), np.cumsum(dt, axis=1)], axis=1)
    clock = np.asarray(time_s, dtype=float)[:, -1]
    ratio = clock / np.maximum(t[:, -1], 1e-6)
    return t * ratio[:, None], ratio


def slowest_point(speed: np.ndarray) -> np.ndarray:
    """Grid index of each row's slowest point within APEX_BAND_M of the apex: the turn's own,
    not the neighbouring turn's."""
    band = np.flatnonzero(APEX_BAND)
    return band[speed[:, band].argmin(axis=1)]


def _anchor(speed: np.ndarray, anchor: np.ndarray | None) -> np.ndarray:
    return slowest_point(speed) if anchor is None else np.asarray(anchor, dtype=int)


def _braking_run(
    traces: dict[str, np.ndarray], anchor: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Braking for the turn: the grid index of the first brake application after the driver
    last had full throttle before the slowest point (-1: none), and whether it began before the
    window did (braking where the window starts, with no full throttle before it).

    From the last application of any throttle instead, a lap that never got back to full
    throttle between two turns would be measured from this turn's own braking, but the usual
    laps' brief blips of throttle inside braking zones would split them: braking points 100 m
    or more from usual became three times as common among the flags."""
    speed, throttle, brake = traces["speed"], traces["throttle"], traces["brake"]
    n, width = speed.shape
    idx = np.broadcast_to(np.arange(width), (n, width))
    before = idx <= _anchor(speed, anchor)[:, None]
    last_full = np.where(before & (throttle >= THROTTLE_FULL), idx, -1).max(axis=1)
    braking = (brake > BRAKE_ON) & before & (idx > last_full[:, None])
    first = np.where(braking.any(axis=1), braking.argmax(axis=1), -1)
    return first, (first == 0) & (last_full < 0)


def braking_point(traces: dict[str, np.ndarray], anchor: np.ndarray | None = None) -> np.ndarray:
    """Where braking for the corner began: the first brake application after the driver last
    had full throttle before the turn's slowest point (`slowest_point`, `_braking_run`; NaN: no
    braking, or already braking where the window starts, so the point is somewhere before it).
    `anchor` (a grid index per row) measures from there instead of each row's own slowest
    point.

    The hand-crafted `brake_start_d` is the start of the last braking run before the slowest
    point, so a brief release of the brake (common in chicanes, and on a 4 Hz on/off signal)
    moves it by 100 m or more: on ordinary laps its adverse z passed 16 one time in 100.
    """
    first, censored = _braking_run(traces, anchor)
    return np.where((first >= 0) & ~censored, DISTANCE[np.clip(first, 0, None)], np.nan)


def apex_relative(
    traces: dict[str, np.ndarray], anchor: np.ndarray | None = None
) -> dict[str, np.ndarray]:
    """The features measured from the turn's slowest point (`slowest_point`): the braking point
    before it (`braking_point`), and after it the first full throttle and the largest throttle
    lift once back on the power (as the hand-crafted `throttle_pickup_d` and `throttle_dip`,
    which measure from the window's slowest point, often at the neighbouring turn).

    Never back on full throttle inside the window means the pickup is at the end of the window
    or later, so it counts as the end: a lower bound on how late. `anchor` (a grid index per
    row) measures all three from there instead of each row's own slowest point: compare uses
    the usual slowest point when this lap's is at the neighbouring turn.
    """
    speed, throttle = traces["speed"], traces["throttle"]
    n, width = speed.shape
    idx = np.broadcast_to(np.arange(width), (n, width))
    after = idx >= _anchor(speed, anchor)[:, None]
    full = (throttle >= THROTTLE_FULL) & after
    pickup = np.where(full.any(axis=1), DISTANCE[full.argmax(axis=1)], DISTANCE[-1])
    running_max = np.maximum.accumulate(np.where(after, throttle, -np.inf), axis=1)
    dip = np.where(after, running_max - throttle, 0.0).max(axis=1)
    return {
        "brake_start_d": braking_point(traces, anchor),
        "throttle_pickup_d": pickup,
        "throttle_dip": dip,
    }


def comparable_features(frame: pd.DataFrame, traces: dict[str, np.ndarray]) -> np.ndarray:
    """The hand-crafted features as compared between laps: the slowest point (`v_min`,
    `d_vmin`) is the turn's own (`slowest_point`), `brake_start_d` is replaced by
    `braking_point`, and the throttle pickup and lift are measured from the turn's slowest
    point, a pickup never reached counting as the end of the window (`apex_relative`)."""
    x = frame[list(FEATURES)].to_numpy(float).copy()
    speed = np.asarray(traces["speed"], dtype=float)
    lowest = slowest_point(speed)
    x[:, FEATURES.index("v_min")] = speed[np.arange(len(speed)), lowest]
    x[:, FEATURES.index("d_vmin")] = DISTANCE[lowest]
    for f, values in apex_relative(traces, lowest).items():
        x[:, FEATURES.index(f)] = values
    return x


def session_segments(table: pd.DataFrame, traces: dict[str, np.ndarray]) -> SessionSegments:
    """Hand-crafted features and the speed clock for a session's segments."""
    frame = table.reset_index(drop=True).copy()
    frame["corner_letter"] = frame["corner_letter"].fillna("").astype(str)
    frame["deleted"] = frame["deleted"].fillna(False).astype(bool)
    traces = {c: np.asarray(traces[c], dtype=float) for c in TRACE_CHANNELS}
    frame = frame.assign(**corner_features(traces))
    clock, ratio = speed_clock(traces["time_s"], traces["speed"])
    return SessionSegments(
        frame=frame,
        traces=traces,
        features=comparable_features(frame, traces),
        clock=clock,
        timing_ratio=ratio,
    )


def load_session_segments(key: str, root: Path = PROCESSED_DIR) -> SessionSegments:
    """All corner segments of one processed session (every lap class, not just eligible)."""
    path = store.table_path("segments", key, root)
    if not path.exists():
        raise FileNotFoundError(f"no segments for {key} under {root}")
    table = pq.read_table(path)
    columns = [c for c in META_COLUMNS if c in table.column_names]
    traces = {c: store.fixed_list_to_numpy(table.column(c)) for c in TRACE_CHANNELS}
    return session_segments(table.select(columns).to_pandas(), traces)


def load_session_events(key: str, root: Path = PROCESSED_DIR) -> pd.DataFrame:
    """Race-control labels of one session (empty when there are none)."""
    path = store.table_path("events", key, root)
    columns = ["driver", "lap_number", "turn", "kind", "source", "message"]
    if not path.exists():
        return pd.DataFrame(columns=[*columns, "drivers_same_lap_turn"])
    events = pd.read_parquet(path)
    return events[[c for c in [*columns, "drivers_same_lap_turn"] if c in events]]


def clean_mask(frame: pd.DataFrame) -> np.ndarray:
    """Laps a reference can be built from: push laps in qualifying, green-flag laps in races,
    lap time not deleted."""
    return (frame["lap_class"].isin(CLEAN_CLASSES) & ~frame["deleted"]).to_numpy()


def turn_label(corner: int, letter: str) -> str:
    return f"{int(corner)}{letter or ''}"


# ---------------------------------------------------------------------------------------------
# Comparing one segment with the driver's usual


@dataclass(frozen=True)
class CornerComparison:
    row: int  # position in the session frame
    reference: str  # "driver", "field" or "none" (too few laps to compare)
    reference_rows: np.ndarray
    own_laps: int  # the driver's other clean laps at this turn
    values: dict[str, float]  # this segment's features (as compared)
    usual: dict[str, float]  # the reference's median features (NaN: usually absent)
    delta: dict[str, float]  # values - usual, in the features' own units
    z: dict[str, float]  # robust z against the reference, natural sign (see ADVERSE)
    scale: dict[str, float]  # the spread each z is measured in (MAD-based, floored)
    time_lost_s: float  # segment time minus the reference's median
    speed_time_lost_s: float  # the same from the speed trace alone (see data_issues)
    phase_loss_s: tuple[float, float, float]  # entry, apex, exit; they add up to time_lost_s
    delta_trace_s: np.ndarray  # (80,) running time lost along the window
    usual_trace: dict[str, np.ndarray]  # median speed, throttle, brake, gear, offset_m
    apex_moved: bool  # slowest point far from its usual place (see compare)
    braking_zone: bool  # most reference laps brake for this turn (see compare)
    taken_flat: bool = False  # the turn is usually taken flat out (see compare)

    def adverse(self, feature: str) -> float:
        """z in the adverse direction (positive = worse than usual), NaN when not compared."""
        z = self.z.get(feature, math.nan)
        return ADVERSE[feature] * z if np.isfinite(z) else math.nan


def reference_rows(
    seg: SessionSegments, row: int, clean: np.ndarray
) -> tuple[str, np.ndarray, int]:
    """The laps that say how this driver usually takes this turn (see the module docstring):
    "driver", "field" or "none", their rows, and how many other clean laps the driver has."""
    frame = seg.frame
    target = frame.iloc[row]
    same_turn = (
        (frame["corner"].to_numpy() == target["corner"])
        & (frame["corner_letter"].to_numpy() == target["corner_letter"])
        & clean
    )
    same_turn[row] = False
    laps = frame["lap_number"].to_numpy()

    def nearest(mask: np.ndarray, k: int) -> np.ndarray:
        rows = np.flatnonzero(mask)
        if not seg.is_race or len(rows) <= k:
            return rows
        order = np.lexsort((laps[rows], np.abs(laps[rows] - target["lap_number"])))
        return np.sort(rows[order[:k]])

    mine = same_turn & (frame["driver"].to_numpy() == target["driver"])
    n_own = int(mine.sum())
    if n_own >= MIN_REFERENCE:
        return "driver", nearest(mine, RACE_REFERENCE_LAPS), n_own
    everyone = nearest(same_turn, FIELD_REFERENCE_LAPS)
    if len(everyone) >= MIN_REFERENCE:
        return "field", everyone, n_own
    return "none", np.zeros(0, dtype=int), n_own


def _usual(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median and robust spread per column, ignoring NaN; a column that is NaN on most laps
    (e.g. no braking zone) has no usual value."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns
        median = np.nanmedian(values, axis=0)
        mad = np.nanmedian(np.abs(values - median), axis=0)
    median[np.isnan(values).mean(axis=0) > 0.5] = np.nan
    return median, 1.4826 * mad


def compare(seg: SessionSegments, row: int, clean: np.ndarray | None = None) -> CornerComparison:
    """One segment against the driver's usual way through the same turn."""
    clean = clean_mask(seg.frame) if clean is None else clean
    kind, ref, own_laps = reference_rows(seg, row, clean)
    nan = math.nan
    if kind == "none":
        empty = dict.fromkeys(NAMES, nan)
        blank = np.full(len(DISTANCE), nan)
        return CornerComparison(
            row=row,
            reference=kind,
            reference_rows=ref,
            own_laps=own_laps,
            values=dict(zip(NAMES, seg.features[row].tolist(), strict=True)),
            usual=empty,
            delta=dict(empty),
            z=dict(empty),
            scale=dict(empty),
            time_lost_s=nan,
            speed_time_lost_s=nan,
            phase_loss_s=(nan, nan, nan),
            delta_trace_s=blank,
            usual_trace={c: blank for c in ("speed", "throttle", "brake", "gear", "offset_m")},
            apex_moved=False,
            braking_zone=False,
        )

    x, ref_x = seg.features[row], seg.features[ref]
    # The braking point, throttle pickup and throttle lift are measured from the turn's slowest
    # point (`slowest_point`). Where turns come close together that point can switch to the
    # neighbouring turn on one lap, and all three jump by tens of metres (a normal blip of
    # throttle between two turns becomes a "lift after the pickup"). Then they are measured
    # from the usual slowest point instead, on this lap and the reference laps alike.
    i_vmin = FEATURES.index("d_vmin")
    usual_vmin_d = float(np.median(ref_x[:, i_vmin]))
    apex_moved = bool(abs(x[i_vmin] - usual_vmin_d) > SAME_APEX_M)
    traces = {c: seg.traces[c][ref] for c in ("speed", "throttle", "brake")}
    anchor = None
    if apex_moved:
        rows = np.concatenate([[row], ref])
        anchor = np.full(len(rows), int(np.abs(DISTANCE - usual_vmin_d).argmin()))
        again = apex_relative({c: seg.traces[c][rows] for c in traces}, anchor)
        x, ref_x = x.copy(), ref_x.copy()
        for f, v in again.items():
            x[FEATURES.index(f)] = v[0]
            ref_x[:, FEATURES.index(f)] = v[1:]
        anchor = anchor[1:]
    # A turn usually taken flat out (full throttle and no braking all through its apex band, on
    # the median of the reference laps) has no slowest point of its own: its speed is lowest
    # wherever it happens to dip, so the braking point, pickup and lift aren't compared.
    taken_flat = bool(
        (np.median(traces["throttle"], axis=0)[APEX_BAND] >= THROTTLE_FULL).all()
        and (np.median(traces["brake"], axis=0)[APEX_BAND] <= BRAKE_ON).all()
    )
    if taken_flat:
        x, ref_x = x.copy(), ref_x.copy()
        for f in APEX_RELATIVE:
            x[FEATURES.index(f)] = math.nan
            ref_x[:, FEATURES.index(f)] = math.nan
    # Does this turn have a braking zone at all? Braking after the last full throttle before
    # the slowest point, even from before the window starts (where the braking point itself is
    # unknown); braking for the previous turn, with full throttle between, doesn't count.
    braked = _braking_run(traces, anchor)[0] >= 0
    median, spread = _usual(ref_x)
    scale = np.maximum(spread, SCALE_FLOOR)
    z = (x - median) / scale

    usual_clock = np.median(seg.clock[ref], axis=0)  # ends at the median segment time
    trace = seg.clock[row] - usual_clock
    edges = [int(np.searchsorted(DISTANCE, e)) for e in PHASE_EDGES_M]
    phases = np.diff(trace[[0, *edges, len(trace) - 1]])
    integrated = seg.clock[:, -1] / seg.timing_ratio
    seg_time = seg.frame["segment_time_s"].to_numpy(float)
    return CornerComparison(
        row=row,
        reference=kind,
        reference_rows=ref,
        own_laps=own_laps,
        values=dict(zip(NAMES, x.tolist(), strict=True)),
        usual=dict(zip(NAMES, median.tolist(), strict=True)),
        delta=dict(zip(NAMES, (x - median).tolist(), strict=True)),
        z=dict(zip(NAMES, z.tolist(), strict=True)),
        scale=dict(zip(NAMES, scale.tolist(), strict=True)),
        time_lost_s=float(seg_time[row] - np.median(seg_time[ref])),
        speed_time_lost_s=float(integrated[row] - np.median(integrated[ref])),
        phase_loss_s=(float(phases[0]), float(phases[1]), float(phases[2])),
        delta_trace_s=trace,
        usual_trace={
            c: np.median(seg.traces[c][ref], axis=0)
            for c in ("speed", "throttle", "brake", "gear", "offset_m")
        },
        apex_moved=apex_moved,
        braking_zone=bool(braked.mean() > 0.5),
        taken_flat=taken_flat,
    )


# ---------------------------------------------------------------------------------------------
# Data problems, race control and lap context


def _longest_run(mask: np.ndarray) -> int:
    """Length of the longest run of True."""
    best = run = 0
    for on in mask:
        run = run + 1 if on else 0
        best = max(best, run)
    return best


@dataclass(frozen=True)
class DataIssue:
    kind: str  # "timing": the time lost itself is suspect; "channel": one channel is
    text: str


def data_issues(seg: SessionSegments, cmp: CornerComparison) -> list[DataIssue]:
    """Signs that a flag is about the data feed rather than the driving, found by checking the
    top flags against their traces by eye (report/m4_mistakes.md has examples).

    Timing problems (the time lost is suspect):
    - the timing clock jumps while the speed trace looks normal (a position-data glitch: the
      clock says seconds were lost, the speed trace a tenth);
    - time lost along the corner is won back within the window, which driving on the track
      can't do: the trace is shifted along the track, or the car cut a corner or ran off (the
      distance along the reference line then no longer matches the distance driven). Checked
      only when no speed problem explains it.
    Channel problems (one channel is suspect):
    - the speed channel sticks on one value with the brake fully on, or the clock contradicts
      it (`lap_comparison.stale_speed`);
    - the speed trace jumps by more than a car can accelerate, or spikes for one point;
    - the brake signal stays on at full throttle (a stuck brake channel).
    """
    row = cmp.row
    t = seg.traces
    speed, brake, throttle = t["speed"][row], t["brake"][row], t["throttle"][row]
    issues = []
    clock, by_speed = cmp.time_lost_s, cmp.speed_time_lost_s
    if (
        np.isfinite(clock)
        and abs(clock - by_speed) > CLOCK_TOL_S
        and abs(by_speed) < CLOCK_SHARE * abs(clock)
    ):
        text = f"the timing clock says {clock:.2f} s lost but the speed trace only {by_speed:.2f} s"
        issues.append(DataIssue("timing", text))
    unchanged = np.concatenate([[False], np.diff(speed) == 0])
    stale = stale_speed(t["time_s"][row], speed, GRID_STEP_M)
    stuck_m = max(_longest_run(unchanged & (brake >= BRAKE_FULL)), _longest_run(stale))
    if stuck_m * GRID_STEP_M >= STUCK_SPEED_M:
        issues.append(
            DataIssue("channel", f"the speed channel is stuck for {stuck_m * GRID_STEP_M:.0f} m")
        )
    accel = np.diff((speed / 3.6) ** 2) / (2 * GRID_STEP_M)
    spike = speed - median_filter(speed, size=5, mode="nearest")
    if accel.max() > MAX_ACCEL or spike.max() > SPIKE_KPH:
        jump = np.abs(np.diff(speed)).max()
        issues.append(DataIssue("channel", f"the speed trace jumps by {jump:.0f} km/h"))
    stuck_brake = _longest_run((brake > BRAKE_ON) & (throttle >= THROTTLE_FULL)) * GRID_STEP_M
    if stuck_brake >= STUCK_BRAKE_M:
        text = f"the brake signal is on at full throttle for {stuck_brake:.0f} m"
        issues.append(DataIssue("channel", text))
    d = cmp.delta_trace_s
    speed_problem = any("speed" in i.text for i in issues)
    if np.all(np.isfinite(d)) and not speed_problem:
        lost_so_far = np.maximum(d, 0.0)  # time gained on usual isn't "won back"
        fallback = float(np.max(np.maximum.accumulate(lost_so_far) - lost_so_far))
        if fallback > FALLBACK_S:
            text = (
                f"the speed trace doesn't match the distance covered ({fallback:.1f} s of time "
                "lost is won back within the corner): misaligned position data, or the car "
                "left the track"
            )
            issues.append(DataIssue("timing", text))
    return issues


@dataclass(frozen=True)
class RaceControl:
    kind: str  # "track_limits", "incident", "involved" (a message naming two cars) or ""
    message: str
    others: tuple[str, ...] = ()  # the other drivers an "involved" message names
    named_here: int = 1  # drivers race control named at this turn on this lap (events table)
    turn: int | None = None  # the turn race control named, when a neighbouring one (see below)


def _cars(message: str) -> list[str]:
    """The drivers a race-control message names, in order."""
    return [m["driver"] for m in CAR_RE.finditer(message.split("NOTED", 1)[0])]


def race_control(
    events: pd.DataFrame,
    driver: str,
    lap: int,
    corner: int,
    deviation: dict[int, tuple[bool, float]] | None = None,
    nearby: tuple[int, ...] = (),
) -> RaceControl:
    """A track-limits deletion or a race-control incident naming this driver at this turn, or
    else at one of the `nearby` turns (those whose apex lies inside this corner's window,
    nearest first): at a chicane race control names one turn, and the other turn's window
    covers the same moment.

    Track limits are matched on the exact lap. Incidents are mapped to the lap in progress
    when race control posted them, which can be a lap late: an incident posted during lap E
    belongs to lap E - 1 or E, whichever deviated more from usual at that turn (`deviation`:
    lap number -> (whether the detectors score that lap, absolute time lost there); a scored
    lap beats an out-lap, in-lap or safety-car lap, which are slow for reasons of their own;
    without it, both). Leaving the track counts as track limits; collisions and the rest as an
    incident. A message naming two cars ("INCIDENT INVOLVING CARS 10 (GAS) AND 18 (STR) NOTED -
    FORCING ANOTHER DRIVER OFF THE TRACK") doesn't say which driver made the mistake, so it
    comes back as "involved": context for both, a mistake for neither.
    """
    if events.empty:
        return RaceControl("", "")
    mine = events[events["driver"] == driver]
    for turn in (corner, *(t for t in nearby if t != corner)):
        rc = _race_control_at_turn(mine[mine["turn"] == turn], lap, driver, deviation)
        if rc.kind:
            return rc if turn == corner else replace(rc, turn=int(turn))
    return RaceControl("", "")


def _race_control_at_turn(
    here: pd.DataFrame, lap: int, driver: str, deviation: dict[int, tuple[bool, float]] | None
) -> RaceControl:
    """`race_control` for the events of one turn."""
    limits = here[(here["kind"] == "track_limits") & (here["lap_number"] == lap)]
    if len(limits):
        record = limits.iloc[0]
        return RaceControl("track_limits", str(record["message"]), named_here=_named(record))
    records = here[here["kind"] == "incident"].to_dict("records")
    for record in sorted(records, key=lambda r: len(_cars(str(r["message"]))) > 1):
        posted = int(record["lap_number"])
        candidates = [n for n in (posted - 1, posted) if deviation is None or n in deviation]
        if deviation is not None and candidates:
            candidates = [max(candidates, key=lambda n: deviation[n])]
        if lap not in candidates:
            continue
        message = str(record["message"])
        others = tuple(dict.fromkeys(c for c in _cars(message) if c != driver))
        if others:
            return RaceControl("involved", message, others, _named(record))
        kind = "track_limits" if any(k in message for k in LEFT_TRACK) else "incident"
        return RaceControl(kind, message, named_here=_named(record))
    return RaceControl("", "")


def _named(record: pd.Series | dict) -> int:
    value = record.get("drivers_same_lap_turn", 1)
    return int(value) if pd.notna(value) else 1


def _nearest_median(
    rows: np.ndarray, pool: np.ndarray, laps: np.ndarray, times: np.ndarray, k: int | None
) -> tuple[np.ndarray, np.ndarray]:
    """For each of `rows`: the median time of the `k` rows of `pool` nearest in lap number
    (all of them when k is None), itself left out, ties to the earlier lap as in
    `reference_rows`; and how many pool rows it had to choose from."""
    if not len(pool):
        return np.full(len(rows), np.nan), np.zeros(len(rows), dtype=int)
    dist = np.abs(laps[rows][:, None] - laps[pool][None, :])
    dist[rows[:, None] == pool[None, :]] = np.inf
    available = np.isfinite(dist).sum(axis=1)
    order = np.argsort(dist * 10_000 + laps[pool][None, :], axis=1, kind="stable")
    take = available if k is None else np.minimum(available, k)
    picked = np.where(np.arange(len(pool)) < take[:, None], times[pool][order], np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # rows with nothing to choose from
        return np.nanmedian(picked, axis=1), available


def lap_losses(seg: SessionSegments, clean: np.ndarray) -> np.ndarray:
    """Every segment's time minus the median of its reference laps: the same laps and the same
    number as CornerComparison.time_lost_s (`reference_rows`), for all segments at once (NaN
    without a reference). Used for the lap context and to place race-control incidents.

    A race-long median would make early laps look slow: a corner gets ~0.2 s faster over a race
    as fuel burns off, and against the whole race 5-14% of clean corners in the first third of
    dry races were 0.3 s "slow", against 1-6% against the nearest laps.
    """
    frame = seg.frame
    times = frame["segment_time_s"].to_numpy(float)
    laps = frame["lap_number"].to_numpy(float)
    drivers = frame["driver"].to_numpy()
    own_k = RACE_REFERENCE_LAPS if seg.is_race else None
    field_k = FIELD_REFERENCE_LAPS if seg.is_race else None
    usual = np.full(len(frame), np.nan)
    groups = frame.groupby(["corner", "corner_letter"], sort=False).indices
    for positions in groups.values():
        rows = np.asarray(positions, dtype=int)
        pool = rows[clean[rows]]
        for driver in np.unique(drivers[rows]):
            mine = rows[drivers[rows] == driver]
            median, available = _nearest_median(
                mine, pool[drivers[pool] == driver], laps, times, own_k
            )
            usual[mine] = np.where(available >= MIN_REFERENCE, median, np.nan)
            few = mine[available < MIN_REFERENCE]
            if len(few):
                median, available = _nearest_median(few, pool, laps, times, field_k)
                usual[few] = np.where(available >= MIN_REFERENCE, median, np.nan)
    return times - usual


@dataclass(frozen=True)
class LapContext:
    notes: list[str]  # traffic and lap class, for the sentence
    race: bool
    slow_after: tuple[int, int]  # (slow, all) corners after this one on the lap
    others_slow: int  # in races, other drivers slow at this turn on the same lap (0: too few)
    slowdown: bool  # in a slow stretch of the lap, or slow with the field (see lap_context)
    field_loss_s: float  # with the field slow here: the other drivers' median loss (else NaN)
    previous_lap: bool = False  # the slow stretch before it began on the previous lap


def lap_context(
    seg: SessionSegments,
    row: int,
    losses: np.ndarray,
    arrived_slow: bool = False,
    placed: bool = False,
) -> LapContext:
    """What else was going on: the kind of lap, traffic at the line, a slow stretch of the lap,
    the field slow at the same place. With the cars `placed` on the track (lap grids, see
    traffic_finding), the gap to the car ahead at the line is left to the traffic notes, which
    measure it at the corner itself.

    In races, when at least CLUSTER_OTHERS other drivers, and a third of those at this turn on
    the same lap, were also CLUSTER_SLOW_S slower than usual there, something was on track (a
    yellow flag, debris) or the conditions changed (rain), and it isn't this driver's mistake;
    race-control labels are set aside the same way (`data.labels.mark_clusters`). In dry races
    that happens at under 1% of turns and laps, in wet or drying ones far more often. The other
    drivers' median loss there is what the conditions cost everyone (`field_loss_s`).
    When the two corners before and at least half of the (three or more) corners after this
    one were also slow, or the SLOW_RUN corners before it all were, the lap was abandoned or
    neutralised (a qualifying lap given up, a slow-down for a problem): the flag is part of
    that stretch, not a mistake at this corner. The corners before it run on from the end of
    the previous lap when that lap is lap - 1 and one the detectors score (`_previous_lap`): a
    slow-down that began there (damage, a problem) reaches this lap's first corners, which
    otherwise have too few corners before them on their own lap. Two corners, because an off
    often spans two (wide at one turn, off at the next) before the driver gives up the lap. In
    the last corners of a lap, with fewer than three after it, the two corners before being
    slow and the car arriving slower than usual (`arrived_slow`: the approach-speed rule fired)
    is enough: the slow stretch carried on into this corner. When only the corners after it
    were slow, the lap may have been given up because of this corner.
    """
    frame = seg.frame
    target = frame.iloc[row]
    notes = []
    if target["lap_class"] == "slow":
        notes.append("on a slow lap")
    elif target["lap_class"] not in CLEAN_CLASSES:
        kind = str(target["lap_class"])
        text = LAP_CLASS_TEXT.get(kind, f"a lap classed {kind}")
        notes.append(text if text.startswith("under") else f"on {text}")
    gap = target.get("gap_ahead_s", math.nan)
    if seg.is_race and not placed and pd.notna(gap) and 0 < gap < TRAFFIC_GAP_S:
        behind = f"{gap:.1f} s" if gap >= 0.05 else "right"
        notes.append(f"{behind} behind the car ahead at the start of the lap")
    same_lap = frame["lap_id"].to_numpy() == target["lap_id"]
    apex = frame["apex_m"].to_numpy()
    slow = np.nan_to_num(losses, nan=0.0) > LAP_SLOW_S
    later = same_lap & (apex > target["apex_m"])
    earlier = np.flatnonzero(same_lap & (apex < target["apex_m"]))
    ordered = earlier[np.argsort(apex[earlier])]
    previous = _previous_lap(seg, row)
    ordered = np.concatenate([previous, ordered])

    def slow_before(k: int) -> bool:
        return len(ordered) >= k and bool(slow[ordered[-k:]].all())

    n_after, n_slow = int(later.sum()), int(slow[later].sum())
    slow_after = n_after >= 3 and n_slow >= 0.5 * n_after
    others, cluster, field_loss = 0, False, math.nan
    if seg.is_race:
        here = (
            (frame["corner"].to_numpy() == target["corner"])
            & (frame["corner_letter"].to_numpy() == target["corner_letter"])
            & (frame["lap_number"].to_numpy() == target["lap_number"])
            & (frame["driver"].to_numpy() != target["driver"])
        )
        others = int((np.nan_to_num(losses[here], nan=0.0) > CLUSTER_SLOW_S).sum())
        cluster = others >= CLUSTER_OTHERS and others >= CLUSTER_SHARE * here.sum()
        if cluster:
            field_loss = float(np.nanmedian(losses[here]))
    end_of_lap = n_after < 3 and slow_before(2) and arrived_slow
    stretch = (slow_before(2) and slow_after) or slow_before(SLOW_RUN) or end_of_lap
    # The slow corners before it reach back into the previous lap.
    reaches_back = bool(stretch and len(earlier) < (SLOW_RUN if slow_before(SLOW_RUN) else 2))
    return LapContext(
        notes=notes,
        race=seg.is_race,
        slow_after=(n_slow, n_after),
        others_slow=others if cluster else 0,
        slowdown=stretch or cluster,
        field_loss_s=field_loss,
        previous_lap=reaches_back,
    )


def _previous_lap(seg: SessionSegments, row: int) -> np.ndarray:
    """The rows of the driver's previous lap, in track order, when it is lap - 1, one the
    detectors score (a green-flag or slow race lap, a qualifying push lap) and complete to its
    last corner, so that its last corners run on into this lap's first ones (none otherwise).
    An out-, in-, neutralised or opening lap is slow for reasons of its own that end with it:
    a push lap after an out-lap, or the restart after a safety car, isn't a slow stretch."""
    frame = seg.frame
    target = frame.iloc[row]
    mine = frame["driver"].to_numpy() == target["driver"]
    rows = np.flatnonzero(mine & (frame["lap_number"].to_numpy() == int(target["lap_number"]) - 1))
    apex = frame["apex_m"].to_numpy(float)
    if not len(rows) or not seg.eligible()[rows].all():
        return np.zeros(0, dtype=int)
    if apex[rows].max() < apex[mine].max():  # its last corners are missing
        return np.zeros(0, dtype=int)
    return rows[np.argsort(apex[rows])]


def _slow_lap_note(
    context: LapContext, primary: str, lost: float, slower: bool, to_flag: bool = False
) -> str:
    n_slow, n_after = context.slow_after
    after = f"{n_slow} of the {n_after} corners after it were slow"
    if context.others_slow and primary in NAMED:
        field = context.field_loss_s
        much = "most" if lost > 0 and field >= MOSTLY * lost else "part"
        return (
            f"{context.others_slow} other drivers were also slow here on the same lap (by "
            f"{field:.2f} s at the median), so the conditions or something on track explain "
            f"{much} of the time lost"
        )
    if context.others_slow:
        return (
            f"{context.others_slow} other drivers were slow here on the same lap too: probably "
            "something on track or the conditions (a yellow flag, debris, rain), not a mistake"
        )
    if primary == "slowdown" and to_flag and not context.slowdown:
        return (
            "the run to the flag: one of the last corners of their final lap, where drivers "
            "ease off to the line, with no car passing or passed there, so probably not a "
            "mistake"
        )
    if primary == "slowdown":
        since = " (from the end of the previous lap on)" if context.previous_lap else ""
        return (
            f"part of a slow stretch of the lap: the corners before it were slow too{since} (an "
            "abandoned lap, a slow-down or a problem), so probably not a mistake here"
        )
    if n_after >= 3 and n_slow >= 0.5 * n_after:
        if not slower:  # this corner didn't cost time, so it didn't start the slow stretch
            return f"{after} (damage, a problem or traffic?)" if context.race else after
        if context.race:
            return f"{after} too (damage, a problem or traffic?)"
        return f"{after} too: the lap may have been given up after this"
    return ""


# ---------------------------------------------------------------------------------------------
# Traffic: the other cars around the corner (inference/traffic.py has the geometry)


@dataclass(frozen=True)
class TrafficFinding:
    """What the cars around a corner say about it (`traffic_finding`)."""

    kind: str  # "lapped", "battle", "impeded" or "" (none of them)
    cars: tuple[str, ...]  # the cars the kind is about
    clause: str  # the kind in words, for the sentence ("" without one)
    notes: tuple[str, ...] = ()  # context: places lost, cars passed, following closely
    start_m: float = math.nan  # the extended window looked at, metres from the apex
    end_m: float = math.nan
    encounters: tuple[Encounter, ...] = ()  # every car that came near (for explain_corner)
    # Cars that would have made the loss traffic but are placed too roughly (see `rough`).
    maybe: tuple[str, ...] = ()
    verdict: str = ""  # how the kind's clause ends in the sentence (TRAFFIC_VERDICT)
    onset_m: float = math.nan  # where the driver's loss began (loss_onset), metres from the apex
    # The placed cars already at the driver when the loss began (`_at_the_driver`: alongside,
    # past, lapping them from close behind, or ahead holding them up): the cars that can
    # explain a race-control flag's loss itself (see `explains`, priority).
    alongside: tuple[str, ...] = ()

    @property
    def explains(self) -> bool:
        """One of the kind's cars was already at the driver when the loss began, so the time lost
        is the traffic's, not the driver's (a race-control flag then ranks on its bonus alone,
        see priority)."""
        return bool(self.kind) and any(c in self.alongside for c in self.cars)


NO_TRAFFIC = TrafficFinding("", (), "")


def loss_onset(cmp: CornerComparison) -> float:
    """Where the driver's loss began: the first point of the window where the running time lost
    (`delta_trace_s`) reaches ONSET_S, metres from the apex (NaN when it never does)."""
    trace = cmp.delta_trace_s
    hit = np.isfinite(trace) & (trace >= ONSET_S)
    return float(DISTANCE[int(np.argmax(hit))]) if hit.any() else math.nan


def _after_the_turn(cmp: CornerComparison) -> np.ndarray:
    """The window from the turn's usual slowest point on (all of it at a turn usually taken flat
    out, which has none)."""
    usual = cmp.usual.get("d_vmin", math.nan)
    if cmp.taken_flat or not np.isfinite(usual):
        return np.ones(len(DISTANCE), dtype=bool)
    return DISTANCE - usual >= 0


def yield_point(seg: SessionSegments, cmp: CornerComparison) -> float:
    """Where the driver braked on a stretch that the usual lap takes on the power (the median
    of the reference laps at YIELD_USUAL_THROTTLE or more, not braking), after the turn's usual
    slowest point: metres from the apex, NaN when they didn't.

    That is how a driver about to be lapped lets the faster car by on the straight after a turn:
    braking where nobody brakes. Braking before the usual slowest point is left out, because it
    looks the same as braking early, a mistake; so is a lift on the power, which looks the same
    as a slow pickup or a traction loss (`yield_lift`, kept for the calibration)."""
    if cmp.reference == "none":
        return math.nan
    brake = seg.traces["brake"][cmp.row]
    on_power = (cmp.usual_trace["throttle"] >= YIELD_USUAL_THROTTLE) & (
        cmp.usual_trace["brake"] <= BRAKE_ON
    )
    hit = _after_the_turn(cmp) & on_power & (brake > BRAKE_ON)
    return _costly(cmp, hit)


def _costly(cmp: CornerComparison, hit: np.ndarray) -> float:
    """The first point of `hit`, metres from the apex, if the running time lost grew by
    ONSET_S or more from there to the end of the window (NaN otherwise): easing off there cost
    time, unlike a dab of the brake or braking for the next turn a few metres early."""
    if not hit.any():
        return math.nan
    i = int(np.argmax(hit))
    trace = cmp.delta_trace_s
    return float(DISTANCE[i]) if trace[-1] - trace[i] >= ONSET_S else math.nan


def yield_lift(seg: SessionSegments, cmp: CornerComparison) -> float:
    """As `yield_point`, for a lift instead of braking: after the turn's usual slowest point and
    back on full throttle, the throttle YIELD_LIFT or more below the usual lap's where that is
    on the power (NaN when it never is). Not a rule (see yield_point); calibrate_traffic counts
    it."""
    if cmp.reference == "none":
        return math.nan
    throttle = seg.traces["throttle"][cmp.row]
    after = _after_the_turn(cmp)
    usual = cmp.usual_trace["throttle"]
    back_on = np.maximum.accumulate(np.where(after, throttle, 0.0)) >= THROTTLE_FULL
    on_power = (usual >= YIELD_USUAL_THROTTLE) & (cmp.usual_trace["brake"] <= BRAKE_ON)
    hit = after & back_on & on_power & (throttle <= usual - YIELD_LIFT)
    return _costly(cmp, hit)


def _yielded(e: Encounter, yield_m: float) -> bool:
    """The driver braked where the usual lap is on the power (`yield_point`) with this car at
    least a lap ahead within YIELD_GAP_S behind before that point, and it went past from there
    on: letting it by."""
    if not np.isfinite(yield_m) or e.laps_ahead < 1 or e.passed != "by":
        return False
    gap, _ = e.closest_before(yield_m)
    return bool(e.pass_m >= yield_m and 0 < gap <= YIELD_GAP_S)


def _lapping(e: Encounter, onset_m: float = math.nan, yield_m: float = math.nan) -> bool:
    """A car at least a lap ahead that went past in the extended window, and was already close
    when the driver's loss began: it passed before the onset (`onset_m`), or it was within
    LAPPED_GAP_S behind then (`Encounter.gap_before_loss`), or the driver braked to let it by
    (`_yielded`). The driver was being lapped."""
    if e.laps_ahead < 1 or e.passed != "by":
        return False
    gap, _ = e.gap_before_loss()
    return e.passed_before(onset_m) or 0 < gap <= LAPPED_GAP_S or _yielded(e, yield_m)


def _slowed(e: Encounter, bar_s: float) -> bool:
    """The car was itself slower than usual through the corner, by the corner's bar for "slower
    than usual" (`bar_s`)."""
    return bool(np.isfinite(e.usual_window_s) and e.window_s >= e.usual_window_s + bar_s)


def _battling(e: Encounter, onset_m: float = math.nan, bar_s: float = math.inf) -> bool:
    """A car on the same lap, already within BATTLE_GAP_S when the extended window began, that
    passed or was passed in it, or came alongside in the corner's own window. A car that went
    past the driver had to be in the fight before the loss: alongside (within ALONGSIDE_S) when
    the driver's loss began (`onset_m`), past already, or slowed itself through the corner
    (`_slowed`, by `bar_s`); otherwise the driver lost the time and the car took its chance."""
    close = e.laps_ahead == 0 and abs(e.gap_start_s) <= BATTLE_GAP_S
    if not close:
        return False
    if e.passed == "by" and np.isfinite(onset_m):
        gap, _ = e.gap_before_loss()
        return abs(gap) <= ALONGSIDE_S or e.passed_before(onset_m) or _slowed(e, bar_s)
    return bool(e.passed != "" or e.min_gap_s <= SIDE_BY_SIDE_S)


def _impeding(e: Encounter, own_s: float, usual_s: float, bar_s: float) -> bool:
    """A car ahead of the driver from the window start through the apex, which they came
    within IMPEDE_GAP_S of, that was slower through the corner than the driver usually is (by
    the corner's own bar for "slower than usual", `bar_s`), while the driver was no slower than
    it (dropping back IMPEDE_DROP_S at most): it set the driver's pace, or the driver had to get
    past it. `own_s` and `usual_s` are the driver's time through the corner's window and their
    usual time."""
    close = np.isfinite(e.behind_s) and e.behind_s <= IMPEDE_GAP_S
    slow = e.window_s >= usual_s + bar_s
    return bool(close and slow and own_s - e.window_s <= IMPEDE_DROP_S)


def _at_the_driver(e: Encounter, onset_m: float) -> bool:
    """The car was already at the driver when the loss began (`onset_m`), so it can explain
    the loss of a flag race control named (TrafficFinding.alongside, priority): it had passed
    them or been passed, it was alongside (a same-lap car within ALONGSIDE_S), it was lapping
    them from within YIELD_START_S behind, or it was ahead holding them up (within
    IMPEDE_GAP_S from the window start through the apex)."""
    gap, _ = e.gap_before_loss()
    lapping = e.laps_ahead >= 1 and 0 < gap <= YIELD_START_S
    held = bool(np.isfinite(e.behind_s) and e.behind_s <= IMPEDE_GAP_S)
    return e.passed_before(onset_m) or abs(gap) <= ALONGSIDE_S or lapping or held


def _names(names: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _laps_ahead(n: int) -> str:
    if n >= 0:
        return "a lap ahead" if n == 1 else f"{n} laps ahead"
    return "a lap down" if n == -1 else f"{-n} laps down"


def _where(pass_m: float) -> str:
    """Where along the corner a pass happened, in words."""
    if pass_m < DISTANCE[0]:
        return "before the corner"
    if pass_m < PHASE_EDGES_M[0]:
        return "on the way in"
    if pass_m <= PHASE_EDGES_M[1]:
        return "through the corner"
    return "on the way out" if pass_m <= DISTANCE[-1] else "after the corner"


def _at(m: float) -> str:
    """A point of the window in words: "150 m before the apex", "at the apex", "30 m after"."""
    if abs(m) < 5:
        return "at the apex"
    return f"{abs(m):.0f} m {'before' if m < 0 else 'after'} the apex"


def _who(e: Encounter, race: bool) -> str:
    """The other car's lap, as the impeded clause names it: "on its out lap", "a lap down"."""
    text = LAP_CLASS_TEXT.get(e.lap_class, "")
    own = {"in": "on its in lap", "out": "on its out lap"}.get(e.lap_class, "")
    if not race:
        return own or (f"on {text}" if text.startswith(("a ", "an ", "the ")) else text)
    lap = _laps_ahead(e.laps_ahead) if e.laps_ahead else "on the same lap"
    return f"{own}, {lap}" if own else lap


def _car_list(cars: Sequence[Encounter], race: bool = True) -> str:
    """ "LEC and HAM (2 laps ahead)", "PIA (2 laps ahead) and GAS (a lap ahead)"."""
    laps = {e.laps_ahead for e in cars}
    if not race or laps == {0}:
        return _names([e.driver for e in cars])
    if len(laps) == 1:
        return f"{_names([e.driver for e in cars])} ({_laps_ahead(cars[0].laps_ahead)})"
    return _names([f"{e.driver} ({_laps_ahead(e.laps_ahead)})" for e in cars])


def _gap_range(
    cars: Sequence[Encounter], onset_m: float = math.nan, point_m: float = math.nan
) -> str:
    """How far behind the cars were when the driver's loss began, each where it was measured
    (`Encounter.gap_before_loss`; for a car that passed before the loss began, its start gap;
    with `point_m`, the closest before that point instead):
    "1.2 s behind 150 m before the apex, where the loss began", "2.6-8.8 s behind 450 m before
    the apex", "PER 0.1 s behind 410 m before the apex, RUS 1.9 s behind 450 m before the
    apex". Cars without a gap are left out ("" when none has one)."""

    def gap(e: Encounter) -> tuple[float, float]:
        if np.isfinite(point_m):
            return e.closest_before(point_m)
        if e.passed_before(onset_m):  # the closest it came is the pass itself: say from where
            return e.gap_start_s, e.gap_start_m
        return e.gap_before_loss()

    measured = [(e, *gap(e)) for e in cars]
    measured = [(e, g, m) for e, g, m in measured if np.isfinite(g)]
    if not measured:
        return ""

    def how(g: float) -> str:
        return f"{abs(g):.1f} s {'behind' if g >= 0 else 'ahead'}"

    if len({round(m) for _, _, m in measured}) > 1:
        return ", ".join(f"{e.driver} {how(g)} {_at(m)}" for e, g, m in measured)
    gaps = sorted(g for _, g, _ in measured)
    where = measured[0][2]
    if len(gaps) == 1 or f"{gaps[0]:.1f}" == f"{gaps[-1]:.1f}":
        span = how(gaps[0])
    elif gaps[0] >= 0 or gaps[-1] < 0:
        span = f"{abs(gaps[0]):.1f}-{abs(gaps[-1]):.1f} s {'behind' if gaps[0] >= 0 else 'ahead'}"
        if gaps[-1] < 0:
            span = f"{abs(gaps[-1]):.1f}-{abs(gaps[0]):.1f} s ahead"
    else:
        span = f"{how(gaps[0])} to {how(gaps[-1])}"
    at_onset = np.isfinite(onset_m) and abs(where - onset_m) < 1
    return f"{span} {_at(where)}" + (", where the loss began" if at_onset else "")


def _lapped_text(lapped: Sequence[Encounter], onset_m: float, yield_m: float) -> str:
    """The lapping cars (in the order they went past) in words, grouped by what made each one
    traffic: it passed before the loss began, it was within LAPPED_GAP_S behind then, or the
    driver braked to let it by (`_lapping`)."""

    def why(e: Encounter) -> str:
        if e.passed_before(onset_m):
            return "early"
        return "close" if 0 < e.gap_before_loss()[0] <= LAPPED_GAP_S else "yield"

    groups: list[tuple[str, list[Encounter]]] = []
    for e in lapped:
        if groups and groups[-1][0] == why(e):
            groups[-1][1].append(e)
        else:
            groups.append((why(e), [e]))
    texts = []
    for reason, part in groups:
        where = _where(part[0].pass_m)
        if reason == "early":
            texts.append(f"{_car_list(part)} went past {where}, before the loss began")
        elif reason == "close":
            gaps = _gap_range(part, onset_m)
            texts.append(
                f"{_car_list(part)}" + (f", {gaps}," if gaps else "") + f" went past {where}"
            )
        else:
            gaps = _gap_range(part, point_m=yield_m)
            points = {round(e.closest_before(yield_m)[1]) for e in part}
            cars = gaps if len(points) > 1 else f"{_car_list(part)} {gaps}"
            texts.append(
                f"braked {_at(yield_m)}, where the usual lap is on the power, with "
                f"{cars.removesuffix(' ' + _at(yield_m))}, and "
                f"{'it' if len(part) == 1 else 'they'} went past {where}"
            )
    return ", then ".join(texts)


def _closeness(e: Encounter, onset_m: float) -> str:
    """How close one car was when the driver's loss began (`_gap_range` for one car)."""
    return _gap_range([e], onset_m)


def traffic_finding(
    around: Surroundings | None,
    race: bool,
    own_s: float,
    usual_s: float,
    bar_s: float,
    yield_m: float = math.nan,
) -> TrafficFinding:
    """What the cars around a corner explain (`traffic.surroundings`; NO_TRAFFIC without it).

    Only a car that was already close before the driver's loss began makes the loss traffic.
    Where the loss began is `around.onset_m` (loss_onset), and how close each car was then is
    the closest it came between the start of the extended window and that point
    (`Encounter.gap_before_loss`; without an onset, the gap at the start of the window). In
    races, in this order: **lapped** (a car at least a lap ahead went past, after passing
    before the onset or from within LAPPED_GAP_S behind then; or the driver braked where the
    usual lap is on the power, `yield_m` from yield_point, with it within YIELD_GAP_S behind,
    and it went past from there: the driver let it by, under blue flags), **battle** (a car on
    the same lap already within BATTLE_GAP_S when the extended window began passed or was
    passed in it, or came alongside, within SIDE_BY_SIDE_S, in the corner's own window; a car
    that went past only when it was alongside within ALONGSIDE_S as the loss began, past
    already, or slowed itself, `_battling`) and, in any session, **impeded** (a car stayed
    ahead from the window start through the apex with the driver within IMPEDE_GAP_S behind
    it, it was slower through the corner than the driver usually is, and the driver no slower
    than it: it set their pace, typically a car on an out- or in-lap in qualifying, or they had
    to get past it; `_impeding`). `own_s`, `usual_s` and `bar_s` are the driver's time through
    the window, their usual time and the corner's bar for "slower than usual", in seconds.

    When a car that was further back went past, the driver lost the time some other way (a
    mistake, a problem) and the car took its chance: that stays the driver's, and the pass is
    context ("lost the place to X"). So is following a car within FOLLOW_GAP_S at the start of
    the corner (its dirty air), and passing a car that wasn't close.

    A car whose position there (or the driver's) is `rough`, placed from sector times on a pit,
    slow or neutralised lap, never makes the loss traffic: such positions were 1.2-6.7 s out
    where they were checked against the raw position feed, far more than the gaps the rules
    use (traffic.py). When it would have, the sentence says it may have been in the way.
    """
    if around is None:
        return NO_TRAFFIC
    enc = around.encounters
    onset = around.onset_m
    placed = [e for e in enc if not e.rough]
    back = f"{-around.start_m:.0f} m before the apex"
    lapped = sorted(
        (e for e in placed if race and _lapping(e, onset, yield_m)), key=lambda e: e.pass_m
    )
    battle = sorted(
        (e for e in placed if race and _battling(e, onset, bar_s)),
        key=lambda e: abs(e.gap_start_s),
    )
    fighting = {e.driver for e in battle}
    impeded = sorted(
        (e for e in placed if e.driver not in fighting and _impeding(e, own_s, usual_s, bar_s)),
        key=lambda e: e.behind_s,
    )
    kind, cars, clause, verdict = "", [], "", ""
    if lapped:
        kind, cars, verdict = "lapped", lapped, TRAFFIC_VERDICT["lapped"]
        clause = "being lapped: " + _lapped_text(lapped, onset, yield_m)
    elif battle:
        kind, cars, verdict = "battle", battle, TRAFFIC_VERDICT["battle"]
        e = battle[0]
        if e.passed == "by":
            how = f"{e.driver}, {_closeness(e, onset) or 'close'}, went past {_where(e.pass_m)}"
            gap, _ = e.gap_before_loss()
            if e.passed_before(onset) and np.isfinite(onset):
                how += ", before the loss began"
            elif np.isfinite(onset) and abs(gap) > ALONGSIDE_S and _slowed(e, bar_s):
                how += (
                    f", slowed too ({e.window_s:.1f} s through the corner against its usual "
                    f"{e.usual_window_s:.1f} s)"
                )
        elif e.passed == "of":
            how = f"passed {e.driver} {_where(e.pass_m)}, from {-e.gap_start_s:.1f} s behind {back}"
        else:
            order = "behind" if e.gap_start_s > 0 else "ahead"
            swapped = ", swapping places and back" if e.swapped_back else ""
            how = (
                f"side by side with {e.driver} ({e.min_gap_s:.1f} s apart at the closest"
                f"{swapped}), who was {abs(e.gap_start_s):.1f} s {order} {back}"
            )
        others = [x.driver for x in battle[1:]]
        clause = f"racing {e.driver}: {how}" + (
            f", with {_names(others)} close too" if others else ""
        )
    elif impeded:
        kind, cars = "impeded", impeded[:1]
        e = impeded[0]
        estimate = " (its position estimated from its sector times)" if e.approximate else ""
        # They passed it: the positions say so, or they took less time through the window
        # than it by more than the gap they were behind it.
        got_past = (e.passed == "of" and e.pass_m <= DISTANCE[-1]) or (
            own_s < e.window_s - max(e.behind_s, IMPEDE_DROP_S)
        )
        verdict = TRAFFIC_VERDICT["impeded_passed" if got_past else "impeded"]
        then = ", until they got past it" if got_past else ""
        clause = (
            f"held up by {e.driver} ({_who(e, race)}): {e.behind_s:.1f} s behind it at the "
            f"closest on the way in{then}, and it took {e.window_s:.1f} s through the corner "
            f"against this driver's usual {usual_s:.1f} s{estimate}"
        )
    named = {e.driver for e in cars}
    rest = [e for e in placed if e.driver not in named]
    order = sorted(rest, key=lambda e: e.pass_m if np.isfinite(e.pass_m) else math.inf)
    notes = []
    maybe = [
        e
        for e in enc
        if e.rough
        and (
            (race and (_lapping(e, onset, yield_m) or _battling(e, onset, bar_s)))
            or _impeding(e, own_s, usual_s, bar_s)
        )
    ]
    if maybe and not kind:
        who = (
            _car_list(maybe)
            if race
            else _names(
                [f"{e.driver} ({_who(e, race)})" if _who(e, race) else e.driver for e in maybe]
            )
        )
        notes.append(
            f"{who} may have been in the way, but the positions there come only from sector "
            "times on a pit or slow lap, too rough to count as traffic"
        )
    if race:
        lost = [e for e in order if e.passed == "by" and e.laps_ahead == 0]
        went = [e for e in order if e.passed == "by" and e.laps_ahead != 0]
        passed = [e for e in order if e.passed == "of"]
        if lost:
            gaps = _gap_range(lost, onset)
            places = "the place" if len(lost) == 1 else "places"
            notes.append(f"lost {places} to {_car_list(lost)}" + (f" ({gaps})" if gaps else ""))
        if went:
            gaps = _gap_range(went, onset)
            also = " too" if kind == "lapped" else ""
            notes.append(
                f"{_car_list(went)}" + (f", {gaps}," if gaps else "") + f" went past{also}"
            )
        if passed:
            notes.append(f"passed {_car_list(passed)}")
    ahead = [e for e in rest if not e.passed and -FOLLOW_GAP_S <= e.gap_window_s < 0]
    if ahead:
        e = max(ahead, key=lambda e: e.gap_window_s)
        notes.append(
            f"following {e.driver} closely ({-e.gap_window_s:.1f} s behind at the start of the "
            "corner)"
        )
    alongside = [e.driver for e in placed if _at_the_driver(e, onset)]
    return TrafficFinding(
        kind=kind,
        cars=tuple(e.driver for e in cars),
        clause=clause,
        notes=tuple(notes),
        start_m=around.start_m,
        end_m=around.end_m,
        encounters=tuple(enc),
        maybe=tuple(e.driver for e in maybe) if not kind else (),
        verdict=verdict,
        onset_m=onset,
        alongside=tuple(alongside),
    )


NEARBY_S = 2.0  # explain_corner lists the cars this close in the corner's window, or passing


def cars_around_text(cars: TrafficFinding, race: bool) -> str:
    """The cars around the corner in one line, for explain_corner ("" when none was near)."""
    near = [
        e
        for e in cars.encounters
        if e.passed or (np.isfinite(e.min_gap_s) and e.min_gap_s <= NEARBY_S)
    ]
    if not near:
        return ""
    onset = cars.onset_m
    items = []
    for e in sorted(near, key=lambda e: e.gap_start_s if np.isfinite(e.gap_start_s) else math.inf):
        text = f"{e.driver} " + (
            f"{e.gap_start_s:+.1f} s" if np.isfinite(e.gap_start_s) else "no position"
        )
        if np.isfinite(e.gap_start_s) and abs(e.gap_start_m - cars.start_m) >= 1:
            text += f" ({_at(e.gap_start_m)})"
        if e.passed_before(onset):
            text += " / past by then"
        elif np.isfinite(onset):
            text += f" / {e.gap_onset_s:+.1f} s" if np.isfinite(e.gap_onset_s) else " / none"
        if race and e.laps_ahead:
            text += f" ({_laps_ahead(e.laps_ahead)})"
        if e.passed == "by":
            text += f", went past {_where(e.pass_m)}"
        elif e.passed == "of":
            text += f", passed {_where(e.pass_m)}"
        elif np.isfinite(e.min_gap_s):
            text += f", {e.min_gap_s:.1f} s at the closest in the corner"
        if e.rough:
            text += "*"
        elif e.approximate:
            text += " (position estimated from sector times)"
        items.append(text)
    rough = any(e.rough for e in near)
    when = f"the gap {-cars.start_m:.0f} m before the apex"
    if np.isfinite(onset):
        when += f" / the closest it came before the loss began, {_at(onset)}"
    return (
        f"Cars around ({when}; + behind, - ahead): "
        + "; ".join(items)
        + "."
        + (
            " * Placed only roughly, from sector times on a pit or slow lap: not counted as "
            "traffic."
            if rough
            else ""
        )
    )


def lap_deficit(tracks: SessionTracks | None, target: pd.Series) -> tuple[float, float]:
    """How much slower than their usual push lap the driver was where the corner's window
    starts, and the usual time to get there (`traffic.lap_deficit`; NaN in races, without
    tracks, or without another push lap to compare with)."""
    if tracks is None or target["session"] in RACE_CODES:
        return math.nan, math.nan
    start = float(target["apex_m"]) + float(DISTANCE[0])
    return traffic.lap_deficit(tracks, str(target["driver"]), int(target["lap_number"]), start)


def not_push_lap(deficit_s: float, usual_s: float) -> bool:
    """In qualifying, the lap was already clearly slow before the corner: at least PREP_SHARE
    slower than the driver's usual push lap where the window starts, PREP_MIN_ELAPSED_S or more
    into the lap. A preparation lap between an out-lap and the push lap, a lap given up or a
    problem; the losses are on the corners before this one, so a lap given up after a mistake
    here keeps the mistake."""
    return bool(usual_s >= PREP_MIN_ELAPSED_S and deficit_s >= PREP_SHARE * usual_s)


def run_to_flag(
    seg: SessionSegments, row: int, tracks: SessionTracks | None, around: Surroundings | None
) -> bool:
    """In races: the corner is one of the FLAG_RUN_CORNERS last turns of the lap on the lap
    the driver took the chequered flag (`traffic.chequered_lap`: not a lap they retired on),
    and no placed car passed them or was passed by them there (nor swapped places and back).
    Drivers ease off on the run to the line, so a loss there isn't a mistake; a place won or
    lost there is a moment of its own. Race control naming the driver comes first anyway
    (explain). Without tracks the finish isn't known: False."""
    if tracks is None or not seg.is_race:
        return False
    frame = seg.frame
    target = frame.iloc[row]
    if traffic.chequered_lap(tracks, str(target["driver"])) != int(target["lap_number"]):
        return False
    turns = frame.groupby(["corner", "corner_letter"])["apex_m"].median().sort_values()
    if float(target["apex_m"]) < float(turns.iloc[-FLAG_RUN_CORNERS:].min()) - 1:
        return False
    cars = [] if around is None else [e for e in around.encounters if not e.rough]
    return not any(e.passed or e.swapped_back for e in cars)


def restart_note(tracks: SessionTracks | None, target: pd.Series) -> str:
    """In races: the first racing lap after a safety car, a virtual safety car or a red flag
    (the previous lap was neutralised), when the field is bunched up ("" otherwise). After a
    red flag the cars leave the pit lane for the grid and restart from there: the lap before
    the restart is that out-lap, and the red flag is on the lap before it."""
    if tracks is None or target["session"] not in RACE_CODES:
        return ""
    driver, lap = str(target["driver"]), int(target["lap_number"])
    before = traffic.lap_info(tracks, driver, lap - 1)
    if before is None:
        return ""
    if before["pit_out"]:
        stopped = traffic.lap_info(tracks, driver, lap - 2)
        if stopped is not None and "5" in str(stopped["track_status"] or ""):
            return "the first racing lap after a red flag"
    status = str(before["track_status"] or "")
    if before["lap_class"] != "neutralised" and not any(c in status for c in "4567"):
        return ""
    what = "a red flag" if "5" in status else "a safety car" if "4" in status else ""
    return f"the first racing lap after {what or 'a virtual safety car'}"


# ---------------------------------------------------------------------------------------------
# Mistake types and the plain-English explanation


@dataclass(frozen=True)
class Explanation:
    comparison: CornerComparison
    mistake_type: str
    secondary_type: str  # "" when nothing else stands out
    strength: float  # the driving type's adverse z over its threshold (NaN if not from driving)
    phase: str  # entry, apex or exit: where most time was lost ("" when not clearly slower)
    data_issue: str  # "; "-joined data_issues ("" when none)
    race_control: str  # the race-control message naming the driver at that turn ("" when none)
    race_control_turn: str = ""  # the turn it names, when a neighbouring one (see race_control)
    bonus: bool = False  # race control named this driver alone there: see priority
    field_loss_s: float = math.nan  # the field's median loss when it was slow there too
    context: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # features behind the types, strongest first
    sentence: str = ""
    # What the cars around the corner show (traffic_finding), even when another type wins (a
    # race-control type, a data problem, not slower): then it is context, and when race control
    # named the driver, the time lost doesn't count towards the ranking (see priority).
    traffic: TrafficFinding = NO_TRAFFIC
    type_without_traffic: str = ""  # the type without the traffic rules (the report compares)
    lap_deficit_s: float = math.nan  # qualifying: behind the usual push lap at the window start
    lap_usual_s: float = math.nan  # and the usual time to get there (see not_push_lap)


def is_slower(cmp: CornerComparison, thresholds: dict[str, float]) -> bool:
    """Slower than usual beyond the lap-to-lap noise (see the module docstring)."""
    return bool(cmp.adverse("segment_time_s") >= thresholds["segment_time_s"])


def coasting_explains(cmp: CornerComparison, thresholds: dict[str, float]) -> bool:
    """Coasting fired, nothing in the braking was adverse, and the time lost is no more than
    that much extra coasting costs (COAST_COST_S_PER_M): the corner was slow because the driver
    lifted and coasted, and the slower speeds and later throttle are its effects."""
    coast = cmp.adverse("coast_m") >= thresholds["coast_m"]
    affordable = cmp.time_lost_s <= COAST_COST_S_PER_M * max(cmp.delta["coast_m"], 0.0)
    braking = any(cmp.adverse(f) >= thresholds[f] for f in BRAKING)
    return bool(coast) and bool(affordable) and not braking


def carried_in(cmp: CornerComparison, thresholds: dict[str, float]) -> bool:
    """The car arrived slower than usual (the approach-speed rule fired) and lost most of the
    time on entry, before the turn-in: the loss was carried in from before the corner, and the
    slower speeds and later throttle after it follow from arriving slow."""
    arrived_slow = cmp.adverse("v_start") >= thresholds["v_start"]
    lost = cmp.time_lost_s
    return bool(arrived_slow and lost > 0 and cmp.phase_loss_s[0] > MOSTLY * lost)


def driving_types(
    cmp: CornerComparison, thresholds: dict[str, float]
) -> list[tuple[str, float, str]]:
    """(type, strength, strongest feature) for each rule that fires, strongest first; a slow
    approach comes after the rest, because it was inherited from before the corner and only
    explains a flag when nothing in the corner itself stands out, unless the loss was carried
    in (`carried_in`): then it comes first, whatever else the corner shows.

    Over-slowing needs a braking zone at that turn (most reference laps brake for it): in a
    corner usually taken without braking, a lower minimum speed comes from lifting. Coasting
    more than usual is lift-and-coast only when coasting explains the corner
    (`coasting_explains`), and then it is the only type: its effects are not separate mistakes,
    and in 2026 it is mostly energy management. When the loss is more than that much coasting
    costs, or the braking was adverse too, something else happened, and the other rules type it
    (unclear when none fires).
    """
    fired = []
    for kind, features in TYPE_RULES.items():
        if kind == "over_slowing" and not cmp.braking_zone:
            continue
        ratios = {f: cmp.adverse(f) / thresholds[f] for f in features}
        ratios = {f: r for f, r in ratios.items() if np.isfinite(r)}
        if ratios:
            best = max(ratios, key=lambda f: ratios[f])
            if ratios[best] >= 1:
                fired.append((kind, ratios[best], best))
    if coasting_explains(cmp, thresholds):  # early braking can't have fired either
        fired = [item for item in fired if item[0] not in COAST_EFFECTS]
    else:
        fired = [item for item in fired if item[0] != "lift_and_coast"]
    approach = 0 if carried_in(cmp, thresholds) else 2
    return sorted(fired, key=lambda item: (approach if item[0] == "slow_approach" else 1, -item[1]))


def phase_of(cmp: CornerComparison, slower: bool) -> str:
    if not slower or not np.isfinite(cmp.time_lost_s):
        return ""
    return PHASES[int(np.argmax(cmp.phase_loss_s))]


def _speed(d: float, where: str) -> str:
    return f"{abs(d):.0f} km/h {'slower' if d < 0 else 'faster'} {where}"


def feature_phrase(feature: str, value: float, delta: float) -> str:
    """How one feature differed from usual, in words and the feature's own units."""
    d = delta
    more = "more" if d > 0 else "less"
    match feature:
        case "v_start":
            return _speed(d, "on the approach")
        case "v_min":
            return _speed(d, "at the slowest point")
        case "v_exit":
            return _speed(d, "at the exit")
        case "brake_start_d":
            return f"braked {abs(d):.0f} m {'earlier' if d < 0 else 'later'} than usual"
        case "brake_len_m":
            return f"{abs(d):.0f} m {more} braking"
        case "decel_max":
            return f"braking {abs(d) / G:.1f} g {'harder' if d > 0 else 'softer'} at its peak"
        case "throttle_pickup_d":
            if d > 0 and value >= DISTANCE[-1]:
                return f"not back on full throttle by {DISTANCE[-1]:.0f} m after the apex"
            when = "later" if d > 0 else "earlier"
            return f"back on full throttle {abs(d):.0f} m {when} than usual"
        case "throttle_dip":
            if d > 0:
                return f"lifted {value:.0f}% of throttle after getting back on the power"
            return f"{abs(d):.0f}% less throttle lift after the apex"
        case "coast_m":
            return f"{abs(d):.0f} m {more} coasting (off both pedals)"
        case "exit_accel":
            return f"{abs(d):.1f} m/s² {more} acceleration out of the corner"
        case "offset_exit_max":
            off = "further off" if d > 0 else "closer to"
            return f"{abs(d):.1f} m {off} the usual line on exit"
    return f"{feature} {d:+.1f}"


def _evidence(
    cmp: CornerComparison, types: list[str], thresholds: dict[str, float], limit: int = 3
) -> list[str]:
    """The features behind the given types that crossed their thresholds, strongest first."""
    features = [f for t in types for f in (*TYPE_RULES.get(t, ()), *SUPPORTING.get(t, ()))]
    ratios = {f: cmp.adverse(f) / thresholds[f] for f in dict.fromkeys(features)}
    ranked = sorted((f for f, r in ratios.items() if r >= 1), key=lambda f: -ratios[f])
    return ranked[:limit]


def _largest(
    cmp: CornerComparison, limit: int = 2, min_z: float = 2.0, adverse_only: bool = False
) -> list[str]:
    """The biggest deviations (only adverse ones if asked), for corners no rule explains."""
    size = {f: cmp.adverse(f) if adverse_only else abs(cmp.z[f]) for f in QUOTABLE}
    size = {f: v for f, v in size.items() if np.isfinite(v)}
    return [f for f in sorted(size, key=lambda f: -size[f]) if size[f] >= min_z][:limit]


def _race_control_at(
    seg: SessionSegments, row: int, events: pd.DataFrame | None, losses: np.ndarray
) -> RaceControl:
    """`race_control` for one segment, weighing the driver's laps at that turn, with the turns
    whose apex lies inside its window as the nearby ones."""
    if events is None:
        return RaceControl("", "")
    frame = seg.frame
    target = frame.iloc[row]
    mine = np.flatnonzero(
        (frame["driver"] == target["driver"])
        & (frame["corner"] == target["corner"])
        & (frame["corner_letter"] == target["corner_letter"])
    )
    laps, eligible = frame["lap_number"].to_numpy(), seg.eligible()
    deviation = {
        int(laps[i]): (bool(eligible[i]), abs(float(np.nan_to_num(losses[i])))) for i in mine
    }
    turns = frame[["corner", "apex_m"]].drop_duplicates()
    ahead = turns["apex_m"].to_numpy(float) - float(target["apex_m"])
    inside = turns[(ahead >= DISTANCE[0]) & (ahead <= DISTANCE[-1])]
    nearest = inside.assign(d=np.abs(inside["apex_m"] - float(target["apex_m"]))).sort_values("d")
    nearby = tuple(dict.fromkeys(int(c) for c in nearest["corner"]))
    return race_control(
        events,
        str(target["driver"]),
        int(target["lap_number"]),
        int(target["corner"]),
        deviation,
        nearby,
    )


def explain(
    seg: SessionSegments,
    row: int,
    events: pd.DataFrame | None = None,
    clean: np.ndarray | None = None,
    losses: np.ndarray | None = None,
    thresholds: dict[str, float] | None = None,
    tracks: SessionTracks | None = None,
) -> Explanation:
    """Compare one segment with the driver's usual, give it a type and explain it. `tracks`
    places the other cars (traffic.load_tracks): without them there are no traffic types."""
    thresholds = THRESHOLDS if thresholds is None else thresholds
    clean = clean_mask(seg.frame) if clean is None else clean
    losses = lap_losses(seg, clean) if losses is None else losses
    target = seg.frame.iloc[row]
    turn = turn_label(target["corner"], target["corner_letter"])
    head = f"Lap {int(target['lap_number'])}, turn {turn}"
    cmp = compare(seg, row, clean)
    rc = _race_control_at(seg, row, events, losses)
    named = rc.kind in NAMED
    # Race control naming several drivers at one turn on one race lap: something on track.
    bonus = named and not (seg.is_race and rc.named_here >= CLUSTER_NAMED)
    if cmp.reference == "none":
        parts = ["too few clean laps at this turn to say what is usual"]
        if rc.kind:
            parts.append(_race_control_text(rc, turn))
        kind = rc.kind if named else "no_reference"
        return Explanation(
            comparison=cmp,
            mistake_type=kind,
            secondary_type="",
            strength=math.nan,
            phase="",
            data_issue="",
            race_control=rc.message,
            race_control_turn="" if rc.turn is None else str(rc.turn),
            bonus=bonus,
            sentence=f"{head}: " + "; ".join(parts) + ".",
            type_without_traffic=kind,
        )

    issues = data_issues(seg, cmp)
    fired = driving_types(cmp, thresholds)
    if not _lift_saves(target):  # nothing to save: a lift on a push lap is a mistake
        fired = [("lift_on_push_lap" if k == "lift_and_coast" else k, r, f) for k, r, f in fired]
    driving = [kind for kind, _, _ in fired]
    lost = cmp.time_lost_s
    slower = is_slower(cmp, thresholds)
    # A problem with one channel doesn't hide a loss the timing clock confirms, and none hides
    # race control naming the driver there: leaving the track or contact itself breaks the
    # link between speed and distance that the timing checks rely on.
    doubtful = any(i.kind == "timing" for i in issues) or (bool(issues) and lost < REAL_LOSS_S)
    doubtful = doubtful and not rc.kind
    arrived_slow = bool(cmp.adverse("v_start") >= thresholds["v_start"])
    context = lap_context(seg, row, losses, arrived_slow, placed=tracks is not None)
    onset = loss_onset(cmp)
    around = None
    if tracks is not None:
        around = traffic.surroundings(
            tracks,
            str(target["driver"]),
            int(target["lap_number"]),
            float(target["apex_m"]),
            onset,
        )
    own = float(target["segment_time_s"])
    bar = thresholds["segment_time_s"] * cmp.scale["segment_time_s"]
    cars = traffic_finding(around, seg.is_race, own, own - lost, bar, yield_point(seg, cmp))
    deficit, usual_so_far = lap_deficit(tracks, target)
    prep = not_push_lap(deficit, usual_so_far)
    to_flag = run_to_flag(seg, row, tracks, around)
    # The traffic rules come after race control (a deletion or a penalty is the driver's
    # whatever the traffic; the time lost then doesn't count towards the ranking, see
    # priority) and a data problem (the loss itself is suspect), and before the slow stretch:
    # a driver letting two cars by is slow over several corners, and naming them says more.
    if doubtful:
        primary, rest = "data_problem", driving
    elif named:
        primary, rest = rc.kind, driving
    elif slower and cars.kind:
        primary, rest = cars.kind, driving
    elif slower and prep:
        primary, rest = "not_push_lap", driving
    elif (context.slowdown or to_flag) and slower:
        primary, rest = "slowdown", driving
    elif not slower:
        primary, rest = "no_time_lost", driving
    elif driving:
        primary, rest = driving[0], driving[1:]
    else:
        primary, rest = "unclear", []
    base = primary
    if primary in (*TRAFFIC, "not_push_lap"):
        slow = context.slowdown or to_flag
        base = "slowdown" if slow else (driving[0] if driving else "unclear")
    secondary = next((k for k in rest if k != primary), "")
    strength = next((s for kind, s, _ in fired if kind in (primary, secondary)), math.nan)
    if driving[:1] in (["lift_and_coast"], ["lift_on_push_lap"]):
        # The coasting first, then what it did to the speeds.
        effects = [f for f in ("v_min", "v_exit") if cmp.adverse(f) >= thresholds[f]]
        evidence = ["coast_m", *effects]
    else:
        evidence = _evidence(cmp, driving, thresholds)
        if primary == "slow_approach":  # what the corner inherited first
            evidence = ["v_start", *(f for f in evidence if f != "v_start")][:3]
        if evidence and cmp.adverse("coast_m") >= thresholds["coast_m"]:
            evidence = [*evidence, "coast_m"][:3]  # coasting too, though not the explanation
    if not slower:  # not clearly slower: why it looked unusual, whichever way
        evidence = _largest(cmp, limit=3) or evidence
    elif not evidence and primary == "unclear":
        evidence = _largest(cmp, adverse_only=True)
    phase = phase_of(cmp, slower)

    # The sentence: what it cost and where, why (race control, the feed, the other cars), how,
    # and context.
    issue_text = "; ".join(i.text for i in issues)
    if doubtful:
        parts = [f"probably a data problem, not driving: {issue_text}"]
    elif slower:
        share = cmp.phase_loss_s[PHASES.index(phase)] / lost
        where = f"mostly {PHASE_TEXT[phase]}" if share > MOSTLY else "spread through the corner"
        parts = [f"{lost:.2f} s lost, {where}"]
    elif lost >= EVEN_S:
        bar = thresholds["segment_time_s"] * cmp.scale["segment_time_s"]
        whose = "their" if cmp.reference == "driver" else "the field's"
        digits = 3 if f"{lost:.2f}" == f"{bar:.2f}" else 2
        parts = [
            f"{lost:.{digits}f} s slower than usual, but within the spread of {whose} laps here "
            f"(a loss stands out from {bar:.{digits}f} s)"
        ]
    elif lost > -EVEN_S:
        parts = ["no slower than usual"]
    else:
        parts = [f"not slower than usual ({-lost:.2f} s quicker)"]
    if rc.kind:
        parts.append(_race_control_text(rc, turn))
    if rc.kind and seg.is_race and rc.named_here >= CLUSTER_NAMED:
        parts.append(_named_many_text(rc))
    if primary in TRAFFIC:
        parts.append(f"{cars.clause}: {cars.verdict}")
    elif cars.kind and named and slower and cars.explains:
        parts.append(f"{cars.clause}, so the time lost doesn't count towards its ranking")
    elif cars.kind and named and slower:
        parts.append(
            f"{cars.clause}; the loss began before they reached the driver, so it counts towards "
            "its ranking"
        )
    elif cars.kind:
        parts.append(cars.clause)
    if primary == "not_push_lap":
        parts.append(
            f"not a full push lap: already {deficit:.1f} s ({deficit / usual_so_far:.0%}) slower "
            "than their usual push lap by the start of the corner (a preparation lap, a lap "
            "given up or a problem), so not a mistake here"
        )
    details = [] if doubtful else [feature_phrase(f, cmp.values[f], cmp.delta[f]) for f in evidence]
    if details:
        parts.append(" and ".join(details) if len(details) <= 2 else ", ".join(details))
    elif primary == "unclear":
        parts.append("no single feature stands out against their usual laps")
    lock_up = any(cmp.adverse(f) >= thresholds[f] for f in SUPPORTING["over_slowing"])
    if "over_slowing" in (primary, secondary) and lock_up:
        parts.append("with longer or harder braking than usual: a possible lock-up")
    coast_cost = COAST_COST_S_PER_M * max(cmp.delta["coast_m"], 0.0)
    if primary == "unclear" and "coast_m" in evidence and lost > coast_cost:
        # Coasting stood out, but it doesn't explain the loss (see driving_types).
        parts.append(f"that much coasting costs up to {coast_cost:.2f} s: something else happened")
    if primary in ("lift_and_coast", "lift_on_push_lap"):
        parts.append(_coast_note(target))
    if primary == "slow_approach":
        parts.append(_approach_note(target, cmp.delta["v_start"]))
    # A spin, an off or a problem, unless something else already explains the loss: coasting
    # (the loss is no more than that much coasting costs), the other cars or a slow lap.
    explained = primary in ("lift_and_coast", "lift_on_push_lap", "not_push_lap") or cars.kind
    if lost > BIG_LOSS_S and not doubtful and not explained:
        parts.append("a loss this size in one corner usually means a spin, an off or a problem")
    if issues and not doubtful:
        confirmed = "race control" if rc.kind else "the timing clock"
        parts.append(f"confirmed by {confirmed}, but {issue_text}")
    if cmp.reference == "field":
        laps = "no other clean lap"
        if cmp.own_laps:
            laps = f"only {_plural(cmp.own_laps, 'other clean lap')}"
        parts.append(f"compared with the field, as they had {laps} here")
    restart = restart_note(tracks, target)
    slow_note = _slow_lap_note(context, primary, lost, slower, to_flag)
    notes = [n for n in (*cars.notes, restart, *context.notes, slow_note) if n]
    parts.extend(notes)
    return Explanation(
        comparison=cmp,
        mistake_type=primary,
        secondary_type=secondary,
        strength=strength,
        phase=phase,
        data_issue=issue_text,
        race_control=rc.message,
        race_control_turn="" if rc.turn is None else str(rc.turn),
        bonus=bonus,
        field_loss_s=context.field_loss_s,
        context=notes,
        evidence=evidence,
        sentence=f"{head}: " + "; ".join(parts) + ".",
        traffic=cars,
        type_without_traffic=base,
        lap_deficit_s=deficit,
        lap_usual_s=usual_so_far,
    )


# How a traffic type's clause ends in the sentence.
TRAFFIC_VERDICT = {
    "lapped": "letting them through (blue flags), not a mistake",
    "battle": "the time lost is the fight for the place, not a mistake",
    "impeded": "it set their pace, so not a mistake",
    "impeded_passed": "having to get past it, so not a mistake",
}


def _named_many_text(rc: RaceControl) -> str:
    """Race control naming CLUSTER_NAMED or more drivers at one turn on one race lap. For
    deleted lap times that is a place many ran wide, each deletion the driver's own; for
    incidents, probably something on track or the conditions."""
    where = "this turn" if rc.turn is None else f"turn {rc.turn}"
    if rc.kind == "track_limits" and "INCIDENT" not in rc.message:
        return (
            f"{rc.named_here} drivers had lap times deleted at {where} on this lap: a place many "
            "ran wide, so no extra weight in the ranking"
        )
    return (
        f"race control named {rc.named_here} drivers at {where} on this lap: probably something "
        "on track or the conditions"
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _race_control_text(rc: RaceControl, turn: str) -> str:
    reason = rc.message.split("NOTED - ", 1)[1] if "NOTED - " in rc.message else ""
    reason = reason.split(" (")[0].strip().lower()  # drop "(15:53:37)"-style suffixes
    why = f" ({reason})" if reason else ""
    turn = turn if rc.turn is None else str(rc.turn)
    if rc.kind == "involved":
        others = " and ".join(rc.others)
        return f"involved in an incident with {others} at turn {turn} noted by race control{why}"
    if rc.kind == "track_limits" and "INCIDENT" not in rc.message:
        return f"lap time deleted for track limits at turn {turn}"
    return f"race control noted an incident at turn {turn}{why}"


def _lift_saves(target: pd.Series) -> bool:
    """Whether lifting and coasting there saves something: energy (the 2026 cars harvest by
    lifting) or, in races, fuel and tyres."""
    return int(target["year"]) >= 2026 or target["session"] in RACE_CODES


def _coast_note(target: pd.Series) -> str:
    if int(target["year"]) >= 2026:
        return "likely energy management (the 2026 cars harvest by lifting), not a mistake"
    if target["session"] in RACE_CODES:
        return "likely fuel or tyre saving, not a mistake"
    return "a lift before braking, unusual on a push lap: there is nothing to save in qualifying"


def _approach_note(target: pd.Series, delta_kph: float) -> str:
    if int(target["year"]) >= 2026 and delta_kph > -DEPLOYMENT_KPH:
        return "carried in from before the corner (in 2026 often the car running out of deployment)"
    return "carried in from before the corner (the previous exit, traffic or a tow)"


# ---------------------------------------------------------------------------------------------
# Batch: every flagged segment


def _row_record(exp: Explanation, seg: SessionSegments) -> dict:
    cmp = exp.comparison
    frame = seg.frame
    ref_time = (
        float(np.median(frame["segment_time_s"].to_numpy()[cmp.reference_rows]))
        if len(cmp.reference_rows)
        else math.nan
    )
    record = {
        "segment_id": frame.at[cmp.row, "segment_id"],
        "apex_m": float(frame["apex_m"].iloc[cmp.row]),
        "reference": cmp.reference,
        "n_reference": len(cmp.reference_rows),
        "segment_time_s": float(frame["segment_time_s"].iloc[cmp.row]),
        "reference_time_s": ref_time,
        "time_lost_s": cmp.time_lost_s,
        "loss_entry_s": cmp.phase_loss_s[0],
        "loss_apex_s": cmp.phase_loss_s[1],
        "loss_exit_s": cmp.phase_loss_s[2],
        "field_loss_s": exp.field_loss_s,
        "time_scale_s": cmp.scale.get("segment_time_s", math.nan),  # the time z's spread
        "phase": exp.phase,
        "mistake_type": exp.mistake_type,
        "secondary_type": exp.secondary_type,
        "type_strength": exp.strength,
        "evidence": ",".join(exp.evidence),
        "apex_moved": cmp.apex_moved,
        "taken_flat": cmp.taken_flat,
        "data_issue": exp.data_issue,
        "race_control": exp.race_control,
        "race_control_turn": exp.race_control_turn,
        "race_control_bonus": exp.bonus,
        "context": "; ".join(exp.context),
        "explanation": exp.sentence,
        # The traffic the cars around show (traffic_finding), whatever the type: see priority
        # and traffic_cover.
        "traffic": exp.traffic.kind,
        "traffic_cars": ", ".join(exp.traffic.cars),
        "traffic_start_m": exp.traffic.start_m,
        "traffic_end_m": exp.traffic.end_m,
        "traffic_approximate": any(
            e.approximate for e in exp.traffic.encounters if e.driver in exp.traffic.cars
        ),
        "traffic_maybe": ", ".join(exp.traffic.maybe),
        "type_without_traffic": exp.type_without_traffic,
        "lap_deficit_s": exp.lap_deficit_s,
        "lap_usual_s": exp.lap_usual_s,
        # Where the loss began, and the cars already at the driver then (see priority).
        "loss_onset_m": loss_onset(cmp),
        "traffic_alongside": ", ".join(exp.traffic.alongside),
    }
    for f in DEVIATIONS:
        record[f"delta_{f}"] = cmp.delta[f]
        record[f"z_{f}"] = cmp.z[f]
    return record


def priority(
    time_lost_s: pd.Series,
    score_pct: pd.Series,
    race_control: pd.Series,
    flag_share: float = FLAG_SHARE,
    field_loss_s: pd.Series | None = None,
    traffic: pd.Series | None = None,
) -> pd.Series:
    """How find_mistakes ranks flags: time lost, discounted by up to half for corners the
    detectors find only borderline unusual, plus RACE_CONTROL_BONUS_S when race control named
    the driver at that corner (`race_control`; a deleted lap time or a penalty costs more than
    the corner itself, and it is the surest sign of a mistake). `score_pct` is the flag's
    percentile in its session; flags are the top `flag_share`, so (score_pct - (1 -
    flag_share)) / flag_share runs from 0 (just flagged) to 1 (the session's most unusual
    corner). When the field was slow at that turn on that lap too (`field_loss_s`, NaN
    otherwise), only the loss beyond theirs counts: the rest is the conditions or something on
    track, not the driver. When traffic explains the loss (`traffic`, see traffic_explained:
    the flag's own cars, or a neighbouring turn's covering it, were already at the driver
    when its loss began), none of it counts: such a flag is listed only when race control
    named the driver, and then it ranks on the bonus alone. A car that was still further back
    when the loss began didn't cause it, so the full loss counts."""
    confidence = ((score_pct - (1 - flag_share)) / flag_share).clip(0, 1)
    lost = time_lost_s.fillna(0)
    if field_loss_s is not None:
        lost = lost - field_loss_s.fillna(0).clip(lower=0)
    if traffic is not None:
        lost = lost.where(~traffic.astype(bool), 0.0)
    cost = lost.clip(lower=0) + RACE_CONTROL_BONUS_S * race_control.astype(float)
    return cost * (0.5 + 0.5 * confidence)


def traffic_explained(mistakes: pd.DataFrame) -> pd.Series:
    """For each flag, whether traffic explains its loss, so that it doesn't count towards the
    ranking (priority): a car its own traffic type names (`traffic_cars`), or one the traffic
    flag covering it names (`covered_by`, traffic_cover), was already at the driver when
    this flag's loss began (`traffic_alongside`, TrafficFinding.alongside). Without that column
    (results from before it), any traffic does, as it used to."""
    own = mistakes["traffic"].fillna("") != ""
    covered = mistakes.get("covered_by", pd.Series("", index=mistakes.index)).fillna("") != ""
    if "traffic_alongside" not in mistakes:
        return own | covered

    def names(text: object) -> set[str]:
        return {c.strip() for c in str(text or "").split(",") if c.strip()}

    cars_of = dict(zip(mistakes["segment_id"].astype(str), mistakes["traffic_cars"], strict=True))
    out = []
    for r in mistakes.to_dict("records"):
        near = names(r["traffic_alongside"])
        mine = bool(r["traffic"]) and bool(names(r["traffic_cars"]) & near)
        cover = str(r.get("covered_by", "") or "")
        theirs = bool(cover) and bool(names(cars_of.get(cover, "")) & near)
        out.append(mine or theirs)
    return pd.Series(out, index=mistakes.index, dtype=bool)


def traffic_cover(mistakes: pd.DataFrame) -> pd.Series:
    """For each flag, the traffic-typed flag of the same lap that covers it ("" for none): one
    whose extended window (`traffic_start_m` to `traffic_end_m` around its apex) contains this
    flag's window start.

    The cars that made that flag traffic were already close when its extended window began,
    and passed or held the driver up inside it; a neighbouring turn's window that starts inside
    it lost its time with those cars already close, so its loss is the same traffic, whatever
    its own rules say. Every neighbour whose window starts within EXTEND_BEFORE_M before the
    traffic turn's window or up to its extended end is covered; a turn whose window starts
    earlier lost time before the cars were known to be close, and keeps its type (its own
    traffic check measured the gaps at its own window's extended start).

    A covered driving flag isn't listed (find_mistakes counts it as the same moment as the
    traffic); a covered race-control flag still is, ranked on its bonus alone (priority).
    """
    out = pd.Series("", index=mistakes.index, dtype=object)
    if "traffic_start_m" not in mistakes:
        return out
    moving = mistakes[mistakes["mistake_type"].isin(TRAFFIC)]
    if moving.empty:
        return out
    starts = mistakes["apex_m"] + float(DISTANCE[0])
    for lap, cars in moving.groupby("lap_id"):
        here = mistakes.index[mistakes["lap_id"] == lap]
        for i in here:
            if mistakes.at[i, "mistake_type"] in NOT_MISTAKES:
                continue
            start = starts[i]
            covers = cars[
                (cars["apex_m"] + cars["traffic_start_m"] <= start)
                & (start <= cars["apex_m"] + cars["traffic_end_m"])
            ]
            if len(covers):
                nearest = (covers["apex_m"] - mistakes.at[i, "apex_m"]).abs().idxmin()
                out[i] = str(covers.at[nearest, "segment_id"])
    return out


def _tell_covered(mistakes: pd.DataFrame) -> None:
    """Add to the explanation of each race-control flag that traffic at a neighbouring turn
    covers (traffic_cover) whether its time lost counts towards the ranking, in place (as
    merge_overlapping adds race control's other messages to an event): not when those cars
    were already at the driver as its loss began (traffic_explained), else it does."""
    by_id = {str(r["segment_id"]): r for r in mistakes.to_dict("records")}
    explained = traffic_explained(mistakes)
    for i in mistakes.index[(mistakes["covered_by"] != "") & mistakes["mistake_type"].isin(NAMED)]:
        if mistakes.at[i, "traffic"]:
            continue  # its own cars already say so
        cover = by_id[str(mistakes.at[i, "covered_by"])]
        turn = turn_label(int(cover["corner"]), str(cover["corner_letter"] or ""))
        what = TYPE_TEXT[str(cover["mistake_type"])].split(" (")[0]
        sentence = str(mistakes.at[i, "explanation"]).removesuffix(".")
        counts = (
            "so the time lost doesn't count towards its ranking"
            if explained[i]
            else "but those cars hadn't reached the driver yet when the loss began here, so it "
            "counts towards its ranking"
        )
        mistakes.at[i, "explanation"] = (
            f"{sentence}; the same moment as {what} at turn {turn}, {counts}."
        )


def listed_flags(mistakes: pd.DataFrame) -> pd.Series:
    """Which flags find_mistakes lists (before overlapping ones are merged into one): the
    driving mistakes and race-control types, except driving flags that traffic at a
    neighbouring turn covers (traffic_cover, the `covered_by` column)."""
    kind = mistakes["mistake_type"]
    if "covered_by" in mistakes:
        covered = mistakes["covered_by"].fillna("") != ""
    else:
        covered = pd.Series(False, index=mistakes.index)
    return ~kind.isin(NOT_MISTAKES) & (~covered | kind.isin(NAMED))


def merge_overlapping(mistakes: pd.DataFrame) -> pd.DataFrame:
    """Listed flags on the same lap whose windows overlap (apexes less than WINDOW_SPAN_M
    apart, chained) are one moment seen from two or three turns: a slow exit of one turn is
    the slow approach of the next, and a spin shows in every window it touches. Each run
    becomes one event, listed under the turn it is about (`event_head`); the others point to it.

    Adds `merged_into` (the listed flag's segment_id; "" for flags find_mistakes doesn't list)
    and `also_turns` (for the listed flag, the event's other turns, e.g. "2, 3"). Their time
    losses are never added up: the windows share the same metres of track. When race control
    named the driver at another of the event's turns too, the listed flag's explanation says so.
    Driving flags covered by traffic at a neighbouring turn (`covered_by`, see traffic_cover)
    aren't listed, so they join no event: the moment is the traffic's.
    """
    out = mistakes.assign(merged_into="", also_turns="")
    listed = out[listed_flags(out)]
    if listed.empty:
        return out
    listed = listed.sort_values(["lap_id", "apex_m"])
    starts = (listed["lap_id"] != listed["lap_id"].shift()) | (
        listed["apex_m"].diff() >= WINDOW_SPAN_M
    )
    for _, part in listed.groupby(starts.cumsum()):
        head = event_head(part)
        out.loc[part.index, "merged_into"] = out.at[head, "segment_id"]
        others = part.drop(index=head)
        turns = zip(others["corner"], others["corner_letter"], strict=True)
        out.at[head, "also_turns"] = ", ".join(turn_label(c, lt) for c, lt in turns)
        told = {str(out.at[head, "race_control"])}
        for member in others[others["mistake_type"].isin(NAMED)].to_dict("records"):
            if member["race_control"] in told:
                continue
            told.add(member["race_control"])
            named_turn = str(member["race_control_turn"])
            rc = RaceControl(
                str(member["mistake_type"]),
                str(member["race_control"]),
                turn=int(named_turn) if named_turn else None,
            )
            text = _race_control_text(rc, turn_label(member["corner"], member["corner_letter"]))
            sentence = str(out.at[head, "explanation"]).removesuffix(".")
            out.at[head, "explanation"] = f"{sentence}; {text}."
    return out


def event_head(part: pd.DataFrame) -> int:
    """The flag an event of overlapping flags (`merge_overlapping`, in track order) is listed
    under: the turn where it started, not the one that lost the most.

    A later window re-counts the earlier turn's exit loss and whatever follows, so it usually
    loses more. Race control naming the driver comes first (a track-limits deletion or an
    incident is why the moment matters, and at the turn race control named rather than a
    neighbouring one whose window covers it); then the first turn whose loss wasn't mostly on
    entry (a turn that lost most of its time on entry carried it in from the one before); else
    the first turn.
    """
    named = part[part["mistake_type"].isin(NAMED)]
    if len(named):
        own = named[named["race_control_turn"] == ""]
        return int((own if len(own) else named).index[0])
    lost = part["time_lost_s"]
    started = part[~(part["loss_entry_s"] > MOSTLY * lost) | ~(lost > 0)]
    return int((started if len(started) else part).index[0])


def explain_flags(
    scores: pd.DataFrame,
    root: Path = PROCESSED_DIR,
    thresholds: dict[str, float] | None = None,
    calibration_per_session: int = CALIBRATION_PER_SESSION,
    seed: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Explain every flagged segment of `scores`; also sample ordinary corners (clean laps,
    not flagged) for the threshold calibration. Returns (explanations, calibration sample)."""
    rng = np.random.default_rng(seed)
    records, sample = [], []
    sessions = scores.groupby(["year", "round", "session"], sort=True)
    for i, (session_id, part) in enumerate(sessions):
        year, round_, code = cast(tuple[int, int, str], session_id)
        key = store.session_key(int(year), int(round_), str(code))
        seg = load_session_segments(key, root)
        events = load_session_events(key, root)
        tracks = traffic.load_tracks(key, root)
        clean = clean_mask(seg.frame)
        losses = lap_losses(seg, clean)
        position = pd.Series(np.arange(len(seg.frame)), index=seg.frame["segment_id"])
        flagged = part.loc[part["flagged"], "segment_id"]
        for row in position.reindex(flagged).dropna().astype(int):
            exp = explain(seg, int(row), events, clean, losses, thresholds, tracks)
            records.append(_row_record(exp, seg))
        ordinary = np.flatnonzero(clean & ~seg.frame["segment_id"].isin(flagged).to_numpy())
        take = rng.choice(ordinary, size=min(calibration_per_session, len(ordinary)), replace=False)
        for row in take:
            cmp = compare(seg, int(row), clean)
            if cmp.reference != "none":
                sample.append(
                    {
                        "year": int(year),
                        **{f: cmp.adverse(f) for f in DEVIATIONS},
                        "coast_extra_m": cmp.delta["coast_m"],
                        "time_lost_s": cmp.time_lost_s,
                        "apex_moved": cmp.apex_moved,
                        "taken_flat": cmp.taken_flat,
                        # The hand-crafted slowest point, anywhere in the window.
                        "window_vmin_d": float(seg.frame["d_vmin"].iloc[int(row)]),
                    }
                )
        if (i + 1) % 25 == 0:
            log.info("explained %d of %d sessions", i + 1, sessions.ngroups)
    return pd.DataFrame(records), pd.DataFrame(sample)


def calibrate_thresholds(
    sample: pd.DataFrame, quantiles: dict[str, float] | None = None
) -> dict[str, float]:
    """Each feature's threshold: its quantile (QUANTILES) of adverse z on ordinary corners,
    floored at MIN_THRESHOLD (THRESHOLDS where the sample has no values)."""
    quantiles = QUANTILES if quantiles is None else quantiles
    out = {}
    for f in DEVIATIONS:
        values = sample[f].to_numpy(float) if f in sample else np.zeros(0)
        values = values[np.isfinite(values)]
        out[f] = (
            max(MIN_THRESHOLD, float(np.quantile(values, quantiles[f])))
            if len(values)
            else THRESHOLDS[f]
        )
    return out


def coast_cost(sample: pd.DataFrame, quantile: float = CALIBRATION_QUANTILE) -> tuple[float, int]:
    """COAST_COST_S_PER_M from the calibration sample: the quantile of time lost per extra
    metre of coasting on the ordinary corners where coasting fired with nothing adverse in the
    braking, and how many there were (the random sample has few; COAST_COST_S_PER_M comes from
    a larger one, so this is a check)."""
    if not len(sample) or "coast_extra_m" not in sample:
        return math.nan, 0
    braking = np.column_stack([sample[f] >= THRESHOLDS[f] for f in BRAKING]).any(axis=1)
    coasting = (sample["coast_m"] >= THRESHOLDS["coast_m"]) & ~braking
    part = sample[coasting & (sample["coast_extra_m"] > 0)]
    per_m = (part["time_lost_s"] / part["coast_extra_m"]).to_numpy(float)
    return (float(np.quantile(per_m, quantile)) if len(per_m) else math.nan), len(per_m)


# ---------------------------------------------------------------------------------------------
# Calibrating the traffic settings (`--calibrate-traffic`)

CALIBRATION_YEARS = range(2022, 2026)  # never 2026, the test year
GAP_BINS = (0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)


CALIBRATION_SAMPLE = 0.1  # race corners without a pass compared for the yield's base rate


def traffic_sample(
    key: str, flagged: frozenset[str], root: Path = PROCESSED_DIR, seed: int = 0
) -> pd.DataFrame:
    """One session's rows for calibrate_traffic: for every corner the detectors score (flagged
    or not), each other car near it (traffic.surroundings) with the corner's time lost and
    usual time; in qualifying also the lap deficit where the window starts (`lap_deficit`).

    In races, a corner where a placed car passed or was passed (and a random CALIBRATION_SAMPLE
    of the others, `sampled`) is compared with the driver's usual laps for where its loss began
    (loss_onset) and whether the driver braked or lifted where the usual lap is on the power
    (yield_point, yield_lift): each car's closest gap before those points, and one row per
    such corner (`kind` "corner": whether a lapping car went past). And each corner of the
    last three laps before the chequered flag (traffic.chequered_lap) gives a "final" row: its
    rank from the end of the lap and its loss, for FLAG_RUN_CORNERS."""
    tracks = traffic.load_tracks(key, root)
    if tracks is None:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    seg = load_session_segments(key, root)
    frame = seg.frame
    clean = clean_mask(frame)
    losses = lap_losses(seg, clean)
    seg_time = frame["segment_time_s"].to_numpy(float)
    flag = frame["segment_id"].isin(flagged).to_numpy()
    flagged_laps = set(frame.loc[flag, "lap_id"])
    turns = frame.groupby(["corner", "corner_letter"])["apex_m"].median()
    from_end = turns.rank(ascending=False, method="first").astype(int)
    chequered = {d: traffic.chequered_lap(tracks, str(d)) for d in frame["driver"].unique()}
    rows = []
    for r in np.flatnonzero(seg.eligible() & np.isfinite(losses)):
        target = frame.iloc[r]
        base = {
            "race": seg.is_race,
            "ordinary": bool(clean[r] and not flag[r]),
            "flagged": bool(flag[r]),
            "loss_s": float(losses[r]),
            "own_s": float(seg_time[r]),
            "usual_s": float(seg_time[r] - losses[r]),
        }
        if not seg.is_race:
            deficit, usual = lap_deficit(tracks, target)
            clean_lap = target["lap_id"] not in flagged_laps
            rows.append({**base, "deficit_s": deficit, "elapsed_s": usual, "clean_lap": clean_lap})
            continue
        driver, lap, apex = str(target["driver"]), int(target["lap_number"]), target["apex_m"]
        last = chequered.get(driver)
        if last is not None and 0 <= last - lap <= 2:
            rank = int(from_end[(target["corner"], target["corner_letter"])])
            rows.append({**base, "kind": "final", "laps_to_flag": last - lap, "from_end": rank})
        around = traffic.surroundings(tracks, driver, lap, float(apex))
        placed = [e for e in around.encounters if not e.rough] if around is not None else []
        passing = any(e.passed for e in placed)
        sampled = bool(rng.random() < CALIBRATION_SAMPLE)
        onset = yield_m = lift_m = math.nan
        if passing or sampled:
            cmp = compare(seg, int(r), clean)
            onset, yield_m, lift_m = loss_onset(cmp), yield_point(seg, cmp), yield_lift(seg, cmp)
            lapping = any(e.passed == "by" and e.laps_ahead >= 1 for e in placed)
            rows.append(
                {
                    **base,
                    "kind": "corner",
                    "sampled": sampled,
                    "lapping_passed": lapping,
                    "onset_m": onset,
                    "yield_m": yield_m,
                    "lift_m": lift_m,
                }
            )
        for e in placed:  # rough positions are out of the rules, so not evidence for them
            row = {
                **base,
                "kind": "car",
                "laps_ahead": e.laps_ahead,
                "gap_start_s": e.gap_start_s,
                "passed": e.passed,
                "pass_m": e.pass_m,
                "min_gap_s": e.min_gap_s,
                "behind_s": e.behind_s,
                "window_s": e.window_s,
                "usual_window_s": e.usual_window_s,
                "approximate": e.approximate,
                "onset_m": onset,
            }
            for name, point in (("onset", onset), ("yield", yield_m), ("lift", lift_m)):
                row[f"gap_{name}_s"] = e.closest_before(point)[0]
            row["yield_m"], row["lift_m"] = yield_m, lift_m
            rows.append(row)
    return pd.DataFrame(rows)


def _sample_job(job: tuple[str, frozenset[str], Path]) -> pd.DataFrame:
    return traffic_sample(*job)


def _binned_loss(values: pd.Series, loss: pd.Series, edges: Sequence[float] = GAP_BINS) -> str:
    """Median time lost by bins of `values` (GAP_BINS: 0.25-1 s): "0-0.25 s: 0.32 (425), ..."."""
    bins = pd.cut(values, list(edges))
    medians = loss.groupby(bins, observed=True).agg(["median", "size"])
    return ", ".join(
        f"{b.left:g}-{b.right:g} s: {m:.2f} s ({n:,})"
        for b, (m, n) in zip(medians.index, medians.to_numpy(), strict=True)
    )


def _quantiles(values: pd.Series, qs: Sequence[float] = (0.5, 0.9, 0.95, 0.99)) -> str:
    """ "median 0.51 s, 90th percentile 1.11 s, 95th 1.43 s, 99th 2.21 s"."""
    q = values.quantile(list(qs))
    names = ["median" if x == 0.5 else f"{100 * x:.0f}th" for x in qs]
    text = [f"{n} {v:.2f} s" for n, v in zip(names, q.to_numpy(), strict=True)]
    first = next((i for i, n in enumerate(names) if n != "median"), None)
    if first is not None:
        text[first] = text[first].replace("th", "th percentile", 1)
    return ", ".join(text)


def summarise_onsets(race: pd.DataFrame) -> dict[str, str]:
    """The evidence for LAPPED_GAP_S, ALONGSIDE_S, YIELD_GAP_S and FLAG_RUN_CORNERS from
    traffic_sample's race rows: gaps when the driver's loss began (loss_onset), braking where
    the usual lap is on the power (yield_point), and the corners of the chequered-flag lap."""
    out: dict[str, str] = {}
    cars = race[race["kind"] == "car"] if "kind" in race else race.iloc[:0]
    if "gap_onset_s" in cars:
        lapping = cars[(cars["laps_ahead"] >= 1) & (cars["passed"] == "by")]
        known = lapping["onset_m"].notna()
        after = known & (lapping["pass_m"] > lapping["onset_m"])
        ordinary = lapping["ordinary"].astype(bool)
        flagged = lapping["flagged"].astype(bool)
        both = (ordinary | flagged) & after  # LAPPED_GAP_S: a costly yield is flagged
        out["lapping passes, gap when the loss began"] = (
            f"{int((ordinary & known).sum()):,} on ordinary corners with a loss of {ONSET_S} s "
            f"or more, {int((ordinary & known & ~after).sum()):,} of them before it began; the "
            f"{int((ordinary & after).sum()):,} after it from: "
            f"{_quantiles(lapping.loc[ordinary & after, 'gap_onset_s'])} (flagged corners, "
            f"{int((flagged & after).sum()):,}: "
            f"{_quantiles(lapping.loc[flagged & after, 'gap_onset_s'], (0.5, 0.95))}; both, "
            f"{int(both.sum()):,}: {_quantiles(lapping.loc[both, 'gap_onset_s'], (0.5, 0.95))})"
        )
        part = lapping[after & (lapping["gap_onset_s"] > 0)]
        out["lapping passes after the loss began: time lost by that gap (all scored corners)"] = (
            _binned_loss(part["gap_onset_s"], part["loss_s"])
        )
        same = cars[(cars["laps_ahead"] == 0) & (cars["passed"] == "by")]
        after = (
            same["onset_m"].notna()
            & (same["pass_m"] > same["onset_m"])
            & (same["gap_start_s"].abs() <= BATTLE_GAP_S)
        )
        close = same[after & same["ordinary"].astype(bool)]
        with_flagged = same[after & (same["ordinary"] | same["flagged"]).astype(bool)]
        out["same-lap passes after the loss began, within BATTLE_GAP_S at the start"] = (
            f"{len(close):,} on ordinary corners, |gap| when the loss began: "
            f"{_quantiles(close['gap_onset_s'].abs())} (with the flagged ones, "
            f"{len(with_flagged):,}: {_quantiles(with_flagged['gap_onset_s'].abs(), (0.95,))}); "
            "time lost by it (all scored corners): "
            + _binned_loss(
                same.loc[after, "gap_onset_s"].abs(), same.loc[after, "loss_s"], ALONGSIDE_BINS
            )
        )
        y = lapping[lapping["yield_m"].notna() & (lapping["pass_m"] >= lapping["yield_m"])]
        yo = y[y["ordinary"].astype(bool)]
        out["braking where the usual lap is on the power, lapping car going past after it"] = (
            f"{len(yo):,} on ordinary corners, gap there: {_quantiles(yo['gap_yield_s'])} "
            f"(all scored corners, {len(y):,}: {_quantiles(y['gap_yield_s'], (0.5, 0.95))})"
        )
    corners = race[race["kind"] == "corner"] if "kind" in race else race.iloc[:0]
    if len(corners):
        lap = corners["lapping_passed"].astype(bool)
        ordinary = corners["ordinary"].astype(bool)
        flagged = corners["flagged"].astype(bool)
        base = corners[corners["sampled"].astype(bool) & ordinary & ~lap]
        rates = []
        for col, what in (("yield_m", "braking"), ("lift_m", "a lift on the power")):
            fired = corners[col].notna()
            rates.append(
                f"{what}: {base[col].notna().mean():.1%} of {len(base):,} sampled ordinary "
                f"corners without a lapping car going past, {fired[ordinary & lap].mean():.1%} "
                f"with one ({int((ordinary & lap).sum()):,}); flagged: "
                f"{fired[flagged & ~lap].mean():.1%} without, {fired[flagged & lap].mean():.1%} "
                "with"
            )
        out["easing off where the usual lap is on the power, costing time"] = "; ".join(rates)
    final = race[race["kind"] == "final"] if "kind" in race else race.iloc[:0]
    if len(final):
        slow = final.assign(slow=final["loss_s"] > LAP_SLOW_S)
        share = slow.groupby(["from_end", "laps_to_flag"])["slow"].mean().unstack()
        out["corners slower than usual by LAP_SLOW_S, by turns from the end of the lap"] = (
            ", ".join(
                f"{k}: {row.get(0, math.nan):.1%} on the chequered-flag lap, "
                f"{row.get(1, math.nan):.1%} and {row.get(2, math.nan):.1%} the two laps before"
                for k, row in share.head(6).iterrows()
            )
        )
    return out


ALONGSIDE_BINS = (0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)


def summarise_traffic(sample: pd.DataFrame) -> dict[str, str]:
    """The traffic settings' evidence from traffic_sample rows (2022-2025): the quantiles and
    binned losses quoted with LAPPED_GAP_S, BATTLE_GAP_S, SIDE_BY_SIDE_S, IMPEDE_GAP_S,
    IMPEDE_DROP_S, PREP_SHARE and (summarise_onsets) ALONGSIDE_S, YIELD_GAP_S and
    FLAG_RUN_CORNERS."""
    out: dict[str, str] = {}
    race = sample[sample["race"]] if "race" in sample else sample.iloc[:0]
    if "kind" in race:
        out |= summarise_onsets(race)
        race = race[race["kind"] == "car"]
    if "passed" in race:
        passes = race[(race["passed"] == "by") & (race["gap_start_s"] > 0)]
        for name, part in (
            ("lapping", passes[passes["laps_ahead"] >= 1]),
            ("same lap", passes[passes["laps_ahead"] == 0]),
        ):
            ordinary = part[part["ordinary"]]
            q = ordinary["gap_start_s"].quantile([0.5, 0.95, 0.99])
            out[f"{name} passes"] = (
                f"{len(ordinary):,} on ordinary corners; gap at the start: median {q[0.5]:.2f} s, "
                f"95th percentile {q[0.95]:.2f} s, 99th {q[0.99]:.2f} s"
            )
            out[f"{name} time lost by gap (all scored corners)"] = _binned_loss(
                part["gap_start_s"], part["loss_s"]
            )
        alongside = race[
            race["ordinary"]
            & (race["laps_ahead"] == 0)
            & (race["passed"] == "")
            & (race["gap_start_s"].abs() <= BATTLE_GAP_S)
        ]
        near = alongside["min_gap_s"] <= SIDE_BY_SIDE_S
        apart = alongside["min_gap_s"].between(0.3, 0.5)
        out["side by side"] = (
            f"{int(near.sum()):,} ordinary corners with a same-lap car within {SIDE_BY_SIDE_S} s: "
            f"{alongside.loc[near, 'loss_s'].median():.2f} s lost at the median, against "
            f"{alongside.loc[apart, 'loss_s'].median():.2f} s at 0.3-0.5 s apart"
        )
        placed = ~race["approximate"].eq(True)
        held = race[race["ordinary"].astype(bool) & race["behind_s"].notna() & placed]
        slower = held[held["window_s"] - held["usual_s"] >= 0.3]
        out["held up: time lost by gap behind a slower car"] = _binned_loss(
            slower["behind_s"], slower["loss_s"]
        )
        close = slower[slower["behind_s"] <= IMPEDE_GAP_S]
        drop = (close["own_s"] - close["window_s"]).quantile(0.95)
        out["held up: dropping back"] = (
            f"{len(close):,} ordinary corners within {IMPEDE_GAP_S} s: 95th percentile {drop:.3f} s"
        )
    quali = sample[~sample["race"]] if "race" in sample else sample.iloc[:0]
    if "deficit_s" in quali:
        clean_lap = quali["clean_lap"].eq(True)
        laps = quali[quali["ordinary"].astype(bool) & clean_lap & quali["deficit_s"].notna()]
        share = laps["deficit_s"] / laps["elapsed_s"]
        late = laps["elapsed_s"] >= PREP_MIN_ELAPSED_S
        out["not a full push lap"] = (
            f"{int(late.sum()):,} ordinary push-lap corners at least "
            f"{PREP_MIN_ELAPSED_S:.0f} s in: "
            f"99th percentile {share[late].quantile(0.99):.2%} of the lap so far "
            f"(earlier: {share[~late].quantile(0.99):.1%})"
        )
    return out


def calibrate_traffic(
    scores: pd.DataFrame, root: Path = PROCESSED_DIR, workers: int | None = None
) -> dict[str, str]:
    """summarise_traffic over every session of CALIBRATION_YEARS (a few minutes, in parallel)."""
    from concurrent.futures import ProcessPoolExecutor

    scores = scores[scores["year"].isin(list(CALIBRATION_YEARS))]
    jobs = []
    for (year, round_, code), part in scores.groupby(["year", "round", "session"], sort=True):
        key = store.session_key(int(year), int(round_), str(code))  # type: ignore[call-overload]
        jobs.append((key, frozenset(part.loc[part["flagged"], "segment_id"]), root))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        frames = [f for f in pool.map(_sample_job, jobs) if len(f)]
    return summarise_traffic(pd.concat(frames, ignore_index=True))


def run_explain(
    scores_path: Path = RESULTS_DIR / "segment_scores.parquet",
    out_path: Path = RESULTS_DIR / "mistakes.parquet",
    report: Path | None = REPORT_DIR / "m4_mistakes.md",
    root: Path = PROCESSED_DIR,
    allow_unrecorded: bool = False,
) -> pd.DataFrame:
    """Explain every flagged segment and write mistakes.parquet and the report.

    The scores must be the current scoring run's (`provenance.check_current` against the
    manifest next to them; StaleResultsError otherwise), and mistakes.parquet records that run.
    `allow_unrecorded` accepts scores from before runs were recorded (neither the manifest nor
    the scores name a run). When only one of them does, the scores still fail: recorded scores
    without a manifest belong to a checkpoint that is no longer the selected one (`select`
    removed it) or to a scoring run that didn't finish, and a manifest naming a run with
    unrecorded scores next to it means the scores were replaced since.
    """
    started = time.time()
    results = scores_path.parent
    run_id = provenance.scoring_run(results)
    unrecorded = (
        scores_path.exists() and run_id is None and provenance.parquet_run(scores_path) is None
    )
    if not (allow_unrecorded and unrecorded):
        provenance.check_current(scores_path, "score", results)
    scores = pd.read_parquet(scores_path)
    explained, sample = explain_flags(scores, root)
    train = sample[sample["year"] <= 2024] if len(sample) else sample
    fresh = calibrate_thresholds(train if len(train) else sample)
    drift = {f: fresh[f] / THRESHOLDS[f] - 1 for f in DEVIATIONS}
    if any(abs(v) > 0.1 for v in drift.values()):
        log.warning("calibrated thresholds differ from THRESHOLDS by >10%%: %s", fresh)

    keep = [
        "segment_id", "lap_id", "year", "round", "session", "event", "location", "driver",
        "driver_number", "team", "lap_number", "corner", "corner_letter", "lap_class",
        "compound", "tyre_life", "deleted", "label", "split", "transformer_pct",
        "isolation_forest_pct", "score", "score_pct",
    ]  # fmt: skip
    mistakes = scores.loc[scores["flagged"], keep].merge(explained, on="segment_id", how="inner")
    mistakes["covered_by"] = traffic_cover(mistakes)
    _tell_covered(mistakes)
    mistakes["priority"] = priority(
        mistakes["time_lost_s"],
        mistakes["score_pct"],
        mistakes["race_control_bonus"],
        field_loss_s=mistakes["field_loss_s"],
        traffic=traffic_explained(mistakes),
    )
    mistakes = (
        merge_overlapping(mistakes)
        .sort_values(["year", "round", "session", "priority"], ascending=[True, True, True, False])
        .reset_index(drop=True)
    )
    provenance.write_parquet(mistakes, out_path, run_id)
    log.info("wrote %s (%d flags) in %.0f s", out_path, len(mistakes), time.time() - started)
    if report is not None:
        write_report(mistakes, sample, fresh, report, root, out_path.parent)
    return mistakes


# ---------------------------------------------------------------------------------------------
# Report

# Top flags checked by eye against their traces while building this module: what each trace
# shows. The report prints them with what the explanation says now.
CHECKED_BY_EYE = (
    ("2026_05_SQ_44_10_T1", "data: the clock jumps 7.5 s at the window start; speed, pedals "
     "and gear match the usual lap"),
    ("2026_09_R_31_30_T16", "data: speed frozen at 240 km/h from 130 m before the apex to 60 m "
     "after it, through the braking zone"),
    ("2025_24_R_12_24_T6", "data: speed frozen at 200 km/h through the braking zone"),
    ("2025_13_S_1_8_T18", "data: a one-point speed spike to 310 km/h in a 90 km/h chicane"),
    ("2024_01_R_27_32_T10", "data: the brake signal stays on through the exit, 75 m of it at "
     "full throttle; the speed trace is normal"),
    ("2025_10_R_5_52_T10", "data: the whole trace is shifted about 40 m along the track"),
    ("2023_14_R_18_43_T2", "driving: braked later and carried 40 km/h more into the chicane, "
     "braked again after the apex and crawled out (a lock-up or a missed chicane)"),
    ("2025_10_Q_43_2_T9", "driving: braked early, lifted before the apex and was 35 km/h slow "
     "through it, then late on the throttle"),
    ("2025_06_S_44_13_T12", "not a mistake: much faster than the 10 nearest laps everywhere, "
     "as on a drying track"),
    ("2023_14_R_4_49_T1", "not a mistake: braked 60 m later with 8 km/h more from a tow, then "
     "a slow exit (probably an overtaking move)"),
    ("2024_08_Q_10_9_T19", "not a mistake here: 20-40 km/h slower everywhere, like the rest of "
     "the lap"),
)  # fmt: skip


@dataclass(frozen=True)
class KnownIncident:
    """A documented incident the tools should surface (the M4 "done when" check)."""

    event: str
    year: int
    session: str
    driver: str
    lap: int
    corner: int | str
    record: str  # what race control or the timing says happened
    plausible: tuple[str, ...]  # mistake types that would explain it fairly
    # What the driving rules alone (without race control) should say: a track-limits type is
    # attached from the events table whenever the corner is flagged, so it doesn't test them.
    driving: tuple[str, ...] = ()


# A cut chicane gains time, so "not slower" is what the driving rules should say about it.
CUT = ("no_time_lost",)
KNOWN_INCIDENTS = (
    KnownIncident(
        "Australian Grand Prix", 2026, "Q", "COL", 2, 7,
        "Lap time deleted: 'TRACK LIMITS AT TURN 7' (FastF1's deleted-lap reason).",
        ("track_limits",), ("late_throttle", "over_slowing"),
    ),
    KnownIncident(
        "Monaco Grand Prix", 2025, "R", "RUS", 48, 10,
        "Race control, posted during lap 49: 'TURN 10 INCIDENT INVOLVING CAR 63 (RUS) NOTED - "
        "LEAVING THE TRACK AND GAINING AN ADVANTAGE'; lap 48's time deleted for track limits at "
        "turn 10 (cutting the chicane).",
        ("track_limits",), CUT,
    ),
    KnownIncident(
        "Monaco Grand Prix", 2025, "R", "RUS", 47, 10,
        "Lap 47's time was deleted for track limits at turn 10 too, the lap before the one "
        "race control noted.",
        ("track_limits",), CUT,
    ),
    KnownIncident(
        "Canadian Grand Prix", 2026, "S", "HAM", 22, 13,
        "Race control, posted during lap 23: 'TURN 13 INCIDENT INVOLVING CAR 44 (HAM) NOTED - "
        "LEAVING THE TRACK AND GAINING AN ADVANTAGE' (the final chicane).",
        ("track_limits", "over_slowing", "late_throttle"),
        ("over_slowing", "late_throttle", "early_braking"),
    ),
    KnownIncident(
        "Hungarian Grand Prix", 2024, "R", "VER", 1, 1,
        "Race control, posted during lap 2: 'TURN 1 INCIDENT INVOLVING CAR 1 (VER) NOTED - "
        "LEAVING THE TRACK AND GAINING AN ADVANTAGE', i.e. at the start.",
        ("track_limits",),
    ),
)  # fmt: skip


def _event_label(row: pd.Series | dict) -> str:
    return f"{row['year']} {row['event']} {row['session']}"


def _turn(row: pd.Series | dict) -> str:
    return f"T{turn_label(row['corner'], row['corner_letter'] or '')}"


def _pct(n: int, total: int) -> str:
    return f"{n / total:.0%}" if total else "n/a"


def _type_table(mistakes: pd.DataFrame) -> pd.DataFrame:
    total = len(mistakes)
    quali = mistakes["session"].isin(["Q", "SQ", "SS"])
    rows = []
    for kind, part in mistakes.groupby("mistake_type"):
        lost = part["time_lost_s"]
        rows.append(
            {
                "type": f"`{kind}`",
                "meaning": TYPE_TEXT.get(str(kind), str(kind)),
                "flags": len(part),
                "share": _pct(len(part), total),
                "median time lost (s)": f"{lost.median():.2f}",
                "qualifying": int(quali[part.index].sum()),
                "races": int((~quali[part.index]).sum()),
                "2026": int((part["year"] == 2026).sum()),
                "listed by find_mistakes": "no" if kind in NOT_MISTAKES else "yes",
            }
        )
    return pd.DataFrame(rows).sort_values("flags", ascending=False)


def _pct1(n: int, total: int) -> str:
    return f"{n / total:.1%}" if total else "n/a"


def _fires(values: pd.Series, feature: str) -> tuple[int, int]:
    """How many of the corners where a feature is compared pass its threshold, and how many
    there are. A feature isn't compared where it doesn't exist (no braking point without
    braking, no pickup or lift at a turn taken flat out). The thresholds are quantiles of the
    compared corners of 2022-2024 (QUANTILES), so a rate over all corners would understate
    how often a rule fires where it can."""
    compared = values[np.isfinite(values.to_numpy(float))]
    return int((compared >= THRESHOLDS[feature]).sum()), len(compared)


def _rate(values: pd.Series, feature: str) -> float:
    fires, compared = _fires(values, feature)
    return fires / compared if compared else math.nan


def _threshold_table(sample: pd.DataFrame, fresh: dict[str, float], mistakes: pd.DataFrame):
    rows = []
    for f in DEVIATIONS:
        rule = next((t for t, feats in TYPE_RULES.items() if f in feats), None)
        support = next((t for t, feats in SUPPORTING.items() if f in feats), None)
        role = f"`{rule}`" if rule else (f"supports `{support}`" if support else "")
        if f == "segment_time_s":
            role = "slower than usual (95th percentile)"
        val = sample.loc[sample["year"] == 2025, f]
        test = sample.loc[sample["year"] == 2026, f]
        flags = ADVERSE[f] * mistakes[f"z_{f}"]
        label = f"{f} ({'higher' if ADVERSE[f] > 0 else 'lower'})"
        if f == "brake_start_d":
            label = "braking point, see below (earlier)"
        rows.append(
            {
                "feature (adverse direction)": label,
                "used for": role,
                "threshold (z)": f"{THRESHOLDS[f]:.2f}",
                "this run's calibration": f"{fresh[f]:.2f}",
                "fires on ordinary 2025": _pct1(*_fires(val, f)),
                "ordinary 2026": _pct1(*_fires(test, f)),
                "flags": _pct(int((flags >= THRESHOLDS[f]).sum()), len(flags)),
            }
        )
    return pd.DataFrame(rows)


def _examples(mistakes: pd.DataFrame) -> list[str]:
    """One typical explanation per type: a random flag (fixed seed) on a clean lap, compared
    with the driver's own laps, losing 0.15-1.5 s with nothing else going on (for the types
    find_mistakes lists), from a different session each time. The most unusual or costly flags
    are mostly slow-downs and oddities, so they would make poor examples."""
    lines, used = [], set()
    order = ["late_throttle", "over_slowing", "early_braking", "hesitation", "track_limits"]
    order += ["incident", "unclear", "lift_on_push_lap", "lift_and_coast", "slow_approach"]
    order += ["slowdown", "no_time_lost"]
    order += ["data_problem"]
    for kind in order:
        part = mistakes[mistakes["mistake_type"] == kind]
        typical = part["lap_class"].isin(CLEAN_CLASSES) & (part["reference"] == "driver")
        if kind not in NOT_MISTAKES:
            typical &= part["time_lost_s"].between(0.15, 1.5) & (part["context"] == "")
        if typical.any():
            part = part[typical]
        if kind == "lift_and_coast" and (part["year"] == 2026).any():
            part = part[part["year"] == 2026]
        for row in part.sample(frac=1.0, random_state=0).to_dict("records"):
            session = (row["year"], row["round"], row["session"])
            if session in used:
                continue
            used.add(session)
            lines.append(f"- `{kind}`: {_event_label(row)}, {row['driver']}: {row['explanation']}")
            break
    return lines


def _checked_by_eye(mistakes: pd.DataFrame) -> pd.DataFrame:
    by_id = {r["segment_id"]: r for r in mistakes.to_dict("records")}
    rows = []
    for segment_id, seen in CHECKED_BY_EYE:
        if segment_id not in by_id:
            continue
        row = by_id[segment_id]
        rows.append(
            {
                "flag": f"{_event_label(row)}, {row['driver']} lap {row['lap_number']} "
                f"{_turn(row)}",
                "what the trace shows": seen,
                "type now": f"`{row['mistake_type']}`",
                "time lost (s)": f"{row['time_lost_s']:.2f}",
            }
        )
    return pd.DataFrame(rows)


TRAFFIC_KINDS = (*TRAFFIC, "not_push_lap")
PREREAD = "m4_review_preread_v1.csv"  # Claude's reading of the top 2026 flags (see below)


def _traffic_involved(mistakes: pd.DataFrame) -> pd.Series:
    """Flags whose loss the traffic rules explain: a traffic type or a qualifying lap that
    wasn't a full push lap, a driving flag covered by traffic at a neighbouring turn, or race
    control's type with a car already at the driver as its loss began (listed, ranked on the bonus
    alone; traffic_explained)."""
    covered = mistakes["covered_by"].fillna("") != ""
    driving = ~mistakes["mistake_type"].isin([*NOT_MISTAKES, *NAMED])
    return (
        mistakes["mistake_type"].isin(TRAFFIC_KINDS)
        | (mistakes["mistake_type"].isin(NAMED) & traffic_explained(mistakes))
        | (covered & driving)
    )


# Cases the review of the traffic rules raised (gaps then measured 450 m before the apex),
# with what it found: the report prints what the explanations make of them now.
TRAFFIC_REVIEWED = (
    ("2026_12_R_11_59_T10", "blue-flag yield: LEC (2 laps ahead) 1.8 s back 450 m before the "
     "apex, 0.8-1.0 s when PER braked early; LEC went past on the way in"),
    ("2023_11_R_2_51_T1A", "blue-flag yield: VER (a lap ahead) leaving the pits, no position "
     "450 m before the apex, then 0.7-1.1 s back; SAR braked on the straight"),
    ("2022_12_R_24_41_T10", "yield: PER (a lap ahead) across a feed hole 450 m before the "
     "apex, past 390 m before it; RUS 1.9 s back went past too"),
    ("2023_16_R_20_48_T16", "a real mistake (a possible lock-up): NOR (a lap ahead) still 2.0 s "
     "back when the loss began, past only after it"),
    ("2026_14_R_77_48_T5", "yield: braking on the straight after the turn with ANT (3 laps "
     "ahead) closing, about 2.0 s back when the loss began"),
    ("2026_10_R_14_29_T1", "yield or a slow stretch: HAM (a lap ahead) about 2.0 s back when "
     "the loss began; ALO lifted and braked on the exit"),
    ("2022_03_R_10_53_T13", "a real mistake: BOT (same lap) 0.6 s back when GAS's loss began, "
     "not slowed, passed because of it"),
    ("2026_12_R_22_43_T13", "a real mistake (track limits): NOR (a lap ahead) 1.3 s back when "
     "the loss began, past only after it; had ranked on the bonus alone"),
    ("2026_05_S_6_6_T2", "a slow-down: laps 5 and 6 20 s and 16 s slow before a pit stop"),
    ("2026_03_R_12_53_T18", "the run to the flag: leading on the final lap, off the throttle "
     "and on the brake on the straight to the line"),
)  # fmt: skip


def _reviewed_rows(mistakes: pd.DataFrame) -> pd.DataFrame:
    """The TRAFFIC_REVIEWED flags, with their type and ranking now."""
    by_id = {str(r["segment_id"]): r for r in mistakes.to_dict("records")}
    listed = dict(zip(mistakes["segment_id"], listed_flags(mistakes), strict=True))
    rows = []
    for segment_id, found in TRAFFIC_REVIEWED:
        if segment_id not in by_id:
            continue
        f = by_id[segment_id]
        rows.append(
            {
                "flag": f"{_event_label(f)}, {f['driver']} lap {f['lap_number']} {_turn(f)}",
                "the review found": found,
                "type before the traffic rules": f"`{f['type_without_traffic']}`",
                "type now": f"`{f['mistake_type']}`"
                + (f" ({f['traffic_cars']})" if f["traffic_cars"] else ""),
                "listed now": f"yes (priority {f['priority']:.2f})" if listed[segment_id] else "no",
            }
        )
    return pd.DataFrame(rows)


def _without_traffic(mistakes: pd.DataFrame) -> pd.DataFrame:
    """The flags as find_mistakes had them before the traffic rules: each flag's type without
    them, nothing covered, and the time lost counting in the ranking."""
    before = mistakes.drop(columns=["merged_into", "also_turns"]).assign(
        mistake_type=mistakes["type_without_traffic"], covered_by=""
    )
    before["priority"] = priority(
        before["time_lost_s"],
        before["score_pct"],
        before["race_control_bonus"],
        field_loss_s=before["field_loss_s"],
    )
    return merge_overlapping(before)


def _top3(flags: pd.DataFrame) -> pd.DataFrame:
    """Each race's (and sprint's) three highest-ranked findings in find_mistakes."""
    distinct = listed_flags(flags) & (flags["merged_into"] == flags["segment_id"])
    races = flags[distinct & flags["session"].isin(RACE_CODES)]
    ranked = races.sort_values(["priority", "score_pct"], ascending=False)
    return ranked.groupby(["year", "round", "session"], sort=False).head(3)


def _session_of(flags: pd.DataFrame) -> np.ndarray:
    return (
        flags["year"].astype(str) + "_" + flags["round"].astype(str) + flags["session"]
    ).to_numpy()


def _top3_table(mistakes: pd.DataFrame) -> pd.DataFrame:
    """What share of each race's top-3 findings were traffic, before the traffic rules (read
    with what the flags are now) and after."""
    involved = pd.Series(_traffic_involved(mistakes).to_numpy(), index=mistakes["segment_id"])
    before, after = _top3(_without_traffic(mistakes)), _top3(mistakes)
    rows = []
    for label, years in (("2022-2025", range(2022, 2026)), ("2026", [2026]), ("all", None)):
        b = before if years is None else before[before["year"].isin(list(years))]
        a = after if years is None else after[after["year"].isin(list(years))]
        was = involved.reindex(b["segment_id"]).fillna(False).to_numpy(bool)
        now = involved.reindex(a["segment_id"]).fillna(False).to_numpy(bool)
        per_b = pd.Series(was, index=_session_of(b)).groupby(level=0).any()
        per_a = pd.Series(now, index=_session_of(a)).groupby(level=0).any()
        sessions = len(per_b)
        rows.append(
            {
                "races and sprints": label,
                "sessions": sessions,
                "top-3 findings before": len(b),
                "traffic before": f"{int(was.sum())} ({_pct(int(was.sum()), len(b))})",
                "sessions with traffic in their top 3, before": f"{int(per_b.sum())}",
                "top-3 findings after": len(a),
                "traffic after": f"{int(now.sum())} ({_pct(int(now.sum()), len(a))})",
                "sessions with traffic in their top 3, after": f"{int(per_a.sum())}",
            }
        )
    return pd.DataFrame(rows)


def _traffic_counts(mistakes: pd.DataFrame) -> pd.DataFrame:
    """Flags by traffic type (and the other ways traffic reaches the ranking) and year."""
    covered = mistakes["covered_by"].fillna("") != ""
    named = mistakes["mistake_type"].isin(NAMED) & ((mistakes["traffic"] != "") | covered)
    explained = traffic_explained(mistakes)
    before = ~mistakes["type_without_traffic"].isin(NOT_MISTAKES)
    kinds = {
        **{f"`{k}`: {TYPE_TEXT[k]}": mistakes["mistake_type"] == k for k in TRAFFIC_KINDS},
        "driving flag covered by traffic at a neighbouring turn": covered
        & ~mistakes["mistake_type"].isin([*NOT_MISTAKES, *NAMED]),
        "race control's type, a car already at the driver as the loss began (listed, ranked "
        "on the bonus alone)": named & explained,
        "race control's type, traffic around but not at the driver yet (listed, ranked on the "
        "full loss)": named & ~explained,
    }
    years = sorted(mistakes["year"].unique())
    rows = []
    for label, mask in kinds.items():
        row: dict[str, object] = {"": label}
        for year in years:
            row[str(year)] = int((mask & (mistakes["year"] == year)).sum())
        row["all"] = int(mask.sum())
        row["listed before"] = int((mask & before).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def _traffic_examples(mistakes: pd.DataFrame) -> list[str]:
    """One explanation per traffic type, as `_examples` picks them (a random flag, fixed seed,
    losing 0.3-2 s), 2026 first."""
    lines = []
    for kind in TRAFFIC_KINDS:
        part = mistakes[
            (mistakes["mistake_type"] == kind) & mistakes["time_lost_s"].between(0.3, 2)
        ]
        if (part["year"] == 2026).any():
            part = part[part["year"] == 2026]
        if len(part):
            row = part.sample(1, random_state=0).iloc[0]
            lines.append(f"- `{kind}`: {_event_label(row)}, {row['driver']}: {row['explanation']}")
    return lines


def _preread_rows(mistakes: pd.DataFrame, path: Path) -> pd.DataFrame:
    """The flags of the pre-read (`PREREAD`), with what the explanations make of them now."""
    if not path.exists():
        return pd.DataFrame()
    read = pd.read_csv(path)
    now = mistakes.set_index("segment_id")
    listed = listed_flags(mistakes).to_numpy()
    is_listed = dict(zip(mistakes["segment_id"], listed, strict=True))
    explained = dict(zip(mistakes["segment_id"], traffic_explained(mistakes), strict=True))
    rows = []
    for r in read.to_dict("records"):
        if r["id"] not in now.index:
            continue
        f = now.loc[r["id"]]
        kind, before = str(f["mistake_type"]), str(f["type_without_traffic"])
        cars, listed = str(f["traffic_cars"]), bool(is_listed[r["id"]])
        earlier = None if listed else _listed_before(mistakes, r["id"])
        if kind in TRAFFIC:
            why = f"`{kind}` ({cars})"
            if earlier is not None:
                why += (
                    f"; its loss runs on from turn {_turn(earlier)[1:]}, listed as "
                    f"`{earlier['mistake_type']}` (priority {earlier['priority']:.2f})"
                )
        elif kind == "not_push_lap":
            why = f"`{kind}`: {f['lap_deficit_s']:.1f} s down before the corner"
        elif kind in NAMED and (f["traffic"] or f["covered_by"]):
            what = f["traffic"] or "covered"
            ranked = (
                "ranked on the bonus alone"
                if explained[r["id"]]
                else "ranked on the full loss: no car alongside yet when the loss began"
            )
            why = f"race control's type; `{what}` ({cars}): {ranked}"
        elif not listed:
            why = f"left out as before (`{kind}`)"
        elif f.get("traffic_maybe", ""):
            why = f"{f['traffic_maybe']} placed only roughly (sector times on a pit or slow lap)"
        elif r["session"] not in QUALI_CODES:
            why = "no car close enough for a traffic type"
        elif not np.isfinite(f["lap_deficit_s"]):
            why = "no other push lap to compare the lap with"
        elif f["lap_usual_s"] < PREP_MIN_ELAPSED_S:
            why = (
                f"{f['lap_usual_s']:.0f} s into the lap, before the full-push-lap check starts "
                f"({PREP_MIN_ELAPSED_S:.0f} s)"
            )
        else:
            share = f["lap_deficit_s"] / f["lap_usual_s"]
            state = f"{share:.1%} slower" if share > 0 else "no slower"
            why = (
                f"lap {state} than usual before the corner (a full push lap below {PREP_SHARE:.1%})"
            )
        rows.append(
            {
                "#": int(r["rank"]),
                "flag": f"{r['session']} {r['driver']} lap {r['lap']} T{r['corner_label']}",
                "pre-read": str(r["claude_verdict"]),
                "type before": f"`{before}`",
                "type now": f"`{kind}`",
                "listed now": "yes"
                if listed
                else (f"as turn {_turn(earlier)[1:]}" if earlier is not None else "no"),
                "why": why,
            }
        )
    return pd.DataFrame(rows)


def _listed_before(mistakes: pd.DataFrame, segment_id: str) -> dict | None:
    """For a flag find_mistakes doesn't list, the listed flag of the same lap at the turn just
    before it whose window overlaps its own and holds where its loss began (its loss carried
    on into this window): the same moment, listed there. None otherwise."""
    flags = mistakes[
        mistakes["lap_id"] == mistakes.loc[mistakes["segment_id"] == segment_id, "lap_id"].iloc[0]
    ]
    me = flags[flags["segment_id"] == segment_id].iloc[0]
    onset = float(me.get("loss_onset_m", math.nan))
    if not np.isfinite(onset):
        return None
    start = float(me["apex_m"]) + onset
    listed = flags[listed_flags(flags)]
    before = listed[
        (listed["apex_m"] < me["apex_m"])
        & (listed["apex_m"] + float(DISTANCE[0]) <= start)
        & (start <= listed["apex_m"] + float(DISTANCE[-1]))
    ]
    if before.empty:
        return None
    return before.sort_values("apex_m").iloc[-1].to_dict()


def traffic_section(mistakes: pd.DataFrame, preread: Path) -> list[str]:
    """The report's Traffic section (see traffic_finding and inference/traffic.py)."""
    race = mistakes["session"].isin(RACE_CODES)
    kind = mistakes["mistake_type"]
    moving = kind.isin(TRAFFIC_KINDS)
    slower = ~kind.isin(["no_time_lost", "data_problem", "no_reference"])
    was_listed = ~mistakes["type_without_traffic"].isin(NOT_MISTAKES)
    approx = mistakes["traffic_approximate"] & kind.isin(TRAFFIC)
    maybe = mistakes["traffic_maybe"].fillna("") != ""
    rough_quali = maybe & ~race
    counts = _traffic_counts(mistakes)
    explained = traffic_explained(mistakes)
    named = kind.isin(NAMED) & ((mistakes["traffic"] != "") | (mistakes["covered_by"] != ""))
    calibration = pd.DataFrame(
        [
            ("where the loss began", f"{ONSET_S:.1f} s lost",
             "the first point where the running time lost reaches it: twice the timing "
             "clock's point-to-point jitter"),
            ("being lapped: gap of the lapping car when the loss began", f"{LAPPED_GAP_S:.2f} s",
             "95th percentile of that gap on ordinary and flagged race corners where a lapping "
             "car went past after the loss began (1.75 s; 2,785 passes, median 0.63 s; 1,250 "
             "more went past before it began). Ordinary corners alone (1.43 s; 1,432 passes, "
             "median 0.51 s) leave out the costly yields, which are flagged (1.95 s over those). "
             "The time lost when one goes past stays at what letting it by costs, 0.83-0.99 s, "
             "up to 1.25 s back, then 1.39 s at 1.25-1.5 s and 1.9-2.1 s from 1.5 s to 3 s back. "
             "450 m before the apex, the old bar (1.73 s, the ordinary corners' 95th percentile "
             "there) left out yields from a car still 2 s back there"),
            ("being lapped: braking where the usual lap is on the power", f"{YIELD_GAP_S:.2f} s",
             "95th percentile of the lapping car's gap where the driver braked, on ordinary "
             "corners where it went past from there on (1.65 s; 40 corners, median 0.48 s). "
             "Braking after the turn's slowest point where the usual lap is on the power, "
             "costing time, comes with 0.2% of ordinary corners without a lapping car going "
             "past and 2.2% with one"),
            ("racing: gap of a car on the same lap at the start", f"{BATTLE_GAP_S:.2f} s",
             "95th percentile over 8,036 same-lap passes (0.58 s; median 0.18 s). Over every "
             "scored corner the median time lost when one passes is 0.35-0.42 s from up to "
             "0.75 s back, then 0.72, 1.13 and 1.71 s for each quarter second further"),
            ("racing: a car that went past was alongside when the loss began",
             f"{ALONGSIDE_S:.2f} s",
             "95th percentile of that gap on ordinary corners where a same-lap car within the "
             "battle gap went past after the loss began (0.35 s; 1,330 passes, median 0.10 s; "
             "0.36 s with the flagged corners too). "
             "The time lost is 0.48-0.50 s up to 0.3 s apart, then 0.64, 0.79 and 0.86 s for "
             "each tenth further. Or it passed before, or was slowed itself (by the corner's "
             "bar)"),
            ("race control's flag ranked on the bonus alone: a lapping car when the loss began",
             f"{YIELD_START_S:.2f} s",
             "the median gap from which drivers began to yield on ordinary corners (0.51 s): "
             "with race control naming the driver, only a lapping car that close explains the "
             "loss; a same-lap car has to be alongside (the battle bar above), a car ahead "
             "holding them up within the held-up gap, or any car past already"),
            ("racing: side by side", f"{SIDE_BY_SIDE_S:.2f} s",
             "about a car length at 200 km/h; a same-lap car that close in the window costs "
             "0.13 s at the median (20,000 ordinary corners), against 0.03 s at 0.3-0.5 s"),
            ("held up: gap behind the car ahead", f"{IMPEDE_GAP_S:.2f} s",
             "behind a car that stayed ahead through the apex and was 0.3 s slower than the "
             "driver's usual, an ordinary race corner costs 0.37 s within 0.25 s of it, "
             "0.18 s at 0.75-1 s, 0.15 s at 1-1.25 s and 0.08 s at 3-4 s: up to about 1 s "
             "the car ahead at least doubles what such a corner costs far behind it"),
            ("held up: most the driver drops back", f"{IMPEDE_DROP_S:.2f} s",
             "95th percentile (0.222 s) on those corners within 1 s (38,077)"),
            ("held up: the car ahead slower than usual by", "the corner's own bar",
             "as for 'slower than usual' (a median of 0.26 s over the flags)"),
            ("not a full push lap (qualifying)", f"{PREP_SHARE:.1%} of the lap so far",
             f"99th percentile on ordinary push-lap corners at least {PREP_MIN_ELAPSED_S:.0f} s "
             "into the lap (3.23%, 103,237 corners; in the first 15 s it is 8%, so the rule "
             "starts there)"),
            ("the run to the flag", f"the last {FLAG_RUN_CORNERS} turns",
             "of the lap a driver takes the chequered flag on, 0.3 s slower than usual 10.8%, "
             "11.4% and 10.9% of the time, against 3.8-6.2% on the two laps before; from the "
             "fourth turn from the end 9.0%, 7.8%, 7.7%"),
            ("following closely (context)", f"{FOLLOW_GAP_S:.2f} s", "as the held-up gap"),
        ],
        columns=["setting", "value", "from 2022-2025 (never 2026)"],
    )  # fmt: skip
    parts = [
        "## Traffic: the other cars",
        "Without the other cars, every loss reads as driving, and in races the costliest flags "
        "are mostly traffic: backmarkers letting the leaders by lift and brake early, and a "
        "fight for a place compromises a corner. `inference/traffic.py` places every car on "
        "the track over the session (each lap's grid clock, laid end to end across the line; no "
        "position in the pit lane or the exit and entry lanes beside the track, where the feed "
        "failed or shifted the clock, or after a retirement; laps without a "
        "grid, most qualifying out-laps among them, placed from their sector times, and an "
        "out-lap ended where the next lap starts) and looks "
        "at an extended window around each flag, from 450 m before the apex (before the "
        "previous turn's apex for four turns in five, so before the loss the corner carries "
        "in) to 445 m after it (on the flagged corners of 2022-2025, 97% of the passes by a "
        "lapping car that was already close came by then). The gap to another car at a point "
        "is how much later it went past it.",
        "**Only a car that was already close when the loss began makes the loss traffic.** "
        f"The loss begins where the running time lost first reaches {ONSET_S:.1f} s, and what "
        "counts is the closest each car came before that point (where a car has no position "
        "at the start of the window, leaving the pit lane or across a feed hole, from where it "
        "has one). Traffic comes after race control and data problems and before the slow-down "
        "check (letting two cars by is slow over several corners, and naming them says more), "
        "and only for a corner that was slower than usual. In races, in this order: **being "
        "lapped** (a car at least a lap ahead went past, after passing before the loss began or "
        f"from within {LAPPED_GAP_S:.2f} s behind then; or the driver braked after the turn "
        "where the usual lap is on the power, costing time, with it within "
        f"{YIELD_GAP_S:.1f} s, and it went past from there on), **racing another car** (a car "
        f"on the same lap, within {BATTLE_GAP_S:.1f} s at the start of the window, passed or "
        f"was passed, or came within {SIDE_BY_SIDE_S:.1f} s in the corner's own window; a car "
        f"that went past only when it was alongside, within {ALONGSIDE_S:.2f} s, as the loss "
        "began, past already, or slowed itself through the corner), and in any session "
        "**held up** (a car stayed ahead from the window start through the apex with the "
        f"driver within {IMPEDE_GAP_S:.1f} s of it, was slower through the corner than the "
        f"driver usually is, and the driver dropped back no more than {IMPEDE_DROP_S:.1f} s: "
        "it set their pace, or they had to get past it). In qualifying, a lap already clearly "
        f"slow before the corner ({PREP_SHARE:.1%} slower than the driver's usual push lap "
        "where the window starts) is **not a full push lap**: a preparation lap, a lap given "
        "up or a problem; the losses are on the corners before, so a lap given up after a "
        "mistake keeps the mistake.",
        "When a car that was further back went past, the driver lost the time some other way "
        "and the car took its chance: the flag keeps its type and the sentence says 'lost the "
        "place to X' with the gap and where it was measured. Following a car within 1 s at the "
        "start of the corner, passing a car that wasn't close and the first racing lap after "
        "a safety car or VSC are context, not types. A race-control deletion or incident keeps "
        "its type whatever the traffic (it is the driver's own); when a car that explains the "
        "loss was already at the driver as it began (alongside, past, or lapping them from "
        f"within {YIELD_START_S:.1f} s), the flag ranks on the race-control bonus alone, and "
        "otherwise on the full loss plus the bonus, with the cars as context. "
        "A driving flag at a neighbouring turn of the same lap whose window starts inside a "
        "traffic flag's extended window lost its time with those cars already close: it isn't "
        "listed either (the same moment as the traffic), while one whose window starts "
        "earlier keeps its type (its own check measured the gaps from further back).",
        "The settings, each from the races (or qualifying) of 2022-2025 over every corner the "
        "detectors score, never 2026; 'ordinary' corners are clean laps that were not flagged. "
        "`race-engineer-infer explain --calibrate-traffic` prints this evidence again from the "
        "data.",
        markdown_table(calibration),
        f"**{int(moving.sum()):,} flags are traffic** ({_pct(int(moving.sum()), len(mistakes))} "
        f"of all flags, {_pct(int((moving & race).sum()), int((slower & race).sum()))} of the "
        "race flags that were slower than usual and not a data problem), "
        f"{int((moving & was_listed).sum()):,} of them listed as driving mistakes before. "
        f"{int(approx.sum())} traffic flags rely on a car placed from its sector times on a "
        "clean racing lap (0.07 s out at the median, see `inference/traffic.py`); their "
        "sentences say so. On a pit, slow or neutralised lap such a placement is too rough "
        "for the rules: checked against the raw position feed, 4 of 5 cars that the rules had "
        "holding a driver up from such positions were 1.2-6.7 s from where their sector times "
        f"put them. So those cars don't count as traffic: {int(maybe.sum())} flags "
        f"({int(rough_quali.sum())} in qualifying, where cars on out-laps have no lap grid) "
        "name one that would have, as possibly in the way, and keep their own type. Of the "
        f"{int(named.sum())} race-control flags with traffic around, "
        f"{int((named & explained).sum())} had a car already at the driver as the loss began "
        f"and rank on the bonus alone; {int((named & ~explained).sum())} rank on the full loss.",
        markdown_table(counts),
        "**Each race's top three findings** in find_mistakes, before the traffic rules (read "
        "with what the flags are now) and after. After, the traffic left in the top three is "
        "race control's flags ranked on the bonus alone (in races with few other listed "
        "mistakes).",
        markdown_table(_top3_table(mistakes)),
        "**Cases from the review of the traffic rules** (gaps measured 450 m before the apex "
        "had made yields look like mistakes and the reverse), and what the explanations say "
        "now:",
        markdown_table(_reviewed_rows(mistakes)),
        "Examples (a random flag of each type losing 0.3-2 s, 2026 where there is one):",
        "\n".join(_traffic_examples(mistakes)),
    ]
    rows = _preread_rows(mistakes, preread)
    if len(rows):
        nots, reals = rows[rows["pre-read"] == "not"], rows[rows["pre-read"] == "real"]
        left = nots[nots["listed now"] == "no"]
        kept = reals[reals["listed now"] != "no"]
        before_out = int((nots["type before"].str.strip("`").isin(NOT_MISTAKES)).sum())
        why = left["type now"].value_counts()
        parts += [
            "### Development check against the pre-read (not an evaluation)",
            f"Before any human review, Claude read the top {len(rows)} flags of 2026 "
            f"(`report/{preread.name}`: a verdict and the reason per flag). Those readings "
            "motivated these rules, so this is a development check, not an evaluation, and "
            "no setting was tuned to it. Of the "
            f"{len(nots)} judged not a driver mistake, {len(left)} are now left out ("
            + ", ".join(f"{n} `{str(k).strip('`')}`" for k, n in why.items())
            + f"; {before_out} were left out before the traffic rules too). Of the "
            f"{len(reals)} judged real, {len(kept)} are still listed"
            + (
                f" ({int(kept['listed now'].str.startswith('as turn').sum())} under the turn "
                "before, where the loss began)."
                if kept["listed now"].str.startswith("as turn").any()
                else "."
            ),
            _preread_misses(nots, reals),
            markdown_table(rows),
        ]
    return parts


def _preread_misses(nots: pd.DataFrame, reals: pd.DataFrame) -> str:
    """What the development check's misses are, from `_preread_rows`."""
    listed = nots[nots["listed now"] != "no"]
    named = listed[listed["why"].str.startswith("race control")]
    rest = listed.drop(index=named.index)
    lost = reals[reals["listed now"] == "no"]
    elsewhere = reals[reals["listed now"].str.startswith("as turn")]

    def items(part: pd.DataFrame) -> str:
        return "; ".join(f"#{r['#']}, {r['why']}" for r in part.to_dict("records"))

    text = []
    if len(named):
        alone = named[named["why"].str.endswith("bonus alone")]
        full = named.drop(index=alone.index)
        how = []
        if len(alone):
            how.append("on the bonus alone (" + ", ".join(f"#{n}" for n in alone["#"]) + ")")
        if len(full):
            how.append("on the full loss (" + ", ".join(f"#{n}" for n in full["#"]) + ")")
        text.append(
            f"Still listed: {len(named)} race-control flags, which keep their type with the "
            "traffic in the sentence and rank " + " or ".join(how)
        )
    if len(elsewhere):
        text.append(
            "The real "
            + ("mistake" if len(elsewhere) == 1 else "mistakes")
            + f" listed under the turn before ({items(elsewhere)}): the flag itself is "
            "traffic, but its loss carries on from there"
        )
    if len(rest):
        text.append(
            f"{len(rest)} where the data shows no traffic ({items(rest)}): what the pre-read "
            "saw there (a problem or deliberate slowing with no car near, a car problem on the "
            "only timed lap, a stuck speed channel the feed checks take for a channel problem "
            "under a real loss, preparation or abandoned laps less slow before the corner than "
            "1 ordinary push lap in 100) the positions can't show"
        )
    if len(lost):
        before = [
            f"#{r['#']} was {r['type before']} before the traffic rules"
            for r in lost.to_dict("records")
            if r["type before"].strip("`") in NOT_MISTAKES
        ]
        what = "mistake" if len(lost) == 1 else "mistakes"
        text.append(
            f"The real {what} left out ({items(lost)}) happened with the other car already "
            "close: within the gap from which such cars pass on 95 ordinary corners in 100, "
            "which the rules can't tell from letting it by"
            + (f" ({', '.join(before)}, not listed either)" if before else "")
        )
    return ". ".join(text) + "." if text else ""


def known_incident_rows(root: Path, results: Path) -> list[dict[str, str]]:
    """What find_mistakes and explain_corner say about each KNOWN_INCIDENTS case."""
    from race_engineer.tools.mistakes import explain_corner, find_mistakes  # tools import us
    from race_engineer.tools.sessions import ToolInputError

    def first_sentence(exc: Exception) -> str:
        text = str(exc)
        if "No current scoring run" in text:  # the same for every case: say it shortly
            return "the results record no current scoring run (`race-engineer-infer score`)."
        return text.split(". ")[0].removesuffix(".") + "."

    rows = []
    for inc in KNOWN_INCIDENTS:
        row = {
            "case": f"{inc.year} {inc.event} {inc.session}, {inc.driver} lap {inc.lap} "
            f"T{inc.corner}",
            "record": inc.record,
        }
        found = None
        try:
            found = find_mistakes(
                inc.event, inc.driver, inc.year, inc.session, MAX_LISTED, root, results
            )
        except ToolInputError as exc:
            row["find_mistakes"] = f"error: {first_sentence(exc)}"
        try:
            corner = explain_corner(
                inc.event, inc.driver, inc.lap, inc.corner, inc.year, inc.session, root, results
            )
        except ToolInputError as exc:
            row.setdefault("find_mistakes", "not listed (no data for the corner)")
            row["explain_corner"] = f"error: {first_sentence(exc)}"
            row["plausible type"] = "no (missed)"
            row["driving rules alone"] = "n/a"
            rows.append(row)
            continue
        exp = corner.explanation
        ranks = [
            i
            for i, m in enumerate(found.mistakes if found else [], 1)
            if m.lap_number == inc.lap and corner.turn in (m.turn, *m.also_turns)
        ]
        if ranks and found is not None:
            listing = f"listed #{ranks[0]} of {len(found.mistakes)} for {inc.driver}"
            head = found.mistakes[ranks[0] - 1]
            if head.turn != corner.turn:
                listing += f" (under turn {head.turn}, whose window overlaps)"
            elif head.also_turns:
                listing += f" (with turn {', '.join(head.also_turns)}: overlapping windows)"
        elif corner.flagged:
            listing = f"flagged but not listed (type `{exp.mistake_type}`)"
        elif corner.flagged is None:  # why, as explain_corner says it
            listing = f"not listed: {corner.unscored.split(':')[0].removesuffix('.')}"
        else:
            listing = f"not flagged (percentile {100 * (corner.score_pct or 0):.1f}), not listed"
        types = f"`{exp.mistake_type}`"
        if exp.secondary_type:
            types += f" / `{exp.secondary_type}`"
        row["case"] = (
            f"{inc.year} {corner.session.event} {corner.session.name}, {inc.driver} lap "
            f"{inc.lap} T{corner.turn}"
        )
        row.setdefault("find_mistakes", listing)
        row["explain_corner"] = f"{types}: {exp.sentence}"
        plausible = exp.mistake_type in inc.plausible or exp.secondary_type in inc.plausible
        row["plausible type"] = "yes" if plausible else "no"
        if plausible and corner.flagged is False:
            row["plausible type"] = "yes, but only when asked: the detectors didn't flag it"
        elif plausible and corner.flagged is None:
            row["plausible type"] = "yes (no detector verdict, see find_mistakes)"
        # The driving rules on their own, without the race-control join.
        alone = explain(load_session_segments(corner.session.key, root), exp.comparison.row)
        fits = alone.mistake_type in inc.driving or alone.secondary_type in inc.driving
        second = f" / `{alone.secondary_type}`" if alone.secondary_type else ""
        row["driving rules alone"] = (
            f"`{alone.mistake_type}`{second} ({'fits' if fits else 'does not fit'})"
        )
        rows.append(row)
    return rows


def _known_incident_summary(incidents: pd.DataFrame) -> str:
    if not len(incidents):
        return ""
    listed = incidents["find_mistakes"].str.startswith("listed")
    with_data = incidents["driving rules alone"] != "n/a"
    plausible = incidents["plausible type"].str.startswith("yes") & with_data
    fits = incidents["driving rules alone"].str.endswith("(fits)")
    missed = "; ".join(
        f"{case}: {why}"
        for case, why in zip(
            incidents.loc[~listed, "case"], incidents.loc[~listed, "find_mistakes"], strict=True
        )
    )
    corner = (
        f"explain_corner gives a plausible type for {int(plausible.sum())} of the "
        f"{int(with_data.sum())} cases with data, but with race control that is the join at "
        f"work; the driving rules alone fit {int(fits.sum())} of them."
    )
    answers = incidents["find_mistakes"]
    if answers.str.startswith("error").all() and answers.nunique() == 1:
        why = answers.iloc[0].removeprefix("error: ").removesuffix(".")
        return f"find_mistakes answered none of the cases ({why}). {corner}"
    return (
        f"find_mistakes lists {int(listed.sum())} of the {len(incidents)} cases, each as one "
        f"mistake. {corner} Not listed: {missed}."
    )


MAX_LISTED = 10


@dataclass(frozen=True)
class IncidentRecall:
    incidents: int  # single-car "leaving the track" incidents at a named turn
    flagged: int  # flagged at that turn on the posted lap or the one before
    named: int  # of those, explained with the race-control message
    opening: int  # at the start (posted on lap 1 or 2, or "LAP 1" in the message)
    scored: int  # the other misses that have a scored segment at that turn
    median_pct: float  # their highest session percentile on the two laps, median over them


def incident_recall(mistakes: pd.DataFrame, root: Path, results: Path) -> IncidentRecall:
    """How the flags cover the single-car 'leaving the track' incidents race control noted at
    a named turn (incidents are posted up to a lap late: the posted lap or the one before)."""
    events = store.connect(root).sql("select * from events").df()
    single = events[
        (events["kind"] == "incident")
        & (events["turn"] > 0)
        & events["message"].str.contains("|".join(LEFT_TRACK))
        & ~events["message"].str.contains("CARS ")
    ].rename(columns={"turn": "corner"})
    keys = ["year", "round", "session", "driver", "corner"]
    scores = pd.read_parquet(
        results / "segment_scores.parquet", columns=[*keys, "lap_number", "score_pct"]
    )
    flagged = named = opening = 0
    pct = []
    for record in single.to_dict("records"):
        laps = [record["lap_number"] - 1, record["lap_number"]]
        mine, here = mistakes, scores
        for k in keys:
            mine = mine[mine[k] == record[k]]
            here = here[here[k] == record[k]]
        mine = mine[mine["lap_number"].isin(laps)]
        here = here[here["lap_number"].isin(laps)]
        flagged += bool(len(mine))
        named += bool((mine["race_control"] != "").any())
        start = record["lap_number"] <= 2 or "LAP 1 " in str(record["message"])
        opening += start
        if not len(mine) and not start and len(here):
            pct.append(float(here["score_pct"].max()))
    return IncidentRecall(
        incidents=len(single),
        flagged=flagged,
        named=named,
        opening=opening,
        scored=len(pct),
        median_pct=float(np.median(pct)) if pct else math.nan,
    )


def _fire_range(sample: pd.DataFrame, year: int) -> str:
    """The range of the driving features' fire rates on ordinary corners of one season (of the
    corners where each is compared, `_fires`)."""
    part = sample[sample["year"] == year]
    features = [f for f in DEVIATIONS if f not in ("segment_time_s", "offset_exit_max")]
    rates = [r for r in (_rate(part[f], f) for f in features) if np.isfinite(r)]
    return f"{min(rates):.1%}-{max(rates):.1%}" if rates else "n/a"


def _coast_2026(sample: pd.DataFrame) -> str:
    """Whether the coasting and throttle-lift rules fire more often on ordinary 2026 corners
    than before (the 2026 cars harvest energy by lifting): the rates (of the corners where each
    is compared, `_fires`), each earlier season's, and Fisher's exact test against 2022-2024."""
    train, test = sample[sample["year"] <= 2024], sample[sample["year"] == 2026]
    if not len(train) or not len(test):
        return ""
    before = sample[sample["year"] < 2026]
    more, parts = [], []
    for f, what in (("coast_m", "coasting"), ("throttle_dip", "throttle-lift")):
        (fires, n), (fires_train, n_train) = _fires(test[f], f), _fires(train[f], f)
        if not n or not n_train:
            continue
        table = [[fires, n - fires], [fires_train, n_train - fires_train]]
        p = float(cast(tuple[float, float], fisher_exact(table))[1])
        rate = fires / n
        yearly = before.groupby("year")[f].apply(lambda v, f=f: _rate(v, f))
        if p < 0.05 and rate > yearly.max():
            more.append(what)
        where = "" if n == len(test) else " where it is compared, not the turns taken flat out"
        parts.append(
            f"the {what} rule fires on {rate:.1%} of ordinary 2026 corners{where} ("
            f"{yearly.min():.1%}-{yearly.max():.1%} in each of {int(yearly.index.min())}-"
            f"{int(yearly.index.max())}; Fisher's exact test against 2022-2024 p = {p:.2g})"
        )
    if len(parts) < 2:
        return ""
    rates = f"{parts[0][0].upper()}{parts[0][1:]}, and {parts[1]}"
    if not more:
        return f"{rates}: no clear difference from earlier seasons."
    if "throttle-lift" not in more:
        return f"{rates}: the 2026 cars coast more on ordinary laps (likely energy management)."
    what = "coast and lift" if len(more) == 2 else "lift"
    return (
        f"{rates}: the 2026 cars {what} more on ordinary laps (likely energy management), so "
        "some 2026 hesitation flags may be energy management too."
    )


def write_report(
    mistakes: pd.DataFrame,
    sample: pd.DataFrame,
    fresh: dict[str, float],
    path: Path,
    root: Path = PROCESSED_DIR,
    results: Path = RESULTS_DIR,
) -> Path:
    total = len(mistakes)
    counts = mistakes["mistake_type"].value_counts()
    listed = mistakes[listed_flags(mistakes)]
    distinct = listed[listed["merged_into"] == listed["segment_id"]]
    lost = distinct["time_lost_s"]
    phases = distinct["phase"].value_counts()
    data = mistakes[mistakes["mistake_type"] == "data_problem"]["data_issue"]
    issue_kinds = {
        "the timing clock disagrees with the speed trace": "timing clock says",
        "the speed trace doesn't match the distance covered": "doesn't match the distance",
        "speed channel stuck": "speed channel is stuck",
        "speed trace jumps or spikes": "speed trace jumps",
        "brake signal on at full throttle": "brake signal is on",
    }
    issue_rows = pd.DataFrame(
        [
            {"issue": k, "data-problem flags": int(data.str.contains(v).sum())}
            for k, v in issue_kinds.items()
        ]
    )
    field_share = (mistakes["reference"] == "field").mean()
    by_year = mistakes.groupby("year")["mistake_type"].apply(lambda t: (t == "data_problem").mean())
    data_by_year = ", ".join(f"{share:.0%} of {year} flags" for year, share in by_year.items())
    data_by_year = f"data problems are {data_by_year}"
    is_type = mistakes["mistake_type"]
    coasting = mistakes["z_coast_m"] >= THRESHOLDS["coast_m"]
    braking = np.column_stack(
        [ADVERSE[f] * mistakes[f"z_{f}"] >= THRESHOLDS[f] for f in BRAKING]
    ).any(axis=1)
    costly = mistakes["time_lost_s"] > COAST_COST_S_PER_M * mistakes["delta_coast_m"].clip(lower=0)
    coast_check, coast_n = coast_cost(sample)
    push_lifts = is_type == "lift_on_push_lap"
    coast_listed = coasting & ~is_type.isin(NOT_MISTAKES) & ~push_lifts
    carried = (is_type == "slow_approach") & (
        mistakes["loss_entry_s"] > MOSTLY * mistakes["time_lost_s"]
    )
    train = sample[sample["year"] <= 2024]
    outside = (sample["window_vmin_d"].abs() > APEX_BAND_M).mean()
    far = (sample["window_vmin_d"].abs() >= 100).mean()
    earlier = mistakes["explanation"].str.extract(r"braked (\d+) m earlier")[0].astype(float)
    far_braking = earlier >= 150
    via_neighbour = is_type.isin(NAMED) & (mistakes["race_control_turn"] != "")
    time_z = mistakes["z_segment_time_s"]
    time_bar = THRESHOLDS["segment_time_s"] * mistakes["time_scale_s"]
    q90 = float(train["segment_time_s"].quantile(0.9)) if len(train) else math.nan
    q99 = float(train["segment_time_s"].quantile(0.99)) if len(train) else math.nan
    not_slower = mistakes[is_type == "no_time_lost"]["time_lost_s"]
    involved = mistakes["explanation"].str.contains("involved in an incident with")
    named_many = is_type.isin(NAMED) & ~mistakes["race_control_bonus"]
    field_slow = is_type.isin(NAMED) & mistakes["field_loss_s"].notna()
    busiest = mistakes[field_slow].value_counts(["year", "event", "session"]).head(2)
    where_slow = ", most in " + " and ".join(
        f"the {_event_label(dict(zip(['year', 'event', 'session'], key, strict=True)))} ({n})"
        for key, n in zip(busiest.index.tolist(), busiest.tolist(), strict=True)
    )
    where_slow = where_slow if len(busiest) else ""

    def share(phase: str) -> str:
        return _pct(int(phases.get(phase, 0)), len(distinct))

    quali = mistakes["session"].isin(["Q", "SQ", "SS"])
    slowdowns = mistakes["explanation"].where(is_type == "slowdown", "")
    back_slow = slowdowns.str.contains("from the end of the previous lap on", regex=False)
    flag_run = slowdowns.str.contains("the run to the flag", regex=False)
    recall = incident_recall(mistakes, root, results)
    incidents = pd.DataFrame(known_incident_rows(root, results))
    parts = [
        "# M4: explaining the flagged corners",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `race-engineer-infer explain` "
        "from `data/results/segment_scores.parquet`; one row per flag in "
        "`data/results/mistakes.parquet`._",
        f"Every one of the {total:,} flagged corners (the top 1% of each session by the combined "
        "detector score, see [m4_scoring.md](m4_scoring.md)) is compared with the same driver's "
        "usual way through that turn in that session: the median of their other clean laps "
        "there (in races the 10 nearest in lap number, because a corner gets ~0.2 s faster over "
        "a race as fuel burns off), or of the field's when they have fewer than 5 "
        f"({field_share:.0%} of flags, nearly all in qualifying, where drivers do 3-4 push laps; "
        "car pace then counts towards the time lost). Time lost is the segment time (250 m "
        "before to 145 m after the apex) minus the reference's median, split into entry (to "
        "50 m before the apex), apex (to 30 m after) and exit using elapsed time integrated "
        "from the speed trace and scaled to the timing clock. The lap context (a slow stretch "
        "of the lap, the field slow at the same place) and the placing of race-control "
        "incidents use the same reference for every corner of the session. Method details are "
        "in the module docstring of `inference/explain.py`.",
        "## Mistake types and how the thresholds were set",
        "A type is read off driver-relative robust z-scores (median and MAD of the reference "
        "laps, with the baselines' minimum spreads) of the hand-crafted features, counted only "
        "in the adverse direction. Each feature's threshold is the **99th percentile of its "
        "adverse z on ordinary corners** (clean laps that were not flagged, "
        f"{len(train):,} sampled from 2022-2024), so evidence means a deviation bigger than on "
        "99 of 100 ordinary laps. The strongest rule (z over threshold) is the type, the next "
        "one the secondary type. On later seasons the same thresholds fire on "
        f"{_fire_range(sample, 2025)} of ordinary 2025 corners and {_fire_range(sample, 2026)} "
        "of ordinary 2026 ones (the offset rule aside, which almost never fires), so they hold "
        "up reasonably (2026's coasting and lifting are discussed below). These rates, like the "
        "thresholds, count only the corners where a feature is compared: a turn taken flat out "
        "has no braking point, pickup or lift, and one without braking no braking point.",
        markdown_table(_threshold_table(sample, fresh, mistakes)),
        "**Slower than usual** is a calibrated bar too: the robust z of the segment time has "
        f"to pass {THRESHOLDS['segment_time_s']:.2f}, its 95th percentile on ordinary corners, "
        f"with the time spread floored at {TIME_MIN_SCALE:.2f} s (the timing clock's own "
        "jitter, so that reference laps agreeing to the hundredth don't make a few hundredths "
        "significant). In seconds that bar is the flag's own: a median of "
        f"{time_bar.median():.2f} s over the flags (10th-90th percentile "
        f"{time_bar.quantile(0.1):.2f}-{time_bar.quantile(0.9):.2f} s; median "
        f"{time_bar[~quali].median():.2f} s in races and {time_bar[quali].median():.2f} s in "
        "qualifying, where the reference is often the field). The "
        "95th percentile rather than the 99th because the question is only whether the corner "
        "cost time beyond the lap-to-lap noise, and exit losses carry on past the window; the "
        f"99th (z {q99:.2f}) would need {q99 / THRESHOLDS['segment_time_s']:.1f} times the "
        "loss. With a 90th-percentile bar "
        f"(z {q90:.2f}), {_pct(int((time_z >= q90).sum()), total)} of flags would count as "
        f"slower instead of {_pct(int((time_z >= THRESHOLDS['segment_time_s']).sum()), total)}.",
        "Three features needed fixing before they could be calibrated at all. The hand-crafted "
        "braking point is the start of the *last* braking run before the slowest point, so a "
        "brief release of the (on/off, 4 Hz) brake moves it by 100 m or more; its adverse z "
        "passed 16 on 1% of ordinary laps. The rules use the first brake application after the "
        "driver last had full throttle instead. And the braking point, the throttle pickup and "
        "the throttle lift after it are all measured from the slowest point, which the "
        "hand-crafted features take anywhere in the 395 m window: on "
        f"{outside:.0%} of ordinary corners that is more than {APEX_BAND_M:.0f} m from the apex "
        f"({far:.0%} at least 100 m), at the previous turn's exit or in the next one's braking "
        "zone, and measured from there a pickup 'later than usual' was the previous turn's "
        "exit and an 'earlier' braking point the next turn's. The rules take the turn's own "
        f"slowest point, within {APEX_BAND_M:.0f} m of the apex. A turn usually taken flat out "
        f"({sample['taken_flat'].mean():.0%} of ordinary corners, "
        f"{mistakes['taken_flat'].mean():.0%} of flags) has none, so there the three aren't "
        "compared at all. Where turns come close together (Monaco, Jeddah T1-T3) the slowest "
        "point can still switch to the neighbouring turn on one lap: the normal blip of "
        "throttle between two turns then reads as a lift after the pickup. On such laps all "
        "three are measured from the usual slowest point instead, for the lap and its "
        f"reference alike ({mistakes['apex_moved'].mean():.0%} of flags, against "
        f"{sample['apex_moved'].mean():.0%} of ordinary corners). Compared from a moved slowest "
        "point, the throttle lift's 99th percentile had been 7.0; measured like with like it is "
        f"{fresh['throttle_dip']:.1f}.",
        "Three rules have exceptions. **Over-slowing** needs a braking zone (most reference "
        "laps brake for the turn after their last full throttle): in a corner usually taken "
        "without braking, a lower minimum speed comes from lifting. It is called a possible "
        "lock-up only when the braking was also longer or harder than usual. **Lift and coast** "
        "is the type only when coasting fires, nothing in the braking is adverse and the loss "
        "is no more than that much coasting costs: coasting always lowers the minimum and exit "
        "speeds and delays the throttle, and those effects are not typed as separate mistakes. "
        f"The cost bar is {COAST_COST_S_PER_M:.3f} s per extra metre of coasting, the 99th "
        f"percentile on {COAST_COST_CORNERS:,} ordinary corners where drivers coasted more than "
        "usual with nothing adverse in the braking (up to 60 per session; this run's random "
        f"sample gives {coast_check:.3f} from "
        f"{coast_n} such corners). A bigger loss, or adverse braking, has another cause (a "
        "problem, traffic, a slow-down, braking early): coasting doesn't explain it, so the "
        "other rules type the flag (unclear when none fires) and it is listed. A **slow "
        "approach** (inherited from before the corner) comes after the rest, except when the "
        "car arrived slower than usual and lost most of the time on entry: then the loss was "
        "carried in, the slower speeds and later throttle after it follow from arriving slow, "
        f"and the slow approach is the type ({int(carried.sum())} flags), not listed.",
        "Before the driving rules, in this order: a **track-limits deletion or race-control "
        "incident** naming the driver alone at that turn, or else at a neighbouring turn whose "
        "apex lies inside its window (at a chicane race control names one turn, and the "
        "other's window covers the same moment; incidents are posted up to a lap late, so the "
        "posted lap or the one before, whichever deviated more; a scored lap beats an out-lap, "
        "in-lap or safety-car lap, which are slow for reasons of their own), a **data "
        "problem** (below), **traffic** (the next section: being lapped, racing another car, "
        "held up by a slower one, or in qualifying a lap that wasn't a full push lap), a "
        "**slow-down** (the two corners before and most after it were "
        "also slow, or the four before it: an abandoned lap; in the last corners of a lap, the "
        "two before it slow and the car arriving slower than usual; or in races, a third of "
        "the field slow at the same turn on the same lap: something on track), and a corner "
        "that was **not clearly slower** than usual. A race-control message naming two "
        "cars ('INCIDENT INVOLVING CARS 10 (GAS) AND 18 (STR) NOTED - FORCING ANOTHER DRIVER "
        "OFF THE TRACK') doesn't say whose mistake it was, so it is context in both drivers' "
        "sentences ('involved in an incident with ...'), not a type.",
        "## What the flags turned out to be",
        markdown_table(_type_table(mistakes)),
        f"- **{_pct(int(counts.get('no_time_lost', 0)), total)} of flags were not clearly "
        "slower than usual** (median "
        f"{not_slower.median():+.2f} s; {_pct(int((not_slower > 0.2).sum()), len(not_slower))} "
        "of them more than 0.2 s slower, within a wide spread). The detectors find unusual "
        "corners, not costly ones: a later braking point to overtake, a tow, a drying track or "
        "a different line all look unusual. find_mistakes leaves them out; explain_corner "
        "still describes them, with the bar in seconds.",
        f"- **{_pct(int(counts.get('slowdown', 0)), total)} are part of a slow-down**: a slow "
        "stretch of the lap (abandoned qualifying laps, slow-downs in races), where every "
        "corner is slow and none is the mistake, or, in races, a turn where at least three "
        "other drivers and a third of the field were 0.5 s slower than usual on the same lap "
        "(something on track, or the conditions). The corners before a flag run on from the "
        "end of the previous lap when that was a racing lap too, so a slow-down that began "
        f"there reaches the first turns of the next ({int(back_slow.sum())} flags). In races "
        f"the last {FLAG_RUN_CORNERS} turns of the lap a driver takes the chequered flag on are "
        "the run to the flag, where drivers ease off for the line, unless a car passed or was "
        f"passed there ({int(flag_run.sum())} flags; on the chequered-flag laps of 2022-2025 "
        "those turns were 0.3 s slow 11% of the time, against 4-6% on the laps before). The "
        "corner where a slow stretch starts keeps its driving type, with a note that the lap "
        "may have been given up after it.",
        f"- **{_pct(int(counts.get('data_problem', 0)), total)} are data problems**, not driving "
        "(next section).",
        f"- **{len(listed):,} flags look like driving mistakes; they are {len(distinct):,} "
        f"distinct mistakes.** {len(listed) - len(distinct):,} flags are a neighbouring turn of "
        "a listed one on the same lap whose window overlaps it (apexes less than "
        f"{WINDOW_SPAN_M:.0f} m apart): one moment seen twice, such as a spin that shows in "
        "three windows. find_mistakes lists each moment once, under the turn where it started "
        "(the one race control named, if any; else the first whose loss wasn't mostly on "
        "entry, since a later window re-counts the loss carried in from the one before), and "
        "names the others; their losses are never added up. A distinct mistake costs "
        f"{lost.median():.2f} s at the median (75th percentile {lost.quantile(0.75):.2f} s, "
        f"90th {lost.quantile(0.9):.2f} s), lost mostly on exit in {share('exit')}, on entry "
        f"in {share('entry')} and through the apex in {share('apex')}. The exit share is a "
        "lower bound: a slow exit keeps costing time on the straight after the window.",
        f"- Late throttle / slow exit is the commonest driving type "
        f"({int(counts.get('late_throttle', 0)):,}), then over-slowing "
        f"({int(counts.get('over_slowing', 0)):,}); early braking is rare "
        f"({int(counts.get('early_braking', 0)):,}) because braking points are only accurate "
        "to one 4 Hz sample (15-20 m at speed), so the threshold is high. Running wide almost "
        "never shows in `offset_exit_max`: the position feed is smoothed (typical offsets "
        "0.5 m), so a wide exit shows as a slower exit and a later throttle instead.",
        f"- Coasting fires on {int(coasting.sum()):,} flags. "
        f"{int(counts.get('lift_and_coast', 0)):,} are typed lift and coast "
        f"({int(((is_type == 'lift_and_coast') & (mistakes['year'] == 2026)).sum())} in 2026), "
        "labelled energy management (2026) or fuel and tyre saving (races), not a mistake; "
        f"{int(push_lifts.sum())} on qualifying push laps before 2026, where there is nothing "
        "to save, are listed as a lift on a push lap. "
        f"{int(coast_listed.sum())} others with coasting are listed as mistakes "
        f"({int((coast_listed & (mistakes['year'] == 2026)).sum())} in 2026): race control "
        f"named the driver, they braked early, longer or harder too "
        f"({int((coast_listed & braking).sum())}), or they lost more than coasting costs "
        f"({int((coast_listed & ~braking & costly).sum())}). {_coast_2026(sample)}",
        f"- Race control named the driver alone at a flagged turn "
        f"{int(is_type.isin(NAMED).sum())} times ("
        f"{int((is_type.isin(NAMED) & (mistakes['race_control_turn'] != '')).sum())} of them at "
        "a neighbouring turn inside the window, "
        f"{int(counts.get('incident', 0))} for something other than leaving the track or track "
        "limits), and "
        f"{int(involved.sum())} flags carry a two-car message as context instead. In races, "
        "race control naming three or more drivers at one turn "
        f"on one lap takes away the ranking bonus ({int(named_many.sum())} flags), and when "
        f"the field was slow there too ({int(field_slow.sum())} flags{where_slow}) only the "
        "loss beyond the field's counts towards the ranking.",
        f"- {_pct(int(quali.sum()), total)} of flags are in qualifying sessions; qualifying "
        "flags are compared with the field more often than not.",
        *traffic_section(mistakes, REPORT_DIR / PREREAD),
        "## Data problems: flags about the feed, not the driving",
        "Checking the top flags against their traces by eye showed that some are feed "
        "problems. Each became an automatic check (`data_issues`):",
        markdown_table(issue_rows),
        f"The feed got worse in 2026: {data_by_year}. Looking at 2026 examples by eye, they "
        "are real glitches: one-point speed spikes to 200 km/h in a 100 km/h corner (at the "
        "same turn for several drivers) and speed frozen through a braking zone.",
        "A problem with one channel (speed, brake) doesn't hide a loss of "
        f"{REAL_LOSS_S:.1f} s or more that the timing clock confirms: the flag keeps its "
        "driving type and the sentence mentions the problem. Nor does any check override race "
        "control naming the driver at that turn, because leaving the track (cutting a chicane) "
        "or contact itself breaks the link between speed and distance along the reference "
        "line that the timing checks rely on: the misalignment check can't tell a cut chicane "
        "from misaligned position data, and some of its flags are probably real cuts or offs "
        "that race control didn't name.",
        "Flags checked by eye while building this, and what the explanations say now:",
        markdown_table(_checked_by_eye(mistakes)),
        "## Examples",
        "A typical flag of each type: a random one (fixed seed) on a clean lap, compared with "
        "the driver's own laps and (for the types find_mistakes lists) losing 0.15-1.5 s with "
        "nothing else going on, one per session. The most unusual or costly flags are mostly "
        "slow-downs and oddities: the most unusual late-throttle flag in qualifying, for "
        "example, is a driver braking hard after the last corner of a push lap, most likely "
        "for a flag the telemetry can't show.",
        "\n".join(_examples(mistakes)),
        "## find_mistakes ranking",
        "find_mistakes ranks the distinct mistakes by time lost, discounted by up to half when "
        "the detectors found the corner only borderline unusual (the flag's percentile within "
        f"the session's top 1%), plus {RACE_CONTROL_BONUS_S:.1f} s when race control named the "
        "driver alone at that turn (a deleted lap or a penalty costs more than the corner "
        "itself, and it is the surest sign of a mistake). When the field was slow at that turn "
        "on that lap too, only the loss beyond the other drivers' median counts, and when "
        "traffic explains the loss of a flag race control named (a car of its own traffic, or "
        "of the traffic at a neighbouring turn covering it, was already at the driver when the "
        "loss began: alongside, past, or lapping them from within "
        f"{YIELD_START_S:.1f} s), none of it does: it ranks on the bonus alone. A car that was "
        "still further back then didn't cause the loss, so the flag ranks on the full loss plus "
        "the bonus. find_mistakes' ranking line says so. Time lost is what a mistake costs; the "
        "discount keeps a slow corner the detectors barely noticed below an equally slow one "
        "they are sure about.",
        "## Known incidents",
        "The M4 check: do the tools surface documented incidents with a plausible type? The "
        "documented cases in the data are race-control ones: the deleted lap of COL in "
        "Melbourne, Russell's cuts of the Monaco chicane (lap 48, which race control noted, "
        "and lap 47, also deleted there), a 2026 race-control incident, and one chosen "
        "because it is famous and expected to be hard (an incident at the start). The dataset "
        "has no race-control messages about spins.",
        "**This check is partly circular**: a track-limits deletion or a single-car incident "
        "at the flagged turn becomes the type automatically once the corner is flagged, so a "
        "`track_limits` type there tests the flagging and the events join, not the driving "
        "rules. The last column runs the driving rules alone (no race control), which is the "
        "real test of the explanations; a cut chicane gains time, so 'not clearly slower' is "
        "the fair answer there.",
        markdown_table(incidents) if len(incidents) else "(no cases could be run)",
        _known_incident_summary(incidents),
        f"**Systematically**, race control noted {recall.incidents} single-car 'leaving the "
        f"track' incidents at a named turn. Only {recall.flagged} were flagged at that turn on "
        f"the posted lap or the one before ({recall.named} of them explained with the "
        f"race-control message). {recall.opening} happened at the start, which isn't scored "
        "(the opening lap is excluded, and the first turns often have no window at all because "
        "it would start before the line). Of the other misses with a scored corner "
        f"({recall.scored}), the median was at percentile {100 * recall.median_pct:.0f} of its "
        "session: unusual, but not in the top 1%. Leaving the track 'and gaining an advantage' "
        "is usually a cut that saves time, and with a smoothed position feed a cut looks much "
        "like a normal corner; the detectors look for corners that are unusual in shape, and "
        "a small cut isn't.",
        "## Limits",
        "- Types are rules on hand-crafted features, calibrated but not validated against "
        "labels (only track limits are labelled). They describe *how* a corner differed, not "
        "*why*: a tow, a car problem or a strategy look the same in the data. Traffic is read "
        "from where the cars were (next bullet).",
        "- Traffic comes from positions on the reference line, a few metres apart at best: "
        "'side by side' can't tell alongside from nose to tail, a car that pits has no "
        "position in the pit lane, and cars on qualifying out-laps (no lap grid) are placed "
        "only roughly from their sector times, seconds out at worst, so they never count as "
        "traffic: a driver really held up by one keeps a driving type, with the car named as "
        "possibly in the way. A mistake made while a car "
        "was already close counts as traffic when that car then passes: the rules can't tell "
        "the two apart. A car slowing for the pit entry is still on the track line until it "
        "turns in, so a driver passing it there reads as a pass. A stretch of a lap the feed "
        "shifted is dropped only when a later feed hole shifts it back; a shift that nothing "
        "undoes stays, as it could as well be before the hole. A yield that is only a lift "
        "on the power, with the lapping car further back than the lapping gap when the loss "
        "began, stays a driving type (a lift looks the same as a traction loss).",
        "- In qualifying the reference is usually the field, so a slower car's corners look "
        "like time lost and a faster car's mistakes look smaller.",
        "- Time lost stops at 145 m after the apex, so exit mistakes are undercounted.",
        "- The data-problem checks were set by eye on a few dozen flags; they will miss short "
        "glitches and can take a real cut or off for misaligned data.",
        "- Which lap a race-control incident belongs to is a guess when it was posted a lap "
        "late (the lap that deviated more at that turn).",
        "- The braking point is measured from the driver's last full throttle, so on a lap that "
        "never got back to full throttle between two turns it is the previous turn's: "
        f"{int(far_braking.sum())} flags say they braked 150 m or more earlier than usual, "
        "most of them for that reason. Measuring from any throttle instead split the usual "
        "laps' braking zones at brief blips of throttle and did worse.",
        "- Where race control named a neighbouring turn inside the window, the flag gets the "
        f"race-control type too ({int(via_neighbour.sum())} flags): at a chicane that is the "
        "same moment, but a deletion at a turn near the window's edge may be about a different "
        "one.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(parts) + "\n")
    log.info("wrote %s", path)
    return path


def main(argv: Sequence[str] | None = None, prog: str | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog=prog or "python -m race_engineer.inference.explain",
        description="Type and cost every flagged corner of the current scoring run -> "
        "data/results/mistakes.parquet and report/m4_mistakes.md.",
    )
    parser.add_argument(
        "--allow-unrecorded",
        action="store_true",
        help="explain scores from before scoring runs were recorded (no run id in the manifest "
        "nor in the scores); scores from a recorded run that isn't the current one still fail",
    )
    parser.add_argument("--no-report", action="store_true", help="write mistakes.parquet only")
    parser.add_argument(
        "--calibrate-traffic",
        action="store_true",
        help="print the evidence for the traffic settings from every scored corner of 2022-2025 "
        "(a few minutes; writes nothing)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", force=True
    )
    if args.calibrate_traffic:
        scores = pd.read_parquet(
            RESULTS_DIR / "segment_scores.parquet",
            columns=["segment_id", "year", "round", "session", "flagged"],
        )
        for name, text in calibrate_traffic(scores).items():
            print(f"- {name}: {text}")
        return
    report = None if args.no_report else REPORT_DIR / "m4_mistakes.md"
    try:
        run_explain(report=report, allow_unrecorded=args.allow_unrecorded)
    except provenance.StaleResultsError as err:  # as race-engineer-infer prints it
        raise SystemExit(f"error: {err}") from None


if __name__ == "__main__":
    main()
