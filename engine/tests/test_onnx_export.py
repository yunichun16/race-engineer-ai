"""The ONNX scorer against score_and_embed, on tiny random models (no real data needed)."""

import json
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

# find_spec, not importorskip: onnxruntime must first be imported by onnx_scorer, which turns
# its telemetry off beforehand (see there).
if find_spec("onnxruntime") is None:
    pytest.skip("onnxruntime is not installed", allow_module_level=True)

from race_engineer.evaluation.deep import score_and_embed
from race_engineer.inference import onnx_export
from race_engineer.inference.onnx_export import (
    FLAGS_CHANGED,
    Parity,
    ParityError,
    SegmentScorer,
    check_parity,
    differences,
    export_scorer,
    gate_failures,
    op_profile,
    pick_session,
    random_data,
    rank_agreement,
    run,
    score_names,
    session_flags,
    stored_provenance,
    synthetic_parity,
    write_report,
)
from race_engineer.inference.onnx_scorer import (
    EMBEDDING,
    INPUTS,
    SCORER_PATH,
    SELECTED_CHECKPOINT,
    OnnxScorer,
    StaleScorerError,
    file_sha256,
)
from race_engineer.inference.provenance import MANIFEST, StaleResultsError, write_parquet
from race_engineer.models.mae import MAEConfig, MaskedAutoencoder, build_model
from race_engineer.models.tensors import TelemetryData, cache_key

# Dropout on, so an export or a scorer left in training mode gives random scores and fails.
TINY = MAEConfig(d_model=16, n_heads=2, n_layers=1, d_ff=32, dropout=0.3, embed_dim=8)
# An ablation-style variant: squared-error objective, and a subset of channels without speed.
VARIANT = MAEConfig(
    d_model=16,
    n_heads=2,
    n_layers=1,
    d_ff=32,
    dropout=0.3,
    embed_dim=8,
    objective="mse",
    signal_channels=("throttle", "brake", "offset_m"),
)
CPU = torch.device("cpu")


def random_model(cfg: MAEConfig, seed: int = 0) -> MaskedAutoencoder:
    """A freshly built model, in training mode as build_model leaves it."""
    torch.manual_seed(seed)
    model = build_model(cfg)
    with torch.no_grad():  # a nonzero mask token and position table, as after training
        for p in model.parameters():
            p.add_(0.05 * torch.randn_like(p))
    assert model.training
    return model


def session_data(n: int, seed: int = 0) -> TelemetryData:
    """Random inputs spread over three sessions; the most recent race is 2026 round 1."""
    data = random_data(n, seed)
    third = n // 3
    data.frame["year"] = [2025] * third + [2026] * (n - third)
    data.frame["round"] = [5] * third + [1] * third + [2] * (n - 2 * third)
    data.frame["session"] = ["R"] * (2 * third) + ["Q"] * (n - 2 * third)
    data.frame["event"] = "Test Grand Prix"
    return data


def exported(tmp_path: Path, cfg: MAEConfig) -> tuple[MaskedAutoencoder, Path]:
    pytest.importorskip("onnx")
    pytest.importorskip("onnxscript")
    model = random_model(cfg)
    return model, export_scorer(model, tmp_path / "scorer.onnx", {"checkpoint": "tiny.pt"})


@pytest.fixture(scope="module")
def tiny(tmp_path_factory: pytest.TempPathFactory) -> tuple[MaskedAutoencoder, Path]:
    return exported(tmp_path_factory.mktemp("tiny"), TINY)


def assert_matches(
    model: MaskedAutoencoder, scorer: OnnxScorer, data: TelemetryData, batch: int
) -> None:
    model.eval()  # the reference: score_and_embed expects a model in eval mode
    ref_scores, ref_emb = score_and_embed(model, data, CPU, batch=16)
    got_scores, got_emb = scorer.score_and_embed(data, batch=batch)
    assert list(got_scores) == list(ref_scores)  # same names, same order
    for name, ref in ref_scores.items():
        np.testing.assert_allclose(got_scores[name], ref, rtol=1e-4, atol=1e-6, err_msg=name)
    np.testing.assert_allclose(got_emb, ref_emb, rtol=1e-4, atol=1e-5)


@pytest.mark.parametrize("cfg", [TINY, VARIANT], ids=["tiny", "mse-no-speed"])
def test_the_scorer_module_is_score_and_embed(cfg: MAEConfig) -> None:
    model = random_model(cfg)
    data = random_data(12)
    scorer = SegmentScorer(model)
    assert not model.training  # the scorer switches dropout off itself
    inputs = [torch.from_numpy(getattr(data, name)) for name in INPUTS]
    inputs = [x.float() if x.is_floating_point() else x for x in inputs]
    with torch.no_grad():
        *scores, embedding = scorer(*inputs)
    ref_scores, ref_emb = score_and_embed(model.eval(), data, CPU)
    assert score_names(cfg) == list(ref_scores)
    for name, value in zip(score_names(cfg), scores, strict=True):
        np.testing.assert_allclose(value.numpy(), ref_scores[name], rtol=1e-5, atol=1e-7)
    np.testing.assert_allclose(embedding.numpy(), ref_emb, rtol=1e-5, atol=1e-6)


def test_onnxruntime_matches_pytorch(tiny: tuple[MaskedAutoencoder, Path]) -> None:
    model, path = tiny
    scorer = OnnxScorer(path)
    # 37 rows in batches of 10: the last batch is smaller, so the batch dimension is dynamic.
    assert_matches(model, scorer, random_data(37), batch=10)
    assert_matches(model, scorer, random_data(1, seed=1), batch=10)  # a batch of one
    assert scorer.score_names == score_names(TINY)
    assert scorer.metadata["checkpoint"] == "tiny.pt"
    assert MAEConfig.from_dict(scorer.model_cfg) == TINY
    # Traced in eval mode. A scorer traced in training mode carries Dropout nodes (which
    # onnxruntime happens to run as identity) and loses the fused Attention operator.
    ops = {node.op_type for node in pytest.importorskip("onnx").load(path).graph.node}
    assert "Dropout" not in ops and "Attention" in ops


def test_variant_models_export_their_own_scores(tmp_path: Path) -> None:
    model, path = exported(tmp_path, VARIANT)
    scorer = OnnxScorer(path)
    assert scorer.score_names == ["mae_error", "mae_nll", "mae_error_exit"]
    # The inputs still carry all five channels; the graph picks throttle, brake and offset.
    assert_matches(model, scorer, random_data(9), batch=4)


def test_serving_needs_only_numpy_and_onnxruntime(tiny: tuple[MaskedAutoencoder, Path]) -> None:
    _, path = tiny
    blocked = ["torch", "pandas", "pyarrow", "duckdb", "scipy"]
    code = (
        f"import sys; sys.modules.update(dict.fromkeys({blocked!r}))\n"  # importing them fails
        "import numpy as np\n"
        "from race_engineer.inference.onnx_scorer import OnnxScorer\n"
        "s = OnnxScorer(__import__('pathlib').Path(sys.argv[1]))\n"
        "out = s.run(np.zeros((3, 5, 80)), np.zeros((3, 2, 80)), np.zeros((3, 2), int),\n"
        "            np.zeros((3, 4)))\n"
        "assert out['cls_embedding'].shape == (3, 8), out\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_missing_file_says_how_to_export(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="onnx_export"):
        OnnxScorer(tmp_path / "missing.onnx")


def test_a_file_exported_from_another_checkpoint_is_refused(tmp_path: Path) -> None:
    pytest.importorskip("onnx")
    pytest.importorskip("onnxscript")
    checkpoint = tmp_path / "tiny.pt"
    checkpoint.write_bytes(b"the weights it was exported from")
    metadata = {"checkpoint": "tiny.pt", "checkpoint_sha256": file_sha256(checkpoint)}
    path = export_scorer(random_model(TINY), tmp_path / "scorer.onnx", metadata)
    OnnxScorer(path, checkpoint=checkpoint)
    checkpoint.write_bytes(b"another model")  # as `race-engineer-infer select` would
    with pytest.raises(StaleScorerError, match="onnx_export"):
        OnnxScorer(path, checkpoint=checkpoint)
    OnnxScorer(path, checkpoint=None)  # the check can be skipped
    OnnxScorer(path, checkpoint=tmp_path / "missing.pt")  # nothing to compare with


def test_the_served_scorer_sits_next_to_the_selected_checkpoint() -> None:
    from race_engineer.inference.score import SELECTED_CHECKPOINT as SCORED_CHECKPOINT

    assert SELECTED_CHECKPOINT == SCORED_CHECKPOINT
    assert SCORER_PATH.parent == SELECTED_CHECKPOINT.parent


def test_differences_are_relative_per_segment() -> None:
    ref = {"score": np.array([1.0, 100.0]), EMBEDDING: np.array([[3.0, 4.0], [1.0, 0.0]])}
    got = {"score": np.array([1.1, 100.0]), EMBEDDING: np.array([[3.0, 4.5], [1.0, 1e-9]])}
    table = differences(ref, got).set_index("output")
    assert table.loc["score", "max abs diff"] == pytest.approx(0.1)
    assert table.loc["score", "magnitude at max abs diff"] == pytest.approx(1.0)
    assert table.loc["score", "max rel diff"] == pytest.approx(0.1)
    # The embedding's relative difference is by vector norm: 0.5 / 5, not 1e-9 / 0.
    assert table.loc[EMBEDDING, "max rel diff"] == pytest.approx(0.1)
    assert table.loc[EMBEDDING, "magnitude at max rel diff"] == pytest.approx(5.0)


def test_rank_agreement() -> None:
    ref = np.arange(200.0)
    same = rank_agreement(ref, ref * 1.000001)
    assert same["identical order"] == "yes" and same["1 - Spearman"] == pytest.approx(0.0)
    assert same["top 50 overlap"] == "50/50" and same["top 1% overlap"] == "2/2"
    swapped = ref.copy()
    swapped[[198, 199]] = swapped[[199, 198]]  # the two highest trade places
    moved = rank_agreement(ref, swapped)
    assert moved["identical order"] == "no"
    assert moved["segments changing rank"] == 2 and moved["largest rank change"] == 1
    assert moved["top 1% overlap"] == "2/2"
    with pytest.raises(ValueError, match="at least one"):
        rank_agreement(np.array([]), np.array([]))


def test_session_flags_are_the_top_share_of_each_session() -> None:
    n = 500
    frame = pd.DataFrame({"year": 2026, "round": np.repeat([1, 2], n), "session": "R"})
    rng = np.random.default_rng(0)
    transformer = rng.random(2 * n)
    iforest = frame.groupby("round").cumcount().to_numpy() / n  # a percentile within session
    flags = session_flags(frame, transformer, iforest, "mean")
    assert flags[:n].sum() == flags[n:].sum() == 5  # the top 1% of each session
    # Scaling one session's scores changes nothing: percentiles are within the session.
    scaled = np.concatenate([transformer[:n], 1000 * transformer[n:]])
    assert (session_flags(frame, scaled, iforest, "mean") == flags).all()


def test_pick_session() -> None:
    frame = session_data(9).frame
    assert pick_session(frame, "latest") == (2026, 1, "R")
    assert pick_session(frame, "2025-5-R") == (2025, 5, "R")
    assert pick_session(frame, "none") is None
    with pytest.raises(ValueError, match="YEAR-ROUND-CODE"):
        pick_session(frame, "Baku")
    with pytest.raises(ValueError, match="no race"):
        pick_session(frame[frame["session"] == "Q"], "latest")


def test_op_profile_shares_kernel_time_by_operator(tiny: tuple[MaskedAutoencoder, Path]) -> None:
    _, path = tiny
    profile = op_profile(path, random_data(8), batch=8)
    assert {"MatMul", "Attention"} <= set(profile["operator"])  # opset 23 keeps attention whole
    assert profile["share of time"].str.endswith("%").all()
    assert not list(path.parent.glob("onnxruntime*.json"))  # the profile went to a temp dir


# --- the parity gate ---------------------------------------------------------------------------


def outputs(n: int = 50, seed: int = 0) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {
        "mae_nll": rng.normal(size=n),
        "mae_error_exit": rng.random(n) + 0.1,
        EMBEDDING: rng.normal(size=(n, 8)),
    }


def parity_of(diffs: pd.DataFrame, overlap: str = "5/5", changed: int = 0) -> Parity:
    """A Parity with only the fields the gate reads filled in."""
    return Parity(
        batch=1,
        repeats=1,
        rows=1,
        sample=0,
        session="2026-1-R",
        session_rows=1,
        diffs=diffs,
        ranking=pd.DataFrame({"whole session": {"top 1% overlap": overlap}}),
        flags={FLAGS_CHANGED: changed},
        stored=None,
        stored_note="",
        timed="whole session",
        timed_rows=1,
        speed=pd.DataFrame(),
        timing_spread=0.0,
        profile=pd.DataFrame(),
    )


def test_the_gate_passes_rounding_and_fails_real_differences() -> None:
    ref = outputs()
    rounding = {name: value * (1 + 1e-6) for name, value in ref.items()}
    same = differences(ref, rounding)
    assert gate_failures(same, None) == []
    assert gate_failures(same, parity_of(same)) == []
    # mae_nll is not gated: near zero, rounding alone gives it large relative differences.
    nll_off = differences(ref, {**rounding, "mae_nll": ref["mae_nll"] + 1e-3})
    assert gate_failures(nll_off, None) == []
    off = differences(ref, {**rounding, "mae_error_exit": ref["mae_error_exit"] * 1.001})
    [failure] = gate_failures(off, None)
    assert "mae_error_exit on random inputs" in failure
    broken = differences(ref, {**rounding, EMBEDDING: np.full_like(ref[EMBEDDING], np.nan)})
    [failure] = gate_failures(same, parity_of(broken))
    assert f"{EMBEDDING} on real segments" in failure
    ranking, flags = gate_failures(same, parity_of(same, overlap="4/5", changed=2))
    assert "4/5" in ranking and "2 flags" in flags


def test_stored_scores_count_only_when_they_are_the_current_run_of_the_same_checkpoint() -> None:
    manifest = {"run_id": "run-1", "checkpoint_sha256": "a"}
    same, how = stored_provenance(manifest, "run-1", "a")
    assert same and "this checkpoint's SHA-256" in how
    same, how = stored_provenance(manifest, "run-1", "b")
    assert not same and "another checkpoint" in how
    same, how = stored_provenance(manifest, "run-0", "a")  # `score` stopped partway
    assert not same and "not the current scoring run's" in how
    # No manifest: never scored, or `select` since. A manifest from before run ids is no better.
    same, how = stored_provenance({}, "run-1", "a")
    assert not same and "no current scoring run" in how
    assert not stored_provenance({"checkpoint_sha256": "a"}, None, "a")[0]
    assert not stored_provenance(manifest, "run-1", None)[0]  # nothing to compare the hash with


def test_a_whole_session_is_checked_without_a_random_sample(
    tiny: tuple[MaskedAutoencoder, Path], tmp_path: Path
) -> None:
    model, path = tiny
    data = session_data(60)
    parity = check_parity(
        model, path, data, segments=0, batch=16, repeats=1, results_dir=tmp_path / "results"
    )
    assert (parity.sample, parity.session, parity.session_rows) == (0, "2026-1-R", 20)
    assert list(parity.ranking.columns) == ["whole session"]
    assert (parity.timed, parity.timed_rows) == ("whole session", 20)
    assert parity.flags == {} and parity.stored is None  # no stored results to compare with
    synthetic = synthetic_parity(model, path, rows=40)
    assert gate_failures(synthetic, parity) == []

    checkpoint = tmp_path / "tiny.pt"
    checkpoint.write_bytes(b"weights")
    label = "the 2026 Test Grand Prix (R)"
    report = write_report(tmp_path / "m4_onnx.md", checkpoint, path, 1.0, synthetic, parity, label)
    text = report.read_text()
    assert "**This export passed the gate.**" in text
    assert f"real segments: every segment of {label} (20)." in text
    assert "Flags.** Not checked" in text and "no stored M4 scores" in text
    assert "Throughput on the 20 segments of the whole session" in text

    with pytest.raises(ValueError, match="nothing to check"):
        check_parity(model, path, data, segments=0, session="none")


def test_flags_are_compared_with_the_stored_run_of_the_same_checkpoint(
    tiny: tuple[MaskedAutoencoder, Path], tmp_path: Path
) -> None:
    model, path = tiny
    data = session_data(60)
    frame = data.frame
    exit_score = score_and_embed(model.eval(), data, CPU)[0]["mae_error_exit"]
    iforest = frame.groupby(["year", "round", "session"]).cumcount().to_numpy() / 20
    results = tmp_path / "results"
    results.mkdir()
    stored = pd.DataFrame(
        {
            "segment_id": frame["segment_id"],
            "transformer_raw": exit_score,
            "isolation_forest_pct": iforest,
            "flagged": session_flags(frame, exit_score, iforest, "mean"),
        }
    )
    write_parquet(stored, results / "segment_scores.parquet", "run-1")
    manifest = {"run_id": "run-1", "checkpoint_sha256": "tiny", "combined": "mean"}
    (results / MANIFEST).write_text(json.dumps(manifest))
    options: dict = {"segments": 10, "batch": 16, "repeats": 1, "results_dir": results}

    parity = check_parity(model, path, data, checkpoint_sha256="tiny", **options)
    assert parity.stored is not None and "this checkpoint's SHA-256" in parity.stored_note
    assert parity.flags[FLAGS_CHANGED] == 0
    assert parity.flags["flags that differ, stored run vs PyTorch CPU"] == 0

    def not_compared(sha: str, why: str) -> None:
        parity = check_parity(model, path, data, checkpoint_sha256=sha, **options)
        assert parity.stored is None and why in parity.stored_note
        assert FLAGS_CHANGED in parity.flags  # the ONNX flags are still checked, not the stored
        assert "flags that differ, stored run vs PyTorch CPU" not in parity.flags

    not_compared("new", "another checkpoint")
    (results / MANIFEST).unlink()  # as `race-engineer-infer select` leaves it
    not_compared("tiny", "no current scoring run")


def test_only_an_export_that_passes_the_gate_replaces_the_served_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("onnx")
    pytest.importorskip("onnxscript")
    checkpoint = tmp_path / "tiny.pt"
    torch.save({"model_cfg": TINY.to_dict(), "model": random_model(TINY).state_dict()}, checkpoint)
    out, report = tmp_path / "served" / "scorer.onnx", tmp_path / "m4_onnx.md"
    options = {"batch": 16, "repeats": 1, "results_dir": tmp_path / "results"}
    run(checkpoint, out, report, segments=0, session="latest", data=session_data(30), **options)
    assert [p.name for p in out.parent.iterdir()] == ["scorer.onnx"]
    assert OnnxScorer(out, checkpoint=checkpoint).metadata["checkpoint_sha256"] == file_sha256(
        checkpoint
    )
    assert "passed the gate" in report.read_text()
    served, report_text = out.read_bytes(), report.read_text()

    # A broken export (the graph of other weights) must fail even with no real data to check.
    export = onnx_export.export_scorer

    def broken(model: MaskedAutoencoder, path: Path, metadata: dict[str, str]) -> Path:
        return export(random_model(TINY, seed=1), path, metadata)

    monkeypatch.setattr(onnx_export, "export_scorer", broken)
    with pytest.raises(ParityError, match="failed the parity gate"):
        run(checkpoint, out, report, segments=0, session="none", **options)
    assert out.read_bytes() == served and report.read_text() == report_text
    assert sorted(p.name for p in out.parent.iterdir()) == ["scorer.onnx", "scorer.rejected.onnx"]

    monkeypatch.setattr(onnx_export, "export_scorer", export)
    run(checkpoint, out, None, segments=0, session="none", **options)  # a good one again
    assert [p.name for p in out.parent.iterdir()] == ["scorer.onnx"]  # the rejected one goes


def test_the_real_segments_must_be_the_frame_the_current_run_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "tiny.pt"
    torch.save({"model_cfg": TINY.to_dict(), "model": random_model(TINY).state_dict()}, checkpoint)
    data = session_data(30)  # the eval frame run() loads itself
    built: list[int] = []  # tensors built, by number of segments

    def load_or_build_tensors(frame: pd.DataFrame) -> TelemetryData:
        built.append(len(frame))
        return data

    monkeypatch.setattr(onnx_export, "load_eval_frame", lambda: data.frame)
    monkeypatch.setattr(onnx_export, "load_or_build_tensors", load_or_build_tensors)
    results, out = tmp_path / "results", tmp_path / "served" / "scorer.onnx"
    results.mkdir()
    # Scored before features were computed for another session: the frames differ.
    manifest = {"run_id": "run-1", "frame_key": cache_key(data.frame.iloc[1:], sources=True)}
    (results / MANIFEST).write_text(json.dumps(manifest))
    options: dict = {"segments": 0, "session": "latest", "batch": 16, "repeats": 1}
    with pytest.raises(StaleResultsError, match="not the one the current scoring run scored"):
        run(checkpoint, out, None, results_dir=results, **options)
    assert not out.parent.exists() and built == []  # refused before the tensors and the export

    pytest.importorskip("onnx")
    pytest.importorskip("onnxscript")
    manifest["frame_key"] = cache_key(data.frame, sources=True)
    (results / MANIFEST).write_text(json.dumps(manifest))
    run(checkpoint, out, None, results_dir=results, **options)
    assert out.exists() and built == [30]
