"""Ablation grids run on a cloud GPU, laid out on a shared volume, then installed locally.

engine/modal_app.py supplies the GPU containers, the volume and the commands. Everything else
lives here, free of Modal, so it runs and is tested on the Mac. On the volume:

    bundles/<name>/                   an export_bundle() directory
    runs/<run id>/<grid>.toml         the grid as launched (the file name keeps the grid's name)
    runs/<run id>/run.json            which variants were launched
    runs/<run id>/parts/<variant>/    results.json, the checkpoint and a one-variant report

One folder per variant, because variants may train in parallel containers and a volume keeps
only the last write when two containers write the same file. A variant that already finished is
skipped when a run restarts (a preempted container, or a relaunch with the same run id).
`install_run` turns a downloaded run into the local layout: checkpoints and a merged
results.json under models/ablations/<run id>/, the grid's report under report/, and one MLflow
run per variant in the "ablations" experiment, as a local grid would have logged.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import tomllib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch

from race_engineer.config import REPORT_DIR
from race_engineer.evaluation.tracking import mlflow_experiment
from race_engineer.models.experiments import (
    ABLATIONS_DIR,
    Grid,
    log_variant,
    parse_grid,
    run_grid,
    write_report,
)
from race_engineer.models.tensors import TelemetryData, load_bundle
from race_engineer.models.train import pick_device

log = logging.getLogger(__name__)

BUNDLES, RUNS, PARTS = "bundles", "runs", "parts"
RESULTS = "results.json"
LAUNCH = "run.json"
STAMP = "%Y%m%d-%H%M%S"
UNFINISHED = "no result yet: still running, or its container stopped before finishing"


def new_run_id(grid_name: str, now: datetime | None = None) -> str:
    """`<grid>-cloud-<UTC time>`: unique per launch, and recognisable next to local runs."""
    return f"{grid_name}-cloud-{(now or datetime.now(UTC)).strftime(STAMP)}"


def launch_time(run_id: str) -> str:
    """Sort key for run ids, oldest first, across grids: the UTC time at the end."""
    return run_id.rsplit("-cloud-", 1)[-1]


def grid_from_text(text: str, file_name: str) -> Grid:
    """The grid as load_grid would read it from `file_name` (its stem names the grid)."""
    return parse_grid(tomllib.loads(text), Path(file_name).stem)


def start_run(
    run_dir: Path, grid_text: str, grid_file: str, names: list[str] | None = None
) -> tuple[Grid, list[str]]:
    """Check the variant names and record the grid and the launch in run_dir.

    Safe to repeat (a restart, or every parallel container): launched variants accumulate.
    A run keeps the grid it was launched with: resuming it with any other grid (or an edited
    copy) is refused, since finished variants are skipped by name alone. Returns the grid and
    the variants to run now (default: all of them).
    """
    grid = grid_from_text(grid_text, grid_file)
    names = list(names or [v.name for v in grid.variants])
    unknown = set(names) - {v.name for v in grid.variants}
    if unknown:
        raise ValueError(f"no variant(s) {sorted(unknown)} in grid {grid.name!r}")
    stored = sorted(run_dir.glob("*.toml"))
    if stored and [(p.name, p.read_text()) for p in stored] != [(f"{grid.name}.toml", grid_text)]:
        raise ValueError(
            f"run {run_dir.name} was launched with {', '.join(p.name for p in stored)}, not this "
            f"{grid.name}.toml: resume it with the grid it was launched with"
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{grid.name}.toml").write_text(grid_text)
    launched = {*_read_json(run_dir / LAUNCH, {"variants": []})["variants"], *names}
    variants = [v.name for v in grid.variants if v.name in launched]
    _write_json(run_dir / LAUNCH, {"run_id": run_dir.name, "grid": grid.name, "variants": variants})
    return grid, names


def run_variants(
    run_dir: Path,
    grid_text: str,
    grid_file: str,
    bundle: Path,
    names: list[str] | None = None,
    after_each: Callable[[], None] | None = None,
    device: torch.device | None = None,
) -> list[dict[str, Any]]:
    """Train and evaluate `names` (default: the whole grid) one at a time from the bundle.

    Returns one result per variant: its report row (None if it failed), the error, the
    per-epoch history, the device and the wall time. `after_each` runs after every variant; on
    Modal it commits the volume, so finished variants survive a lost container.
    """
    grid, names = start_run(run_dir, grid_text, grid_file, names)
    device = device or pick_device()
    data: TelemetryData | None = None
    results = []
    for name in names:
        part = run_dir / PARTS / name
        earlier = _read_json(part / RESULTS, None)
        if earlier is not None and earlier["row"] is not None:
            log.info("variant %s finished in an earlier attempt: skipped", name)
            results.append(earlier)
            continue
        if data is None:  # not before it's needed: a restart may have nothing left to do
            data = load_bundle(bundle)
            log.info("bundle %s: %d segments", bundle, len(data))
        started = time.time()
        _, rows, failed = run_grid(
            grid,
            name_prefix=run_dir.name,
            data=data,
            only=[name],
            log_mlflow=False,
            report_dir=part,
            checkpoint_root=part,
            device=device,
        )
        row, history = (rows[0] if rows else None), []
        if row is not None:
            checkpoint = Path(row["checkpoint"])
            state = torch.load(checkpoint, map_location="cpu", weights_only=False)
            history = state.get("history", [])
            row["checkpoint"] = checkpoint.relative_to(run_dir).as_posix()  # survives copying
        result = _plain(
            {
                "variant": name,
                "row": row,
                "error": failed.get(name),
                "history": history,
                "device": device_name(device),
                "seconds": time.time() - started,
            }
        )
        _write_json(part / RESULTS, result)
        results.append(result)
        if after_each is not None:
            after_each()
    return results


def device_name(device: torch.device) -> str:
    return torch.cuda.get_device_name(device) if device.type == "cuda" else device.type


def merge_parts(
    grid: Grid, parts: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Report rows and failures in grid order (parallel variants finish in any order)."""
    by_name = {part["variant"]: part for part in parts}
    rows: list[dict[str, Any]] = []
    failed: dict[str, str] = {}
    for variant in grid.variants:
        part = by_name.get(variant.name)
        if part is None:
            continue
        if part["row"] is not None:
            rows.append(part["row"])
        else:
            failed[variant.name] = part.get("error") or "no result"
    return rows, failed


def install_run(
    run_dir: Path,
    models_dir: Path = ABLATIONS_DIR,
    report_dir: Path = REPORT_DIR,
    log_mlflow: bool = True,
) -> tuple[Path, Path]:
    """Install a downloaded run locally: returns (report, checkpoint folder).

    The report is rebuilt from every finished variant, so it's whole even when variants
    trained in separate containers; launched variants without a result are listed as such.
    """
    grids = sorted(run_dir.glob("*.toml"))
    if len(grids) != 1:
        raise FileNotFoundError(f"{run_dir}: expected one grid .toml, found {len(grids)}")
    grid = grid_from_text(grids[0].read_text(), grids[0].name)
    everything = {"variants": [v.name for v in grid.variants]}
    launched = _read_json(run_dir / LAUNCH, everything)["variants"]
    parts = [p for n in launched if (p := _read_json(run_dir / PARTS / n / RESULTS, None))]
    rows, failed = merge_parts(grid, parts)
    unfinished = set(launched) - {r["variant"] for r in rows} - set(failed)
    failed = {
        v.name: failed.get(v.name, UNFINISHED)
        for v in grid.variants
        if v.name in failed or v.name in unfinished
    }

    run_id = run_dir.name
    folder = models_dir / run_id
    folder.mkdir(parents=True, exist_ok=True)
    for row in rows:
        target = folder / f"{row['variant']}.pt"
        shutil.copyfile(run_dir / row["checkpoint"], target)
        row["checkpoint"] = str(target)
    shutil.copyfile(grids[0], folder / grids[0].name)
    histories = {p["variant"]: p.get("history", []) for p in parts}
    summary = {
        "run_id": run_id,
        "grid": grid.name,
        "rows": rows,
        "failed": failed,
        "devices": {p["variant"]: p.get("device") for p in parts},
        "history": histories,
    }
    _write_json(folder / RESULTS, summary)
    report = write_report(grid, run_id, rows, report_dir / grid.report, failed)
    if log_mlflow and rows:
        try:  # the files are in place already; tracking is a convenience
            logged = log_to_mlflow(grid, run_id, rows, histories)
            log.info("logged %d variant(s) to the MLflow experiment 'ablations'", logged)
        except Exception:
            log.exception("run %s: MLflow logging failed", run_id)
    return report, folder


def log_to_mlflow(
    grid: Grid, run_id: str, rows: list[dict[str, Any]], histories: dict[str, list]
) -> int:
    """One run per variant, as run_grid logs them; variants an earlier fetch of the same run
    already logged are skipped. Returns how many were logged."""
    mlflow = mlflow_experiment("ablations")
    variants = {v.name: v for v in grid.variants}
    logged = 0
    for row in rows:
        name = f"{run_id}-{row['variant']}"
        existing = mlflow.search_runs(
            experiment_names=["ablations"],
            filter_string=f"attributes.run_name = '{name}'",
            output_format="list",
        )
        if existing:
            continue
        state = {"history": histories.get(row["variant"], [])}
        log_variant(mlflow, name, grid, variants[row["variant"]], row, state)
        logged += 1
    return logged


def _plain(value: Any) -> Any:
    """Plain JSON types (numpy scalars and arrays become Python numbers and lists)."""
    return json.loads(json.dumps(value, default=_numpy_to_python))


def _numpy_to_python(value: Any) -> Any:
    if isinstance(value, np.generic | np.ndarray):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=_numpy_to_python) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default
