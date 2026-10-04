import numpy as np
from synthetic import LENGTH_M, RADIUS_M, circle_xy_dm

from race_engineer.features.reference import ReferenceLine


def make_reference() -> ReferenceLine:
    x, y = circle_xy_dm(np.arange(0, LENGTH_M, 20.0))  # coarse, like ~4 Hz position data
    return ReferenceLine.from_positions(x, y)


def test_length_matches_the_circle() -> None:
    assert abs(make_reference().length_m - LENGTH_M) / LENGTH_M < 0.005


def test_projection_gives_distance_along_the_line() -> None:
    ref = make_reference()
    x, y = circle_xy_dm(np.array([100.0, 1000.0, 2500.0]))
    assert np.allclose(ref.project(x, y).distance_m, [100, 1000, 2500], atol=3)


def test_offset_is_signed_left_positive() -> None:
    ref = make_reference()
    d = np.array([800.0])
    inside = ref.project(*circle_xy_dm(d, RADIUS_M - 8))  # counter-clockwise: inside is left
    outside = ref.project(*circle_xy_dm(d, RADIUS_M + 8))
    assert 6 < inside.offset_m[0] < 10
    assert -10 < outside.offset_m[0] < -6


def test_curvature_of_a_left_hand_circle() -> None:
    curvature = make_reference().curvature()
    assert np.allclose(np.median(curvature), 1 / RADIUS_M, rtol=0.05)


def test_a_crossing_is_resolved_by_the_expected_distance() -> None:
    # A figure of eight crossing at the origin: at theta = 0 and again halfway round.
    theta = np.linspace(0, 2 * np.pi, 4000, endpoint=False)
    ref = ReferenceLine(np.column_stack([500 * np.sin(theta), 500 * np.sin(theta) * np.cos(theta)]))
    x, y = np.array([2.0]), np.array([1.0])  # decimetres: 22 cm from the crossing
    assert ref.project(x, y).distance_m[0] < 5 or ref.project(x, y).distance_m[0] > ref.length_m - 5
    second = ref.project(x, y, expected_m=np.array([ref.length_m / 2 - 50])).distance_m[0]
    assert abs(second - ref.length_m / 2) < 5
    first = ref.project(x, y, expected_m=np.array([40.0])).distance_m[0]
    assert first < 5 or first > ref.length_m - 5


def test_a_rough_expected_distance_never_shifts_a_match_along_its_branch() -> None:
    # On a plain circle there is no other branch, so however far off the expectation is, the
    # match stays where the point is.
    ref = make_reference()
    x, y = circle_xy_dm(np.array([1000.0]))
    for off in (290.0, 305.0, 315.0, 325.0, 400.0):
        got = ref.project(x, y, expected_m=np.array([1000.0 + off])).distance_m[0]
        assert abs(got - 1000) < 3, off
