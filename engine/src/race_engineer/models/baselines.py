"""Baselines the deep model has to beat.

Mistake detectors score how unusual a corner segment is for *that driver at that corner in
that session*, so style and car differences don't count as mistakes:

- TimeLoss: slower than the driver's usual time through the corner. The simplest possible idea.
- ZScore: root-mean-square of the driver-relative z-scores of the hand-crafted features.
- IsoForest: an Isolation Forest on the same z-scores.
- PCATrace: reconstruction error of the raw traces under PCA, the linear cousin of the
  autoencoder in M3.

Style probes ask how much of a driver's identity a single corner carries, from features
normalised per session and corner (so track and corner differences are removed).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from race_engineer.config import PROCESSED_DIR
from race_engineer.data.store import fixed_list_to_numpy, session_key
from race_engineer.features.handcrafted import FEATURES

TIME_FEATURES = ("segment_time_s", "time_to_apex_s")
SHAPE_FEATURES = tuple(f for f in FEATURES if f not in TIME_FEATURES)
SESSION_CORNER = ["year", "round", "session", "corner"]
DRIVER_CORNER = [*SESSION_CORNER, "driver"]

# Smallest spread a feature is allowed, from the 5 m grid and sensor resolution. Stops a
# zero MAD (e.g. identical braking points on the grid) from turning tiny changes into huge z.
MIN_SCALE = {
    "v_start": 2.0, "v_min": 1.0, "d_vmin": 5.0, "v_apex": 1.0, "v_exit": 2.0,
    "brake_start_d": 5.0, "brake_len_m": 5.0, "decel_max": 1.0, "throttle_off_d": 5.0,
    "throttle_pickup_d": 5.0, "throttle_dip": 5.0, "coast_m": 5.0, "exit_accel": 0.3,
    "gear_min": 0.5, "offset_apex": 0.2, "offset_exit_max": 0.2, "offset_range": 0.2,
    "time_to_apex_s": 0.01, "segment_time_s": 0.01,
}  # fmt: skip
MIN_GROUP = 5
Z_CLIP = 10.0


def robust_z(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    by: list[str],
    min_scale: dict[str, float] | None = None,
) -> np.ndarray:
    """(value - group median) / (1.4826 * group MAD), NaN where the group is too small."""
    min_scale = MIN_SCALE if min_scale is None else min_scale
    values = frame[list(columns)].astype(float)
    keys = [frame[c] for c in by]
    median = values.groupby(keys).transform("median")
    mad = (values - median).abs().groupby(keys).transform("median")
    scale = np.maximum(1.4826 * mad.to_numpy(), np.array([min_scale[c] for c in columns]))
    z = (values.to_numpy() - median.to_numpy()) / scale
    size = frame.groupby(by)[by[0]].transform("size").to_numpy()
    z[size < MIN_GROUP] = np.nan
    return np.clip(z, -Z_CLIP, Z_CLIP)


def driver_relative_z(
    frame: pd.DataFrame, columns: tuple[str, ...], min_scale: dict[str, float] | None = None
) -> np.ndarray:
    """Z-scores against the driver's own laps, falling back to the field when they have few."""
    z = robust_z(frame, columns, DRIVER_CORNER, min_scale)
    fallback = robust_z(frame, columns, SESSION_CORNER, min_scale)
    return np.where(np.isnan(z), fallback, z)


class Detector:
    name = "detector"

    def fit(self, frame: pd.DataFrame, train: np.ndarray) -> Detector:
        return self

    def score(self, frame: pd.DataFrame) -> np.ndarray:  # higher = more likely a mistake
        raise NotImplementedError


class TimeLoss(Detector):
    name = "time_loss"

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        return np.nan_to_num(driver_relative_z(frame, ("segment_time_s",))[:, 0])


class ZScore(Detector):
    name = "z_score"

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        z = driver_relative_z(frame, SHAPE_FEATURES)
        known = (~np.isnan(z)).sum(axis=1)  # corners with too few laps have no statistics
        return np.sqrt(np.square(np.nan_to_num(z)).sum(axis=1) / np.maximum(known, 1))


class IsoForest(Detector):
    name = "isolation_forest"

    def fit(self, frame: pd.DataFrame, train: np.ndarray) -> Detector:
        self._z = np.nan_to_num(driver_relative_z(frame, SHAPE_FEATURES))
        self.model = IsolationForest(
            n_estimators=300,
            max_samples=4096,  # pyright: ignore[reportArgumentType]  (sklearn infers str from "auto")
            random_state=0,
            n_jobs=-1,
        )
        self.model.fit(self._z[train])
        return self

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        return -self.model.score_samples(self._z)


TRACE_CHANNELS = ("speed", "throttle", "brake", "offset_m")
TRACE_FLOOR = {"speed": 2.0, "throttle": 5.0, "brake": 0.1, "offset_m": 0.3}


def session_traces(frame: pd.DataFrame, rows: np.ndarray) -> np.ndarray:
    """Raw traces for `rows` (all from one session), normalised per corner: (n, 4 * 80)."""
    part = frame.iloc[rows]
    year, round_, code = part[["year", "round", "session"]].iloc[0]
    table = pq.read_table(
        PROCESSED_DIR / "segments" / f"{session_key(int(year), int(round_), str(code))}.parquet",
        columns=["segment_id", *TRACE_CHANNELS],
    )
    position = pd.Series(np.arange(table.num_rows), index=table.column("segment_id").to_pylist())
    take = position.loc[part["segment_id"]].to_numpy()
    corner = part["corner"].to_numpy()
    blocks = []
    for channel in TRACE_CHANNELS:
        x = fixed_list_to_numpy(table.column(channel))[take]
        mean = np.zeros_like(x)
        std = np.ones_like(x)
        for c in np.unique(corner):
            m = corner == c
            mean[m] = x[m].mean(axis=0)
            std[m] = np.maximum(x[m].std(axis=0), TRACE_FLOOR[channel])
        blocks.append((x - mean) / std)
    return np.concatenate(blocks, axis=1)


@dataclass
class PCATrace(Detector):
    n_components: int = 16
    max_fit: int = 150_000
    name: str = field(default="pca_trace", init=False)

    def fit(self, frame: pd.DataFrame, train: np.ndarray) -> Detector:
        rng = np.random.default_rng(0)
        keep = np.flatnonzero(train)
        keep = np.sort(rng.choice(keep, size=min(len(keep), self.max_fit), replace=False))
        chosen = np.zeros(len(frame), bool)
        chosen[keep] = True
        parts = []
        for rows in _session_rows(frame):
            traces = session_traces(frame, rows)  # normalised with the whole session
            parts.append(traces[chosen[rows]])
        self.model = PCA(n_components=self.n_components, random_state=0).fit(np.concatenate(parts))
        return self

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        score = np.empty(len(frame))
        for rows in _session_rows(frame):
            x = session_traces(frame, rows)
            recon = self.model.inverse_transform(self.model.transform(x))
            score[rows] = np.mean(np.square(x - recon), axis=1)
        return score


def _session_rows(frame: pd.DataFrame) -> list[np.ndarray]:
    groups = frame.groupby(["year", "round", "session"], sort=True).indices
    return [np.sort(rows) for rows in groups.values()]


DETECTORS: tuple[type[Detector], ...] = (TimeLoss, ZScore, IsoForest, PCATrace)


# ---------------------------------------------------------------------------------------------
# Style probes


def _probe_features(frame: pd.DataFrame) -> np.ndarray:
    return np.nan_to_num(robust_z(frame, FEATURES, SESSION_CORNER))


def _lap_accuracy(
    log_proba: np.ndarray, laps: np.ndarray, truth: np.ndarray, classes: np.ndarray
) -> float:
    """Accuracy when a whole lap's corners vote (summed log-probabilities)."""
    frame = pd.DataFrame(log_proba, columns=classes).assign(_lap=laps, _truth=truth)
    summed = frame.groupby("_lap")[list(classes)].sum()
    truth_by_lap = frame.groupby("_lap")["_truth"].first()
    return float((summed.idxmax(axis=1) == truth_by_lap).mean())


def driver_probe(
    frame: pd.DataFrame, split: pd.Series, x: np.ndarray | None = None, max_train: int = 100_000
) -> dict[str, float]:
    """Which driver took this corner? Top-1/top-5 over all drivers seen in train and test.

    `x` defaults to the hand-crafted features; pass model embeddings to probe those instead.
    """
    x = _probe_features(frame) if x is None else x
    drivers = frame["driver"].to_numpy()
    train, test = (split == "train").to_numpy(), (split == "test").to_numpy()
    classes = np.array(sorted(set(drivers[train]) & set(drivers[test])))
    train &= np.isin(drivers, classes)
    test &= np.isin(drivers, classes)
    rows = np.flatnonzero(train)
    rows = np.random.default_rng(0).choice(rows, size=min(len(rows), max_train), replace=False)

    out: dict[str, float] = {"drivers": len(classes), "chance": 1 / len(classes)}
    models = {
        "logreg": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
        "gbm": HistGradientBoostingClassifier(max_iter=200, random_state=0),
    }
    for name, model in models.items():
        model.fit(x[rows], drivers[rows])
        proba = np.clip(model.predict_proba(x[test]), 1e-9, 1)
        order = np.argsort(-proba, axis=1)
        truth = np.searchsorted(model.classes_, drivers[test])
        out[f"{name}_top1"] = float((order[:, 0] == truth).mean())
        out[f"{name}_top5"] = float((order[:, :5] == truth[:, None]).any(axis=1).mean())
        out[f"{name}_lap_top1"] = _lap_accuracy(
            np.log(proba), frame["lap_id"].to_numpy()[test], drivers[test], model.classes_
        )
    return out


def teammate_probe(
    frame: pd.DataFrame, split: pd.Series, x: np.ndarray | None = None
) -> dict[str, float]:
    """Which of two teammates (same car) took this corner? Chance is 50%."""
    x = _probe_features(frame) if x is None else x
    train, test = (split == "train").to_numpy(), (split == "test").to_numpy()
    seg_acc, lap_acc, pairs = [], [], []
    for team, members in frame.groupby("team").indices.items():
        rows = np.asarray(members)
        in_train = frame.iloc[rows[train[rows]]]["driver"].value_counts()
        pair = list(in_train.index[:2])
        if len(pair) < 2:
            continue
        m_train = train & frame["driver"].isin(pair).to_numpy()
        m_test = test & frame["driver"].isin(pair).to_numpy()
        if frame.loc[m_test, "driver"].nunique() < 2:
            continue
        y = frame["driver"].to_numpy()
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
        model.fit(x[m_train], y[m_train])
        proba = np.clip(model.predict_proba(x[m_test]), 1e-9, 1)
        seg_acc.append(float((model.classes_[proba.argmax(axis=1)] == y[m_test]).mean()))
        lap_acc.append(
            _lap_accuracy(
                np.log(proba), frame["lap_id"].to_numpy()[m_test], y[m_test], model.classes_
            )
        )
        pairs.append(f"{team}: {'/'.join(sorted(pair))}")
    return {
        "pairs": len(pairs),
        "segment_accuracy": float(np.mean(seg_acc)) if seg_acc else float("nan"),
        "segment_accuracy_sd": float(np.std(seg_acc)) if seg_acc else float("nan"),
        "lap_accuracy": float(np.mean(lap_acc)) if lap_acc else float("nan"),
        "chance": 0.5,
    }
