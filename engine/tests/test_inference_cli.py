"""`race-engineer-infer`: what each subcommand does with its arguments, with the steps stubbed."""

import json
import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest

from race_engineer.config import REPORT_DIR
from race_engineer.inference import cli, explain, score, style
from race_engineer.inference.provenance import MANIFEST, StaleResultsError


@pytest.fixture
def explain_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(explain, "run_explain", lambda **kwargs: calls.append(kwargs))
    return calls


def test_explain_help_and_unknown_arguments_run_nothing(
    explain_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_:
        cli.main(["explain", "--help"])
    assert exit_.value.code == 0
    out = capsys.readouterr().out
    assert "usage: race-engineer-infer explain" in out and "--no-report" in out
    with pytest.raises(SystemExit) as exit_:
        cli.main(["explain", "--bogus"])
    assert exit_.value.code == 2
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err
    assert explain_calls == []  # neither started the step


def test_explain_runs_the_step_with_the_options_of_explain_main(
    explain_calls: list[dict[str, Any]],
) -> None:
    cli.main(["explain"])
    cli.main(["explain", "--no-report"])
    cli.main(["explain", "--allow-unrecorded"])  # as explain.py's docstring has it
    report = REPORT_DIR / "m4_mistakes.md"
    assert explain_calls == [
        {"report": report, "allow_unrecorded": False},
        {"report": None, "allow_unrecorded": False},
        {"report": report, "allow_unrecorded": True},
    ]


def test_results_of_another_run_are_an_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stale(**kwargs: object) -> None:
        raise StaleResultsError("segment_scores.parquet was built from another scoring run")

    monkeypatch.setattr(explain, "run_explain", stale)
    with pytest.raises(SystemExit, match=r"^error: segment_scores\.parquet was built from"):
        cli.main(["explain"])
    # And the same from `python -m race_engineer.inference.explain`.
    with pytest.raises(SystemExit, match=r"^error: segment_scores\.parquet was built from"):
        explain.main([])


def test_style_gets_the_remaining_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(style, "run_style", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(SystemExit) as exit_:
        cli.main(["style", "--help"])
    assert exit_.value.code == 0
    assert "usage: race-engineer-infer style" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_:
        cli.main(["style", "--bogus"])
    assert exit_.value.code == 2 and calls == []
    cli.main(["style", "--chance-reps", "3", "--no-report"])
    assert calls == [{"report": None, "chance_reps": 3}]


@pytest.mark.skipif(find_spec("onnxruntime") is None, reason="onnxruntime is not installed")
def test_onnx_gets_the_remaining_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from race_engineer.inference import onnx_export

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(onnx_export, "run", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(SystemExit) as exit_:
        cli.main(["onnx", "--help"])
    assert exit_.value.code == 0
    assert "usage: race-engineer-infer onnx" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_:
        cli.main(["onnx", "--bogus"])
    assert exit_.value.code == 2 and calls == []


def test_score_and_select_refuse_unknown_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(score, "run_scoring", lambda *args, **kwargs: calls.append(args))
    for argv in (["score", "--bogus"], ["select", "mae.pt", "--bogus"]):
        with pytest.raises(SystemExit) as exit_:
            cli.main(argv)
        assert exit_.value.code == 2
    assert calls == []


def test_select_retires_the_results_of_the_previous_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "models" / "selected" / "mae.pt"
    results = tmp_path / "results"
    monkeypatch.setattr(score, "SELECTED_CHECKPOINT", selected)
    monkeypatch.setattr(cli, "RESULTS_DIR", results)
    selected.parent.mkdir(parents=True)
    results.mkdir()
    (selected.parent / "mae_scorer.onnx").write_bytes(b"old graph")
    (results / MANIFEST).write_text(json.dumps({"run_id": "old run"}))
    (results / "segment_scores.parquet").write_bytes(b"old scores")

    with pytest.raises(SystemExit) as exit_:  # a typo must not cost the current results
        cli.main(["select", str(tmp_path / "missing.pt")])
    assert exit_.value.code == 2 and (results / MANIFEST).exists()

    checkpoint = tmp_path / "new.pt"
    checkpoint.write_bytes(b"new weights")
    cli.main(["select", str(checkpoint)])
    assert selected.read_bytes() == b"new weights"
    assert not (selected.parent / "mae_scorer.onnx").exists()
    assert not (results / MANIFEST).exists()  # so nothing builds on or serves the old results
    assert "rebuild them" in capsys.readouterr().out

    # Selecting the selected file again changes nothing, so it must not cost the new results.
    (results / MANIFEST).write_text(json.dumps({"run_id": "new run"}))
    with pytest.raises(SystemExit) as exit_:
        cli.main(["select", str(selected)])
    assert exit_.value.code == 2 and (results / MANIFEST).exists()
    assert selected.read_bytes() == b"new weights"


def test_the_command_line_prints_help_without_running_explain(tmp_path: Path) -> None:
    # An empty data directory: were the step to run after all, it couldn't touch real results.
    done = subprocess.run(
        [sys.executable, "-m", "race_engineer.inference.cli", "explain", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "RACE_ENGINEER_DATA": str(tmp_path)},
    )
    assert done.returncode == 0, done.stderr
    assert "usage: race-engineer-infer explain" in done.stdout
