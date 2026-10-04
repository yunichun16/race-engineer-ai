"""Ablation runner: train and quickly evaluate a grid of model variants described in TOML.

    uv run race-engineer-experiments --grid configs/ablations.toml        # make experiments
    uv run race-engineer-experiments --grid configs/smoke.toml            # make experiments-smoke
    uv run race-engineer-experiments --grid configs/ablations.toml --only cnn mask_0.7
    uv run race-engineer-experiments --grid configs/ablations.toml --dry-run
    uv run race-engineer-experiments --export-bundle ../bundle    # then, on a cloud GPU:
    uv run race-engineer-experiments --grid configs/ablations.toml --bundle ../bundle

A grid file has an optional top-level `report` (a file name under report/), a [base] table and
[[variant]] tables. Any MAEConfig, TrainConfig or EvalConfig field can go in either; a variant
is its `name` plus whatever differs from the base. When d_model is set and d_ff isn't, d_ff
follows at 2 x d_model, the ratio the Transformer was designed with.

Every variant is trained on the same data and split, then evaluated the fast way
(deep.evaluate_checkpoint with fast=True): the score variant chosen on validation events and the
planned `mae_nll`, on test and all events, plus logistic-regression driver and teammate probes
on the embedding. One row per variant goes to the report, rewritten after each variant so an
interrupted grid keeps what finished, and to the MLflow experiment "ablations".

Each run also saves its rows as results.json next to its checkpoints. A rerun of a few variants
(`--only`) rebuilds the report from those plus the newest earlier row of every other variant
(same grid and run prefix), and says which rows are older and whether their data differ.
Before training anything, the grid refuses a split whose training part is a sliver of the data
(TrainConfig.min_train_share), and the report records each split's size.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import time
import tomllib
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

from race_engineer.config import REPORT_DIR, repo_root
from race_engineer.data.qa import markdown_table
from race_engineer.evaluation.baselines import SPLIT_NOTES, _metric_name
from race_engineer.evaluation.data import build_features, load_eval_frame
from race_engineer.evaluation.deep import PLANNED_SCORE, evaluate_checkpoint, load_checkpoint
from race_engineer.evaluation.splits import assign_split, resolve_scheme
from race_engineer.evaluation.tracking import mlflow_experiment
from race_engineer.models.mae import MAEConfig
from race_engineer.models.tensors import (
    SIGNAL,
    TelemetryData,
    cache_key,
    export_bundle,
    load_bundle,
    load_or_build_tensors,
)
from race_engineer.models.train import (
    TrainConfig,
    check_split,
    describe_split,
    pick_device,
    split_summary,
    train,
)

log = logging.getLogger(__name__)
ABLATIONS_DIR = repo_root() / "models" / "ablations"
VARIANT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # also a checkpoint file name
RESULTS = "results.json"  # a run's rows, next to its checkpoints


@dataclass
class EvalConfig:
    bootstrap: int = 100  # event resamples for the test-event intervals
    eval_max_events: int | None = None  # whole events kept per split (smoke runs); None = all
    probe_max_train: int = 100_000  # segments the driver probe is fitted on


@dataclass
class Variant:
    name: str
    model: MAEConfig = field(default_factory=MAEConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)


@dataclass
class Grid:
    name: str
    report: str
    variants: list[Variant]


SETTINGS: dict[str, type] = {"model": MAEConfig, "train": TrainConfig, "eval": EvalConfig}


def make_variant(name: str, settings: dict[str, Any]) -> Variant:
    """A variant from flat settings, each routed to the config that owns that field."""
    owned = {group: {f.name for f in fields(cls)} for group, cls in SETTINGS.items()}
    unknown = set(settings) - set().union(*owned.values())
    if unknown:
        raise ValueError(f"variant {name!r}: unknown setting(s) {sorted(unknown)}")
    parts = {g: {k: v for k, v in settings.items() if k in names} for g, names in owned.items()}
    if "d_model" in parts["model"] and "d_ff" not in parts["model"]:
        parts["model"]["d_ff"] = 2 * parts["model"]["d_model"]
    return Variant(
        name=name,
        model=MAEConfig(**parts["model"]),
        train=TrainConfig(**parts["train"]),
        eval=EvalConfig(**parts["eval"]),
    )


def parse_grid(raw: dict[str, Any], name: str) -> Grid:
    unknown = set(raw) - {"report", "base", "variant"}
    if unknown:
        raise ValueError(f"grid {name!r}: unknown top-level key(s) {sorted(unknown)}")
    base = raw.get("base", {})
    variants = []
    for entry in raw.get("variant", []):
        variant_name = str(entry.get("name", ""))
        if not VARIANT_NAME.match(variant_name):
            raise ValueError(f"grid {name!r}: every variant needs a file-safe name, not {entry}")
        overrides = {k: v for k, v in entry.items() if k != "name"}
        variants.append(make_variant(variant_name, {**base, **overrides}))
    if not variants:
        raise ValueError(f"grid {name!r} has no [[variant]] tables")
    names = [v.name for v in variants]
    if len(set(names)) < len(names):
        raise ValueError(f"grid {name!r}: variant names repeat: {names}")
    default = "m3_ablations.md" if name == "ablations" else f"m3_ablations_{name}.md"
    return Grid(name=name, report=str(raw.get("report", default)), variants=variants)


def load_grid(path: Path) -> Grid:
    with path.open("rb") as f:
        return parse_grid(tomllib.load(f), path.stem)


def eval_rows(
    frame: pd.DataFrame, split: pd.Series, max_events: int | None, seed: int = 0
) -> np.ndarray:
    """Rows of up to `max_events` whole events from each split; every row when None.

    Whole events, so each driver's laps at a corner stay together (the driver-relative scores
    need them). The tensors were normalised on the full sessions before this subsample.
    """
    if max_events is None:
        return np.arange(len(frame))
    events = (frame["year"].astype(str) + "-" + frame["round"].astype(str)).to_numpy()
    split_values = split.to_numpy()
    rng = np.random.default_rng(seed)
    keep: list[str] = []
    for part in ("train", "val", "test"):
        names = np.unique(events[split_values == part])
        keep += list(rng.choice(names, size=min(len(names), max_events), replace=False))
    return np.flatnonzero(np.isin(events, keep))


def run_variant(
    variant: Variant, data: TelemetryData, checkpoint: Path, device: torch.device
) -> tuple[dict[str, Any], dict]:
    """Train one variant and evaluate it quickly: (summary row, checkpoint state)."""
    split = assign_split(data.frame, variant.train.split)
    scheme = resolve_scheme(data.frame, variant.train.split)
    log.info("variant %s: training", variant.name)
    started = time.time()
    path = train(
        variant.model,
        variant.train,
        data,
        split,
        log_mlflow=False,
        checkpoint=checkpoint,
        device=device,
    )
    train_seconds = time.time() - started
    if device.type == "mps":
        torch.mps.empty_cache()  # training's copy of the data; scoring makes its own

    model, state = load_checkpoint(path, device)
    rows = eval_rows(data.frame, split, variant.eval.eval_max_events)
    subset, sub_split = data, split.reset_index(drop=True)
    if len(rows) < len(data):
        subset, sub_split = data.subset(rows), split.iloc[rows].reset_index(drop=True)
    log.info("variant %s: evaluating on %d segments", variant.name, len(rows))
    started = time.time()
    out = evaluate_checkpoint(
        model,
        subset,
        sub_split,
        device,
        bootstrap=variant.eval.bootstrap,
        fast=True,
        probe_max_train=variant.eval.probe_max_train,
    )
    eval_seconds = time.time() - started

    results = out["results"].set_index(["detector", "scope"])
    driver, teammate = next(iter(out["probes"].values()))
    held_out = (sub_split != "train").to_numpy()
    cfg = variant.model
    row: dict[str, Any] = {
        "variant": variant.name,
        "model": cfg.model_type,
        "objective": cfg.objective,
        "context": cfg.use_context,
        "channels": channels_label(cfg.signal_channels),
        "d_model": cfg.d_model,
        "n_layers": cfg.n_layers,
        "mask_ratio": variant.train.mask_ratio,
        "epochs": variant.train.epochs,
        "best_epoch": state["epoch"],
        "params": state["params"],
        "split": scheme,
        "split_counts": split_summary(data.frame, split),
        "train_segments": state["train_segments"],
        "eval_segments": len(rows),
        "val_loss": state["val_loss"],
        "val_mse": state["val_mse"],
        "recon_mse": float(np.mean(out["scores"]["mae_error"][held_out])),
        **{
            f"recon_mse_{part}": float(
                np.mean(out["scores"]["mae_error"][(sub_split == part).to_numpy()])
            )
            for part in ("val", "test")
        },
        "chosen": out["chosen"],
    }
    for prefix, detector in (("chosen", out["chosen"]), ("planned", PLANNED_SCORE)):
        for scope in ("val", "test", "all"):
            record = results.loc[(detector, scope)]
            for key in ("avg_precision", "auroc", "avg_precision_lo", "avg_precision_hi"):
                row[f"{prefix}_{key}_{scope}"] = float(record[key])
    row |= {
        "drivers": driver["drivers"],
        "driver_top1": driver["logreg_top1"],
        "driver_top5": driver["logreg_top5"],
        "driver_lap_top1": driver["logreg_lap_top1"],
        "teammate_segment": teammate["segment_accuracy"],
        "teammate_lap": teammate["lap_accuracy"],
        "train_seconds": train_seconds,
        "eval_seconds": eval_seconds,
        "checkpoint": str(path),
    }
    return row, state


def channels_label(channels: tuple[str, ...]) -> str:
    missing = [c for c in SIGNAL if c not in channels]
    return "all" if not missing else "no " + ", ".join(missing)


def load_data(refresh: bool = False) -> TelemetryData:
    """The current evaluation dataset, including any sessions processed since the last run."""
    new = build_features()
    log.info("features computed for %d new sessions", new)
    return load_or_build_tensors(load_eval_frame(), refresh=refresh)


def check_splits(variants: list[Variant], frame: pd.DataFrame) -> None:
    """Log each split the variants use, and raise before any training if one is unusable."""
    for scheme in dict.fromkeys(v.train.split for v in variants):
        split = assign_split(frame, scheme)
        resolved = resolve_scheme(frame, scheme)
        min_share = min(v.train.min_train_share for v in variants if v.train.split == scheme)
        check_split(frame, split, resolved, min_share)
        log.info("%s split: %s", resolved, describe_split(split_summary(frame, split)))


def earlier_rows(checkpoint_root: Path, grid: str, prefix: str) -> dict[str, dict[str, Any]]:
    """The newest finished row per variant from earlier runs of `grid` under `prefix`."""
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(checkpoint_root.glob(f"*/{RESULTS}"), key=lambda p: p.stat().st_mtime):
        saved = json.loads(path.read_text())
        if saved.get("grid") == grid and saved.get("prefix") == prefix:
            rows |= {row["variant"]: row for row in saved.get("rows", [])}
    return rows


def merge_rows(
    grid: Grid, earlier: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """This run's rows and, for variants it didn't finish, earlier ones; plus report notes."""
    current = {r["variant"]: r for r in rows}
    shown = [r for v in grid.variants if (r := current.get(v.name) or earlier.get(v.name))]
    kept = [r for r in shown if r["variant"] not in current]
    if not kept:
        return shown, []
    names = ", ".join(f"`{r['variant']}`" for r in kept)
    runs = ", ".join(sorted({f"`{r.get('run', '?')}`" for r in kept}))
    notes = [f"Rows for {names} come from earlier runs of this grid ({runs}); the rest are new."]
    if len({r.get("dataset") for r in shown}) > 1:
        notes.append(
            "**Warning:** these rows were trained on different data (sessions were processed "
            "between runs), so their splits differ too: compare them with care."
        )
    return shown, notes


def write_results(
    path: Path, grid: Grid, prefix: str, rows: list[dict[str, Any]], failed: dict[str, str]
) -> None:
    value = {"grid": grid.name, "prefix": prefix, "rows": rows, "failed": failed}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=_to_python) + "\n")
    tmp.replace(path)


def _to_python(value: Any) -> Any:
    if isinstance(value, np.generic | np.ndarray):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def run_grid(
    grid: Grid,
    name_prefix: str | None = None,
    data: TelemetryData | None = None,
    only: list[str] | None = None,
    log_mlflow: bool = True,
    report_dir: Path = REPORT_DIR,
    checkpoint_root: Path = ABLATIONS_DIR,
    device: torch.device | None = None,
) -> tuple[Path, list[dict[str, Any]], dict[str, str]]:
    """Run every variant (or those named in `only`), rewriting the report after each one.

    Returns the report, one row per variant finished in this call, and {variant: error} for any
    that failed: a long unattended grid carries on past one bad variant (say, out of memory).
    With `only`, the report keeps the newest earlier row of every variant not rerun.
    """
    variants = grid.variants
    if only:
        missing = set(only) - {v.name for v in variants}
        if missing:
            raise ValueError(f"no variant(s) {sorted(missing)} in grid {grid.name!r}")
        variants = [v for v in variants if v.name in only]
    prefix = name_prefix or grid.name
    data = data if data is not None else load_data()
    check_splits(variants, data.frame)
    device = device or pick_device()
    earlier = earlier_rows(checkpoint_root, grid.name, prefix) if only else {}
    folder = checkpoint_root / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"
    dataset = cache_key(data.frame)
    mlflow = mlflow_experiment("ablations") if log_mlflow else None
    report = report_dir / grid.report
    log.info("grid %s: %d variants, %d segments -> %s", grid.name, len(variants), len(data), report)

    rows: list[dict[str, Any]] = []
    failed: dict[str, str] = {}

    def save() -> None:  # this run's results.json, and the report with any earlier rows
        write_results(folder / RESULTS, grid, prefix, rows, failed)
        shown, notes = merge_rows(grid, earlier, rows)
        write_report(grid, prefix, shown, report, failed, notes)

    for variant in variants:
        try:
            row, state = run_variant(variant, data, folder / f"{variant.name}.pt", device)
        except Exception as error:
            log.exception("variant %s failed", variant.name)
            failed[variant.name] = f"{type(error).__name__}: {error}".splitlines()[0][:300]
            save()
            continue
        row |= {"run": folder.name, "dataset": dataset}
        rows.append(row)
        log.info(
            "variant %s done: train %.0fs, eval %.0fs, chosen %s AP(all) %.4f",
            variant.name,
            row["train_seconds"],
            row["eval_seconds"],
            row["chosen"],
            row["chosen_avg_precision_all"],
        )
        if mlflow:
            try:  # the checkpoint and report row matter more than the tracking entry
                log_variant(mlflow, f"{prefix}-{variant.name}", grid, variant, row, state)
            except Exception:
                log.exception("variant %s: MLflow logging failed", variant.name)
        save()
        if device.type == "mps":
            torch.mps.empty_cache()
    return report, rows, failed


def log_variant(
    mlflow: Any, run_name: str, grid: Grid, variant: Variant, row: dict, state: dict
) -> None:
    with mlflow.start_run(run_name=run_name):
        mlflow.set_tags({"grid": grid.name, "variant": variant.name})
        mlflow.log_params(
            {
                **variant.model.to_dict(),
                **asdict(variant.train),
                **asdict(variant.eval),
                "variant": variant.name,
                "split": row["split"],
                "params": row["params"],
                "train_segments": row["train_segments"],
                "eval_segments": row["eval_segments"],
                "chosen": row["chosen"],
                "checkpoint": row["checkpoint"],
            }
        )
        for epoch in state.get("history", []):
            metrics = {k: v for k, v in epoch.items() if k != "epoch"}
            mlflow.log_metrics(metrics, step=int(epoch["epoch"]))
        mlflow.log_metrics(
            {
                _metric_name(k): float(v)
                for k, v in row.items()
                if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
            }
        )


def _num(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if math.isfinite(value) else "n/a"


def _ci(row: dict, prefix: str, scope: str) -> str:
    """Average precision with its bootstrap interval, when there is one."""
    value = row[f"{prefix}_avg_precision_{scope}"]
    lo, hi = row[f"{prefix}_avg_precision_lo_{scope}"], row[f"{prefix}_avg_precision_hi_{scope}"]
    return f"{_num(value)} [{_num(lo)}, {_num(hi)}]" if math.isfinite(lo) else _num(value)


def _get(row: dict, key: str) -> float:
    """A metric that rows from older runs may not have."""
    value = row.get(key)
    return float("nan") if value is None else float(value)


def _duration(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


def write_report(
    grid: Grid,
    prefix: str,
    rows: list[dict[str, Any]],
    path: Path,
    failed: dict[str, str] | None = None,
    notes: list[str] | None = None,
) -> Path:
    failed = failed or {}
    parts = [
        f"# Telemetry Transformer ablations ({grid.name})",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `race-engineer-experiments` "
        f"(grid `{grid.name}`, run prefix `{prefix}`); {len(rows)} of {len(grid.variants)} "
        "variants finished so far._",
        *(notes or []),
    ]
    if rows:
        parts += _result_sections(grid, rows)
    if failed:
        parts += ["## Failed variants", "\n".join(f"- `{n}`: {e}" for n, e in failed.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(parts) + "\n")
    return path


def _result_sections(grid: Grid, rows: list[dict[str, Any]]) -> list[str]:
    setup = pd.DataFrame(
        [
            {
                "variant": r["variant"],
                "model": r["model"],
                "objective": r["objective"],
                "context": "yes" if r["context"] else "no",
                "channels": r["channels"],
                "d_model": str(r["d_model"]),
                "layers": str(r["n_layers"]) if r["model"] == "transformer" else "-",
                "mask": f"{r['mask_ratio']:g}",
                "params": f"{r['params'] / 1e3:.0f}k",
                "epochs (best)": f"{r['epochs']} ({r['best_epoch']})",
                "split": r["split"],
                "train segments": f"{r['train_segments']:,}",
            }
            for r in rows
        ]
    )
    results = pd.DataFrame(
        [
            {
                "variant": r["variant"],
                "held-out recon MSE": _num(r["recon_mse"]),
                "chosen score": f"`{r['chosen']}`",
                "AP test [95% CI]": _ci(r, "chosen", "test"),
                "AUROC test": _num(r["chosen_auroc_test"]),
                "AP all": _num(r["chosen_avg_precision_all"]),
                "AUROC all": _num(r["chosen_auroc_all"]),
                "mae_nll AP all": _num(r["planned_avg_precision_all"]),
                "mae_nll AUROC all": _num(r["planned_auroc_all"]),
                "driver top-1": _num(r["driver_top1"]),
                "driver lap votes": _num(r["driver_lap_top1"]),
                "teammate": _num(r["teammate_segment"]),
                "train": _duration(r["train_seconds"]),
                "eval": _duration(r["eval_seconds"]),
            }
            for r in rows
        ]
    )
    variants = {v.name: v for v in grid.variants}
    subsampled = sorted(
        {m for r in rows if (m := variants[r["variant"]].eval.eval_max_events) is not None}
    )
    by_split: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_split.setdefault(r["split"], []).append(r)

    def sizes_of(part: list[dict[str, Any]]) -> str:
        texts = sorted({describe_split(r["split_counts"]) for r in part if "split_counts" in r})
        return " ".join(f"{text.capitalize()}." for text in texts)

    if len(by_split) == 1:
        split, part = next(iter(by_split.items()))
        split_note = f"**Split:** {SPLIT_NOTES[split]} {sizes_of(part)} "
    else:  # say which variants used which split
        main = max(by_split, key=lambda k: len(by_split[k]))
        others = [r["variant"] for k, part in by_split.items() if k != main for r in part]
        lines = []
        for split, part in sorted(by_split.items(), key=lambda kv: kv[0] != main):
            who = (
                "all variants except " + ", ".join(f"`{v}`" for v in others)
                if split == main
                else ", ".join(f"`{r['variant']}`" for r in part)
            )
            lines.append(f"- `{split}` ({who}): {SPLIT_NOTES[split]} {sizes_of(part)}")
        split_note = "**Splits:**\n\n" + "\n".join(lines) + "\n\n"
    mse = [r["variant"] for r in rows if r["objective"] == "mse"]
    mse_note = (
        f" Under the `mse` objective ({', '.join(mse)}) the variance head is untrained, so "
        "`mae_nll` assumes unit variance: it is half of `mae_error` and ranks the same."
        if mse
        else ""
    )
    drivers = rows[0]["drivers"]
    eval_note = (
        f"Evaluated on up to {'/'.join(map(str, subsampled))} whole events per split (a smoke "
        "setting): expect noisy numbers."
        if subsampled
        else "Evaluated on every event."
    )
    shift = pd.DataFrame(
        [
            {
                "variant": r["variant"],
                "recon MSE val": _num(_get(r, "recon_mse_val")),
                "recon MSE test": _num(_get(r, "recon_mse_test")),
                "change": _num(_get(r, "recon_mse_test") / _get(r, "recon_mse_val") - 1, 2),
                "mae_nll AUROC val": _num(_get(r, "planned_auroc_val")),
                "mae_nll AUROC test": _num(_get(r, "planned_auroc_test")),
                "mae_nll AP val": _num(_get(r, "planned_avg_precision_val")),
                "mae_nll AP test": _num(_get(r, "planned_avg_precision_test")),
            }
            for r in rows
        ]
    )
    shift_note = (
        "Under the temporal split validation is the 2025 season and test is 2026, the first "
        "year of the new cars: the change from one to the other is the model's drop under the "
        "rule change (RQ3). "
        if "temporal" in by_split
        else ""
    )
    return [
        split_note + eval_note,
        "## Variants",
        markdown_table(setup),
        "## Results",
        "Mistake scores: for each variant the reported score is **chosen on the validation "
        "events only** (by average precision), and the planned score `mae_nll` is shown next to "
        "it. AP = average precision (base rate is about 0.001). Held-out recon MSE: squared "
        "error on hidden points under the five complementary masks, on validation and test "
        "events; only comparable between variants that reconstruct the same channels. Probes "
        "use logistic regression on the embedding: driver top-1 over "
        f"{drivers} drivers (chance {1 / max(drivers, 1):.3f}), a whole lap's corners voting, "
        "and teammates (chance 0.5)." + mse_note,
        markdown_table(results),
        "## Validation vs test events",
        shift_note + "Reconstruction error on each split, and the planned score (fixed before "
        "seeing any results, so its validation numbers are not tuned).",
        markdown_table(shift),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="race-engineer-experiments", description="Train and compare model variants."
    )
    parser.add_argument("--grid", type=Path, help="TOML grid, e.g. configs/ablations.toml")
    parser.add_argument(
        "--name-prefix", default=None, help="run and checkpoint folder prefix (default: grid name)"
    )
    parser.add_argument("--only", nargs="+", metavar="VARIANT", help="run just these variants")
    parser.add_argument("--bundle", type=Path, default=None, help="load data from a bundle")
    parser.add_argument(
        "--export-bundle", type=Path, default=None, help="write the dataset as a bundle and exit"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the variants and exit")
    parser.add_argument(
        "--refresh-tensors", action="store_true", help="rebuild the cached model inputs"
    )
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.export_bundle:
        print(export_bundle(args.export_bundle, refresh=args.refresh_tensors))
        return
    if args.grid is None:
        parser.error("--grid is required (or --export-bundle)")
    grid = load_grid(args.grid)
    if args.dry_run:
        for v in grid.variants:
            print(f"{v.name}: {v.model.to_dict()} {asdict(v.train)} {asdict(v.eval)}")
        return
    data = load_bundle(args.bundle) if args.bundle else load_data(args.refresh_tensors)
    report, rows, failed = run_grid(
        grid, args.name_prefix, data=data, only=args.only, log_mlflow=not args.no_mlflow
    )
    print(report.read_text())
    for row in rows:
        print(
            f"{row['variant']}: train {row['train_seconds']:.0f}s, eval {row['eval_seconds']:.0f}s"
        )
    if failed:
        raise SystemExit(f"{len(failed)} variant(s) failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
