"""Mistake explanations on synthetic corners whose mistakes are known.

Every lap takes one corner the same way: 250 km/h, braking at -120 m down to 100 km/h at the
apex, back on full throttle 10 m after it. A mistake lap changes one thing (brakes 50 m early,
picks up the throttle 40 m late, ...), and the explanation has to name it.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from test_inference_traffic import (
    GRID_M,
    Lap,
    lap_clock,
    lapped_laps,
    lapped_session,
    laps_of,
    make_tracks,
    speeds,
)

from race_engineer.data import store
from race_engineer.features.handcrafted import DISTANCE
from race_engineer.inference import provenance, traffic
from race_engineer.inference.explain import (
    ALONGSIDE_S,
    BATTLE_GAP_S,
    DEVIATIONS,
    FLAG_RUN_CORNERS,
    LAPPED_GAP_S,
    MIN_THRESHOLD,
    NOT_MISTAKES,
    PHASES,
    YIELD_GAP_S,
    YIELD_START_S,
    SessionSegments,
    _coast_2026,
    _gap_range,
    _tell_covered,
    apex_relative,
    braking_point,
    calibrate_thresholds,
    cars_around_text,
    clean_mask,
    compare,
    explain,
    lap_losses,
    listed_flags,
    loss_onset,
    merge_overlapping,
    priority,
    race_control,
    reference_rows,
    run_explain,
    session_segments,
    speed_clock,
    traffic_cover,
    traffic_explained,
    yield_point,
)
from race_engineer.inference.traffic import Encounter

COAST_DECEL = 4.0  # m/s^2 rolling off both pedals (drag and engine braking at 250 km/h)
TEAMS = {"AAA": ("1", "Team A"), "BBB": ("2", "Team B"), "CCC": ("3", "Team C")}
TEAMS |= {"DDD": ("4", "Team D"), "EEE": ("5", "Team E")}


def corner_trace(
    *,
    v_start: float = 250.0,
    brake_d: float = -120.0,
    v_min: float = 100.0,
    pickup_d: float = 10.0,
    accel: float = 8.0,
    coast_d: float | None = None,
    dip: bool = False,
    stuck_brake: bool = False,
    clock_jump: float = 0.0,
    scale: float = 1.0,
) -> dict[str, np.ndarray]:
    """One pass through the corner on the 80-point window (see the module docstring).

    Braking runs from `brake_d` to just before the apex at a constant deceleration; the car
    holds `v_min` from the apex to `pickup_d`, then accelerates at `accel` (much less during
    a throttle `dip` from 60 to 80 m, where it slows a little). `coast_d` lifts off before
    braking; `scale` slows the whole corner down (an abandoned lap)."""
    d = DISTANCE
    v0, vmin = v_start / 3.6, v_min / 3.6
    lift = brake_d if coast_d is None else coast_d
    v2 = np.where(d > lift, v0**2 - 2 * COAST_DECEL * (d - lift), v0**2)
    v_brake2 = v0**2 - 2 * COAST_DECEL * (brake_d - lift)
    decel = (v_brake2 - vmin**2) / (0 - brake_d)
    braking = (d >= brake_d) & (d < 0)
    v2 = np.where(braking, v_brake2 - decel * (d - brake_d), v2)
    push = np.where((d >= 60) & (d < 80), -2.0, accel) if dip else np.full(len(d), accel)
    gained = np.cumsum(np.where(d > pickup_d, 2 * push * 5.0, 0.0))
    v2 = np.where(d >= 0, vmin**2 + gained, v2)
    speed = np.sqrt(v2) * 3.6 * scale
    throttle = np.where(d < lift, 100.0, 0.0)
    throttle = np.where((d >= 0) & (d <= pickup_d), 30.0, throttle)
    throttle = np.where(d > pickup_d, 100.0, throttle)
    if dip:
        throttle = np.where((d >= 60) & (d < 80), 20.0, throttle)
    brake = braking.astype(float)
    if stuck_brake:
        brake = np.where(d >= 30, 1.0, brake)
    mps = speed / 3.6
    time_s = np.concatenate([[0.0], np.cumsum(5.0 / ((mps[1:] + mps[:-1]) / 2))])
    time_s[2:] += clock_jump
    return {
        "time_s": time_s,
        "speed": speed,
        "throttle": throttle,
        "brake": brake,
        "gear": np.clip(speed // 40, 2, 8),
        "offset_m": np.zeros(len(d)),
    }


def shaped_trace(
    speed: list[tuple[float, float]],
    throttle: list[tuple[float, float]],
    brake: tuple[tuple[float, float], ...] = (),
) -> dict[str, np.ndarray]:
    """A trace drawn by hand: speed knots (distance, km/h; straight lines between them),
    throttle steps (the value from each distance on) and braking ranges (from, to)."""
    d = DISTANCE
    knots_d, knots_v = zip(*speed, strict=True)
    v = np.interp(d, knots_d, knots_v)
    thr = np.zeros(len(d))
    for start, value in throttle:
        thr[d >= start] = value
    brk = np.zeros(len(d))
    for start, end in brake:
        brk[(d >= start) & (d < end)] = 1.0
    mps = v / 3.6
    return {
        "time_s": np.concatenate([[0.0], np.cumsum(5.0 / ((mps[1:] + mps[:-1]) / 2))]),
        "speed": v,
        "throttle": thr,
        "brake": brk,
        "gear": np.clip(v // 40, 2, 8),
        "offset_m": np.zeros(len(d)),
    }


def lap_spec(driver: str, lap: int, corner: int = 3, **trace) -> dict:
    """A lap through one corner: meta fields (lap_class, deleted, apex_m, letter) and the
    corner_trace arguments, or `shape=` a finished trace (see shaped_trace)."""
    meta = {
        k: trace.pop(k) for k in ("lap_class", "deleted", "apex_m", "letter", "gap") if k in trace
    }
    return {"driver": driver, "lap": lap, "corner": corner, "meta": meta, "trace": trace}


def build_table(
    specs: list[dict], year: int = 2026, round_: int = 1, code: str = "Q"
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Segment meta rows and (n, 80) traces, in the processed segments' columns."""
    key = store.session_key(year, round_, code)
    rows, traces = [], []
    for spec in specs:
        number, team = TEAMS[spec["driver"]]
        meta = spec["meta"]
        letter = meta.get("letter", "")
        rows.append(
            {
                "segment_id": f"{key}_{number}_{spec['lap']}_T{spec['corner']}{letter}",
                "lap_id": f"{key}_{number}_{spec['lap']}",
                "year": year,
                "round": round_,
                "session": code,
                "driver": spec["driver"],
                "driver_number": number,
                "team": team,
                "lap_number": spec["lap"],
                "corner": spec["corner"],
                "corner_letter": letter,
                "apex_m": meta.get("apex_m", 1000.0 * spec["corner"]),
                "lap_class": meta.get("lap_class", "push" if code in ("Q", "SQ") else "race"),
                "compound": "SOFT",
                "tyre_life": 2.0,
                "gap_ahead_s": meta.get("gap", np.nan),
                "rainfall": 0.0,
                "deleted": meta.get("deleted", False),
            }
        )
        trace = spec["trace"]
        traces.append(trace["shape"] if "shape" in trace else corner_trace(**trace))
    channels = {c: np.stack([t[c] for t in traces]) for c in traces[0]}
    return pd.DataFrame(rows), channels


def make_session(specs: list[dict], **kwargs) -> SessionSegments:
    table, traces = build_table(specs, **kwargs)
    return session_segments(table, traces)


def usual_laps(
    driver: str = "AAA", laps: range = range(1, 9), corner: int = 3, **trace
) -> list[dict]:
    """Ordinary laps with a little lap-to-lap variation, so spreads aren't all zero."""
    jitter = [(-2.0, 0.4), (0.0, -0.3), (2.0, 0.2), (-1.0, 0.0), (1.0, -0.4), (0.0, 0.3)]
    specs = []
    for i, lap in enumerate(laps):
        db, dv = jitter[i % len(jitter)]
        base: dict[str, Any] = {"brake_d": -120.0 + db, "v_min": 100.0 + dv} | trace
        specs.append(lap_spec(driver, lap, corner, **base))
    return specs


def row_of(seg: SessionSegments, driver: str, lap: int, corner: int = 3) -> int:
    f = seg.frame
    hit = (f["driver"] == driver) & (f["lap_number"] == lap) & (f["corner"] == corner)
    return int(np.flatnonzero(hit.to_numpy())[0])


def events_frame(rows: list[dict]) -> pd.DataFrame:
    columns = ["driver", "lap_number", "turn", "kind", "source", "message"]
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------------------------------------


def test_speed_clock_ends_at_the_segment_time() -> None:
    trace = corner_trace()
    clock, ratio = speed_clock(trace["time_s"][None], trace["speed"][None])
    assert clock[0, -1] == pytest.approx(trace["time_s"][-1])
    assert ratio[0] == pytest.approx(1.0, abs=1e-3)  # the synthetic clock comes from speed


def test_braking_point_ignores_a_brief_release() -> None:
    trace = corner_trace()
    released = (DISTANCE >= -95) & (DISTANCE <= -90)  # a 10 m release, throttle stays shut
    trace["brake"] = np.where(released, 0.0, trace["brake"])
    traces = {k: v[None] for k, v in trace.items()}
    seg = session_segments(build_table([lap_spec("AAA", 1)])[0], traces)
    assert seg.frame["brake_start_d"].iloc[0] == -85.0  # the hand-crafted feature jumps
    assert braking_point(traces)[0] == -120.0


def test_phases_add_up_to_the_time_lost() -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, pickup_d=50.0)])
    cmp = compare(seg, row_of(seg, "AAA", 9))
    assert cmp.reference == "driver" and len(cmp.reference_rows) == 8
    assert sum(cmp.phase_loss_s) == pytest.approx(cmp.time_lost_s, abs=1e-6)
    assert cmp.delta_trace_s[-1] == pytest.approx(cmp.time_lost_s, abs=1e-6)


@pytest.mark.parametrize(
    ("mistake", "kind", "phase", "words"),
    [
        ({"pickup_d": 50.0}, "late_throttle", "exit", "slower at the exit"),
        ({"brake_d": -170.0}, "early_braking", "entry", "braked 50 m earlier than usual"),
        ({"v_min": 80.0, "brake_d": -150.0}, "over_slowing", "apex", "possible lock-up"),
        ({"dip": True}, "hesitation", "exit", "lifted 80% of throttle"),
        ({"coast_d": -200.0}, "lift_and_coast", "entry", "energy management"),
    ],
)
def test_each_mistake_gets_its_type(mistake: dict, kind: str, phase: str, words: str) -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, **mistake)])
    exp = explain(seg, row_of(seg, "AAA", 9))
    assert exp.mistake_type == kind
    assert exp.comparison.time_lost_s > 0.05
    assert exp.phase == phase
    assert exp.sentence.startswith("Lap 9, turn 3: ")
    assert words in exp.sentence


def test_an_ordinary_lap_is_unremarkable() -> None:
    seg = make_session(usual_laps(laps=range(1, 10)))
    exp = explain(seg, row_of(seg, "AAA", 5))
    assert exp.mistake_type in ("no_time_lost", "unclear")
    assert exp.secondary_type == ""
    assert abs(exp.comparison.time_lost_s) < 0.05


def test_a_faster_lap_is_not_a_mistake() -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, brake_d=-100.0, v_min=112.0)])
    exp = explain(seg, row_of(seg, "AAA", 9))
    assert exp.mistake_type == "no_time_lost"
    assert "quicker" in exp.sentence and exp.phase == ""


def test_track_limits_come_before_the_driving_type() -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, pickup_d=50.0, deleted=True)])
    events = events_frame(
        [{"driver": "AAA", "lap_number": 9, "turn": 3, "kind": "track_limits",
          "source": "fastf1_deleted", "message": "TRACK LIMITS AT TURN 3"}]
    )  # fmt: skip
    exp = explain(seg, row_of(seg, "AAA", 9), events)
    assert (exp.mistake_type, exp.secondary_type) == ("track_limits", "late_throttle")
    assert "lap time deleted for track limits at turn 3" in exp.sentence
    # A deleted lap is not a reference lap for the others.
    other = compare(seg, row_of(seg, "AAA", 1))
    assert row_of(seg, "AAA", 9) not in other.reference_rows


def test_an_incident_posted_late_goes_to_the_lap_that_deviated() -> None:
    specs = [*usual_laps(), lap_spec("AAA", 9, v_min=75.0), *usual_laps(laps=range(10, 12))]
    seg = make_session(specs, code="R")
    message = "TURN 3 INCIDENT INVOLVING CAR 1 (AAA) NOTED - CAUSING A COLLISION"
    events = events_frame(
        [{"driver": "AAA", "lap_number": 10, "turn": 3, "kind": "incident",
          "source": "race_control", "message": message}]
    )  # fmt: skip
    assert explain(seg, row_of(seg, "AAA", 9), events).mistake_type == "incident"
    assert explain(seg, row_of(seg, "AAA", 10), events).race_control == ""
    # Without the deviations to choose from, both laps match.
    assert race_control(events, "AAA", 9, 3).kind == race_control(events, "AAA", 10, 3).kind


def test_track_limits_at_the_neighbouring_turn_inside_the_window() -> None:
    # A chicane: race control names turn 3; turn 4's apex is 80 m on, so its window covers the
    # same moment. Turn 5, 480 m on, is too far.
    specs = []
    for lap in range(1, 10):
        for corner, apex in ((3, 3000.0), (4, 3080.0), (5, 3480.0)):
            late = {"pickup_d": 50.0} if lap == 9 else {}
            specs.append(lap_spec("AAA", lap, corner, apex_m=apex, deleted=lap == 9, **late))
    seg = make_session(specs)
    events = events_frame(
        [{"driver": "AAA", "lap_number": 9, "turn": 3, "kind": "track_limits",
          "source": "fastf1_deleted", "message": "TRACK LIMITS AT TURN 3 LAP 9"}]
    )  # fmt: skip
    exp = explain(seg, row_of(seg, "AAA", 9, 4), events)
    assert (exp.mistake_type, exp.race_control_turn) == ("track_limits", "3") and exp.bonus
    assert "lap time deleted for track limits at turn 3" in exp.sentence
    own = explain(seg, row_of(seg, "AAA", 9, 3), events)
    assert (own.mistake_type, own.race_control_turn) == ("track_limits", "")
    assert explain(seg, row_of(seg, "AAA", 9, 5), events).race_control == ""
    # Race control naming the turn itself comes first.
    both = pd.concat([events, events.assign(turn=4, message="TRACK LIMITS AT TURN 4 LAP 9")])
    assert race_control(both, "AAA", 9, 4, nearby=(3,)).message == "TRACK LIMITS AT TURN 4 LAP 9"
    assert race_control(events, "AAA", 9, 4, nearby=(3,)).turn == 3


def test_leaving_the_track_counts_as_track_limits() -> None:
    message = "TURN 3 INCIDENT INVOLVING CAR 1 (AAA) NOTED - LEAVING THE TRACK AND GAINING"
    events = events_frame(
        [{"driver": "AAA", "lap_number": 5, "turn": 3, "kind": "incident",
          "source": "race_control", "message": message}]
    )  # fmt: skip
    assert race_control(events, "AAA", 5, 3).kind == "track_limits"
    assert race_control(events, "AAA", 5, 4).kind == ""
    assert race_control(events, "BBB", 5, 3).kind == ""


def test_data_problems_are_not_driving() -> None:
    specs = [
        *usual_laps(),
        lap_spec("AAA", 9, stuck_brake=True),
        lap_spec("AAA", 10, clock_jump=3.0),
    ]
    seg = make_session(specs)
    stuck = explain(seg, row_of(seg, "AAA", 9))
    assert stuck.mistake_type == "data_problem"
    assert "brake signal is on at full throttle" in stuck.data_issue
    jump = explain(seg, row_of(seg, "AAA", 10))
    assert jump.mistake_type == "data_problem"
    assert jump.comparison.time_lost_s == pytest.approx(3.0, abs=0.05)
    assert "timing clock says 3.00 s lost" in jump.sentence


def test_race_control_outranks_a_timing_problem() -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, clock_jump=2.0)])
    events = events_frame(
        [{"driver": "AAA", "lap_number": 9, "turn": 3, "kind": "track_limits",
          "source": "race_control", "message": "TRACK LIMITS AT TURN 3"}]
    )  # fmt: skip
    exp = explain(seg, row_of(seg, "AAA", 9), events)
    assert exp.mistake_type == "track_limits"
    assert "confirmed by race control" in exp.sentence


def test_few_own_laps_compare_with_the_field() -> None:
    specs = [*usual_laps(), *usual_laps("BBB", range(1, 4)), lap_spec("BBB", 4, pickup_d=50.0)]
    seg = make_session(specs)
    exp = explain(seg, row_of(seg, "BBB", 4))
    cmp = exp.comparison
    assert cmp.reference == "field" and cmp.own_laps == 3
    assert len(cmp.reference_rows) == 11  # AAA's 8 and BBB's other 3
    assert "compared with the field, as they had only 3 other clean laps here" in exp.sentence
    assert exp.mistake_type == "late_throttle"


def test_race_reference_is_the_nearest_laps() -> None:
    seg = make_session(usual_laps(laps=range(1, 41)), code="R")
    kind, rows, own = reference_rows(seg, row_of(seg, "AAA", 30), np.ones(40, bool))
    laps = sorted(seg.frame["lap_number"].to_numpy()[rows].tolist())
    assert kind == "driver" and own == 39
    assert laps == [25, 26, 27, 28, 29, 31, 32, 33, 34, 35]


def test_a_slow_stretch_is_not_a_mistake_but_its_start_may_be() -> None:
    # Eight corners a lap; on lap 9 the driver slows from turn 2 onwards.
    specs = []
    for lap in range(1, 10):
        for corner in range(1, 9):
            slow = lap == 9 and corner >= 2
            specs.append(lap_spec("AAA", lap, corner, scale=0.8 if slow else 1.0))
    seg = make_session(specs)
    start = explain(seg, row_of(seg, "AAA", 9, 2))
    assert start.mistake_type != "slowdown"
    assert "6 of the 6 corners after it were slow too" in start.sentence
    middle = explain(seg, row_of(seg, "AAA", 9, 4))
    assert middle.mistake_type == "slowdown"
    assert "part of a slow stretch" in middle.sentence


def test_a_slow_stretch_at_the_end_of_the_lap() -> None:
    # On lap 9 the driver slows from turn 5 of 8: turns 7 and 8 have too few corners after
    # them to see the slow stretch carry on, but they come after two slow corners, arriving
    # slow. Turn 6 has one slow corner before it, so it could be where the trouble started.
    specs = []
    for lap in range(1, 10):
        for corner in range(1, 9):
            slow = lap == 9 and corner >= 5
            specs.append(lap_spec("AAA", lap, corner, scale=0.8 if slow else 1.0))
    seg = make_session(specs, code="R")
    for corner in (7, 8):
        exp = explain(seg, row_of(seg, "AAA", 9, corner))
        assert exp.comparison.adverse("v_start") > 10
        assert exp.mistake_type == "slowdown", corner
    assert explain(seg, row_of(seg, "AAA", 9, 6)).mistake_type != "slowdown"
    start = explain(seg, row_of(seg, "AAA", 9, 5))
    assert start.mistake_type != "slowdown"
    assert "3 of the 3 corners after it were slow too (damage" in start.sentence


def test_a_corner_that_cost_nothing_did_not_start_the_slow_stretch() -> None:
    # Quicker than usual at turn 2, then slow from turn 3 on: the corners after it were slow,
    # but not "too", and the lap wasn't given up because of this corner.
    specs = []
    for lap in range(1, 10):
        for corner in range(1, 9):
            quick = {"brake_d": -100.0, "v_min": 112.0} if lap == 9 and corner == 2 else {}
            scale = 0.8 if lap == 9 and corner >= 3 else 1.0
            specs.append(lap_spec("AAA", lap, corner, scale=scale, **quick))
    seg = make_session(specs)
    exp = explain(seg, row_of(seg, "AAA", 9, 2))
    assert exp.mistake_type == "no_time_lost" and "quicker" in exp.sentence
    assert exp.sentence.endswith("; 6 of the 6 corners after it were slow.")


def test_the_field_slow_at_one_place_is_not_a_mistake() -> None:
    # In a race, four of five drivers are slow at turn 3 on lap 5: something was on track.
    specs = []
    for driver in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        specs += usual_laps(driver, range(1, 5)) + usual_laps(driver, range(6, 10))
        specs.append(lap_spec(driver, 5, v_min=75.0 if driver != "EEE" else 100.0))
    specs.append(lap_spec("AAA", 10, v_min=75.0))  # AAA alone on lap 10
    seg = make_session(specs, code="R")
    together = explain(seg, row_of(seg, "AAA", 5))
    assert together.mistake_type == "slowdown"
    assert "3 other drivers were slow here on the same lap too" in together.sentence
    assert explain(seg, row_of(seg, "AAA", 10)).mistake_type == "over_slowing"
    # Lap numbers don't line up between drivers in qualifying, so there it's not checked.
    quali = make_session(specs, code="Q")
    assert explain(quali, row_of(quali, "AAA", 5)).mistake_type == "over_slowing"


def test_traffic_is_mentioned_in_races() -> None:
    specs = [*usual_laps(), lap_spec("AAA", 9, pickup_d=50.0, gap=0.6)]
    seg = make_session(specs, code="R")
    assert "0.6 s behind the car ahead" in explain(seg, row_of(seg, "AAA", 9)).sentence


def two_turns(t1_kph: float, t2_kph: float) -> dict[str, np.ndarray]:
    """A window over two turns inside the apex band: braking into turn 1 at the apex, a blip of
    throttle, a lift and a dab of brake into turn 2 at +50 m, then full throttle. The slower of
    the two is the turn's slowest point."""
    return shaped_trace(
        speed=[(-250, 280), (-100, 280), (0, t1_kph), (25, 170), (50, t2_kph), (145, 220)],
        throttle=[(-250, 100), (-100, 0), (0, 60), (30, 0), (50, 50), (60, 100)],
        brake=((-100, -5), (35, 48)),
    )


def test_slowest_point_at_the_neighbouring_turn() -> None:
    # Usually turn 2 (+50 m) is the slowest point; on lap 9 the driver is much slower at turn
    # 1, so the slowest point moves 50 m and the normal blip of throttle between the turns
    # would read as a lift after the pickup.
    usual = [lap_spec("AAA", lap, shape=two_turns(150 + i % 3, 120 - i % 2)) for i, lap in
             enumerate(range(1, 9))]  # fmt: skip
    seg = make_session([*usual, lap_spec("AAA", 9, shape=two_turns(100, 120))])
    row = row_of(seg, "AAA", 9)
    assert apex_relative({k: v[[row]] for k, v in seg.traces.items()})["throttle_dip"][0] == 60
    exp = explain(seg, row)
    cmp = exp.comparison
    assert cmp.apex_moved and cmp.values["throttle_dip"] == 0 == cmp.usual["throttle_dip"]
    assert cmp.values["brake_start_d"] == cmp.usual["brake_start_d"] == -100
    assert exp.mistake_type == "over_slowing" and "hesitation" not in exp.secondary_type
    assert "20 km/h slower at the slowest point" in exp.sentence


def after_a_slow_turn(
    pickup_d: float = 20.0, exit_throttle: float = 100.0
) -> dict[str, np.ndarray]:
    """A window starting on the slow exit of the previous turn (90 km/h, slower than this
    turn's 150), then braking at -100 m for this turn and back on full throttle at `pickup_d`.
    `exit_throttle` below 100 leaves the previous turn on part throttle until -150 m."""
    return shaped_trace(
        speed=[(-250, 90), (-120, 250), (-100, 250), (0, 150), (pickup_d, 150), (145, 230)],
        throttle=[(-250, exit_throttle), (-150, 100), (-100, 0), (0, 40), (pickup_d, 100)],
        brake=((-100, -5),),
    )


def test_features_are_measured_from_this_turns_slowest_point() -> None:
    # The window's slowest point is the previous turn's exit at its start, on every lap. From
    # there, the pickup is the full throttle out of the previous turn and there is no braking
    # zone; from this turn's own slowest point, the pickup is 55 m late and it brakes at -100.
    usual = [lap_spec("AAA", lap, shape=after_a_slow_turn(20.0 + 5 * (lap % 2))) for lap in
             range(1, 10)]  # fmt: skip
    late = after_a_slow_turn(pickup_d=80.0, exit_throttle=60.0)
    seg = make_session([*usual, lap_spec("AAA", 10, shape=late)])
    row = row_of(seg, "AAA", 10)
    assert seg.frame["d_vmin"].iloc[row] == -250  # the hand-crafted slowest point
    exp = explain(seg, row)
    cmp = exp.comparison
    assert cmp.values["d_vmin"] == 0 and cmp.values["v_min"] == 150 and not cmp.apex_moved
    assert cmp.braking_zone and cmp.values["brake_start_d"] == cmp.usual["brake_start_d"] == -100
    assert cmp.delta["throttle_pickup_d"] == 55
    assert exp.mistake_type == "late_throttle"
    assert "back on full throttle 55 m later than usual" in exp.sentence


def test_a_turn_taken_flat_has_no_braking_point_or_pickup() -> None:
    # A kink taken flat out: a lap that lifts through it is slower at the exit, but the
    # braking point, pickup and lift of a turn with no slowest point of its own would be
    # measured from wherever the speed happens to dip, so they aren't compared.
    usual = [lap_spec("AAA", lap, shape=flat_corner(speed=[(-250, 290), (0, 280 + lap % 3),
             (145, 285 - lap % 2)])) for lap in range(1, 9)]  # fmt: skip
    lift = flat_corner(
        speed=[(-250, 290), (-20, 290), (40, 250), (145, 265)],
        throttle=[(-250, 100), (-20, 30), (40, 100), (60, 50), (80, 100)],
    )
    seg = make_session([*usual, lap_spec("AAA", 9, shape=lift)], code="R")
    exp = explain(seg, row_of(seg, "AAA", 9))
    cmp = exp.comparison
    assert cmp.taken_flat and not cmp.braking_zone
    assert all(np.isnan(cmp.z[f]) for f in ("brake_start_d", "throttle_pickup_d", "throttle_dip"))
    assert exp.mistake_type == "late_throttle" and exp.secondary_type != "hesitation"
    assert "back on full throttle" not in exp.sentence and "slower at the exit" in exp.sentence


def flat_corner(**kwargs) -> dict[str, np.ndarray]:
    """A corner taken flat out: 290 km/h, 270 at the apex, no braking."""
    speed = kwargs.pop("speed", [(-250, 290), (0, 270), (145, 285)])
    return shaped_trace(speed, kwargs.pop("throttle", [(-250, 100)]))


def test_coasting_explains_the_slower_speeds() -> None:
    usual = [lap_spec("AAA", lap, shape=flat_corner(speed=[(-250, 290), (0, 270 + lap % 3),
             (145, 285 - lap % 2)])) for lap in range(1, 9)]  # fmt: skip
    coast = flat_corner(
        speed=[(-250, 290), (-150, 290), (100, 241), (145, 255)],
        throttle=[(-250, 100), (-150, 0), (100, 100)],
    )
    lift = flat_corner(
        speed=[(-250, 290), (-150, 290), (100, 235), (145, 250)],
        throttle=[(-250, 100), (-150, 40), (100, 100)],
    )
    # A short coast in front of a huge loss: something else happened (a problem, a moment).
    collapse = flat_corner(
        speed=[(-250, 290), (-60, 290), (0, 120), (60, 110), (145, 170)],
        throttle=[(-250, 100), (-60, 0), (-20, 40), (60, 100)],
    )
    laps = [lap_spec("AAA", 9, shape=coast), lap_spec("AAA", 10, shape=lift)]
    seg = make_session([*usual, *laps, lap_spec("AAA", 11, shape=collapse)])
    exp = explain(seg, row_of(seg, "AAA", 9))
    assert exp.comparison.adverse("v_min") > 10 and exp.comparison.adverse("v_exit") > 10
    assert (exp.mistake_type, exp.secondary_type) == ("lift_and_coast", "")
    assert exp.sentence.split("; ")[1].startswith("250 m more coasting")
    assert "energy management" in exp.sentence and "lift_and_coast" in NOT_MISTAKES
    # A partial lift, no coasting: slower through a corner with no braking zone is not
    # over-slowing.
    lifted = explain(seg, row_of(seg, "AAA", 10))
    assert not lifted.comparison.braking_zone
    assert lifted.mistake_type == "late_throttle" and lifted.secondary_type != "over_slowing"
    collapsed = explain(seg, row_of(seg, "AAA", 11))
    assert collapsed.comparison.time_lost_s > 2 and collapsed.comparison.delta["coast_m"] == 40
    assert (
        collapsed.mistake_type == "late_throttle" and "energy management" not in collapsed.sentence
    )


def test_coasting_that_does_not_explain_the_loss_is_not_lift_and_coast() -> None:
    usual = [lap_spec("AAA", lap, shape=flat_corner(speed=[(-250, 290), (0, 270 + lap % 3),
             (145, 285 - lap % 2)])) for lap in range(1, 9)]  # fmt: skip
    # Coasting 90 m more costs up to 2.25 s; this lap lost 3.4 s crawling through the corner.
    crawl = flat_corner(
        speed=[(-250, 290), (-60, 290), (0, 60), (30, 60), (145, 283)],
        throttle=[(-250, 100), (-60, 0), (30, 100)],
    )
    seg = make_session([*usual, lap_spec("AAA", 9, shape=crawl)], code="R")
    exp = explain(seg, row_of(seg, "AAA", 9))
    assert exp.comparison.time_lost_s > 3 and exp.comparison.adverse("coast_m") > 4.5
    assert (exp.mistake_type, exp.secondary_type) == ("unclear", "")
    assert "unclear" not in NOT_MISTAKES and "energy management" not in exp.sentence
    assert "90 m more coasting (off both pedals); that much coasting costs up to 2.25 s" in (
        exp.sentence
    )
    # Coasting before braking 50 m early: the braking is the mistake, not energy management.
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, coast_d=-250.0, brake_d=-170.0)])
    exp = explain(seg, row_of(seg, "AAA", 9))
    assert (exp.mistake_type, exp.secondary_type) == ("early_braking", "")
    assert "braked 50 m earlier than usual and 80 m more coasting" in exp.sentence


@pytest.mark.parametrize(
    ("year", "code", "kind", "words"),
    [
        (2024, "Q", "lift_on_push_lap", "nothing to save in qualifying"),
        (2024, "R", "lift_and_coast", "fuel or tyre saving"),
        (2026, "Q", "lift_and_coast", "energy management"),
    ],
)
def test_a_lift_on_a_push_lap_is_listed(year: int, code: str, kind: str, words: str) -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, coast_d=-200.0)], year=year, code=code)
    exp = explain(seg, row_of(seg, "AAA", 9))
    assert exp.mistake_type == kind and words in exp.sentence
    assert (kind in NOT_MISTAKES) == (kind == "lift_and_coast")


def test_a_loss_carried_in_is_a_slow_approach() -> None:
    specs = [*usual_laps(), lap_spec("AAA", 9, v_start=200.0, pickup_d=30.0),
             lap_spec("AAA", 10, v_start=220.0, pickup_d=60.0)]  # fmt: skip
    seg = make_session(specs, code="R")
    # 50 km/h slower on the approach and most of the time lost there: the slower exit and later
    # throttle follow from arriving slow.
    carried = explain(seg, row_of(seg, "AAA", 9))
    cmp = carried.comparison
    assert cmp.phase_loss_s[0] > 0.5 * cmp.time_lost_s > 0.25
    assert (carried.mistake_type, carried.secondary_type) == ("slow_approach", "late_throttle")
    assert "slow_approach" in NOT_MISTAKES
    assert "mostly on entry; 50 km/h slower on the approach, " in carried.sentence
    assert "carried in from before the corner" in carried.sentence
    # Arriving slower but losing most of it after the apex: the corner's own mistake comes first.
    own = explain(seg, row_of(seg, "AAA", 10))
    assert own.comparison.phase_loss_s[0] < 0.5 * own.comparison.time_lost_s
    assert (own.mistake_type, own.secondary_type) == ("late_throttle", "slow_approach")


def test_a_few_hundredths_is_not_slower() -> None:
    # The reference laps agree to the thousandth, but the timing clock is only good to a few
    # hundredths: a 0.03 s loss is not a mistake, whatever the z against a zero spread.
    same = [lap_spec("AAA", lap) for lap in range(1, 9)]
    seg = make_session([*same, lap_spec("AAA", 9, pickup_d=16.0)])
    exp = explain(seg, row_of(seg, "AAA", 9))
    assert 0.005 < exp.comparison.time_lost_s < 0.1
    assert exp.mistake_type == "no_time_lost" and exp.phase == ""
    assert "but within the spread of their laps here (a loss stands out from 0.12 s)" in (
        exp.sentence
    )


def test_lap_losses_are_the_comparisons_time_lost() -> None:
    specs = usual_laps(laps=range(1, 25)) + usual_laps("BBB", range(1, 4))
    specs += [lap_spec("AAA", 25, v_min=80.0, lap_class="out"), lap_spec("BBB", 4, v_min=90.0)]
    seg = make_session(specs, code="R")
    clean = clean_mask(seg.frame)
    losses = lap_losses(seg, clean)
    for row in range(len(seg.frame)):
        assert losses[row] == pytest.approx(compare(seg, row, clean).time_lost_s, abs=1e-9)
    assert compare(seg, row_of(seg, "BBB", 4), clean).reference == "field"


def test_a_two_car_incident_is_context_not_a_mistake() -> None:
    specs = [*usual_laps(), lap_spec("AAA", 9, v_min=75.0), *usual_laps("BBB")]
    seg = make_session(specs, code="R")
    message = (
        "TURN 3 INCIDENT INVOLVING CARS 1 (AAA) AND 2 (BBB) NOTED - FORCING ANOTHER DRIVER OFF "
        "THE TRACK"
    )
    events = events_frame(
        [{"driver": d, "lap_number": 9, "turn": 3, "kind": "incident", "source": "race_control",
          "message": message} for d in ("AAA", "BBB")]
    )  # fmt: skip
    rc = race_control(events, "AAA", 9, 3)
    assert (rc.kind, rc.others) == ("involved", ("BBB",))
    exp = explain(seg, row_of(seg, "AAA", 9), events)
    assert exp.mistake_type == "over_slowing" and not exp.bonus
    assert "involved in an incident with BBB at turn 3 noted by race control (forcing " in (
        exp.sentence
    )
    assert exp.race_control == message


def test_an_incident_goes_to_a_scored_lap_not_the_in_lap() -> None:
    # Posted during lap 10, the in-lap: it deviated most at turn 3 (the pit entry), but an
    # in-lap is slow there anyway, so lap 9 gets the incident.
    specs = [*usual_laps(), lap_spec("AAA", 9, v_min=90.0), lap_spec("AAA", 10, v_min=60.0,
             lap_class="in")]  # fmt: skip
    seg = make_session(specs, code="R")
    message = "TURN 3 INCIDENT INVOLVING CAR 1 (AAA) NOTED - LEAVING THE TRACK AND GAINING"
    events = events_frame(
        [{"driver": "AAA", "lap_number": 10, "turn": 3, "kind": "incident",
          "source": "race_control", "message": message}]
    )  # fmt: skip
    assert explain(seg, row_of(seg, "AAA", 9), events).mistake_type == "track_limits"
    assert explain(seg, row_of(seg, "AAA", 10), events).race_control == ""


def test_track_limits_with_the_field_slow_too() -> None:
    specs = []
    for driver in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        specs += usual_laps(driver, range(1, 5)) + usual_laps(driver, range(6, 10))
        specs.append(lap_spec(driver, 5, v_min=75.0 if driver != "EEE" else 100.0))
    seg = make_session(specs, code="R")
    events = events_frame(
        [{"driver": "AAA", "lap_number": 5, "turn": 3, "kind": "track_limits",
          "source": "race_control", "message": "CAR 1 (AAA) LAP DELETED - TRACK LIMITS AT TURN 3"}]
    )  # fmt: skip
    exp = explain(seg, row_of(seg, "AAA", 5), events)
    assert exp.mistake_type == "track_limits" and exp.bonus
    assert "not a mistake" not in exp.sentence
    assert "3 other drivers were also slow here on the same lap" in exp.sentence
    assert "explain most of the time lost" in exp.sentence
    lost = exp.comparison.time_lost_s
    assert exp.field_loss_s == pytest.approx(lost, abs=0.01)
    ranked = priority(pd.Series([lost]), pd.Series([1.0]), pd.Series([True]),
                      field_loss_s=pd.Series([exp.field_loss_s]))  # fmt: skip
    assert ranked.iloc[0] == pytest.approx(0.5, abs=0.02)  # the bonus, not the conditions


def test_stuck_speed_and_spikes_are_data_problems() -> None:
    stuck, spike = corner_trace(), corner_trace()
    frozen = (DISTANCE >= -90) & (DISTANCE <= -60)  # held through the braking zone
    stuck["speed"] = np.where(frozen, stuck["speed"][np.flatnonzero(frozen)[0]], stuck["speed"])
    spike["speed"] = spike["speed"] + np.where(DISTANCE == 40, 60.0, 0.0)
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, shape=stuck),
                        lap_spec("AAA", 10, shape=spike)])  # fmt: skip
    exp = explain(seg, row_of(seg, "AAA", 9))
    assert exp.mistake_type == "data_problem" and "speed channel is stuck for" in exp.data_issue
    exp = explain(seg, row_of(seg, "AAA", 10))
    assert exp.mistake_type == "data_problem" and "speed trace jumps by" in exp.data_issue


def test_too_few_laps_to_compare_is_not_listed() -> None:
    seg = make_session([lap_spec("AAA", lap) for lap in range(1, 4)])
    exp = explain(seg, row_of(seg, "AAA", 2))
    assert exp.mistake_type == "no_reference" and "no_reference" in NOT_MISTAKES
    assert "too few clean laps" in exp.sentence


def flags_frame(rows: list[tuple]) -> pd.DataFrame:
    """Flags as merge_overlapping sees them: (lap, turn, apex, type, priority, time lost, of
    it on entry, race-control message)."""
    columns = ["lap_id", "corner", "apex_m", "mistake_type", "priority", "time_lost_s",
               "loss_entry_s", "race_control"]  # fmt: skip
    frame = pd.DataFrame(rows, columns=columns)
    return frame.assign(
        segment_id=frame["lap_id"] + "_T" + frame["corner"].astype(str),
        corner_letter="",
        race_control_turn="",
        explanation=[f"Lap 1, turn {c}: lost." for c in frame["corner"]],
    )


def test_overlapping_windows_are_one_mistake() -> None:
    frame = flags_frame([
        ("A", 1, 1000.0, "late_throttle", 0.4, 0.4, 0.1, ""),  # lost on its exit
        ("A", 2, 1200.0, "over_slowing", 0.9, 0.9, 0.6, ""),  # mostly on entry: carried in
        ("A", 3, 1500.0, "unclear", 0.2, 0.2, 0.0, ""),  # 300 m after turn 2: chained on
        ("A", 4, 2000.0, "early_braking", 0.3, 0.3, 0.2, ""),  # 500 m on: a mistake of its own
        ("A", 5, 2100.0, "no_time_lost", 0.0, 0.0, 0.0, ""),  # not listed, not merged
        ("B", 1, 1000.0, "track_limits", 0.5, 0.1, 0.0, "TRACK LIMITS AT TURN 1"),  # another lap
    ])  # fmt: skip
    out = merge_overlapping(frame).set_index("segment_id")
    # Listed where it started (turn 1), not where the most time was counted (turn 2).
    assert out["merged_into"].to_dict() == {
        "A_T1": "A_T1", "A_T2": "A_T1", "A_T3": "A_T1", "A_T4": "A_T4", "A_T5": "", "B_T1": "B_T1"
    }  # fmt: skip
    assert out.loc["A_T1", "also_turns"] == "2, 3"
    assert out.loc["A_T4", "also_turns"] == "" == out.loc["B_T1", "also_turns"]


def test_race_control_heads_the_moment_it_named() -> None:
    # Track limits at turn 8, whose exit loss turn 9's window counts again, and more.
    frame = flags_frame([
        ("A", 8, 1973.0, "track_limits", 2.65, 2.16, 0.1, "TRACK LIMITS AT TURN 8 LAP 22"),
        ("A", 9, 2054.0, "late_throttle", 2.93, 2.93, 0.5, ""),
        ("B", 13, 3432.0, "track_limits", 4.07, 3.60, 0.2, "TRACK LIMITS AT TURN 13 LAP 43"),
        ("B", 14, 3710.0, "incident", 6.0, 5.47, 4.7, "TURN 14 INCIDENT INVOLVING CAR 22 (TSU) "
         "NOTED - CAUSING A COLLISION"),
    ])  # fmt: skip
    out = merge_overlapping(frame).set_index("segment_id")
    assert out.loc["A_T9", "merged_into"] == "A_T8" and out.loc["A_T8", "also_turns"] == "9"
    # Two race-control flags in one moment: the first, and the other's message is kept.
    assert out.loc["B_T14", "merged_into"] == "B_T13"
    assert out.loc["B_T13", "explanation"] == (
        "Lap 1, turn 13: lost; race control noted an incident at turn 14 (causing a collision)."
    )


def test_calibrated_thresholds_are_a_quantile_with_a_floor() -> None:
    rng = np.random.default_rng(0)
    sample = pd.DataFrame({f: rng.normal(0, 1, 10_000) for f in DEVIATIONS})
    sample["v_min"] = rng.normal(0, 3, 10_000)
    sample["v_exit"] = rng.normal(0, 0.5, 10_000)
    sample["segment_time_s"] = rng.normal(0, 2, 10_000)
    thresholds = calibrate_thresholds(sample)
    assert thresholds["v_min"] == pytest.approx(3 * 2.326, rel=0.05)
    assert thresholds["v_exit"] == MIN_THRESHOLD  # its 99th percentile is only 1.16
    assert thresholds["segment_time_s"] == pytest.approx(2 * 1.645, rel=0.05)  # the 95th
    tenth = calibrate_thresholds(sample, dict.fromkeys(DEVIATIONS, 0.9))
    assert tenth["v_min"] == pytest.approx(3 * 1.28, rel=0.05)


def test_2026_fire_rates_count_only_the_corners_compared() -> None:
    # 1,000 ordinary corners a season, a quarter taken flat out (no throttle lift compared).
    # Coasting passes its threshold on 1% before 2026 and 2% in 2026; the lift on 8 of the 750
    # compared corners before 2026 (1.1%, not 0.8%) and on 30 in 2026.
    frames = []
    for year in range(2022, 2027):
        coast = np.zeros(1000)
        coast[: 20 if year == 2026 else 10] = 10.0
        dip = np.zeros(1000)
        dip[:250] = np.nan
        dip[250 : 250 + (30 if year == 2026 else 8)] = 10.0
        frames.append(pd.DataFrame({"year": year, "coast_m": coast, "throttle_dip": dip}))
    text = _coast_2026(pd.concat(frames, ignore_index=True))
    assert "coasting rule fires on 2.0% of ordinary 2026 corners (1.0%-1.0% in each" in text
    assert (
        "throttle-lift rule fires on 4.0% of ordinary 2026 corners where it is compared, not "
        "the turns taken flat out (1.1%-1.1% in each of 2022-2025" in text
    )
    assert text.endswith("so some 2026 hesitation flags may be energy management too.")


def test_priority_discounts_borderline_flags_and_adds_race_control() -> None:
    lost = pd.Series([1.0, 1.0, 1.0, -0.4])
    pct = pd.Series([1.0, 0.99, 1.0, 1.0])
    named = pd.Series([False, False, True, True])
    p = priority(lost, pct, named)
    assert p.tolist() == pytest.approx([1.0, 0.5, 1.5, 0.5])


# ---------------------------------------------------------------------------------------------
# The batch, on a processed directory


def write_segments(table: pd.DataFrame, traces: dict[str, np.ndarray], root: Path) -> None:
    """The table in the processed `segments` layout (80-point fixed-size lists)."""
    key = store.session_key(*table[["year", "round", "session"]].iloc[0])
    arrays = {c: store.fixed_list_column(list(v), v.shape[1]) for c, v in traces.items()}
    frame = table.assign(segment_time_s=traces["time_s"][:, -1], stitched="", start_m=0.0)
    store.write_table(store.to_arrow(frame, arrays), "segments", key, root)


def write_events(rows: list[dict], key: str, root: Path, ids: dict) -> None:
    frame = events_frame(rows).assign(driver_number="1", drivers_same_lap_turn=1, **ids)
    store.write_table(pa.Table.from_pandas(frame, preserve_index=False), "events", key, root)


def scores_frame(table: pd.DataFrame, flagged: list[str]) -> pd.DataFrame:
    """A segment_scores table for `table`, with `flagged` segment ids flagged."""
    is_flag = table["segment_id"].isin(flagged).to_numpy()
    pct = np.where(is_flag, 0.995, 0.5)
    return table.assign(
        event="Sample Grand Prix",
        location="Synthetic Circuit",
        label=0,
        split="test",
        transformer_pct=pct,
        isolation_forest_pct=pct,
        score=pct,
        score_pct=pct,
        flagged=is_flag,
    )


def scored_session(tmp_path: Path, run_id: str | None, current: str | None = "run-1") -> Path:
    """A processed session with laps 9 and 10 flagged, scored by `run_id`, and a manifest
    naming `current` as the current scoring run (none when None). Returns the scores' path."""
    table, traces = build_table(
        [*usual_laps(), lap_spec("AAA", 9, pickup_d=50.0), lap_spec("AAA", 10, brake_d=-170.0)]
    )
    write_segments(table, traces, tmp_path / "processed")
    flagged = ["2026_01_Q_1_9_T3", "2026_01_Q_1_10_T3"]
    results = tmp_path / "results"
    scores = results / "segment_scores.parquet"
    provenance.write_parquet(scores_frame(table, flagged), scores, run_id)
    if current is not None:
        (results / provenance.MANIFEST).write_text(json.dumps({"run_id": current}))
    return scores


def test_run_explain_writes_one_row_per_flag(tmp_path: Path) -> None:
    scores = scored_session(tmp_path, "run-1")
    out = scores.parent / "mistakes.parquet"
    mistakes = run_explain(scores, out, None, tmp_path / "processed")
    assert out.exists() and len(mistakes) == 2
    assert provenance.parquet_run(out) == "run-1"  # the scoring run it was built from
    by_lap = mistakes.set_index("lap_number")
    assert by_lap.loc[9, "mistake_type"] == "late_throttle"
    assert by_lap.loc[10, "mistake_type"] == "early_braking"
    assert by_lap.loc[10, "delta_brake_start_d"] == pytest.approx(-50.0, abs=3)
    assert {"time_lost_s", "explanation", "priority", *(f"loss_{p}_s" for p in PHASES)} <= set(
        mistakes.columns
    )
    assert (mistakes["priority"] > 0).all()


def test_run_explain_refuses_stale_scores(tmp_path: Path) -> None:
    scores = scored_session(tmp_path, "run-0")  # scored by a run the manifest has replaced
    out = scores.parent / "mistakes.parquet"
    processed = tmp_path / "processed"
    with pytest.raises(provenance.StaleResultsError, match="another scoring run"):
        run_explain(scores, out, None, processed)
    with pytest.raises(provenance.StaleResultsError, match="another scoring run"):
        run_explain(scores, out, None, processed, allow_unrecorded=True)
    (scores.parent / provenance.MANIFEST).unlink()  # a new model selected, not scored yet
    for allow in (False, True):  # the scores record a run, so they aren't from before runs
        with pytest.raises(provenance.StaleResultsError, match="No current scoring run"):
            run_explain(scores, out, None, processed, allow_unrecorded=allow)
    assert not out.exists()


def test_run_explain_refuses_unrecorded_scores_next_to_a_recorded_run(tmp_path: Path) -> None:
    scores = scored_session(tmp_path, None, current="run-1")  # replaced after run-1 wrote them
    out = scores.parent / "mistakes.parquet"
    with pytest.raises(provenance.StaleResultsError, match="unrecorded"):
        run_explain(scores, out, None, tmp_path / "processed", allow_unrecorded=True)


def test_run_explain_can_allow_scores_from_before_runs_were_recorded(tmp_path: Path) -> None:
    scores = scored_session(tmp_path, None, current=None)
    out = scores.parent / "mistakes.parquet"
    with pytest.raises(provenance.StaleResultsError):
        run_explain(scores, out, None, tmp_path / "processed")
    mistakes = run_explain(scores, out, None, tmp_path / "processed", allow_unrecorded=True)
    assert len(mistakes) == 2 and provenance.parquet_run(out) is None


# ---------------------------------------------------------------------------------------------
# Traffic: the other cars (tracks from test_inference_traffic; AAA's turn 3 apex is at 3000 m)


def race_with_mistake(**mistake: Any) -> tuple[SessionSegments, int]:
    """AAA's usual turn 3 on race laps 1-8 and lap 9 taken as `mistake` says."""
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, **mistake)], code="R")
    return seg, row_of(seg, "AAA", 9)


def test_being_lapped_by_a_car_already_close_is_traffic() -> None:
    # BBB, a lap ahead, was 1.0 s behind 450 m before the apex and went past: letting it by.
    seg, row = race_with_mistake(brake_d=-170.0)
    exp = explain(seg, row, tracks=lapped_session(1.0))
    assert exp.mistake_type == "lapped" and "lapped" in NOT_MISTAKES
    assert exp.secondary_type == "early_braking" and exp.type_without_traffic == "early_braking"
    assert exp.traffic.cars == ("BBB",)
    assert (
        "being lapped: BBB (a lap ahead) went past before the corner, before the loss began: "
        "letting them through (blue flags), not a mistake" in exp.sentence
    )
    assert "a spin, an off or a problem" not in exp.sentence


def test_a_car_from_further_back_passing_after_a_mistake_keeps_the_mistake() -> None:
    # A lock-up (the loss begins 70 m before the apex; AAA slow from the apex on), and BBB
    # (same lap), 2.0 s behind when the extended window began, took the place: not close
    # before the loss began, so the loss is the driver's.
    seg, row = race_with_mistake(v_min=80.0, brake_d=-150.0)
    late = (3000.0, 3445.0, 20.0)  # AAA slows from 3000 m: its loss begins late
    exp = explain(seg, row, tracks=lapped_session(2.0, ahead=0, slow=late))
    assert exp.mistake_type == "over_slowing" and exp.traffic.kind == ""
    assert "a possible lock-up" in exp.sentence
    (bbb,) = exp.traffic.encounters
    gap = f"{bbb.gap_onset_s:.1f} s behind 70 m before the apex, where the loss began"
    assert f"lost the place to BBB ({gap})" in exp.sentence
    # A lapping car from that far back only went past; the lapping gap is wider (LAPPED_GAP_S).
    far = explain(seg, row, tracks=lapped_session(LAPPED_GAP_S + 1.3, slow=late))
    (bbb,) = far.traffic.encounters
    assert far.mistake_type == "over_slowing" and bbb.gap_onset_s > LAPPED_GAP_S
    assert f"BBB (a lap ahead), {bbb.gap_onset_s:.1f} s behind 70 m before the apex" in (
        far.sentence
    )
    # Past before the loss began, it was the traffic's (AAA lifted on the straight to let it by).
    early = explain(seg, row, tracks=lapped_session(LAPPED_GAP_S + 0.5))
    assert early.mistake_type == "lapped"
    assert "being lapped: BBB (a lap ahead) went past before the corner, before the loss" in (
        early.sentence
    )


def test_a_fight_for_the_place_is_traffic() -> None:
    seg, row = race_with_mistake(v_min=80.0)
    exp = explain(seg, row, tracks=lapped_session(0.3, ahead=0))
    assert exp.mistake_type == "battle" and exp.traffic.cars == ("BBB",)
    assert (
        "racing BBB: BBB, 0.3 s behind 450 m before the apex, went past before the corner, "
        "before the loss began" in exp.sentence
    )
    assert "the time lost is the fight for the place, not a mistake" in exp.sentence


def test_race_control_keeps_its_type_with_traffic_around() -> None:
    seg, row = race_with_mistake(brake_d=-170.0)
    events = events_frame([{"driver": "AAA", "lap_number": 9, "turn": 3, "kind": "track_limits",
                            "message": "TRACK LIMITS AT TURN 3"}])  # fmt: skip
    exp = explain(seg, row, events, tracks=lapped_session(1.0))
    assert exp.mistake_type == "track_limits" and exp.traffic.kind == "lapped"
    assert "so the time lost doesn't count towards its ranking" in exp.sentence


def test_the_first_racing_lap_after_a_safety_car() -> None:
    laps = [
        replace(lap, lap_class="neutralised") if (lap.driver, lap.lap) == ("AAA", 8) else lap
        for lap in lapped_laps(3.0)
    ]
    tracks = make_tracks(laps)
    tracks.laps.loc[("AAA", 8), "track_status"] = "4"
    seg, row = race_with_mistake(brake_d=-170.0)
    assert "the first racing lap after a safety car" in explain(seg, row, tracks=tracks).sentence


def test_the_first_racing_lap_after_a_red_flag() -> None:
    # Stopped on lap 7 (red flag, into the pit lane), out to the grid on lap 8, restarted on 9.
    laps = [
        replace(lap, pit_out=True, lap_class="no_time") if (lap.driver, lap.lap) == ("AAA", 8)
        else replace(lap, pit_in=True) if (lap.driver, lap.lap) == ("AAA", 7)
        else lap
        for lap in lapped_laps(3.0)
    ]  # fmt: skip
    tracks = make_tracks(laps)
    tracks.laps.loc[("AAA", 7), "track_status"] = "451"
    seg, row = race_with_mistake(brake_d=-170.0)
    assert "the first racing lap after a red flag" in explain(seg, row, tracks=tracks).sentence


def quali_tracks(
    ccc_ahead_s: float | None, prep: bool = False, held_kph: float = 0.0, ccc_grid: bool = True
) -> Any:
    """Qualifying: AAA's push laps 1-9 at 50 m/s, 100 s apart. With `ccc_ahead_s`, CCC is on
    its out-lap `ccc_ahead_s` ahead of AAA at 2600 m of lap 9, at `held_kph` from 2000 m to
    3300 m, and AAA follows it at that speed through the corner. With `prep`, AAA's lap 9 is
    at 45 m/s up to 2700 m: already slow before turn 3."""
    aaa = laps_of("AAA", 1, 8, 0.0, 50.0, lap_class="push")
    v = speeds(50.0)
    if prep:
        v = speeds(50.0, (0.0, 2700.0, 45.0))
    if ccc_ahead_s is not None:
        v = speeds(50.0, (2600.0, 3200.0, held_kph / 3.6))
    aaa.append(Lap("AAA", 9, 800.0, v, lap_class="push"))
    laps = aaa
    if ccc_ahead_s is not None:
        out = speeds(40.0, (0.0, 300.0, 20.0), (2000.0, 3300.0, held_kph / 3.6))
        t, _ = lap_clock(out)
        start = 800.0 + 2600.0 / 50.0 - ccc_ahead_s - float(np.interp(2600.0, GRID_M, t))
        laps = [*aaa, Lap("CCC", 3, start, out, "out", pit_out=True, grid=ccc_grid)]
        if not ccc_grid:  # its next lap, so that the out-lap's end is known
            laps.append(Lap("CCC", 4, start + lap_clock(out)[1], speeds(50.0), "push"))
    return make_tracks(laps, code="Q")


def test_a_car_placed_roughly_is_not_traffic() -> None:
    # CCC's out-lap has no grid: placed from its sector times, it is too rough to count, and
    # the sentence says it may have been in the way instead.
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, v_min=75.0)])
    row = row_of(seg, "AAA", 9)
    usual = float(np.median(seg.frame["segment_time_s"].to_numpy()[:8]))
    held = 395.0 / (usual + 1.5) * 3.6
    exp = explain(seg, row, tracks=quali_tracks(0.5, held_kph=held, ccc_grid=False))
    assert exp.mistake_type == "over_slowing" and exp.traffic.kind == ""
    assert exp.traffic.maybe == ("CCC",)
    assert "CCC (on its out lap) may have been in the way" in exp.sentence
    assert "Placed only roughly" in cars_around_text(exp.traffic, race=False)


def test_held_up_by_a_car_on_its_out_lap() -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, v_min=75.0)])
    row = row_of(seg, "AAA", 9)
    usual = float(np.median(seg.frame["segment_time_s"].to_numpy()[:8]))
    held = 395.0 / (usual + 0.8) * 3.6  # CCC takes 0.8 s longer than AAA's usual, as AAA did
    exp = explain(seg, row, tracks=quali_tracks(0.5, held_kph=held))
    assert exp.mistake_type == "impeded" and exp.traffic.cars == ("CCC",)
    assert (
        "held up by CCC (on its out lap): 0.5 s behind it at the closest on the way in, and it "
        "took" in exp.sentence
    )
    assert "it set their pace, so not a mistake" in exp.sentence
    # Much slower than that, CCC was in the way until AAA got past it: not "it set their pace".
    slower = 395.0 / (usual + 3.0) * 3.6
    past = explain(seg, row, tracks=quali_tracks(0.5, held_kph=slower))
    assert past.mistake_type == "impeded"
    assert "0.5 s behind it at the closest on the way in, until they got past it" in past.sentence
    assert "having to get past it, so not a mistake" in past.sentence
    assert "set their pace" not in past.sentence
    # Far enough ahead, CCC doesn't set the pace: the loss is the driver's.
    alone = explain(seg, row, tracks=quali_tracks(3.0, held_kph=held))
    assert alone.mistake_type == "over_slowing"


def test_a_lap_already_slow_before_the_corner_is_not_a_full_push_lap() -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, pickup_d=50.0)])
    row = row_of(seg, "AAA", 9)
    exp = explain(seg, row, tracks=quali_tracks(None, prep=True))
    assert exp.mistake_type == "not_push_lap" and exp.secondary_type == "late_throttle"
    assert exp.lap_deficit_s == pytest.approx(6.0, abs=0.05)
    assert "not a full push lap: already 6.0 s (11%) slower than their usual push lap" in (
        exp.sentence
    )
    # A lap that slows only after the corner keeps the mistake.
    assert explain(seg, row, tracks=quali_tracks(None)).mistake_type == "late_throttle"


def cover_frame(rows: list[tuple]) -> pd.DataFrame:
    """flags_frame rows plus (traffic kind, extended window start and end around the apex)."""
    frame = flags_frame([r[:8] for r in rows])
    return frame.assign(
        traffic=[r[8] for r in rows],
        traffic_start_m=[r[9] for r in rows],
        traffic_end_m=[r[10] for r in rows],
    )


def test_traffic_covers_the_neighbouring_turns_whose_windows_start_inside_it() -> None:
    lapped = ("lapped", -450.0, 445.0)
    none = ("", float("nan"), float("nan"))
    frame = cover_frame([
        ("A", 12, 2700.0, "late_throttle", 1.0, 1.0, 0.1, "", *none),  # starts at 2450: before
        ("A", 13, 3000.0, "lapped", 0.0, 3.0, 1.0, "", *lapped),  # extended: 2550 to 3445
        ("A", 14, 3300.0, "over_slowing", 1.2, 1.2, 0.8, "", *none),  # starts at 3050: covered
        ("A", 15, 3600.0, "track_limits", 1.5, 1.0, 0.2, "TRACK LIMITS AT TURN 15", *none),
        ("A", 16, 3800.0, "unclear", 0.4, 0.4, 0.1, "", *none),  # starts at 3550: after
        ("B", 14, 3300.0, "over_slowing", 0.5, 0.5, 0.1, "", *none),  # another lap
    ])  # fmt: skip
    frame["covered_by"] = traffic_cover(frame)
    _tell_covered(frame)
    assert frame.set_index("segment_id")["covered_by"].to_dict() == {
        "A_T12": "", "A_T13": "", "A_T14": "A_T13", "A_T15": "A_T13", "A_T16": "", "B_T14": "",
    }  # fmt: skip
    assert frame.set_index("segment_id").loc["A_T15", "explanation"] == (
        "Lap 1, turn 15: lost; the same moment as being lapped at turn 13, so the time lost "
        "doesn't count towards its ranking."
    )
    listed = frame.loc[listed_flags(frame), "segment_id"].tolist()
    assert listed == ["A_T12", "A_T15", "A_T16", "B_T14"]  # race control's stays listed
    out = merge_overlapping(frame).set_index("segment_id")
    assert out.loc["A_T14", "merged_into"] == ""  # the traffic's moment, in no event
    assert out.loc["A_T15", "merged_into"] == "A_T15"  # T12, T15 and T16 aren't one run
    # Traffic explains the covered race-control flag's loss: it ranks on the bonus alone.
    p = priority(
        frame["time_lost_s"],
        pd.Series(1.0, index=frame.index),
        frame["mistake_type"].isin(["track_limits"]),
        traffic=(frame["traffic"] != "") | (frame["covered_by"] != ""),
    )
    assert p[frame["segment_id"] == "A_T15"].iloc[0] == pytest.approx(0.5)
    assert p[frame["segment_id"] == "A_T12"].iloc[0] == pytest.approx(1.0)


def test_leftovers_from_the_last_review() -> None:
    # A lap class in the sentence reads as words, not the raw class.
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, brake_d=-170.0, lap_class="start")],
                       code="R")  # fmt: skip
    assert "on the opening lap" in explain(seg, row_of(seg, "AAA", 9)).sentence
    # Several drivers' deleted lap times at one turn aren't "something on track".
    seg, row = race_with_mistake(pickup_d=50.0)
    limits = {"driver": "AAA", "lap_number": 9, "turn": 3, "kind": "track_limits",
              "message": "TRACK LIMITS AT TURN 3"}  # fmt: skip
    events = events_frame([limits]).assign(drivers_same_lap_turn=4)
    text = explain(seg, row, events).sentence
    assert "4 drivers had lap times deleted at this turn on this lap" in text
    assert "something on track" not in text


# ---------------------------------------------------------------------------------------------
# Traffic measured where the loss began (the review of the traffic rules)


def onset_of(seg: SessionSegments, row: int) -> float:
    return loss_onset(compare(seg, row))


def test_a_lapping_car_close_when_the_loss_began_is_traffic() -> None:
    # Early braking (the loss begins 95 m before the apex). BBB, a lap ahead, was 2.5 s behind
    # 450 m before the apex, beyond the old start-gap bar, but AAA had slowed to 44 m/s from
    # 2600 m and BBB was within LAPPED_GAP_S when the loss began, then went past.
    seg, row = race_with_mistake(brake_d=-170.0)
    tracks = lapped_session(2.5, slow=(2600.0, 3200.0, 44.0))
    exp = explain(seg, row, tracks=tracks)
    onset = onset_of(seg, row)
    (bbb,) = exp.traffic.encounters
    assert bbb.gap_start_s > 1.7 and 0 < bbb.gap_onset_s <= LAPPED_GAP_S < bbb.gap_start_s
    assert bbb.pass_m > onset
    assert exp.mistake_type == "lapped" and exp.type_without_traffic == "early_braking"
    assert (
        f"being lapped: BBB (a lap ahead), {bbb.gap_onset_s:.1f} s behind {-onset:.0f} m before "
        "the apex, where the loss began, went past" in exp.sentence
    )


def test_a_lapping_car_out_of_the_pits_is_measured_where_it_has_a_position() -> None:
    # A corner at 700 m: CCC, a lap ahead, leaves the pit lane on its out-lap and has no
    # position 450 m before the apex. From where it has one (305 m) it is 0.7 s behind,
    # closes, and passes AAA after AAA's loss began: AAA let it by.
    specs = [*usual_laps(apex_m=700.0), lap_spec("AAA", 9, apex_m=700.0, brake_d=-170.0)]
    seg = make_session(specs, code="R")
    row = row_of(seg, "AAA", 9)
    out = speeds(55.0, (0.0, 150.0, 20.0))
    t_out, _ = lap_clock(out)
    start = 800.0 + 305.0 / 50.0 + 0.7 - float(np.interp(305.0, GRID_M, t_out))
    ccc = laps_of("CCC", 1, 9, start - 9 * 100.0, 50.0)
    ccc.append(Lap("CCC", 10, start, out, "out", pit_out=True))
    exp = explain(seg, row, tracks=make_tracks(laps_of("AAA", 1, 12, 0.0, 50.0) + ccc))
    (e,) = exp.traffic.encounters
    assert e.gap_start_m > -450.0 and e.gap_start_s == pytest.approx(0.7, abs=0.05)
    assert exp.mistake_type == "lapped" and exp.traffic.cars == ("CCC",)


def test_a_mistake_with_the_lapping_car_still_far_back_keeps_its_type() -> None:
    # A big slow corner (a lock-up), and BBB, a lap ahead, still more than LAPPED_GAP_S behind
    # when the loss began, passes because of it: the mistake stays listed.
    seg, row = race_with_mistake(v_min=60.0, pickup_d=40.0)
    exp = explain(seg, row, tracks=lapped_session(3.0, slow=(3000.0, 3445.0, 20.0)))
    (bbb,) = exp.traffic.encounters
    assert bbb.passed == "by" and bbb.gap_onset_s > LAPPED_GAP_S
    assert exp.mistake_type == "over_slowing" and exp.traffic.kind == ""
    assert f"BBB (a lap ahead), {bbb.gap_onset_s:.1f} s behind" in exp.sentence
    assert "went past" in exp.sentence


def test_a_costly_yield_from_further_back_is_still_traffic() -> None:
    # BBB, a lap ahead, 1.6 s behind when the loss began (70 m before the apex): further back
    # than 95 of 100 yields on ordinary corners begin (1.43 s), but a yield that costly is
    # flagged, and over the flagged corners too yields begin from up to LAPPED_GAP_S.
    seg, row = race_with_mistake(v_min=80.0, brake_d=-150.0)
    closing = (450.0 - 70.0) * (1 / 50 - 1 / 55)  # what BBB gains up to the onset
    exp = explain(seg, row, tracks=lapped_session(1.6 + closing, slow=(3000.0, 3445.0, 20.0)))
    (bbb,) = exp.traffic.encounters
    assert 1.43 < bbb.gap_onset_s <= LAPPED_GAP_S and bbb.pass_m > -70.0
    assert exp.mistake_type == "lapped" and exp.type_without_traffic == "over_slowing"


def braking_on_the_straight() -> dict[str, np.ndarray]:
    """Over-slowing (85 km/h at the apex), then braking hard from 80 m to 110 m after it, where
    the usual lap is back on full throttle: letting a car by."""
    trace = corner_trace(v_min=85.0)
    d = DISTANCE
    brake_zone = (d >= 80) & (d < 110)
    v2 = (trace["speed"] / 3.6) ** 2
    for i in range(int(np.argmax(brake_zone)), len(d)):
        a = -15.0 if brake_zone[i] else 8.0
        v2[i] = min(max(v2[i - 1] + 2 * a * 5.0, 20.0**2), v2[i])
    speed = np.sqrt(v2) * 3.6
    mps = speed / 3.6
    time_s = np.concatenate([[0.0], np.cumsum(5.0 / ((mps[1:] + mps[:-1]) / 2))])
    return trace | {
        "speed": speed,
        "time_s": time_s,
        "throttle": np.where(brake_zone, 0.0, trace["throttle"]),
        "brake": np.where(brake_zone, 1.0, trace["brake"]),
    }


def test_braking_on_the_straight_to_let_a_lapping_car_by() -> None:
    seg = make_session([*usual_laps(), lap_spec("AAA", 9, shape=braking_on_the_straight())],
                       code="R")  # fmt: skip
    row = row_of(seg, "AAA", 9)
    onset = onset_of(seg, row)
    assert onset < 80.0 and yield_point(seg, compare(seg, row)) == pytest.approx(80.0)
    # BBB, a lap ahead, is beyond LAPPED_GAP_S when the loss begins, but AAA (at 30 m/s from
    # 10 m before the apex) lets it close to within YIELD_GAP_S by the time it brakes, and it
    # passes from there on.
    slow = (2990.0, 3200.0, 30.0)
    exp = explain(seg, row, tracks=lapped_session(LAPPED_GAP_S + 1.4, slow=slow))
    (bbb,) = exp.traffic.encounters
    at_yield = bbb.closest_before(80.0)[0]
    assert bbb.gap_onset_s > LAPPED_GAP_S and 0 < at_yield <= YIELD_GAP_S and bbb.pass_m > 80
    assert exp.mistake_type == "lapped" and exp.traffic.cars == ("BBB",)
    assert (
        "being lapped: braked 80 m after the apex, where the usual lap is on the power, with "
        f"BBB (a lap ahead) {at_yield:.1f} s behind, and it went past" in exp.sentence
    )
    # Without braking there, the same over-slowing with BBB that far back is the driver's.
    plain = make_session([*usual_laps(), lap_spec("AAA", 9, v_min=85.0)], code="R")
    alone = explain(plain, row, tracks=lapped_session(LAPPED_GAP_S + 1.4, slow=slow))
    assert alone.mistake_type == "over_slowing"
    # Braking early for the corner looks like a mistake, not a yield: no signature there.
    early, early_row = race_with_mistake(brake_d=-170.0)
    assert np.isnan(yield_point(early, compare(early, early_row)))


V_SAME = 50.5  # m/s: a car on the same lap, closing slowly


def same_lap_pass(gap_s: float, onset_m: float) -> Any:
    """BBB on the same lap at V_SAME m/s, `gap_s` behind AAA 450 m before the 3000 m apex, closing
    slowly until AAA slows to 25 m/s after the apex: how far back it is at `onset_m`."""
    tracks = lapped_session(gap_s, ahead=0, slow=(3000.0, 3200.0, 25.0), v_other=V_SAME)
    around = traffic.surroundings(tracks, "AAA", 9, 3000.0, onset_m)
    assert around is not None
    return tracks, around.encounters[0]


def test_a_car_that_passes_after_the_loss_began_from_behind_is_not_a_battle() -> None:
    # Over-slowing (the loss begins 5 m before the apex). BBB, on the same lap and within
    # BATTLE_GAP_S 450 m before the apex, was still more than ALONGSIDE_S back when the loss
    # began, and passed because of it, unslowed: the loss stays the driver's.
    seg, row = race_with_mistake(v_min=80.0)
    onset = onset_of(seg, row)
    closing = (450 + onset) * (1 / 50 - 1 / V_SAME)
    tracks, bbb = same_lap_pass(ALONGSIDE_S + 0.1 + closing, onset)
    assert abs(bbb.gap_start_s) <= BATTLE_GAP_S and bbb.gap_onset_s > ALONGSIDE_S
    assert bbb.passed == "by" and bbb.pass_m > onset
    exp = explain(seg, row, tracks=tracks)
    assert exp.mistake_type == "over_slowing" and exp.traffic.kind == ""
    assert f"lost the place to BBB ({bbb.gap_onset_s:.1f} s behind" in exp.sentence
    # Alongside when the loss began, it is the fight for the place.
    tracks, bbb = same_lap_pass(ALONGSIDE_S / 2 + closing, onset)
    assert 0 < bbb.gap_onset_s <= ALONGSIDE_S
    fight = explain(seg, row, tracks=tracks)
    assert fight.mistake_type == "battle" and fight.traffic.cars == ("BBB",)
    assert "racing BBB: BBB, " in fight.sentence


def test_a_battle_with_a_car_slowed_itself() -> None:
    # BBB, 0.5 s behind and not alongside when the loss began, was slowed through the corner
    # too (2 s slower than its usual): they fought for the place.
    seg, row = race_with_mistake(v_min=80.0)
    laps = lapped_laps(0.55, ahead=0, slow=(3000.0, 3200.0, 25.0), v_other=51.0)
    laps = [
        replace(lap, v=speeds(51.0, (3000.0, 3145.0, 30.0)))
        if (lap.driver, lap.lap) == ("BBB", 9)
        else lap
        for lap in laps
    ]
    # Its later laps start later by what lap 9 cost it.
    cost = lap_clock(speeds(51.0, (3000.0, 3145.0, 30.0)))[1] - lap_clock(speeds(51.0))[1]
    laps = [replace(lap, start_s=lap.start_s + cost) if lap.driver == "BBB" and lap.lap > 9
            else lap for lap in laps]  # fmt: skip
    exp = explain(seg, row, tracks=make_tracks(laps))
    (bbb,) = exp.traffic.encounters
    assert bbb.gap_onset_s > ALONGSIDE_S and bbb.window_s > bbb.usual_window_s + 1.0
    assert exp.mistake_type == "battle"
    assert "slowed too" in exp.sentence


def test_race_control_ranks_on_the_full_loss_when_the_car_was_still_behind() -> None:
    # Track limits, with BBB lapping AAA: 1.6 s behind 450 m before the apex, about 1 s when
    # the loss began (95 m before it) and past only after it, as AAA slowed. The deletion
    # keeps its type, and the full loss counts, with the bonus.
    seg, row = race_with_mistake(brake_d=-170.0)
    limits = {"driver": "AAA", "lap_number": 9, "turn": 3, "kind": "track_limits",
              "message": "TRACK LIMITS AT TURN 3"}  # fmt: skip
    events = events_frame([limits])
    later = explain(seg, row, events, tracks=lapped_session(1.6, slow=(2905.0, 3200.0, 25.0)))
    (nor,) = later.traffic.encounters
    assert YIELD_START_S < nor.gap_onset_s <= LAPPED_GAP_S and nor.pass_m > -95.0
    assert later.mistake_type == "track_limits" and later.traffic.kind == "lapped"
    assert not later.traffic.explains and later.traffic.alongside == ()
    assert "the loss began before they reached the driver, so it counts towards its ranking" in (
        later.sentence
    )
    # BBB already past when the loss began, or close enough that AAA was yielding to it as the
    # loss began (within YIELD_START_S, passing just after): the time lost is the traffic's.
    past = explain(seg, row, events, tracks=lapped_session(1.0))
    assert past.traffic.explains and past.traffic.alongside == ("BBB",)
    close = explain(seg, row, events, tracks=lapped_session(0.95, slow=(2905.0, 3200.0, 25.0)))
    (bbb,) = close.traffic.encounters
    assert 0 < bbb.gap_onset_s <= YIELD_START_S and bbb.pass_m > -95.0
    assert close.traffic.explains and "doesn't count towards its ranking" in close.sentence
    frame = pd.DataFrame(
        {
            "segment_id": ["later", "past"],
            "traffic": ["lapped", "lapped"],
            "traffic_cars": ["BBB", "BBB"],
            "traffic_alongside": [", ".join(later.traffic.alongside), "BBB"],
            "covered_by": ["", ""],
        }
    )
    explained = traffic_explained(frame)
    assert explained.tolist() == [False, True]
    lost = pd.Series([later.comparison.time_lost_s, past.comparison.time_lost_s])
    p = priority(lost, pd.Series([1.0, 1.0]), pd.Series([True, True]), traffic=explained)
    assert p.tolist() == pytest.approx([lost[0] + 0.5, 0.5])


def test_a_covered_race_control_flag_counts_unless_the_cars_were_alongside() -> None:
    lapped, none = ("lapped", -450.0, 445.0), ("", -450.0, 445.0)
    limits = "TRACK LIMITS AT TURN 15"
    frame = cover_frame([
        ("A", 13, 3000.0, "lapped", 0.0, 3.0, 1.0, "", *lapped),
        ("A", 15, 3300.0, "track_limits", 1.5, 1.0, 0.2, limits, *none),
        ("B", 13, 3000.0, "lapped", 0.0, 3.0, 1.0, "", *lapped),
        ("B", 15, 3300.0, "track_limits", 1.5, 1.0, 0.2, limits, *none),
    ])  # fmt: skip
    frame["traffic_cars"] = ["NOR", "", "NOR", ""]
    frame["traffic_alongside"] = ["NOR", "", "NOR", "NOR"]  # at B's turn 15, NOR was past
    frame["covered_by"] = traffic_cover(frame)
    assert frame["covered_by"].tolist() == ["", "A_T13", "", "B_T13"]
    assert traffic_explained(frame).tolist() == [True, False, True, True]
    _tell_covered(frame)
    texts = frame.set_index("segment_id")["explanation"]
    assert texts["A_T15"].endswith(
        "the same moment as being lapped at turn 13, but those cars hadn't reached the driver "
        "yet when the loss began here, so it counts towards its ranking."
    )
    assert texts["B_T15"].endswith("so the time lost doesn't count towards its ranking.")


def test_a_slow_down_that_began_on_the_previous_lap() -> None:
    # Eight corners a lap in a race; the driver slows from turn 5 of lap 8 on and stays slow
    # on lap 9 (damage): lap 9's turn 2 is in the middle of that slow stretch, though only turn
    # 1 comes before it on its own lap.
    def specs(lap8_class: str = "race") -> list[dict]:
        out = []
        for lap in range(1, 11):
            for corner in range(1, 9):
                slow = (lap == 8 and corner >= 5) or lap == 9
                kind = lap8_class if lap == 8 else "race"
                out.append(lap_spec("AAA", lap, corner, scale=0.8 if slow else 1.0,
                                    lap_class=kind))  # fmt: skip
        return out

    seg = make_session(specs(), code="R")
    exp = explain(seg, row_of(seg, "AAA", 9, 2))
    assert exp.mistake_type == "slowdown"
    assert "the corners before it were slow too (from the end of the previous lap on)" in (
        exp.sentence
    )
    # After an out-lap (or a safety car), the lap before is slow for reasons of its own.
    after_pits = make_session(specs("out"), code="R")
    assert explain(after_pits, row_of(after_pits, "AAA", 9, 2)).mistake_type != "slowdown"


def final_lap_tracks(passer: bool = False, retired: bool = False) -> Any:
    """A 10-lap race: AAA wins it (its lap 10 ends first); BBB finishes 5 s behind on the
    same lap, or with `passer` catches AAA and passes it at the last corner (apex 4000 m). With
    `retired`, AAA stops after lap 10 while BBB, a lap ahead, finishes on its lap 12."""
    aaa = laps_of("AAA", 1, 10, 0.0, 50.0)
    if retired:
        return make_tracks(aaa + laps_of("BBB", 1, 12, -95.0, 50.0))
    if passer:
        aaa[-1] = Lap("AAA", 10, 900.0, speeds(50.0, (3900.0, 4300.0, 20.0)))
    bbb = laps_of("BBB", 1, 10, 5.0 if not passer else 2.0, 50.0)
    return make_tracks(aaa + bbb)


def test_the_run_to_the_flag_is_not_a_mistake() -> None:
    specs = []
    for corner in (1, 2, 3, 4):  # apexes at 1000-4000 m; the last FLAG_RUN_CORNERS count
        specs += usual_laps(laps=range(1, 10), corner=corner)
        specs.append(lap_spec("AAA", 10, corner, pickup_d=50.0))
    seg = make_session(specs, code="R")
    row = row_of(seg, "AAA", 10, 4)
    exp = explain(seg, row, tracks=final_lap_tracks())
    assert exp.mistake_type == "slowdown"
    assert "the run to the flag" in exp.sentence
    if FLAG_RUN_CORNERS < 4:  # earlier on the final lap, a mistake is a mistake
        first = explain(seg, row_of(seg, "AAA", 10, 1), tracks=final_lap_tracks())
        assert first.mistake_type == "late_throttle"
    # A place lost there is a moment of its own.
    passed = explain(seg, row, tracks=final_lap_tracks(passer=True))
    assert passed.mistake_type != "slowdown" and "lost the place to BBB" in passed.sentence
    # A lap AAA retired on isn't the run to the flag.
    retired = explain(seg, row, tracks=final_lap_tracks(retired=True))
    assert retired.mistake_type == "late_throttle"
    # Race control naming the driver comes first.
    limits = {"driver": "AAA", "lap_number": 10, "turn": 4, "kind": "track_limits",
              "message": "TRACK LIMITS AT TURN 4"}  # fmt: skip
    named = explain(seg, row, events_frame([limits]), tracks=final_lap_tracks())
    assert named.mistake_type == "track_limits"


def test_race_control_not_slower_says_nothing_about_the_ranking() -> None:
    # A deleted lap that wasn't slower, with a car lapping: the cars are context only.
    seg, row = race_with_mistake(brake_d=-100.0, v_min=112.0)
    limits = {"driver": "AAA", "lap_number": 9, "turn": 3, "kind": "track_limits",
              "message": "TRACK LIMITS AT TURN 3"}  # fmt: skip
    exp = explain(seg, row, events_frame([limits]), tracks=lapped_session(1.0))
    assert exp.mistake_type == "track_limits" and "quicker" in exp.sentence
    assert "being lapped: BBB" in exp.sentence
    assert "ranking" not in exp.sentence


def encounter(driver: str, gap_s: float, at_m: float) -> Encounter:
    return Encounter(
        driver=driver, laps_ahead=1, lap_class="race", gap_start_s=gap_s, gap_window_s=0.0,
        min_gap_s=0.0, behind_s=math.nan, passed="by", pass_m=0.0, swapped_back=False,
        window_s=7.0, approximate=False, gap_start_m=at_m,
    )  # fmt: skip


def test_gaps_are_given_where_each_car_was_measured() -> None:
    # PER had a position from 410 m before the apex only, RUS from 450 m.
    per, rus = encounter("PER", 0.13, -410.0), encounter("RUS", 1.87, -450.0)
    assert _gap_range([per, rus]) == (
        "PER 0.1 s behind 410 m before the apex, RUS 1.9 s behind 450 m before the apex"
    )
    assert (
        _gap_range([rus, encounter("SAI", 2.6, -450.0)]) == "1.9-2.6 s behind 450 m before the apex"
    )
    assert _gap_range([encounter("SAI", math.nan, math.nan)]) == ""


def test_held_up_by_a_car_rejoining_from_the_pit_exit() -> None:
    # A corner at 700 m. CCC, a lap down, leaves the pits: the exit lane runs 20 m beside the
    # track up to 520 m, inside the corner's window, where it rejoins 0.3 s ahead of AAA at
    # 44.5 m/s; AAA follows it through the corner. It had no position before (the pit exit,
    # not a feed hole), so it held AAA up from where it rejoined.
    specs = [*usual_laps(apex_m=700.0), lap_spec("AAA", 9, apex_m=700.0, v_min=75.0)]
    seg = make_session(specs, code="R")
    row = row_of(seg, "AAA", 9)
    aaa = laps_of("AAA", 1, 8, 0.0, 50.0)
    aaa.append(Lap("AAA", 9, 800.0, speeds(50.0, (520.0, 900.0, 44.5))))
    aaa += laps_of("AAA", 10, 3, 800.0 + lap_clock(aaa[-1].v)[1], 50.0)
    out = speeds(44.5, (0.0, 150.0, 20.0))
    lane = np.where(GRID_M < 520.0, 20.0, 0.0)
    t_out, _ = lap_clock(out)
    start = 800.0 + 520.0 / 50.0 - 0.3 - float(np.interp(520.0, GRID_M, t_out))
    ccc = laps_of("CCC", 1, 7, start - 7 * 100.0, 50.0)
    ccc.append(Lap("CCC", 8, start, out, "out", pit_out=True, offset=lane))
    exp = explain(seg, row, tracks=make_tracks(aaa + ccc))
    (e,) = exp.traffic.encounters
    assert (
        np.isnan(e.gap_start_s)
        and e.laps_ahead == -1
        and e.behind_s == pytest.approx(0.3, abs=0.05)
    )
    assert exp.mistake_type == "impeded" and exp.traffic.cars == ("CCC",)
    assert "held up by CCC (on its out lap, a lap down)" in exp.sentence
    assert "it set their pace, so not a mistake" in exp.sentence
