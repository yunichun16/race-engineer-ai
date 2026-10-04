"""Metrics for ranking mistakes, with confidence intervals from an event-level bootstrap.

Mistakes are rare (about 1 segment in 1,000 carries a label), so average precision and
precision in the top k matter more than AUROC. Many real mistakes are unlabelled, which pushes
every number down: compare detectors with each other, not against 1.0.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

Metric = Callable[[np.ndarray, np.ndarray], float]


def precision_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float:
    top = np.argsort(-score, kind="stable")[:k]
    return float(y[top].mean()) if len(top) else float("nan")


def recall_at_fraction(y: np.ndarray, score: np.ndarray, fraction: float) -> float:
    """Share of labelled mistakes that land in the top `fraction` of scores."""
    k = max(1, round(fraction * len(score)))
    top = np.argsort(-score, kind="stable")[:k]
    return float(y[top].sum() / max(y.sum(), 1))


METRICS: dict[str, Metric] = {
    "auroc": lambda y, s: float(roc_auc_score(y, s)),
    "avg_precision": lambda y, s: float(average_precision_score(y, s)),
    "p@50": lambda y, s: precision_at_k(y, s, 50),
    "p@200": lambda y, s: precision_at_k(y, s, 200),
    "recall@1%": lambda y, s: recall_at_fraction(y, s, 0.01),
    "recall@5%": lambda y, s: recall_at_fraction(y, s, 0.05),
}


def cluster_bootstrap(
    y: np.ndarray,
    score: np.ndarray,
    groups: np.ndarray,
    metric: Metric,
    n: int = 200,
    seed: int = 0,
) -> tuple[float, float]:
    """95% interval, resampling whole events (segments within an event are correlated)."""
    rng = np.random.default_rng(seed)
    unique, inverse = np.unique(groups, return_inverse=True)
    members = [np.flatnonzero(inverse == g) for g in range(len(unique))]
    values = []
    for _ in range(n):
        idx = np.concatenate([members[g] for g in rng.integers(0, len(unique), len(unique))])
        if 0 < y[idx].sum() < len(idx):
            values.append(metric(y[idx], score[idx]))
    if not values:
        return float("nan"), float("nan")
    lo, hi = np.percentile(values, [2.5, 97.5])
    return float(lo), float(hi)


def evaluate(
    y: np.ndarray, score: np.ndarray, groups: np.ndarray, bootstrap: int = 200
) -> dict[str, float]:
    """All metrics, plus 95% intervals for AUROC and average precision."""
    out = {name: fn(y, score) for name, fn in METRICS.items()}
    for name in ("auroc", "avg_precision"):
        lo, hi = cluster_bootstrap(y, score, groups, METRICS[name], n=bootstrap)
        out[f"{name}_lo"], out[f"{name}_hi"] = lo, hi
    out["positives"] = float(y.sum())
    out["base_rate"] = float(y.mean())
    return out
