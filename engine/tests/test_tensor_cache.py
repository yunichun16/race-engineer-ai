import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from race_engineer.models import tensors
from race_engineer.models.tensors import (
    BUNDLE_COLUMNS,
    TelemetryData,
    export_bundle,
    load_bundle,
    load_or_build_tensors,
)


def fake_frame(ids: range | list[int]) -> pd.DataFrame:
    ids = list(ids)
    return pd.DataFrame(
        {
            "segment_id": [f"seg{i:03d}" for i in ids],
            "lap_id": [f"lap{i // 4}" for i in ids],
            "year": 2026,
            "round": 1,
            "session": "R",
            "event": "Test GP",
            "location": "Testville",
            "driver": ["AAA", "BBB"] * (len(ids) // 2) + ["AAA"] * (len(ids) % 2),
            "team": "Team",
            "corner": [i % 4 + 1 for i in ids],
            "label": 0,
            "v_min": 80.0,  # a feature column: in the cache, not in bundles
        }
    )


def fake_build(frame: pd.DataFrame) -> TelemetryData:
    """Arrays whose values are the segment number, so row alignment is checkable."""
    frame = frame.reset_index(drop=True)
    value = frame["segment_id"].str[3:].astype(int).to_numpy()
    n = len(frame)
    return TelemetryData(
        frame=frame,
        signal=np.broadcast_to(value[:, None, None], (n, 5, 80)).astype(np.float16),
        context_channels=np.broadcast_to(-value[:, None, None], (n, 2, 80)).astype(np.float16),
        context_cat=np.column_stack([value % 2, value % 6]).astype(np.int64),
        context_num=np.broadcast_to(value[:, None] / 10, (n, 4)).astype(np.float32),
    )


@pytest.fixture(autouse=True)
def processed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private processed/ tree: the cache key stats its files, never the real ones."""
    root = tmp_path / "processed"
    monkeypatch.setattr(tensors, "PROCESSED_DIR", root)
    return root


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def counting_build(frame: pd.DataFrame) -> TelemetryData:
        calls.append(len(frame))
        return fake_build(frame)

    monkeypatch.setattr(tensors, "build_tensors", counting_build)
    return calls


def test_cache_round_trip_follows_the_callers_row_order(tmp_path: Path, builds: list[int]) -> None:
    frame = fake_frame(range(12))
    first = load_or_build_tensors(frame, tmp_path)
    assert builds == [12]
    assert len(list(tmp_path.glob("*.npz"))) == len(list(tmp_path.glob("*.parquet"))) == 1

    shuffled = frame.sample(frac=1, random_state=0)
    second = load_or_build_tensors(shuffled, tmp_path)
    assert builds == [12]  # served from the cache
    assert second.frame["segment_id"].tolist() == shuffled["segment_id"].tolist()
    expected = shuffled["segment_id"].str[3:].astype(int).to_numpy()
    assert np.array_equal(second.signal[:, 3, 40], expected)
    assert np.array_equal(second.context_cat[:, 1], expected % 6)
    assert second.signal.dtype == np.float16 and second.context_num.dtype == np.float32
    assert np.array_equal(first.signal, load_or_build_tensors(frame, tmp_path).signal)


def test_a_changed_dataset_or_version_rebuilds(
    tmp_path: Path, builds: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    load_or_build_tensors(fake_frame(range(12)), tmp_path)
    load_or_build_tensors(fake_frame(range(11)), tmp_path)  # a segment fewer
    load_or_build_tensors(fake_frame([*range(11), 99]), tmp_path)  # same size, other segment
    assert builds == [12, 11, 12]
    monkeypatch.setattr(tensors, "TENSOR_VERSION", tensors.TENSOR_VERSION + 1)
    load_or_build_tensors(fake_frame(range(12)), tmp_path)
    load_or_build_tensors(fake_frame(range(12)), tmp_path, refresh=True)
    assert len(builds) == 5


def test_a_reprocessed_session_rebuilds(tmp_path: Path, builds: list[int], processed: Path) -> None:
    source = processed / "segments" / "2026_01_R.parquet"  # fake_frame's one session
    source.parent.mkdir(parents=True)
    source.write_bytes(b"first build")
    cache = tmp_path / "cache"
    load_or_build_tensors(fake_frame(range(12)), cache)
    load_or_build_tensors(fake_frame(range(12)), cache)
    assert builds == [12]
    source.write_bytes(b"reprocessed")  # same segment ids, new values
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 10**9))
    load_or_build_tensors(fake_frame(range(12)), cache)
    assert builds == [12, 12]


def test_old_cache_entries_are_pruned(tmp_path: Path, builds: list[int]) -> None:
    for n in range(4, 4 + tensors.CACHE_KEEP + 2):
        load_or_build_tensors(fake_frame(range(n)), tmp_path)
    assert len(list(tmp_path.glob("*.npz"))) == tensors.CACHE_KEEP
    assert len(list(tmp_path.glob("*.parquet"))) == tensors.CACHE_KEEP


def test_bundle_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = fake_build(fake_frame(range(10)))
    export_bundle(tmp_path / "bundle", data)
    loaded = load_bundle(tmp_path / "bundle")
    assert set(loaded.frame.columns) == set(BUNDLE_COLUMNS) & set(data.frame.columns)
    assert loaded.frame["segment_id"].tolist() == data.frame["segment_id"].tolist()
    for name in tensors.ARRAYS:
        a, b = getattr(loaded, name), getattr(data, name)
        assert a.dtype == b.dtype and np.array_equal(a, b)

    monkeypatch.setattr(tensors, "TENSOR_VERSION", tensors.TENSOR_VERSION + 1)
    with pytest.raises(ValueError, match="export the bundle again"):
        load_bundle(tmp_path / "bundle")


def test_a_mismatched_or_unfinished_bundle_is_refused(tmp_path: Path) -> None:
    bundle = export_bundle(tmp_path / "bundle", fake_build(fake_frame(range(12))))
    other = export_bundle(tmp_path / "other", fake_build(fake_frame(range(20, 32))))
    (other / "frame.parquet").replace(bundle / "frame.parquet")  # same size, other segments
    with pytest.raises(ValueError, match="export the bundle again"):
        load_bundle(bundle)
    export_bundle(bundle, fake_build(fake_frame(range(8))))  # fewer rows than the old arrays
    np.savez(bundle / "tensors.npz", **{n: getattr(fake_build(fake_frame(range(12))), n)
                                        for n in tensors.ARRAYS})  # fmt: skip
    with pytest.raises(ValueError, match="inconsistent"):
        load_bundle(bundle)
    (bundle / "manifest.json").unlink()  # an export that stopped before its manifest
    with pytest.raises(ValueError, match="no manifest"):
        load_bundle(bundle)


def test_subset_keeps_rows_aligned() -> None:
    data = fake_build(fake_frame(range(10)))
    part = data.subset(np.array([7, 2]))
    assert part.frame["segment_id"].tolist() == ["seg007", "seg002"]
    assert part.signal[:, 0, 0].tolist() == [7, 2] and len(part) == 2
