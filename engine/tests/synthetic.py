"""A synthetic circular track and car, for testing the pipeline without FastF1 downloads."""

from __future__ import annotations

import numpy as np

from race_engineer.features.grid import DriverTelemetry

RADIUS_M = 500.0
LENGTH_M = 2 * np.pi * RADIUS_M
SPEED_MPS = 50.0  # 180 km/h
LAP_TIME_S = LENGTH_M / SPEED_MPS
HZ = 4.0


def circle_xy_dm(
    distance_m: np.ndarray, radius_m: float = RADIUS_M
) -> tuple[np.ndarray, np.ndarray]:
    """Counter-clockwise circle positions in FastF1 units (decimetres), starting at angle 0."""
    theta = distance_m / RADIUS_M
    return radius_m * np.cos(theta) * 10, radius_m * np.sin(theta) * 10


def driver_telemetry(
    n_laps: int = 3,
    radius_m: float = RADIUS_M,
    pos_gap: tuple[float, float] | None = None,
    frozen: tuple[float, float] | None = None,
    pos_stall: tuple[float, float] | None = None,
    car_gap: tuple[float, float] | None = None,
    stuck_speed: tuple[float, float, float] | None = None,
) -> DriverTelemetry:
    """Constant-speed laps; lap k starts at session time k * LAP_TIME_S.

    RPM wobbles slightly, like real telemetry. Session-time windows simulate feed failures:
    `pos_gap` has no position samples, `pos_stall` repeats the last position (a stalled
    position feed), `frozen` repeats the last car sample, and `car_gap` has no car samples.
    `stuck_speed` = (start, end, km/h) holds the speed channel alone on a wrong value.
    """
    t = np.arange(-2.0, n_laps * LAP_TIME_S + 2.0, 1 / HZ)
    distance = t * SPEED_MPS
    x, y = circle_xy_dm(distance, radius_m)
    t_pos, keep = t, np.ones_like(t, dtype=bool)
    if pos_gap is not None:
        keep = (t < pos_gap[0]) | (t > pos_gap[1])
    if pos_stall is not None:
        stalled = np.flatnonzero((t >= pos_stall[0]) & (t <= pos_stall[1]))
        x[stalled], y[stalled] = x[stalled[0] - 1], y[stalled[0] - 1]
    n = len(t)
    speed = np.full(n, SPEED_MPS * 3.6)
    rpm = 11000.0 + np.round(40 * np.sin(np.arange(n)))
    if frozen is not None:
        stall = np.flatnonzero((t >= frozen[0]) & (t <= frozen[1]))
        speed[stall], rpm[stall] = speed[stall[0] - 1], rpm[stall[0] - 1]
    if stuck_speed is not None:
        speed[(t >= stuck_speed[0]) & (t <= stuck_speed[1])] = stuck_speed[2]
    car = np.ones(n, dtype=bool)
    if car_gap is not None:
        car = (t < car_gap[0]) | (t > car_gap[1])
    return DriverTelemetry(
        t_car=t[car],
        speed=speed[car],
        throttle=np.full(n, 100.0)[car],
        brake=np.zeros(n)[car],
        gear=np.full(n, 7.0)[car],
        rpm=rpm[car],
        drs=np.zeros(n)[car],
        t_pos=t_pos[keep],
        x=x[keep],
        y=y[keep],
    )
