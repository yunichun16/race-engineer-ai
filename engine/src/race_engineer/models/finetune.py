"""RQ3, part two: how many races with the new (2026) cars does the model need to adapt?

Start from a model trained on 2022-2024 (the temporal split). Order the 2026 events by round:
the first POOL are for fine-tuning, the next N_VAL pick each run's best epoch, and the rest are
the test events, which no run trains on. Fine-tune on the first N events of the pool, for each N,
and score the test events. A control curve fine-tunes on the first N events of 2025 instead,
which the model validated on but never trained on: if 2026 races help more than the same number
of 2025 races, the gain is adaptation to the new cars rather than just more data.

    uv run python -m race_engineer.models.finetune --checkpoint <a temporal checkpoint>
    uv run python -m race_engineer.models.finetune --checkpoint <...> --bundle ../data/bundle

Writes report/m3_shift.md, report/figures/shift_finetune.png and models/finetune/<run>/.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

from race_engineer.config import REPORT_DIR, repo_root
from race_engineer.data.qa import markdown_table
from race_engineer.evaluation.data import load_eval_frame
from race_engineer.evaluation.deep import (
    PLANNED_SCORE,
    load_checkpoint,
    safe_evaluate,
    score_and_embed,
)
from race_engineer.models.mae import MAEConfig, MaskedAutoencoder
from race_engineer.models.tensors import TelemetryData, load_bundle, load_or_build_tensors
from race_engineer.models.train import TrainConfig, event_keys, pick_device, train

log = logging.getLogger(__name__)

FINETUNE_DIR = repo_root() / "models" / "finetune"
TARGET_YEAR, CONTROL_YEAR = 2026, 2025
POOL, N_VAL, MIN_TEST = 8, 2, 2
STEPS = (0, 1, 2, 4, 8)
SCORES = (PLANNED_SCORE, "mae_error", "mae_error_exit")
TARGET, CONTROL = f"{TARGET_YEAR}", f"{CONTROL_YEAR} (control)"


@dataclass(frozen=True)
class ShiftEvents:
    pool: list[str]  # 2026 events to fine-tune on, in calendar order
    val: list[str]  # 2026 events that pick each run's best epoch
    test: list[str]  # 2026 events no run trains on
    control: list[str]  # 2025 events for the control curve, in calendar order
    reference: list[str]  # the other 2025 events: the base model on the old cars


def year_events(frame: pd.DataFrame, year: int) -> list[str]:
    """`year`'s events in calendar order, as event keys (`<year>-<round>`)."""
    rounds = sorted(int(r) for r in frame.loc[frame["year"] == year, "round"].unique())
    return [f"{year}-{r}" for r in rounds]


def shift_events(frame: pd.DataFrame, pool: int = POOL, n_val: int = N_VAL) -> ShiftEvents:
    target, control = year_events(frame, TARGET_YEAR), year_events(frame, CONTROL_YEAR)
    if len(target) < pool + n_val + MIN_TEST:
        raise ValueError(
            f"{len(target)} {TARGET_YEAR} events processed; the study needs {pool} to fine-tune "
            f"on, {n_val} for validation and at least {MIN_TEST} to test on"
        )
    if len(control) < pool + MIN_TEST:
        raise ValueError(f"{len(control)} {CONTROL_YEAR} events processed; need {pool + MIN_TEST}")
    return ShiftEvents(
        pool=target[:pool],
        val=target[pool : pool + n_val],
        test=target[pool + n_val :],
        control=control[:pool],
        reference=control[pool:],
    )


def check_base(state: dict) -> None:
    """The base model must not have trained on either year the study fine-tunes on."""
    trained = state.get("split_events", {}).get("train", [])
    late = sorted(e for e in trained if int(e.split("-")[0]) >= CONTROL_YEAR)
    if not trained or late:
        raise ValueError(
            "fine-tune from a checkpoint trained on 2022-2024 only (split = 'temporal'); "
            + (f"this one trained on {late[:3]}" if late else "this one records no split")
        )


@torch.no_grad()
def score_events(
    model: MaskedAutoencoder, data: TelemetryData, device: torch.device, bootstrap: int
) -> dict[str, float]:
    """Reconstruction error and mistake detection on every segment of `data`."""
    scores, _ = score_and_embed(model, data, device)
    frame = data.frame
    labelled = frame["label"].to_numpy() >= 0
    y = frame["label"].to_numpy()[labelled] == 1
    events = event_keys(frame).to_numpy()[labelled]
    row: dict[str, float] = {
        "segments": len(frame),
        "recon_mse": float(np.mean(scores["mae_error"])),
    }
    for name in SCORES:
        if name not in scores:
            continue
        metrics = safe_evaluate(y, scores[name][labelled], events, bootstrap)
        row[f"{name}_auroc"] = metrics["auroc"]
        for key in ("avg_precision", "avg_precision_lo", "avg_precision_hi"):
            row[f"{name}_{key}"] = metrics[key]
    return row


def run_study(
    checkpoint: Path,
    data: TelemetryData,
    out_dir: Path,
    *,
    epochs: int = 5,
    lr: float = 2e-4,
    steps: tuple[int, ...] = STEPS,
    bootstrap: int = 200,
    device: torch.device | None = None,
) -> tuple[pd.DataFrame, ShiftEvents, dict[str, float]]:
    """Both fine-tuning curves: (one row per source and number of races, the events, the base
    model on the other 2025 events)."""
    device = device or pick_device()
    base, state = load_checkpoint(checkpoint, device)
    check_base(state)
    events = shift_events(data.frame, max(max(steps), 1))
    keys = event_keys(data.frame)
    test_rows = keys.isin(events.test).to_numpy()
    test = data.subset(np.flatnonzero(test_rows))
    log.info("scoring the base model on %d test segments", len(test))
    base_row = score_events(base, test, device, bootstrap)
    reference_data = data.subset(np.flatnonzero(keys.isin(events.reference).to_numpy()))
    reference: dict[str, Any] = score_events(base, reference_data, device, 0)
    # Track matters as much as the year: the same circuits in both seasons, old cars vs new.
    # Only circuits raced in both years (a new circuit has no old-car counterpart).
    location = data.frame["location"].astype(str).to_numpy()
    year = data.frame["year"].to_numpy()
    shared = sorted(set(location[test_rows]) & set(location[year == CONTROL_YEAR]))
    for label, rows_of_year in (
        ("same_tracks_old", year == CONTROL_YEAR),
        ("same_tracks_new", test_rows),
    ):
        ref_rows = np.flatnonzero(rows_of_year & np.isin(location, shared))
        scored = score_events(base, data.subset(ref_rows), device, 0) if len(ref_rows) else {}
        reference[f"{label}_recon_mse"] = scored.get("recon_mse", math.nan)
        reference[f"{label}_segments"] = float(len(ref_rows))
    reference["same_tracks"] = ", ".join(shared)

    model_cfg = MAEConfig.from_dict(state["model_cfg"])
    cfg = replace(
        TrainConfig(**state["train_cfg"]),
        epochs=epochs,
        lr=lr,
        warmup_steps=20,
        split="finetune",
        max_train=None,
        min_train_share=0.0,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for source, pool in ((TARGET, events.pool), (CONTROL, events.control)):
        for n in steps:
            if n == 0:
                rows.append({"source": source, "races": 0, "train_segments": 0, **base_row})
                continue
            # Only the rows this run touches, so the whole dataset never sits on the GPU.
            rows_used = np.flatnonzero(keys.isin(pool[:n] + events.val).to_numpy())
            subset = data.subset(rows_used)
            sub_keys = keys.iloc[rows_used].reset_index(drop=True)
            split = pd.Series(np.where(sub_keys.isin(pool[:n]), "train", "val"))
            tag = f"{'target' if source == TARGET else 'control'}-{n}"
            log.info("fine-tuning on %d %s event(s): %s", n, source, ", ".join(pool[:n]))
            started = time.time()
            path = train(
                model_cfg,
                cfg,
                subset,
                split,
                log_mlflow=False,
                checkpoint=out_dir / f"{tag}.pt",
                device=device,
                init_weights=state["model"],
            )
            model, tuned = load_checkpoint(path, device)
            rows.append(
                {
                    "source": source,
                    "races": n,
                    "train_segments": tuned["train_segments"],
                    "best_epoch": tuned["epoch"],
                    "train_seconds": time.time() - started,
                    **score_events(model, test, device, bootstrap),
                }
            )
            if device.type == "mps":
                torch.mps.empty_cache()
    results = pd.DataFrame(rows)
    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "checkpoint": str(checkpoint),
                "epochs": epochs,
                "lr": lr,
                "events": events.__dict__,
                "reference": reference,
                "rows": results.to_dict("records"),
            },
            indent=2,
            default=float,
        )
        + "\n"
    )
    return results, events, reference


def _num(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if isinstance(value, float | int) and math.isfinite(value) else "-"


def _names(frame: pd.DataFrame, keys: list[str]) -> str:
    names = (
        frame.assign(key=event_keys(frame))
        .drop_duplicates("key")
        .set_index("key")["event"]
        .str.replace(" Grand Prix", "")
    )
    return ", ".join(str(names.get(k, k)) for k in keys)


def plot(results: pd.DataFrame, reference: dict[str, float], path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.5, 4))
    for source, style in ((TARGET, "o-"), (CONTROL, "s--")):
        part = results[results["source"] == source].sort_values("races")
        ax.plot(part["races"], part["recon_mse"], style, label=f"fine-tuned on {source} races")
    ax.axhline(
        reference["recon_mse"],
        color="0.5",
        lw=1,
        ls=":",
        label=f"no fine-tuning, on later {CONTROL_YEAR} events (other tracks)",
    )
    top = max(float(results["recon_mse"].max()), float(reference["recon_mse"]))
    ax.set_ylim(0, top * 1.2)  # differences of a few percent, shown at their true size
    ax.set_xlabel("races fine-tuned on (in calendar order)")
    ax.set_ylabel(f"reconstruction error on held-out {TARGET_YEAR} races")
    ax.set_title("How fast the model adapts to the 2026 cars", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def write_report(
    results: pd.DataFrame,
    events: ShiftEvents,
    reference: dict[str, float],
    frame: pd.DataFrame,
    checkpoint: Path,
    epochs: int,
    lr: float,
    path: Path,
    figure: Path | None = None,
) -> Path:
    base = float(results.loc[results["races"] == 0, "recon_mse"].iloc[0])
    table = pd.DataFrame(
        [
            {
                "fine-tuned on": "nothing" if r["races"] == 0 else r["source"],
                "races": str(r["races"]),
                "segments": f"{int(r['train_segments']):,}",
                "best epoch": str(int(r["best_epoch"])) if r["races"] else "-",
                "recon MSE": _num(r["recon_mse"]),
                "vs none": f"{r['recon_mse'] / base - 1:+.1%}",
                "mae_nll AUROC": _num(r.get(f"{PLANNED_SCORE}_auroc", math.nan)),
                "mae_error AUROC": _num(r.get("mae_error_auroc", math.nan)),
                "mae_error_exit AP [95% CI]": (
                    f"{_num(r.get('mae_error_exit_avg_precision', math.nan))} "
                    f"[{_num(r.get('mae_error_exit_avg_precision_lo', math.nan))}, "
                    f"{_num(r.get('mae_error_exit_avg_precision_hi', math.nan))}]"
                ),
            }
            for r in results.to_dict("records")
            if not (r["races"] == 0 and r["source"] == CONTROL)
        ]
    )
    test_rows = int(results["segments"].iloc[0])
    parts = [
        "# The 2026 rule change: how fast does the model adapt?",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `race_engineer.models.finetune` "
        f"from `{checkpoint.name}`._",
        "Research question 3, part two. The base model trained on 2022-2024 only. Each run "
        f"fine-tunes it for up to {epochs} epochs (learning rate {lr:g}) on the first N "
        f"{TARGET_YEAR} races, keeps the epoch with the lowest validation loss on the "
        f"validation races, and scores the test races, which no run trains on. The control runs "
        f"fine-tune on the same number of {CONTROL_YEAR} races instead: if {TARGET_YEAR} races "
        "help more than the same amount of older data, the gain is adaptation to the new cars.",
        f"- **Fine-tuning pool ({TARGET_YEAR}):** {_names(frame, events.pool)}",
        f"- **Validation ({TARGET_YEAR}):** {_names(frame, events.val)}",
        f"- **Test ({TARGET_YEAR}, {test_rows:,} segments):** {_names(frame, events.test)}",
        f"- **Control pool ({CONTROL_YEAR}):** {_names(frame, events.control)}",
        "Inputs are normalised per session and corner, so level shifts (every car faster or "
        "slower through a corner) are removed before the model sees them; what remains is the "
        "shape of the traces, such as lifting and coasting to save energy.",
        "## Results on the test races",
        f"For reference, the base model's reconstruction error on the later {CONTROL_YEAR} "
        f"events (old cars, not trained on) is {_num(reference['recon_mse'])}. Tracks differ "
        "between those and the test races, so the same circuits in both years are the fairer "
        f"comparison ({reference.get('same_tracks') or 'none'}): "
        f"{_num(reference.get('same_tracks_old_recon_mse', math.nan))} in {CONTROL_YEAR} vs "
        f"{_num(reference.get('same_tracks_new_recon_mse', math.nan))} in {TARGET_YEAR}, "
        "before any fine-tuning. Training on the Mac GPU is not bit-for-bit repeatable: "
        "repeating the study moved single rows by up to about 0.7%, so smaller differences are "
        "noise. AP base rate is about 0.001.",
        markdown_table(table),
    ]
    if figure is not None:
        parts.append(f"![Reconstruction error against races fine-tuned on]({figure})")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(parts) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="race_engineer.models.finetune",
        description="How many 2026 races does the model need to adapt? (RQ3)",
    )
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, default=None, help="data bundle instead of Parquet")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--steps", type=int, nargs="+", default=list(STEPS))
    parser.add_argument("--bootstrap", type=int, default=200)
    parser.add_argument("--report", type=Path, default=REPORT_DIR / "m3_shift.md")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", force=True
    )
    data = load_bundle(args.bundle) if args.bundle else load_or_build_tensors(load_eval_frame())
    out_dir = FINETUNE_DIR / f"{args.checkpoint.stem}-{time.strftime('%Y%m%d-%H%M%S')}"
    results, events, reference = run_study(
        args.checkpoint,
        data,
        out_dir,
        epochs=args.epochs,
        lr=args.lr,
        steps=tuple(sorted(set(args.steps) | {0})),
        bootstrap=args.bootstrap,
    )
    figure = plot(results, reference, args.report.parent / "figures" / "shift_finetune.png")
    report = write_report(
        results,
        events,
        reference,
        data.frame,
        args.checkpoint,
        args.epochs,
        args.lr,
        args.report,
        figure.relative_to(args.report.parent),
    )
    print(f"wrote {report} and {figure}; checkpoints in {out_dir}")


if __name__ == "__main__":
    main()
