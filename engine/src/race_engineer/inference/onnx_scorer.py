"""Score corner segments with the exported ONNX model, without PyTorch.

`race_engineer.inference.onnx_export` writes models/selected/mae_scorer.onnx: one graph that does
everything `score_and_embed` (evaluation/deep.py) does for a batch, the five complementary masks,
the four mistake scores and the [CLS] style embedding. Serving it needs only numpy and
onnxruntime: this module deliberately imports nothing that pulls in torch, pandas or the data
store, so a server that scores new laps starts quickly and can run where PyTorch isn't installed.

    scorer = OnnxScorer()                                # models/selected/mae_scorer.onnx
    scores, embeddings = scorer.score_and_embed(data)    # data: models.tensors.TelemetryData

The inputs are the arrays of `TelemetryData` as they are: all five SIGNAL channels (the graph
picks the ones its model reconstructs), normalised per corner and session by build_tensors.

onnxruntime (1.30, macOS wheel) starts a telemetry uploader as soon as it is imported. Besides
sending usage data, its worker thread can race the process's exit and abort it (SIGABRT, about
one run in ten after an export). ORT_DISABLE_TELEMETRY=1 stops it, but only if it is set before
onnxruntime is first imported, so this module sets it (unless it is already set) before its own
import. Import this module, or onnx_export, before anything else that imports onnxruntime.

The file records the SHA-256 of the checkpoint it was exported from. `race-engineer-infer select`
removes the ONNX file when it replaces models/selected/mae.pt, but a checkpoint put there any
other way would leave it serving the old model while the batch scores come from the new one; so
`OnnxScorer` compares the two and refuses a stale file (StaleScorerError) until it is exported
again.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from race_engineer.config import repo_root

if TYPE_CHECKING:  # only for annotations: importing it at run time pulls in pandas and duckdb
    from race_engineer.models.tensors import TelemetryData

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")  # before importing onnxruntime
import onnxruntime as ort

# The same path as inference.score.SELECTED_CHECKPOINT, which can't be imported here (torch).
SELECTED_CHECKPOINT = repo_root() / "models" / "selected" / "mae.pt"
SCORER_PATH = SELECTED_CHECKPOINT.with_name("mae_scorer.onnx")
INPUTS = ("signal", "context_channels", "context_cat", "context_num")
# Not "embedding": the exporter already uses that name for the output of nn.Embedding layers.
EMBEDDING = "cls_embedding"


class StaleScorerError(RuntimeError):
    """The ONNX file was exported from another checkpoint than the one it is checked against."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OnnxScorer:
    """An onnxruntime session over the exported scorer, with score_and_embed's interface."""

    def __init__(
        self,
        path: Path = SCORER_PATH,
        threads: int | None = None,
        profile_dir: Path | None = None,
        checkpoint: Path | None = SELECTED_CHECKPOINT,
    ) -> None:
        """`threads`: onnxruntime's intra-op threads (default: one per physical core).
        `profile_dir`: record a kernel profile there (read it with `session.end_profiling()`).
        `checkpoint`: the checkpoint the file must have been exported from (default: the
        selected one). When the file records a checkpoint SHA-256 and `checkpoint` exists, they
        must match, or StaleScorerError is raised; None skips the check."""
        if not path.exists():
            raise FileNotFoundError(
                f"no ONNX scorer at {path}: run `uv run python -m "
                "race_engineer.inference.onnx_export` first"
            )
        options = ort.SessionOptions()
        if threads:
            options.intra_op_num_threads = threads
        if profile_dir is not None:
            options.enable_profiling = True
            options.profile_file_prefix = str(profile_dir / "onnxruntime")
        self.path = path
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        outputs = self.session.get_outputs()
        self.score_names = [o.name for o in outputs if o.name != EMBEDDING]
        self.embed_dim = int(next(o.shape[1] for o in outputs if o.name == EMBEDDING))
        # What the exporter recorded: the model's config, the source checkpoint and so on.
        self.metadata = dict(self.session.get_modelmeta().custom_metadata_map)
        recorded = self.metadata.get("checkpoint_sha256", "")
        stale = (
            recorded
            and checkpoint is not None
            and checkpoint.exists()
            and file_sha256(checkpoint) != recorded
        )
        if stale:
            raise StaleScorerError(
                f"{path} was exported from another checkpoint than {checkpoint} (its SHA-256 "
                f"starts {recorded[:12]}): export it again with `uv run python -m "
                "race_engineer.inference.onnx_export`, or pass checkpoint= the file it came "
                "from (None skips this check)"
            )

    @property
    def model_cfg(self) -> dict:
        return json.loads(self.metadata.get("model_cfg", "{}"))

    def run(
        self,
        signal: np.ndarray,  # (B, 5, 80): every SIGNAL channel
        context_channels: np.ndarray,  # (B, 2, 80)
        context_cat: np.ndarray,  # (B, 2) integers
        context_num: np.ndarray,  # (B, 4)
    ) -> dict[str, np.ndarray]:
        """One batch: {score name: (B,), EMBEDDING: (B, embed_dim)}."""
        feed = {
            "signal": np.asarray(signal, np.float32),
            "context_channels": np.asarray(context_channels, np.float32),
            "context_cat": np.asarray(context_cat, np.int64),
            "context_num": np.asarray(context_num, np.float32),
        }
        names = [o.name for o in self.session.get_outputs()]
        values = self.session.run(names, feed)
        return {name: np.asarray(value) for name, value in zip(names, values, strict=True)}

    def score_and_embed(
        self, data: TelemetryData, batch: int = 4096
    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
        """Mistake scores and style embeddings for every segment, as score_and_embed returns
        them (same score names, float32)."""
        n = len(data)
        scores = {name: np.empty(n, np.float32) for name in self.score_names}
        embeddings = np.empty((n, self.embed_dim), np.float32)
        for start in range(0, n, batch):
            rows = slice(start, min(start + batch, n))
            out = self.run(
                data.signal[rows],
                data.context_channels[rows],
                data.context_cat[rows],
                data.context_num[rows],
            )
            for name in self.score_names:
                scores[name][rows] = out[name]
            embeddings[rows] = out[EMBEDDING]
        return scores, embeddings
