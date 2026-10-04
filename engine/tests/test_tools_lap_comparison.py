"""compare_laps on synthetic laps whose time gaps are known exactly.

The track is 5 km with every lap at 180 km/h (100 s), except where a test slows a driver down:
BBB's lap 2 drops to 162 km/h for 200 m around T2, which costs 200 * (1/45 - 1/50) = 0.444 s.
The hairpin session has a real braking zone at T2, for the stale speed feed test.
"""

from pathlib import Path

import numpy as np
import pytest

from race_engineer.tools.lap_comparison import (
    MAX_POINTS,
    braking_points,
    compare_laps,
    lap_time_trace,
    stale_speed,
    top_speed,
)
from race_engineer.tools.sessions import ToolInputError
from race_engineer.tools.synthetic import (
    GRID_STEP_M,
    SyntheticLap,
    SyntheticSession,
    grid_distance,
    lap_clock,
    write_session,
)

LENGTH_M = 5000.0
N = len(grid_distance(LENGTH_M))
CORNERS = ((1, "", 500.0), (2, "", 1100.0), (3, "", 2500.0), (4, "a", 4000.0))
SLOW_ZONE = slice(200, 240)  # 1000-1195 m, around T2
T2_LOSS_S = 200 * (1 / 45 - 1 / 50)


def speed(kph: float) -> np.ndarray:
    return np.full(N, kph)


def slow_at_t2() -> np.ndarray:
    trace = speed(180.0)
    trace[SLOW_ZONE] = 162.0
    return trace


def frozen(where: slice = slice(600, 620)) -> np.ndarray:
    valid = np.ones(N)
    valid[where] = 0.0
    return valid


def hairpin() -> np.ndarray:
    """180 km/h with a hairpin at T2: down to exactly 80 km/h at 1100 m."""
    return 180.0 - 100.0 * np.exp(-0.5 * ((grid_distance(LENGTH_M) - 1100.0) / 60.0) ** 2)


STUCK = slice(220, 281)  # 1100-1400 m: the speed feed holds the hairpin's 80 km/h


def stuck_after_hairpin() -> np.ndarray:
    reported = hairpin()
    reported[STUCK] = reported[STUCK.start]
    return reported


def lap(driver: str, number: int, trace: np.ndarray, **kwargs) -> SyntheticLap:
    team = {"AAA": "Team A", "BBB": "Team B", "CCC": "Team C", "DDD": "Team D"}[driver]
    code = {"AAA": "1", "BBB": "22", "CCC": "3", "DDD": "4"}[driver]
    return SyntheticLap(driver, number, trace, driver_number=code, team=team, **kwargs)


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("processed")
    quali = SyntheticSession(
        2026,
        1,
        "Q",
        "Test Grand Prix",
        "Testville",
        track_length_m=LENGTH_M,
        corners=CORNERS,
        laps=(
            lap("AAA", 2, speed(180.0)),  # AAA's fastest clean lap
            lap("AAA", 3, speed(175.0)),
            lap("AAA", 4, speed(190.0), deleted=True),  # faster, but the time was deleted
            lap("AAA", 5, speed(195.0), grid_ok=False),  # faster, but no telemetry
            lap("AAA", 6, speed(200.0), lap_class="out"),
            lap("BBB", 2, slow_at_t2()),  # BBB's fastest clean lap
            lap("BBB", 3, speed(170.0)),
            lap("BBB", 4, speed(160.0), valid=frozen()),
            lap("CCC", 1, speed(185.0), lap_class="slow"),  # CCC has no push lap
            # BBB's lap 2, with the car data frozen at 900-1105 m, across the T2 braking zone
            lap("DDD", 1, slow_at_t2(), valid=frozen(slice(180, 222))),
        ),
    )
    hairpin_session = SyntheticSession(
        2026,
        3,
        "Q",
        "Hairpin Grand Prix",
        "Hairpinville",
        track_length_m=LENGTH_M,
        corners=CORNERS,
        laps=(
            lap("AAA", 1, hairpin()),
            # The same lap, but the speed feed sticks at 80 km/h out of the hairpin while the
            # rest of the car data stays live (valid = 1).
            lap("BBB", 1, hairpin(), reported_speed_kph=stuck_after_hairpin()),
        ),
    )
    race = SyntheticSession(
        2026,
        1,
        "R",
        "Test Grand Prix",
        "Testville",
        date="2026-03-08",
        track_length_m=LENGTH_M,
        corners=CORNERS,
        laps=(
            lap("AAA", 1, speed(200.0), lap_class="start"),
            lap("AAA", 2, speed(178.0), lap_class="race"),
            lap("AAA", 3, speed(190.0), lap_class="neutralised"),
            lap("BBB", 2, speed(176.0), lap_class="race"),
        ),
    )
    long_track = SyntheticSession(
        2026,
        2,
        "Q",
        "Long Grand Prix",
        "Longville",
        track_length_m=8000.0,
        laps=(
            lap("AAA", 1, np.full(len(grid_distance(8000.0)), 200.0)),
            lap("BBB", 1, np.full(len(grid_distance(8000.0)), 199.0)),
        ),
    )
    for session in (quali, race, long_track, hairpin_session):
        write_session(session, root)
    return root


def test_defaults_to_each_drivers_fastest_clean_lap(root: Path) -> None:
    result = compare_laps("test", "AAA", "BBB", root=root)
    assert (result.lap_a.lap_number, result.lap_b.lap_number) == (2, 2)
    assert result.lap_a.default and result.lap_b.default
    assert result.gap_s == pytest.approx(T2_LOSS_S, abs=0.005)


def test_race_default_ignores_start_and_neutralised_laps(root: Path) -> None:
    result = compare_laps("test", "AAA", "BBB", session="race", root=root)
    assert result.lap_a.lap_number == 2
    assert result.session.name == "Race"


def test_running_gap_matches_where_time_was_lost(root: Path) -> None:
    chart = compare_laps("test", "AAA", "BBB", root=root).chart
    distance, delta = np.array(chart["distance_m"]), np.array(chart["delta_s"])
    assert delta[0] == pytest.approx(0.0, abs=1e-3)
    assert delta[np.searchsorted(distance, 900)] == pytest.approx(0.0, abs=1e-3)
    assert delta[np.searchsorted(distance, 1300)] == pytest.approx(T2_LOSS_S, abs=0.005)
    assert delta[-1] == pytest.approx(chart["gap_s"], abs=0.005)


def test_ranks_the_corner_where_time_was_lost_first(root: Path) -> None:
    result = compare_laps("test", "AAA", "BBB", root=root)
    ranked = sorted(result.corners, key=lambda c: abs(c.delta_s), reverse=True)
    assert ranked[0].label == "T2"
    assert ranked[0].delta_s == pytest.approx(T2_LOSS_S, abs=0.005)
    assert all(abs(c.delta_s) < 0.005 for c in ranked[1:])
    assert ranked[0].apex_speed == (pytest.approx(180.0), pytest.approx(162.0))
    assert [c.label for c in result.corners][-1] == "T4a"
    assert "T2 (1.10 km): AAA gained 0.44" in result.summary
    assert "only BBB braked" in result.summary  # AAA was flat out
    assert "AAA was 0.44" in result.summary
    assert "gained 0.000" not in result.summary  # turns that didn't matter are left out


def test_chart_payload_describes_both_laps(root: Path) -> None:
    chart = compare_laps("test", "aaa", "22", root=root).chart  # code in any case, or number
    a, b = chart["drivers"]
    assert (a["code"], a["team"], b["code"], b["team"]) == ("AAA", "Team A", "BBB", "Team B")
    assert a["lap_time_s"] == pytest.approx(100.0, abs=1e-3)
    assert len(chart["distance_m"]) == len(chart["delta_s"]) == len(a["speed"]) == N
    assert len(b["throttle"]) == len(b["brake"]) == N
    assert chart["corners"][1] | {"delta_s": None} == {
        "label": "T2",
        "number": 2,
        "letter": "",
        "apex_m": 1100.0,
        "delta_s": None,
        "apex_speed": [180.0, 162.0],
        "brake_m": [None, 980.0],  # the synthetic pedals see the drop 3 points early
        "braking": ["flat", "braked"],
    }
    assert (chart["event"], chart["year"], chart["session"]) == (
        "Test Grand Prix",
        2026,
        "Qualifying",
    )


def test_explicit_laps_and_notes(root: Path) -> None:
    result = compare_laps("test", "AAA", "BBB", lap_a=4, lap_b=3, root=root)
    _, a_time = lap_clock(speed(190.0), LENGTH_M)
    _, b_time = lap_clock(speed(170.0), LENGTH_M)
    assert result.gap_s == pytest.approx(b_time - a_time)
    assert "lap time deleted" in result.summary
    assert "(fastest" not in result.summary
    out_lap = compare_laps("test", "AAA", "BBB", lap_a=6, root=root)
    assert "AAA lap 6 was an out lap, not a representative push lap." in out_lap.summary


def test_frozen_car_data_is_left_out_of_the_chart(root: Path) -> None:
    chart = compare_laps("test", "AAA", "BBB", lap_b=4, root=root).chart
    speeds = chart["drivers"][1]["speed"]
    assert speeds[600:620] == [None] * 20
    assert speeds[599] == speeds[620] == 160.0
    assert all(d is not None for d in chart["delta_s"])  # timing still covers the gap


def test_frozen_braking_zone_is_unknown_not_flat(root: Path) -> None:
    result = compare_laps("test", "BBB", "DDD", root=root)  # identical laps
    t2 = result.corners[1]
    assert t2.braking == ("braked", "unknown")
    assert t2.brake_m == (980.0, None)
    assert t2.apex_speed[1] is None  # the apex window is partly frozen
    assert "only" not in result.summary
    assert "frozen (not recorded) for DDD 0.90 to 1.10 km" in result.summary
    assert result.chart["corners"][1]["braking"] == ["braked", "unknown"]


def test_stale_speed_feed_is_timed_by_the_clock(root: Path) -> None:
    result = compare_laps("hairpin", "AAA", "BBB", root=root)  # the same lap underneath
    chart = result.chart
    assert np.abs(chart["delta_s"]).max() < 0.01
    stuck = chart["drivers"][1]["speed"][STUCK]
    assert stuck == [None] * len(stuck)
    assert "frozen (not recorded) for BBB 1.10 to 1.40 km" in result.summary
    # Without the guard, integrating the stuck speed puts BBB well behind out of the hairpin.
    clock, lap_time = lap_clock(hairpin(), LENGTH_M)
    naive = lap_time_trace(clock, stuck_after_hairpin(), lap_time, LENGTH_M, GRID_STEP_M)
    assert np.abs(naive - clock).max() > 0.3


def test_stale_speed_leaves_plateaus_and_clock_glitches_alone() -> None:
    plateau = np.full(100, 300.0)
    clock, _ = lap_clock(plateau, 500.0)
    assert not stale_speed(clock, plateau, GRID_STEP_M).any()
    glitch = clock.copy()
    glitch[40:60] = glitch[40]  # the position clock stalls, then catches up
    assert not stale_speed(glitch, plateau, GRID_STEP_M).any()


def test_falls_back_to_fastest_lap_with_telemetry(root: Path) -> None:
    result = compare_laps("test", "AAA", "CCC", root=root)
    assert result.lap_b.lap_number == 1
    assert "CCC has no clean push lap" in result.summary
    assert "CCC (Team C) lap 1 (fastest lap with telemetry)" in result.summary


def test_long_laps_are_downsampled(root: Path) -> None:
    chart = compare_laps("long", "AAA", "BBB", root=root).chart
    assert len(chart["distance_m"]) <= MAX_POINTS
    assert chart["distance_m"][-1] == 8000.0 - GRID_STEP_M
    assert len(chart["drivers"][0]["speed"]) == len(chart["distance_m"])


@pytest.mark.parametrize(
    ("driver_a", "driver_b", "laps", "message"),
    [
        ("AAA", "XYZ", {}, "No driver 'XYZ'.*Drivers: AAA \\(Team A\\), BBB \\(Team B\\)"),
        ("AAA", "BBB", {"lap_a": 9}, "AAA has no lap 9.*laps with telemetry: 2, 3, 4, 6"),
        ("AAA", "BBB", {"lap_a": 5}, "AAA lap 5 has no usable telemetry"),
        ("AAA", "AAA", {}, "Both sides are AAA lap 2"),
    ],
)
def test_input_errors_are_helpful(
    root: Path, driver_a: str, driver_b: str, laps: dict, message: str
) -> None:
    with pytest.raises(ToolInputError, match=message):
        compare_laps("test", driver_a, driver_b, root=root, **laps)


def test_lap_time_trace_keeps_official_ends_and_ignores_clock_jitter() -> None:
    trace_kph = speed(180.0)
    trace_kph[300:400] = 120.0
    truth, lap_time = lap_clock(trace_kph, LENGTH_M)
    jittery = truth + np.random.default_rng(0).normal(0.0, 0.05, N)
    # A speed sensor reading 2% high integrates to a lap 2% short; scaling removes it.
    result = lap_time_trace(jittery, trace_kph * 1.02, lap_time, LENGTH_M, GRID_STEP_M)
    assert result[0] == 0.0
    assert result[-1] + (result[-1] - result[-2]) == pytest.approx(lap_time, abs=1e-3)
    assert np.abs(result - truth).max() < 0.03


def test_braking_points_tell_flat_from_no_braking_of_its_own() -> None:
    d = np.arange(0.0, 2000.0, GRID_STEP_M)
    apex = np.array([900.0, 960.0, 1600.0])  # a T1/T2 chicane, then T3
    bounds = np.concatenate([[0.0], (apex[:-1] + apex[1:]) / 2, [2000.0]])
    brake = np.where((d >= 800) & (d <= 910), 1.0, 0.0)  # one braking zone for the chicane
    brake[(d >= 1240) & (d <= 1250)] = 1.0  # a dab after T2's apex, on the way to T3
    throttle = np.where(brake > 0, 0.0, 100.0)
    throttle[(d > 910) & (d < 1000)] = 50.0  # part throttle through T2
    kph = np.interp(d, [0, 800, 900, 1000, 1500, 1600, 2000], [300, 300, 100, 100, 250, 120, 280])
    grid = {"brake": brake, "throttle": throttle, "speed": kph}
    assert braking_points(grid, d, apex, bounds) == [
        ("braked", 800.0),
        ("none", None),  # not "flat": the car was still slow from T1
        ("braked", 1240.0),
    ]
    flat = grid | {"throttle": np.where(brake > 0, 0.0, 100.0)}
    assert braking_points(flat, d, apex, bounds)[1] == ("flat", None)
    released = brake.copy()
    released[(d > 850) & (d < 870)] = 0.0  # a brief release while still slowing hard
    assert braking_points(grid | {"brake": released}, d, apex, bounds)[:2] == [
        ("braked", 800.0),
        ("none", None),
    ]
    dab = brake.copy()
    dab[(d >= 935) & (d <= 945)] = 1.0  # a touch of the brakes for T2, at constant speed
    assert braking_points(grid | {"brake": dab}, d, apex, bounds)[:2] == [
        ("braked", 800.0),
        ("braked", 935.0),
    ]

    frozen_brake = brake.copy()
    frozen_brake[(d >= 700) & (d < 800)] = np.nan  # the zone may have begun earlier
    frozen_brake[(d >= 1300) & (d < 1400)] = np.nan  # T3 might have had a second zone
    frozen_grid = grid | {"brake": frozen_brake}
    states = [state for state, _ in braking_points(frozen_grid, d, apex, bounds)]
    assert states == ["unknown", "none", "unknown"]


def test_top_speed_is_unknown_when_a_freeze_could_hide_it() -> None:
    nan = np.nan
    assert top_speed(np.array([200.0, 300.0, 310.0, 150.0, nan, nan, 120.0])) == 310.0
    assert top_speed(np.array([200.0, 300.0, 310.0, 305.0, nan, nan, 120.0])) is None
    assert top_speed(np.full(5, nan)) is None
