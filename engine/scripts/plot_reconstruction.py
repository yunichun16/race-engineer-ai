"""Figure: what the Telemetry Transformer predicts for hidden parts of a corner.

Picks a labelled track-limits segment and a normal lap by the same driver at the same corner,
hides the middle of each corner (around the apex), and plots the model's prediction with a
+/-2 sigma band against what actually happened. Writes report/figures/mae_reconstruction.png.

    uv run python scripts/plot_reconstruction.py [checkpoint]
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch

from race_engineer.config import REPORT_DIR, WINDOW_BEFORE_M
from race_engineer.evaluation.data import POSITIVE, load_eval_frame
from race_engineer.evaluation.deep import latest_checkpoint, load_checkpoint
from race_engineer.models.tensors import SIGNAL, TelemetryData, build_tensors
from race_engineer.models.train import pick_device

LABELS = {
    "speed": "Speed (z vs field)",
    "throttle": "Throttle (z vs field)",
    "offset_m": "Line offset (z)",
}


def main() -> None:
    device = pick_device()
    checkpoint = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_checkpoint()
    model, _ = load_checkpoint(checkpoint, device)
    frame = load_eval_frame()
    positive = frame[frame["label"] == POSITIVE].iloc[0]
    same = frame[
        (
            frame[["year", "round", "session", "driver", "corner"]]
            == positive[["year", "round", "session", "driver", "corner"]]
        ).all(axis=1)
        & (frame["label"] == 0)
    ]
    # Normalise against the whole session (as training does), then keep the two laps.
    key = ["year", "round", "session"]
    session = frame[(frame[key] == positive[key]).all(axis=1)]
    full = build_tensors(session)
    keep = (
        full.frame["segment_id"]
        .isin([positive["segment_id"], same.iloc[0]["segment_id"]])
        .to_numpy()
    )
    data = TelemetryData(
        frame=full.frame[keep].reset_index(drop=True),
        signal=full.signal[keep],
        context_channels=full.context_channels[keep],
        context_cat=full.context_cat[keep],
        context_num=full.context_num[keep],
    )

    cfg = model.cfg
    mask = torch.zeros(len(data), cfg.n_patches, dtype=torch.bool)
    mask[:, 4:7] = True  # hide 120 m around the apex
    with torch.no_grad():
        mean, logvar, _ = model(
            torch.from_numpy(data.signal).float().to(device),
            torch.from_numpy(data.context_channels).float().to(device),
            torch.from_numpy(data.context_cat).to(device),
            torch.from_numpy(data.context_num).to(device),
            mask.to(device),
        )
    mean, std = mean.cpu().numpy(), np.exp(0.5 * logvar.cpu().numpy())
    x = np.arange(cfg.n_points) * 5 - WINDOW_BEFORE_M
    hidden = slice(4 * cfg.patch, 7 * cfg.patch)
    channels = ["speed", "throttle", "offset_m"]

    fig, axes = plt.subplots(len(channels), len(data), figsize=(11, 7), sharex=True, squeeze=False)
    for col in range(len(data)):
        meta = data.frame.iloc[col]
        title = "labelled track-limits lap" if meta["label"] == POSITIVE else "a normal lap"
        for row, channel in enumerate(channels):
            ax = axes[row, col]
            c = SIGNAL.index(channel)
            ax.axvspan(
                x[hidden][0], x[hidden][-1], color="0.93", zorder=0, label="hidden from the model"
            )
            ax.plot(x, data.signal[col, c], color="0.2", lw=1.3, label="what happened")
            ax.plot(
                x[hidden], mean[col, c, hidden], color="tab:blue", lw=1.5, label="model prediction"
            )
            ax.fill_between(
                x[hidden],
                (mean - 2 * std)[col, c, hidden],
                (mean + 2 * std)[col, c, hidden],
                color="tab:blue",
                alpha=0.2,
                label="±2σ",  # noqa: RUF001 (sigma is intended)
            )
            ax.set_ylabel(LABELS[channel]) if col == 0 else None
            ax.grid(alpha=0.25)
        axes[0, col].set_title(
            f"{meta['driver']} {meta['event']} {meta['year']} ({meta['session']}), "
            f"turn {meta['corner']}, lap {meta['lap_number']}: {title}",
            fontsize=9,
        )
        axes[-1, col].set_xlabel("Metres from the apex")
    axes[0, 0].legend(loc="lower left", fontsize=7)
    fig.suptitle("Telemetry Transformer: reconstructing a hidden 120 m around the apex", y=0.99)
    fig.tight_layout()
    out = REPORT_DIR / "figures" / "mae_reconstruction.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
