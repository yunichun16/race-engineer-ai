"""Synthetic lap telemetry for the MCP Apps spike.

The spike proves the chart pipeline (Python tool -> structured result -> chart inside
Claude) before real data exists, so this module builds a plausible-looking lap from
simple physics instead of loading FastF1 data.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

TRACK_LENGTH_M = 5000
GRID_STEP_M = 5
TOP_SPEED_KPH = 330.0
BRAKING_MPS2 = 40.0  # roughly 4 g, typical peak braking for an F1 car
ACCELERATION_MPS2 = 11.0

# (turn number, apex distance in metres, apex speed in km/h)
CORNERS: tuple[tuple[int, int, float], ...] = (
    (1, 600, 95.0),
    (2, 1300, 160.0),
    (3, 1900, 240.0),
    (4, 2600, 85.0),
    (5, 3300, 130.0),
    (6, 4100, 200.0),
    (7, 4700, 110.0),
)

GEAR_THRESHOLDS_KPH = (0, 95, 130, 165, 200, 240, 275, 305)  # upshift points, gears 1-8


@dataclass(frozen=True)
class SampleLap:
    distance_m: list[float]
    speed_kph: list[float]
    throttle_pct: list[float]
    brake: list[bool]
    gear: list[int]


def _kph_to_mps(kph: float) -> float:
    return kph / 3.6


def _envelope_speed_kph(distance_m: float) -> float:
    """Fastest speed allowed at `distance_m` by the braking/acceleration limits of every corner."""
    limit_mps = _kph_to_mps(TOP_SPEED_KPH)
    for _, apex_m, apex_kph in CORNERS:
        apex_mps = _kph_to_mps(apex_kph)
        # Consider the corner on this lap and its neighbours so the lap wraps around.
        for offset in (-TRACK_LENGTH_M, 0, TRACK_LENGTH_M):
            gap = apex_m + offset - distance_m
            rate = BRAKING_MPS2 if gap >= 0 else ACCELERATION_MPS2
            limit_mps = min(limit_mps, math.sqrt(apex_mps**2 + 2 * rate * abs(gap)))
    return limit_mps * 3.6


def _gear_for(speed_kph: float) -> int:
    gears = enumerate(GEAR_THRESHOLDS_KPH, start=1)
    return max(gear for gear, threshold in gears if speed_kph >= threshold)


def build_sample_lap(seed: int = 7) -> SampleLap:
    """Build one deterministic synthetic lap on a 5 m distance grid."""
    rng = random.Random(seed)
    distance = [float(d) for d in range(0, TRACK_LENGTH_M, GRID_STEP_M)]
    speed = [_envelope_speed_kph(d) + rng.uniform(-0.6, 0.6) for d in distance]

    throttle: list[float] = []
    brake: list[bool] = []
    for i, v in enumerate(speed):
        change = speed[(i + 1) % len(speed)] - v
        braking = change < -1.5
        brake.append(braking)
        if braking:
            throttle.append(0.0)
        elif change > 0.4 or v >= TOP_SPEED_KPH - 1:
            throttle.append(100.0)
        else:  # rolling through the apex
            throttle.append(round(35 + rng.uniform(-10, 10), 1))

    return SampleLap(
        distance_m=distance,
        speed_kph=[round(v, 1) for v in speed],
        throttle_pct=throttle,
        brake=brake,
        gear=[_gear_for(v) for v in speed],
    )
