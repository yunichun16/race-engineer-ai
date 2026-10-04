"""Hand-crafted corner features: what a race engineer reads off a trace.

These are the baselines' inputs, and the yardstick the deep model has to beat. All distances
are metres relative to the apex (negative = before it), on the 80-point corner window.
"""

from __future__ import annotations

import numpy as np

from race_engineer.config import GRID_STEP_M, WINDOW_BEFORE_M

N_POINTS = 80
APEX_INDEX = round(WINDOW_BEFORE_M / GRID_STEP_M)  # 50
DISTANCE = np.arange(N_POINTS) * GRID_STEP_M - WINDOW_BEFORE_M

BRAKE_ON = 0.5  # the raw brake signal is on/off; >0.5 after interpolation means "braking"
THROTTLE_FULL = 80.0
THROTTLE_OFF = 20.0

FEATURES = (
    "v_start",  # km/h, 250 m before the apex
    "v_min",  # minimum speed in the window
    "d_vmin",  # where the minimum speed happens
    "v_apex",
    "v_exit",  # km/h, 150 m after the apex
    "brake_start_d",  # where braking starts, for the braking zone into this corner
    "brake_len_m",  # distance spent braking
    "decel_max",  # peak deceleration, m/s^2
    "throttle_off_d",  # first lift below 20% throttle
    "throttle_pickup_d",  # back above 80% throttle after the minimum speed
    "throttle_dip",  # largest throttle drop after the pickup starts (hesitation, traction)
    "coast_m",  # distance with neither throttle nor brake (lift and coast)
    "exit_accel",  # mean acceleration after the apex, m/s^2
    "gear_min",
    "offset_apex",  # distance from the reference line at the apex, m
    "offset_exit_max",  # largest distance from the line after the apex (running wide)
    "offset_range",
    "time_to_apex_s",
    "segment_time_s",  # time through the whole window: the outcome
)


def _first(mask: np.ndarray) -> np.ndarray:
    """Index of the first True per row, or -1."""
    idx = np.argmax(mask, axis=1)
    return np.where(mask.any(axis=1), idx, -1)


def _distance_at(idx: np.ndarray) -> np.ndarray:
    return np.where(idx >= 0, DISTANCE[np.clip(idx, 0, N_POINTS - 1)], np.nan)


def corner_features(channels: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Features for N segments; each channel is an (N, 80) array."""
    speed = channels["speed"].astype(float)
    throttle = channels["throttle"].astype(float)
    brake = channels["brake"].astype(float)
    gear = channels["gear"].astype(float)
    offset = channels["offset_m"].astype(float)
    time_s = channels["time_s"].astype(float)
    n = len(speed)
    idx = np.broadcast_to(np.arange(N_POINTS), (n, N_POINTS))
    i_min = speed.argmin(axis=1)
    rows = np.arange(n)

    # Braking zone into this corner: the braking run that ends last before the minimum speed.
    braking = brake > BRAKE_ON
    run_start = braking & ~np.concatenate([np.zeros((n, 1), bool), braking[:, :-1]], axis=1)
    latest_start = np.maximum.accumulate(np.where(run_start, idx, -1), axis=1)
    last_brake = np.where(braking & (idx <= i_min[:, None]), idx, -1).max(axis=1)
    brake_start = np.where(last_brake >= 0, latest_start[rows, np.clip(last_brake, 0, None)], -1)

    dt = np.clip(np.diff(time_s, axis=1), 1e-3, None)
    accel = np.diff(speed / 3.6, axis=1) / dt  # (N, 79)

    after_min = idx >= i_min[:, None]
    pickup = _first((throttle >= THROTTLE_FULL) & after_min)
    running_max = np.maximum.accumulate(np.where(after_min, throttle, -np.inf), axis=1)
    dip = np.where(after_min, running_max - throttle, 0.0).max(axis=1)

    exit_offset = np.abs(offset[:, APEX_INDEX:]).max(axis=1)
    return {
        "v_start": speed[:, 0],
        "v_min": speed[rows, i_min],
        "d_vmin": DISTANCE[i_min],
        "v_apex": speed[:, APEX_INDEX],
        "v_exit": speed[:, -1],
        "brake_start_d": _distance_at(brake_start),
        "brake_len_m": braking.sum(axis=1) * GRID_STEP_M,
        "decel_max": np.clip(-accel.min(axis=1), 0, None),
        "throttle_off_d": _distance_at(_first((throttle < THROTTLE_OFF) & (idx <= i_min[:, None]))),
        "throttle_pickup_d": _distance_at(pickup),
        "throttle_dip": dip,
        "coast_m": ((throttle < THROTTLE_OFF) & ~braking).sum(axis=1) * GRID_STEP_M,
        "exit_accel": accel[:, APEX_INDEX:].mean(axis=1),
        "gear_min": gear.min(axis=1),
        "offset_apex": offset[:, APEX_INDEX],
        "offset_exit_max": exit_offset,
        "offset_range": offset.max(axis=1) - offset.min(axis=1),
        "time_to_apex_s": time_s[:, APEX_INDEX],
        "segment_time_s": time_s[:, -1],
    }
