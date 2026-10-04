from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import torch

from race_engineer.inference import score
from race_engineer.inference.provenance import (
    MANIFEST,
    StaleResultsError,
    check_current,
    new_run_id,
    parquet_run,
)
from race_engineer.inference.score import (
    TRANSFORMER_SCORE,
    choose,
    combinations,
    session_percentile,
)
from race_engineer.models import tensors
from race_engineer.models.tensors import TelemetryData, cache_key


def two_sessions(n: int = 200) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "year": 2025,
            "round": np.repeat([1, 2], n),
            "session": "R",
        }
    )


def test_percentiles_are_within_each_session() -> None:
    frame = two_sessions()
    # Session 2's scores are all 100x larger; within-session percentiles don't care.
    score = np.concatenate([np.arange(200.0), 100 * np.arange(200.0)])
    pct = session_percentile(frame, score)
    assert np.allclose(pct[:200], pct[200:])
    assert pct[199] == 1.0 and 0 < pct[0] <= 0.01


def test_combinations() -> None:
    t, f = np.array([0.2, 0.9, 0.5]), np.array([0.8, 0.9, 0.1])
    c = combinations({"transformer": t, "isolation_forest": f})
    assert np.allclose(c["mean"], [0.5, 0.9, 0.3])
    assert np.allclose(c["max"], [0.8, 0.9, 0.5])
    assert set(c) == {"transformer", "isolation_forest", "mean", "max"}


def test_the_choice_uses_validation_events_only() -> None:
    rng = np.random.default_rng(0)
    n = 2000
    frame = pd.DataFrame({"year": 2025, "round": rng.integers(1, 9, n), "session": "R", "label": 0})
    frame.loc[rng.choice(n, 40, replace=False), "label"] = 1
    y = frame["label"].to_numpy() == 1
    split = np.where(frame["round"] <= 4, "val", "test")
    noise = rng.random(n)
    # "good_on_val" separates mistakes on validation events only; "good_on_test" the reverse.
    good_on_val = np.where(y & (split == "val"), 2.0, noise)
    good_on_test = np.where(y & (split == "test"), 2.0, noise)
    choice = choose({"good_on_test": good_on_test, "good_on_val": good_on_val}, frame, split, 0)
    assert choice.name == "good_on_val"
    assert set(choice.table["part"]) == {"val", "test"}


# --- run_scoring's outputs, with the model and the detectors stubbed out ---------------------


class FixedDetector:
    """Isolation Forest's interface, with scores fixed by the test."""

    def fit(self, frame: pd.DataFrame, train: np.ndarray) -> FixedDetector:
        return self

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        return np.linspace(0, 1, len(frame))


def small_data(n: int = 120) -> TelemetryData:
    """Three races of n / 3 segments (a training, a validation and a test event)."""
    frame = pd.DataFrame(
        {
            "segment_id": [f"seg-{i:04d}" for i in range(n)],
            "year": np.repeat([2025, 2025, 2026], n // 3),
            "round": np.repeat([1, 2, 1], n // 3),
            "session": "R",
            "driver": "AAA",
            "label": 0,
        }
    )
    return TelemetryData(
        frame=frame,
        signal=np.zeros((n, 5, 80), np.float16),
        context_channels=np.zeros((n, 2, 80), np.float16),
        context_cat=np.zeros((n, 2), np.int64),
        context_num=np.zeros((n, 4), np.float32),
    )


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """run_scoring without a model, the Isolation Forest or real data: the eval frame it loads
    itself is small_data()'s, its processed files are under tmp_path/processed. Returns the
    calls to build_features and load_eval_frame, in order."""
    calls: list[str] = []
    data = small_data()
    processed = tmp_path / "processed"
    for folder in ("segments", "corners"):
        (processed / folder).mkdir(parents=True)
        for key in ("2025_01_R", "2025_02_R", "2026_01_R"):
            (processed / folder / f"{key}.parquet").write_bytes(b"processed")
    monkeypatch.setattr(tensors, "PROCESSED_DIR", processed)

    def split(state: dict, frame: pd.DataFrame) -> tuple[pd.Series, str, list[str]]:
        parts = np.where(
            frame["year"] == 2026, "test", np.where(frame["round"] == 1, "train", "val")
        )
        return pd.Series(parts), "temporal", []

    def score_and_embed(model: object, data: TelemetryData, device: object) -> tuple:
        n = len(data)
        return {TRANSFORMER_SCORE: np.arange(n, dtype=np.float32)}, np.ones((n, 8), np.float32)

    def build_features() -> int:
        calls.append("build_features")
        return 2

    def load_eval_frame() -> pd.DataFrame:
        calls.append("load_eval_frame")
        return data.frame.copy()

    monkeypatch.setattr(score, "load_checkpoint", lambda path, device: (None, {"model_cfg": {}}))
    monkeypatch.setattr(score, "checkpoint_split", split)
    monkeypatch.setattr(score, "score_and_embed", score_and_embed)
    monkeypatch.setattr(score, "IsoForest", FixedDetector)
    monkeypatch.setattr(score, "build_features", build_features)
    monkeypatch.setattr(score, "load_eval_frame", load_eval_frame)
    monkeypatch.setattr(score, "load_or_build_tensors", lambda frame: data)
    return calls


def test_scoring_names_its_run_and_writes_the_manifest_last(
    stubbed: list[str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = tmp_path / "mae.pt"
    checkpoint.write_bytes(b"weights")
    results = tmp_path / "results"
    results.mkdir()
    (results / MANIFEST).write_text(json.dumps({"run_id": "an older run"}))
    cpu = torch.device("cpu")

    with caplog.at_level(logging.INFO, logger=score.__name__):
        scores = score.run_scoring(checkpoint, None, results, tmp_path / "r.md", cpu, 0)
    # it computes the features of newly processed sessions before loading the eval frame
    assert stubbed == ["build_features", "load_eval_frame"]
    assert "features computed for 2 new or reprocessed sessions" in caplog.text
    manifest = json.loads((results / MANIFEST).read_text())
    sha = hashlib.sha256(b"weights").hexdigest()
    assert manifest["checkpoint_sha256"] == sha
    assert manifest["run_id"] == new_run_id(manifest["created"], sha)
    assert manifest["segments"] == len(scores) == 120
    assert manifest["frame_key"] == cache_key(small_data().frame, sources=True)
    assert parquet_run(results / "segment_scores.parquet") == manifest["run_id"]
    check_current(results / "segment_scores.parquet", "score", results)  # no error
    pd.testing.assert_frame_equal(score.load_scores(results), scores)
    with np.load(results / "embeddings.npz") as f:
        assert list(f["segment_id"]) == list(scores["segment_id"])
    score.check_scored_frame(small_data().frame.sample(frac=1, random_state=0), results)
    with pytest.raises(StaleResultsError, match="not the one the current scoring run scored"):
        score.check_scored_frame(small_data().frame.iloc[1:], results)  # a session gained or lost
    reprocessed = tensors.PROCESSED_DIR / "segments" / "2026_01_R.parquet"
    reprocessed.write_bytes(b"processed again")  # same segment ids, other telemetry
    with pytest.raises(StaleResultsError, match="not the one the current scoring run scored"):
        score.check_scored_frame(small_data().frame, results)

    # A run that stops after writing the scores leaves no manifest, so no one builds on them.
    def fail(*args: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(score, "write_report", fail)
    with pytest.raises(RuntimeError, match="disk full"):
        score.run_scoring(checkpoint, small_data(), results, tmp_path / "r.md", cpu, 0)
    assert stubbed == ["build_features", "load_eval_frame"]  # not for data passed in
    assert not (results / MANIFEST).exists()
    assert parquet_run(results / "segment_scores.parquet") is not None  # the new, unfinished run
    with pytest.raises(StaleResultsError, match="No current scoring run"):
        check_current(results / "segment_scores.parquet", "score", results)
    with pytest.raises(StaleResultsError, match="No current scoring run"):
        score.load_scores(results)
    with pytest.raises(StaleResultsError, match="No current scoring run"):
        score.check_scored_frame(small_data().frame, results)

    # A session reprocessed while the run scores must not pass for the one it scored.
    score_and_embed = score.score_and_embed

    def reprocessed_meanwhile(*args: Any) -> tuple:
        reprocessed.write_bytes(b"processed while scoring")
        return score_and_embed(*args)

    monkeypatch.setattr(score, "score_and_embed", reprocessed_meanwhile)
    score.run_scoring(checkpoint, small_data(), results, None, cpu, 0)
    with pytest.raises(StaleResultsError, match="not the one the current scoring run scored"):
        score.check_scored_frame(small_data().frame, results)
