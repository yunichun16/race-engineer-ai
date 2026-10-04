"""Train / validation / test splits. Whole events stay together, so nothing leaks between them.

- temporal: train 2022-2024, validate 2025, test 2026 (the new regulations). The main split.
- by_event: events assigned at random, 60/20/20. Used while only a season or two are processed.
- leave_tracks_out: whole circuits held out, to test generalisation to unseen tracks.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SCHEMES = ("auto", "temporal", "by_event", "leave_tracks_out")


def _assign_groups(
    groups: pd.Series, seed: int, fractions: tuple[float, float] = (0.6, 0.2)
) -> pd.Series:
    unique = np.array(sorted(groups.unique()), dtype=object)
    order = np.random.default_rng(seed).permutation(len(unique))
    n_train = round(fractions[0] * len(unique))
    n_val = max(1, round(fractions[1] * len(unique)))
    names = np.empty(len(unique), dtype=object)
    names[order[:n_train]] = "train"
    names[order[n_train : n_train + n_val]] = "val"
    names[order[n_train + n_val :]] = "test"
    return groups.map(dict(zip(unique, names, strict=True)))


# "auto" only switches to the temporal split once 2022-2024 hold this share of the segments.
# During a partial download a handful of 2024 sessions would otherwise become a tiny training set.
MIN_TEMPORAL_TRAIN_SHARE = 0.4


def resolve_scheme(frame: pd.DataFrame, scheme: str) -> str:
    if scheme != "auto":
        return scheme
    year = frame["year"]
    ready = (
        (year <= 2024).mean() >= MIN_TEMPORAL_TRAIN_SHARE
        and (year == 2025).any()
        and (year == 2026).any()
    )
    return "temporal" if ready else "by_event"


def assign_split(frame: pd.DataFrame, scheme: str = "auto", seed: int = 0) -> pd.Series:
    """'train', 'val' or 'test' for every row."""
    scheme = resolve_scheme(frame, scheme)
    if scheme == "temporal":
        year = frame["year"]
        return pd.Series(
            np.where(year <= 2024, "train", np.where(year == 2025, "val", "test")),
            index=frame.index,
        )
    if scheme == "by_event":
        return _assign_groups(frame["year"].astype(str) + "-" + frame["round"].astype(str), seed)
    if scheme == "leave_tracks_out":
        return _assign_groups(frame["location"], seed)
    raise ValueError(f"unknown split scheme {scheme!r}; choose from {SCHEMES}")
