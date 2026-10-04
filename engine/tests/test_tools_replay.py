"""The corner replay and the cars at the apex, on a synthetic race with cars at known gaps.

Every car laps a 5 km circle at 50 m/s (100 s laps). AAA is the driver. BBB is 2 s (100 m)
behind on the road, CCC 20 s behind, DDD a lap ahead and 1 s (50 m) behind on the road, EEE 3 s
(150 m) ahead with no telemetry on lap 3 (placed from its sector times), and FFF retired after
lap 2. The corner is at 2500 m, so on AAA's lap 3 the car is at the apex 250 s in.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from race_engineer.inference import traffic
from race_engineer.inference.traffic import SessionTracks
from race_engineer.tools import replay
from race_engineer.tools.replay import (
    REPLAY_BEFORE_M,
    REPLAY_DT_S,
    corner_replay,
    json_size,
    load_session_tracks,
    nearby_cars,
    runs_from_tracks,
)
from race_engineer.tools.synthetic import SyntheticLap, SyntheticSession, write_session
from race_engineer.tools.track_map import TrackMap, track_map

LENGTH_M = 5000.0
GRID_M = np.arange(0.0, LENGTH_M - 2.5, 5.0)
SPEED = 50.0  # m/s
LAP_S = LENGTH_M / SPEED
APEX_M = 2500.0


@dataclass
class Lap:
    driver: str
    lap: int
    start_s: float
    grid: bool = True  # False: no lap grid (placed from its sector times)
    pit_out: bool = False


def laps_of(driver: str, laps: range, offset_s: float, **kw) -> list[Lap]:
    """Laps at SPEED, lap k starting at (k - 1) * LAP_S + offset_s."""
    return [Lap(driver, k, (k - 1) * LAP_S + offset_s, **kw) for k in laps]


def make_tracks(
    laps: list[Lap], code: str = "R", holes: tuple[tuple[str, int, float, float], ...] = ()
) -> SessionTracks:
    """The session's tracks, as traffic.load_tracks builds them from the processed tables;
    `holes` are (driver, lap, from_m, to_m) stretches of grid points marked not valid."""
    t = GRID_M / SPEED
    s1, s2 = np.interp([1665.0, 3330.0], GRID_M, t)
    rows, grids = [], []
    for lap in laps:
        rows.append(
            {
                "driver": lap.driver, "lap_number": lap.lap, "lap_class": "race",
                "lap_start_s": lap.start_s, "lap_time_s": LAP_S, "sector1_s": s1,
                "sector2_s": s2 - s1, "sector3_s": LAP_S - s2, "pit_in": False,
                "pit_out": lap.pit_out, "track_status": "1", "session": code,
            }
        )  # fmt: skip
        if lap.grid:
            grids.append({"driver": lap.driver, "lap_number": lap.lap})
    n = len(grids)
    clocks, valid, kph = np.tile(t, (n, 1)), np.ones((n, len(t))), np.full((n, len(t)), 180.0)
    for driver, number, lo, hi in holes:
        row = grids.index({"driver": driver, "lap_number": number})
        valid[row, (lo <= GRID_M) & (hi >= GRID_M)] = 0
    return traffic.session_tracks(
        pd.DataFrame(rows), pd.DataFrame(grids), clocks, valid, kph, LENGTH_M, "2026_01_R"
    )


def race_laps() -> list[Lap]:
    eee = laps_of("EEE", range(1, 7), -3.0)
    eee[2].grid = False
    bbb = laps_of("BBB", range(1, 7), 2.0)
    bbb[2].pit_out = True
    return [
        *laps_of("AAA", range(1, 7), 0.0),
        *bbb,
        *laps_of("CCC", range(1, 7), 20.0),
        *laps_of("DDD", range(2, 8), 1.0 - LAP_S),  # its lap 2 starts 1 s after AAA's lap 1
        *eee,
        *laps_of("FFF", range(1, 3), 4.0),
    ]


@pytest.fixture(scope="module")
def tracks() -> SessionTracks:
    return make_tracks(race_laps())


@pytest.fixture(scope="module")
def track(tmp_path_factory: pytest.TempPathFactory) -> TrackMap:
    root = tmp_path_factory.mktemp("processed")
    write_session(SyntheticSession(2026, 1, "R", "Sample Grand Prix", "Synthetic Circuit"), root)
    found = track_map("2026_01_R", root)
    assert found is not None
    return found


def frame_count(duration_s: float) -> int:
    return math.floor(duration_s / REPLAY_DT_S + 1e-9) + 1


def test_runs_from_tracks(tracks: SessionTracks) -> None:
    runs = runs_from_tracks(tracks)
    assert set(runs) == {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF"}
    for run in runs.values():
        assert (np.diff(run.time) > 0).all() and np.isfinite(run.time).all()
    aaa = runs["AAA"]
    np.testing.assert_allclose(aaa.distance_at(np.array([0.0, 250.0])), [0.0, 12500.0])
    # FFF retired after lap 2: no position after that, nor before the session.
    fff = runs["FFF"]
    assert np.isnan(fff.distance_at(np.array([300.0, 1.0]))).all()
    assert runs["EEE"].approximate_at(np.array([12500.0]))[0]
    assert not runs["EEE"].approximate_at(np.array([7500.0]))[0]


def test_replay_frames_and_distance(tracks: SessionTracks, track: TrackMap) -> None:
    out = corner_replay(runs_from_tracks(tracks), "AAA", 3, APEX_M, None, track, race=True)
    assert out is not None
    duration = (REPLAY_BEFORE_M + replay.REPLAY_AFTER_M) / SPEED
    assert len(out["me"]) == len(out["dist"]) == frame_count(duration) == 71
    assert out["dt"] == REPLAY_DT_S
    dist = np.array(out["dist"])
    assert dist[0] == -REPLAY_BEFORE_M and (np.diff(dist) > 0).all()
    assert dist.min() < 0 < dist.max() and dist[-1] == pytest.approx(300.0, abs=0.2)
    # The car at each frame is where its distance from the apex puts it on the map.
    expected = track.pixels(2 * LENGTH_M + APEX_M + dist)
    np.testing.assert_allclose(np.array(out["me"]), expected, atol=0.06)
    assert "ghost" not in out
    x, y, w, h = out["box"]
    assert w == h and all(x <= p[0] <= x + w and y <= p[1] <= y + h for p in out["me"] if p)


def test_cars_within_300_m_only(tracks: SessionTracks, track: TrackMap) -> None:
    out = corner_replay(runs_from_tracks(tracks), "AAA", 3, APEX_M, None, track, race=True)
    assert out is not None
    cars = {c["driver"]: c for c in out["cars"]}
    assert list(cars) == ["BBB", "DDD", "EEE"]  # not CCC (1000 m back) nor FFF (retired)
    assert [cars[d]["laps"] for d in cars] == [0, 1, 0]
    assert [cars[d]["approx"] for d in cars] == [False, False, True]
    dist = np.array(out["dist"])
    behind = track.pixels(2 * LENGTH_M + APEX_M + dist - 100.0)
    np.testing.assert_allclose(np.array(cars["BBB"]["pts"]), behind, atol=0.06)
    qualifying = corner_replay(runs_from_tracks(tracks), "AAA", 3, APEX_M, None, track, False)
    assert qualifying is not None and {c["laps"] for c in qualifying["cars"]} == {0}


def test_the_closest_cars_within_the_budget(
    tracks: SessionTracks, track: TrackMap, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = runs_from_tracks(tracks)
    bare = corner_replay(runs, "AAA", 3, APEX_M, None, track, True, max_bytes=0)
    full = corner_replay(runs, "AAA", 3, APEX_M, None, track, True)
    assert bare is not None and full is not None and bare["cars"] == []
    ddd = next(c for c in full["cars"] if c["driver"] == "DDD")  # the closest, 50 m
    budget = json_size(bare) + json_size(ddd) + 1
    one = corner_replay(runs, "AAA", 3, APEX_M, None, track, True, max_bytes=budget)
    assert one is not None and [c["driver"] for c in one["cars"]] == ["DDD"]
    assert json_size(one) <= budget
    monkeypatch.setattr(replay, "REPLAY_MAX_CARS", 2)
    two = corner_replay(runs, "AAA", 3, APEX_M, None, track, True)
    assert two is not None and [c["driver"] for c in two["cars"]] == ["BBB", "DDD"]


def test_ghost_starts_level(tracks: SessionTracks, track: TrackMap) -> None:
    runs = runs_from_tracks(tracks)
    out = corner_replay(runs, "AAA", 3, APEX_M, ("AAA", 2), track, race=True)
    assert out is not None and out.get("ghost_label") == "usual: lap 2"
    # Same pace on lap 2: the ghost is on top of the car all the way through.
    np.testing.assert_allclose(np.array(out.get("ghost")), np.array(out["me"]), atol=1e-9)
    other = corner_replay(runs, "AAA", 3, APEX_M, ("BBB", 5), track, race=True)
    assert other is not None and other.get("ghost_label") == "usual: BBB lap 5"
    assert other.get("ghost", [])[0] == out["me"][0]  # level at the start
    beyond = corner_replay(runs, "AAA", 3, APEX_M, ("AAA", 7), track, race=True)
    assert beyond is not None and "ghost" not in beyond  # AAA has no lap 7


def test_no_replay_outside_the_run(tracks: SessionTracks, track: TrackMap) -> None:
    runs = runs_from_tracks(tracks)
    assert corner_replay(runs, "AAA", 1, 100.0, None, track, True) is None  # before the start
    assert corner_replay(runs, "AAA", 6, 4900.0, None, track, True) is None  # after the end
    # Past the end of the run but not of the analysed window: the replay stops at the end.
    short = corner_replay(runs, "AAA", 6, 4800.0, None, track, True)
    assert short is not None and 185.0 < short["dist"][-1] <= 195.0  # frames every 10 m
    assert corner_replay(runs, "ZZZ", 3, APEX_M, None, track, True) is None


def test_stretch_stops_at_a_feed_hole(track: TrackMap) -> None:
    # No position for AAA from 160 m to 330 m after the apex on lap 3 (longer than the grid
    # bridges): the replay stops where the hole starts instead of being dropped.
    laps = [*laps_of("AAA", range(1, 7), 0.0), *laps_of("BBB", range(1, 7), 1.0)]
    runs = runs_from_tracks(make_tracks(laps, holes=(("AAA", 3, APEX_M + 160, APEX_M + 330),)))
    out = corner_replay(runs, "AAA", 3, APEX_M, ("AAA", 2), track, race=True)
    assert out is not None and all(p is not None for p in out["me"])
    dist = np.array(out["dist"])
    assert dist[0] == -REPLAY_BEFORE_M and 145.0 <= dist[-1] < 160.0
    assert len(out["me"]) == frame_count((REPLAY_BEFORE_M + 155.0) / SPEED)
    assert len(out.get("ghost", [])) == len(out["me"])  # level with the car, as long
    # A hole across the end of the analysed window, or across its start: no replay.
    for lo, hi in ((APEX_M + 100, APEX_M + 330), (APEX_M - 420, APEX_M - 240)):
        holed = runs_from_tracks(make_tracks(laps, holes=(("AAA", 3, lo, hi),)))
        assert corner_replay(holed, "AAA", 3, APEX_M, None, track, race=True) is None
    # A hole inside the window leaves the car out of those frames only.
    inside = runs_from_tracks(make_tracks(laps, holes=(("AAA", 3, APEX_M - 100, APEX_M + 60),)))
    out = corner_replay(inside, "AAA", 3, APEX_M, None, track, race=True)
    assert out is not None and out["dist"][-1] == pytest.approx(300.0, abs=0.2)
    gone = [d for d, p in zip(out["dist"], out["me"], strict=True) if p is None]
    assert gone and min(gone) >= -100.0 and max(gone) <= 60.0


def test_no_position_in_a_missing_lap(track: TrackMap) -> None:
    laps = [*laps_of("AAA", range(1, 7), 0.0), *laps_of("GGG", range(1, 7), 1.0)]
    laps = [lap for lap in laps if (lap.driver, lap.lap) != ("GGG", 3)]  # no record of lap 3
    runs = runs_from_tracks(make_tracks(laps))
    out = corner_replay(runs, "AAA", 3, APEX_M, None, track, race=True)
    assert out is not None and out["cars"] == []  # GGG has no position on lap 3
    assert np.isnan(runs["GGG"].distance_at(np.array([250.0])))[0]
    assert np.isfinite(runs["GGG"].bridged_at(np.array([250.0])))[0]


def test_nearby_cars_at_the_apex(tracks: SessionTracks, track: TrackMap) -> None:
    cars = nearby_cars(tracks, "AAA", 3, APEX_M, track, race=True)
    assert [(c["driver"], c["gap_s"], c["laps"]) for c in cars] == [
        ("EEE", -3.0, 0),
        ("DDD", 1.0, 1),
        ("BBB", 2.0, 0),
    ]
    notes = {c["driver"]: c["note"] for c in cars}
    assert notes == {"EEE": "placed from sector times", "DDD": "", "BBB": "out-lap"}
    bbb = next(c for c in cars if c["driver"] == "BBB")
    expected = track.pixels(np.array([2 * LENGTH_M + APEX_M - 100.0]))[0]
    np.testing.assert_allclose([bbb["x"], bbb["y"]], expected, atol=0.06)
    assert {c["laps"] for c in nearby_cars(tracks, "AAA", 3, APEX_M, track, race=False)} == {0}
    assert nearby_cars(tracks, "AAA", 9, APEX_M, track, race=True) == []
    assert nearby_cars(tracks, "ZZZ", 3, APEX_M, track, race=True) == []


def test_session_tracks_are_cached(tmp_path: Path) -> None:
    speed = np.full(len(GRID_M), 180.0)
    laps = tuple(SyntheticLap("AAA", n, speed) for n in range(1, 4))
    write_session(SyntheticSession(2026, 2, "R", "Sample", "Synthetic", laps=laps), tmp_path)
    loaded = load_session_tracks("2026_02_R", tmp_path)
    assert loaded is not None and set(loaded.runs) == {"AAA"}
    assert load_session_tracks("2026_02_R", tmp_path) is loaded
    write_session(SyntheticSession(2026, 3, "R", "Sample", "Synthetic"), tmp_path)  # no laps
    assert load_session_tracks("2026_03_R", tmp_path) is None
