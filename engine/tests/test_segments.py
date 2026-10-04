import numpy as np

from race_engineer.features.segments import SEGMENT_CHANNELS, Corner, cut_segments

STEP = 5.0
N = 600  # a 3 km lap
LAP_TIME = 60.0


def lap_grid(lap: int) -> dict[str, np.ndarray]:
    distance = np.arange(N) * STEP
    grid = {c: np.full(N, float(lap)) for c in SEGMENT_CHANNELS}
    grid["time_s"] = distance / (N * STEP) * LAP_TIME  # constant speed, 0 at the line
    return grid


def test_window_in_the_middle_of_the_lap() -> None:
    segs, skipped, _ = cut_segments({1: lap_grid(1)}, {1: LAP_TIME}, [Corner(3, "", 1500.0)], STEP)
    assert skipped == 0 and len(segs) == 1
    seg = segs[0]
    assert seg.stitched == ""
    assert all(len(v) == 80 for v in seg.channels.values())
    assert seg.start_m == 1250.0
    assert np.isclose(seg.segment_time_s, 79 * STEP / (N * STEP) * LAP_TIME)


def test_window_before_the_line_borrows_the_previous_lap() -> None:
    grids = {1: lap_grid(1), 2: lap_grid(2)}
    segs, _, _ = cut_segments(grids, {1: LAP_TIME, 2: LAP_TIME}, [Corner(1, "", 100.0)], STEP)
    lap2 = next(s for s in segs if s.lap_number == 2)
    assert lap2.stitched == "previous"
    # 150 m from lap 1 (marked 1.0), then lap 2 (marked 2.0); time stays continuous.
    assert np.all(lap2.channels["speed"][:30] == 1.0)
    assert np.all(lap2.channels["speed"][30:] == 2.0)
    assert np.all(np.diff(lap2.channels["time_s"]) > 0)


def test_window_without_its_neighbour_lap_is_skipped() -> None:
    segs, skipped, _ = cut_segments(
        {5: lap_grid(5)}, {5: LAP_TIME}, [Corner(1, "", 100.0), Corner(9, "", 2950.0)], STEP
    )
    assert segs == [] and skipped == 2


def test_windows_touching_frozen_data_are_dropped() -> None:
    grid = lap_grid(1) | {"valid": np.ones(N)}
    grid["valid"][300:305] = 0  # frozen around 1500-1525 m
    corners = [Corner(3, "", 1500.0), Corner(5, "", 2500.0)]
    segs, skipped, frozen = cut_segments({1: grid}, {1: LAP_TIME}, corners, STEP)
    assert [s.corner.number for s in segs] == [5]
    assert (skipped, frozen) == (0, 1)
    assert "valid" not in segs[0].channels
