"""The maps and the replay in find_mistakes' and explain_corner's chart data, on the synthetic
session of test_tools_mistakes (with its turns added to a corners table, so the map has them)."""

import dataclasses
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pandas as pd
import pytest
from test_tools_mistakes import mistakes_dataset

from race_engineer.data import store
from race_engineer.inference import traffic
from race_engineer.inference.explain import compare, load_session_segments
from race_engineer.tools import mistake_charts, mistakes
from race_engineer.tools.definitions import EXPLAIN_CORNER, FIND_MISTAKES
from race_engineer.tools.mistake_charts import map_and_replay
from race_engineer.tools.mistakes import corner_chart, explain_corner, find_mistakes, ghost_lap
from race_engineer.tools.registry import PAYLOAD_MAX_BYTES, RESULTS, DataPaths, run_tool
from race_engineer.tools.replay import json_size, load_session_tracks
from race_engineer.tools.synthetic import circle_xy
from race_engineer.tools.track_map import track_map

LENGTH_M = 5000.0
TURNS = ((3, "", 3000.0), (4, "", 3200.0), (5, "", 5000.0), (5, "A", 5200.0))


def write_corners(root: Path, key: str) -> None:
    """A corners table for the session, on the synthetic circle (as synthetic.write_session)."""
    apex = np.array([a for _, _, a in TURNS])
    x, y = circle_xy(apex, LENGTH_M)
    year, round_, code = key.split("_")
    frame = pd.DataFrame(
        {
            "number": [n for n, _, _ in TURNS],
            "letter": [letter for _, letter, _ in TURNS],
            "apex_m": apex,
            "x_m": x,
            "y_m": y,
            "angle": np.degrees(2 * np.pi * apex / LENGTH_M),
            "year": int(year),
            "round": int(round_),
            "session": code,
        }
    )
    curvature = store.fixed_list_column([np.zeros(80)] * len(frame), 80)
    store.write_table(store.to_arrow(frame, {"curvature": curvature}), "corners", key, root)


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> DataPaths:
    root, results = mistakes_dataset(tmp_path_factory.mktemp("charts"))
    write_corners(root, "2026_01_Q")
    return DataPaths(root, results)


def size(payload: dict) -> int:
    return len(json.dumps(payload, separators=(",", ":"), allow_nan=False))


def corner(paths: DataPaths, driver: str, lap: int, turn: int | str, session: str = "Q"):
    return explain_corner(
        "sample", driver, lap, turn, session=session, root=paths.processed, results=paths.results
    )


def test_find_mistakes_has_the_track(paths: DataPaths) -> None:
    data = find_mistakes("sample", root=paths.processed, results=paths.results).data
    track = data["track"]
    found = track_map("2026_01_Q", paths.processed)
    assert found is not None and track == found.payload
    assert track["rotation_source"] == "default" and len(track["outline"]) == 251
    assert [t["label"] for t in track["turns"]] == ["3", "4", "5", "5A"]
    # The turns the mistakes are at are on the map, by label, and each has its apex.
    labels = {t["label"] for t in track["turns"]}
    assert {m["turn"] for m in data["mistakes"]} <= labels
    apexes = {c["label"]: c["apex_m"] for c in track["corners"]}
    assert all(m["apex_m"] == apexes[m["turn"]] for m in data["mistakes"])
    assert size(data) < PAYLOAD_MAX_BYTES


def test_no_track_for_a_session_without_one(paths: DataPaths, tmp_path: Path) -> None:
    root = tmp_path / "processed"
    root.mkdir()
    for path in paths.processed.rglob("*.parquet"):  # everything but the tracks table
        if path.parent.name != "tracks":
            (root / path.relative_to(paths.processed)).parent.mkdir(exist_ok=True)
            (root / path.relative_to(paths.processed)).write_bytes(path.read_bytes())
    assert find_mistakes("sample", root=root, results=paths.results).data["track"] is None
    report = explain_corner("sample", "AAA", 10, 3, root=root, results=paths.results)
    assert report.chart["map"] is None and report.chart["replay"] is None


def test_explain_corner_has_the_map_and_replay(paths: DataPaths) -> None:
    chart = corner(paths, "AAA", 10, 3).chart
    track = track_map("2026_01_Q", paths.processed)
    assert track is not None and chart["apex_m"] == 3000.0
    corner_map = chart["map"]
    assert corner_map["outline"] == track.payload["outline"]
    # The analysed stretch, every 5 m from 250 m before the apex (3000 m) to 145 m after.
    window = np.array(corner_map["window"])
    assert len(window) == 80
    np.testing.assert_allclose(window, track.pixels(np.arange(2750.0, 3146.0, 5.0)), atol=0.06)
    apex = track.pixels(np.array([3000.0]))[0]
    np.testing.assert_allclose([corner_map["apex"]["x"], corner_map["apex"]["y"]], apex, atol=0.06)
    assert corner_map["cars"] == []  # nobody else on track on AAA's lap 10
    replay = chart["replay"]
    assert replay["dist"][0] == -400.0 and replay["cars"] == []
    assert len(replay["me"]) == len(replay["dist"]) == len(replay["ghost"])
    # The ghost is the reference lap the chart names: one of AAA's clean laps (9 was deleted).
    ghost = chart["ghost"]
    assert ghost["driver"] == "AAA" and 1 <= ghost["lap"] <= 8
    assert replay["ghost_label"] == f"usual: lap {ghost['lap']}"
    assert size(chart) < PAYLOAD_MAX_BYTES


def test_the_usual_band_and_the_line(paths: DataPaths) -> None:
    chart = corner(paths, "AAA", 10, 3).chart
    for channel in ("speed", "throttle"):
        lo, mid, hi = (chart[k][channel] for k in ("usual_lo", "usual", "usual_hi"))
        assert len(lo) == len(hi) == 80
        assert all(a <= b + 0.05 and b <= c + 0.05 for a, b, c in zip(lo, mid, hi, strict=True))
    assert len(chart["offset"]["lap"]) == len(chart["offset"]["usual"]) == 80


def test_the_ghost_of_a_field_reference(paths: DataPaths) -> None:
    # BBB has three other clean laps: compared with the field, so the ghost can be AAA's.
    chart = corner(paths, "BBB", 4, 3).chart
    assert chart["reference"]["kind"] == "field"
    ghost = chart["ghost"]
    who = "" if ghost["driver"] == "BBB" else f"{ghost['driver']} "
    assert chart["replay"]["ghost_label"] == f"usual: {who}lap {ghost['lap']}"


def test_ghost_lap_is_the_reference_lap_closest_to_the_median() -> None:
    frame = pd.DataFrame(
        {
            "driver": ["AAA", "AAA", "BBB", "AAA", "CCC"],
            "lap_number": [1, 2, 1, 3, 1],
            "segment_time_s": [10.0, 10.4, 10.2, 12.0, np.nan],
        }
    )
    seg = cast(Any, SimpleNamespace(frame=frame))

    def reference(*rows: int) -> Any:
        return SimpleNamespace(reference_rows=np.array(rows, dtype=int))

    assert ghost_lap(seg, reference(0, 1, 2, 4)) == ("BBB", 1)  # row 3 is the lap explained
    assert ghost_lap(seg, reference(0, 1)) == ("AAA", 1)  # the first of a tie
    assert ghost_lap(seg, reference(4)) is None  # no time through the corner
    assert ghost_lap(seg, reference()) is None


def test_no_band_or_ghost_without_a_reference(paths: DataPaths) -> None:
    # The same corner with no clean lap to compare with: no usual, no band and no ghost.
    report = corner(paths, "AAA", 10, 3)
    seg = load_session_segments("2026_01_Q", paths.processed)
    row = report.explanation.comparison.row
    alone = compare(seg, row, clean=np.zeros(len(seg.frame), dtype=bool))
    assert alone.reference == "none"
    exp = dataclasses.replace(report.explanation, comparison=alone)
    chart = corner_chart(report.session, exp, seg, seg.frame.iloc[row], "Team A", True, 0.99)
    assert chart["usual_lo"] is None and chart["usual_hi"] is None and chart["ghost"] is None
    assert set(chart["offset"]["usual"]) == {None} and None not in chart["offset"]["lap"]
    corner_map, replay = map_and_replay(chart, "2026_01_Q", paths.processed, None)
    assert corner_map is not None and corner_map["cars"] == [] and replay is None
    loaded = load_session_tracks("2026_01_Q", paths.processed)
    replay = map_and_replay(chart, "2026_01_Q", paths.processed, loaded)[1]
    assert replay is not None and "ghost" not in replay and "ghost_label" not in replay


def test_cars_around_in_the_race(paths: DataPaths) -> None:
    # In the race, BBB runs alongside AAA (same lap starts, same speed).
    chart = corner(paths, "AAA", 3, 3, session="R").chart
    assert chart["map"]["turns"] == []  # the race has no corners table: a map without turns
    assert [(c["driver"], c["gap_s"], c["laps"]) for c in chart["map"]["cars"]] == [("BBB", 0.0, 0)]
    assert [(c["driver"], c["approx"]) for c in chart["replay"]["cars"]] == [("BBB", False)]
    # In a race the reference laps are the nearest clean laps in lap number.
    assert chart["ghost"]["driver"] == "AAA" and chart["ghost"]["lap"] in (2, 4, 5, 6)
    assert chart["replay"]["ghost_label"] == f"usual: lap {chart['ghost']['lap']}"


def test_the_session_tracks_load_once(paths: DataPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    # The traffic explanation and the map and replay get the same tracks, read once.
    load_session_tracks.cache_clear()
    reads: list[str] = []
    seen: dict[str, Any] = {}
    load, explain, views = traffic.load_tracks, mistakes.explain, mistakes.map_and_replay

    def load_spy(key: str, root: Path) -> Any:
        reads.append(key)
        return load(key, root)

    def explain_spy(*args: Any, **kwargs: Any) -> Any:
        seen["traffic"] = kwargs["tracks"]
        return explain(*args, **kwargs)

    def views_spy(chart: dict, key: str, root: Path, loaded: Any) -> Any:
        seen["replay"] = loaded
        return views(chart, key, root, loaded)

    monkeypatch.setattr(traffic, "load_tracks", load_spy)
    monkeypatch.setattr(mistakes, "explain", explain_spy)
    monkeypatch.setattr(mistakes, "map_and_replay", views_spy)
    corner(paths, "AAA", 3, 3, session="R")
    assert reads == ["2026_01_R"]
    assert seen["traffic"] is not None and seen["traffic"] is seen["replay"].tracks
    corner(paths, "AAA", 4, 3, session="R")
    assert reads == ["2026_01_R"]  # cached


def test_payload_budget_drops_replay_cars(
    paths: DataPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = corner(paths, "AAA", 3, 3, session="R").chart
    bare = {**full, "replay": {**full["replay"], "cars": []}}
    monkeypatch.setattr(mistake_charts, "CHART_MAX_BYTES", json_size(bare) + 10)
    tight = corner(paths, "AAA", 3, 3, session="R").chart
    assert tight["replay"]["cars"] == [] and tight["replay"]["me"] == full["replay"]["me"]
    assert {k: v for k, v in tight.items() if k != "replay"} == {
        k: v for k, v in full.items() if k != "replay"
    }


def test_lettered_turn(paths: DataPaths) -> None:
    chart = corner(paths, "AAA", 2, "5a").chart
    track = track_map("2026_01_Q", paths.processed)
    assert track is not None and chart["turn"] == "5A" and chart["apex_m"] == 5200.0
    apex = track.pixels(np.array([5200.0]))[0]  # 5A's apex, not 5's
    found = chart["map"]["apex"]
    np.testing.assert_allclose([found["x"], found["y"]], apex, atol=0.06)


def test_the_map_never_fails_the_tool(
    paths: DataPaths, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    found = find_mistakes("sample", root=paths.processed, results=paths.results)
    report = corner(paths, "AAA", 10, 3)

    def broken(*args: object) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(mistake_charts, "track_map", broken)
    with caplog.at_level(logging.WARNING, logger="race_engineer.tools.mistake_charts"):
        found_now = find_mistakes("sample", root=paths.processed, results=paths.results)
        report_now = corner(paths, "AAA", 10, 3)
    assert found_now.data["track"] is None and found_now.summary == found.summary
    assert report_now.chart["map"] is None and report_now.chart["replay"] is None
    assert report_now.summary == report.summary
    assert report_now.chart["explanation"] == report.chart["explanation"]
    assert "disk on fire" in caplog.text


def test_through_the_registry(paths: DataPaths) -> None:
    RESULTS.clear()
    found = run_tool(FIND_MISTAKES, {"event": "sample", "driver": "AAA"}, paths)
    assert found.data is not None and found.data["track"]["turns"]
    args = {"event": "sample", "driver": "AAA", "lap": 10, "corner": 3}
    report = run_tool(EXPLAIN_CORNER, args, paths)
    assert report.data is not None and report.data["map"] and report.data["replay"]
    assert size(report.data) < PAYLOAD_MAX_BYTES
