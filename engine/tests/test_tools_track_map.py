"""The track map: frame, outline, turns and start line, the rotation from circuits.json, and
building circuits.json (with FastF1 replaced by a fake reader)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from race_engineer.data import circuits, store
from race_engineer.data.circuits import (
    build_circuits,
    catalog_rounds,
    circuits_path,
    load_circuits,
    read_rotation,
)
from race_engineer.inference.explain import turn_label
from race_engineer.tools.synthetic import SyntheticSession, write_session
from race_engineer.tools.track_map import (
    MAP_PAD,
    MAP_SIZE,
    MapFrame,
    rotation,
    rotation_of,
    track_map,
)

LENGTH_M = 5000.0
CORNERS = ((1, "", 0.0), (2, "", 1250.0), (5, "", 2500.0), (5, "A", 2600.0))


def session(year: int = 2026, round_: int = 3, **kwargs) -> SyntheticSession:
    return SyntheticSession(
        year, round_, "Q", "Sample Grand Prix", "Synthetic Circuit", corners=CORNERS, **kwargs
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_session(session(), tmp_path)
    return tmp_path


def write_circuits(root: Path, entries: dict) -> None:
    circuits_path(root).write_text(json.dumps(entries))


def turn(track, label: str) -> dict:
    return next(t for t in track.payload["turns"] if t["label"] == label)


def test_frame_keeps_the_longer_side_at_the_map_size() -> None:
    x, y = np.array([0.0, 1000.0, 1000.0, 0.0]), np.array([0.0, 0.0, 500.0, 500.0])
    frame = MapFrame(x, y, 0.0)
    assert frame.width == MAP_SIZE
    assert frame.height == round(2 * MAP_PAD + 500 * (MAP_SIZE - 2 * MAP_PAD) / 1000)
    px, py = frame(x, y)
    assert px.min() == pytest.approx(MAP_PAD) and px.max() == pytest.approx(MAP_SIZE - MAP_PAD)
    assert py[2] < py[1]  # north is up: larger y, smaller pixel row


def test_frame_turns_the_track_anticlockwise() -> None:
    x, y = np.array([0.0, 1000.0, 1000.0, 0.0]), np.array([0.0, 0.0, 500.0, 500.0])
    frame = MapFrame(x, y, 90.0)
    assert (frame.width, frame.height) == (MapFrame(x, y, 0.0).height, MAP_SIZE)
    # East turns to north: the far east corner of the rectangle is now at the top.
    _, py = frame(x, y)
    assert py[1] == pytest.approx(MAP_PAD) and py[0] == pytest.approx(MAP_SIZE - MAP_PAD)


def test_map_payload(root: Path) -> None:
    track = track_map("2026_03_Q", root)
    assert track is not None
    payload = track.payload
    n = len(np.arange(0.0, LENGTH_M - 2.5, 5.0))
    assert payload["step_m"] == 20.0 and payload["length_m"] == LENGTH_M
    assert len(payload["outline"]) == math.ceil(n / 4) + 1  # every 20 m, closed
    assert payload["outline"][0] == payload["outline"][-1]
    assert max(payload["width"], payload["height"]) == MAP_SIZE
    labels = [turn_label(n, letter) for n, letter, _ in CORNERS]
    assert [t["label"] for t in payload["turns"]] == labels == ["1", "2", "5", "5A"]
    assert payload["corners"] == [
        {"label": label, "apex_m": apex}
        for label, (_, _, apex) in zip(labels, CORNERS, strict=True)
    ]
    # Each turn number sits 70 m outwards of its turn (the circle's radius is ~796 m, so ~9%
    # of the map's radius further from the centre).
    centre = np.array([payload["width"], payload["height"]]) / 2
    t1 = turn(track, "1")
    assert np.hypot(t1["tx"] - centre[0], t1["ty"] - centre[1]) > np.hypot(
        t1["x"] - centre[0], t1["y"] - centre[1]
    )
    json.dumps(payload, allow_nan=False)  # plain JSON, no NaN


def test_start_line_points_the_way_the_cars_drive(root: Path) -> None:
    track = track_map("2026_03_Q", root)
    assert track is not None
    start = track.payload["start"]
    assert start is not None
    # The synthetic circle is driven anticlockwise from its east end: north, so up the map.
    assert start["x"] == pytest.approx(MAP_SIZE - MAP_PAD, abs=0.1)
    assert start["dy"] == pytest.approx(-1.0, abs=0.01) and abs(start["dx"]) < 0.05


def test_north_up_without_circuits_file(root: Path) -> None:
    track = track_map("2026_03_Q", root)
    assert track is not None and track.payload["rotation_source"] == "default"
    assert rotation_of(2026, 3, root) == (0.0, "default") and rotation(2026, 3, root) == 0.0
    t1 = turn(track, "1")  # the east end of the circle: the right edge
    assert t1["x"] == pytest.approx(MAP_SIZE - MAP_PAD, abs=0.1)


def test_rotation_from_circuits_file(root: Path) -> None:
    assert track_map("2026_03_Q", root) is not None  # cached north up first
    write_circuits(root, {"2026_03": {"rotation_deg": 90.0, "source": "fastf1"}})
    track = track_map("2026_03_Q", root)  # the file changed: built again
    assert track is not None and track.payload["rotation_source"] == "fastf1"
    assert rotation(2026, 3, root) == 90.0
    t1 = turn(track, "1")  # east turned to north: the top edge
    assert t1["y"] == pytest.approx(MAP_PAD, abs=0.1)
    assert rotation_of(2026, 4, root) == (0.0, "default")  # a round the file doesn't have


def test_map_without_turns_or_track(tmp_path: Path) -> None:
    write_session(SyntheticSession(2026, 1, "R", "Sample", "Synthetic"), tmp_path)
    track = track_map("2026_01_R", tmp_path)
    assert track is not None
    assert track.payload["turns"] == [] and track.payload["corners"] == []
    assert track_map("2026_09_R", tmp_path) is None


def test_pixels_wrap_around_the_lap(root: Path) -> None:
    track = track_map("2026_03_Q", root)
    assert track is not None
    d = np.array([0.0, 1250.0, 4997.5, math.nan])
    here, later = track.pixels(d), track.pixels(d + 3 * LENGTH_M)
    np.testing.assert_allclose(here[:3], later[:3], atol=1e-6)
    assert np.isnan(here[3]).all() and np.isfinite(here[:3]).all()
    # Between the last grid point (4995 m) and the line, half way back to the first point.
    last, first = track.pixels(np.array([4995.0, 0.0]))
    np.testing.assert_allclose(here[2], (last + first) / 2, atol=1e-6)
    t2 = turn(track, "2")
    np.testing.assert_allclose(here[1], [t2["x"], t2["y"]], atol=0.06)  # the turn is on the line


# circuits.json


def test_catalog_rounds_from_the_session_files(tmp_path: Path) -> None:
    for year, round_, code in ((2025, 16, "Q"), (2025, 16, "R"), (2026, 2, "SQ")):
        write_session(SyntheticSession(year, round_, code, "Sample", "Synthetic"), tmp_path)
    assert catalog_rounds(tmp_path) == [(2025, 16), (2026, 2)]
    assert catalog_rounds(tmp_path / "nothing") == []


def test_rotation_comes_from_the_race_then_qualifying() -> None:
    calls = []

    def read(year: int, round_: int, code: str) -> float | None:
        calls.append(code)
        return {"Q": 44.0}.get(code)

    assert read_rotation(2026, 1, read) == 44.0 and calls == ["R", "Q"]
    assert read_rotation(2026, 1, lambda *_: None) is None


def test_build_circuits(tmp_path: Path) -> None:
    for round_ in (1, 2, 3):
        write_session(SyntheticSession(2026, round_, "R", "Sample", "Synthetic"), tmp_path)
    angles = {(2026, 1, "R"): 44.04, (2026, 2, "Q"): 237.0}
    out = build_circuits(tmp_path, read=lambda y, r, c: angles.get((y, r, c)))
    assert out == {
        "2026_01": {"rotation_deg": 44.0, "source": "fastf1"},
        "2026_02": {"rotation_deg": 237.0, "source": "fastf1"},
        "2026_03": {"rotation_deg": 0.0, "source": "default"},
    }
    assert load_circuits(tmp_path) == out
    # FastF1's cache lost round 1: the earlier angle stays. Round 3 is tried again.
    again = build_circuits(tmp_path, read=lambda y, r, c: 90.0 if r == 3 else None)
    assert again["2026_01"]["rotation_deg"] == 44.0
    assert again["2026_02"]["rotation_deg"] == 237.0
    assert again["2026_03"] == {"rotation_deg": 90.0, "source": "fastf1"}


def test_build_circuits_only_missing(tmp_path: Path) -> None:
    write_circuits(tmp_path, {"2026_01": {"rotation_deg": 0.0, "source": "default"}})
    for round_ in (1, 2):
        write_session(SyntheticSession(2026, round_, "R", "Sample", "Synthetic"), tmp_path)
    asked = []

    def read(year: int, round_: int, code: str) -> float | None:
        asked.append(round_)
        return 10.0

    out = build_circuits(tmp_path, only_missing=True, read=read)
    assert set(asked) == {2}
    assert out["2026_01"]["source"] == "default" and out["2026_02"]["rotation_deg"] == 10.0


def test_build_circuits_without_a_fastf1_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Processed data copied from elsewhere: every round north up, an earlier FastF1 angle kept.
    monkeypatch.setattr(circuits, "FASTF1_CACHE", tmp_path / "fastf1-cache")
    write_circuits(tmp_path, {"2026_01": {"rotation_deg": 44.0, "source": "fastf1"}})
    for round_ in (1, 2):
        write_session(SyntheticSession(2026, round_, "R", "Sample", "Synthetic"), tmp_path)
    assert build_circuits(tmp_path) == {
        "2026_01": {"rotation_deg": 44.0, "source": "fastf1"},
        "2026_02": {"rotation_deg": 0.0, "source": "default"},
    }


def test_load_circuits_ignores_what_it_cannot_read(tmp_path: Path) -> None:
    assert load_circuits(tmp_path) == {}
    circuits_path(tmp_path).write_text("{not json")
    assert load_circuits(tmp_path) == {}
    write_circuits(
        tmp_path,
        {
            "2026_01": {"rotation_deg": 12, "source": "fastf1"},
            "2026_02": {"rotation_deg": "east", "source": "fastf1"},
            "2026_03": {"rotation_deg": 1.0, "source": "guess"},
            "2026_04": 5,
        },
    )
    assert load_circuits(tmp_path) == {"2026_01": {"rotation_deg": 12.0, "source": "fastf1"}}


def test_circuits_file_is_not_a_table(tmp_path: Path) -> None:
    write_session(session(), tmp_path)
    write_circuits(tmp_path, {"2026_03": {"rotation_deg": 90.0, "source": "fastf1"}})
    # The processed folder lists its tables by name: circuits.json is never read as one.
    assert "circuits" not in store.TABLES
    assert store.connect(tmp_path).execute("SELECT count(*) FROM sessions").fetchone() == (1,)
