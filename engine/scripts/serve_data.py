"""Build the served-data bundle: only the files the deployed API reads (plan M7 section 6.3).

The API needs 6.4 GB of the data folder's 32 GB: the seven processed tables of every session,
the circuit rotations, the M4 results the tools read and the chat's saved example answers. This
writes them to one folder, data/serve/ by default, which `python modal_api.py upload` copies to
the Modal volume (and the Cloud Run fallback mounts from a bucket):

    processed/{sessions,laps,lap_grids,segments,corners,tracks,events}/*.parquet
    processed/circuits.json
    results/{mistakes,segment_scores,style_corners,style_embeddings,style_embedding_pairs,
             style_axes}.parquet
    results/manifest.json          the scoring run's manifest, `checkpoint` cut to its file name
    saved/*.json                   the saved answers the API would serve (Claude's only, unless
                                   --allow-fake)
    SERVE_MANIFEST.json            every file with its size, the scoring run, the counts and the
                                   bundle's health; written last, once the bundle checks out

The data files are hard links: no disk space, and they upload as ordinary files. Nothing is ever
written through a link (that would change the source file too): the results manifest and the
saved answers are new files, written to a temporary name and renamed. The bundle is rebuilt from
scratch on every run, so a bundle built with --allow-fake never keeps its scripted answers, and
the source data is only read. Then the bundle is checked the way the API sees it: its health
(`routes.health.data_status`) must be ok and the same as the source data's (sessions, mistakes,
the scoring run, the style tables, the circuits).

    uv run python scripts/serve_data.py                 # data/ -> data/serve/ (make serve-data)
    uv run python scripts/serve_data.py --allow-fake    # with scripted saved answers (local)
    uv run python scripts/serve_data.py --data <dir> --out <dir> [--saved <dir>]

It refuses an output folder inside the repository that git doesn't ignore (the bundle is F1
data, never committed), one that holds or is inside the source data, one that holds the saved
answers it copies, and an existing folder that isn't a bundle it built.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from race_engineer.api.routes.health import data_status
from race_engineer.api.routes.saved import saved_index
from race_engineer.config import DATA_DIR, repo_root
from race_engineer.tools.registry import DataPaths

TABLES = ("sessions", "laps", "lap_grids", "segments", "corners", "tracks", "events")
PROCESSED_FILES = ("circuits.json",)
RESULTS_FILES = (
    "mistakes.parquet",
    "segment_scores.parquet",
    "style_corners.parquet",
    "style_embeddings.parquet",
    "style_embedding_pairs.parquet",
    "style_axes.parquet",
)
RESULTS_MANIFEST = "manifest.json"
MANIFEST = "SERVE_MANIFEST.json"
MARKER = ".serve-data"  # written first: this folder is a bundle (maybe an unfinished one)
FORMAT_VERSION = 1
DEFAULT_OUT = repo_root() / "data" / "serve"


class BundleError(RuntimeError):
    """The bundle can't be built where or from what was asked; the message says why."""


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _git_ignored(path: Path) -> bool | None:
    """Whether git ignores `path`: None outside a work tree (or without git)."""
    probe = next(p for p in (path, *path.parents) if p.exists())
    try:
        done = subprocess.run(
            ["git", "-C", str(probe), "check-ignore", "-q", str(path)], capture_output=True
        )
    except FileNotFoundError:
        return None
    return {0: True, 1: False}.get(done.returncode)


def check_target(out: Path, data: Path, saved: Path | None = None) -> None:
    """Raise BundleError unless `out` can be (re)built: outside the source data, not holding it
    or the saved answers it copies, ignored by git when inside a work tree, and either new,
    empty or a bundle built here."""
    out, data = out.resolve(), data.resolve()
    for source in (data / "processed", data / "results"):
        if _inside(out, source) or _inside(source, out):
            raise BundleError(f"{out} overlaps the source data ({source}): pick another --out")
    if saved is not None and _inside(saved.resolve(), out):
        raise BundleError(
            f"the saved answers ({saved.resolve()}) are inside {out}, which is rebuilt from "
            "scratch: pass --saved the folder they were recorded into"
        )
    # A file in the bundle: a directory pattern (data/, serve/) matches a folder not made yet.
    if _git_ignored(out / MANIFEST) is False:
        raise BundleError(
            f"{out} is inside the repository but not ignored by git: the bundle is F1 data and "
            "must never be committed (data/serve/ is ignored)"
        )
    if out.exists():
        if not out.is_dir():
            raise BundleError(f"{out} exists and isn't a folder")
        if any(out.iterdir()) and not ((out / MARKER).exists() or (out / MANIFEST).exists()):
            raise BundleError(f"{out} isn't a bundle this script built: remove it yourself")


def _link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError as exc:
        raise BundleError(
            f"can't hard-link {source} into {target.parent} ({exc.strerror}): the bundle must be "
            "on the same disk as the data"
        ) from exc


def _write_new(target: Path, data: bytes) -> None:
    """Write `data` as a new file: a temporary name, then a rename, so an existing file at
    `target` (or a hard link to one) is replaced, never written through."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(target)


def served_manifest(source: Path) -> bytes:
    """The scoring run's manifest with `checkpoint` cut to its file name (it holds an absolute
    path on the machine that scored). `run_id`, all the tools read, is kept."""
    manifest = json.loads(source.read_text())
    if isinstance(manifest.get("checkpoint"), str):
        manifest["checkpoint"] = Path(manifest["checkpoint"]).name
    return (json.dumps(manifest, indent=2) + "\n").encode()


def build(
    data: Path, out: Path, saved: Path | None = None, *, allow_fake: bool = False
) -> dict[str, Any]:
    """Build the bundle from `data` (processed/ and results/) and `saved` (default
    data/saved) into `out`, check it, and return what SERVE_MANIFEST.json records."""
    saved = data / "saved" if saved is None else saved
    check_target(out, data, saved)
    if out.exists():
        shutil.rmtree(out)  # removes the links, never the files they point to
    out.mkdir(parents=True)
    (out / MARKER).write_text("built by engine/scripts/serve_data.py\n")

    linked: list[str] = []
    missing: list[str] = []

    def link(relative: str) -> None:
        source = data / relative
        if source.is_file():
            _link(source, out / relative)
            linked.append(relative)
        else:
            missing.append(relative)

    for table in TABLES:
        files = sorted((data / "processed" / table).glob("*.parquet"))
        if not files:
            missing.append(f"processed/{table}/*.parquet")
        for path in files:
            link(f"processed/{table}/{path.name}")
    for name in PROCESSED_FILES:
        link(f"processed/{name}")
    for name in RESULTS_FILES:
        link(f"results/{name}")

    written: list[str] = []
    manifest_source = data / "results" / RESULTS_MANIFEST
    if manifest_source.is_file():
        _write_new(out / "results" / RESULTS_MANIFEST, served_manifest(manifest_source))
        written.append(f"results/{RESULTS_MANIFEST}")
    else:
        missing.append(f"results/{RESULTS_MANIFEST}")
    answers = saved_index(saved, allow_fake)
    for answer in answers:
        _write_new(
            out / "saved" / f"{answer['id']}.json", (saved / f"{answer['id']}.json").read_bytes()
        )
        written.append(f"saved/{answer['id']}.json")

    bundle = data_status(DataPaths(out / "processed", out / "results"))
    source = data_status(DataPaths(data / "processed", data / "results"))
    if bundle.problems or source.problems:
        raise BundleError(
            "the bundle's health isn't ok: "
            + "; ".join(bundle.problems or [f"source data: {p}" for p in source.problems])
        )
    if bundle.health != source.health:
        raise BundleError(
            f"the bundle ({bundle.health}) doesn't match its source ({source.health})"
        )

    files = [{"path": p, "size": (out / p).stat().st_size} for p in sorted(linked + written)]
    counts = {
        f"processed/{t}": sum(p.startswith(f"processed/{t}/") for p in linked) for t in TABLES
    }
    counts["results"] = sum(p.startswith("results/") for p in linked + written)
    counts["saved"] = len(answers)
    record = {
        "v": FORMAT_VERSION,
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results_run": bundle.health.results_run,
        "allow_fake": allow_fake,
        "health": bundle.health.model_dump(),
        "counts": counts,
        "saved": answers,
        "missing": missing,
        "files": files,
        "bytes": sum(f["size"] for f in files),
    }
    _write_new(out / MANIFEST, (json.dumps(record, indent=2) + "\n").encode())
    (out / MARKER).unlink()
    return record


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DATA_DIR, help="default: RACE_ENGINEER_DATA")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="default: data/serve")
    parser.add_argument("--saved", type=Path, default=None, help="default: <data>/saved")
    parser.add_argument(
        "--allow-fake", action="store_true", help="include saved answers from the scripted chat"
    )
    args = parser.parse_args(argv)
    try:
        record = build(args.data, args.out, args.saved, allow_fake=args.allow_fake)
    except BundleError as exc:
        sys.exit(f"serve_data: {exc}")
    health = record["health"]
    print(f"{args.out}: {len(record['files']):,} files, {record['bytes'] / 1e9:.2f} GB")
    for part, n in record["counts"].items():
        print(f"  {part:<20} {n:>5}")
    print(
        f"health ok, same as the source: {health['sessions']} sessions (latest "
        f"{health['latest']}), {health['mistakes']:,} mistakes, scoring run "
        f"{health['results_run']}, style {health['style']}, circuits {health['circuits']}"
    )
    saved = ", ".join(f"{a['id']} ({a['mode']})" for a in record["saved"]) or "none"
    print(f"saved answers: {saved}")
    if record["missing"]:
        print(f"not in the source data: {', '.join(record['missing'])}")
    print(f"wrote {args.out / MANIFEST}")


if __name__ == "__main__":
    main()
