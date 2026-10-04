"""The maps and the replay in the find-mistakes and explain-corner charts.

`find_mistakes` and `explain_corner` (`tools.mistakes`) add these to their chart payloads:

- `session_track(key, root)`: find_mistakes' `track`, the session's map (`tools.track_map`), or
  None without one.
- `map_and_replay(chart, key, root, loaded)`: explain_corner's `map`, the map with the analysed
  stretch (`window`, every 5 m from 250 m before the apex to 145 m after), the apex and the cars
  around at the apex moment (`tools.replay.nearby_cars`), and `replay`, the corner as an
  animation (`tools.replay.corner_replay`); each None when it can't be built. Both come from
  `loaded`, the session's tracks that explain_corner also hands its traffic explanation
  (`tools.replay.load_session_tracks`), so the session's positions are read once and the
  picture matches what the explanation says about the cars around. The replay's ghost is the
  reference lap the chart names as `ghost`, and the replay leaves out its farthest cars when
  the whole payload would pass CHART_MAX_BYTES.

Neither ever fails the tool: the map is an extra, so a session it can't be built for gets
None, and an unexpected error is logged and gives None too.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, TypedDict

import numpy as np

from race_engineer.features.handcrafted import DISTANCE
from race_engineer.tools.replay import (
    RACE_SESSIONS,
    LoadedTracks,
    NearbyCar,
    Replay,
    corner_replay,
    json_size,
    nearby_cars,
)
from race_engineer.tools.track_map import TrackPayload, track_map

log = logging.getLogger(__name__)

# explain_corner's whole payload as compact JSON stays within this: the replay leaves out its
# farthest cars rather than overrun it (the payload budget is 30 KiB,
# registry.PAYLOAD_MAX_BYTES; the rest is margin).
CHART_MAX_BYTES = 28_000

Data = dict[str, Any]


class Point(TypedDict):
    x: float
    y: float


class CornerMap(TrackPayload):
    """`map` in the explain-corner chart (web/src/charts/explain-corner.ts)."""

    window: list[list[float]]  # px every 5 m of the analysed stretch
    apex: Point
    cars: list[NearbyCar]


def session_track(key: str, root: Path) -> TrackPayload | None:
    """find_mistakes' `track`: the session's map, None without one or when it can't be built
    (logged)."""
    try:
        track = track_map(key, root)
    except Exception:
        log.warning("No track map for %s", key, exc_info=True)
        return None
    return None if track is None else track.payload


def map_and_replay(
    chart: Data, key: str, root: Path, loaded: LoadedTracks | None
) -> tuple[CornerMap | None, Replay | None]:
    """explain_corner's `map` and `replay` (see the module docstring) for its chart payload
    `chart` (which needs `driver`, `lap_number`, `session_code`, `apex_m` and `ghost`), with the
    cars from `loaded` (None: a map without cars, and no replay)."""
    try:
        return _map_and_replay(chart, key, root, loaded)
    except Exception:
        log.warning("No corner map or replay for %s", chart.get("title"), exc_info=True)
        return None, None


def _map_and_replay(
    chart: Data, key: str, root: Path, loaded: LoadedTracks | None
) -> tuple[CornerMap | None, Replay | None]:
    track = track_map(key, root)
    apex_m = chart.get("apex_m")
    if track is None or apex_m is None or not math.isfinite(apex_m):
        return None, None
    driver, lap = str(chart["driver"]), int(chart["lap_number"])
    race = str(chart["session_code"]) in RACE_SESSIONS
    window = track.pixels(apex_m + DISTANCE)
    (ax, ay) = track.pixels(np.array([apex_m]))[0]
    cars = [] if loaded is None else nearby_cars(loaded.tracks, driver, lap, apex_m, track, race)
    corner_map: CornerMap = {
        **track.payload,
        "window": [[round(float(x), 1), round(float(y), 1)] for x, y in window],
        "apex": {"x": round(float(ax), 1), "y": round(float(ay), 1)},
        "cars": cars,
    }
    replay = None
    if loaded is not None:
        ghost = chart.get("ghost")
        usual = None if ghost is None else (str(ghost["driver"]), int(ghost["lap"]))
        # What's left of the budget once the rest is in (the replay takes the place of "null").
        budget = CHART_MAX_BYTES - json_size({**chart, "map": corner_map, "replay": None}) + 4
        replay = corner_replay(loaded.runs, driver, lap, apex_m, usual, track, race, budget)
    return corner_map, replay
