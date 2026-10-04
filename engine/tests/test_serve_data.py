"""scripts/serve_data.py: the served-data bundle, built from a synthetic data tree."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from race_engineer.api.routes.health import data_status
from race_engineer.tools.registry import DataPaths
from race_engineer.tools.synthetic_style import write_style_results

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve_data.py"
spec = importlib.util.spec_from_file_location("serve_data", SCRIPT)
assert spec is not None and spec.loader is not None
serve_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve_data)

CHECKPOINT = "/Users/someone/race-engineer/models/selected/mae.pt"


def saved_answer(answer_id: str, mode: str) -> dict:
    return {
        "v": 1,
        "id": answer_id,
        "question": f"Question {answer_id}?",
        "recorded_at": "2026-10-04T13:05:12Z",
        "mode": mode,
        "events": [{"type": "done"}],
    }


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A complete synthetic data folder (health ok), plus what the bundle must leave out."""
    from test_tools_mistakes import RUN_ID, mistakes_dataset

    base = tmp_path_factory.mktemp("data")
    processed, results = mistakes_dataset(base)
    write_style_results(results, RUN_ID)
    (processed / "circuits.json").write_text("{}")
    manifest = json.loads((results / "manifest.json").read_text())
    (results / "manifest.json").write_text(json.dumps(manifest | {"checkpoint": CHECKPOINT}))
    # Not served: logs, the embeddings, the review folder, other result tables.
    (processed / "build.log").write_text("log")
    (processed / "laps" / "notes.txt").write_text("not a table")
    (results / "embeddings.npz").write_bytes(b"\0" * 16)
    (results / "style_profiles.parquet").write_bytes(b"PAR1")
    (results / "review").mkdir()
    (results / "review" / "queue.json").write_text("[]")
    saved = base / "saved"
    saved.mkdir()
    for answer_id, mode in (("monaco-2023", "anthropic"), ("latest", "fake")):
        (saved / f"{answer_id}.json").write_text(json.dumps(saved_answer(answer_id, mode)))
    assert data_status(DataPaths(processed, results)).problems == []
    return base


def files_under(folder: Path) -> set[str]:
    return {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}


def test_the_bundle_links_only_the_served_files(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "serve"
    record = serve_data.build(data, out)
    files = files_under(out)
    source_tables = {
        str(p.relative_to(data))
        for table in serve_data.TABLES
        for p in (data / "processed" / table).glob("*.parquet")
    }
    results = {f"results/{name}" for name in serve_data.RESULTS_FILES}
    assert files == source_tables | results | {
        "processed/circuits.json",
        "results/manifest.json",
        "saved/monaco-2023.json",
        serve_data.MANIFEST,
    }
    for relative in source_tables | results | {"processed/circuits.json"}:
        assert os.path.samefile(out / relative, data / relative), relative  # a hard link
    for relative in ("results/manifest.json", "saved/monaco-2023.json"):
        assert not os.path.samefile(out / relative, data / relative), relative  # a new file
    assert record["counts"]["saved"] == 1 and record["allow_fake"] is False
    assert {f["path"] for f in record["files"]} == files - {serve_data.MANIFEST}
    on_disk = json.loads((out / serve_data.MANIFEST).read_text())
    assert on_disk == record and on_disk["health"]["sessions"] == 2


def test_the_manifest_loses_the_local_path_and_keeps_the_run(data: Path, tmp_path: Path) -> None:
    source = data / "results" / "manifest.json"
    before = source.read_bytes()
    serve_data.build(data, tmp_path / "serve")
    served = json.loads((tmp_path / "serve" / "results" / "manifest.json").read_text())
    original = json.loads(before)
    assert served["checkpoint"] == "mae.pt"
    assert served == original | {"checkpoint": "mae.pt"}  # run_id and the rest kept
    assert source.read_bytes() == before  # never written through a link
    assert os.stat(source).st_nlink == 1


def test_the_bundle_is_healthy_like_its_source(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "serve"
    record = serve_data.build(data, out)
    bundle = data_status(DataPaths(out / "processed", out / "results"))
    assert bundle.problems == []
    assert bundle.health == data_status(DataPaths(data / "processed", data / "results")).health
    assert record["results_run"] == bundle.health.results_run is not None


def test_scripted_answers_only_with_allow_fake_and_rebuilt_from_scratch(
    data: Path, tmp_path: Path
) -> None:
    out = tmp_path / "serve"
    record = serve_data.build(data, out, allow_fake=True)
    assert sorted((out / "saved").iterdir()) == [
        out / "saved/latest.json",
        out / "saved/monaco-2023.json",
    ]
    assert record["allow_fake"] is True
    (out / "stale.txt").write_text("left from an earlier build")
    serve_data.build(data, out)
    assert [p.name for p in (out / "saved").iterdir()] == ["monaco-2023.json"]
    assert not (out / "stale.txt").exists()
    assert (data / "saved" / "latest.json").exists()  # the source is only read


def test_it_refuses_unsafe_targets(data: Path, tmp_path: Path) -> None:
    with pytest.raises(serve_data.BundleError, match="overlaps the source data"):
        serve_data.build(data, data / "processed" / "serve")
    with pytest.raises(serve_data.BundleError, match="overlaps the source data"):
        serve_data.build(data, data)
    stranger = tmp_path / "somebody-elses"
    stranger.mkdir()
    (stranger / "keep.txt").write_text("not a bundle")
    with pytest.raises(serve_data.BundleError, match="isn't a bundle this script built"):
        serve_data.build(data, stranger)
    assert (stranger / "keep.txt").exists()
    # Saved answers kept inside the bundle would be removed by the rebuild before being read.
    out = tmp_path / "serve"
    serve_data.build(data, out, allow_fake=True)
    with pytest.raises(serve_data.BundleError, match="which is rebuilt from scratch"):
        serve_data.build(data, out, out / "saved", allow_fake=True)
    assert sorted(p.name for p in (out / "saved").iterdir()) == ["latest.json", "monaco-2023.json"]


def test_it_refuses_a_tracked_path(data: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(serve_data.BundleError, match="not ignored by git"):
        serve_data.build(data, repo / "serve")
    assert not (repo / "serve").exists()
    (repo / ".gitignore").write_text("/serve/\n")
    serve_data.build(data, repo / "serve")
    assert (repo / "serve" / serve_data.MANIFEST).is_file()


def test_a_bundle_that_fails_its_check_has_no_manifest(data: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    (broken / "processed").mkdir(parents=True)
    (broken / "results").mkdir()
    out = tmp_path / "serve"
    with pytest.raises(serve_data.BundleError, match="health isn't ok"):
        serve_data.build(broken, out)
    assert not (out / serve_data.MANIFEST).exists()  # so `modal_api.py upload` refuses it
    serve_data.build(data, out)  # an unfinished bundle is rebuilt
