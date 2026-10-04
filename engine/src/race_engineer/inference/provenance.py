"""Which scoring run an M4 output belongs to.

The M4 steps run in order (score, then explain and style), each reading the previous step's
files from data/results/. A partial rerun (a new checkpoint scored but not explained yet, or
`explain` failing after `score`) would otherwise leave files from two runs side by side, and
the tools would quietly combine them: flags from one run, explanations from the other.

`race-engineer-infer score` gives each run an id and writes it to manifest.json; `select`
removes the manifest, since the results then belong to a checkpoint that is no longer the
selected one. Every later output records the id it was built from (in the parquet file's
key-value metadata), and readers check it against the manifest with `check_current`.

This module imports nothing heavy: the tools and the serving path use it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from race_engineer.config import RESULTS_DIR

MANIFEST = "manifest.json"
RUN_KEY = b"race_engineer.scoring_run"


class StaleResultsError(RuntimeError):
    """An M4 output is missing, or was built from another scoring run than the current one."""


def new_run_id(created: str, checkpoint_sha256: str) -> str:
    """A scoring run's id: when it ran and which checkpoint it scored with."""
    return f"{created}/{checkpoint_sha256[:12]}"


def scoring_run(results: Path = RESULTS_DIR) -> str | None:
    """The current scoring run's id from manifest.json (None: no complete scoring run)."""
    path = results / MANIFEST
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("run_id")


def write_parquet(frame: pd.DataFrame, path: Path, run_id: str | None) -> None:
    """frame.to_parquet(path), recording the scoring run it was built from."""
    table = pa.Table.from_pandas(frame, preserve_index=False)
    if run_id is not None:
        table = table.replace_schema_metadata(
            {**(table.schema.metadata or {}), RUN_KEY: run_id.encode()}
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def parquet_run(path: Path) -> str | None:
    """The scoring run a parquet file was built from (None: not recorded)."""
    value = (pq.read_schema(path).metadata or {}).get(RUN_KEY)
    return value.decode() if value is not None else None


def check_current(path: Path, step: str, results: Path = RESULTS_DIR) -> None:
    """Raise StaleResultsError unless `path` exists and was built from the current scoring
    run. `step` names the command that rebuilds it (e.g. "explain")."""
    rebuild = f"run `race-engineer-infer {step}` (or `make m4`)"
    current = scoring_run(results)
    if current is None:
        why = (
            "manifest.json has no run id: scored before runs were recorded"
            if (results / MANIFEST).exists()
            else "manifest.json missing: never scored, or a new model was selected since"
        )
        raise StaleResultsError(
            f"No current scoring run in {results} ({why}): run `race-engineer-infer score`, "
            "then the later steps (`make m4`)."
        )
    if not path.exists():
        raise StaleResultsError(f"{path.name} not found in {results}: {rebuild}.")
    built_from = parquet_run(path)
    if built_from != current:
        raise StaleResultsError(
            f"{path.name} was built from another scoring run ({built_from or 'unrecorded'}) "
            f"than the current one ({current}): {rebuild}."
        )
