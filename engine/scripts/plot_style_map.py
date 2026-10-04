"""Figure: the Telemetry Transformer's style embedding, mapped to 2D.

Each point is one driver at one event: the mean [CLS] embedding of their clean corners there.
The same map is drawn twice, coloured by team (teammates share a colour, the second driver of
each team drawn hollow) and by event, to show whether the embedding groups drivers or just
conditions. t-SNE from scikit-learn stands in for UMAP (no numba dependency). Uses the held-out
events of the checkpoint's split. Writes report/figures/style_map.png.

    uv run python scripts/plot_style_map.py <checkpoint> [--split test]
"""

import argparse
from pathlib import Path
from typing import cast

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.manifold import TSNE

from race_engineer.config import REPORT_DIR
from race_engineer.evaluation.data import load_eval_frame
from race_engineer.evaluation.deep import checkpoint_split, load_checkpoint, score_and_embed
from race_engineer.models.tensors import load_or_build_tensors
from race_engineer.models.train import event_keys, pick_device

MIN_CORNERS = 40  # a driver-event point needs at least this many clean corners


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    args = parser.parse_args()

    device = pick_device()
    model, state = load_checkpoint(args.checkpoint, device)
    data = load_or_build_tensors(load_eval_frame())
    split, scheme, _ = checkpoint_split(state, data.frame)
    frame = data.frame
    clean = frame["lap_class"].isin(["push", "race"]).to_numpy() if "lap_class" in frame else True
    rows = np.flatnonzero((split.to_numpy() == args.split) & clean)
    subset = data.subset(rows)
    _, embeddings = score_and_embed(model, subset, device)

    meta = subset.frame.assign(event_key=event_keys(subset.frame))
    points = []
    for key, idx in meta.groupby(["driver", "team", "event_key", "event"]).indices.items():
        if len(idx) >= MIN_CORNERS:
            driver, team, event_key, event = (str(v) for v in cast(tuple, key))
            points.append((driver, team, event_key, event, embeddings[idx].mean(axis=0)))
    table = pd.DataFrame(points, columns=["driver", "team", "event_key", "event", "embedding"])
    x = np.stack(table["embedding"].to_numpy())
    x = (x - x.mean(axis=0)) / (x.std(axis=0) + 1e-6)
    xy = TSNE(perplexity=min(30, len(x) // 4), random_state=0, init="pca").fit_transform(x)
    table["x"], table["y"] = xy[:, 0], xy[:, 1]

    teams = sorted(table["team"].unique())
    team_colour = {t: plt.get_cmap("tab20")(i % 20) for i, t in enumerate(teams)}
    first_driver = table.groupby("team")["driver"].min().to_dict()
    events = sorted(table["event_key"].unique(), key=lambda k: tuple(map(int, k.split("-"))))
    shade = plt.get_cmap("viridis")
    event_colour = {e: shade(i / max(len(events) - 1, 1)) for i, e in enumerate(events)}
    event_name = dict(zip(table["event_key"], table["event"], strict=True))

    fig, axes = plt.subplots(1, 2, figsize=(13, 6))
    for _, r in table.iterrows():
        colour = team_colour[r["team"]]
        hollow = r["driver"] != first_driver[r["team"]]
        axes[0].scatter(
            r["x"], r["y"], s=36, edgecolors=[colour], facecolors="none" if hollow else [colour]
        )
        axes[1].scatter(r["x"], r["y"], s=30, color=event_colour[r["event_key"]])
    for (driver, team), part in table.groupby(["driver", "team"]):
        axes[0].annotate(
            str(driver), (part["x"].mean(), part["y"].mean()), fontsize=7, ha="center",
            color=team_colour[team], weight="bold",
        )  # fmt: skip
    axes[0].set_title("Coloured by team (hollow: second driver); labels at each driver's centre")
    for e in events:
        name = event_name[e].replace(" Grand Prix", "")
        axes[1].scatter([], [], color=event_colour[e], label=name)
    axes[1].legend(fontsize=7, loc="best", ncol=2)
    axes[1].set_title("Coloured by event")
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    fig.suptitle(
        f"Style embedding, one point per driver per event ({args.split} events of the {scheme} "
        f"split, {len(table)} points; t-SNE)",
        y=0.99,
    )
    fig.tight_layout()
    out = REPORT_DIR / "figures" / "style_map.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
