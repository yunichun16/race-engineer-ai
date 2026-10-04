"""Style probes within one season: train on its first races, test on the later ones.

The main probes train on 2022-2024 and test on 2026, which asks whether a style signature
survives new cars and drivers changing teams. This asks the simpler question, with the car
held (roughly) fixed: from one corner, can a linear probe tell the team, and the driver? It
compares the Telemetry Transformer's embedding with the hand-crafted features.
Writes report/m3_style_within_season.md.

    uv run python scripts/probe_within_season.py <checkpoint> [--year 2026] [--train-events 8]
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from race_engineer.config import REPORT_DIR
from race_engineer.data.qa import markdown_table
from race_engineer.evaluation.data import load_eval_frame
from race_engineer.evaluation.deep import load_checkpoint, score_and_embed
from race_engineer.models.baselines import _probe_features
from race_engineer.models.finetune import year_events
from race_engineer.models.tensors import load_or_build_tensors
from race_engineer.models.train import event_keys, pick_device

MAX_TRAIN = 100_000


def top1(x: np.ndarray, y: np.ndarray, train: np.ndarray, test: np.ndarray) -> float:
    probe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=300))
    probe.fit(np.nan_to_num(x[train]), y[train])
    return float((probe.predict(np.nan_to_num(x[test])) == y[test]).mean())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--train-events", type=int, default=8)
    args = parser.parse_args()

    device = pick_device()
    model, _ = load_checkpoint(args.checkpoint, device)
    data = load_or_build_tensors(load_eval_frame())
    frame = data.frame
    events = year_events(frame, args.year)
    train_events = events[: args.train_events]
    keys = event_keys(frame)
    rows = np.flatnonzero(
        keys.isin(events).to_numpy() & frame["lap_class"].isin(["push", "race"]).to_numpy()
    )
    subset = data.subset(rows)
    _, embedding = score_and_embed(model, subset, device)
    features = {"Transformer embedding": embedding, "hand-crafted": _probe_features(frame)[rows]}
    meta = subset.frame
    in_train = event_keys(meta).isin(train_events).to_numpy()
    sample = np.random.default_rng(0).choice(
        np.flatnonzero(in_train), min(MAX_TRAIN, int(in_train.sum())), replace=False
    )
    test = np.flatnonzero(~in_train)

    table = []
    for target in ("team", "driver"):
        y = meta[target].astype(str).to_numpy()
        classes = sorted(set(y[sample]) & set(y[test]))
        train_rows, test_rows = sample[np.isin(y[sample], classes)], test[np.isin(y[test], classes)]
        row = {"predict": target, "classes": str(len(classes)), "chance": f"{1 / len(classes):.3f}"}
        for name, x in features.items():
            row[name] = f"{top1(x, y, train_rows, test_rows):.3f}"
        table.append(row)

    names = (
        meta.drop_duplicates("round").set_index("round")["event"].sort_index()
        .str.replace(" Grand Prix", "")
    )  # fmt: skip
    n_test = len(events) - args.train_events
    parts = [
        f"# Style probes within {args.year}",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `scripts/probe_within_season.py` "
        f"from `{args.checkpoint.name}`._",
        f"Logistic regression on one corner at a time, trained on the first {args.train_events} "
        f"{args.year} events ({', '.join(names.iloc[: args.train_events])}) and tested on the "
        f"other {n_test}. Unlike the main probes (train 2022-2024, test 2026), the cars stay the "
        "same between training and test, so this measures how much of a team's and a driver's "
        "signature is in one corner. The embedding never saw team or driver labels.",
        markdown_table(pd.DataFrame(table)),
    ]
    out = REPORT_DIR / "m3_style_within_season.md"
    out.write_text("\n\n".join(parts) + "\n")
    print(out.read_text())


if __name__ == "__main__":
    main()
