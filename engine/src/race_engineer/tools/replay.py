"""The corner replay in the explain-corner chart, and the cars on its map at the apex.

Where each car was comes from `inference.traffic.SessionTracks`, the same positions the
traffic explanations use (`explain_corner` names the cars around from them): each car's total
distance (metres from the start of its lap 1) against session time, with no position in the pit
lane, on stretches the feed shifted, on laps without a grid or sector times, and after it
retired. So the picture matches what the explanation says. Positions placed from sector times
rather than the telemetry are `approximate` (traffic's module docstring has how far out they
can be), and the replay marks a car with any (`approx`).

`corner_replay` animates the corner: where the car, a ghost of a usual lap (starting level with
it; explain_corner gives the reference lap closest to the usual time through the corner) and
the cars near it were, every REPLAY_DT_S from REPLAY_BEFORE_M before the apex to
REPLAY_AFTER_M after it (a little more than the analysed window, so a pass around it shows), at
most REPLAY_MAX_S, in map pixels. Where the car has no position at an end of that stretch (a
feed hole just past the window, the pit lane), the stretch stops where it has one, as long as
it still covers the analysed window. Another car is in it when it came within REPLAY_NEAR_M
of the car, and drawn while it is that close or on the replayed stretch itself (what the
zoomed view shows), not elsewhere on the circuit. A train of cars lapping the driver can
bring many (11 within REPLAY_NEAR_M of ALB at turn 11 on lap 51 of the 2023 Monaco GP, about
1.2 KB each), so the replay keeps the REPLAY_MAX_CARS that came closest, and fewer when the
caller gives it a size budget (`max_bytes`) they would overrun. `nearby_cars` is the still:
the other cars within NEARBY_S of the corner when the car reached the apex.

`load_session_tracks` keeps the last TRACKS_CACHE_SIZE sessions' tracks: explain_corner reads
them once and hands the same object to its traffic explanation (`inference.explain.explain`)
and to the map and replay (`tools.mistake_charts`).

Ported from the review page's builders (`scripts/review_queue.py`), which used each lap's grid
clock directly; that script stays as it is until M4 is merged. One difference besides the
source of positions: a lap's distance here is the track length (the `sessions` table's), not
the grid's point count times 5 m, which falls up to 2.5 m short, so positions wrap at
`TrackMap.length_m` and don't drift a few metres a lap.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import numpy as np

from race_engineer.config import PROCESSED_DIR
from race_engineer.data import store
from race_engineer.features.handcrafted import DISTANCE
from race_engineer.inference import traffic
from race_engineer.inference.traffic import Car, SessionTracks
from race_engineer.tools.cache import memo_by_files
from race_engineer.tools.track_map import TrackMap

REPLAY_BEFORE_M = 400.0
REPLAY_AFTER_M = 300.0
REPLAY_DT_S = 0.2
REPLAY_MAX_S = 45.0
REPLAY_NEAR_M = 300.0  # other cars this close on the track at some frame are in the replay
REPLAY_MAX_CARS = 8  # at most this many of them, the closest
REPLAY_ZOOM_PAD = 18.0  # px around the replayed stretch in the zoomed view
REPLAY_MIN_BOX = 60.0  # px, the zoomed view's smallest side
# The analysed window (m from the apex): a replay always covers it.
WINDOW_M = (float(DISTANCE[0]), float(DISTANCE[-1]))
NEARBY_S = 5.0  # cars within this many seconds of the corner at the apex moment are drawn
RACE_SESSIONS = ("R", "S")  # laps up or down only mean something in a race
TRACKS_CACHE_SIZE = 2  # sessions' tracks kept (tens of MB each)

Pt = list[float] | None  # [x, y] px; None where the car has no position


class NearbyCar(TypedDict):
    driver: str
    gap_s: float  # how much later it passed the apex: + behind, - ahead
    laps: int  # laps up (+) or down (-) on the driver; 0 outside races
    note: str  # "out-lap", "in-lap", "placed from sector times"
    x: float  # px
    y: float


class ReplayCar(TypedDict):
    driver: str
    laps: int
    approx: bool  # some of its positions here come from sector times
    pts: list[Pt]


class Replay(TypedDict):
    """`replay` in the explain-corner chart (web/src/charts/explain-corner.ts)."""

    dt: float  # s between frames
    me: list[Pt]
    dist: list[float]  # the car's metres from the apex at each frame, for the playhead
    ghost: NotRequired[list[Pt]]
    ghost_label: NotRequired[str]
    cars: list[ReplayCar]
    box: list[float]  # the zoomed view: x, y, width, height (px)


@dataclass(frozen=True)
class Run:
    """One car's session for the replay: its positions (`car`) and, for finding where it was
    at a given time, the times it has a position at, strictly increasing, with their
    distances."""

    car: Car
    distance: np.ndarray  # m from the start of its lap 1
    time: np.ndarray  # session time (s)

    def distance_at(self, times: np.ndarray) -> np.ndarray:
        """Total distance at each session time, NaN where the car had no position then (in
        the pit lane, across a feed hole, before its first or after its last position)."""
        s = np.interp(times, self.time, self.distance, left=math.nan, right=math.nan)
        ok = np.isfinite(s)
        on_track = np.isfinite(self.car.time_at(np.where(ok, s, self.distance[0])))
        return np.where(ok & on_track, s, math.nan)

    def bridged_at(self, times: np.ndarray) -> np.ndarray:
        """As distance_at, but across the stretches without a position too (for the playhead)."""
        return np.interp(times, self.time, self.distance)

    def approximate_at(self, distance: np.ndarray) -> np.ndarray:
        """Whether the position at each total distance comes from sector times."""
        flag = self.car.approximate.astype(float)
        return np.interp(distance, self.car.distance, flag, left=0, right=0) > 0


def runs_from_tracks(tracks: SessionTracks) -> dict[str, Run]:
    """Each car's Run: its known positions with the times strictly increasing (a car's clock
    can step back a few hundredths where one lap's grid meets the next)."""
    runs = {}
    for driver, car in tracks.cars.items():
        ok = np.isfinite(car.time)
        d, t = car.distance[ok], car.time[ok]
        if len(t) < 2:
            continue
        keep = np.concatenate([[True], t[1:] > np.maximum.accumulate(t)[:-1]])
        runs[driver] = Run(car=car, distance=d[keep], time=t[keep])
    return runs


@dataclass(frozen=True)
class LoadedTracks:
    tracks: SessionTracks
    runs: dict[str, Run]


def _tracks_files(arguments: Mapping[str, Any]) -> list[Path]:
    key, root = arguments["key"], arguments["root"]
    return [store.table_path(t, key, root) for t in ("laps", "lap_grids", "sessions")]


@memo_by_files(_tracks_files, maxsize=TRACKS_CACHE_SIZE)
def load_session_tracks(key: str, root: Path = PROCESSED_DIR) -> LoadedTracks | None:
    """The session's tracks (`traffic.load_tracks`) and runs; None without laps, lap grids or
    a sessions table. Cached until those files change, and shared: read-only."""
    tracks = traffic.load_tracks(key, root)
    if tracks is None:
        return None
    return LoadedTracks(tracks=tracks, runs=runs_from_tracks(tracks))


def _points(xy: np.ndarray) -> list[Pt]:
    return [
        None if not np.isfinite(p).all() else [round(float(p[0]), 1), round(float(p[1]), 1)]
        for p in xy
    ]


def _flag(value: object) -> bool:
    """A laps table flag (pit_in, pit_out), missing counting as False."""
    return bool(value is True or (isinstance(value, np.bool_ | int | float) and value == 1))


def _note(tracks: SessionTracks, driver: str, lap: int, approximate: bool) -> str:
    """What to say about a car's position: on a pit lap, and placed from sector times."""
    parts = []
    info = traffic.lap_info(tracks, driver, lap)
    if info is not None:
        lap_class = str(info["lap_class"])
        if _flag(info["pit_out"]) or lap_class == "out":
            parts.append("out-lap")
        elif _flag(info["pit_in"]) or lap_class == "in":
            parts.append("in-lap")
    if approximate:
        parts.append("placed from sector times")
    return ", ".join(parts)


def nearby_cars(
    tracks: SessionTracks, driver: str, lap: int, apex_m: float, track: TrackMap, race: bool
) -> list[NearbyCar]:
    """The other cars on the map at the moment the driver reached the apex (`apex_m` along
    lap `lap`): where they were, how much later (+) or earlier (-) they passed that apex on
    their nearest lap, kept within NEARBY_S, and in races how many laps up (+) or down (-) on
    the driver they were. A car without a position then (in the pit lane, say) is left out."""
    me = tracks.cars.get(driver)
    if me is None:
        return []
    length = tracks.length_m
    apex = (lap - 1) * length + apex_m
    t_apex = float(me.time_at(np.array([apex]))[0])
    if not np.isfinite(t_apex):
        return []
    out: list[NearbyCar] = []
    for other, car in tracks.cars.items():
        if other == driver:
            continue
        where = car.distance_at(t_apex)
        if not np.isfinite(where) or not np.isfinite(car.time_at(np.array([where]))[0]):
            continue
        ahead = round((where - apex) / length)
        crossing = float(car.time_at(np.array([apex + ahead * length]))[0])
        gap = crossing - t_apex
        if not np.isfinite(gap) or abs(gap) > NEARBY_S:
            continue
        flag = car.approximate.astype(float)
        approximate = bool(np.interp(where, car.distance, flag, left=0, right=0) > 0)
        x, y = track.pixels(np.array([where]))[0]
        out.append(
            {
                "driver": other,
                "gap_s": round(gap, 1),
                "laps": int(ahead) if race else 0,
                "note": _note(tracks, other, int(where // length) + 1, approximate),
                "x": round(float(x), 1),
                "y": round(float(y), 1),
            }
        )
    return sorted(out, key=lambda c: (c["gap_s"], c["driver"]))


def corner_replay(
    runs: dict[str, Run],
    driver: str,
    lap: int,
    apex_m: float,
    ghost: tuple[str, int] | None,
    track: TrackMap,
    race: bool,
    max_bytes: int | None = None,
) -> Replay | None:
    """The corner as an animation (see the module docstring): the car, a ghost of `ghost`
    (driver, lap) starting level with it, and the other cars that came within REPLAY_NEAR_M of
    it, in map pixels; the car's distance from the apex at each frame; and the zoomed view's
    box. The cars are the closest REPLAY_MAX_CARS, and only as many as keep the payload within
    `max_bytes` as compact JSON (None: no limit). None when the stretch with a position
    doesn't cover the analysed window (`_stretch`)."""
    me = runs.get(driver)
    if me is None:
        return None
    length = track.length_m
    s_apex = (lap - 1) * length + apex_m
    stretch = _stretch(me.car, s_apex)
    if stretch is None:
        return None
    s0, s1 = stretch
    t0, t1 = (float(t) for t in me.car.time_at(np.array([s0, s1])))
    if not (np.isfinite(t0) and np.isfinite(t1)) or t1 <= t0:
        return None
    taus = np.arange(0.0, min(t1 - t0, REPLAY_MAX_S) + 1e-9, REPLAY_DT_S)
    at = t0 + taus
    s_me = me.distance_at(at)
    out: Replay = {
        "dt": REPLAY_DT_S,
        "me": _points(track.pixels(s_me)),
        "dist": [round(float(s - s_apex), 1) for s in me.bridged_at(at)],
        "cars": [],
        "box": [],
    }
    if ghost is not None and ghost[0] in runs:
        g = runs[ghost[0]]
        g_apex = (ghost[1] - 1) * length + apex_m
        edges = np.array([g_apex + s0 - s_apex, g_apex + s1 - s_apex])
        g0, g1 = (float(t) for t in g.car.time_at(edges))
        if np.isfinite(g0) and np.isfinite(g1):  # the ghost lap covers the whole stretch
            out["ghost"] = _points(track.pixels(g.distance_at(g0 + taus)))
            who = "" if ghost[0] == driver else f"{ghost[0]} "
            out["ghost_label"] = f"usual: {who}lap {ghost[1]}"
    cars: list[tuple[float, ReplayCar]] = []  # (closest approach in metres, car)
    for other, run in runs.items():
        if other == driver:
            continue
        s_b = run.distance_at(at)
        known = np.isfinite(s_b)
        if not known.any():
            continue
        # Track distance between them, either way round the lap.
        gap = ((s_b - s_me) % length + length / 2) % length - length / 2
        near = np.isfinite(gap) & (np.abs(np.where(np.isfinite(gap), gap, np.inf)) <= REPLAY_NEAR_M)
        if not near.any():
            continue
        on_stretch = known & ((np.where(known, s_b, 0.0) - s0) % length <= s1 - s0)
        shown = near | on_stretch
        s_b = np.where(shown, s_b, math.nan)
        laps = 0
        if race:
            i = int(np.flatnonzero(near)[len(np.flatnonzero(near)) // 2])
            laps = round((s_b[i] - s_me[i] - gap[i]) / length)
        car: ReplayCar = {
            "driver": other,
            "laps": int(laps),
            "approx": bool(run.approximate_at(s_b[shown]).any()),
            "pts": _points(track.pixels(s_b)),
        }
        cars.append((float(np.abs(gap[near]).min()), car))
    # The zoomed view: the replayed stretch with a margin, square so turns aren't squashed.
    stretch = track.pixels(np.linspace(s0, s1, 60))
    lo, hi = stretch.min(axis=0), stretch.max(axis=0)
    side = max(float((hi - lo).max()) + 2 * REPLAY_ZOOM_PAD, REPLAY_MIN_BOX)
    centre = (lo + hi) / 2
    out["box"] = [
        round(float(centre[0] - side / 2), 1),
        round(float(centre[1] - side / 2), 1),
        round(side, 1),
        round(side, 1),
    ]
    size = json_size(out)
    kept: list[ReplayCar] = []
    for _, car in sorted(cars, key=lambda c: (c[0], c[1]["driver"]))[:REPLAY_MAX_CARS]:
        extra = json_size(car) + 1  # and the comma before it
        if max_bytes is not None and size + extra > max_bytes:
            break
        kept.append(car)
        size += extra
    out["cars"] = sorted(kept, key=lambda c: c["driver"])
    return out


def _stretch(car: Car, s_apex: float) -> tuple[float, float] | None:
    """The replayed stretch's ends (total distances) around the apex at `s_apex`:
    REPLAY_BEFORE_M before it to REPLAY_AFTER_M after it, an end where the car has no position
    moved in to the nearest point where it has one, as long as the stretch still covers the
    analysed window (WINDOW_M); None when it doesn't."""
    s0, s1 = s_apex - REPLAY_BEFORE_M, s_apex + REPLAY_AFTER_M
    t0, t1 = car.time_at(np.array([s0, s1]))
    known = car.distance[np.isfinite(car.time)]
    if not np.isfinite(t0):
        starts = known[(known >= s0) & (known <= s_apex + WINDOW_M[0])]
        if not len(starts):
            return None
        s0 = float(starts[0])
    if not np.isfinite(t1):
        ends = known[(known >= s_apex + WINDOW_M[1]) & (known <= s1)]
        if not len(ends):
            return None
        s1 = float(ends[-1])
    return s0, s1


def json_size(value: object) -> int:
    """Bytes of `value` as compact JSON, as the MCP and REST responses send it."""
    return len(json.dumps(value, separators=(",", ":"), default=str))
