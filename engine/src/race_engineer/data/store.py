"""Parquet storage for the processed dataset, one file per session per table, plus DuckDB views."""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from race_engineer.config import PROCESSED_DIR

TABLES = ("sessions", "tracks", "corners", "laps", "lap_grids", "segments", "events")


def session_key(year: int, round_: int, code: str) -> str:
    return f"{year}_{round_:02d}_{code}"


def table_path(table: str, key: str, root: Path = PROCESSED_DIR) -> Path:
    return root / table / f"{key}.parquet"


def list_column(arrays: list[np.ndarray]) -> pa.Array:
    """Variable-length float32 lists (one array per row)."""
    lengths = np.array([len(a) for a in arrays], dtype=np.int32)
    offsets = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int32)
    values = np.concatenate(arrays).astype(np.float32) if arrays else np.zeros(0, np.float32)
    return pa.ListArray.from_arrays(pa.array(offsets), pa.array(values))


def fixed_list_column(arrays: list[np.ndarray], size: int) -> pa.Array:
    """Fixed-size float32 lists, e.g. the 80-point corner segments."""
    values = np.concatenate(arrays).astype(np.float32) if arrays else np.zeros(0, np.float32)
    return pa.FixedSizeListArray.from_arrays(pa.array(values), size)


def fixed_list_to_numpy(column: pa.ChunkedArray | pa.Array) -> np.ndarray:
    """A fixed-size list column (e.g. 80-point segments) as an (N, size) float32 array."""
    array = column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column
    size = array.type.list_size
    return array.flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(-1, size)


def to_arrow(frame: pd.DataFrame, arrays: dict[str, pa.Array] | None = None) -> pa.Table:
    table = pa.Table.from_pandas(frame.reset_index(drop=True), preserve_index=False)
    for name, column in (arrays or {}).items():
        table = table.append_column(name, column)
    return table


def write_table(table: pa.Table, name: str, key: str, root: Path = PROCESSED_DIR) -> Path:
    path = table_path(name, key, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd", compression_level=6)
    tmp.replace(path)  # atomic: a crash never leaves a half-written file behind
    return path


def connect(root: Path = PROCESSED_DIR) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with one view per table over all session files."""
    con = duckdb.connect()
    for name in TABLES:
        if any((root / name).glob("*.parquet")):
            glob = (root / name / "*.parquet").as_posix()
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{glob}', union_by_name = true)"
            )
    return con
