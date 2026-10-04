from itertools import pairwise

from race_engineer.mcp_server.sample_data import (
    CORNERS,
    GRID_STEP_M,
    TOP_SPEED_KPH,
    TRACK_LENGTH_M,
    build_sample_lap,
)


def test_channels_share_the_distance_grid() -> None:
    lap = build_sample_lap()
    n = TRACK_LENGTH_M // GRID_STEP_M
    assert len(lap.distance_m) == n
    assert len(lap.speed_kph) == len(lap.throttle_pct) == len(lap.brake) == len(lap.gear) == n
    assert all(b - a == GRID_STEP_M for a, b in pairwise(lap.distance_m))


def test_values_are_physically_plausible() -> None:
    lap = build_sample_lap()
    assert min(lap.speed_kph) > 60
    assert max(lap.speed_kph) <= TOP_SPEED_KPH + 1
    assert all(0 <= t <= 100 for t in lap.throttle_pct)
    assert all(1 <= g <= 8 for g in lap.gear)
    # Never on the throttle while braking.
    assert all(t == 0 for t, b in zip(lap.throttle_pct, lap.brake, strict=True) if b)


def test_every_corner_has_a_braking_zone() -> None:
    lap = build_sample_lap()
    zone_starts = sum(1 for i, b in enumerate(lap.brake) if b and not lap.brake[i - 1])
    assert zone_starts == len(CORNERS)


def test_is_deterministic() -> None:
    assert build_sample_lap(seed=3) == build_sample_lap(seed=3)
