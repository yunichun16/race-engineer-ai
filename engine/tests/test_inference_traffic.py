"""Traffic geometry on small synthetic sessions: cars on a 5 km track at known speeds.

A driver laps at 50 m/s (a 100 s lap). The other cars are placed so that the gaps are known:
a faster car a lap ahead that catches and passes them, a car in the pit lane, a lap without a
grid placed from its sector times, a corner near the line whose window reaches into the lap
before.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from race_engineer.inference import traffic
from race_engineer.inference.traffic import SessionTracks, lap_deficit, surroundings

LENGTH_M = 5000.0
GRID_M = np.arange(0.0, LENGTH_M - 2.5, 5.0)
SECTOR_ENDS_M = (1665.0, 3330.0)


def speeds(base: float, *slow: tuple[float, float, float]) -> np.ndarray:
    """m/s at each grid point: `base`, and (from_m, to_m, m/s) stretches at another speed."""
    v = np.full(len(GRID_M), float(base))
    for start, end, value in slow:
        v[(start <= GRID_M) & (end > GRID_M)] = value
    return v


def lap_clock(v: np.ndarray) -> tuple[np.ndarray, float]:
    """Time since the lap started at each grid point, and the lap time."""
    t = np.concatenate([[0.0], np.cumsum(5.0 / ((v[1:] + v[:-1]) / 2))])
    return t, float(t[-1] + 5.0 / v[-1])


@dataclass
class Lap:
    driver: str
    lap: int
    start_s: float
    v: np.ndarray  # m/s at each grid point
    lap_class: str = "race"
    pit_in: bool = False
    pit_out: bool = False
    grid: bool = True  # False: no lap grid (placed from its sector times)
    valid: np.ndarray | None = None
    reported_time_s: float | None = None  # the laps table's lap time, when not the true one
    offset: np.ndarray | None = None  # m off the reference line (0: on it)
    clock_shift: tuple[float, float, float] | None = None  # (from_m, to_m, s) added to the clock


def laps_of(driver: str, first: int, n: int, start_s: float, v: float, **kw) -> list[Lap]:
    """`n` laps at a constant speed from lap `first`, starting at `start_s`."""
    duration = lap_clock(np.full(len(GRID_M), v))[1]
    return [Lap(driver, first + i, start_s + i * duration, np.full(len(GRID_M), v), **kw)
            for i in range(n)]  # fmt: skip


def make_tracks(laps: list[Lap], code: str = "R") -> SessionTracks:
    """The session's tracks, as traffic.load_tracks builds them from the processed tables."""
    rows, grids, clocks, valids, kph, offsets = [], [], [], [], [], []
    for lap in laps:
        t, lap_time = lap_clock(lap.v)
        if lap.clock_shift is not None:  # a feed fault: the grid clock is off on a stretch
            start, end, shift = lap.clock_shift
            t = t + np.where((start <= GRID_M) & (end > GRID_M), shift, 0.0)
        s1, s2 = np.interp(SECTOR_ENDS_M, GRID_M, t)
        rows.append(
            {
                "driver": lap.driver,
                "lap_number": lap.lap,
                "lap_class": lap.lap_class,
                "lap_start_s": lap.start_s,
                "lap_time_s": (
                    lap.reported_time_s
                    if lap.reported_time_s is not None
                    else np.nan
                    if lap.pit_out and not lap.grid
                    else lap_time
                ),
                "sector1_s": np.nan if lap.pit_out else s1,
                "sector2_s": s2 - s1,
                "sector3_s": lap_time - s2,
                "pit_in": lap.pit_in,
                "pit_out": lap.pit_out,
                "track_status": "1",
                "session": code,
            }
        )
        if lap.grid:
            grids.append({"driver": lap.driver, "lap_number": lap.lap})
            clocks.append(t)
            valids.append(np.ones(len(GRID_M)) if lap.valid is None else lap.valid)
            kph.append(lap.v * 3.6)
            offsets.append(np.zeros(len(GRID_M)) if lap.offset is None else lap.offset)
    grid = pd.DataFrame(grids)
    arrays = (np.array(clocks), np.array(valids), np.array(kph))
    return traffic.session_tracks(
        pd.DataFrame(rows), grid, *arrays, LENGTH_M, offset=np.array(offsets)
    )


def lapped_laps(
    gap_s: float = 1.0,
    ahead: int = 1,
    slow: tuple[float, float, float] = (2600.0, 3200.0, 25.0),
    v_other: float = 55.0,
) -> list[Lap]:
    """AAA laps at 50 m/s but slows to `slow` (from_m, to_m, m/s; by default a lift to 25 m/s
    from 2600 m to 3200 m) on lap 9; BBB, at `v_other` m/s and `ahead` laps ahead, is `gap_s`
    behind on the road at 2550 m of that lap and goes past."""
    aaa = laps_of("AAA", 1, 8, 0.0, 50.0)
    aaa.append(Lap("AAA", 9, 800.0, speeds(50.0, slow)))
    aaa += laps_of("AAA", 10, 3, 800.0 + lap_clock(aaa[-1].v)[1], 50.0)
    meets = 800.0 + 2550.0 / 50.0 + gap_s - 2550.0 / v_other  # BBB's lap 9 + ahead starts here
    duration = lap_clock(np.full(len(GRID_M), v_other))[1]
    return aaa + laps_of("BBB", 1, 13, meets - (8 + ahead) * duration, v_other)


def lapped_session(gap_s: float = 1.0, ahead: int = 1, **kw) -> SessionTracks:
    return make_tracks(lapped_laps(gap_s, ahead, **kw))


def test_a_car_lapping_the_driver() -> None:
    around = surroundings(lapped_session(1.0), "AAA", 9, 3000.0)
    assert around is not None and around.start_m == -450.0 and around.end_m == 445.0
    (bbb,) = around.encounters
    assert bbb.driver == "BBB" and bbb.laps_ahead == 1 and bbb.passed == "by"
    # 1 s behind at 2550 m, i.e. the start of the extended window (450 m before the apex).
    assert bbb.gap_start_s == pytest.approx(1.0, abs=0.02)
    assert -400 < bbb.pass_m < -300  # it caught the lifting driver 60-70 m after 2600 m
    # It passed before the window and pulled away: ahead all through it, but not close.
    assert bbb.gap_window_s < 0 and bbb.behind_s > traffic.NEAR_S / 4


def test_the_window_wraps_across_the_line() -> None:
    # A corner 300 m after the line: its extended window starts 150 m before the line, on
    # lap 8, where BBB was behind on the road by as much as at the line less what it gains.
    tracks = lapped_session(1.0)
    around = surroundings(tracks, "AAA", 9, 300.0)
    assert around is not None and around.start_m == -450.0
    bbb = next(e for e in around.encounters if e.driver == "BBB")
    at_line = tracks.cars["BBB"].time_at(np.array([9 * LENGTH_M]))[0]
    at_line -= tracks.cars["AAA"].time_at(np.array([8 * LENGTH_M]))[0]
    gained = 150.0 / 50.0 - 150.0 / 55.0  # from 150 m before the line to it
    assert bbb.gap_start_s == pytest.approx(at_line + gained, abs=0.02)
    # Without a grid for the lap before, the window starts at the line.
    without = make_tracks([x for x in lapped_laps(1.0) if (x.driver, x.lap) != ("AAA", 8)])
    clipped = surroundings(without, "AAA", 9, 300.0)
    assert clipped is not None and clipped.start_m == -300.0


def test_passing_a_car_in_the_pit_lane_is_not_a_pass() -> None:
    # CCC comes in at the end of its lap 5 and leaves at the start of lap 6, at 20 m/s (72
    # km/h) in the pit lane from 4600 m to 400 m; AAA goes by meanwhile on the track.
    aaa = laps_of("AAA", 1, 8, 0.0, 50.0)
    in_lap = speeds(50.0, (4600.0, LENGTH_M, 20.0))
    out_lap = speeds(50.0, (0.0, 400.0, 20.0))
    ccc = laps_of("CCC", 1, 4, 20.0, 50.0)
    ccc.append(Lap("CCC", 5, 420.0, in_lap, "in", pit_in=True))
    t5 = 420.0 + lap_clock(in_lap)[1] + 20.0  # 20 s stationary in the pit box
    ccc.append(Lap("CCC", 6, t5, out_lap, "out", pit_out=True))
    tracks = make_tracks(aaa + ccc)
    car = tracks.cars["CCC"]
    in_pits = car.time_at(np.array([4 * LENGTH_M + 4700.0, 5 * LENGTH_M + 200.0]))
    assert np.isnan(in_pits).all()  # no position in the pit lane (and the lanes around it)
    assert np.isfinite(car.time_at(np.array([4 * LENGTH_M + 4000.0, 5 * LENGTH_M + 900.0]))).all()
    # AAA passes CCC's pit stop on lap 6 (CCC was ahead before the pits and behind after).
    around = surroundings(tracks, "AAA", 6, 100.0)
    assert around is not None
    assert all(e.passed == "" for e in around.encounters if e.driver == "CCC")


def test_a_lap_without_a_grid_is_placed_from_its_sector_times() -> None:
    # DDD's lap 3 has no grid; its sector times put it where it was, only approximately.
    ddd = laps_of("DDD", 1, 6, 5.0, 50.0)
    ddd[2] = Lap("DDD", 3, ddd[2].start_s, np.full(len(GRID_M), 50.0), grid=False)
    tracks = make_tracks(laps_of("AAA", 1, 6, 0.0, 50.0) + ddd)
    car = tracks.cars["DDD"]
    points = 2 * LENGTH_M + np.array([100.0, 2000.0, 4900.0])
    assert car.time_at(points) == pytest.approx(ddd[2].start_s + points % LENGTH_M / 50.0, abs=0.05)
    around = surroundings(tracks, "AAA", 3, 2500.0)
    assert around is not None
    (e,) = around.encounters
    assert e.approximate and e.gap_start_s == pytest.approx(5.0, abs=0.05)
    # A clean racing lap is placed well enough for the traffic rules: not rough.
    assert not e.rough


def test_an_out_lap_ends_where_the_next_lap_starts() -> None:
    # GGG's lap 2 is an out-lap without a grid whose reported lap time is 6 s too long (as
    # FastF1's can be, counted from the garage): it ends where lap 3 starts, not 6 s later.
    ggg = laps_of("GGG", 1, 4, -10.0, 50.0)  # 2 s behind AAA at 4000 m of lap 2
    out = speeds(50.0, (0.0, 400.0, 20.0))
    true_time = lap_clock(out)[1]
    ggg[1] = Lap("GGG", 2, ggg[1].start_s, out, "out", pit_out=True, grid=False,
                 reported_time_s=true_time + 6.0)  # fmt: skip
    for i, lap in enumerate(ggg[2:], start=2):
        ggg[i] = Lap("GGG", lap.lap, ggg[1].start_s + true_time + (i - 2) * 100.0, lap.v)
    tracks = make_tracks(laps_of("AAA", 1, 6, 0.0, 50.0) + ggg)
    car = tracks.cars["GGG"]
    point = np.array([LENGTH_M + 4000.0])  # in its last sector
    true_at = ggg[1].start_s + float(np.interp(4000.0, GRID_M, lap_clock(out)[0]))
    assert car.time_at(point)[0] == pytest.approx(true_at, abs=0.3)
    # An out-lap placed from its sector times is too rough for the traffic rules.
    around = surroundings(tracks, "AAA", 2, 4000.0)
    assert around is not None
    (e,) = around.encounters
    assert e.driver == "GGG" and e.approximate and e.rough


def test_feed_failures_short_holes_are_bridged_long_ones_are_not() -> None:
    valid = np.ones(len(GRID_M))
    valid[(GRID_M >= 1000) & (GRID_M < 1080)] = 0  # 80 m
    valid[(GRID_M >= 3000) & (GRID_M < 3500)] = 0  # 500 m
    eee = laps_of("EEE", 1, 3, 0.0, 50.0)
    eee[1] = Lap("EEE", 2, eee[1].start_s, eee[1].v, valid=valid)
    car = make_tracks(eee).cars["EEE"]
    assert np.isfinite(car.time_at(np.array([LENGTH_M + 1040.0])))[0]
    assert np.isnan(car.time_at(np.array([LENGTH_M + 3250.0])))[0]


def test_a_retired_car_has_no_position_after_its_last_lap() -> None:
    tracks = make_tracks(laps_of("AAA", 1, 8, 0.0, 50.0) + laps_of("FFF", 1, 3, 0.5, 50.0))
    assert np.isnan(tracks.cars["FFF"].time_at(np.array([5 * LENGTH_M])))[0]
    around = surroundings(tracks, "AAA", 6, 2500.0)
    assert around is not None and around.encounters == []


def test_lap_deficit_against_the_usual_push_lap() -> None:
    laps = laps_of("AAA", 1, 4, 0.0, 50.0, lap_class="push")
    laps[2] = Lap("AAA", 3, laps[2].start_s, speeds(45.0), lap_class="push")
    tracks = make_tracks(laps, code="Q")
    deficit, usual = lap_deficit(tracks, "AAA", 3, 2750.0)
    assert usual == pytest.approx(55.0, abs=0.01)
    assert deficit == pytest.approx(2750.0 / 45.0 - 55.0, abs=0.01)
    assert np.isnan(lap_deficit(tracks, "AAA", 9, 2750.0)[0])  # no such lap


def test_the_start_gap_is_where_both_cars_first_have_a_position() -> None:
    # A corner at 700 m: its extended window starts at 250 m, where CCC (a lap ahead, leaving
    # the pit lane, at the limit up to 150 m and in the exit lane for PIT_MARGIN_M after) has
    # no position yet. Its start gap is the first one it has before the corner's own window,
    # rather than none.
    laps = laps_of("AAA", 1, 12, 0.0, 50.0)
    out = speeds(55.0, (0.0, 150.0, 20.0))
    t_out, _ = lap_clock(out)
    start = 800.0 + 700.0 / 50.0 + 0.5 - float(np.interp(700.0, GRID_M, t_out))
    ccc = laps_of("CCC", 1, 9, start - 9 * 100.0, 50.0)
    ccc.append(Lap("CCC", 10, start, out, "out", pit_out=True))
    around = surroundings(make_tracks(laps + ccc), "AAA", 9, 700.0)
    assert around is not None
    (e,) = around.encounters
    assert e.driver == "CCC" and e.laps_ahead == 1 and around.start_m == -450.0
    assert e.gap_start_s == pytest.approx(0.5 + (700.0 - 305.0) * (1 / 50 - 1 / 55), abs=0.05)
    assert -400.0 <= e.gap_start_m <= -390.0  # from 305 m on


def test_the_gap_when_the_loss_began() -> None:
    # BBB (a lap ahead) closes on AAA, which slows to 44 m/s from 2600 m: 2.5 s behind 450 m
    # before the 3000 m apex, it is within about 1 s when a loss beginning 95 m before the apex
    # starts, and passes after it.
    tracks = lapped_session(2.5, slow=(2600.0, 3200.0, 44.0))
    around = surroundings(tracks, "AAA", 9, 3000.0, onset_m=-95.0)
    assert around is not None and around.onset_m == -95.0
    (bbb,) = around.encounters
    assert bbb.gap_start_s == pytest.approx(2.5, abs=0.02) and bbb.gap_start_m == -450.0
    # The closest it came before the loss began: at the onset itself, as it was still closing.
    assert bbb.gap_onset_s == pytest.approx(1.0, abs=0.1) and bbb.gap_onset_m == -95.0
    assert bbb.gap_before_loss() == (bbb.gap_onset_s, bbb.gap_onset_m)
    assert bbb.passed == "by" and bbb.pass_m > -95.0 and not bbb.passed_before(-95.0)
    # Without an onset, the start gap stands in.
    plain = surroundings(tracks, "AAA", 9, 3000.0)
    assert plain is not None and np.isnan(plain.encounters[0].gap_onset_s)
    assert plain.encounters[0].gap_before_loss() == (bbb.gap_start_s, -450.0)


def test_a_car_in_the_pit_exit_lane_beside_the_track_has_no_position() -> None:
    # CCC leaves the pit lane at 20 m/s up to 400 m, but the exit lane runs beside the track
    # until 900 m, 15 m to one side and then, after crossing under it at 450 m, 20 m to the
    # other: no position there, though it is above the pit speed limit from 400 m.
    aaa = laps_of("AAA", 1, 8, 0.0, 50.0)
    out_lap = speeds(50.0, (0.0, 400.0, 20.0))
    lane = np.interp(GRID_M, [0.0, 400.0, 500.0, 895.0, 900.0], [-15.0, -15.0, 20.0, 20.0, 0.0])
    ccc = laps_of("CCC", 1, 5, 20.0, 50.0)
    ccc.append(Lap("CCC", 6, 540.0, out_lap, "out", pit_out=True, offset=lane))
    car = make_tracks(aaa + ccc).cars["CCC"]
    in_lane = car.time_at(5 * LENGTH_M + np.array([600.0, 850.0]))
    assert np.isnan(in_lane).all()
    assert np.isfinite(car.time_at(np.array([5 * LENGTH_M + 950.0])))[0]
    # In-laps likewise: the entry lane leaves the track 300 m before the limit line.
    in_lap = speeds(50.0, (4800.0, LENGTH_M, 20.0))
    entry = np.where(GRID_M >= 4500.0, -15.0, 0.0)
    ccc[-1] = Lap("CCC", 6, 540.0, in_lap, "in", pit_in=True, offset=entry)
    car = make_tracks(aaa + ccc).cars["CCC"]
    assert np.isnan(car.time_at(np.array([5 * LENGTH_M + 4550.0])))[0]
    assert np.isfinite(car.time_at(np.array([5 * LENGTH_M + 4450.0])))[0]


def test_a_stretch_the_clock_shifts_between_two_holes_is_dropped() -> None:
    # EEE's lap 2 clock runs 2.5 s late from 3000 m to 3600 m, between two feed holes, at
    # 50 m/s (180 km/h) on both sides of each: the stretch is out, the rest of the lap stays.
    valid = np.ones(len(GRID_M))
    valid[(GRID_M >= 2950) & (GRID_M < 3000)] = 0
    valid[(GRID_M >= 3600) & (GRID_M < 3650)] = 0
    eee = laps_of("EEE", 1, 3, 0.0, 50.0)
    eee[1] = Lap("EEE", 2, eee[1].start_s, eee[1].v, valid=valid,
                 clock_shift=(2950.0, 3600.0, 2.5))  # fmt: skip
    car = make_tracks(eee).cars["EEE"]
    assert np.isnan(car.time_at(LENGTH_M + np.array([3100.0, 3500.0]))).all()
    kept = car.time_at(LENGTH_M + np.array([2500.0, 4000.0]))
    assert kept == pytest.approx(eee[1].start_s + np.array([2500.0, 4000.0]) / 50.0, abs=0.01)
    # A lap whose clock steps at a hole that nothing undoes is left as it is: the shift could
    # as well be before the hole.
    single = valid.copy()
    single[(GRID_M >= 3600) & (GRID_M < 3650)] = 1
    eee[1] = Lap("EEE", 2, eee[1].start_s, eee[1].v, valid=single,
                 clock_shift=(2950.0, 3600.0, 2.5))  # fmt: skip
    assert np.isfinite(make_tracks(eee).cars["EEE"].time_at(np.array([LENGTH_M + 3100.0])))[0]
    # A speed channel frozen in the holes (stuck high in the first, low in the second) makes
    # the clock look shifted by the speed alone; the driver's usual lap says it isn't.
    frozen = speeds(50.0, (2950.0, 3000.0, 150.0), (3600.0, 3650.0, 30.0))
    eee[1] = Lap("EEE", 2, eee[1].start_s, eee[1].v, valid=valid)
    tracks = make_tracks(eee)
    kph = np.array([lap.v * 3.6 for lap in eee])
    kph[1] = frozen * 3.6
    clocks = np.array([lap_clock(lap.v)[0] for lap in eee])
    valids = np.array([np.ones(len(GRID_M)), valid, np.ones(len(GRID_M))])
    grids = pd.DataFrame({"driver": "EEE", "lap_number": [1, 2, 3]})
    laps = tracks.laps.reset_index()
    frozen_tracks = traffic.session_tracks(laps, grids, clocks, valids, kph, LENGTH_M)
    assert np.isfinite(
        frozen_tracks.cars["EEE"].time_at(LENGTH_M + np.array([3100.0, 3500.0]))
    ).all()


def test_the_chequered_flag_lap_is_not_a_retirement_lap() -> None:
    # A 10-lap race: AAA wins, BBB finishes a lap down (its lap 9 spans AAA's finish), CCC
    # retires on lap 6 and DDD's last lap has no lap time.
    laps = laps_of("AAA", 1, 10, 0.0, 50.0) + laps_of("BBB", 1, 9, 1.0, 45.0)
    laps += laps_of("CCC", 1, 6, 2.0, 50.0) + laps_of("DDD", 1, 10, 3.0, 50.0)
    tracks = make_tracks(laps)
    tracks.laps.loc[("DDD", 10), "lap_time_s"] = np.nan
    assert traffic.chequered_lap(tracks, "AAA") == 10
    assert traffic.chequered_lap(tracks, "BBB") == 9
    assert traffic.chequered_lap(tracks, "CCC") is None
    assert traffic.chequered_lap(tracks, "DDD") is None
