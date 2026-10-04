"""The serving dependencies hold no training package (plan M7 decision 6).

[project].dependencies are what the API, the tools and the MCP server need. torch, MLflow,
FastF1, matplotlib and ONNX Runtime are in the `train` dependency group, which the default
groups include (so `uv sync`, `make check` and CI install everything as before) and the serving
images leave out (`uv sync --no-default-groups`: no torch, no CUDA wheels, no MLflow server
stack). What can regress is checked here: the lock resolves the serve set without a training
package, and importing the serving code loads none.
"""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
# Package names as uv.lock spells them; nvidia-* are torch's CUDA wheels on Linux.
TRAINING = {
    "torch",
    "triton",
    "mlflow",
    "mlflow-skinny",
    "mlflow-tracing",
    "fastf1",
    "matplotlib",
    "onnxruntime",
}
TRAINING_MODULES = ("torch", "triton", "mlflow", "fastf1", "matplotlib", "onnxruntime")
SERVING_MODULES = (
    "race_engineer.api.app",
    "race_engineer.tools.definitions",
    "race_engineer.mcp_server.server",
)


def lock_closure(lock: dict, root: str) -> set[str]:
    """Every package `root` pulls in through uv.lock, extras followed and every platform's
    markers counted (so a Linux-only CUDA wheel shows up on a Mac too)."""
    entries: dict[str, list[dict]] = {}
    for package in lock["package"]:
        entries.setdefault(package["name"], []).append(package)
    seen: set[str] = set()
    todo: list[tuple[str, tuple[str, ...]]] = [(root, ())]
    while todo:
        name, extras = todo.pop()
        key = f"{name}[{','.join(extras)}]"
        if key in seen:
            continue
        seen.add(key)
        for package in entries[name]:
            wanted = list(package.get("dependencies", []))
            for extra in extras:
                wanted += package.get("optional-dependencies", {}).get(extra, [])
            todo += [(d["name"], tuple(d.get("extra", ()))) for d in wanted]
    return {key.split("[")[0] for key in seen} - {root}


def test_the_serve_set_in_the_lock_has_no_training_package() -> None:
    lock = tomllib.loads((ENGINE / "uv.lock").read_text())
    closure = lock_closure(lock, "race-engineer")
    found = sorted(n for n in closure if n in TRAINING or n.startswith("nvidia-"))
    assert found == []
    # The serving code's own imports, pandas through the tools and httpx2 through the limits'
    # Upstash client, are direct dependencies, not left to arrive through training packages.
    assert {"fastapi", "mcp", "pandas", "httpx2", "duckdb", "pyarrow"} <= closure


def test_the_train_group_is_installed_by_default() -> None:
    pyproject = tomllib.loads((ENGINE / "pyproject.toml").read_text())
    train = {d.split(">")[0].split("=")[0] for d in pyproject["dependency-groups"]["train"]}
    assert {"torch", "mlflow", "fastf1", "matplotlib", "onnxruntime"} <= train
    assert {"dev", "train"} <= set(pyproject["tool"]["uv"]["default-groups"])


def test_the_serving_code_imports_no_training_package() -> None:
    probe = (
        "import importlib, json, sys\n"
        f"for module in {SERVING_MODULES!r}:\n"
        "    importlib.import_module(module)\n"
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules})))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=120
    )
    loaded = set(json.loads(done.stdout.strip().splitlines()[-1]))
    assert sorted(loaded & set(TRAINING_MODULES)) == []
    assert {"race_engineer", "fastapi", "mcp"} <= loaded
