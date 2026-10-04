import math
from dataclasses import fields
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from race_engineer.models import train as train_module
from race_engineer.models.experiments import (
    SETTINGS,
    check_splits,
    eval_rows,
    load_grid,
    make_variant,
    parse_grid,
    run_grid,
)
from race_engineer.models.mae import MAEConfig
from race_engineer.models.tensors import SIGNAL, TelemetryData
from race_engineer.models.train import TrainConfig, event_keys, train

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_settings_route_to_exactly_one_config() -> None:
    names = [f.name for cls in SETTINGS.values() for f in fields(cls)]
    assert len(names) == len(set(names))


def test_toml_grid_applies_overrides(tmp_path: Path) -> None:
    path = tmp_path / "mini.toml"
    path.write_text(
        """
[base]
d_model = 128
epochs = 3
mask_ratio = 0.5
bootstrap = 10

[[variant]]
name = "baseline"

[[variant]]
name = "wide_mse"
d_model = 256
objective = "mse"
use_context = false
signal_channels = ["speed", "brake"]
mask_ratio = 0.7
eval_max_events = 2
"""
    )
    grid = load_grid(path)
    assert grid.name == "mini" and grid.report == "m3_ablations_mini.md"
    base, wide = grid.variants
    assert (base.model.d_model, base.model.d_ff, base.train.epochs) == (128, 256, 3)
    assert (base.model.objective, base.model.use_context, base.model.signal_channels) == (
        "nll",
        True,
        SIGNAL,
    )
    assert (wide.model.d_model, wide.model.d_ff) == (256, 512)  # d_ff follows d_model
    assert (wide.model.objective, wide.model.use_context) == ("mse", False)
    assert wide.model.signal_channels == ("speed", "brake")
    assert (wide.train.mask_ratio, wide.train.epochs) == (0.7, 3)  # the rest from [base]
    assert (wide.eval.eval_max_events, wide.eval.bootstrap) == (2, 10)
    assert base.eval.eval_max_events is None


def test_grid_mistakes_are_caught() -> None:
    with pytest.raises(ValueError, match="unknown setting"):
        make_variant("typo", {"mask_ration": 0.3})
    with pytest.raises(ValueError, match="repeat"):
        parse_grid({"variant": [{"name": "a"}, {"name": "a"}]}, "g")
    with pytest.raises(ValueError, match="name"):
        parse_grid({"variant": [{"epochs": 1}]}, "g")
    with pytest.raises(ValueError, match="no \\[\\[variant\\]\\]"):
        parse_grid({"base": {"epochs": 1}}, "g")
    with pytest.raises(ValueError):
        make_variant("bad", {"objective": "l1"})


def test_shipped_grids() -> None:
    grid = load_grid(CONFIGS / "ablations.toml")
    variants = {v.name: v for v in grid.variants}
    assert grid.report == "m3_ablations.md" and len(variants) == 11
    base = variants["baseline"]
    assert (base.model.d_model, base.model.n_layers, base.train.mask_ratio) == (128, 4, 0.5)
    assert base.train.epochs == 20 and base.eval.eval_max_events is None
    assert variants["objective_mse"].model.objective == "mse"
    assert variants["no_context"].model.use_context is False
    assert variants["mask_0.3"].train.mask_ratio == 0.3
    assert variants["mask_0.7"].train.mask_ratio == 0.7
    assert variants["small_d64"].model.d_ff == 128 and variants["large_d256"].model.d_ff == 512
    assert "offset_m" not in variants["no_offset_channel"].model.signal_channels
    assert "gear" not in variants["no_gear_channel"].model.signal_channels
    assert variants["cnn"].model.model_type == "cnn"
    assert base.train.split == "temporal"
    assert variants["unseen_tracks"].train.split == "leave_tracks_out"

    smoke = load_grid(CONFIGS / "smoke.toml")
    assert smoke.report == "m3_ablations_smoke.md" and 2 <= len(smoke.variants) <= 3
    assert all(v.train.epochs == 1 and v.eval.eval_max_events for v in smoke.variants)
    # "auto" changes meaning as older seasons are processed, so the shipped grids pin a split.
    assert all(v.train.split != "auto" for v in [*grid.variants, *smoke.variants])


def synthetic_data(events: int = 9, laps: int = 8, corners: int = 4) -> TelemetryData:
    """Nine events (2026 only, so a by-event split), two teams of two, a few labelled mistakes
    in every event. Each driver has a slight habit, so the probes have something to find."""
    rng = np.random.default_rng(0)
    drivers = {"AAA": "Red", "BBB": "Red", "CCC": "Blue", "DDD": "Blue"}
    rows = [
        (r, d, lap, c)
        for r in range(1, events + 1)
        for d in drivers
        for lap in range(1, laps + 1)
        for c in range(1, corners + 1)
    ]
    frame = pd.DataFrame(rows, columns=["round", "driver", "lap_number", "corner"])
    frame = frame.assign(
        year=2026,
        session="R",
        team=frame["driver"].map(drivers),
        location="track" + frame["round"].astype(str),
        lap_id=(
            "2026_" + frame["round"].astype(str) + "_R_" + frame["driver"] + "_"
            + frame["lap_number"].astype(str)
        ),
    )  # fmt: skip
    frame["segment_id"] = frame["lap_id"] + "_T" + frame["corner"].astype(str)
    label = np.zeros(len(frame), int)
    for r in range(1, events + 1):
        label[rng.choice(np.flatnonzero(frame["round"] == r), 3, replace=False)] = 1
    frame["label"] = label
    n = len(frame)
    habit = frame["driver"].map({"AAA": 0.0, "BBB": 0.5, "CCC": -0.5, "DDD": 1.0}).to_numpy()
    signal = rng.normal(0, 1, (n, len(SIGNAL), 80)) + habit[:, None, None]
    return TelemetryData(
        frame=frame,
        signal=signal.astype(np.float16),
        context_channels=rng.normal(0, 1, (n, 2, 80)).astype(np.float16),
        context_cat=np.column_stack([np.ones(n), rng.integers(0, 6, n)]).astype(np.int64),
        context_num=rng.random((n, 4)).astype(np.float32),
    )


def test_a_split_that_trains_on_a_sliver_is_refused() -> None:
    frame = synthetic_data().frame.copy()
    frame.loc[frame["round"] == 1, "year"] = 2024  # one older event processed so far
    frame.loc[frame["round"] == 2, "year"] = 2025
    # "auto" now waits for enough older seasons; asking for temporal explicitly this early
    # would train on 1 of 9 events, which the training guard refuses.
    temporal = make_variant("temporal", {"split": "temporal"})
    with pytest.raises(ValueError, match="trains on only 11%"):
        check_splits([temporal], frame)
    check_splits([make_variant("anyway", {"min_train_share": 0.0})], frame)
    check_splits([make_variant("by_event", {"split": "by_event"})], frame)


def test_eval_rows_keep_whole_events_per_split() -> None:
    frame = synthetic_data().frame
    split = pd.Series(np.where(frame["round"] <= 5, "train", np.where(frame["round"] <= 7, "val",
                      "test")))  # fmt: skip
    rows = eval_rows(frame, split, max_events=1)
    kept = frame.iloc[rows]
    assert kept["round"].nunique() == 3
    assert kept.groupby("round").size().eq(frame.groupby("round").size().iloc[0]).all()
    assert set(split.iloc[rows]) == {"train", "val", "test"}
    assert len(eval_rows(frame, split, None)) == len(frame)


def test_grid_runs_end_to_end_on_synthetic_data(tmp_path: Path) -> None:
    grid = parse_grid(
        {
            "report": "ablations_test.md",
            "base": {
                "d_model": 16,
                "n_heads": 2,
                "n_layers": 1,
                "embed_dim": 8,
                "dropout": 0.0,
                "epochs": 1,
                "batch_size": 256,
                "warmup_steps": 1,
                "max_val": 400,
                "bootstrap": 0,
            },
            "variant": [
                {"name": "transformer"},
                {
                    "name": "bad_heads",
                    "n_heads": 3,
                },  # 16 dims can't split 3 ways: fails, run goes on
                {
                    "name": "cnn_mse",
                    "model_type": "cnn",
                    "objective": "mse",
                    "use_context": False,
                    "signal_channels": ["speed", "throttle", "brake"],
                    "eval_max_events": 1,
                },
            ],
        },
        "test",
    )
    report, rows, failed = run_grid(
        grid,
        data=synthetic_data(),
        log_mlflow=False,
        report_dir=tmp_path,
        checkpoint_root=tmp_path / "ckpt",
        device=torch.device("cpu"),
    )
    assert list(failed) == ["bad_heads"]
    assert [r["variant"] for r in rows] == ["transformer", "cnn_mse"]
    assert rows[1]["channels"] == "no gear, offset_m" and rows[1]["model"] == "cnn"
    assert rows[1]["eval_segments"] < rows[0]["eval_segments"]
    for row in rows:
        assert Path(row["checkpoint"]).exists()
        assert np.isfinite(row["val_loss"]) and np.isfinite(row["recon_mse"])
        assert row["chosen"].startswith("mae_") and row["drivers"] == 4
        assert 0 <= row["driver_top1"] <= 1 and 0 <= row["teammate_segment"] <= 1
    text = report.read_text()
    assert report.name == "ablations_test.md" and "cnn_mse" in text and "transformer" in text
    assert "## Failed variants" in text and "`bad_heads`" in text
    assert "Train / validation / test: 5 / 2 / 2 events" in text
    assert "`mse` objective (cnn_mse)" in text

    # The checkpoint names the events each split held, for evaluating it later.
    state = torch.load(rows[0]["checkpoint"], weights_only=False)
    assert sorted(e for events in state["split_events"].values() for e in events) == sorted(
        set(event_keys(synthetic_data().frame))
    )
    assert [len(state["split_events"][p]) for p in ("train", "val", "test")] == [5, 2, 2]

    # Rerunning one variant keeps the others' earlier rows in the report.
    _, rerun, _ = run_grid(
        grid,
        data=synthetic_data(),
        only=["cnn_mse"],
        log_mlflow=False,
        report_dir=tmp_path,
        checkpoint_root=tmp_path / "ckpt",
        device=torch.device("cpu"),
    )
    assert [r["variant"] for r in rerun] == ["cnn_mse"]
    text = report.read_text()
    assert "2 of 3 variants finished" in text and "| transformer |" in text
    assert (
        f"Rows for `transformer` come from earlier runs of this grid (`{rows[0]['run']}`)" in text
    )
    assert "Warning" not in text  # same data both times


def test_a_diverged_first_epoch_does_not_stay_the_best(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    losses = iter([(math.nan, math.nan), (2.0, 1.0), (3.0, 1.5)])
    monkeypatch.setattr(train_module, "validation_loss", lambda *args: next(losses))
    model = MAEConfig(d_model=16, n_heads=2, n_layers=1, d_ff=32, embed_dim=8, dropout=0.0)
    cfg = TrainConfig(epochs=3, batch_size=512, warmup_steps=1, max_val=100)
    path = train(model, cfg, synthetic_data(events=5, laps=2), log_mlflow=False,
                 checkpoint=tmp_path / "m.pt", device=torch.device("cpu"))  # fmt: skip
    assert torch.load(path, weights_only=False)["epoch"] == 2
