"""The evaluation dataset: per-segment features, which segments count, and their weak labels.

Features are computed once per processed session and cached next to the processed data, so
new sessions from an ongoing build are picked up incrementally.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from race_engineer.config import DATA_DIR, PROCESSED_DIR
from race_engineer.data.store import connect, fixed_list_to_numpy, write_table
from race_engineer.features.handcrafted import corner_features
from race_engineer.features.segments import SEGMENT_CHANNELS

FEATURES_DIR = DATA_DIR / "features"
META_COLUMNS = [
    "segment_id", "lap_id", "year", "round", "session", "driver", "driver_number", "team",
    "lap_number", "corner", "corner_letter", "apex_m", "lap_class", "compound", "tyre_life",
    "stint", "gap_ahead_s", "track_temp", "air_temp", "rainfall", "deleted",
]  # fmt: skip

# Which segments are evaluated: qualifying attempts, and race laps including slow ones (going
# off track is a common reason for a slow lap). Out/in laps, yellow flags, safety cars and the
# race start are excluded.
ELIGIBLE_SQL = (
    "((session in ('Q', 'SQ', 'SS') and lap_class = 'push')"
    " or (session in ('R', 'S') and lap_class in ('race', 'slow')))"
)

POSITIVE, NEGATIVE, IGNORE = 1, 0, -1
CLUSTER_SIZE = 3  # this many drivers at one lap and turn: something on track, not a mistake
NEIGHBOUR_M = 200.0  # corner windows this close to a labelled corner overlap it


def build_features(force: bool = False) -> int:
    """Compute segment features for processed sessions that don't have them yet."""
    written = 0
    for source in sorted((PROCESSED_DIR / "segments").glob("*.parquet")):
        target = FEATURES_DIR / "segment_features" / source.name
        if not force and target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            continue
        table = pq.read_table(source)
        channels = {c: fixed_list_to_numpy(table.column(c)) for c in SEGMENT_CHANNELS}
        meta = table.select([c for c in META_COLUMNS if c in table.column_names]).to_pandas()
        frame = meta.assign(**corner_features(channels))
        write_table(
            pa.Table.from_pandas(frame, preserve_index=False),
            "segment_features",
            source.stem,
            FEATURES_DIR,
        )
        written += 1
    return written


def load_eval_frame() -> pd.DataFrame:
    """Eligible segments with features, event/location info and a `label` column."""
    con = connect()
    glob = (FEATURES_DIR / "segment_features" / "*.parquet").as_posix()
    frame = con.sql(
        f"""select f.*, s.event, s.location
            from read_parquet('{glob}', union_by_name = true) f
            join sessions s using (year, round, session)
            where {ELIGIBLE_SQL}
            order by f.segment_id"""
    ).df()
    events = con.sql("select * from events").df()
    frame["label"] = assign_labels(frame, events)
    return frame


def assign_labels(frame: pd.DataFrame, events: pd.DataFrame) -> np.ndarray:
    """1 = track-limits mistake at this corner, 0 = no label, -1 = ambiguous (left out)."""
    labels = np.full(len(frame), NEGATIVE)
    lap_key = ["year", "round", "session", "driver", "lap_number"]
    seg = (
        frame[[*lap_key, "corner", "apex_m"]]
        .reset_index(drop=True)
        .assign(_row=np.arange(len(frame)))
    )

    track_limits = events[events["kind"] == "track_limits"].rename(columns={"turn": "corner"})
    hits = seg.merge(
        track_limits[[*lap_key, "corner", "drivers_same_lap_turn"]], on=[*lap_key, "corner"]
    )
    clustered = hits["drivers_same_lap_turn"] >= CLUSTER_SIZE
    positives = hits[~clustered]

    # Overlapping windows of neighbouring corners on the same lap would also look unusual.
    near = seg.merge(
        positives[[*lap_key, "apex_m"]].rename(columns={"apex_m": "_label_apex"}), on=lap_key
    )
    near = near[np.abs(near["apex_m"] - near["_label_apex"]) <= NEIGHBOUR_M]
    labels[near["_row"].to_numpy()] = IGNORE
    labels[hits.loc[clustered, "_row"].to_numpy()] = IGNORE

    # Incidents are mapped to the lap in progress when race control posted them, which can be
    # a lap late, so the whole lap and the one before are left out.
    incidents = events[events["kind"] == "incident"]
    for shift in (0, 1):
        inc = incidents[lap_key].assign(lap_number=incidents["lap_number"] - shift)
        labels[seg.merge(inc, on=lap_key)["_row"].to_numpy()] = IGNORE

    labels[positives["_row"].to_numpy()] = POSITIVE
    return labels
