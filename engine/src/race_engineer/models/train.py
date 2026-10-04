"""Train the Telemetry Transformer (masked autoencoder) on corner segments.

    uv run race-engineer-train --epochs 10                 # Mac GPU (MPS), CUDA or CPU
    uv run race-engineer-train --epochs 1 --max-train 20000   # quick smoke run
    uv run race-engineer-train --model-type cnn --objective mse --no-context
    uv run race-engineer-train --bundle /data/bundle       # cloud: no processed Parquet needed

The same code runs on a cloud GPU; only the device changes. Grids of variants go through
`race-engineer-experiments` (models/experiments.py) instead.

A checkpoint records which events it trained and validated on, so evaluating it later (after
more sessions are processed, which reshuffles by_event and can flip "auto" to temporal) scores
the same held-out events and never its own training data.
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from race_engineer.config import repo_root
from race_engineer.evaluation.data import load_eval_frame
from race_engineer.evaluation.splits import assign_split, resolve_scheme
from race_engineer.evaluation.tracking import mlflow_experiment
from race_engineer.models.mae import (
    MODEL_TYPES,
    OBJECTIVES,
    MAEConfig,
    MaskedAutoencoder,
    build_model,
    masked_metrics,
    random_patch_mask,
)
from race_engineer.models.tensors import (
    SIGNAL,
    TelemetryData,
    load_bundle,
    load_or_build_tensors,
)

log = logging.getLogger(__name__)
CHECKPOINTS = repo_root() / "models" / "mae"


@dataclass
class TrainConfig:
    epochs: int = 10
    batch_size: int = 512
    lr: float = 1e-3
    weight_decay: float = 0.05
    warmup_steps: int = 300
    mask_ratio: float = 0.5
    split: str = "auto"
    seed: int = 0
    max_train: int | None = None
    max_val: int = 20_000
    # Refuse a split whose training part holds less than this share of segments (by design it's
    # about 60%). "auto" turns temporal as soon as one 2022-2024 session is processed, which
    # part-way through the build would mean training on a few sessions. 0 turns the check off.
    min_train_share: float = 0.4


def pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def to_device(data: TelemetryData, device: torch.device) -> dict[str, torch.Tensor]:
    """The whole dataset on the device (float16 signals keep it small)."""
    return {
        "signal": torch.from_numpy(data.signal).to(device),
        "context_channels": torch.from_numpy(data.context_channels).to(device),
        "context_cat": torch.from_numpy(data.context_cat).to(device),
        "context_num": torch.from_numpy(data.context_num).to(device),
    }


def take(arrays: dict[str, torch.Tensor], idx: torch.Tensor) -> dict[str, torch.Tensor]:
    return {k: (v[idx].float() if v.is_floating_point() else v[idx]) for k, v in arrays.items()}


PARTS = ("train", "val", "test")


def event_keys(frame: pd.DataFrame) -> pd.Series:
    """`<year>-<round>`: every split scheme keeps these whole."""
    return frame["year"].astype(str) + "-" + frame["round"].astype(str)


def split_events(frame: pd.DataFrame, split: pd.Series) -> dict[str, list[str]]:
    """The events in each part of a split, as saved with a checkpoint."""
    events, values = event_keys(frame).to_numpy(), split.to_numpy()
    return {part: sorted(set(events[values == part])) for part in PARTS}


def split_from_events(frame: pd.DataFrame, events: dict[str, list[str]]) -> pd.Series:
    """A saved split applied to `frame`. Events it doesn't name (processed since) go to test:
    never to train, and never to validation, where the reported score variant is chosen."""
    lookup = {event: part for part in ("test", "val", "train") for event in events.get(part, [])}
    return pd.Series(event_keys(frame).map(lookup).fillna("test").to_numpy(), index=frame.index)


def split_summary(frame: pd.DataFrame, split: pd.Series) -> dict[str, dict[str, int]]:
    """Events and segments in each part of a split."""
    events, values = event_keys(frame).to_numpy(), split.to_numpy()
    return {
        part: {
            "events": len(set(events[values == part])),
            "segments": int((values == part).sum()),
        }
        for part in PARTS
    }


def describe_split(counts: dict[str, dict[str, int]]) -> str:
    events = " / ".join(str(counts[p]["events"]) for p in PARTS)
    segments = " / ".join(f"{counts[p]['segments']:,}" for p in PARTS)
    return f"train / validation / test: {events} events, {segments} segments"


def check_split(frame: pd.DataFrame, split: pd.Series, scheme: str, min_share: float) -> None:
    """Raise if the training part is a sliver of the data (see TrainConfig.min_train_share)."""
    share = float((split.to_numpy() == "train").mean()) if len(split) else 0.0
    if share < min_share:
        raise ValueError(
            f"the {scheme} split trains on only {share:.0%} of segments ("
            f"{describe_split(split_summary(frame, split))}). Wait until the older seasons are "
            'processed, pick another split (split = "by_event"), or set min_train_share = 0.'
        )


@torch.no_grad()
def validation_loss(
    model: MaskedAutoencoder, arrays: dict[str, torch.Tensor], idx: np.ndarray, cfg: TrainConfig
) -> tuple[float, float]:
    """Masked loss and squared error on a fixed sample with fixed masks, so epochs compare."""
    model.eval()
    rng = torch.Generator().manual_seed(1234)
    device = arrays["signal"].device
    losses, errors, weights = [], [], []
    for start in range(0, len(idx), 2048):
        chunk = torch.from_numpy(idx[start : start + 2048]).to(device)
        mask = random_patch_mask(len(chunk), model.cfg.n_patches, cfg.mask_ratio, generator=rng).to(
            device
        )
        loss, error = masked_metrics(model, take(arrays, chunk), mask)
        losses.append(loss.item())
        errors.append(error.item())
        weights.append(len(chunk))
    return float(np.average(losses, weights=weights)), float(np.average(errors, weights=weights))


def run_name(model_cfg: MAEConfig, scheme: str) -> str:
    kind = "mae" if model_cfg.model_type == "transformer" else model_cfg.model_type
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{kind}-d{model_cfg.d_model}-l{model_cfg.n_layers}-{scheme}-{stamp}"


def train(
    model_cfg: MAEConfig,
    cfg: TrainConfig,
    data: TelemetryData | None = None,
    split: pd.Series | None = None,
    log_mlflow: bool = True,
    checkpoint: Path | None = None,
    device: torch.device | None = None,
    init_weights: dict[str, torch.Tensor] | None = None,
) -> Path:
    """Train and save the best epoch (by validation loss) to `checkpoint`.

    The checkpoint also records the per-epoch history, parameter count and training time.
    `init_weights` (a state dict of the same architecture) starts from a trained model, for
    fine-tuning.
    """
    torch.manual_seed(cfg.seed)
    rng = np.random.default_rng(cfg.seed)
    if data is None:
        data = load_or_build_tensors(load_eval_frame())
    if split is None:
        split = assign_split(data.frame, cfg.split)
    scheme = resolve_scheme(data.frame, cfg.split)
    check_split(data.frame, split, scheme, cfg.min_train_share)
    log.info("%s split, %s", scheme, describe_split(split_summary(data.frame, split)))
    events = split_events(data.frame, split)
    split_values = split.to_numpy()
    train_idx = np.flatnonzero(split_values == "train")
    if cfg.max_train and len(train_idx) > cfg.max_train:
        train_idx = rng.choice(train_idx, cfg.max_train, replace=False)
    val_idx = np.flatnonzero(split_values == "val")
    val_idx = rng.choice(val_idx, min(len(val_idx), cfg.max_val), replace=False)

    device = device or pick_device()
    arrays = to_device(data, device)
    model = build_model(model_cfg).to(device)
    if init_weights is not None:
        model.load_state_dict(init_weights)
    n_params = sum(p.numel() for p in model.parameters())
    steps_per_epoch = math.ceil(len(train_idx) / cfg.batch_size)
    total_steps = cfg.epochs * steps_per_epoch
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer,
        lambda step: (
            min(1.0, (step + 1) / cfg.warmup_steps)
            * 0.5
            * (1 + math.cos(math.pi * min(step, total_steps) / total_steps))
        ),
    )
    log.info(
        "training %s on %s: %d segments, %d params, %d steps/epoch",
        model_cfg.model_type,
        device,
        len(train_idx),
        n_params,
        steps_per_epoch,
    )

    name = run_name(model_cfg, scheme)
    path = checkpoint or CHECKPOINTS / f"{name}.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    mlflow = mlflow_experiment("mae") if log_mlflow else None
    run = mlflow.start_run(run_name=name) if mlflow else None
    if mlflow:
        mlflow.log_params(
            {
                **model_cfg.to_dict(),
                **asdict(cfg),
                "split": scheme,
                "params": n_params,
                "train_segments": len(train_idx),
                "device": str(device),
            }
        )

    best, best_state = float("inf"), None
    history: list[dict[str, float]] = []
    started_training = time.time()
    try:
        for epoch in range(1, cfg.epochs + 1):
            model.train()
            started = time.time()
            order = torch.from_numpy(rng.permutation(train_idx)).to(device)
            total = 0.0
            for start in range(0, len(order), cfg.batch_size):
                idx = order[start : start + cfg.batch_size]
                mask = random_patch_mask(
                    len(idx), model_cfg.n_patches, cfg.mask_ratio, device=device
                )
                loss, _ = masked_metrics(model, take(arrays, idx), mask)
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                scheduler.step()
                total += loss.item() * len(idx)
            train_loss = total / len(order)
            val_loss, val_mse = validation_loss(model, arrays, val_idx, cfg)
            seconds = time.time() - started
            log.info(
                "epoch %d/%d  train %.4f  val %.4f  val mse %.4f  (%.0fs)",
                epoch,
                cfg.epochs,
                train_loss,
                val_loss,
                val_mse,
                seconds,
            )
            metrics = {
                "train_loss": train_loss,
                "val_loss": val_loss,
                "val_mse": val_mse,
                "epoch_seconds": seconds,
            }
            history.append({"epoch": epoch, **metrics})
            if mlflow:
                mlflow.log_metrics(metrics, step=epoch)
            # A diverged (NaN) epoch counts as the worst possible, so any later finite one wins.
            score = val_loss if math.isfinite(val_loss) else math.inf
            if best_state is None or score < best:  # a NaN first epoch still saves
                best = score
                best_state = {
                    "model": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()},
                    "model_cfg": model_cfg.to_dict(),
                    "train_cfg": asdict(cfg),
                    "split": scheme,
                    "split_events": events,
                    "epoch": epoch,
                    "val_loss": val_loss,
                    "val_mse": val_mse,
                    "params": n_params,
                    "train_segments": len(train_idx),
                }
                torch.save({**best_state, "history": history}, path)
        if best_state is not None:  # the best epoch's weights, with the full history
            train_seconds = time.time() - started_training
            torch.save({**best_state, "history": history, "train_seconds": train_seconds}, path)
        if mlflow and math.isfinite(best):
            mlflow.log_metric("best_val_loss", best)
            mlflow.log_param("checkpoint", str(path))
    finally:
        if run is not None:
            mlflow.end_run()  # pyright: ignore[reportOptionalMemberAccess]
    return path


def main() -> None:
    parser = argparse.ArgumentParser(prog="race-engineer-train")
    defaults = TrainConfig()
    for name, value in asdict(defaults).items():
        kind = type(value) if value is not None else int
        parser.add_argument(f"--{name.replace('_', '-')}", type=kind, default=value)
    parser.add_argument("--d-model", type=int, default=MAEConfig.d_model)
    parser.add_argument("--n-layers", type=int, default=MAEConfig.n_layers)
    parser.add_argument("--model-type", choices=MODEL_TYPES, default=MAEConfig.model_type)
    parser.add_argument("--objective", choices=OBJECTIVES, default=MAEConfig.objective)
    parser.add_argument("--no-context", action="store_true", help="zero all context inputs")
    parser.add_argument(
        "--signal-channels", default=",".join(SIGNAL), help="comma-separated subset of SIGNAL"
    )
    parser.add_argument("--bundle", type=Path, default=None, help="train from export_bundle()")
    parser.add_argument(
        "--refresh-tensors", action="store_true", help="rebuild the cached model inputs"
    )
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = TrainConfig(**{k: getattr(args, k) for k in asdict(defaults)})
    model_cfg = MAEConfig(
        d_model=args.d_model,
        n_layers=args.n_layers,
        d_ff=2 * args.d_model,
        model_type=args.model_type,
        objective=args.objective,
        use_context=not args.no_context,
        signal_channels=tuple(args.signal_channels.split(",")),
    )
    if args.bundle:
        data = load_bundle(args.bundle)
    else:
        data = load_or_build_tensors(load_eval_frame(), refresh=args.refresh_tensors)
    print(train(model_cfg, cfg, data=data, log_mlflow=not args.no_mlflow))


if __name__ == "__main__":
    main()
