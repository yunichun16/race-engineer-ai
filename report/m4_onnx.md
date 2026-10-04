# M4: the Telemetry Transformer as an ONNX model

_Generated 2026-10-01 00:32 UTC by `python -m race_engineer.inference.onnx_export` from `mae.pt`._

The selected model is exported so that scoring can be served with onnxruntime alone, without PyTorch. The graph is not the bare autoencoder but the whole scoring step of `score_and_embed`: a batch of model inputs goes in, the four mistake scores and the [CLS] style embedding come out, and the five complementary patch masks (each 40 m patch predicted once without seeing itself) plus the pass with nothing hidden are unrolled inside the graph. Serving code therefore can't drift from the PyTorch scoring: `OnnxScorer` (`race_engineer/inference/onnx_scorer.py`) only loops over batches, and importing it imports numpy and onnxruntime but not torch, pandas or the data store.

## How to export

```
cd engine
uv run python -m race_engineer.inference.onnx_export   # export, parity check, report
uv run python -m race_engineer.inference.onnx_export --segments 0 --session none   # check on random inputs only
```

The export is written next to the served file as `mae_scorer.candidate.onnx` and checked against PyTorch (the parity gate below). Only if it passes is it moved over `mae_scorer.onnx` (atomically) and this report rewritten; otherwise the served file and this report are left alone, the export is kept as `mae_scorer.rejected.onnx` and the command exits with status 1. Rerun it after `race-engineer-infer select`, which removes the served file (and `OnnxScorer` refuses one whose recorded checkpoint SHA-256 differs from `models/selected/mae.pt`, `StaleScorerError`).

The exporter is PyTorch's torch.export-based `torch.onnx.export(dynamo=True)` (torch 2.14.0), the default since PyTorch 2.9; the TorchScript exporter still works but is deprecated. The graph targets ONNX opset 23, whose Attention operator keeps each attention call as one node: measured while building this export, about 40% faster in onnxruntime than the default opset 20, which spells attention out in transposes and matrix products. Exporting needs `onnx` and `onnxscript`, which are dev dependencies because only exporting uses them; serving needs `onnxruntime` (a main dependency, 1.30.0 here). The export took 14 s. The file records the model config, the score names, the source checkpoint and its SHA-256 as metadata (`OnnxScorer.metadata`). The exporter attaches a Python stack trace, with local file paths, to every node; those are stripped, which halves the file.

### Inputs and outputs

The inputs are the arrays of `TelemetryData` (`models/tensors.py`) as they are: all five signal channels in the order speed, throttle, brake, gear, offset_m (normalised per corner and session by `build_tensors`), float32; `context_cat` is int64. The batch dimension is dynamic.

|  | name | shape | type |
|---|---|---|---|
| input | signal | ['batch', 5, 80] | tensor(float) |
| input | context_channels | ['batch', 2, 80] | tensor(float) |
| input | context_cat | ['batch', 2] | tensor(int64) |
| input | context_num | ['batch', 4] | tensor(float) |
| output | mae_nll | ['batch'] | tensor(float) |
| output | mae_error | ['batch'] | tensor(float) |
| output | mae_error_exit | ['batch'] | tensor(float) |
| output | mae_error_speed_patch | ['batch'] | tensor(float) |
| output | cls_embedding | ['batch', 64] | tensor(float) |

## Parity with PyTorch

Both engines score the same segments on CPU: `score_and_embed` in PyTorch and the exported graph in onnxruntime. Relative differences are per segment: |a - b| / |b| for a score and ||a - b|| / ||b|| for the 64-dimensional embedding. The export passes the gate only if every output but `mae_nll` stays under a relative difference of 1e-04 on random inputs and on real segments, the top 1% of segments by `mae_error_exit` is the same set in both engines, and no flag of the checked session changes. `mae_nll` is not gated: it sums terms of both signs, so where it is near zero rounding alone makes its relative difference large, and the flags don't use it.

**This export passed the gate.**

### Random inputs

512 segments of standard normal inputs, in batches of 200 (so the last batch is smaller). Checked on every export, with or without real data.

| output | median magnitude | max abs diff | magnitude at max abs diff | max rel diff | magnitude at max rel diff | median rel diff |
|---|---|---|---|---|---|---|
| mae_nll | 2.7e+00 | 1.3e-05 | 1.1e+01 | 1.3e-06 | 6.3e+00 | 1.9e-07 |
| mae_error | 1.2e+00 | 4.8e-07 | 1.4e+00 | 3.4e-07 | 1.4e+00 | 0 |
| mae_error_exit | 1.3e+00 | 8.3e-07 | 1.5e+00 | 5.5e-07 | 1.5e+00 | 7.8e-08 |
| mae_error_speed_patch | 3.4e+00 | 5.7e-06 | 1.1e+01 | 1.5e-06 | 2.6e+00 | 1.9e-07 |
| cls_embedding | 1.9e+00 | 5.4e-07 | 1.9e+00 | 5.9e-07 | 1.9e+00 | 3.4e-07 |

### Real segments

17,913 real segments: 4,000 drawn at random from all sessions, plus every segment of the 2026 Azerbaijan Grand Prix (R) (13,945).

| output | median magnitude | max abs diff | magnitude at max abs diff | max rel diff | magnitude at max rel diff | median rel diff |
|---|---|---|---|---|---|---|
| mae_nll | 1.3e+00 | 1.2e-04 | 1.4e+02 | 1.8e-03 | 1.2e-03 | 1.0e-07 |
| mae_error | 1.6e-01 | 1.9e-06 | 1.3e+01 | 1.8e-06 | 5.8e-03 | 8.4e-08 |
| mae_error_exit | 8.7e-02 | 3.1e-05 | 1.6e+02 | 4.1e-06 | 4.2e-03 | 2.0e-07 |
| mae_error_speed_patch | 4.6e-01 | 7.6e-06 | 5.9e+01 | 1.1e-05 | 2.7e-02 | 3.0e-07 |
| cls_embedding | 1.9e+00 | 8.3e-07 | 2.0e+00 | 6.7e-07 | 2.0e+00 | 3.6e-07 |

The largest relative difference on a gated output is 1.1e-05 (`mae_error_speed_patch`), under the gate's 1e-04: float32 rounding, from operations that run in a different order or are fused. `mae_nll` (not gated) has its largest relative difference, 1.8e-03, where its magnitude is 1.2e-03, near zero: it sums positive and negative terms (the log-variance is negative wherever the model is confident), so a rounding-sized difference is large relative to it. Its largest absolute difference, 1.2e-04, is at an NLL of magnitude 1.4e+02.

**Ranking by `mae_error_exit`** (the Transformer score the M4 flags use). Segments that change rank only swap places with their immediate neighbour, whose score was within float32 rounding of theirs:

|  | random sample | whole session |
|---|---|---|
| segments | 4,000 | 13,945 |
| 1 - Spearman | 4.7e-11 | 3.9e-11 |
| identical order | no | no |
| segments changing rank | 2 | 28 |
| largest rank change | 0.5 | 1 |
| top 50 overlap | 50/50 | 50/50 |
| top 1% overlap | 40/40 | 139/139 |

**Flags.** Recomputing the flags of the 2026 Azerbaijan Grand Prix (R) the way `race-engineer-infer score` does (session percentiles, combined with the stored Isolation Forest percentiles, top 1%): 140 flags from PyTorch on CPU, of which 0 change with the ONNX scores. The stored M4 run of the same checkpoint differs from PyTorch on CPU on 0 of them.

**Device noise, for scale.** The stored M4 scores (`data/results/segment_scores.parquet` from `race-engineer-infer score`, which runs on the GPU when there is one; its manifest doesn't record the device) come from the same checkpoint: the current scoring run's manifest records this checkpoint's SHA-256. Against PyTorch on CPU, same segments: max abs diff 3.1e-05, max rel diff 4.1e-06, median rel diff 1.9e-07. Moving to onnxruntime changes `mae_error_exit` about as much as scoring it on the stored run's device instead of the CPU does (max rel diff 4.1e-06 against 4.1e-06).

## Size and speed

| file | what | size |
|---|---|---|
| mae.pt | PyTorch checkpoint (weights, config, training history) | 2.33 MB |
| mae_scorer.onnx | ONNX scorer (weights and the unrolled six-pass graph) | 2.61 MB |

A 128-wide, 4-layer Transformer: both files hold its float32 weights once (the six passes share them).

Throughput on the 4,000 segments of the random sample, batches of 4,096, best of 3 runs after a warm-up, on arm64 (8 cores, macOS-27.0). A segment costs six model passes in both. Each engine's slowest timed run was within 9% of its best (a laptop running other jobs).

| threads | PyTorch CPU (segments/s) | onnxruntime CPU (segments/s) | ONNX / PyTorch |
|---|---|---|---|
| all cores (torch uses 6 threads) | 2,693 | 2,030 | 0.75 |
| 1 thread | 1,808 | 578 | 0.32 |

On this machine onnxruntime runs at 0.75 times PyTorch's speed with all cores. PyTorch runs `nn.TransformerEncoderLayer` through its fused inference kernels and its matrix products through the platform's BLAS (Apple Accelerate on a Mac); onnxruntime uses its own kernels on the exported graph. Where onnxruntime's time goes (share of kernel time, one profiled batch):

| operator | share of time |
|---|---|
| MatMul | 38% |
| LayerNormalization | 18% |
| Transpose | 17% |
| Gemm | 6% |
| BiasGelu | 5% |

At this speed the ONNX scorer handles a whole race (13,945 segments) in 7 s on one machine, fine for scoring a new session on demand; the full 1.86 million segments stay a batch job (`race-engineer-infer score`, on the GPU).

## Notes

- The output is named `cls_embedding`, not `embedding`: the exporter already names the `nn.Embedding` outputs that way, and a clashing output name made the file invalid.

- onnxruntime 1.30's macOS wheel starts a telemetry uploader when it is imported. Its worker thread can race the process's exit and abort it (SIGABRT; about one export run in ten while building this, and intermittently in the test suite). `race_engineer.inference.onnx_scorer` sets `ORT_DISABLE_TELEMETRY=1` (unless it is already set) before importing onnxruntime, which fixes both; it only works if nothing imported onnxruntime earlier in the process.

- `OnnxScorer` uses the CPU execution provider. Scoring still needs a session's other laps: the inputs are z-scores against the field at that corner (`build_tensors`), and flags are percentiles within the session.
