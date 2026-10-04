"""Evaluate a trained Telemetry Transformer against the baselines.

    uv run race-engineer-eval deep                       # newest checkpoint
    uv run race-engineer-eval deep --checkpoint models/mae/<run>.pt

Mistake score: every corner is masked five different ways so each 40 m patch is predicted
once without seeing itself; the score is the mean negative log-likelihood of what actually
happened. It's reported raw and relative to the driver's own laps at that corner (as the
baselines are). Style: the [CLS] embedding goes through the same driver and teammate probes
as the hand-crafted features.

`evaluate_checkpoint` does the work for both this report and the ablation runner
(models/experiments.py), which uses its fast settings.

The split is the one the checkpoint trained with (saved in it), not one recomputed from
today's data: by_event reshuffles every event when one is added and "auto" changes scheme as
older seasons land, which would put training events into validation and test.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from race_engineer.config import REPORT_DIR
from race_engineer.data.qa import markdown_table
from race_engineer.evaluation.baselines import SPLIT_NOTES, detector_table
from race_engineer.evaluation.data import load_eval_frame
from race_engineer.evaluation.metrics import METRICS, evaluate
from race_engineer.evaluation.splits import assign_split, resolve_scheme
from race_engineer.evaluation.tracking import mlflow_experiment
from race_engineer.models.baselines import (
    DETECTORS,
    DRIVER_CORNER,
    _lap_accuracy,
    driver_probe,
    driver_relative_z,
    teammate_probe,
)
from race_engineer.models.mae import (
    MAEConfig,
    MaskedAutoencoder,
    build_model,
    gaussian_nll,
    model_inputs,
    point_mask,
)
from race_engineer.models.tensors import TelemetryData, load_or_build_tensors
from race_engineer.models.train import (
    CHECKPOINTS,
    event_keys,
    pick_device,
    split_from_events,
    take,
    to_device,
)

log = logging.getLogger(__name__)


def load_checkpoint(path: Path, device: torch.device) -> tuple[MaskedAutoencoder, dict]:
    state = torch.load(path, map_location=device, weights_only=False)
    model = build_model(MAEConfig.from_dict(state["model_cfg"])).to(device).eval()
    model.load_state_dict(state["model"])
    return model, state


def latest_checkpoint() -> Path:
    runs = sorted(CHECKPOINTS.glob("*.pt"), key=lambda p: p.stat().st_mtime)
    if not runs:
        raise FileNotFoundError("no checkpoints yet: run `race-engineer-train` first")
    return runs[-1]


# Ways of turning reconstructions into a mistake score. `mae_nll` was planned up front; the
# others came from looking at what the model gets wrong, so the report picks among them on the
# validation events only and says so.
PLANNED_SCORE = "mae_nll"
EXIT = slice(50, 80)  # from the apex to 150 m after it


@torch.no_grad()
def score_and_embed(
    model: MaskedAutoencoder,
    data: TelemetryData,
    device: torch.device,
    n_masks: int = 5,
    batch: int = 4096,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Mistake scores (several variants) and the style embedding for every segment.

    Each 40 m patch is predicted once without seeing itself (5 complementary masks). Errors
    cover the channels the model reconstructs; variants needing a channel it doesn't have are
    skipped. With the "mse" objective the variance head is untrained, so `mae_nll` assumes
    unit variance: exactly half of `mae_error`. `mae_error` then comes first, so a tie on
    validation goes to it rather than to a score that only looks like the planned one.
    """
    arrays = to_device(data, device)
    cfg = model.cfg
    channels = cfg.signal_channels
    exit_channels = [channels.index(c) for c in ("speed", "offset_m") if c in channels]
    speed = channels.index("speed") if "speed" in channels else None
    patches = torch.arange(cfg.n_patches, device=device)
    masks = [(patches % n_masks) == k for k in range(n_masks)]
    names = ["mae_error", "mae_nll"] if cfg.objective == "mse" else ["mae_nll", "mae_error"]
    names += ["mae_error_exit"] if exit_channels else []
    names += ["mae_error_speed_patch"] if speed is not None else []
    scores = {name: np.empty(len(data), np.float32) for name in names}
    embeddings = np.empty((len(data), cfg.embed_dim), np.float32)
    for start in range(0, len(data), batch):
        idx = torch.arange(start, min(start + batch, len(data)), device=device)
        b = model_inputs(cfg, take(arrays, idx))  # only the channels this model reconstructs
        signal = b["signal"]
        inputs = (signal, b["context_channels"], b["context_cat"], b["context_num"])
        n = len(idx)
        nll = torch.zeros_like(signal)
        error = torch.zeros_like(signal)
        for m in masks:
            mask = m.expand(n, -1)
            mean, logvar, _ = model(*inputs, mask)
            if cfg.objective == "mse":
                logvar = torch.zeros_like(mean)
            hidden = point_mask(mask, cfg.patch).expand_as(mean)
            nll += gaussian_nll(signal, mean, logvar) * hidden
            error += (signal - mean) ** 2 * hidden
        batch_scores = {
            "mae_nll": nll.mean(dim=(1, 2)),  # surprise, weighted by the model's own uncertainty
            "mae_error": error.mean(dim=(1, 2)),  # plain reconstruction error
        }
        if exit_channels:
            batch_scores["mae_error_exit"] = error[:, exit_channels, EXIT].mean(dim=(1, 2))
        if speed is not None:  # worst 40 m of speed
            speed_patches = error[:, speed].reshape(n, cfg.n_patches, cfg.patch).mean(dim=2)
            batch_scores["mae_error_speed_patch"] = speed_patches.max(dim=1).values
        for name, value in batch_scores.items():
            scores[name][start : start + n] = value.cpu().numpy()
        none_hidden = torch.zeros(n, cfg.n_patches, dtype=torch.bool, device=device)
        _, _, emb = model(*inputs, none_hidden)
        embeddings[start : start + n] = emb.cpu().numpy()
    return scores, embeddings


def safe_evaluate(
    y: np.ndarray, score: np.ndarray, groups: np.ndarray, bootstrap: int
) -> dict[str, float]:
    """evaluate(), or NaNs when the rows hold no mistakes (or nothing else), e.g. a subsample."""
    if 0 < y.sum() < len(y):
        return evaluate(y, score, groups, bootstrap)
    empty = {name: float("nan") for name in METRICS}
    for name in ("auroc", "avg_precision"):
        empty[f"{name}_lo"] = empty[f"{name}_hi"] = float("nan")
    return {**empty, "positives": float(y.sum()), "base_rate": float(y.mean()) if len(y) else 0.0}


def choose_on_validation(scores: dict[str, np.ndarray], y: np.ndarray, val: np.ndarray) -> str:
    """The score variant with the best average precision on labelled validation events (the
    first of equals, in the order of `scores`)."""
    if not 0 < y[val].sum() < val.sum():
        log.warning("no labelled mistakes in the validation rows: reporting %s", PLANNED_SCORE)
        return PLANNED_SCORE
    return max(scores, key=lambda name: METRICS["avg_precision"](y[val], scores[name][val]))


def driver_probe_logreg(
    frame: pd.DataFrame, split: pd.Series, x: np.ndarray, max_train: int = 100_000
) -> dict[str, float]:
    """The logistic-regression half of driver_probe (same classes and sample): quicker, for
    comparing models with each other."""
    drivers = frame["driver"].to_numpy()
    train, test = (split == "train").to_numpy(), (split == "test").to_numpy()
    classes = np.array(sorted(set(drivers[train]) & set(drivers[test])))
    if len(classes) < 2:
        nan = float("nan")
        return {"drivers": len(classes), "chance": nan, "logreg_top1": nan, "logreg_top5": nan,
                "logreg_lap_top1": nan}  # fmt: skip
    train &= np.isin(drivers, classes)
    test &= np.isin(drivers, classes)
    rows = np.flatnonzero(train)
    rows = np.random.default_rng(0).choice(rows, size=min(len(rows), max_train), replace=False)
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    model.fit(x[rows], drivers[rows])
    proba = np.clip(model.predict_proba(x[test]), 1e-9, 1)
    order = np.argsort(-proba, axis=1)
    truth = np.searchsorted(model.classes_, drivers[test])
    return {
        "drivers": len(classes),
        "chance": 1 / len(classes),
        "logreg_top1": float((order[:, 0] == truth).mean()),
        "logreg_top5": float((order[:, :5] == truth[:, None]).any(axis=1).mean()),
        "logreg_lap_top1": _lap_accuracy(
            np.log(proba), frame["lap_id"].to_numpy()[test], drivers[test], model.classes_
        ),
    }


def embedding_label(cfg: MAEConfig) -> str:
    return "CNN embedding" if cfg.model_type == "cnn" else "Transformer embedding"


def evaluate_checkpoint(
    model: MaskedAutoencoder,
    data: TelemetryData,
    split: pd.Series,
    device: torch.device,
    bootstrap: int = 200,
    baselines: bool = False,
    fast: bool = False,
    probe_max_train: int = 100_000,
) -> dict:
    """Mistake metrics (validation, test and all events) and style probes for one model.

    The reported score variant is chosen by average precision on labelled validation events
    only. `baselines` adds the baseline detectors (needs hand-crafted features and the
    processed segments). `fast` is for comparing many models: metrics only for the chosen and
    planned scores, intervals on the test events only, and logistic-regression probes on the
    embedding alone.

    Returns chosen, scores, embeddings, results (one row per detector and scope) and probes
    ({features: (driver probe, teammate probe)}).
    """
    frame = data.frame
    scores, embeddings = score_and_embed(model, data, device)
    keys = frame[DRIVER_CORNER]  # not the whole frame: assign() copies what it's given
    for name in list(scores):  # each variant also relative to the driver's own laps
        z = driver_relative_z(keys.assign(s=scores[name]), ("s",), {"s": 1e-3})
        scores[f"{name}_vs_driver"] = np.nan_to_num(z[:, 0])
    split_values = split.to_numpy()
    labelled = frame["label"].to_numpy() >= 0
    y = frame["label"].to_numpy() == 1
    chosen = choose_on_validation(scores, y, labelled & (split_values == "val"))
    log.info("score chosen on validation events: %s", chosen)

    keep = dict.fromkeys([PLANNED_SCORE, chosen]) if fast else scores
    reported = {name: scores[name] for name in keep}
    if baselines:
        train = split_values == "train"
        for detector_cls in DETECTORS:
            detector = detector_cls().fit(frame, train)
            reported[detector.name] = detector.score(frame)
    events = (frame["year"].astype(str) + "-" + frame["round"].astype(str)).to_numpy()
    scopes = {
        "val": (labelled & (split_values == "val"), 0),  # optimistic for the chosen score
        "test": (labelled & (split_values == "test"), bootstrap),
        "all": (labelled, 0 if fast else bootstrap),
    }
    rows = []
    for name, score in reported.items():
        for scope, (mask, n_boot) in scopes.items():
            rows.append(
                {
                    "detector": name,
                    "scope": scope,
                    **safe_evaluate(y[mask], score[mask], events[mask], n_boot),
                }
            )

    label = embedding_label(model.cfg)
    if fast:
        probes = {
            label: (
                driver_probe_logreg(frame, split, embeddings, probe_max_train),
                teammate_probe(frame, split, embeddings),
            )
        }
    else:
        probes = {
            "hand-crafted features": (driver_probe(frame, split), teammate_probe(frame, split)),
            label: (
                driver_probe(frame, split, embeddings),
                teammate_probe(frame, split, embeddings),
            ),
        }
    return {
        "chosen": chosen,
        "scores": scores,
        "embeddings": embeddings,
        "results": pd.DataFrame(rows),
        "probes": probes,
    }


def checkpoint_split(
    state: dict, frame: pd.DataFrame, scheme: str = "auto"
) -> tuple[pd.Series, str, list[str]]:
    """The split `state`'s model trained with, applied to `frame`: (split, scheme, notes).

    Events processed since training are test events. Checkpoints saved before splits were
    recorded fall back to recomputing it, and the notes say the numbers can't be trusted.
    """
    trained = state.get("split")
    if trained is not None and scheme not in ("auto", trained):
        raise ValueError(
            f"this checkpoint trained on the {trained} split, so it can't be evaluated on "
            f"{scheme}: leave the split as auto, or train a model on {scheme}"
        )
    saved = state.get("split_events")
    if saved is None:
        scheme = trained or resolve_scheme(frame, scheme)
        note = (
            "**Warning:** this checkpoint predates saved training splits, so its split was "
            "recomputed from today's data; events it trained on may now count as validation or "
            "test. Retrain it for trustworthy numbers."
        )
        log.warning(note)
        return assign_split(frame, scheme), scheme, [note]
    seen = {event for events in saved.values() for event in events}
    new = sorted(set(event_keys(frame)) - seen)
    notes = []
    if new:
        notes.append(f"{len(new)} event(s) processed since training count as test events.")
        log.info("%d events processed since training go to test: %s", len(new), ", ".join(new))
    return split_from_events(frame, saved), str(trained), notes


def run(
    checkpoint: Path | None = None,
    scheme: str = "auto",
    log_mlflow: bool = True,
    bootstrap: int = 200,
    report: Path | None = None,
    refresh: bool = False,
) -> Path:
    device = pick_device()
    checkpoint = checkpoint or latest_checkpoint()
    model, state = load_checkpoint(checkpoint, device)
    data = load_or_build_tensors(load_eval_frame(), refresh=refresh)
    split, scheme, notes = checkpoint_split(state, data.frame, scheme)
    out = evaluate_checkpoint(model, data, split, device, bootstrap=bootstrap, baselines=True)
    log.info("scored %d segments with %s", len(data), checkpoint.name)
    results, probes, chosen = out["results"], out["probes"], out["chosen"]
    path = write_report(
        scheme,
        checkpoint,
        state,
        results,
        probes,
        chosen,
        report or REPORT_DIR / "m3_results.md",
        notes,
    )
    if log_mlflow:
        mlflow = mlflow_experiment("mae")
        with mlflow.start_run(run_name=f"eval-{checkpoint.stem}"):
            mlflow.log_param("checkpoint", str(checkpoint))
            for r in results[results["scope"] == "all"].to_dict("records"):
                mlflow.log_metrics(
                    {
                        f"all.{r['detector']}.auroc": r["auroc"],
                        f"all.{r['detector']}.avg_precision": r["avg_precision"],
                    }
                )
            for name, (drv, team) in probes.items():
                key = name.split()[0].replace("-", "_").lower()
                mlflow.log_metrics(
                    {
                        f"{key}.driver_lap_top1": drv["gbm_lap_top1"],
                        f"{key}.teammate_segment": team["segment_accuracy"],
                    }
                )
            mlflow.log_artifact(str(path))
    return path


def write_report(
    scheme: str,
    checkpoint: Path,
    state: dict,
    results: pd.DataFrame,
    probes: dict,
    chosen: str,
    path: Path,
    notes: list[str] | None = None,
) -> Path:
    labels = {
        PLANNED_SCORE: f"{PLANNED_SCORE} (planned)",
        chosen: f"{chosen} (chosen on validation)",
    }
    results = results.assign(detector=results["detector"].map(lambda d: labels.get(d, d)))
    headline = results[
        results["detector"].isin([*labels.values(), "isolation_forest", "z_score", "time_loss"])
    ]
    probe_rows = []
    for name, (drv, team) in probes.items():
        probe_rows.append(
            {
                "features": name,
                "driver top-1 (logreg)": round(drv["logreg_top1"], 3),
                "driver top-1 (gbm)": round(drv["gbm_top1"], 3),
                "driver top-5 (gbm)": round(drv["gbm_top5"], 3),
                "driver, lap votes (gbm)": round(drv["gbm_lap_top1"], 3),
                "teammate (segment)": round(team["segment_accuracy"], 3),
                "teammate (lap)": round(team["lap_accuracy"], 3),
            }
        )
    drv = next(iter(probes.values()))[0]
    objective = MAEConfig.from_dict(state["model_cfg"]).objective
    if objective == "mse":
        planned = (
            "This model was trained on plain squared error (objective `mse`), so it has no "
            "uncertainty estimate: the planned score `mae_nll` assumes unit variance, is exactly "
            "half of `mae_error` and ranks segments the same way. "
        )
    else:
        planned = (
            "The planned score, `mae_nll`, is the model's surprise at what actually happened "
            "(negative log-likelihood, weighted by its own predicted uncertainty). It turned out "
            "to be a weak detector: the model learns that corner exits are naturally variable, "
            "so it discounts exactly where most mistakes happen. Plain reconstruction error "
            "works better. "
        )
    parts = [
        "# Telemetry Transformer: first results",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `race-engineer-eval deep`._",
        f"**Checkpoint:** `{checkpoint.name}` (epoch {state['epoch']}, "
        f"validation {objective.upper()} {state['val_loss']:.4f}). "
        f"**Split:** {SPLIT_NOTES[scheme]}",
        *(notes or []),
        "## Mistake detection",
        planned + f"Several variants were tried; `{chosen}` was **chosen on the validation "
        "events only**, and is reported here on test and all events next to the planned score "
        "and the best baselines.",
        "### All events",
        markdown_table(detector_table(headline, "all")),
        "### Test events",
        markdown_table(detector_table(headline, "test")),
        "## Driver style probes",
        f"Chance: {drv['chance']:.3f} for {drv['drivers']} drivers, 0.5 for teammates. The "
        "embedding comes from a model that never saw driver or team labels.",
        markdown_table(pd.DataFrame(probe_rows)),
        "## All score variants (all events)",
        "`mae_error`: mean squared reconstruction error. `mae_error_exit`: speed and line "
        "offset from the apex to 150 m after it. `mae_error_speed_patch`: the worst 40 m of "
        "speed. `_vs_driver`: relative to the driver's own laps at that corner.",
        markdown_table(detector_table(results, "all")),
    ]
    path.write_text("\n\n".join(parts) + "\n")
    return path
