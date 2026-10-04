"""Command line for M4 inference, in the order the steps run.

uv run race-engineer-infer select <checkpoint>   # the model the results come from
uv run race-engineer-infer score                 # score every eligible corner segment
uv run race-engineer-infer explain               # type and cost the flags -> mistakes.parquet
uv run race-engineer-infer style                 # teammate style profiles
uv run race-engineer-infer onnx                  # export the scorer for serving, parity-gated

`explain`, `style` and `onnx` pass any further arguments to their module's own parser (try
`--help` on each). `score` names its
run in data/results/manifest.json and the later steps refuse to build on another run's files
(inference/provenance.py). `select` removes the manifest: the results then belong to a checkpoint
that is no longer the selected one, and have to be rebuilt.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path

from race_engineer.config import RESULTS_DIR
from race_engineer.inference.provenance import MANIFEST, StaleResultsError

# The steps whose module parses its own arguments: main(argv, prog).
MODULES = {
    "explain": "race_engineer.inference.explain",
    "style": "race_engineer.inference.style",
    "onnx": "race_engineer.inference.onnx_export",
}


def _log_to_stderr() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", force=True
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="race-engineer-infer")
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select", help="copy a checkpoint to models/selected/mae.pt")
    select.add_argument("checkpoint", type=Path)
    score = sub.add_parser("score", help="score every eligible corner segment")
    score.add_argument("--checkpoint", type=Path, default=None)
    score.add_argument("--bootstrap", type=int, default=200)
    for name, help_text in (
        ("explain", "type and cost every flagged corner -> data/results/mistakes.parquet"),
        ("style", "driving-style profiles against the teammate -> data/results/style_*.parquet"),
        ("onnx", "export the scorer to models/selected/mae_scorer.onnx with a parity gate"),
    ):
        # No help of their own here: --help goes on to the step's parser with the rest.
        sub.add_parser(name, help=help_text, add_help=False)
    args, rest = parser.parse_known_args(argv)

    step_argv = [a for a in rest if a != "--"]
    prog = f"{parser.prog} {args.command}"
    try:
        if args.command in MODULES:
            importlib.import_module(MODULES[args.command]).main(step_argv, prog)
            return
    except StaleResultsError as err:
        raise SystemExit(f"error: {err}") from None
    if rest:
        parser.error(f"unrecognized arguments: {' '.join(rest)}")

    _log_to_stderr()
    from race_engineer.inference.score import SELECTED_CHECKPOINT, run_scoring

    if args.command == "select":
        if not args.checkpoint.is_file():
            parser.error(f"{args.checkpoint} not found")
        if SELECTED_CHECKPOINT.exists() and args.checkpoint.samefile(SELECTED_CHECKPOINT):
            parser.error(f"{args.checkpoint} is the selected checkpoint already")
        # The results and the served ONNX file come from the old checkpoint. Without the
        # manifest, the later steps and the tools refuse the results until they are rebuilt;
        # OnnxScorer would refuse the ONNX file anyway.
        manifest = RESULTS_DIR / MANIFEST
        had_results = manifest.exists()
        manifest.unlink(missing_ok=True)
        (SELECTED_CHECKPOINT.parent / "mae_scorer.onnx").unlink(missing_ok=True)
        SELECTED_CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.checkpoint, SELECTED_CHECKPOINT)
        (SELECTED_CHECKPOINT.parent / "SOURCE").write_text(f"{args.checkpoint.resolve()}\n")
        print(f"selected {args.checkpoint} -> {SELECTED_CHECKPOINT}")
        if had_results:
            print(f"the results in {RESULTS_DIR} came from the previous model: rebuild them")
        print("next: race-engineer-infer score, explain, style, onnx (or `make m4`)")
    elif args.command == "score":
        checkpoint = args.checkpoint or SELECTED_CHECKPOINT
        if not checkpoint.exists():
            parser.error(f"{checkpoint} not found: run `race-engineer-infer select <checkpoint>`")
        run_scoring(checkpoint, bootstrap=args.bootstrap)


if __name__ == "__main__":
    main()
