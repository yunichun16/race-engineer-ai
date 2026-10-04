"""Run the M3 ablation grid on a Modal cloud GPU (the Makefile's cloud-* targets wrap this).

One-time setup, with your own Modal account: `uv run --group cloud modal setup`.

    make cloud-bundle                               # export the dataset -> ../data/bundle
    make cloud-upload                               # copy it to the Modal volume
    make cloud-train                                # configs/smoke.toml: a few GPU minutes
    make cloud-train GRID=configs/ablations.toml    # the full grid, variants in turn on one L4
    make cloud-train GRID=configs/ablations.toml ARGS=--parallel   # one GPU per variant
    make cloud-fetch                                # newest run -> models/ablations/, report/

Behind the targets (run from engine/):

    uv run --group cloud modal run --detach modal_app.py --grid configs/ablations.toml
        [--gpu A100] [--hours 12] [--only baseline,cnn] [--parallel] [--bundle current]
    uv run --group cloud modal run --detach modal_app.py --run-id <id>
        # resume a run on the volume with the grid it was launched with (no --grid needed);
        # finished variants are skipped
    uv run --group cloud python modal_app.py upload [../data/bundle] [--name current]
    uv run --group cloud python modal_app.py runs
    uv run --group cloud python modal_app.py fetch [--run <id>] [--no-mlflow]

The image installs the locked dependencies with the `train` group (uv.lock, so torch with its
CUDA wheels on Linux; the API's image, modal_api.py, leaves that group out) and mounts the local
race_engineer source when a container starts: code edits need no image rebuild. The
volume holds uploaded bundles and every run's outputs (layout in race_engineer/cloud/runs.py).
MLflow stays on the Mac: the GPU side writes JSON results next to its checkpoints and `fetch`
logs them to the local "ablations" experiment. `upload`, `runs` and `fetch` talk to the volume
directly, so only training builds the image.

Containers import this file too, so only the standard library and modal are imported at the
top; race_engineer is imported where it's used.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import modal
from modal.exception import NotFoundError
from modal.types import FileEntryType

ENGINE = Path(__file__).resolve().parent
APP_NAME = VOLUME_NAME = "race-engineer"
MOUNT = "/vol"  # the volume inside containers
# Nested like the repo, because config.repo_root() walks up three levels from config.py.
REMOTE_SRC = "/app/engine/src"
DEFAULT_GPU = "L4"
DEFAULT_HOURS = 12.0
MAX_TIMEOUT_S = 24 * 3600  # Modal's ceiling for one function call
TRAIN_GROUP = "train"  # pyproject's dependency group with torch, MLflow and FastF1
DEFAULT_BUNDLE = "current"
DEFAULT_GRID = "configs/smoke.toml"
LOCAL_BUNDLE = ENGINE.parent / "data" / "bundle"
BUNDLE_FILES = ("manifest.json", "frame.parquet", "tensors.npz")  # what export_bundle writes

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.13")
    # The serving dependencies plus the training group, no dev tools, with the uv version that
    # wrote uv.lock. Without the group the image would have no torch.
    .uv_sync(
        str(ENGINE),
        groups=[TRAIN_GROUP],
        extra_options="--no-default-groups",
        uv_version="0.12.19",
    )
    .env({"PYTHONUNBUFFERED": "1"})
    .add_local_dir(
        ENGINE / "src" / "race_engineer",
        f"{REMOTE_SRC}/race_engineer",
        ignore=["**/__pycache__", "**/*.pyc", "mcp_server/ui"],
    )
)


def _container_setup() -> None:
    if REMOTE_SRC not in sys.path:
        sys.path.insert(0, REMOTE_SRC)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")


@app.function(
    image=image,
    gpu=DEFAULT_GPU,
    volumes={MOUNT: volume},
    cpu=8.0,  # pandas and the probes' logistic regression
    memory=32 * 1024,  # the bundle in RAM (about 1.2 kB per segment) plus evaluation copies
    timeout=int(DEFAULT_HOURS * 3600),
)
def train(run_id: str, grid_text: str, grid_file: str, bundle: str, names: list[str]) -> list:
    """Train and evaluate `names` from the grid, one after another, on this container's GPU."""
    _container_setup()
    import torch

    from race_engineer.cloud.runs import BUNDLES, RUNS, run_variants

    if not torch.cuda.is_available():  # never fall back to hours of CPU training on a GPU bill
        raise RuntimeError(
            f"torch {torch.__version__} (CUDA {torch.version.cuda}) sees no GPU: check the GPU "
            "type, and that the NVIDIA driver supports the CUDA build locked in uv.lock"
        )
    logging.info("torch %s on %s", torch.__version__, torch.cuda.get_device_name())
    # TF32 matmuls on Ampere and newer GPUs: much faster float32 training, the same for every
    # variant of a cloud grid (Mac runs use full float32).
    torch.set_float32_matmul_precision("high")
    return run_variants(
        Path(MOUNT, RUNS, run_id),
        grid_text,
        grid_file,
        Path(MOUNT, BUNDLES, bundle),
        names,
        after_each=volume.commit,  # each finished variant survives a lost container
        device=torch.device("cuda"),
    )


@app.function(image=image, volumes={MOUNT: volume}, memory=2048, timeout=MAX_TIMEOUT_S)
def fan_out(
    run_id: str,
    grid_text: str,
    grid_file: str,
    bundle: str,
    names: list[str],
    gpu: str,
    timeout_s: int,
) -> list:
    """--parallel: one GPU container per variant, started from here because a detached
    `modal run` keeps only the last function it called alive."""
    _container_setup()
    from race_engineer.cloud.runs import RUNS, start_run

    start_run(Path(MOUNT, RUNS, run_id), grid_text, grid_file, names)
    volume.commit()
    worker = train.with_options(gpu=gpu, timeout=timeout_s)
    calls = {name: worker.spawn(run_id, grid_text, grid_file, bundle, [name]) for name in names}
    parts = []
    for name, call in calls.items():
        try:
            parts += call.get()
        except Exception as error:  # the container died (out of memory, timeout): keep the rest
            message = f"container failed: {type(error).__name__}: {error}".splitlines()[0]
            parts.append({"variant": name, "row": None, "error": message[:300]})
    return parts


@app.local_entrypoint()
def main(
    grid: str = "",
    gpu: str = DEFAULT_GPU,
    hours: float = DEFAULT_HOURS,
    only: str = "",
    parallel: bool = False,
    bundle: str = DEFAULT_BUNDLE,
    run_id: str = "",
) -> None:
    """Launch a grid (a TOML path relative to engine/, default configs/smoke.toml) against an
    uploaded bundle, or resume run `run_id` with the grid it was launched with."""
    from race_engineer.cloud.runs import grid_from_text, new_run_id

    if run_id:
        grid_file, grid_text = remote_grid(run_id)
        if grid and (Path(grid).name != grid_file or Path(grid).read_text() != grid_text):
            sys.exit(
                f"run {run_id} was launched with a different {grid_file}: resume it without "
                "--grid (make cloud-train without GRID) to use the grid it was launched with"
            )
    else:
        path = Path(grid or DEFAULT_GRID)
        grid_file, grid_text = path.name, path.read_text()
    spec = grid_from_text(grid_text, grid_file)  # a bad grid fails here, before any GPU starts
    names = [n.strip() for n in only.split(",") if n.strip()] or [v.name for v in spec.variants]
    unknown = set(names) - {v.name for v in spec.variants}
    if unknown:
        sys.exit(f"no variant(s) {sorted(unknown)} in {grid_file}")
    if not 0 < hours <= MAX_TIMEOUT_S / 3600:
        sys.exit(f"--hours must be more than 0 and at most {MAX_TIMEOUT_S // 3600}")
    manifest = check_remote_bundle(bundle)
    run_id = run_id or new_run_id(spec.name)
    timeout_s = int(hours * 3600)
    mode = "one GPU per variant" if parallel else "one GPU, variants in turn"
    print(f"run {run_id}: {len(names)} variant(s) of {grid_file} on {gpu}, {mode}")
    print(f"bundle '{bundle}': {manifest['segments']:,} segments; limit {hours:g} h per container")
    print(f"fetch results any time (finished variants so far): make cloud-fetch RUN={run_id}")

    args = (run_id, grid_text, grid_file, bundle, names)
    if parallel:
        parts = fan_out.remote(*args, gpu, timeout_s)
    else:
        parts = train.with_options(gpu=gpu, timeout=timeout_s).remote(*args)
    for part in parts:
        row = part["row"]
        if row is None:
            print(f"{part['variant']}: FAILED: {part['error']}")
            continue
        print(
            f"{part['variant']}: {row['chosen']} AP test {row['chosen_avg_precision_test']:.3f}, "
            f"AP all {row['chosen_avg_precision_all']:.3f}, driver top-1 {row['driver_top1']:.3f}"
            f" (train {row['train_seconds'] / 60:.1f} min, eval {row['eval_seconds'] / 60:.1f}"
            f" min on {part.get('device', '?')})"
        )
    print(f"done: make cloud-fetch RUN={run_id}")


def remote_grid(run_id: str) -> tuple[str, str]:
    """(file name, text) of the grid a run on the volume was launched with; exits if the run
    isn't there, so a typo can't start a new run under a hand-picked id."""
    from race_engineer.cloud.runs import RUNS

    runs = list_runs()
    if run_id not in runs:
        sys.exit(
            f"no run {run_id!r} on volume '{VOLUME_NAME}' (it has: {', '.join(runs) or 'none'}): "
            "leave out --run-id to start a new run"
        )
    entries = volume.listdir(f"/{RUNS}/{run_id}")
    grids = [
        PurePosixPath(e.path).name
        for e in entries
        if e.type == FileEntryType.FILE and e.path.endswith(".toml")
    ]
    if len(grids) != 1:
        sys.exit(f"run {run_id} holds {len(grids)} grid files, expected one")
    return grids[0], b"".join(volume.read_file(f"/{RUNS}/{run_id}/{grids[0]}")).decode()


def check_remote_bundle(name: str) -> dict[str, Any]:
    """The uploaded bundle's manifest; exits if it's missing or this code can't load it."""
    from race_engineer.cloud.runs import BUNDLES
    from race_engineer.models.tensors import TENSOR_VERSION

    try:
        manifest = json.loads(b"".join(volume.read_file(f"{BUNDLES}/{name}/manifest.json")))
    except (FileNotFoundError, NotFoundError):
        sys.exit(f"no bundle '{name}' on volume '{VOLUME_NAME}': run `make cloud-upload` first")
    if manifest["tensor_version"] != TENSOR_VERSION:
        sys.exit(
            f"bundle '{name}' holds tensors v{manifest['tensor_version']}, this code builds "
            f"v{TENSOR_VERSION}: run `make cloud-bundle cloud-upload` again"
        )
    return manifest


def upload(bundle_dir: Path, name: str) -> None:
    import pyarrow.parquet as pq

    from race_engineer.cloud.runs import BUNDLES
    from race_engineer.models.tensors import TENSOR_VERSION

    if not (bundle_dir / "manifest.json").exists():  # export_bundle writes it last
        sys.exit(f"{bundle_dir} has no manifest.json (an unfinished export?): `make cloud-bundle`")
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    if manifest["tensor_version"] != TENSOR_VERSION:
        sys.exit(f"{bundle_dir} holds tensors v{manifest['tensor_version']}: `make cloud-bundle`")
    rows = pq.ParquetFile(bundle_dir / "frame.parquet").metadata.num_rows
    if rows != manifest["segments"]:
        sys.exit(f"{bundle_dir}: frame.parquet doesn't match its manifest: `make cloud-bundle`")
    size = sum((bundle_dir / f).stat().st_size for f in BUNDLE_FILES)
    print(
        f"uploading {bundle_dir} ({size / 1e9:.2f} GB, {manifest['segments']:,} segments) "
        f"to {VOLUME_NAME}:/{BUNDLES}/{name}"
    )
    with volume.batch_upload(force=True) as batch:
        for file in BUNDLE_FILES:
            batch.put_file(bundle_dir / file, f"/{BUNDLES}/{name}/{file}")
    print("done")


def list_runs() -> list[str]:
    """Run ids on the volume, oldest first."""
    from race_engineer.cloud.runs import RUNS, launch_time

    try:
        entries = volume.listdir(f"/{RUNS}")
    except (FileNotFoundError, NotFoundError):
        return []
    ids = [PurePosixPath(e.path).name for e in entries if e.type == FileEntryType.DIRECTORY]
    return sorted(ids, key=launch_time)


def show_runs() -> None:
    from race_engineer.cloud.runs import PARTS, RESULTS, RUNS

    runs = list_runs()
    if not runs:
        print(f"no runs on volume '{VOLUME_NAME}' yet")
    for run in runs:
        try:
            files = volume.listdir(f"/{RUNS}/{run}/{PARTS}", recursive=True)
        except (FileNotFoundError, NotFoundError):
            files = []
        finished = sum(PurePosixPath(f.path).name == RESULTS for f in files)
        print(f"{run}: {finished} variant(s) with results")


def download(remote: str, local: Path) -> int:
    """Copy a volume directory to `local`; returns the number of files."""
    count = 0
    for entry in volume.iterdir(remote, recursive=True):
        if entry.type != FileEntryType.FILE:
            continue
        target = local / PurePosixPath(entry.path).relative_to(remote.strip("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as f:
            for chunk in volume.read_file(entry.path):
                f.write(chunk)
        count += 1
    return count


def fetch(run: str | None, log_mlflow: bool) -> None:
    from race_engineer.cloud.runs import RUNS, install_run

    runs = list_runs()
    if not runs:
        sys.exit(f"no runs on volume '{VOLUME_NAME}' yet")
    run = run or runs[-1]
    if run not in runs:
        sys.exit(f"no run {run!r} on the volume; it has: {', '.join(runs)}")
    with tempfile.TemporaryDirectory() as tmp:
        files = download(f"{RUNS}/{run}", Path(tmp) / run)
        report, folder = install_run(Path(tmp) / run, log_mlflow=log_mlflow)
    print(f"{run}: {files} files; checkpoints and results.json in {folder}; report {report}")


def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="python modal_app.py", description="Bundles and results on the Modal volume."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    up = commands.add_parser("upload", help="copy an exported bundle to the volume")
    up.add_argument("bundle", type=Path, nargs="?", default=LOCAL_BUNDLE)
    up.add_argument("--name", default=DEFAULT_BUNDLE, help="bundle name on the volume")
    commands.add_parser("runs", help="list the runs on the volume")
    get = commands.add_parser("fetch", help="download a run into models/ablations/ and report/")
    get.add_argument("--run", default=None, help="run id (default: the newest)")
    get.add_argument("--no-mlflow", action="store_true", help="skip logging to local MLflow")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if args.command == "upload":
        upload(args.bundle, args.name)
    elif args.command == "runs":
        show_runs()
    else:
        fetch(args.run, log_mlflow=not args.no_mlflow)


if __name__ == "__main__":
    cli()
