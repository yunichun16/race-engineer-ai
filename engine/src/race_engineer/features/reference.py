"""Per-track reference line: the yardstick every lap's distance is measured against.

FastF1's own lap distance integrates speed, so it drifts by tens of metres and differs from
driver to driver. Instead, each position sample is matched to the nearest point of a
reference line built from the session's fastest lap, which puts every lap of every driver on
the same distance axis.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import KDTree

from race_engineer.config import POSITION_UNITS_PER_M

# Where the track crosses itself (Suzuka's figure of eight), the nearest point of the line can be
# on the other branch. A match further than this from the expected distance is re-checked
# against the points of the line within CROSSING_RADIUS_M.
CROSSING_TOLERANCE_M = 300.0
CROSSING_RADIUS_M = 30.0


@dataclass(frozen=True)
class Projection:
    distance_m: np.ndarray  # arc length along the reference line
    offset_m: np.ndarray  # signed lateral offset from the line (positive = left of travel)


class ReferenceLine:
    """A closed racing line, densified to `spacing_m` and lightly smoothed."""

    def __init__(self, xy_m: np.ndarray, spacing_m: float = 1.0, smooth_m: float = 4.0) -> None:
        xy = _drop_repeats(np.asarray(xy_m, dtype=float))
        if len(xy) < 10:
            raise ValueError("reference line needs at least 10 distinct points")
        closed = np.vstack([xy, xy[:1]])
        seg = np.hypot(*np.diff(closed, axis=0).T)
        s = np.concatenate([[0.0], np.cumsum(seg)])
        self.length_m = float(s[-1])

        self.s = np.arange(0.0, self.length_m, spacing_m)
        x = np.interp(self.s, s, closed[:, 0])
        y = np.interp(self.s, s, closed[:, 1])
        sigma = smooth_m / spacing_m
        self.xy = np.column_stack(
            [gaussian_filter1d(x, sigma, mode="wrap"), gaussian_filter1d(y, sigma, mode="wrap")]
        )
        tangent = np.gradient(self.xy, axis=0)
        self._tangent = tangent / np.linalg.norm(tangent, axis=1, keepdims=True)
        self._tree = KDTree(self.xy)
        self._curvature: np.ndarray | None = None

    @classmethod
    def from_positions(cls, x: np.ndarray, y: np.ndarray, **kwargs: float) -> ReferenceLine:
        """Build from FastF1 X/Y position samples (decimetres) of one lap."""
        xy = np.column_stack([x, y]).astype(float) / POSITION_UNITS_PER_M
        return cls(xy, **kwargs)

    def project(
        self, x: np.ndarray, y: np.ndarray, expected_m: np.ndarray | None = None
    ) -> Projection:
        """Match FastF1 X/Y samples (decimetres) to the nearest point on the line.

        `expected_m` is a rough distance along the line for each sample (e.g. from integrated
        speed). Samples whose nearest point is far from it, as happens at a crossing, take the
        nearest point within CROSSING_RADIUS_M that agrees with it instead, if there is one.
        """
        pts = np.column_stack([x, y]).astype(float) / POSITION_UNITS_PER_M
        _, idx = self._tree.query(pts)
        idx = np.asarray(idx)
        if expected_m is not None:
            astray = self._along_gap(self.s[idx], expected_m) > CROSSING_TOLERANCE_M
            for i in np.flatnonzero(astray):
                near = np.asarray(self._tree.query_ball_point(pts[i], CROSSING_RADIUS_M), int)
                # Only another branch of the line, never a nearby point on the same one.
                near = near[self._along_gap(self.s[near], self.s[idx[i]]) > 2 * CROSSING_RADIUS_M]
                near = near[self._along_gap(self.s[near], expected_m[i]) <= CROSSING_TOLERANCE_M]
                if len(near):
                    idx[i] = near[np.argmin(np.hypot(*(self.xy[near] - pts[i]).T))]
        delta = pts - self.xy[idx]
        t = self._tangent[idx]
        offset = t[:, 0] * delta[:, 1] - t[:, 1] * delta[:, 0]  # 2D cross product
        return Projection(distance_m=self.s[idx], offset_m=offset)

    def _along_gap(self, s: np.ndarray, other: np.ndarray | float) -> np.ndarray:
        """Distance between two positions along the closed line, the short way round."""
        diff = np.mod(s - other, self.length_m)
        return np.minimum(diff, self.length_m - diff)

    def curvature(self) -> np.ndarray:
        """Signed curvature (1/m) at each reference point; positive = turning left."""
        if self._curvature is None:
            heading = np.unwrap(np.arctan2(self._tangent[:, 1], self._tangent[:, 0]))
            spacing = float(self.s[1] - self.s[0])
            # Remove the 2*pi winding so the wrapped filter doesn't see a jump at the line.
            winding = (heading[-1] - heading[0]) / (len(heading) - 1)
            detrended = heading - winding * np.arange(len(heading))
            smoothed = gaussian_filter1d(detrended, 15.0 / spacing, mode="wrap")
            curvature = (np.gradient(smoothed) + winding) / spacing
            self._curvature = curvature
            return curvature
        return self._curvature

    def curvature_at(self, distance_m: np.ndarray) -> np.ndarray:
        spacing = float(self.s[1] - self.s[0])
        idx = np.round(np.mod(distance_m, self.length_m) / spacing).astype(int)
        return self.curvature()[np.clip(idx, 0, len(self.s) - 1)]


def _drop_repeats(xy: np.ndarray) -> np.ndarray:
    keep = np.ones(len(xy), dtype=bool)
    keep[1:] = np.any(np.diff(xy, axis=0) != 0, axis=1)
    return xy[keep]
