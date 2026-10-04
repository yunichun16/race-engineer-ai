"""Run every baseline, write report/baselines.md and log the run to MLflow."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from race_engineer.config import REPORT_DIR
from race_engineer.data.qa import markdown_table
from race_engineer.evaluation.data import build_features, load_eval_frame
from race_engineer.evaluation.metrics import evaluate
from race_engineer.evaluation.splits import assign_split, resolve_scheme
from race_engineer.evaluation.tracking import mlflow_experiment
from race_engineer.models.baselines import DETECTORS, driver_probe, teammate_probe

log = logging.getLogger(__name__)

SPLIT_NOTES = {
    "temporal": "train 2022-2024, validate 2025, test 2026 (the new regulations).",
    "by_event": (
        "whole events assigned at random (60/20/20). Provisional: used until older seasons "
        "are processed and the temporal split is possible."
    ),
    "leave_tracks_out": "whole circuits held out (60/20/20).",
}


def _fmt_ci(record: dict, metric: str) -> str:
    value, lo, hi = (float(record[k]) for k in (metric, f"{metric}_lo", f"{metric}_hi"))
    return f"{value:.3f} [{lo:.3f}, {hi:.3f}]"


def detector_table(results: pd.DataFrame, scope: str) -> pd.DataFrame:
    part = results[results["scope"] == scope]
    return pd.DataFrame(
        {
            "detector": part["detector"],
            "AUROC [95% CI]": [_fmt_ci(r, "auroc") for r in part.to_dict("records")],
            "avg precision [95% CI]": [
                _fmt_ci(r, "avg_precision") for r in part.to_dict("records")
            ],
            "P@50": part["p@50"].round(3),
            "P@200": part["p@200"].round(3),
            "recall@1%": part["recall@1%"].round(3),
            "recall@5%": part["recall@5%"].round(3),
        }
    )


def run(scheme: str = "auto", log_mlflow: bool = True, bootstrap: int = 200) -> Path:
    new = build_features()
    log.info("features computed for %d new sessions", new)
    frame = load_eval_frame()
    scheme = resolve_scheme(frame, scheme)
    split = assign_split(frame, scheme)
    train = (split == "train").to_numpy()
    events = (frame["year"].astype(str) + "-" + frame["round"].astype(str)).to_numpy()
    labelled = frame["label"].to_numpy() >= 0
    y = frame["label"].to_numpy() == 1
    log.info("%d eligible segments, %d labelled mistakes, split=%s", len(frame), y.sum(), scheme)

    rows = []
    for detector_cls in DETECTORS:
        detector = detector_cls().fit(frame, train)
        score = detector.score(frame)
        for scope, mask in {
            "test": labelled & (split == "test").to_numpy(),
            "all": labelled,
        }.items():
            rows.append(
                {
                    "detector": detector.name,
                    "scope": scope,
                    **evaluate(y[mask], score[mask], events[mask], bootstrap),
                }
            )
        log.info("scored %s", detector.name)
    results = pd.DataFrame(rows)
    driver = driver_probe(frame, split)
    teammates = teammate_probe(frame, split)

    data = (
        frame.assign(split=split, mistake=y, event=events)
        .groupby("split")
        .agg(
            events=("event", "nunique"),
            segments=("segment_id", "size"),
            labelled_mistakes=("mistake", "sum"),
        )
        .reindex(["train", "val", "test"])
        .reset_index()
    )
    path = write_report(scheme, data, results, driver, teammates)

    if log_mlflow:
        mlflow = mlflow_experiment("baselines")
        with mlflow.start_run(run_name=f"baselines-{scheme}"):
            mlflow.log_params({"split": scheme, "segments": len(frame), "mistakes": int(y.sum())})
            for record in results.to_dict("records"):
                prefix = f"{record['scope']}.{record['detector']}"
                mlflow.log_metrics(
                    {f"{prefix}.{_metric_name(str(k))}": float(v) for k, v in record.items()
                     if k not in ("scope", "detector") and np.isfinite(v)}
                )  # fmt: skip
            mlflow.log_metrics({f"driver_probe.{k}": float(v) for k, v in driver.items()})
            mlflow.log_metrics({f"teammate_probe.{k}": float(v) for k, v in teammates.items()})
            mlflow.log_artifact(str(path))
    return path


def _metric_name(name: str) -> str:
    """MLflow metric names can't contain '@' or '%'."""
    return name.replace("@", "_at_").replace("%", "pct")


def write_report(
    scheme: str, data: pd.DataFrame, results: pd.DataFrame, driver: dict, teammates: dict
) -> Path:
    probe = pd.DataFrame(
        [
            {"model": name, "top-1": driver[f"{name}_top1"], "top-5": driver[f"{name}_top5"],
             "top-1 (whole lap votes)": driver[f"{name}_lap_top1"]}
            for name in ("logreg", "gbm")
        ]
    ).round(3)  # fmt: skip
    parts = [
        "# Baselines",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `race-engineer-eval baselines`._",
        f"**Split:** {SPLIT_NOTES[scheme]}",
        markdown_table(data),
        "## Mistake detection",
        "Ranking corner segments by how unusual they are for that driver at that corner, "
        "scored against track-limits violations named by race control (single-corner, not "
        "clustered). Mistakes are rare and many are unlabelled, so compare detectors with each "
        "other rather than against a perfect score. Intervals come from resampling whole events.",
        "### Test events",
        *(
            [
                f"Only {n_test} test events, so these intervals are rough; the all-events table "
                "below is more reliable until more seasons are processed."
            ]
            if (n_test := int(data.loc[data["split"] == "test", "events"].iloc[0])) < 8
            else []
        ),
        markdown_table(detector_table(results, "test")),
        "### All events",
        "Detectors never see labels, so scoring every event is also fair; it gives tighter "
        "intervals. (Isolation Forest and PCA were fit on the training events only.)",
        markdown_table(detector_table(results, "all")),
        f"Base rate: {results['base_rate'].iloc[0]:.4f} (about 1 labelled mistake per "
        f"{1 / max(results['base_rate'].iloc[0], 1e-9):,.0f} segments).",
        "## Driver style probes",
        f"**Which driver took this corner?** {driver['drivers']} drivers, chance "
        f"{driver['chance']:.3f}. Features are normalised per session and corner, so track "
        "and corner differences are removed.",
        markdown_table(probe),
        f"**Which teammate took this corner?** Same car, so only the driver differs. "
        f"{teammates['pairs']} teammate pairs, chance 0.5: segment accuracy "
        f"{teammates['segment_accuracy']:.3f} (sd {teammates['segment_accuracy_sd']:.3f} across "
        f"pairs), whole-lap accuracy {teammates['lap_accuracy']:.3f}.",
    ]
    path = REPORT_DIR / "baselines.md"
    path.write_text("\n\n".join(parts) + "\n")
    return path
