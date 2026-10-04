"""find_mistakes and explain_corner on a synthetic session with known mistakes.

AAA drives turn 3 the same way on laps 1-8, then picks up the throttle 40 m late on lap 9
(and has that lap deleted for track limits at turn 3) and brakes 50 m early on lap 10, where
it is also late on the throttle out of turn 4, 200 m on (the two windows overlap). BBB has
three ordinary laps and over-slows on lap 4, so BBB is compared with the field. Turn 5 comes
in two parts (5 and 5A), turn 7 only in lettered parts (7A, 7B). Nothing of CCC or DDD is
scored: CCC has an out lap and a lap without a lap time (the crash lap, with telemetry at turn
3), DDD two push laps without usable telemetry. The race of the same weekend has 20 laps, but
telemetry for laps 2-6 only.

The results directory is what `race-engineer-infer score` and `explain` write: a manifest with
the scoring run's id, and the parquet files recording that run.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from test_inference_explain import (
    build_table,
    lap_spec,
    scores_frame,
    usual_laps,
    write_events,
    write_segments,
)
from test_inference_traffic import lapped_laps

from race_engineer.inference import provenance
from race_engineer.inference.explain import run_explain
from race_engineer.tools import mistakes as mistakes_tool
from race_engineer.tools.mistakes import explain_corner, find_mistakes, parse_turn
from race_engineer.tools.replay import NEARBY_S
from race_engineer.tools.sessions import ResultsUnavailableError, ToolInputError
from race_engineer.tools.synthetic import (
    SyntheticLap,
    SyntheticSession,
    grid_distance,
    write_session,
)

FLAGGED = ["2026_01_Q_1_9_T3", "2026_01_Q_1_10_T3", "2026_01_Q_2_4_T3", "2026_01_Q_1_3_T3"]
FLAGGED += ["2026_01_Q_1_10_T4"]
RUN_ID = provenance.new_run_id("2026-09-30T12:00:00+00:00", "0123456789abcdef")
SCORES, MISTAKES = "segment_scores.parquet", "mistakes.parquet"


def mistakes_dataset(base: Path) -> tuple[Path, Path]:
    """(processed root, results dir) for the sessions in the module docstring."""
    root, results = base / "processed", base / "results"
    n = len(grid_distance(5000.0))
    drivers = [("AAA", "1", "Team A", ["push"] * 10), ("BBB", "2", "Team B", ["push"] * 4)]
    drivers += [("CCC", "3", "Team C", ["out", "no_time"]), ("DDD", "4", "Team D", ["push"] * 2)]
    laps = tuple(
        SyntheticLap(driver, lap, np.full(n, 180.0), number, team, lap_class)
        for driver, number, team, classes in drivers
        for lap, lap_class in enumerate(classes, start=1)
    )
    write_session(
        SyntheticSession(2026, 1, "Q", "Sample Grand Prix", "Synthetic Circuit", laps=laps), root
    )
    race_laps = tuple(
        SyntheticLap(driver, lap, np.full(n, 180.0), number, team, "start" if lap == 1 else "race")
        for driver, number, team, _ in drivers[:2]
        for lap in range(1, 21)
    )
    write_session(
        SyntheticSession(2026, 1, "R", "Sample Grand Prix", "Synthetic Circuit", laps=race_laps),
        root,
    )
    specs = [
        *usual_laps(),
        lap_spec("AAA", 9, pickup_d=50.0, deleted=True),
        lap_spec("AAA", 10, brake_d=-170.0),
        *usual_laps("BBB", range(1, 4)),
        lap_spec("BBB", 4, v_min=75.0),
        *usual_laps(laps=range(1, 10), corner=4, apex_m=3200.0),
        lap_spec("AAA", 10, 4, apex_m=3200.0, pickup_d=50.0),
        lap_spec("CCC", 2, lap_class="no_time"),
    ]
    for corner, letter, apex in ((5, "", 5000.0), (5, "A", 5200.0), (7, "A", 7000.0)):
        specs += [lap_spec("AAA", lap, corner, letter=letter, apex_m=apex) for lap in range(1, 9)]
    specs += [lap_spec("AAA", lap, 7, letter="B", apex_m=7200.0) for lap in range(1, 9)]
    table, traces = build_table(specs)
    write_segments(table, traces, root)
    race, race_traces = build_table(
        [*usual_laps(laps=range(2, 7)), *usual_laps("BBB", range(2, 7))], code="R"
    )
    write_segments(race, race_traces, root)
    track_limits = {
        "driver": "AAA", "lap_number": 9, "turn": 3, "kind": "track_limits",
        "source": "fastf1_deleted", "message": "TRACK LIMITS AT TURN 3",
    }  # fmt: skip
    write_events([track_limits], "2026_01_Q", root, {"year": 2026, "round": 1, "session": "Q"})
    # As the scoring run: only the laps the detectors score (push laps here) get a score.
    scored = pd.concat([table[table["lap_class"] == "push"], race], ignore_index=True)
    results.mkdir(parents=True, exist_ok=True)
    (results / provenance.MANIFEST).write_text(json.dumps({"run_id": RUN_ID}))
    provenance.write_parquet(scores_frame(scored, FLAGGED), results / SCORES, RUN_ID)
    mistakes = run_explain(results / SCORES, results / MISTAKES, None, root)
    provenance.write_parquet(mistakes, results / MISTAKES, RUN_ID)  # as `explain` records it
    return root, results


def copy_results(
    results: Path,
    out: Path,
    keep: Callable[[pd.DataFrame], pd.Series] | None = None,
    run_id: str = RUN_ID,
) -> Path:
    """A copy of `results` with only the rows `keep` selects, recorded as built from `run_id`
    (the manifest keeps the current run)."""
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(results / provenance.MANIFEST, out / provenance.MANIFEST)
    for name in (SCORES, MISTAKES):
        frame = pd.read_parquet(results / name)
        if keep is not None:
            frame = frame[keep(frame)]
        provenance.write_parquet(frame, out / name, run_id)
    return out


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return mistakes_dataset(tmp_path_factory.mktemp("mistakes"))


def test_find_mistakes_ranks_the_session(dataset: tuple[Path, Path]) -> None:
    root, results = dataset
    found = find_mistakes("sample", root=root, results=results)
    listed = [(m.driver, m.lap_number, m.turn, m.mistake_type) for m in found.mistakes]
    assert listed[0] == ("AAA", 9, "3", "track_limits")  # race control counts extra
    # Lap 10's turns 3 and 4 overlap: one mistake, listed under the costlier turn.
    lap10 = next(m for m in found.mistakes if m.lap_number == 10)
    assert {lap10.turn, *lap10.also_turns} == {"3", "4"} and len(lap10.also_turns) == 1
    assert set(listed) == {
        ("AAA", 9, "3", "track_limits"),
        ("AAA", 10, lap10.turn, lap10.mistake_type),
        ("BBB", 4, "3", "over_slowing"),
    }
    assert [m.priority for m in found.mistakes] == sorted(
        [m.priority for m in found.mistakes], reverse=True
    )
    assert found.flagged == 5 and found.listed == 3 and found.scored == 56
    text = found.summary
    assert text.startswith("2026 Sample Grand Prix Qualifying (Synthetic Circuit): 5 of 56 scored")
    assert "3 look like driving mistakes (4 flagged corners; neighbouring turns flagged" in text
    assert "1. AAA [track limits / off track (race control), also late throttle" in text
    assert "Lap 9, turn 3:" in text
    assert f"The same moment also shows at turn {lap10.also_turns[0]}, whose windows" in text
    assert "Flagged but not listed as mistakes: 1 not clearly slower than usual." in text
    # DDD's two push laps have no usable telemetry: eligible, but not scored.
    assert "Laps scored: 1-10 of 10; 14 of the 16 driver-laps of the kinds the detectors " in text
    assert "never opening laps, in or out laps" in text and "Only" not in text
    data = found.data
    assert data["left_out"] == {"no_time_lost": 1} and data["distinct_mistakes"] == 3
    assert data["mistakes"][0]["type"] == "track_limits"
    assert data["mistakes"][0]["time_lost_s"] > 0.05
    # Time lost to 0.01 s, as the explanation and the chart show it: the same number in both.
    for row in data["mistakes"]:
        assert row["time_lost_s"] == round(row["time_lost_s"], 2)
        assert f"{row['time_lost_s']:.2f} s lost" in row["explanation"]
    # Where each turn's apex is along the lap, for the chart's map.
    assert found.mistakes[0].apex_m == data["mistakes"][0]["apex_m"] == 3000.0
    assert data["coverage"] == {
        "laps": list(range(1, 11)),
        "scored_driver_laps": 14,
        "eligible_driver_laps": 16,
        "session_laps": 10,
        "low": False,
    }
    assert data["note"] == ""


def test_find_mistakes_for_one_driver(dataset: tuple[Path, Path]) -> None:
    root, results = dataset
    found = find_mistakes("sample", "bbb", root=root, results=results)
    assert found.driver == "BBB"
    assert [(m.lap_number, m.mistake_type) for m in found.mistakes] == [(4, "over_slowing")]
    assert "compared with the field" in found.mistakes[0].explanation
    assert found.scored == 4
    assert "BBB: 1 of 4 scored corners flagged" in found.summary
    assert "1 looks like a driving mistake." in found.summary
    assert "Laps scored: 1-4 of 10; 4 of BBB's 4 laps of the kinds" in found.summary
    one = find_mistakes("sample", "AAA", limit=1, root=root, results=results)
    assert len(one.mistakes) == 1 and "top 1," in one.summary


@pytest.mark.parametrize(
    ("driver", "why", "laps"),
    [
        ("CCC", "none of CCC's laps is one of those", "1 out lap and 1 lap without a lap time"),
        ("DDD", "DDD has 2 laps of those kinds, but no usable telemetry", "2 push laps"),
    ],
)
def test_find_mistakes_driver_with_nothing_scored(
    dataset: tuple[Path, Path], driver: str, why: str, laps: str
) -> None:
    # Not "0 flagged, no mistakes": nothing of theirs was scored, and the answer says why.
    root, results = dataset
    found = find_mistakes("sample", driver, root=root, results=results)
    assert found.scored == 0 and found.mistakes == [] and found.coverage.scored == 0
    text = found.summary
    assert text.startswith(f"2026 Sample Grand Prix Qualifying (Synthetic Circuit), {driver}: no")
    assert f"can't say whether {driver} made a mistake" in text and why in text
    assert f"{driver}'s laps in the data: {laps} (laps 1-2)." in text
    assert "No flagged corner looks like a driving mistake" not in text
    assert found.data["scored"] == 0 and found.data["note"] == found.note and why in found.note


def test_find_mistakes_warns_about_missing_telemetry(dataset: tuple[Path, Path]) -> None:
    # The race has 20 laps; the telemetry covers laps 2-6 (and lap 1 is never scored).
    root, results = dataset
    found = find_mistakes("sample", session="race", root=root, results=results)
    assert found.flagged == 0 and found.scored == 10
    text = found.summary
    assert "Laps scored: 2-6 of 20; 10 of the 38 driver-laps" in text
    assert "Only 26% of those have usable telemetry, so few flags here doesn't mean few" in text
    assert found.data["coverage"]["low"] and found.data["coverage"]["laps"] == [2, 3, 4, 5, 6]
    one = find_mistakes("sample", "AAA", session="race", root=root, results=results)
    assert "Laps scored: 2-6 of 20; 5 of AAA's 19 laps" in one.summary


def test_find_mistakes_errors(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    root, results = dataset
    with pytest.raises(ToolInputError, match=r"No driver 'ZZZ'.*AAA \(Team A\)"):
        find_mistakes("sample", "ZZZ", root=root, results=results)
    with pytest.raises(ToolInputError, match="No processed event"):
        find_mistakes("nowhere", root=root, results=results)


def stale_results(results: Path, base: Path) -> list[tuple[Path, str]]:
    """Results directories find_mistakes refuses, each with what the error says: never scored,
    explained from an older run, scored but not explained, and scored again since."""
    missing = base / "missing"
    missing.mkdir()
    older = copy_results(results, base / "older", run_id="2026-01-01T00:00:00+00:00/aaaaaaaaaaaa")
    unexplained = copy_results(results, base / "unexplained")
    (unexplained / MISTAKES).unlink()
    rescored = copy_results(results, base / "rescored")
    provenance.write_parquet(pd.read_parquet(rescored / SCORES), rescored / SCORES, "new/run")
    return [
        (missing, r"No current scoring run.*`race-engineer-infer score`"),
        (older, r"mistakes.parquet was built from another scoring run.*infer explain`"),
        (unexplained, r"mistakes.parquet not found.*`race-engineer-infer explain`"),
        (rescored, r"segment_scores.parquet was built from another scoring run"),
    ]


def test_results_must_come_from_the_current_scoring_run(
    dataset: tuple[Path, Path], tmp_path: Path
) -> None:
    root, results = dataset
    for where, message in stale_results(results, tmp_path):
        # The server's data needs rebuilding, not the request changing (REST answers 503).
        with pytest.raises(
            ResultsUnavailableError, match=f"The M4 results can't be used: {message}"
        ):
            find_mistakes("sample", root=root, results=where)
        # explain_corner still compares the corner, and says why there is no verdict.
        report = explain_corner("sample", "AAA", 10, 3, root=root, results=where)
        assert report.explanation.mistake_type == "early_braking"
        assert report.flagged is None and report.score_pct is None
        assert report.unscored.startswith("No detector verdict for this corner. The M4 results")
        assert report.unscored in report.summary and report.chart["unscored"] == report.unscored
        assert "not an eligible lap" not in report.summary


def test_results_are_read_again_when_they_change(
    dataset: tuple[Path, Path], tmp_path: Path
) -> None:
    # The session's rows are cached between calls, until the files are rebuilt.
    root, results = dataset
    copy = copy_results(results, tmp_path / "results")
    mistakes_tool._load_mistakes.cache_clear()
    mistakes_tool._session_scores.cache_clear()
    first = find_mistakes("sample", root=root, results=copy)
    assert {m.driver for m in first.mistakes} == {"AAA", "BBB"}
    again = find_mistakes("sample", "BBB", root=root, results=copy)
    assert mistakes_tool._load_mistakes.cache_info().hits >= 1
    assert mistakes_tool._session_scores.cache_info().hits >= 1
    assert [m.lap_number for m in again.mistakes] == [4]
    without_aaa = pd.read_parquet(copy / MISTAKES).query("driver != 'AAA'")
    provenance.write_parquet(without_aaa, copy / MISTAKES, RUN_ID)
    assert {m.driver for m in find_mistakes("sample", root=root, results=copy).mistakes} == {"BBB"}


def test_a_session_the_scores_dont_cover(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    # The session was processed after the scoring run: an error, not "nothing flagged".
    root, results = dataset
    without = copy_results(results, tmp_path / "results", lambda f: f["session"] != "Q")
    message = r"don't cover the 2026 Sample Grand Prix Qualifying, although it has 56 corners"
    with pytest.raises(ToolInputError, match=message + ".*`race-engineer-infer score`"):
        find_mistakes("sample", root=root, results=without)
    report = explain_corner("sample", "AAA", 10, 3, root=root, results=without)
    assert report.flagged is None and "don't cover the 2026 Sample" in report.unscored
    assert report.unscored in report.summary and "not an eligible lap" not in report.summary


def test_scores_older_than_the_processed_data(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    # A scored session whose corners on eligible laps have no score: it was processed again.
    root, results = dataset
    without = copy_results(results, tmp_path / "results", lambda f: f["driver"] != "BBB")
    with pytest.raises(ToolInputError, match=r"none of BBB's 4 corners on laps the detectors"):
        find_mistakes("sample", "BBB", root=root, results=without)
    report = explain_corner("sample", "BBB", 2, 3, root=root, results=without)
    assert report.flagged is None
    assert "no score for this corner, although it is on a lap they score" in report.unscored


def test_explain_corner_against_the_usual(dataset: tuple[Path, Path]) -> None:
    root, results = dataset
    report = explain_corner("sample", "AAA", 10, "T3", root=root, results=results)
    exp = report.explanation
    assert exp.mistake_type == "early_braking"
    assert report.flagged is True and report.score_pct == pytest.approx(0.995)
    text = report.summary
    assert text.startswith("2026 Sample Grand Prix Qualifying, AAA (Team A) lap 10, turn 3")
    assert "Compared with AAA's 8 other clean laps at turn 3." in text
    assert "- braking point: -170 m from the apex vs -120 m from the apex usual" in text
    assert "Flagged by the detectors" in text
    turn4 = explain_corner("sample", "AAA", 10, 4, root=root, results=results)
    merged = [r for r in (report, turn4) if "find_mistakes lists them as one mistake" in r.summary]
    assert len(merged) == 1  # the turn not listed points to the one that is
    chart = report.chart
    assert len(chart["distance_m"]) == len(chart["lap"]["speed"]) == len(chart["delta_s"]) == 80
    assert chart["distance_m"][0] == -250.0 and chart["distance_m"][-1] == 145.0
    assert [p["name"] for p in chart["phases"]] == ["entry", "apex", "exit"]
    total = sum(p["loss_s"] for p in chart["phases"])
    assert total == pytest.approx(chart["time_lost_s"], abs=0.02)  # each to 0.01 s, as shown
    braking = next(d for d in chart["deviations"] if d["feature"] == "brake_start_d")
    assert braking["evidence"] and braking["delta"] == pytest.approx(-50.0, abs=3)
    # The chart's extras (test_tools_mistake_charts has the details).
    assert chart["apex_m"] == 3000.0 and chart["ghost"]["driver"] == "AAA"
    assert len(chart["usual_lo"]["speed"]) == len(chart["offset"]["lap"]) == 80
    assert chart["map"] is not None and chart["replay"] is not None


def test_explain_corner_works_on_any_lap(dataset: tuple[Path, Path]) -> None:
    root, results = dataset
    report = explain_corner("sample", "AAA", 5, 3, root=root, results=results)
    assert report.flagged is False and report.unscored == ""
    assert "Not flagged by the detectors" in report.summary
    assert report.explanation.mistake_type == "no_time_lost"
    # An ordinary corner the detectors didn't flag isn't "unusual".
    assert "Type: not clearly slower than usual." in report.summary
    assert "unusual" not in report.summary.split("Not flagged")[0]
    assert report.chart["type_text"] == "not clearly slower than usual"
    flagged = explain_corner("sample", "AAA", 3, 3, root=root, results=results)
    assert flagged.flagged is True and flagged.explanation.mistake_type == "no_time_lost"
    assert "Type: unusual but not clearly slower." in flagged.summary


def test_explain_corner_on_a_lap_the_detectors_dont_score(dataset: tuple[Path, Path]) -> None:
    # CCC's crash lap has no lap time; CCC has no clean lap, so the field is the reference.
    root, results = dataset
    report = explain_corner("sample", "CCC", 2, 3, root=root, results=results)
    assert report.flagged is None and report.score_pct is None
    text = report.summary
    assert text.startswith("2026 Sample Grand Prix Qualifying, CCC (Team C) lap 2, turn 3 (a lap")
    assert "(a lap without a lap time)." in text and "classed no_time)" not in text
    assert report.unscored.startswith(
        "The detectors didn't score this corner (a lap without a lap time): they score only push"
    )
    assert report.unscored in text
    assert "(CCC had no other clean lap there)" in text and "only 0" not in text
    assert "Type: not clearly slower than usual." in text


@pytest.mark.parametrize(
    ("corner", "expected"),
    [(7, (7, None)), ("7", (7, None)), ("T9a", (9, "a")), ("turn 12", (12, None))],
)
def test_parse_turn(corner: int | str, expected: tuple[int, str | None]) -> None:
    assert parse_turn(corner) == expected


def test_explain_corner_turns_and_laps(dataset: tuple[Path, Path]) -> None:
    root, results = dataset
    plain = explain_corner("sample", "AAA", 2, "5", root=root, results=results)
    assert plain.turn == "5"  # the turn without a letter, not 5A
    assert explain_corner("sample", "AAA", 2, "5a", root=root, results=results).turn == "5A"
    with pytest.raises(ToolInputError, match=r"Turn 7 has several parts.*7A, 7B"):
        explain_corner("sample", "AAA", 2, 7, root=root, results=results)
    with pytest.raises(ToolInputError, match=r"No turn 99.*Turns: 3, 4, 5, 5A, 7A, 7B"):
        explain_corner("sample", "AAA", 2, 99, root=root, results=results)
    with pytest.raises(ToolInputError, match=r"no usable telemetry for lap 30.*Laps with data"):
        explain_corner("sample", "AAA", 30, 3, root=root, results=results)
    with pytest.raises(ToolInputError, match="Can't read turn"):
        explain_corner("sample", "AAA", 2, "hairpin", root=root, results=results)


def traffic_dataset(base: Path, lift_mps: float = 25.0) -> tuple[Path, Path]:
    """A race where BBB, a lap ahead, laps AAA at turn 3 of lap 9 (1 s behind 450 m before the
    apex, see test_inference_traffic.lapped_laps) and AAA brakes early there on lap 9 and, with
    nobody near, on lap 10: the first flag is traffic, the second a mistake. On lap 9 AAA lifts
    to `lift_mps` from 2600 m to 3200 m (the apex is at 3000 m): at 25 m/s BBB is 7.9 s up the
    road by the apex, at 40 m/s still within NEARBY_S, so on the chart's map too."""
    root, results = base / "processed", base / "results"
    numbers = {"AAA": ("1", "Team A"), "BBB": ("2", "Team B")}
    laps = tuple(
        SyntheticLap(lap.driver, lap.lap, lap.v * 3.6, *numbers[lap.driver], "race",
                     start_s=lap.start_s)
        for lap in lapped_laps(1.0, slow=(2600.0, 3200.0, lift_mps))
    )  # fmt: skip
    session = SyntheticSession(2026, 2, "R", "Traffic Grand Prix", "Traffic Circuit", laps=laps)
    write_session(session, root)
    specs = [*usual_laps(), lap_spec("AAA", 9, brake_d=-170.0), lap_spec("AAA", 10, brake_d=-170.0)]
    table, traces = build_table(specs, round_=2, code="R")
    write_segments(table, traces, root)
    results.mkdir(parents=True, exist_ok=True)
    (results / provenance.MANIFEST).write_text(json.dumps({"run_id": RUN_ID}))
    flagged = ["2026_02_R_1_9_T3", "2026_02_R_1_10_T3"]
    provenance.write_parquet(scores_frame(table, flagged), results / SCORES, RUN_ID)
    mistakes = run_explain(results / SCORES, results / MISTAKES, None, root)
    provenance.write_parquet(mistakes, results / MISTAKES, RUN_ID)
    return root, results


def test_traffic_is_left_out_and_explained(tmp_path: Path) -> None:
    root, results = traffic_dataset(tmp_path)
    found = find_mistakes("traffic", session="race", root=root, results=results)
    assert [(m.lap_number, m.mistake_type) for m in found.mistakes] == [(10, "early_braking")]
    assert found.left_out == {"lapped": 1}
    assert "Flagged but not listed as mistakes: 1 being lapped (letting a faster car by)." in (
        found.summary
    )
    report = explain_corner("traffic", "AAA", 9, 3, session="race", root=root, results=results)
    exp = report.explanation
    assert exp.mistake_type == "lapped" and "being lapped: BBB (a lap ahead)" in exp.sentence
    assert (
        "Cars around (the gap 450 m before the apex / the closest it came before the loss "
        "began, 95 m before the apex; + behind, - ahead): BBB +1.0 s / past by then (a lap "
        "ahead), went past before the corner." in report.summary
    )
    assert report.chart["traffic"]["loss_onset_m"] == -95.0
    assert report.chart["traffic"]["alongside_before_loss"] == ["BBB"]
    # The ranking line says that traffic doesn't count.
    assert "none of it when other cars explain it: being lapped, racing for a place or held up" in (
        found.summary
    )
    assert report.chart["traffic"]["type"] == "lapped"
    assert report.chart["traffic"]["around"][0]["driver"] == "BBB"
    # The batch and the live answer agree, word for word.
    batch = pd.read_parquet(results / MISTAKES).set_index("segment_id")
    assert batch.loc["2026_02_R_1_9_T3", "explanation"] == exp.sentence
    assert batch.loc["2026_02_R_1_9_T3", "traffic_cars"] == "BBB"
    assert report.chart["map"] is not None and report.chart["map"]["cars"] == []  # 7.9 s ahead


def test_a_lapping_car_close_at_the_apex_is_on_the_map(tmp_path: Path) -> None:
    # The dev host's explain-corner-traffic fixture: BBB is still 1.8 s up the road when AAA
    # reaches the apex, so the map draws it, a lap ahead.
    root, results = traffic_dataset(tmp_path, lift_mps=40.0)
    report = explain_corner("traffic", "AAA", 9, 3, session="race", root=root, results=results)
    assert report.explanation.mistake_type == "lapped"
    assert report.chart["map"] is not None
    (car,) = report.chart["map"]["cars"]
    assert (car["driver"], car["laps"]) == ("BBB", 1) and -NEARBY_S < car["gap_s"] < 0
