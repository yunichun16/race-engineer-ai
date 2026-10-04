import numpy as np
import pandas as pd

from race_engineer.evaluation.data import IGNORE, NEGATIVE, POSITIVE, assign_labels
from race_engineer.evaluation.metrics import cluster_bootstrap, precision_at_k, recall_at_fraction
from race_engineer.evaluation.splits import assign_split, resolve_scheme


def test_ranking_metrics() -> None:
    y = np.array([1, 0, 1, 0, 0, 0, 0, 0, 0, 0])
    score = np.array([0.9, 0.8, 0.7, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 0.0])
    assert precision_at_k(y, score, 2) == 0.5
    assert precision_at_k(y, score, 3) == 2 / 3
    assert recall_at_fraction(y, score, 0.1) == 0.5  # top 1 of 10 holds one of two mistakes


def test_bootstrap_interval_contains_the_estimate() -> None:
    rng = np.random.default_rng(1)
    y = rng.random(2000) < 0.05
    score = y + rng.normal(0, 0.7, 2000)
    groups = np.repeat(np.arange(20), 100)
    lo, hi = cluster_bootstrap(y, score, groups, lambda a, b: float(np.corrcoef(a, b)[0, 1]), n=100)
    assert lo < np.corrcoef(y, score)[0, 1] < hi


def frame_for_splits() -> pd.DataFrame:
    rows = [(y, r, f"track{r % 7}") for y in (2023, 2024, 2025, 2026) for r in range(1, 11)]
    return pd.DataFrame(rows * 3, columns=["year", "round", "location"])


def test_splits_never_share_an_event_or_track() -> None:
    frame = frame_for_splits()
    for scheme, key in (("by_event", ["year", "round"]), ("leave_tracks_out", ["location"])):
        split = assign_split(frame, scheme)
        assert set(split) == {"train", "val", "test"}
        assert frame.assign(split=split).groupby(key)["split"].nunique().max() == 1


def test_temporal_split_is_chosen_once_older_seasons_exist() -> None:
    frame = frame_for_splits()
    assert resolve_scheme(frame, "auto") == "temporal"
    assert resolve_scheme(frame[frame["year"] == 2026], "auto") == "by_event"
    split = assign_split(frame, "temporal")
    assert set(split[frame["year"] == 2025]) == {"val"} and set(split[frame["year"] == 2026]) == {
        "test"
    }


def test_labels() -> None:
    lap = {"year": 2026, "round": 1, "session": "R", "driver": "NOR"}
    frame = pd.DataFrame(
        [
            {**lap, "lap_number": 10, "corner": 1, "apex_m": 300.0},  # labelled corner
            {**lap, "lap_number": 10, "corner": 2, "apex_m": 420.0},  # overlapping window
            {**lap, "lap_number": 10, "corner": 3, "apex_m": 1500.0},  # far away: negative
            {**lap, "lap_number": 20, "corner": 5, "apex_m": 2500.0},  # clustered event
            {**lap, "lap_number": 30, "corner": 3, "apex_m": 1500.0},  # incident lap
            {**lap, "lap_number": 29, "corner": 3, "apex_m": 1500.0},  # lap before an incident
        ]
    )
    events = pd.DataFrame(
        [
            {
                **lap,
                "lap_number": 10,
                "turn": 1,
                "kind": "track_limits",
                "drivers_same_lap_turn": 1,
            },
            {
                **lap,
                "lap_number": 20,
                "turn": 5,
                "kind": "track_limits",
                "drivers_same_lap_turn": 6,
            },
            {**lap, "lap_number": 30, "turn": -1, "kind": "incident", "drivers_same_lap_turn": 1},
        ]
    )
    assert assign_labels(frame, events).tolist() == [
        POSITIVE,
        IGNORE,
        NEGATIVE,
        IGNORE,
        IGNORE,
        IGNORE,
    ]


def test_auto_waits_for_enough_older_seasons() -> None:
    frame = frame_for_splits()
    partial = pd.concat([frame[frame["year"] >= 2025], frame[frame["year"] == 2024].head(3)])
    assert resolve_scheme(partial, "auto") == "by_event"  # a few 2024 sessions are not enough
    assert resolve_scheme(frame, "auto") == "temporal"
