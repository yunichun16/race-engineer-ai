import numpy as np
from synthetic import LENGTH_M, circle_xy_dm

from race_engineer.data.build import detect_corners
from race_engineer.features.grid import make_grid
from race_engineer.features.reference import ReferenceLine


def test_corners_are_found_at_the_speed_minima() -> None:
    reference = ReferenceLine.from_positions(*circle_xy_dm(np.arange(0, LENGTH_M, 20.0)))
    grid = make_grid(reference.length_m, 5.0)
    speed = np.full(len(grid), 300.0)
    for apex, low in [(500.0, 100.0), (1500.0, 150.0), (2500.0, 200.0)]:
        speed -= (300.0 - low) * np.exp(-(((grid - apex) / 60.0) ** 2))
    speed += np.random.default_rng(0).normal(0, 1.0, len(grid))  # sensor noise, not corners

    corners = detect_corners(speed, grid, reference)

    assert corners["number"].tolist() == [1, 2, 3]
    assert np.allclose(corners["apex_m"], [500, 1500, 2500], atol=15)
