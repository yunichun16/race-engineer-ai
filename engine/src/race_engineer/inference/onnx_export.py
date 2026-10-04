"""Export the selected Telemetry Transformer to ONNX, so scoring can be served without PyTorch.

`score_and_embed` (evaluation/deep.py) runs the model six times per batch: once for each of
five complementary patch masks, so every 40 m patch is predicted without seeing itself, and
once with nothing hidden for the [CLS] embedding; the four mistake scores are reductions of the
errors. Exporting the bare model would leave all of that to the serving code, to be kept in step
with deep.py by hand. `SegmentScorer` wraps the model instead: its forward takes a batch of
model inputs and returns the four scores and the embedding, with the masks unrolled inside the
graph. The ONNX file is then the whole scoring step, and `OnnxScorer` (inference/onnx_scorer.py)
is a loop over batches that needs only numpy and onnxruntime.

The exporter is the torch.export-based one (`torch.onnx.export(..., dynamo=True)`), the default
since PyTorch 2.9; the TorchScript exporter is deprecated. It needs onnx and onnxscript, which
are dev dependencies: exporting is a developer step, serving needs only onnxruntime.

    uv run python -m race_engineer.inference.onnx_export                 # export, check, report
    uv run python -m race_engineer.inference.onnx_export --segments 0 --session none  # no real data

Writes models/selected/mae_scorer.onnx and report/m4_onnx.md. The check runs onnxruntime and
score_and_embed on CPU over random inputs, then over a random sample of real segments plus one
whole session (where the flags are decided), compares every output and the ranking, and times
both. The export only replaces the served file if it passes that check (the parity gate, see
PARITY_RTOL): a broken export after a torch or exporter upgrade must not quietly overwrite a good
one. It is written next to the served file first and moved over it atomically on a pass; on a
failure it is kept as mae_scorer.rejected.onnx and the command exits with status 1.

The stored M4 scores (data/results/) are compared too when they are the current scoring run's
and its manifest records this checkpoint's SHA-256. When there is a current scoring run, the
real segments must be the eval frame it scored (features computed or a session reprocessed
since would make the checks compare other data): otherwise the command stops and says to score
again.
"""

from __future__ import annotations

import argparse
import functools
import importlib.metadata
import json
import logging
import os
import platform
import tempfile
import time
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from scipy.stats import rankdata
from torch import nn

from race_engineer.config import REPORT_DIR, RESULTS_DIR
from race_engineer.data.qa import markdown_table
from race_engineer.evaluation.data import load_eval_frame
from race_engineer.evaluation.deep import EXIT, load_checkpoint, score_and_embed
from race_engineer.inference.onnx_scorer import (
    EMBEDDING,
    INPUTS,
    SCORER_PATH,
    OnnxScorer,
    file_sha256,
)
from race_engineer.inference.provenance import StaleResultsError, parquet_run, scoring_run
from race_engineer.inference.score import (
    FLAG_SHARE,
    SELECTED_CHECKPOINT,
    TRANSFORMER_SCORE,
    check_scored_frame,
    combinations,
    session_percentile,
)
from race_engineer.models.mae import MAEConfig, MaskedAutoencoder, gaussian_nll, point_mask
from race_engineer.models.tensors import (
    N_COMPOUNDS,
    N_NUMERIC,
    SIGNAL,
    TelemetryData,
    load_or_build_tensors,
)

log = logging.getLogger(__name__)

CPU = torch.device("cpu")
N_MASKS = 5  # as score_and_embed: each patch is hidden in exactly one of them
# Opset 23 has an Attention operator, so each scaled_dot_product_attention stays one node
# instead of a dozen primitives around it; in onnxruntime 1.30 that scores about 40% faster than
# opset 20 (the exporter's default), mostly through fewer transposes.
OPSET = 23

# The parity gate. An export replaces the served file only if onnxruntime reproduces PyTorch on
# CPU to under this relative difference on every gated output, on random inputs and on real
# segments. Measured on the selected model, over random segments, a whole race and the 3,000
# highest-scored segments: at most 1.1e-5 on a score (mae_error_speed_patch) and 7e-7 on the
# embedding. 1e-4 leaves room for float32 rounding, not for a wrong graph.
PARITY_RTOL = 1e-4
# Compared but not gated: the NLL sums terms of both signs (the log-variance is negative where
# the model is confident), so where it is near zero rounding alone gives large relative
# differences (0.2 measured on the top-scored segments). The flags don't use it.
UNGATED = frozenset({"mae_nll"})
SYNTHETIC_ROWS = 512  # random inputs, checked on every export: no real data needed
FLAGS_CHANGED = "flags that differ, ONNX vs PyTorch CPU"


def score_names(cfg: MAEConfig) -> list[str]:
    """The scores score_and_embed returns for this model, in its order."""
    channels = cfg.signal_channels
    names = ["mae_error", "mae_nll"] if cfg.objective == "mse" else ["mae_nll", "mae_error"]
    names += ["mae_error_exit"] if {"speed", "offset_m"} & set(channels) else []
    names += ["mae_error_speed_patch"] if "speed" in channels else []
    return names


class SegmentScorer(nn.Module):
    """score_and_embed for one batch as a single forward pass, so it exports as one graph.

    forward(signal, context_channels, context_cat, context_num) takes every SIGNAL channel (the
    model's own are picked inside) and returns the scores in score_names(cfg) order, then the
    [CLS] embedding. The masks are constants, so the loop over them unrolls into the graph.
    """

    masks: torch.Tensor  # (n_masks, n_patches) bool, True = hidden
    hidden: torch.Tensor  # (n_masks, 1, n_points) float: the same masks, per point
    channels: torch.Tensor  # positions of the model's channels in SIGNAL
    exit_channels: torch.Tensor  # speed and line offset, among the model's channels

    def __init__(self, model: MaskedAutoencoder, n_masks: int = N_MASKS) -> None:
        super().__init__()
        cfg = model.cfg
        self.model = model
        self.names = score_names(cfg)
        patches = torch.arange(cfg.n_patches)
        masks = torch.stack([(patches % n_masks) == k for k in range(n_masks)])
        channels = cfg.signal_channels
        exit_channels = [channels.index(c) for c in ("speed", "offset_m") if c in channels]
        self.register_buffer("masks", masks, persistent=False)
        self.register_buffer("hidden", point_mask(masks, cfg.patch).float(), persistent=False)
        self.register_buffer("channels", torch.tensor(cfg.channel_index), persistent=False)
        exit_index = torch.tensor(exit_channels, dtype=torch.long)
        self.register_buffer("exit_channels", exit_index, persistent=False)
        self.speed = channels.index("speed") if "speed" in channels else None
        self.eval()  # scoring only: no dropout

    def forward(
        self,
        signal: torch.Tensor,  # (B, len(SIGNAL), 80)
        context_channels: torch.Tensor,  # (B, n_context, 80)
        context_cat: torch.Tensor,  # (B, 2) int64
        context_num: torch.Tensor,  # (B, N_NUMERIC)
    ) -> tuple[torch.Tensor, ...]:
        cfg = self.model.cfg
        if cfg.signal_channels != SIGNAL:
            signal = signal.index_select(1, self.channels)
        n = signal.shape[0]
        inputs = (signal, context_channels, context_cat, context_num)
        nll = torch.zeros_like(signal)
        error = torch.zeros_like(signal)
        for k in range(len(self.masks)):
            mean, logvar, _ = self.model(*inputs, self.masks[k].expand(n, -1))
            if cfg.objective == "mse":  # the variance head is untrained: unit variance
                logvar = torch.zeros_like(mean)
            hidden = self.hidden[k]
            nll = nll + gaussian_nll(signal, mean, logvar) * hidden
            error = error + (signal - mean) ** 2 * hidden
        scores = {"mae_nll": nll.mean(dim=(1, 2)), "mae_error": error.mean(dim=(1, 2))}
        if "mae_error_exit" in self.names:
            exit_error = error.index_select(1, self.exit_channels)[:, :, EXIT]
            scores["mae_error_exit"] = exit_error.mean(dim=(1, 2))
        if self.speed is not None:  # worst 40 m of speed
            patches = error[:, self.speed].reshape(n, cfg.n_patches, cfg.patch).mean(dim=2)
            scores["mae_error_speed_patch"] = patches.amax(dim=1)
        nothing_hidden = torch.zeros_like(self.masks[0]).expand(n, -1)
        _, _, embedding = self.model(*inputs, nothing_hidden)
        return (*(scores[name] for name in self.names), embedding)


def example_inputs(cfg: MAEConfig, batch: int = 4, seed: int = 0) -> tuple[torch.Tensor, ...]:
    """Random inputs of the right shapes and types, to trace the graph with. More than one row,
    so the exporter can't mistake the batch dimension for a constant 1."""
    g = torch.Generator().manual_seed(seed)
    session = torch.randint(0, 2, (batch,), generator=g)
    compound = torch.randint(0, N_COMPOUNDS, (batch,), generator=g)
    return (
        torch.randn(batch, len(SIGNAL), cfg.n_points, generator=g),
        torch.randn(batch, cfg.n_context, cfg.n_points, generator=g),
        torch.stack([session, compound], dim=1),
        torch.rand(batch, N_NUMERIC, generator=g),
    )


def random_data(n: int, seed: int = 0) -> TelemetryData:
    """`n` segments of random model inputs (standard normal, like the z-scored channels, and
    float16 like the real arrays), with a frame of made-up segment ids."""
    rng = np.random.default_rng(seed)
    cat = np.column_stack([rng.integers(0, 2, n), rng.integers(0, N_COMPOUNDS, n)])
    return TelemetryData(
        frame=pd.DataFrame({"segment_id": [f"random-{i}" for i in range(n)]}),
        signal=rng.normal(size=(n, len(SIGNAL), 80)).astype(np.float16),
        context_channels=rng.normal(size=(n, 2, 80)).astype(np.float16),
        context_cat=cat.astype(np.int64),
        context_num=rng.random((n, N_NUMERIC)).astype(np.float32),
    )


def export_scorer(
    model: MaskedAutoencoder, path: Path, metadata: dict[str, str] | None = None
) -> Path:
    """Export SegmentScorer(model) to `path` as one self-contained ONNX file (weights inside)
    with a dynamic batch dimension. `metadata` is stored in the file, next to the model config
    and the score names."""
    try:
        import onnx
    except ImportError as err:  # a serving install has onnxruntime only
        raise ImportError("exporting needs the dev dependencies onnx and onnxscript") from err

    scorer = SegmentScorer(model).cpu().eval()
    batch = torch.export.Dim("batch")
    # The exporter reports every torchvision op it skips, the optimizer every initializer it
    # merges, and there is a note about the axis name.
    logging.getLogger("torch.onnx._internal.exporter._registration").setLevel(logging.ERROR)
    for name in ("onnxscript", "onnx_ir"):
        logging.getLogger(name).setLevel(logging.WARNING)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*axis name.*")
        program = torch.onnx.export(
            scorer,
            example_inputs(model.cfg),
            dynamo=True,
            input_names=list(INPUTS),
            output_names=[*scorer.names, EMBEDDING],
            dynamic_shapes={name: {0: batch} for name in INPUTS},
            opset_version=OPSET,
            verbose=False,
        )
    if program is None:
        raise RuntimeError("torch.onnx.export returned no program")
    proto = program.model_proto
    # Every node carries its Python stack trace (with local file paths): half the file's size.
    for node in proto.graph.node:
        del node.metadata_props[:]
    props = {
        "model_cfg": json.dumps(model.cfg.to_dict()),
        "score_names": json.dumps(scorer.names),
        "signal_input_channels": json.dumps(SIGNAL),
        "n_masks": str(len(scorer.masks)),
        "opset": str(OPSET),
        "torch_version": torch.__version__,
        "exported": datetime.now(UTC).isoformat(timespec="seconds"),
        **(metadata or {}),
    }
    onnx.helper.set_model_props(proto, props)
    proto.doc_string = (
        "Telemetry Transformer scorer: mistake scores and [CLS] style embedding for a batch of "
        "corner segments (race_engineer.inference.onnx_export)."
    )
    onnx.checker.check_model(proto, full_check=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(proto, path)
    return path


# --- checking the export against PyTorch -------------------------------------------------------


def differences(reference: dict[str, np.ndarray], candidate: dict[str, np.ndarray]) -> pd.DataFrame:
    """Per output: largest absolute difference, and relative differences per segment. For a
    score that is |a - b| / |b|; for the embedding, a vector, ||a - b|| / ||b|| (elementwise
    ratios blow up on components that happen to be near zero). The magnitude of the output
    where each maximum occurs tells rounding near zero from a real disagreement."""
    rows = []
    for name, ref in reference.items():
        ref64, got64 = ref.astype(np.float64), candidate[name].astype(np.float64)
        diff = np.abs(got64 - ref64)
        size = np.abs(ref64) if ref.ndim == 1 else np.linalg.norm(ref64, axis=1)
        gap = diff if ref.ndim == 1 else np.linalg.norm(got64 - ref64, axis=1)
        rel = gap / np.maximum(size, 1e-12)
        row_diff = diff if ref.ndim == 1 else diff.max(axis=1)
        rows.append(
            {
                "output": name,
                "median magnitude": float(np.median(size)),
                "max abs diff": float(diff.max()),
                "magnitude at max abs diff": float(size[row_diff.argmax()]),
                "max rel diff": float(rel.max()),
                "magnitude at max rel diff": float(size[rel.argmax()]),
                "median rel diff": float(np.median(rel)),
            }
        )
    return pd.DataFrame(rows)


def rank_agreement(reference: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    """How far the order of segments by a score moves: Spearman, how many segments change
    rank and by how much, and the overlap of the top 50 and the top 1%."""
    if len(reference) == 0:
        raise ValueError("rank_agreement needs at least one segment")
    ref_rank, got_rank = rankdata(reference), rankdata(candidate)
    out: dict[str, Any] = {
        "segments": len(reference),
        "1 - Spearman": 1 - float(np.corrcoef(ref_rank, got_rank)[0, 1]),  # Pearson on ranks
        "identical order": "yes" if np.array_equal(ref_rank, got_rank) else "no",
        "segments changing rank": int((ref_rank != got_rank).sum()),
        "largest rank change": float(np.abs(ref_rank - got_rank).max()),  # .5 for ties
    }
    for label, k in (("top 50", 50), ("top 1%", max(1, round(0.01 * len(reference))))):
        top_ref = set(np.argsort(-reference, kind="stable")[:k])
        top_got = set(np.argsort(-candidate, kind="stable")[:k])
        out[f"{label} overlap"] = f"{len(top_ref & top_got)}/{k}"
    return out


def session_flags(
    frame: pd.DataFrame, transformer: np.ndarray, isolation_forest_pct: np.ndarray, combined: str
) -> np.ndarray:
    """The flags run_scoring would raise from these transformer scores, for rows that make up
    whole sessions (percentiles are within the session)."""
    candidates = combinations(
        {
            "transformer": session_percentile(frame, transformer),
            "isolation_forest": isolation_forest_pct,
        }
    )
    return session_percentile(frame, candidates[combined]) > 1 - FLAG_SHARE


def segments_per_second(run: Callable[[], object], n: int, repeats: int) -> tuple[float, float]:
    """Throughput of the best and the slowest of `repeats` runs over `n` segments (after one
    warm-up run)."""
    run()
    times = []
    for _ in range(max(repeats, 1)):
        started = time.perf_counter()
        run()
        times.append(time.perf_counter() - started)
    return n / min(times), n / max(times)


@dataclass
class Parity:
    batch: int
    repeats: int
    rows: int
    sample: int
    session: str | None
    session_rows: int
    diffs: pd.DataFrame
    ranking: pd.DataFrame  # one column per set of rows
    flags: dict[str, Any]  # the whole session's flags, PyTorch against ONNX (and the stored run)
    stored: pd.DataFrame | None  # the stored M4 scores against PyTorch CPU, same checkpoint only
    stored_note: str  # how the stored scores were matched to this checkpoint, or why not
    timed: str  # which rows were timed: "random sample" or "whole session"
    timed_rows: int
    speed: pd.DataFrame
    timing_spread: float  # the slowest timed run's shortfall from the best, worst case
    profile: pd.DataFrame  # onnxruntime's kernel time by operator type


def op_profile(onnx_path: Path, data: TelemetryData, batch: int = 1024) -> pd.DataFrame:
    """Share of onnxruntime's kernel time by operator type, over a few runs of one batch."""
    with tempfile.TemporaryDirectory() as tmp:
        scorer = OnnxScorer(onnx_path, profile_dir=Path(tmp), checkpoint=None)
        part = data.subset(np.arange(min(batch, len(data))))
        for _ in range(3):
            scorer.score_and_embed(part, batch=batch)
        events = json.loads(Path(scorer.session.end_profiling()).read_text())
    kernels = [e for e in events if e.get("cat") == "Node" and e["name"].endswith("_kernel_time")]
    times = pd.Series([e["dur"] for e in kernels], index=[e["args"]["op_name"] for e in kernels])
    share = times.groupby(level=0).sum().sort_values(ascending=False)
    return pd.DataFrame(
        {"operator": share.index, "share of time": (share / share.sum()).map("{:.0%}".format)}
    )


def pick_session(frame: pd.DataFrame, session: str) -> tuple[int, int, str] | None:
    """`session` as (year, round, code): "latest" is the most recent race, "none" none, or
    "YEAR-ROUND-CODE" like 2026-15-R."""
    if session == "none":
        return None
    if session == "latest":
        races = frame.loc[frame["session"] == "R", ["year", "round"]].drop_duplicates()
        if races.empty:
            raise ValueError('no race among the segments: pass --session YEAR-ROUND-CODE or "none"')
        year, round_ = races.sort_values(["year", "round"]).iloc[-1]
        return int(year), int(round_), "R"
    parts = session.split("-")
    if len(parts) != 3 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError(f'session {session!r} is not "latest", "none" or YEAR-ROUND-CODE')
    return int(parts[0]), int(parts[1]), parts[2]


def synthetic_parity(
    model: MaskedAutoencoder, onnx_path: Path, rows: int = SYNTHETIC_ROWS, seed: int = 1
) -> pd.DataFrame:
    """differences() between score_and_embed on CPU and onnxruntime on random inputs. Batches
    of 200, so the last one is smaller and the dynamic batch dimension is exercised too."""
    data = random_data(rows, seed)
    ref_scores, ref_emb = score_and_embed(model.to(CPU).eval(), data, CPU, batch=200)
    got_scores, got_emb = OnnxScorer(onnx_path, checkpoint=None).score_and_embed(data, batch=200)
    return differences({**ref_scores, EMBEDDING: ref_emb}, {**got_scores, EMBEDDING: got_emb})


def stored_provenance(
    manifest: dict[str, Any], stored_run: str | None, checkpoint_sha256: str | None
) -> tuple[bool, str]:
    """Whether the stored M4 scores are the current scoring run's and come from the checkpoint
    being checked, and how that is known. `stored_run` is the run segment_scores.parquet
    records (provenance.parquet_run). The manifest's checkpoint path can't tell which weights
    they were: `race-engineer-infer select` always writes the same models/selected/mae.pt."""
    current = manifest.get("run_id")
    if current is None:
        return False, "there is no current scoring run (manifest.json is missing or has no run id)"
    if stored_run != current:
        return False, "the stored M4 scores are not the current scoring run's"
    if checkpoint_sha256 is None:
        return False, "the checkpoint this export came from is not known"
    if manifest.get("checkpoint_sha256") != checkpoint_sha256:
        return False, "the current scoring run's manifest records another checkpoint's SHA-256"
    return True, "the current scoring run's manifest records this checkpoint's SHA-256"


def check_parity(
    model: MaskedAutoencoder,
    onnx_path: Path,
    data: TelemetryData,
    segments: int = 4000,
    session: str = "latest",
    batch: int = 4096,
    repeats: int = 3,
    seed: int = 0,
    results_dir: Path = RESULTS_DIR,
    checkpoint_sha256: str | None = None,
) -> Parity:
    """Compare onnxruntime with score_and_embed on CPU over `segments` random segments plus
    one whole session, and time both (on the random sample, or the session when there is no
    sample). `checkpoint_sha256` is that of the checkpoint `model` came from: the stored M4
    scores in `results_dir` are compared too, but only if they are the current scoring run's
    and it used the same checkpoint (stored_provenance). Whichever run they are from, their
    Isolation Forest percentiles serve to recompute the session's flags with both engines."""
    model = model.to(CPU).eval()
    frame = data.frame
    # Drawn in segment_id order, so a seed checks the same segments whatever order the frame's
    # rows come in.
    by_id = np.argsort(frame["segment_id"].to_numpy().astype(str), kind="stable")
    n_draw = min(max(segments, 0), len(frame))
    sample = np.sort(by_id[np.random.default_rng(seed).choice(len(frame), n_draw, replace=False)])
    key = pick_session(frame, session)
    in_session = np.zeros(len(frame), bool)
    if key is not None:
        year, round_, code = key
        in_session = (
            (frame["year"] == year) & (frame["round"] == round_) & (frame["session"] == code)
        ).to_numpy()
        if not in_session.any():
            raise ValueError(f"session {session} has no eligible segments")
    rows = np.union1d(sample, np.flatnonzero(in_session))
    if len(rows) == 0:
        raise ValueError("nothing to check: no random segments and no session")
    part = data.subset(rows)
    log.info("scoring %d segments with PyTorch (CPU) and onnxruntime", len(rows))
    ref_scores, ref_emb = score_and_embed(model, part, CPU, batch=batch)
    got_scores, got_emb = OnnxScorer(onnx_path, checkpoint=None).score_and_embed(part, batch=batch)
    diffs = differences({**ref_scores, EMBEDDING: ref_emb}, {**got_scores, EMBEDDING: got_emb})

    is_sample, is_session = np.isin(rows, sample), in_session[rows]
    # A model without speed and offset has no mae_error_exit: rank by its plain error instead.
    name = TRANSFORMER_SCORE if TRANSFORMER_SCORE in ref_scores else "mae_error"
    ref_rank_by, got_rank_by = ref_scores[name], got_scores[name]
    ranking = {
        label: rank_agreement(ref_rank_by[subset], got_rank_by[subset])
        for label, subset in (("random sample", is_sample), ("whole session", is_session))
        if subset.any()
    }

    flags: dict[str, Any] = {}
    stored = None
    stored_note = f"there are no stored M4 scores in {results_dir}"
    stored_path = results_dir / "segment_scores.parquet"
    manifest_path = results_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if stored_path.exists():
        columns = ["segment_id", "transformer_raw", "isolation_forest_pct", "flagged"]
        saved = pd.read_parquet(stored_path, columns=columns).set_index("segment_id")
        saved = saved.reindex(part.frame["segment_id"].to_numpy())
        found = saved["transformer_raw"].notna().to_numpy()
        stored_score = manifest.get("transformer_score", TRANSFORMER_SCORE)
        same = False
        if not found.any():
            stored_note = "the stored M4 scores hold none of the checked segments"
        elif stored_score != name:
            stored_note = f"the stored M4 scores are {stored_score}, not {name}"
        else:
            agreement = differences(
                {name: ref_rank_by[found]},
                {name: saved["transformer_raw"].to_numpy(np.float32)[found]},
            )
            stored_run = parquet_run(stored_path)
            same, stored_note = stored_provenance(manifest, stored_run, checkpoint_sha256)
            stored = agreement if same else None
        if key is not None and found[is_session].all():
            # The flags as run_scoring raises them: the Isolation Forest percentiles are the
            # stored ones, the same for both engines, so only the Transformer score differs.
            combined = manifest.get("combined", "mean")
            session_frame = part.frame[is_session]
            iforest = saved["isolation_forest_pct"].to_numpy()[is_session]
            ref_flags = session_flags(session_frame, ref_rank_by[is_session], iforest, combined)
            got_flags = session_flags(session_frame, got_rank_by[is_session], iforest, combined)
            flags = {
                "segments": int(is_session.sum()),
                "flags (PyTorch CPU)": int(ref_flags.sum()),
                FLAGS_CHANGED: int((ref_flags != got_flags).sum()),
            }
            if same:  # the stored flags only mean something for the same checkpoint
                stored_flags = saved["flagged"].to_numpy(bool)[is_session]
                flags["flags that differ, stored run vs PyTorch CPU"] = int(
                    (ref_flags != stored_flags).sum()
                )

    timed_label = "random sample" if is_sample.any() else "whole session"
    timed = part.subset(np.flatnonzero(is_sample if is_sample.any() else is_session))
    log.info("timing on %d segments (%s)", len(timed), timed_label)
    speed_rows = []
    spread = 0.0
    torch_threads = torch.get_num_threads()
    for threads in (None, 1):
        torch.set_num_threads(threads or torch_threads)
        onnx_scorer = OnnxScorer(onnx_path, threads=threads, checkpoint=None)
        torch_run = functools.partial(score_and_embed, model, timed, CPU, batch=batch)
        onnx_run = functools.partial(onnx_scorer.score_and_embed, timed, batch=batch)
        torch_rate, torch_slowest = segments_per_second(torch_run, len(timed), repeats)
        onnx_rate, onnx_slowest = segments_per_second(onnx_run, len(timed), repeats)
        spread = max(spread, 1 - torch_slowest / torch_rate, 1 - onnx_slowest / onnx_rate)
        label = "1 thread" if threads == 1 else f"all cores (torch uses {torch_threads} threads)"
        speed_rows.append(
            {
                "threads": label,
                "PyTorch CPU (segments/s)": round(torch_rate),
                "onnxruntime CPU (segments/s)": round(onnx_rate),
                "ONNX / PyTorch": round(onnx_rate / torch_rate, 2),
            }
        )
    torch.set_num_threads(torch_threads)
    return Parity(
        batch=batch,
        repeats=repeats,
        rows=len(rows),
        sample=int(is_sample.sum()),
        session=None if key is None else "-".join(map(str, key)),
        session_rows=int(is_session.sum()),
        diffs=diffs,
        ranking=pd.DataFrame(ranking),
        flags=flags,
        stored=stored,
        stored_note=stored_note,
        timed=timed_label,
        timed_rows=len(timed),
        speed=pd.DataFrame(speed_rows),
        timing_spread=spread,
        profile=op_profile(onnx_path, timed),
    )


def gate_failures(synthetic: pd.DataFrame, parity: Parity | None) -> list[str]:
    """The parity gate: every check the export fails, none if it may replace the served file.

    - every output but the UNGATED ones within PARITY_RTOL of PyTorch (max relative difference
      per segment), on random inputs and on real segments;
    - the same top 1% of segments by the Transformer score in each set of real segments;
    - no flag of the whole session changes.
    """
    failures = []
    tables = {"random inputs": synthetic}
    if parity is not None:
        tables["real segments"] = parity.diffs
    for where, table in tables.items():
        for output, rel in zip(table["output"], table["max rel diff"], strict=True):
            if output not in UNGATED and not rel < PARITY_RTOL:  # also catches NaN
                failures.append(
                    f"{output} on {where}: max relative difference {rel:.1e} "
                    f"(the gate is {PARITY_RTOL:.0e})"
                )
    if parity is not None:
        for rows, overlap in parity.ranking.loc["top 1% overlap"].items():
            hit, k = map(int, str(overlap).split("/"))
            if hit < k:
                failures.append(f"top 1% of the {rows}: only {overlap} segments in common")
        changed = parity.flags.get(FLAGS_CHANGED, 0)
        if changed:
            failures.append(f"{changed} flags of {parity.session} change with the ONNX scores")
    return failures


# --- report ------------------------------------------------------------------------------------


def _sci(value: float) -> str:
    return f"{value:.1e}" if value else "0"


def _mb(path: Path) -> str:
    return f"{path.stat().st_size / 1e6:.2f} MB"


def _diff_table(diffs: pd.DataFrame) -> str:
    table = diffs.copy()
    for column in table.columns[1:]:
        table[column] = table[column].map(_sci)
    return markdown_table(table)


def _by_output(diffs: pd.DataFrame) -> dict[str, dict[Any, Any]]:
    return {str(row["output"]): row for row in diffs.to_dict("records")}


def _diff_prose(diffs: pd.DataFrame) -> str:
    """What the difference table says, from its numbers."""
    rows = _by_output(diffs)
    gated = {name: row for name, row in rows.items() if name not in UNGATED}
    worst = max(gated, key=lambda name: gated[name]["max rel diff"])
    worst_rel = float(gated[worst]["max rel diff"])
    text = f"The largest relative difference on a gated output is {_sci(worst_rel)} (`{worst}`), "
    text += (
        f"under the gate's {PARITY_RTOL:.0e}: float32 rounding, from operations that run in a "
        "different order or are fused."
        if worst_rel < PARITY_RTOL
        else f"over the gate's {PARITY_RTOL:.0e}: more than float32 rounding."
    )
    if "mae_nll" in rows:
        nll = rows["mae_nll"]
        near_zero = nll["magnitude at max rel diff"] < 0.1 * nll["median magnitude"]
        text += (
            f" `mae_nll` (not gated) has its largest relative difference, "
            f"{_sci(nll['max rel diff'])}, where its magnitude is "
            f"{_sci(nll['magnitude at max rel diff'])}"
            + (
                ", near zero: it sums positive and negative terms (the log-variance is negative "
                "wherever the model is confident), so a rounding-sized difference is large "
                "relative to it"
                if near_zero
                else ""
            )
            + f". Its largest absolute difference, {_sci(nll['max abs diff'])}, is at an NLL of "
            f"magnitude {_sci(nll['magnitude at max abs diff'])}."
        )
    return text


def write_report(
    path: Path,
    checkpoint: Path,
    onnx_path: Path,
    export_seconds: float,
    synthetic: pd.DataFrame,
    parity: Parity | None,
    session_label: str | None,
) -> Path:
    scorer = OnnxScorer(onnx_path, checkpoint=checkpoint)
    cfg = scorer.model_cfg
    failures = gate_failures(synthetic, parity)
    passed = not failures
    io = pd.DataFrame(
        [("input", i.name, str(i.shape), i.type) for i in scorer.session.get_inputs()]
        + [("output", o.name, str(o.shape), o.type) for o in scorer.session.get_outputs()],
        columns=["", "name", "shape", "type"],
    )
    candidate = f"{onnx_path.stem}.candidate{onnx_path.suffix}"
    rejected = f"{onnx_path.stem}.rejected{onnx_path.suffix}"
    parts = [
        "# M4: the Telemetry Transformer as an ONNX model",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `python -m "
        f"race_engineer.inference.onnx_export` from `{checkpoint.name}`._",
        "The selected model is exported so that scoring can be served with onnxruntime alone, "
        "without PyTorch. The graph is not the bare autoencoder but the whole scoring step of "
        "`score_and_embed`: a batch of model inputs goes in, the four mistake scores and the "
        "[CLS] style embedding come out, and the five complementary patch masks (each 40 m "
        "patch predicted once without seeing itself) plus the pass with nothing hidden are "
        "unrolled inside the graph. Serving code therefore can't drift from the PyTorch "
        "scoring: `OnnxScorer` (`race_engineer/inference/onnx_scorer.py`) only loops over "
        "batches, and importing it imports numpy and onnxruntime but not torch, pandas or the "
        "data store.",
        "## How to export",
        "```\ncd engine\n"
        "uv run python -m race_engineer.inference.onnx_export   # export, parity check, report\n"
        "uv run python -m race_engineer.inference.onnx_export --segments 0 --session none"
        "   # check on random inputs only\n```",
        "The export is written next to the served file as "
        f"`{candidate}` and checked against PyTorch (the parity gate below). Only if it passes "
        f"is it moved over `{onnx_path.name}` (atomically) and this report rewritten; "
        f"otherwise the served file and this report are left alone, the export is kept as "
        f"`{rejected}` and the command exits with status 1. Rerun it after "
        "`race-engineer-infer select`, which removes the served file (and `OnnxScorer` refuses "
        "one whose recorded checkpoint SHA-256 differs from `models/selected/mae.pt`, "
        "`StaleScorerError`).",
        "The exporter is PyTorch's torch.export-based `torch.onnx.export(dynamo=True)` (torch "
        f"{torch.__version__}), the default since PyTorch 2.9; the TorchScript exporter still "
        "works but is deprecated. The graph targets ONNX opset "
        f"{scorer.metadata.get('opset', '?')}, whose Attention operator keeps each attention "
        "call as one node: measured while building this export, about 40% faster in "
        "onnxruntime than the default opset 20, which spells attention out in transposes and "
        "matrix products. Exporting needs `onnx` and "
        "`onnxscript`, which are dev dependencies because only exporting uses them; serving "
        f"needs `onnxruntime` (a main dependency, {importlib.metadata.version('onnxruntime')} "
        f"here). The export took {export_seconds:.0f} s. The file records the model config, the "
        "score names, the source checkpoint and its SHA-256 as metadata "
        "(`OnnxScorer.metadata`). The exporter attaches a Python stack trace, with local file "
        "paths, to every node; those are stripped, which halves the file.",
        "### Inputs and outputs",
        "The inputs are the arrays of `TelemetryData` (`models/tensors.py`) as they are: all "
        f"five signal channels in the order {', '.join(SIGNAL)} (normalised per corner and "
        "session by `build_tensors`), float32; `context_cat` is int64. The batch dimension is "
        "dynamic.",
        markdown_table(io),
        "## Parity with PyTorch",
        "Both engines score the same segments on CPU: `score_and_embed` in PyTorch and the "
        "exported graph in onnxruntime. Relative differences are per segment: |a - b| / |b| "
        "for a score and ||a - b|| / ||b|| for the 64-dimensional embedding. The export passes "
        "the gate only if every output but `mae_nll` stays under a relative difference of "
        f"{PARITY_RTOL:.0e} on random inputs and on real segments, the top 1% of segments by "
        f"`{TRANSFORMER_SCORE}` is the same set in both engines, and no flag of the checked "
        "session changes. `mae_nll` is not gated: it sums terms of both signs, so where it is "
        "near zero rounding alone makes its relative difference large, and the flags don't "
        "use it.",
        (
            "**This export passed the gate.**"
            if passed
            else "**This export failed the gate:** " + "; ".join(failures) + "."
        ),
        "### Random inputs",
        f"{SYNTHETIC_ROWS} segments of standard normal inputs, in batches of 200 (so the last "
        "batch is smaller). Checked on every export, with or without real data.",
        _diff_table(synthetic),
    ]
    if parity is not None:
        checked = []
        if parity.sample:
            checked.append(f"{parity.sample:,} drawn at random from all sessions")
        if parity.session:
            checked.append(f"every segment of {session_label} ({parity.session_rows:,})")
        ranking = parity.ranking.copy()
        ranking.loc["1 - Spearman"] = ranking.loc["1 - Spearman"].map(_sci)
        shifts = parity.ranking.loc["largest rank change"].to_numpy(float)
        if all(value == "yes" for value in parity.ranking.loc["identical order"]):
            order_text = ": the order is identical."
        elif shifts.max() <= 1:
            order_text = (
                ". Segments that change rank only swap places with their immediate neighbour, "
                "whose score was within float32 rounding of theirs:"
            )
        else:
            order_text = ":"
        parts += [
            "### Real segments",
            f"{parity.rows:,} real segments: {', plus '.join(checked)}.",
            _diff_table(parity.diffs),
            _diff_prose(parity.diffs),
            f"**Ranking by `{TRANSFORMER_SCORE}`** (the Transformer score the M4 flags use)"
            + order_text,
            markdown_table(ranking.reset_index(names="")),
        ]
        stored_run = (
            "The stored M4 scores (`data/results/segment_scores.parquet` from "
            "`race-engineer-infer score`, which runs on the GPU when there is one; its manifest "
            "doesn't record the device)"
        )
        if parity.flags:
            f = parity.flags
            text = (
                f"**Flags.** Recomputing the flags of {session_label} the way "
                "`race-engineer-infer score` does (session percentiles, combined with the "
                f"stored Isolation Forest percentiles, top {FLAG_SHARE:.0%}): "
                f"{f['flags (PyTorch CPU)']} flags from PyTorch on CPU, of which "
                f"{f[FLAGS_CHANGED]} change with the ONNX scores."
            )
            stored_changed = f.get("flags that differ, stored run vs PyTorch CPU")
            if stored_changed is not None:
                text += (
                    f" The stored M4 run of the same checkpoint differs from PyTorch on CPU on "
                    f"{stored_changed} of them."
                )
            parts.append(text)
        elif parity.session:
            parts.append(
                f"**Flags.** Not checked: recomputing the flags of {session_label} needs the "
                "stored Isolation Forest percentiles of all its segments, and the stored M4 "
                "results don't have them."
            )
        if parity.stored is not None:
            s = parity.stored.iloc[0]
            onnx_rel = float(_by_output(parity.diffs)[s["output"]]["max rel diff"])
            stored_rel = float(s["max rel diff"])
            text = (
                f"**Device noise, for scale.** {stored_run} come from the same checkpoint: "
                f"{parity.stored_note}. Against PyTorch on CPU, same segments: max abs diff "
                f"{_sci(s['max abs diff'])}, max rel diff {_sci(stored_rel)}, median rel diff "
                f"{_sci(s['median rel diff'])}."
            )
            if stored_rel == 0:
                text += (
                    " They are bit-identical to PyTorch on CPU (probably scored on the CPU), so "
                    "they give no scale for the ONNX differences."
                )
            else:
                ratio = onnx_rel / stored_rel
                if ratio <= 0.5:
                    how_much = "less than"
                elif ratio <= 2:
                    how_much = "about as much as"
                else:
                    how_much = f"{ratio:.0f} times as much as"
                text += (
                    f" Moving to onnxruntime changes `{s['output']}` {how_much} scoring it on "
                    f"the stored run's device instead of the CPU does (max rel diff "
                    f"{_sci(onnx_rel)} against {_sci(stored_rel)})."
                )
            parts.append(text)
        else:
            parts.append(
                f"**Stored M4 scores.** Not compared with this export: {parity.stored_note}."
            )
    parts += [
        "## Size and speed",
        markdown_table(
            pd.DataFrame(
                [
                    {
                        "file": checkpoint.name,
                        "what": "PyTorch checkpoint (weights, config, training history)",
                        "size": _mb(checkpoint),
                    },
                    {
                        "file": onnx_path.name,
                        "what": "ONNX scorer (weights and the unrolled six-pass graph)",
                        "size": _mb(onnx_path),
                    },
                ]
            )
        ),
        f"A {cfg.get('d_model', '?')}-wide, {cfg.get('n_layers', '?')}-layer Transformer: both "
        "files hold its float32 weights once (the six passes share them).",
    ]
    if parity is not None:
        rates = parity.speed.iloc[0]
        profile = parity.profile.head(5)
        race = parity.session_rows or 14_000  # a typical race
        ratio = float(rates["ONNX / PyTorch"])
        spread = (
            f" Each engine's slowest timed run was within {parity.timing_spread:.0%} of its "
            "best (a laptop running other jobs)."
            if parity.repeats > 1
            else ""
        )
        if ratio < 1:
            speed_text = (
                f"On this machine onnxruntime runs at {ratio:.2f} times PyTorch's speed with all "
                "cores. PyTorch runs `nn.TransformerEncoderLayer` through its fused inference "
                "kernels and its matrix products through the platform's BLAS (Apple Accelerate "
                "on a Mac); onnxruntime uses its own kernels on the exported graph."
            )
        else:
            speed_text = (
                f"On this machine onnxruntime runs at {ratio:.2f} times PyTorch's speed with all "
                "cores."
            )
        parts += [
            f"Throughput on the {parity.timed_rows:,} segments of the {parity.timed}, batches "
            f"of {parity.batch:,}, best of {parity.repeats} runs after a warm-up, on "
            f"{platform.machine()} ({os.cpu_count()} cores, {platform.platform(terse=True)}). "
            f"A segment costs six model passes in both.{spread}",
            markdown_table(parity.speed),
            f"{speed_text} Where onnxruntime's time goes (share of kernel time, one profiled "
            "batch):",
            markdown_table(profile),
            f"At this speed the ONNX scorer handles a whole race ({race:,} segments) in "
            f"{race / rates['onnxruntime CPU (segments/s)']:.0f} s on one machine, fine for "
            "scoring a new session on demand; the full 1.86 million segments stay a batch job "
            "(`race-engineer-infer score`, on the GPU).",
        ]
    parts += [
        "## Notes",
        "- The output is named `cls_embedding`, not `embedding`: the exporter already names "
        "the `nn.Embedding` outputs that way, and a clashing output name made the file "
        "invalid.",
        "- onnxruntime 1.30's macOS wheel starts a telemetry uploader when it is imported. Its "
        "worker thread can race the process's exit and abort it (SIGABRT; about one export run "
        "in ten while building this, and intermittently in the test suite). "
        "`race_engineer.inference.onnx_scorer` sets `ORT_DISABLE_TELEMETRY=1` (unless it is "
        "already set) before importing onnxruntime, which fixes both; it only works if "
        "nothing imported onnxruntime earlier in the process.",
        "- `OnnxScorer` uses the CPU execution provider. Scoring still needs a session's other "
        "laps: the inputs are z-scores against the field at that corner (`build_tensors`), and "
        "flags are percentiles within the session.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(parts) + "\n")
    return path


class ParityError(RuntimeError):
    """The export failed the parity gate, so it did not replace the served file."""


def run(
    checkpoint: Path = SELECTED_CHECKPOINT,
    out: Path = SCORER_PATH,
    report: Path | None = REPORT_DIR / "m4_onnx.md",
    segments: int = 4000,
    session: str = "latest",
    batch: int = 4096,
    repeats: int = 3,
    seed: int = 0,
    data: TelemetryData | None = None,
    results_dir: Path = RESULTS_DIR,
) -> tuple[Path, Parity | None]:
    """Export `checkpoint` next to `out`, check it against PyTorch (on random inputs, and on
    real segments unless `segments` is 0 and `session` "none") and, if it passes the gate, move
    it to `out` and write the report. Otherwise raise ParityError: `out` and the report stay as
    they were, and the failed export is kept at <out>.rejected.onnx. `data` defaults to every
    eligible segment, which must be the eval frame the current scoring run in `results_dir`
    scored, if there is one (else StaleResultsError, before anything is exported)."""
    model, _ = load_checkpoint(checkpoint, CPU)
    checkpoint_sha256 = file_sha256(checkpoint)
    real = None  # the real segments to check, if any
    if segments > 0 or session != "none":
        real = data
        if real is None:
            frame = load_eval_frame()
            # Before the tensors: building them for a frame that is then refused takes minutes.
            if scoring_run(results_dir) is not None:
                check_scored_frame(frame, results_dir)
            real = load_or_build_tensors(frame)
    candidate = out.with_name(f"{out.stem}.candidate{out.suffix}")
    rejected = out.with_name(f"{out.stem}.rejected{out.suffix}")
    try:
        started = time.time()
        export_scorer(
            model,
            candidate,
            metadata={"checkpoint": checkpoint.name, "checkpoint_sha256": checkpoint_sha256},
        )
        export_seconds = time.time() - started
        size = candidate.stat().st_size / 1e6
        log.info("exported %s (%.2f MB) in %.0f s", candidate, size, export_seconds)
        synthetic = synthetic_parity(model, candidate)
        parity = None
        session_label = None
        if real is not None:
            parity = check_parity(
                model,
                candidate,
                real,
                segments,
                session,
                batch,
                repeats,
                seed,
                results_dir,
                checkpoint_sha256,
            )
            if parity.session:
                session_label = describe_session(real.frame, parity.session)
            log.info("differences on real segments:\n%s", parity.diffs.to_string(index=False))
        failures = gate_failures(synthetic, parity)
        if failures:
            os.replace(candidate, rejected)
            raise ParityError(
                f"the export failed the parity gate, so {out} was left as it was (the failed "
                f"export is {rejected}):\n  " + "\n  ".join(failures)
            )
        os.replace(candidate, out)  # atomic: a reader sees the old file or the new one
    finally:
        candidate.unlink(missing_ok=True)  # only left there by an error
    rejected.unlink(missing_ok=True)  # an earlier failure, superseded
    log.info("passed the parity gate: %s", out)
    if report is not None:
        write_report(report, checkpoint, out, export_seconds, synthetic, parity, session_label)
        log.info("wrote %s", report)
    return out, parity


def describe_session(frame: pd.DataFrame, session: str) -> str:
    """ "2026-17-R" as "the 2026 Azerbaijan Grand Prix (R)", when the frame names the event."""
    year, round_, code = session.split("-")
    events = frame.loc[(frame["year"] == int(year)) & (frame["round"] == int(round_))]
    if "event" not in frame.columns or events.empty:
        return session
    return f"the {year} {events['event'].iloc[0]} ({code})"


def main(argv: Sequence[str] | None = None, prog: str | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog=prog or "python -m race_engineer.inference.onnx_export",
        description="Export the selected model to ONNX, check it against PyTorch and, if it "
        "passes, put it in place.",
    )
    parser.add_argument("--checkpoint", type=Path, default=SELECTED_CHECKPOINT)
    parser.add_argument("--out", type=Path, default=SCORER_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_DIR / "m4_onnx.md")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument(
        "--segments",
        type=int,
        default=4000,
        help="random real segments to check (0: none; the --session is still checked)",
    )
    parser.add_argument(
        "--session",
        default="latest",
        help='whole session to check: "latest" race, "none" or YEAR-ROUND-CODE such as 2026-15-R',
    )
    parser.add_argument("--batch", type=int, default=4096)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", force=True
    )
    if not args.checkpoint.exists():
        parser.error(f"{args.checkpoint} not found: run `race-engineer-infer select <checkpoint>`")
    try:
        run(
            checkpoint=args.checkpoint,
            out=args.out,
            report=None if args.no_report else args.report,
            segments=args.segments,
            session=args.session,
            batch=args.batch,
            repeats=args.repeats,
            seed=args.seed,
        )
    except (ParityError, StaleResultsError) as err:
        raise SystemExit(f"error: {err}") from None


if __name__ == "__main__":
    main()
