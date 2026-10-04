from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pandas as pd
from synthetic import LAP_TIME_S, LENGTH_M, SPEED_MPS, circle_xy_dm

from race_engineer.data.build import build_reference, chord_error_m


def fake_session(stall_s: dict[str, float], spike: str | None = None) -> Any:
    """Two drivers, one lap each (driver "1" slightly faster); `stall_s` stalls a driver's
    position feed for that many seconds in the middle of the lap, and `spike` throws one of that
    driver's fixes 40 m off the track."""
    laps, pos, car = [], {}, {}
    for number, lap_time in (("1", LAP_TIME_S - 0.5), ("2", LAP_TIME_S)):
        t = np.arange(-2.0, LAP_TIME_S + 2.0, 0.25)
        x, y = circle_xy_dm(t * SPEED_MPS)
        if stall_s.get(number):
            stalled = np.flatnonzero((t > 20) & (t < 20 + stall_s[number]))
            x[stalled], y[stalled] = x[stalled[0] - 1], y[stalled[0] - 1]
        if number == spike:
            k = int(np.argmin(np.abs(t - 30)))
            x[k], y[k] = x[k] * 1.08, y[k] * 1.08  # 40 m outside the 500 m circle
        time = pd.to_timedelta(t, unit="s")
        pos[number] = pd.DataFrame({"SessionTime": time, "X": x, "Y": y})
        car[number] = pd.DataFrame({"SessionTime": time, "Speed": np.full(len(t), SPEED_MPS * 3.6)})
        laps.append(
            {
                "Driver": f"D{number}",
                "DriverNumber": number,
                "LapNumber": 1,
                "LapTime": pd.to_timedelta(float(lap_time), unit="s"),
                "LapStartTime": pd.Timedelta(0),
                "PitInTime": pd.NaT,
                "PitOutTime": pd.NaT,
            }
        )
    return cast(Any, SimpleNamespace(laps=pd.DataFrame(laps), pos_data=pos, car_data=car))


def test_the_fastest_lap_is_the_reference() -> None:
    reference, lap = build_reference(fake_session({}))
    assert lap.driver == "D1"
    assert abs(reference.length_m - LENGTH_M) / LENGTH_M < 0.01


def test_a_lap_with_a_stalled_position_feed_is_passed_over() -> None:
    # A 3 s stall is a 150 m chord across the circle: the slower, complete lap is used instead.
    _, lap = build_reference(fake_session({"1": 3.0}))
    assert lap.driver == "D2"


def test_with_no_complete_lap_the_most_complete_is_used() -> None:
    _, lap = build_reference(fake_session({"1": 4.0, "2": 2.0}))
    assert lap.driver == "D2"


def test_a_lap_with_a_position_spike_is_passed_over() -> None:
    # One glitched fix would leave a spike in the line that every other lap skips over.
    _, lap = build_reference(fake_session({}, spike="1"))
    assert lap.driver == "D2"


def test_a_gap_on_a_straight_is_harmless() -> None:
    # 100 m between two fixes: on a straight the chord is the path, around a bend it cuts in.
    straight_x, straight_y = np.arange(0, 1000, 20.0), np.zeros(50)
    gap = np.r_[0:20, 25:50]
    assert chord_error_m(straight_x[gap], straight_y[gap]) < 0.1
    theta = np.arange(50) * 20.0 / 100  # a bend of radius 100 m, fixes every 20 m
    assert chord_error_m(100 * np.cos(theta[gap]), 100 * np.sin(theta[gap])) > 5


def test_a_long_gap_ranks_below_a_short_glitch() -> None:
    # Neither lap is clean: driver 1's feed stalls for 5 s (250 m, over the limit), driver 2
    # has a single glitched fix. The glitch does less harm to the line.
    _, lap = build_reference(fake_session({"1": 5.0}, spike="2"))
    assert lap.driver == "D2"
