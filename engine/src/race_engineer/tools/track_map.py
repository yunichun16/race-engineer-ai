"""The track map in the mistake charts: the circuit's outline, turn numbers and start/finish line.

The outline is the session's reference line (the `tracks` table, every 5 m), drawn in pixels
turned like the official circuit map, as on TV: the angle comes from `circuits.json`
(`data.circuits`, worked out ahead of time; never read from FastF1 during a request), and is 0
(north up, `rotation_source: "default"`) for a round it doesn't have. The longer side of the
map is MAP_SIZE px. Turn numbers sit where FastF1 puts them: LABEL_OFFSET_M from the turn, in
the direction FastF1 gives (the `corners` table's `angle`; on the turn without one).

`find_mistakes` sends the payload as `track`, `explain_corner` as the base of `map` (with the
analysed stretch and the cars around added, see `tools.mistake_charts`). `TrackMap.pixels`
places any distance along the lap on the map, for the cars and the replay (`tools.replay`).

Ported from the review page's builder (`scripts/review_queue.py`), which stays as it is until
M4 is merged.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

import numpy as np
import pandas as pd

from race_engineer.config import GRID_STEP_M, PROCESSED_DIR
from race_engineer.data import store
from race_engineer.data.circuits import circuit_key, circuits_path, load_circuits
from race_engineer.inference.explain import turn_label
from race_engineer.tools.cache import memo_by_files

MAP_SIZE = 300  # the map's viewBox, px
MAP_PAD = 22  # room for the turn numbers at the edges
MAP_STEP_M = 20.0  # the outline's resolution
LABEL_OFFSET_M = 70.0  # turn numbers sit this far from their turn, as on the TV graphics
MAP_CACHE_SIZE = 64  # sessions' maps kept (0.07 s and about 4 KB each)


class Turn(TypedDict):
    label: str
    x: float  # the turn, px
    y: float
    tx: float  # its number, px
    ty: float


class CornerMark(TypedDict):
    label: str
    apex_m: float  # along the lap from the line


class Start(TypedDict):
    x: float  # the start/finish line, px
    y: float
    dx: float  # unit vector of the racing direction there, px
    dy: float


class TrackPayload(TypedDict):
    """The map as the charts get it (`TrackPayload` in web/src/charts/dom.ts)."""

    width: int
    height: int
    length_m: float
    step_m: float
    outline: list[list[float]]  # px every step_m, closed (the last point is the first)
    turns: list[Turn]
    corners: list[CornerMark]
    start: Start | None
    rotation_source: Literal["fastf1", "default"]


class MapFrame:
    """Track coordinates (m) -> the map's pixels: rotated like the official map, north of
    the rotated frame up, scaled so the longer side is MAP_SIZE (`width` x `height`)."""

    def __init__(self, x: np.ndarray, y: np.ndarray, rotation_deg: float) -> None:
        a = math.radians(rotation_deg)
        self.cos, self.sin = math.cos(a), math.sin(a)
        rx, ry = self._rotate(x, y)
        self.x0, self.y1 = float(rx.min()), float(ry.max())
        span_x, span_y = float(rx.max()) - self.x0, self.y1 - float(ry.min())
        self.scale = (MAP_SIZE - 2 * MAP_PAD) / max(span_x, span_y, 1e-9)
        self.width = round(2 * MAP_PAD + span_x * self.scale)
        self.height = round(2 * MAP_PAD + span_y * self.scale)

    def _rotate(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return x * self.cos - y * self.sin, x * self.sin + y * self.cos

    def __call__(self, x: object, y: object) -> tuple[np.ndarray, np.ndarray]:
        rx, ry = self._rotate(np.asarray(x, float), np.asarray(y, float))
        return (
            MAP_PAD + (rx - self.x0) * self.scale,
            MAP_PAD + (self.y1 - ry) * self.scale,
        )


@dataclass(frozen=True)
class TrackMap:
    """A session's map: the payload for the charts, plus the frame and the reference line
    (x, y every GRID_STEP_M, m) for placing cars on it. Cached and shared: read-only."""

    payload: TrackPayload
    frame: MapFrame
    x: np.ndarray
    y: np.ndarray
    length_m: float

    def pixels(self, distance_m: np.ndarray) -> np.ndarray:
        """(n, 2) map pixels of distances along the track, in metres from the line on any lap
        (a car's total distance works: it wraps at length_m), between grid points by
        interpolation, from the last grid point back to the first across the line. NaN
        distances give NaN rows."""
        d = np.asarray(distance_m, float)
        ok = np.isfinite(d)
        n = len(self.x)
        s = np.where(ok, d, 0.0) % self.length_m
        i = s / GRID_STEP_M
        lo = np.minimum(np.floor(i).astype(int), n - 1)
        # The last stretch runs from the last grid point to the line, a few metres at most.
        tail = max(self.length_m - (n - 1) * GRID_STEP_M, 1e-9)
        w = np.where(lo < n - 1, i - lo, (s - (n - 1) * GRID_STEP_M) / tail)
        hi = (lo + 1) % n
        px, py = self.frame(
            self.x[lo] * (1 - w) + self.x[hi] * w, self.y[lo] * (1 - w) + self.y[hi] * w
        )
        return np.where(ok[:, None], np.column_stack([px, py]), np.nan)


def _px(value: float) -> float:
    return round(float(value), 1)


def rotation_of(year: int, round_: int, root: Path = PROCESSED_DIR) -> tuple[float, str]:
    """(angle in degrees, "fastf1" or "default") for the round's map, from circuits.json; 0.0
    and "default" when the file or the round is missing."""
    found = _circuits(root).get(circuit_key(year, round_))
    if found is None:
        return 0.0, "default"
    return found["rotation_deg"], found["source"]


def rotation(year: int, round_: int, root: Path = PROCESSED_DIR) -> float:
    """The round's map rotation in degrees (0.0, north up, when circuits.json lacks it)."""
    return rotation_of(year, round_, root)[0]


@memo_by_files(lambda a: circuits_path(a["root"]), maxsize=4)
def _circuits(root: Path) -> dict:
    return load_circuits(root)


def _map_files(arguments: Mapping[str, Any]) -> list[Path]:
    key, root = arguments["key"], arguments["root"]
    return [
        store.table_path("tracks", key, root),
        store.table_path("corners", key, root),
        circuits_path(root),
    ]


@memo_by_files(_map_files, maxsize=MAP_CACHE_SIZE)
def track_map(key: str, root: Path = PROCESSED_DIR) -> TrackMap | None:
    """The session's map (see the module docstring); None when it has no stored track. A
    session without a `corners` table gets a map without turns."""
    path = store.table_path("tracks", key, root)
    if not path.exists():
        return None
    track = pd.read_parquet(path).iloc[0]
    x, y = np.asarray(track["x_m"], float), np.asarray(track["y_m"], float)
    if len(x) < 2 or not (np.isfinite(x).all() and np.isfinite(y).all()):
        return None
    angle, source = rotation_of(int(track["year"]), int(track["round"]), root)
    frame = MapFrame(x, y, angle)
    step = max(1, round(MAP_STEP_M / GRID_STEP_M))
    ox, oy = frame(np.append(x[::step], x[0]), np.append(y[::step], y[0]))
    turns, marks = _turns(key, root, frame)
    (sx, ex), (sy, ey) = frame(x[:2], y[:2])
    norm = math.hypot(ex - sx, ey - sy)
    start: Start | None = None
    if norm > 0:
        start = {
            "x": _px(sx),
            "y": _px(sy),
            "dx": round(float(ex - sx) / norm, 3),
            "dy": round(float(ey - sy) / norm, 3),
        }
    payload: TrackPayload = {
        "width": int(frame.width),
        "height": int(frame.height),
        "length_m": round(float(track["length_m"]), 1),
        "step_m": step * GRID_STEP_M,
        "outline": [[_px(a), _px(b)] for a, b in zip(ox, oy, strict=True)],
        "turns": turns,
        "corners": marks,
        "start": start,
        "rotation_source": "fastf1" if source == "fastf1" else "default",
    }
    return TrackMap(payload=payload, frame=frame, x=x, y=y, length_m=float(track["length_m"]))


def _turns(key: str, root: Path, frame: MapFrame) -> tuple[list[Turn], list[CornerMark]]:
    """The turns on the map (where each is and where its number goes) and their apexes along
    the lap; none without a corners table."""
    path = store.table_path("corners", key, root)
    if not path.exists():
        return [], []
    corners = pd.read_parquet(path, columns=["number", "letter", "apex_m", "x_m", "y_m", "angle"])
    corners = corners[np.isfinite(corners["x_m"]) & np.isfinite(corners["y_m"])]
    cx, cy = corners["x_m"].to_numpy(float), corners["y_m"].to_numpy(float)
    # FastF1 gives each turn number a direction to sit in (no direction: on the turn).
    angle = np.radians(corners["angle"].to_numpy(float))
    has = np.isfinite(angle)
    lx = np.where(has, cx + LABEL_OFFSET_M * np.cos(np.where(has, angle, 0)), cx)
    ly = np.where(has, cy + LABEL_OFFSET_M * np.sin(np.where(has, angle, 0)), cy)
    mx, my = frame(cx, cy)
    tx, ty = frame(lx, ly)
    labels = [
        turn_label(int(n), letter if isinstance(letter, str) else "")
        for n, letter in zip(corners["number"], corners["letter"], strict=True)
    ]
    turns: list[Turn] = [
        {"label": label, "x": _px(a), "y": _px(b), "tx": _px(c), "ty": _px(d)}
        for label, a, b, c, d in zip(labels, mx, my, tx, ty, strict=True)
    ]
    marks: list[CornerMark] = [
        {"label": label, "apex_m": round(float(apex), 1)}
        for label, apex in zip(labels, corners["apex_m"].to_numpy(float), strict=True)
    ]
    return turns, marks
