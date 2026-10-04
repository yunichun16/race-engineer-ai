"""Command line for evaluation.

uv run race-engineer-eval features             # cache hand-crafted segment features
uv run race-engineer-eval baselines            # run baselines, write report/baselines.md
uv run race-engineer-eval baselines --split leave_tracks_out
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from race_engineer.evaluation.splits import SCHEMES


def main() -> None:
    parser = argparse.ArgumentParser(prog="race-engineer-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    feats = sub.add_parser("features", help="compute segment features for new sessions")
    feats.add_argument("--force", action="store_true")
    base = sub.add_parser("baselines", help="run baseline detectors and probes")
    base.add_argument("--split", choices=SCHEMES, default="auto")
    base.add_argument("--no-mlflow", action="store_true")
    base.add_argument(
        "--bootstrap", type=int, default=200, help="bootstrap resamples for intervals"
    )
    deep = sub.add_parser("deep", help="evaluate a trained Telemetry Transformer")
    deep.add_argument("--checkpoint", type=Path, default=None, help="defaults to the newest")
    deep.add_argument("--split", choices=SCHEMES, default="auto")
    deep.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.command == "features":
        from race_engineer.evaluation.data import build_features

        print(f"features computed for {build_features(force=args.force)} sessions")
    elif args.command == "baselines":
        from race_engineer.evaluation.baselines import run

        path = run(args.split, log_mlflow=not args.no_mlflow, bootstrap=args.bootstrap)
        print(path.read_text())
    else:
        from race_engineer.evaluation.deep import run as run_deep

        path = run_deep(args.checkpoint, args.split, log_mlflow=not args.no_mlflow)
        print(path.read_text())


if __name__ == "__main__":
    main()
