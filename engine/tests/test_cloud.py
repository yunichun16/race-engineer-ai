import importlib.util
import json
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
import torch
from test_experiments import synthetic_data

from race_engineer.cloud.runs import (
    LAUNCH,
    PARTS,
    RESULTS,
    UNFINISHED,
    install_run,
    launch_time,
    new_run_id,
    run_variants,
    start_run,
)
from race_engineer.models.tensors import export_bundle

ENGINE = Path(__file__).resolve().parents[1]
GRID = """
report = "ablations_cloud_test.md"

[base]
d_model = 16
n_heads = 2
n_layers = 1
embed_dim = 8
dropout = 0.0
epochs = 1
batch_size = 256
warmup_steps = 1
max_val = 400
bootstrap = 0

[[variant]]
name = "transformer"

[[variant]]
name = "bad_heads"
n_heads = 3  # 16 dims can't split 3 ways: this variant fails

[[variant]]
name = "later"
"""


def test_run_ids_sort_by_launch_time() -> None:
    first = new_run_id("smoke", datetime(2026, 9, 30, 8, 0, 0, tzinfo=UTC))
    second = new_run_id("ablations", datetime(2026, 9, 30, 9, 30, 0, tzinfo=UTC))
    assert first == "smoke-cloud-20260930-080000"
    assert sorted([second, first], key=launch_time) == [first, second]


def test_start_run_checks_names_and_accumulates_launches(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    with pytest.raises(ValueError, match="no variant"):
        start_run(run_dir, GRID, "tiny.toml", ["nope"])
    grid, names = start_run(run_dir, GRID, "tiny.toml", ["later"])
    assert grid.name == "tiny" and names == ["later"]
    start_run(run_dir, GRID, "tiny.toml", ["transformer"])  # a second parallel container
    launched = json.loads((run_dir / LAUNCH).read_text())["variants"]
    assert launched == ["transformer", "later"]  # grid order
    assert (run_dir / "tiny.toml").read_text() == GRID


def test_a_run_resumes_only_with_the_grid_it_was_launched_with(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    start_run(run_dir, GRID, "tiny.toml")
    other = GRID.replace("epochs = 1", "epochs = 2")
    for text, name in ((GRID, "smoke.toml"), (other, "tiny.toml")):  # another grid, or edited
        with pytest.raises(ValueError, match=r"launched with tiny\.toml"):
            start_run(run_dir, text, name, ["transformer"])
    assert [p.name for p in run_dir.glob("*.toml")] == ["tiny.toml"]
    assert json.loads((run_dir / LAUNCH).read_text())["variants"] == [
        "transformer",
        "bad_heads",
        "later",
    ]
    start_run(run_dir, GRID, "tiny.toml", ["later"])  # the same grid resumes


def test_cloud_run_from_bundle_to_local_install(tmp_path: Path) -> None:
    bundle = export_bundle(tmp_path / "bundle", synthetic_data())
    run_dir = tmp_path / "volume" / "runs" / "tiny-cloud-20260930-080000"
    start_run(run_dir, GRID, "tiny.toml", ["later"])  # launched in parallel, not finished
    commits: list[int] = []
    results = run_variants(
        run_dir,
        GRID,
        "tiny.toml",
        bundle,
        ["transformer", "bad_heads"],
        after_each=lambda: commits.append(1),
        device=torch.device("cpu"),
    )
    assert [r["variant"] for r in results] == ["transformer", "bad_heads"] and len(commits) == 2
    done, failed = results
    assert failed["row"] is None and failed["error"]
    assert (run_dir / PARTS / "bad_heads" / RESULTS).exists()
    assert done["history"] and done["device"] == "cpu"
    checkpoint = done["row"]["checkpoint"]
    assert not Path(checkpoint).is_absolute() and (run_dir / checkpoint).exists()
    assert json.loads((run_dir / PARTS / "transformer" / RESULTS).read_text()) == done

    # A restart skips what finished, without even reading the bundle.
    again = run_variants(run_dir, GRID, "tiny.toml", tmp_path / "gone", ["transformer"])
    assert again == [done]

    report, folder = install_run(
        run_dir, models_dir=tmp_path / "models", report_dir=tmp_path / "report", log_mlflow=False
    )
    assert folder == tmp_path / "models" / run_dir.name
    summary = json.loads((folder / RESULTS).read_text())
    assert [r["variant"] for r in summary["rows"]] == ["transformer"]
    assert Path(summary["rows"][0]["checkpoint"]) == folder / "transformer.pt"
    assert (folder / "transformer.pt").exists() and (folder / "tiny.toml").exists()
    assert list(summary["failed"]) == ["bad_heads", "later"]
    assert summary["failed"]["later"] == UNFINISHED
    text = report.read_text()
    assert report == tmp_path / "report" / "ablations_cloud_test.md"
    assert "transformer" in text and "`bad_heads`" in text and "`later`" in text


def test_modal_app_defines_the_app() -> None:
    modal = pytest.importorskip("modal", reason="the cloud dependency group isn't installed")
    spec = importlib.util.spec_from_file_location("modal_app", ENGINE / "modal_app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # builds the app, image and volume handles; no network
    assert module.app.name == "race-engineer"
    assert isinstance(module.train, modal.Function) and isinstance(module.fan_out, modal.Function)
    assert isinstance(module.main, modal.app.LocalEntrypoint)


# The Modal apps' images and modal_api.py's deploy and upload checks (no network: the app, image,
# volume and secret handles are lazy, and nothing here deploys or uploads).


def load_modal_file(name: str, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, list[dict]]:
    """Run engine/<name>.py as a module, recording the keyword arguments of each
    `Image.uv_sync` call it makes."""
    modal = pytest.importorskip("modal", reason="the cloud dependency group isn't installed")
    calls: list[dict] = []
    real = modal.Image.uv_sync

    def spy(self, *args, **kwargs):
        calls.append(kwargs)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(modal.Image, "uv_sync", spy)
    spec = importlib.util.spec_from_file_location(name, ENGINE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def torch_groups() -> list[str]:
    pyproject = tomllib.loads((ENGINE / "pyproject.toml").read_text())
    groups = pyproject["dependency-groups"]
    return [name for name, deps in groups.items() if any(d.startswith("torch") for d in deps)]


def test_the_training_image_syncs_the_group_with_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    _, calls = load_modal_file("modal_app", monkeypatch)
    assert torch_groups() == ["train"]
    assert calls == [
        {"groups": ["train"], "extra_options": "--no-default-groups", "uv_version": "0.12.19"}
    ]


def test_the_api_image_leaves_the_training_group_out(monkeypatch: pytest.MonkeyPatch) -> None:
    modal = pytest.importorskip("modal", reason="the cloud dependency group isn't installed")
    for name in ("SITE_ORIGIN", "PREVIEW_REGEX"):
        monkeypatch.delenv(name, raising=False)
    module, calls = load_modal_file("modal_api", monkeypatch)
    assert calls == [{"extra_options": "--no-default-groups", "uv_version": "0.12.19"}]
    assert module.app.name == "race-engineer-api" and isinstance(module.api, modal.Function)
    assert module.CORS_ORIGINS == "none" and module.ORIGIN_REGEX == ""


def test_the_api_environment_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = load_modal_file("modal_api", monkeypatch)
    env = module.serve_env("https://site.example")
    assert env == {
        "RACE_ENGINEER_DATA": "/data",
        "RACE_ENGINEER_HOST": "0.0.0.0",
        "RACE_ENGINEER_MCP_STATELESS": "1",
        "RACE_ENGINEER_LIMITS": "on",
        "RACE_ENGINEER_LIMITS_STORE": "upstash",
        "RACE_ENGINEER_CORS_ORIGINS": "https://site.example",
        "PYTHONUNBUFFERED": "1",
    }
    with_previews = module.serve_env("none", r"^https://p-[a-z0-9]+\.vercel\.app$")
    assert with_previews["RACE_ENGINEER_CORS_ORIGIN_REGEX"] == r"^https://p-[a-z0-9]+\.vercel\.app$"
    # On the deploying machine from SITE_ORIGIN; in a container, read back from the image's own
    # environment, so both build the same image.
    shell = {"SITE_ORIGIN": " https://site.example/ ", "ANTHROPIC_API_KEY": "not-a-real-key"}
    local = module.cors_settings(shell, local=True)
    assert local == ("https://site.example", "")
    assert module.cors_settings(module.serve_env(*local), local=False) == local
    assert module.cors_settings({}, local=True) == ("none", "")
    assert module.cors_settings({}, local=False) == ("none", "")
    assert "ANTHROPIC_API_KEY" not in module.serve_env(*local)


def test_only_the_modal_cli_starting_the_app_counts_as_a_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, _ = load_modal_file("modal_api", monkeypatch)
    assert module.modal_command(["/x/.venv/bin/modal", "deploy", "modal_api.py"]) == "deploy"
    assert module.modal_command(["/x/modal/__main__.py", "serve", "modal_api.py"]) == "serve"
    assert module.modal_command(["/x/.venv/bin/modal", "volume", "ls"]) is None
    assert module.modal_command(["modal_api.py", "upload"]) is None
    assert module.modal_command(["/x/.venv/bin/pytest", "tests"]) is None


def write_bundles(folder: Path, names: tuple[str, ...], text: str = "<html>chart</html>") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / f"{name}.html").write_text(text)
    return folder


def test_a_deploy_needs_the_chart_bundles_and_a_clean_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, _ = load_modal_file("modal_api", monkeypatch)
    names = module.chart_bundles()
    assert len(names) == 6 and "explain-corner" in names
    ui = write_bundles(tmp_path / "ui", names)
    assert module.deploy_problems({}, ui) == []
    assert module.deploy_problems({"SITE_ORIGIN": "https://a.vercel.app"}, ui) == []

    missing = module.deploy_problems({}, tmp_path / "empty")
    assert len(missing) == 6 and all("not built: run `make mcp-app`" in p for p in missing)
    placeholder = write_bundles(tmp_path / "placeholder", names, "<p>Chart bundle not built.</p>")
    assert all("placeholder" in p for p in module.deploy_problems({}, placeholder))

    gateway = module.deploy_problems({"ANTHROPIC_BASE_URL": "https://gateway.example"}, ui)
    assert len(gateway) == 1 and "ANTHROPIC_BASE_URL is set" in gateway[0]
    for origin in ("http://a.vercel.app", "https://a.vercel.app/chat", "a.vercel.app"):
        assert "SITE_ORIGIN must be" in module.deploy_problems({"SITE_ORIGIN": origin}, ui)[0]
    assert "PREVIEW_REGEX" in module.deploy_problems({"PREVIEW_REGEX": "(unclosed"}, ui)[0]


def test_modal_deploy_stops_before_anything_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["/x/.venv/bin/modal", "deploy", "modal_api.py"])
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    with pytest.raises(SystemExit, match="not deploying:\n- ANTHROPIC_BASE_URL is set"):
        load_modal_file("modal_api", monkeypatch)


def write_serve_bundle(folder: Path, modes: tuple[str, ...] = ("anthropic",)) -> Path:
    for part in ("processed/laps", "results", "saved"):
        (folder / part).mkdir(parents=True)
    (folder / "processed" / "laps" / "2026_01_R.parquet").write_bytes(b"PAR1")
    (folder / "results" / "mistakes.parquet").write_bytes(b"PAR1")
    for i, mode in enumerate(modes):
        (folder / "saved" / f"answer-{i}.json").write_text(json.dumps({"mode": mode}))
    (folder / "SERVE_MANIFEST.json").write_text(json.dumps({"v": 1, "allow_fake": False}))
    return folder


def test_upload_refuses_an_unchecked_or_scripted_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, _ = load_modal_file("modal_api", monkeypatch)
    assert module.bundle_problems(write_serve_bundle(tmp_path / "ok")) == []
    assert "no SERVE_MANIFEST.json" in module.bundle_problems(tmp_path / "nothing")[0]
    scripted = module.bundle_problems(write_serve_bundle(tmp_path / "fake", ("anthropic", "fake")))
    assert len(scripted) == 1 and "scripted answers (answer-1)" in scripted[0]
    with pytest.raises(SystemExit, match="not uploading"):
        module.upload(tmp_path / "fake", dry_run=True)


def test_upload_dry_run_lists_the_volume_paths_without_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module, _ = load_modal_file("modal_api", monkeypatch)
    bundle = write_serve_bundle(tmp_path / "serve")

    def no_volume(*args, **kwargs):
        raise AssertionError("a dry run must not look up the volume")

    monkeypatch.setattr(module.modal.Volume, "from_name", no_volume)
    module.cli(["upload", str(bundle), "--dry-run"])
    out = capsys.readouterr().out
    for remote, files in (("/processed", 1), ("/results", 1), ("/saved", 1)):
        assert f"{remote} " in out and f"{files} file(s)" in out
    assert "/SERVE_MANIFEST.json" in out and "nothing uploaded" in out
    plan = module.upload_plan(bundle)
    assert [(remote, files) for _, remote, files, _ in plan] == [
        ("/processed", 1),
        ("/results", 1),
        ("/saved", 1),
        ("/SERVE_MANIFEST.json", 1),
    ]
    assert "no saved answers" not in out

    # Before any answer is recorded with Claude the bundle has no saved/: listed, and said.
    empty = write_serve_bundle(tmp_path / "unsaved", modes=())
    (empty / "saved").rmdir()
    module.cli(["upload", str(empty), "--dry-run"])
    out = capsys.readouterr().out
    assert "/saved " in out and "no saved answers" in out and "nothing uploaded" in out
    assert [files for _, remote, files, _ in module.upload_plan(empty) if remote == "/saved"] == [0]


# `race-engineer-api` in a container (engine/Dockerfile): the port from $PORT, no access log, and
# uvicorn never takes the client address from X-Forwarded-For (the limits decide what to trust).


def run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str], port: str | None) -> dict:
    from race_engineer.api import main as api_main

    started: dict = {}
    monkeypatch.setattr(api_main.uvicorn, "run", lambda app, **kwargs: started.update(kwargs))
    monkeypatch.setenv("RACE_ENGINEER_HOST", "127.0.0.1")
    if port is None:
        monkeypatch.delenv("PORT", raising=False)
    else:
        monkeypatch.setenv("PORT", port)
    api_main.main(argv)
    return started


def test_the_api_takes_its_port_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from race_engineer.api.main import default_port

    assert run_main(monkeypatch, [], None)["port"] == 8000
    assert run_main(monkeypatch, [], "8080")["port"] == 8080
    assert run_main(monkeypatch, ["--port", "9000"], "8080")["port"] == 9000
    for bad in ("http", "0", "70000"):
        with pytest.raises(ValueError, match="PORT must be a port number"):
            default_port({"PORT": bad})
    with pytest.raises(SystemExit):
        run_main(monkeypatch, [], "http")


def test_the_api_can_run_without_an_access_log(monkeypatch: pytest.MonkeyPatch) -> None:
    started = run_main(monkeypatch, ["--host", "0.0.0.0", "--no-access-log"], "8080")
    assert started["access_log"] is False and started["host"] == "0.0.0.0"
    assert started["proxy_headers"] is False
    assert os.environ["RACE_ENGINEER_HOST"] == "0.0.0.0"
    assert run_main(monkeypatch, [], None)["access_log"] is True
