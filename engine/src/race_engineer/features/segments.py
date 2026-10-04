"""Cut fixed-length corner segments out of the lap grids.

Each segment runs from WINDOW_BEFORE_M before a corner's apex to WINDOW_AFTER_M after it
(80 points on the 5 m grid), so every segment has the same physical scale: braking,
turn-in, apex and exit. A window that crosses the start/finish line borrows the end of the
previous lap or the start of the next one, when that lap has a grid.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from race_engineer.config import WINDOW_AFTER_M, WINDOW_BEFORE_M

SEGMENT_CHANNELS = ("time_s", "speed", "throttle", "brake", "gear", "rpm", "offset_m")


@dataclass(frozen=True)
class Corner:
    number: int
    letter: str
    apex_m: float


@dataclass(frozen=True)
class Segment:
    lap_number: int
    corner: Corner
    start_m: float  # window start relative to the lap's start line (negative = previous lap)
    channels: dict[str, np.ndarray]  # time_s is relative to the window start
    segment_time_s: float
    stitched: str  # "", "previous" or "next"


def window_indices(apex_m: float, step_m: float) -> np.ndarray:
    centre = round(apex_m / step_m)
    return np.arange(
        centre - round(WINDOW_BEFORE_M / step_m), centre + round(WINDOW_AFTER_M / step_m)
    )


def cut_segments(
    lap_grids: dict[int, dict[str, np.ndarray]],
    lap_times_s: dict[int, float],
    corners: list[Corner],
    step_m: float,
) -> tuple[list[Segment], int, int]:
    """Segments for one driver's laps, plus windows skipped for a missing neighbour lap and
    for touching invalid data: a frozen feed or a data gap (grids without a `valid` channel count
    as all valid)."""
    segments: list[Segment] = []
    skipped = invalid = 0
    names = (
        (*SEGMENT_CHANNELS, "valid")
        if all("valid" in g for g in lap_grids.values())
        else SEGMENT_CHANNELS
    )
    for lap, grid in lap_grids.items():
        n = len(grid["speed"])
        for corner in corners:
            idx = window_indices(corner.apex_m, step_m)
            if idx[0] < 0:
                neighbour, stitched = lap - 1, "previous"
            elif idx[-1] >= n:
                neighbour, stitched = lap + 1, "next"
            else:
                neighbour, stitched = None, ""
            if neighbour is not None and neighbour not in lap_grids:
                skipped += 1
                continue

            channels = {}
            for name in names:
                values = grid[name]
                if stitched == "previous":
                    other = lap_grids[lap - 1][name]
                    if name == "time_s":
                        other = other - lap_times_s[lap - 1]
                    arr = np.concatenate([other[idx[idx < 0] + n], values[idx[idx >= 0]]])
                elif stitched == "next":
                    other = lap_grids[lap + 1][name]
                    if name == "time_s":
                        other = other + lap_times_s[lap]
                    arr = np.concatenate([values[idx[idx < n]], other[idx[idx >= n] - n]])
                else:
                    arr = values[idx]
                channels[name] = arr.astype(np.float32)

            if "valid" in channels and channels.pop("valid").min() < 1:
                invalid += 1
                continue
            channels["time_s"] = channels["time_s"] - channels["time_s"][0]
            segments.append(
                Segment(
                    lap_number=lap,
                    corner=corner,
                    start_m=float(idx[0] * step_m),
                    channels=channels,
                    segment_time_s=float(channels["time_s"][-1]),
                    stitched=stitched,
                )
            )
    return segments, skipped, invalid
