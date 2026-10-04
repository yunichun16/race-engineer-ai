import numpy as np

from race_engineer.features.handcrafted import DISTANCE, FEATURES, N_POINTS, corner_features


def synthetic_corner(brake_from: float = -120.0, v_min: float = 90.0, dip: bool = False) -> dict:
    """One corner: full throttle, brake from `brake_from` m to the apex, then accelerate."""
    d = DISTANCE
    speed = np.where(d < brake_from, 300.0, 0.0)
    braking = (d >= brake_from) & (d < 0)
    speed[braking] = 300 - (300 - v_min) * (d[braking] - brake_from) / (0 - brake_from)
    speed[d >= 0] = v_min + 0.9 * d[d >= 0]
    throttle = np.where(braking, 0.0, 100.0)
    throttle[(d >= 0) & (d < 30)] = 50.0  # rolling on the throttle out of the apex
    if dip:
        throttle[(d >= 60) & (d < 75)] = 40.0  # a lift mid-exit
    time_s = np.concatenate([[0.0], np.cumsum(5.0 / np.maximum(speed[:-1] / 3.6, 1.0))])
    return {
        "speed": speed[None],
        "throttle": throttle[None],
        "brake": braking.astype(float)[None],
        "gear": np.where(speed > 200, 8.0, 3.0)[None],
        "offset_m": np.zeros((1, N_POINTS)),
        "time_s": time_s[None],
    }


def test_features_read_the_trace() -> None:
    f = corner_features(synthetic_corner())
    assert set(f) == set(FEATURES)
    assert f["brake_start_d"][0] == -120
    assert f["brake_len_m"][0] == 120
    assert np.isclose(f["v_min"][0], 90) and f["d_vmin"][0] == 0
    assert f["throttle_pickup_d"][0] == 30  # back to full throttle 30 m after the apex
    assert f["throttle_dip"][0] == 0
    assert f["segment_time_s"][0] > f["time_to_apex_s"][0] > 0


def test_later_braking_and_a_mid_exit_lift_are_visible() -> None:
    early, late = corner_features(synthetic_corner(-150)), corner_features(synthetic_corner(-80))
    assert late["brake_start_d"][0] > early["brake_start_d"][0]
    assert corner_features(synthetic_corner(dip=True))["throttle_dip"][0] == 60


def test_braking_for_an_earlier_corner_is_ignored() -> None:
    corner = synthetic_corner(-100)
    corner["brake"][0, 2:6] = 1.0  # a brake application for the previous corner
    assert corner_features(corner)["brake_start_d"][0] == -100
