from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch
from test_experiments import synthetic_data

from race_engineer.models.finetune import (
    CONTROL,
    TARGET,
    check_base,
    plot,
    run_study,
    shift_events,
    write_report,
)
from race_engineer.models.mae import MAEConfig
from race_engineer.models.tensors import TelemetryData
from race_engineer.models.train import TrainConfig, train


def seasons() -> TelemetryData:
    """13 events: three of 2024, four of 2025 and six of 2026, each in calendar order."""
    data = synthetic_data(events=13)
    frame = data.frame
    original = frame["round"].to_numpy()
    year = np.select([original <= 3, original <= 7], [2024, 2025], 2026)
    first = np.select([original <= 3, original <= 7], [1, 4], 8)
    frame = frame.assign(year=year, round=original - first + 1, event="Somewhere Grand Prix")
    return TelemetryData(
        frame=frame,
        signal=data.signal,
        context_channels=data.context_channels,
        context_cat=data.context_cat,
        context_num=data.context_num,
    )


def test_events_are_split_in_calendar_order() -> None:
    events = shift_events(seasons().frame, pool=2, n_val=2)
    assert events.pool == ["2026-1", "2026-2"]
    assert events.val == ["2026-3", "2026-4"]
    assert events.test == ["2026-5", "2026-6"]
    assert events.control == ["2025-1", "2025-2"]
    assert events.reference == ["2025-3", "2025-4"]
    with pytest.raises(ValueError, match="2026 events processed"):
        shift_events(seasons().frame, pool=4, n_val=2)


def test_the_base_model_must_not_have_seen_2025_or_2026() -> None:
    check_base({"split_events": {"train": ["2022-1", "2024-3"]}})
    with pytest.raises(ValueError, match="trained on"):
        check_base({"split_events": {"train": ["2024-3", "2025-1"]}})


def test_study_runs_end_to_end(tmp_path: Path) -> None:
    data = seasons()
    year = data.frame["year"].to_numpy()
    split = pd.Series(np.select([year <= 2024, year == 2025], ["train", "val"], "test"))
    model_cfg = MAEConfig(d_model=16, n_heads=2, n_layers=1, embed_dim=8, dropout=0.0)
    cfg = TrainConfig(epochs=1, batch_size=256, warmup_steps=1, max_val=400, min_train_share=0)
    device = torch.device("cpu")
    base = train(model_cfg, cfg, data, split, False, tmp_path / "base.pt", device)

    results, events, reference = run_study(
        base, data, tmp_path / "study", epochs=1, steps=(0, 1, 2), bootstrap=0, device=device
    )
    assert len(results) == 6 and set(results["source"]) == {TARGET, CONTROL}
    assert results["recon_mse"].map(np.isfinite).all()
    tuned = results[results["races"] > 0]
    assert (tuned["train_segments"] > 0).all()
    assert np.isfinite(reference["recon_mse"])
    assert (tmp_path / "study" / "results.json").exists()

    figure = plot(results, reference, tmp_path / "figures" / "shift.png")
    report = write_report(
        results, events, reference, data.frame, base, 1, 2e-4, tmp_path / "shift.md",
        figure.relative_to(tmp_path),
    )  # fmt: skip
    text = report.read_text()
    assert "Fine-tuning pool (2026):** Somewhere, Somewhere" in text
    assert "figures/shift.png" in text
