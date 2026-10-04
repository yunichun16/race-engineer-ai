import numpy as np
from synthetic import LAP_TIME_S, LENGTH_M, RADIUS_M, SPEED_MPS, circle_xy_dm, driver_telemetry

from race_engineer.features.grid import (
    GRID_CHANNELS,
    DriverTelemetry,
    lap_to_grid,
    make_grid,
    position_fixes,
)
from race_engineer.features.reference import ReferenceLine

REFERENCE = ReferenceLine.from_positions(*circle_xy_dm(np.arange(0, LENGTH_M, 20.0)))
GRID = make_grid(REFERENCE.length_m, 5.0)


def test_lap_is_resampled_onto_the_grid() -> None:
    result = lap_to_grid(driver_telemetry(), LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert result.qa.ok, result.qa.reason
    assert set(result.channels) == set(GRID_CHANNELS)
    assert all(len(v) == len(GRID) for v in result.channels.values())
    # Constant speed: time along the lap is distance / speed, starting at the line.
    assert np.allclose(result.channels["time_s"], GRID / SPEED_MPS, atol=0.1)
    assert np.allclose(result.channels["speed"], SPEED_MPS * 3.6)
    assert np.all(np.abs(result.channels["offset_m"]) < 1.5)


def test_every_lap_lines_up_on_the_same_axis() -> None:
    tel = driver_telemetry(n_laps=3)
    laps = [lap_to_grid(tel, k * LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID) for k in range(3)]
    assert all(lap.qa.ok for lap in laps)
    assert np.allclose(laps[0].channels["x_m"], laps[2].channels["x_m"], atol=1.0)


def test_missing_position_data_is_rejected() -> None:
    tel = driver_telemetry(pos_gap=(LAP_TIME_S - 5, 2 * LAP_TIME_S + 5))
    result = lap_to_grid(tel, LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert not result.qa.ok
    assert result.qa.reason == "no position data"


def test_driving_off_the_line_is_flagged() -> None:
    tel = driver_telemetry(radius_m=RADIUS_M + 40)  # e.g. the pit lane
    result = lap_to_grid(tel, LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert not result.qa.ok
    assert "off the reference line" in result.qa.reason


def test_a_frozen_feed_is_marked_invalid() -> None:
    # 5 s of repeated car data in the middle of lap 1 (lap 1 runs LAP_TIME_S..2*LAP_TIME_S).
    start = LAP_TIME_S + 20
    tel = driver_telemetry(frozen=(start, start + 5))
    result = lap_to_grid(tel, LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert result.qa.ok  # a short freeze only invalidates part of the lap
    frozen_m = GRID[result.channels["valid"] == 0]
    assert 180 < frozen_m.max() - frozen_m.min() < 320  # ~5 s at 50 m/s, give or take a sample
    assert abs(frozen_m.min() - 20 * SPEED_MPS) < 30


def test_a_mostly_frozen_lap_is_rejected() -> None:
    tel = driver_telemetry(frozen=(LAP_TIME_S + 5, 2 * LAP_TIME_S - 5))
    result = lap_to_grid(tel, LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert not result.qa.ok and "frozen car data" in result.qa.reason


def test_only_new_positions_count_as_fixes() -> None:
    x = np.array([0.0, 1.0, 1.0, 1.0, 2.0, 2.0])
    y = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    assert position_fixes(x, y).tolist() == [True, True, False, False, True, True]


def test_a_stalled_position_feed_keeps_the_car_data() -> None:
    # 1.5 s of repeated positions (75 m): distance comes from speed, nothing is lost.
    start = LAP_TIME_S + 20
    result = lap_to_grid(
        driver_telemetry(pos_stall=(start, start + 1.5)), LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID
    )
    assert result.qa.ok, result.qa.reason
    assert result.channels["valid"].min() == 1
    assert np.allclose(result.channels["time_s"], GRID / SPEED_MPS, atol=0.1)
    assert 70 < result.qa.max_fix_gap_m < 100


def test_a_long_position_stall_is_marked_invalid() -> None:
    start = LAP_TIME_S + 20
    result = lap_to_grid(
        driver_telemetry(pos_stall=(start, start + 6)), LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID
    )
    assert result.qa.ok, result.qa.reason
    invalid_m = GRID[result.channels["valid"] == 0]
    assert abs(invalid_m.min() - 20 * SPEED_MPS) < 30
    assert 250 < invalid_m.max() - invalid_m.min() < 350  # ~6 s at 50 m/s
    assert np.allclose(result.channels["time_s"], GRID / SPEED_MPS, atol=0.1)


def test_a_mostly_stalled_position_feed_is_rejected() -> None:
    tel = driver_telemetry(pos_stall=(LAP_TIME_S + 5, 2 * LAP_TIME_S - 30))
    result = lap_to_grid(tel, LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert not result.qa.ok and "sparse position data" in result.qa.reason


def test_distance_through_a_position_stall_follows_the_speed_trace() -> None:
    # The car slows and speeds up while the position feed is stalled; a straight line between
    # the two fixes would put the braking in the wrong place.
    t = np.arange(-2.0, 2 * LAP_TIME_S + 10, 0.25)
    speed = SPEED_MPS + 20 * np.sin(2 * np.pi * t / 8)  # m/s
    distance = SPEED_MPS * t + 20 * 8 / (2 * np.pi) * (1 - np.cos(2 * np.pi * t / 8))
    x, y = circle_xy_dm(distance)
    stalled = np.flatnonzero((t > 30) & (t < 31.75))  # the fix before 30 s repeats
    x[stalled], y[stalled] = x[stalled[0] - 1], y[stalled[0] - 1]
    n = len(t)
    tel = DriverTelemetry(
        t_car=t,
        speed=speed * 3.6,
        throttle=np.full(n, 100.0),
        brake=np.zeros(n),
        gear=np.full(n, 7.0),
        rpm=11000.0 + np.round(40 * np.sin(np.arange(n))),
        drs=np.zeros(n),
        t_pos=t,
        x=x,
        y=y,
    )
    result = lap_to_grid(tel, 0.0, float(np.interp(LENGTH_M, distance, t)), REFERENCE, GRID)
    assert result.qa.ok, result.qa.reason
    true_time = np.interp(GRID, distance, t)
    assert np.abs(result.channels["time_s"] - true_time).max() < 0.05


def test_a_car_data_gap_is_marked_invalid() -> None:
    start = LAP_TIME_S + 20
    result = lap_to_grid(
        driver_telemetry(car_gap=(start, start + 4)), LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID
    )
    assert result.qa.ok, result.qa.reason
    invalid_m = GRID[result.channels["valid"] == 0]
    assert 150 < invalid_m.max() - invalid_m.min() < 260  # ~4 s at 50 m/s
    assert result.qa.gap_share < 0.1


def test_a_lap_mostly_missing_car_data_is_rejected() -> None:
    tel = driver_telemetry(car_gap=(LAP_TIME_S + 5, 2 * LAP_TIME_S - 30))
    result = lap_to_grid(tel, LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert not result.qa.ok and "gap in samples" in result.qa.reason


def test_a_stuck_speed_channel_is_marked_invalid() -> None:
    # The speed channel reads 300 km/h for 4 s while the car keeps doing 180 km/h.
    start = LAP_TIME_S + 20
    tel = driver_telemetry(stuck_speed=(start, start + 4, 300.0))
    result = lap_to_grid(tel, LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert result.qa.ok, result.qa.reason
    invalid_m = GRID[result.channels["valid"] == 0]
    assert len(invalid_m) and abs(invalid_m.min() - 20 * SPEED_MPS) < 100
    assert invalid_m.max() < 20 * SPEED_MPS + 4 * SPEED_MPS + 150
    # Distance stays pinned to the fixes: time along the lap is still right.
    assert np.allclose(result.channels["time_s"], GRID / SPEED_MPS, atol=0.1)


def test_steady_driving_has_no_mismatch() -> None:
    result = lap_to_grid(driver_telemetry(), LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    assert result.qa.mismatch_share == 0


def test_a_speed_channel_stuck_low_is_marked_invalid() -> None:
    # Stuck at 40 km/h: distance from speed barely grows while the fixes move on at 180 km/h.
    start = LAP_TIME_S + 20
    tel = driver_telemetry(stuck_speed=(start, start + 10, 40.0))
    result = lap_to_grid(tel, LAP_TIME_S, LAP_TIME_S, REFERENCE, GRID)
    wrong = (GRID > 20 * SPEED_MPS + 50) & (GRID < 30 * SPEED_MPS - 50)
    assert (result.channels["valid"][wrong] == 0).all()
