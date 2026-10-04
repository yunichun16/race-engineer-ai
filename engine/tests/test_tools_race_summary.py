"""get_race_summary: each timing rule on small hand-made lap tables, then the whole tool on the
synthetic sample race (tools.synthetic.sample_race_session), whose story is known:

AAA wins (1 stop, lap 9, SOFT-HARD) by 11.7 s from BBB (2 stops, laps 6 and 12, SOFT-MEDIUM-
SOFT), passing BBB on lap 15; CCC is lapped once; DDD stops on track on lap 13 after 12 laps.
Safety car on laps 5-7, VSC on lap 12, yellow flags on lap 3. AAA's lap 19 is the quickest but
deleted, so the fastest lap is his lap 18 (89.5 s).
"""

import json
import math
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from race_engineer.data import store
from race_engineer.tools.race_summary import (
    COLUMNS,
    DATA_NOTE,
    NOT_A_RACE,
    RACE_SUMMARY,
    fastest_lap,
    finishing_order,
    lap_ends,
    leader_laps,
    neutralisations,
    pit_stops,
    position_matrix,
    race_summary,
    stints,
    typical_lap_s,
    yellow_laps,
)
from race_engineer.tools.registry import DataPaths, run_tool
from race_engineer.tools.sessions import ToolInputError
from race_engineer.tools.synthetic import (
    SyntheticSession,
    sample_comparison_session,
    sample_race_session,
    write_session,
)

NAN = math.nan


def car(
    driver: str,
    times: Sequence[float],
    positions: Sequence[float] | None = None,
    start: float = 0.0,
    **columns: list,
) -> pd.DataFrame:
    """One car's laps: lap times in order (NaN for a lap without one), each lap starting when
    the previous ended, as in FastF1."""
    starts = start + np.concatenate([[0.0], np.cumsum(np.nan_to_num(times[:-1]))])
    n = len(times)
    frame = pd.DataFrame(
        {
            "driver": driver,
            "driver_number": str(ord(driver[0]) - 64),
            "team": f"Team {driver[0]}",
            "lap_number": np.arange(1, n + 1),
            "position": positions if positions is not None else [NAN] * n,
            "track_status": ["1"] * n,
            "pit_in": [False] * n,
            "pit_out": [False] * n,
            "stint": [1.0] * n,
            "compound": ["MEDIUM"] * n,
            "lap_class": ["race"] * n,
            "lap_time_s": times,
            "lap_start_s": starts,
            "deleted": [False] * n,
        }
    )
    for name, values in columns.items():
        frame[name] = values
    return frame[COLUMNS]


def table(*cars: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(cars, ignore_index=True).sort_values(["driver", "lap_number"])


def by_driver(laps: pd.DataFrame) -> dict:
    return {f.driver: f for f in finishing_order(laps)}


# ---------------------------------------------------------------------------------------------
# Timing rules


def test_a_lap_without_a_time_ends_when_the_next_one_starts() -> None:
    laps = table(car("AAA", [100.0, NAN, 100.0]))
    laps.loc[laps["lap_number"] == 3, "lap_start_s"] = 205.0  # the untimed lap took 105 s
    assert lap_ends(laps).tolist() == [100.0, 205.0, 305.0]


def test_typical_lap_falls_back_to_the_lap_before() -> None:
    laps = table(car("AAA", [100.0, 90.0, NAN]), car("BBB", [101.0, 92.0, NAN]))
    assert typical_lap_s(laps, 2) == pytest.approx(91.0)
    assert typical_lap_s(laps, 3) == pytest.approx(91.0)  # nobody timed lap 3


def test_winner_lapped_and_stopped_cars() -> None:
    laps = table(
        car("AAA", [100.0] * 4, [1, 1, 1, 1]),
        car("BBB", [100.5] * 4, [2, 2, 2, 2]),
        car("CCC", [130.0] * 3, [3, 3, 3]),  # lap 3 ends at 390, before the winner's 400
        car("DDD", [140.0] * 3, [4, 4, 4]),  # lap 3 ends at 420, after the winner finished
    )
    found = by_driver(laps)
    assert [f.driver for f in finishing_order(laps)] == ["AAA", "BBB", "DDD", "CCC"]
    assert found["AAA"].status == "finished" and found["AAA"].gap_s == 0.0
    assert found["BBB"].status == "finished" and found["BBB"].gap_s == pytest.approx(2.0)
    assert found["DDD"].status == "lapped" and found["DDD"].laps_down == 1
    assert found["DDD"].position == 3 and found["DDD"].gap_s is None
    # CCC's last crossing (390 s) came before the winner finished (400 s): it stopped.
    assert found["CCC"].status == "stopped" and found["CCC"].position is None
    assert found["CCC"].laps == 3


def test_a_last_lap_cut_off_by_the_feed_still_counts() -> None:
    """Monza 2023: the live feed ended before the back of the field crossed the line, so their
    last lap has neither a time nor a position."""
    laps = table(
        car("AAA", [100.0, 100.0, 100.0], [1, 1, 1]),
        car("BBB", [101.0, 101.0, NAN], [2, 2, NAN]),  # lap 3 starts at 202, before 300
    )
    found = by_driver(laps)
    assert found["BBB"].status == "finished" and found["BBB"].laps == 3
    assert found["BBB"].untimed_finish and not found["BBB"].timed
    assert found["BBB"].gap_s is None and found["BBB"].position == 2


def test_a_last_lap_with_later_crossings_timed_is_a_stop() -> None:
    """Bahrain 2022: a car stopped on the last lap while the cars behind it were timed
    crossing the line, so the feed was still running."""
    laps = table(
        car("AAA", [100.0, 100.0, 100.0], [1, 1, 1]),
        car("BBB", [101.0, 101.0, NAN], [2, 2, NAN]),
        car("CCC", [103.0, 103.0, 103.0], [3, 3, 2]),  # crosses at 309, after BBB's ~302
    )
    found = by_driver(laps)
    assert found["BBB"].status == "stopped" and found["BBB"].laps == 2
    assert found["CCC"].position == 2


def test_last_laps_placed_but_not_timed_keep_the_timing_order() -> None:
    """Baku 2024: the race finished under the VSC and the last lap has positions but no
    times; the positions give the order and no gap is given."""
    laps = table(
        car("AAA", [100.0, 100.0, NAN], [1, 2, 2]),
        car("BBB", [99.0, 100.0, NAN], [2, 1, 1]),
    )
    order = finishing_order(laps)
    assert [f.driver for f in order] == ["BBB", "AAA"]
    assert all(f.status == "finished" and f.laps == 3 for f in order)
    assert all(f.gap_s is None and not f.timed for f in order)


def test_a_last_lap_into_the_pits_is_a_retirement() -> None:
    """Miami 2022: FastF1 gives the lap into the pits a position, but it has no time."""
    laps = table(
        car("AAA", [100.0, 100.0, 100.0], [1, 1, 1]),
        car("BBB", [101.0, 101.0, NAN], [2, 2, 2], pit_in=[False, False, True]),
    )
    found = by_driver(laps)
    assert found["BBB"].status == "stopped" and found["BBB"].laps == 2


def test_a_car_out_on_lap_one_completed_no_laps() -> None:
    laps = table(car("AAA", [100.0, 100.0], [1, 1]), car("BBB", [NAN], [NAN]))
    found = by_driver(laps)
    assert found["BBB"].status == "stopped" and found["BBB"].laps == 0


def test_positions_per_lap_leave_gaps_where_missing() -> None:
    laps = table(car("AAA", [100.0, 100.0, 100.0], [1, NAN, 1]), car("BBB", [NAN], [NAN]))
    positions = position_matrix(laps, 3)
    assert positions == {"AAA": [1, None, 1], "BBB": [None, None, None]}


def test_neutralisations_follow_the_leader() -> None:
    status = ["1", "14", "4", "41", "1", "6", "7", "1", "45", "1", "12", "1"]
    lead = car("AAA", [100.0] * 12, [1] * 12, track_status=status)
    # The second car saw a VSC on lap 10 that the leader didn't: it doesn't count.
    other = car("BBB", [101.0] * 12, [2] * 12, track_status=["1"] * 9 + ["6", "1", "1"])
    laps = table(lead, other)
    assert neutralisations(laps) == [
        {"kind": "SC", "from_lap": 2, "to_lap": 4},
        {"kind": "VSC", "from_lap": 6, "to_lap": 7},
        {"kind": "SC", "from_lap": 9, "to_lap": 9},
        {"kind": "RED", "from_lap": 9, "to_lap": 9},
    ]
    assert yellow_laps(laps) == 1


def test_leader_without_positions_is_the_first_to_start_the_lap() -> None:
    laps = table(car("AAA", [100.0, 100.0], start=0.5), car("BBB", [100.0, 100.0]))
    assert leader_laps(laps)["driver"].tolist() == ["BBB", "BBB"]


def test_pit_stops_leave_out_red_flags_and_retirements() -> None:
    laps = table(
        car(
            "AAA",
            [100.0] * 6,
            pit_in=[False, True, False, True, False, True],
            track_status=["1", "1", "1", "45", "1", "1"],
        )
    )
    assert pit_stops(laps) == {"AAA": [2]}  # lap 4: red flag; lap 6: into the pits to retire


def test_stints_take_the_most_common_compound() -> None:
    laps = table(
        car(
            "AAA",
            [100.0] * 6,
            stint=[1.0, 1.0, NAN, 2.0, 2.0, 3.0],
            compound=["SOFT", "UNKNOWN", "SOFT", "HARD", "HARD", None],
        )
    )
    assert stints(laps) == {
        "AAA": [
            {"compound": "SOFT", "from_lap": 1, "to_lap": 3},
            {"compound": "HARD", "from_lap": 4, "to_lap": 5},
            {"compound": "UNKNOWN", "from_lap": 6, "to_lap": 6},
        ]
    }


def test_fastest_lap_counts_yellow_laps_but_not_deleted_or_neutralised_ones() -> None:
    laps = table(
        car(
            "AAA",
            [90.0, 85.0, 88.0, 89.0],
            lap_class=["start", "neutralised", "race", "yellow"],
        ),
        car("BBB", [91.0, 87.5, 87.9, 88.5], lap_class=["start", "race", "yellow", "race"]),
    )
    laps.loc[(laps["driver"] == "BBB") & (laps["lap_number"] == 2), "deleted"] = True
    assert fastest_lap(laps) == {"driver": "BBB", "lap": 3, "time_s": 87.9}


# ---------------------------------------------------------------------------------------------
# The tool on the sample race


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("processed")
    race = sample_race_session()
    write_session(race, root)
    write_session(replace(race, code="S", date="2026-03-07"), root)  # the same race as a sprint
    write_session(sample_comparison_session(), root)  # qualifying
    return root


def test_sample_race_order_and_statuses(root: Path) -> None:
    found = race_summary("sample", root=root)
    assert [(f.driver, f.status, f.position, f.laps) for f in found.order] == [
        ("AAA", "finished", 1, 20),
        ("BBB", "finished", 2, 20),
        ("CCC", "lapped", 3, 19),
        ("DDD", "stopped", None, 12),
    ]
    assert found.order[1].gap_s == pytest.approx(11.7)
    assert found.session.code == "R"


def test_sample_race_data_follows_the_chart_contract(root: Path) -> None:
    data = race_summary("Sample Grand Prix", 2026, "race", root=root).data
    assert set(data) == {
        "event",
        "location",
        "year",
        "round",
        "session",
        "session_code",
        "date",
        "laps",
        "focus",
        "drivers",
        "neutralisations",
        "fastest_lap",
        "notes",
    }
    assert (data["session_code"], data["laps"], data["focus"]) == ("R", 20, None)
    aaa, bbb, ccc, ddd = data["drivers"]
    assert set(aaa) == {
        "code",
        "team",
        "finish",
        "status",
        "laps_completed",
        "laps_down",
        "gap_s",
        "start_pos",
        "positions",
        "pit_laps",
        "stints",
        "best_lap_s",
    }
    assert all(len(d["positions"]) == 20 for d in data["drivers"])
    assert aaa["positions"][13:15] == [2, 1]  # passes BBB on lap 15
    assert (aaa["start_pos"], bbb["start_pos"]) == (2, 1)
    assert (aaa["pit_laps"], bbb["pit_laps"], ccc["pit_laps"]) == ([9], [6, 12], [])
    assert [s["compound"] for s in bbb["stints"]] == ["SOFT", "MEDIUM", "SOFT"]
    assert aaa["stints"][1] == {"compound": "HARD", "from_lap": 10, "to_lap": 20}
    assert ccc["positions"][-1] is None and ccc["laps_down"] == 1
    assert ddd["finish"] is None and ddd["positions"][12:] == [None] * 8
    assert data["neutralisations"] == [
        {"kind": "SC", "from_lap": 5, "to_lap": 7},
        {"kind": "VSC", "from_lap": 12, "to_lap": 12},
    ]
    assert data["fastest_lap"] == {"driver": "AAA", "lap": 18, "time_s": 89.5}
    assert data["notes"] == [DATA_NOTE]
    assert len(json.dumps(data)) < 4000


def test_sample_race_summary_text(root: Path) -> None:
    summary = race_summary("sample", root=root).summary
    assert summary.startswith("2026 Sample Grand Prix, Race (R), 2026-03-08, Synthetic Circuit")
    assert "Won by AAA (Sample Racing) by 11.700 s from BBB" in summary
    assert "1 AAA; 2 BBB +11.7 s; 3 CCC +1 lap." in summary
    assert "Stopped (cause not in the data): DDD after 12 laps." in summary
    assert "Safety car: laps 5-7. VSC: lap 12. Red flag: none. Yellow flags on 1 lap." in summary
    assert "AAA 1 (lap 9; S-H), BBB 2 (laps 6, 12; S-M-S)" in summary
    assert "Fastest lap: AAA 1:29.500 (lap 18)." in summary
    assert "during a red flag" not in summary
    assert summary.endswith(DATA_NOTE)
    assert len(summary) < 2500


def test_red_flag_tyre_change_is_not_a_stop(tmp_path: Path) -> None:
    """Lap 12 red-flagged instead of under the VSC: BBB's tyre change in the pit lane then is
    a new stint but not a stop, and the summary says so."""
    race = sample_race_session()
    laps = [replace(lap, track_status="5") if lap.lap_number == 12 else lap for lap in race.laps]
    write_session(replace(race, laps=tuple(laps)), tmp_path)
    found = race_summary("sample", root=tmp_path)
    bbb = found.data["drivers"][1]
    assert bbb["pit_laps"] == [6] and len(bbb["stints"]) == 3
    assert {"kind": "RED", "from_lap": 12, "to_lap": 12} in found.data["neutralisations"]
    assert "BBB 1 (lap 6; S-M-S)" in found.summary
    assert "Tyre changes during a red flag aren't counted as stops." in found.summary


def test_driver_focus(root: Path) -> None:
    found = race_summary("sample", driver="aaa", root=root)
    assert found.data["focus"] == "AAA"
    assert (
        "AAA (Sample Racing): P2 at the end of lap 1, finished P1 (the winner), 1 place gained. "
        "1 stop (lap 9); tyres SOFT to HARD. Best lap 1:29.500 (lap 18), the fastest lap of the "
        "race. 4 laps behind the safety car or under the VSC. Track limits: 1 violation noted "
        "(1 lap time deleted)." in found.summary
    )
    ddd = race_summary("sample", driver="4", root=root).summary  # by car number
    assert "DDD (Sample Racing): P3 at the end of lap 1, stopped after 12 laps, running P1" in ddd
    assert "Race control noted incidents involving DDD: lap 13 turn 4." in ddd
    bbb = race_summary("sample", driver="BBB", root=root).summary
    assert "finished P2 (+11.7 s), 1 place lost" in bbb
    assert "Track limits: 1 violation noted (no lap time deleted)." in bbb


def test_empty_driver_means_the_whole_field(root: Path) -> None:
    found = race_summary("sample", driver="", root=root)
    assert found.data["focus"] is None


def test_unknown_driver_lists_the_roster(root: Path) -> None:
    with pytest.raises(ToolInputError, match=re.escape("Drivers: AAA (Sample Racing), BBB")):
        race_summary("sample", driver="XYZ", root=root)


@pytest.mark.parametrize(
    ("event", "session"),
    [("sample", "Q"), ("sample", "qualifying"), ("sample quali", None), ("sample", "SQ")],
)
def test_qualifying_is_refused(root: Path, event: str, session: str | None) -> None:
    with pytest.raises(ToolInputError, match=re.escape(NOT_A_RACE)):
        race_summary(event, session=session, root=root)


@pytest.mark.parametrize(
    ("event", "session", "code"),
    [
        ("sample", None, "R"),
        ("sample race", None, "R"),
        ("sample", "sprint", "S"),
        ("sample", "S", "S"),
        ("sample sprint", None, "S"),
    ],
)
def test_race_by_default_and_sprints(
    root: Path, event: str, session: str | None, code: str
) -> None:
    assert race_summary(event, session=session, root=root).data["session_code"] == code


def test_missing_position_and_untimed_lap(tmp_path: Path) -> None:
    """A lap FastF1 left without a position and a lap without a time: the chart gets a gap,
    the note says so, and the order is unchanged."""
    race = sample_race_session()
    laps = [
        replace(lap, position=math.nan)
        if (lap.driver, lap.lap_number) == ("BBB", 10)
        else replace(lap, lap_class="no_time")
        if (lap.driver, lap.lap_number) == ("CCC", 8)
        else lap
        for lap in race.laps
    ]
    write_session(replace(race, laps=tuple(laps)), tmp_path)
    stored = pd.read_parquet(store.table_path("laps", race.key, tmp_path))
    assert stored["lap_time_s"].isna().sum() == 2  # DDD's last lap and CCC's lap 8
    found = race_summary("sample", root=tmp_path)
    assert [f.driver for f in found.order] == ["AAA", "BBB", "CCC", "DDD"]
    bbb = found.data["drivers"][1]
    assert bbb["positions"][9] is None and bbb["positions"][10] == 2
    assert "1 timed lap without a position" in found.summary
    assert found.data["drivers"][2]["laps_completed"] == 19


def test_through_the_registry(root: Path, tmp_path: Path) -> None:
    out = run_tool(RACE_SUMMARY, {"event": "sample"}, DataPaths(root, tmp_path))
    assert out.data is not None and out.data["drivers"][0]["code"] == "AAA"
    assert RACE_SUMMARY.chart == "race-summary"
    assert list(RACE_SUMMARY.input_schema()["properties"]) == ["event", "year", "session", "driver"]
    with pytest.raises(ToolInputError, match="unknown parameter"):
        run_tool(RACE_SUMMARY, {"event": "sample", "lap": 3}, DataPaths(root, tmp_path))


def test_session_without_laps(tmp_path: Path) -> None:
    write_session(SyntheticSession(2026, 3, "R", "Empty Grand Prix", "Nowhere"), tmp_path)
    with pytest.raises(ToolInputError, match="No lap data stored"):
        race_summary("empty", root=tmp_path)
