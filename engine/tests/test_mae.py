from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import torch

from race_engineer.evaluation.deep import checkpoint_split, load_checkpoint, score_and_embed
from race_engineer.models.mae import (
    MAEConfig,
    TelemetryMAE,
    masked_loss,
    masked_metrics,
    model_inputs,
    point_mask,
    random_patch_mask,
)
from race_engineer.models.tensors import (
    N_COMPOUNDS,
    N_NUMERIC,
    SIGNAL,
    TelemetryData,
    normalise_per_corner,
)

CFG = MAEConfig(d_model=32, n_heads=4, n_layers=2, d_ff=64, dropout=0.0, embed_dim=16)


def batch(b: int = 8, seed: int = 0) -> dict[str, torch.Tensor]:
    g = torch.Generator().manual_seed(seed)
    return {
        "signal": torch.randn(b, CFG.n_signal, CFG.n_points, generator=g),
        "context_channels": torch.randn(b, CFG.n_context, CFG.n_points, generator=g),
        "context_cat": torch.stack(
            [
                torch.randint(0, 2, (b,), generator=g),
                torch.randint(0, N_COMPOUNDS, (b,), generator=g),
            ],
            1,
        ),
        "context_num": torch.rand(b, N_NUMERIC, generator=g),
    }


def test_shapes() -> None:
    model = TelemetryMAE(CFG).eval()
    x = batch()
    mask = random_patch_mask(8, CFG.n_patches, 0.5)
    mean, logvar, embedding = model(**x, mask=mask)
    assert mean.shape == logvar.shape == (8, CFG.n_signal, CFG.n_points)
    assert embedding.shape == (8, CFG.embed_dim)
    assert (mask.sum(dim=1) == 5).all()


def test_hidden_patches_cannot_leak_into_the_prediction() -> None:
    model = TelemetryMAE(CFG).eval()
    x = batch()
    mask = torch.zeros(8, CFG.n_patches, dtype=torch.bool)
    mask[:, 3] = True
    altered = {**x, "signal": x["signal"].clone()}
    altered["signal"][:, :, 3 * CFG.patch : 4 * CFG.patch] += 100.0  # change only the hidden patch
    with torch.no_grad():
        a, _, _ = model(**x, mask=mask)
        b, _, _ = model(**altered, mask=mask)
    assert torch.allclose(a, b)


def test_it_learns_a_predictable_signal() -> None:
    torch.manual_seed(0)
    model = TelemetryMAE(CFG)
    x = batch(64)
    # A smooth, fully predictable signal: each channel is a sine wave with a random phase.
    t = torch.linspace(0, 3.14, CFG.n_points)
    x["signal"] = torch.sin(t[None, None] + torch.rand(64, CFG.n_signal, 1))
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3)
    losses = []
    for _ in range(150):
        loss = masked_loss(model, x, random_patch_mask(64, CFG.n_patches, 0.5))
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(loss.item())
    assert losses[-1] < losses[0] - 1.0  # NLL falls well below its starting point


def test_per_corner_normalisation() -> None:
    x = np.vstack([np.full((3, 80), 100.0), np.full((3, 80), 200.0)])
    x[0] += 4
    corner = np.array(["1", "1", "1", "2", "2", "2"])
    z, mean = normalise_per_corner(x, corner, floor=2.0)
    assert np.allclose(mean[:3], 100 + 4 / 3) and np.allclose(mean[3:], 200)
    assert np.allclose(z[3:], 0)  # identical laps: no deviation
    assert z[0, 0] > 1 > 0 > z[1, 0]


def fixed_mask(b: int = 8) -> torch.Tensor:
    return random_patch_mask(b, CFG.n_patches, 0.5, generator=torch.Generator().manual_seed(0))


def test_mse_objective_is_squared_error_on_hidden_points() -> None:
    cfg = replace(CFG, objective="mse")
    model = TelemetryMAE(cfg).eval()
    x, mask = batch(), fixed_mask()
    with torch.no_grad():
        loss, mse = masked_metrics(model, x, mask)
        mean, _, _ = model(**x, mask=mask)
    expected = ((x["signal"] - mean) ** 2)[point_mask(mask, cfg.patch).expand_as(mean)].mean()
    assert torch.allclose(loss, expected) and torch.allclose(mse, expected)


def test_without_context_the_context_inputs_are_ignored() -> None:
    x, mask = batch(), fixed_mask()
    other = {
        **x,
        "context_channels": x["context_channels"] + 5.0,
        "context_cat": torch.stack([1 - x["context_cat"][:, 0], x["context_cat"][:, 1]], 1),
        "context_num": x["context_num"] + 1.0,
    }
    for use_context in (True, False):
        model = TelemetryMAE(replace(CFG, use_context=use_context)).eval()
        with torch.no_grad():
            a, b = model(**x, mask=mask), model(**other, mask=mask)
        unchanged = all(torch.allclose(p, q, atol=1e-6) for p, q in zip(a, b, strict=True))
        assert unchanged == (not use_context)


def test_a_channel_subset_is_picked_at_batch_time() -> None:
    cfg = replace(CFG, signal_channels=("speed", "brake", "offset_m"))
    assert cfg.n_signal == 3 and cfg.channel_index == [0, 2, 4]
    x, mask = batch(), fixed_mask()  # every channel, as the dataset stores them
    picked = model_inputs(cfg, x)
    assert torch.equal(picked["signal"], x["signal"][:, [0, 2, 4]])
    model = TelemetryMAE(cfg).eval()
    mean, logvar, embedding = model(**picked, mask=mask)
    assert mean.shape == logvar.shape == (8, 3, CFG.n_points)
    assert embedding.shape == (8, CFG.embed_dim)
    assert torch.isfinite(masked_loss(model, x, mask))
    with pytest.raises(ValueError):
        model_inputs(cfg, picked)  # already reduced: the channel positions would be wrong


def test_scoring_honours_the_switches() -> None:
    cfg = replace(CFG, objective="mse", signal_channels=("throttle", "brake"))
    model = TelemetryMAE(cfg).eval()
    x = batch(16)
    data = TelemetryData(
        frame=pd.DataFrame(index=range(16)),
        signal=x["signal"].numpy().astype(np.float16),
        context_channels=x["context_channels"].numpy().astype(np.float16),
        context_cat=x["context_cat"].numpy(),
        context_num=x["context_num"].numpy(),
    )
    scores, embeddings = score_and_embed(model, data, torch.device("cpu"))
    assert set(scores) == {"mae_nll", "mae_error"}  # exit and speed scores need those channels
    assert np.allclose(scores["mae_nll"], 0.5 * scores["mae_error"], rtol=1e-4)  # unit variance
    assert next(iter(scores)) == "mae_error"  # so a tie on validation goes to plain error
    assert embeddings.shape == (16, cfg.embed_dim)


def test_evaluation_reuses_the_split_the_checkpoint_trained_with() -> None:
    frame = pd.DataFrame({"year": [2025, 2025, 2025, 2026, 2024], "round": [1, 2, 3, 1, 5]})
    state = {
        "split": "by_event",
        "split_events": {"train": ["2025-1"], "val": ["2025-2"], "test": ["2025-3", "2026-1"]},
    }
    split, scheme, notes = checkpoint_split(state, frame)
    assert split.tolist() == ["train", "val", "test", "test", "test"]  # 2024-5 is new: test
    assert scheme == "by_event" and "1 event(s) processed since training" in notes[0]
    with pytest.raises(ValueError, match="trained on the by_event split"):
        checkpoint_split(state, frame, "temporal")

    legacy = {"split": "by_event"}  # saved before split events were: recomputed, with a warning
    split, scheme, notes = checkpoint_split(legacy, frame)
    assert scheme == "by_event" and len(split) == 5 and "Retrain" in notes[0]


def test_old_checkpoints_load_with_the_original_settings(tmp_path: Path) -> None:
    legacy = {"n_points": 80, "patch": 8, "d_model": 32, "n_heads": 4, "n_layers": 2, "d_ff": 64,
              "dropout": 0.0, "embed_dim": 16, "n_signal": 5, "n_context": 2}  # fmt: skip
    cfg = MAEConfig.from_dict(legacy)
    assert (cfg.model_type, cfg.objective, cfg.use_context) == ("transformer", "nll", True)
    assert cfg.signal_channels == SIGNAL
    path = tmp_path / "old.pt"
    state = {"model": TelemetryMAE(cfg).state_dict(), "model_cfg": legacy, "epoch": 1}
    torch.save(state, path)
    model, _ = load_checkpoint(path, torch.device("cpu"))
    assert isinstance(model, TelemetryMAE) and model.cfg == cfg


def test_config_rejects_unknown_switch_values() -> None:
    bad: list[dict[str, Any]] = [
        {"objective": "l1"},
        {"model_type": "rnn"},
        {"signal_channels": ("speed", "rpm")},
    ]
    for settings in bad:
        with pytest.raises(ValueError):
            MAEConfig(**settings)
    lists = MAEConfig(signal_channels=["speed"])  # pyright: ignore[reportArgumentType]
    assert lists.signal_channels == ("speed",)  # TOML gives lists
