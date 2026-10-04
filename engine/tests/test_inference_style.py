import json
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from race_engineer.evaluation import data as eval_data
from race_engineer.inference import style
from race_engineer.inference.provenance import MANIFEST, StaleResultsError, write_parquet
from race_engineer.inference.style import (
    METRIC_NAMES,
    PICKUP_CENSORED_M,
    build_profiles,
    build_style,
    chance_rates,
    clean_segments,
    compare_drivers,
    comparison_summary,
    consistency_test,
    corner_medians,
    corner_types,
    embedding_profiles,
    entry_decel,
    event_differences,
    matched_attempts,
    normalise_compound,
    pace_check,
    quali_gaps,
    style_axes,
    summarise_differences,
    teammate_pairs,
    write_report,
    write_tables,
)
from race_engineer.models.tensors import cache_key
from race_engineer.tools.sessions import ToolInputError

YEAR = 2025
ROUNDS = range(1, 7)
# corner, apex (m), minimum speed, where it is relative to the apex, speed at the apex. Corner 4
# is 80 m after corner 3, so its window's slowest point is corner 3's: it doesn't own it.
CORNERS = [
    (1, 500.0, 90.0, 0.0, 92.0),  # slow
    (2, 1500.0, 230.0, 10.0, 232.0),  # fast
    (3, 2500.0, 150.0, -5.0, 152.0),  # medium
    (4, 2580.0, 150.0, -85.0, 175.0),  # medium, by its speed at the marker
]
# Red: AAA brakes 5 m later than BBB and carries 1 km/h more minimum speed. Blue: DDD drives
# rounds 1-3 and EEE rounds 4-6 alongside CCC; EEE coasts 4 m more than CCC.
LINEUP = {"Red": ("AAA", "BBB"), "Blue": ("CCC", "DDD")}
EFFECTS = {"AAA": {"brake_start_d": 5.0, "v_min": 1.0}, "EEE": {"coast_m": 4.0}}


def driver_of(team: str, seat: int, round_: int) -> str:
    driver = LINEUP[team][seat]
    return "EEE" if driver == "DDD" and round_ > 3 else driver


def lap_context(session: str, lap: int) -> dict:
    if session == "Q":  # an out lap, three push laps, a deleted lap; no traffic filter here
        return {
            "lap_class": "out" if lap == 1 else "push",
            "deleted": lap == 5,
            "gap_ahead_s": 0.3,
            "compound": "SOFT",
        }
    return {"lap_class": "race", "deleted": False, "gap_ahead_s": 0.4 if lap == 2 else 3.0,
            "compound": "MEDIUM" if lap <= 4 else "HARD"}  # fmt: skip


def synthetic_frame(seed: int = 0) -> pd.DataFrame:
    """Eval-frame-like segments at six events: qualifying (an out lap, three push laps and a
    deleted lap per driver) and a race (eight laps, the second in traffic)."""
    rng = np.random.default_rng(seed)
    rows = []
    sessions = (("Q", 5), ("R", 8))
    for round_, (session, laps), team, seat in product(ROUNDS, sessions, LINEUP, (0, 1)):
        driver = driver_of(team, seat, round_)
        for lap, (corner, apex, v_min, d_vmin, v_apex) in product(range(1, laps + 1), CORNERS):
            values = {
                "brake_start_d": -100.0 + rng.normal(0, 2),
                "decel_entry": 30.0 + rng.normal(0, 0.5),
                "v_min": v_min + rng.normal(0, 0.5),
                "throttle_pickup_d": 40.0 + rng.normal(0, 3),
                "coast_m": 20.0 + rng.normal(0, 2),
                "v_exit": 200.0 + rng.normal(0, 1),
                "segment_time_s": 8.0 + rng.normal(0, 0.02),
            }
            for name, shift in EFFECTS.get(driver, {}).items():
                values[name] += shift
            rows.append(
                {
                    "segment_id": f"{round_}_{session}_{driver}_{lap}_T{corner}",
                    "year": YEAR, "round": round_, "session": session, "event": f"Event {round_}",
                    "driver": driver, "team": team, "lap_number": lap, "corner": corner,
                    "corner_letter": "",
                    "apex_m": apex, "d_vmin": d_vmin, "v_apex": v_apex,
                    **lap_context(session, lap), **values,
                }
            )  # fmt: skip
    frame = pd.DataFrame(rows)
    # the feed's ways of not knowing a tyre: these laps can't be paired
    unknown = (frame["driver"] == "CCC") & (frame["session"] == "R") & (frame["round"] == 1)
    frame.loc[unknown, "compound"] = np.resize(
        np.array(["None", "nan", None], object), unknown.sum()
    )
    return frame


def synthetic_embeddings(clean: pd.DataFrame, seed: int = 0, dim: int = 8) -> np.ndarray:
    """A strong team signature, a corner signature, AAA's own offset from BBB, and noise."""
    rng = np.random.default_rng(seed)
    corner = {c: rng.normal(0, 1, dim) for c in clean["corner"].unique()}
    team = {t: rng.normal(0, 2, dim) for t in clean["team"].unique()}
    driver = {d: np.zeros(dim) for d in clean["driver"].unique()}
    driver["AAA"] = rng.normal(0, 0.4, dim)
    x = np.stack(
        [
            corner[c] + team[t] + driver[d]
            for c, t, d in zip(clean["corner"], clean["team"], clean["driver"], strict=True)
        ]
    )
    return (x + rng.normal(0, 0.3, x.shape)).astype(np.float32)


def one(table: pd.DataFrame, **match: object) -> dict:
    """The single row of `table` matching every column=value, as a dict."""
    rows = table
    for column, value in match.items():
        rows = rows[rows[column] == value]
    assert len(rows) == 1, f"{len(rows)} rows match {match}"
    return rows.to_dict("records")[0]


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return synthetic_frame()


@pytest.fixture(scope="module")
def corners(frame: pd.DataFrame) -> pd.DataFrame:
    clean = clean_segments(frame)
    return corner_medians(clean, corner_types(clean))


def test_clean_segments_keeps_clean_laps_in_free_air(frame: pd.DataFrame) -> None:
    clean = clean_segments(frame)
    assert set(clean["lap_class"]) == {"push", "race"}
    assert not clean["deleted"].any()
    race = clean[clean["session"] == "R"]
    assert (race["gap_ahead_s"] >= 1.0).all()
    quali = clean[clean["session"] == "Q"]
    assert (quali["gap_ahead_s"] < 1.0).all()  # the traffic filter is for races only
    assert set(clean["compound"]) == {"SOFT", "MEDIUM", "HARD"}  # unknown tyres left out
    assert set(clean["kind"]) == {"quali", "race"}
    # per driver and event: 3 push laps + 7 free-air race laps, 4 corners each, less CCC's
    # round-1 race laps on unknown tyres
    assert len(clean) == 6 * 4 * (3 + 7) * 4 - 7 * 4


def test_normalise_compound() -> None:
    raw = pd.Series(["SOFT", "None", "nan", None, "UNKNOWN", " medium ", ""], dtype=object)
    assert normalise_compound(raw).tolist() == [
        "SOFT", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "MEDIUM", "UNKNOWN",
    ]  # fmt: skip


def test_matched_attempts_cut_quali_laps_to_the_teammates_count() -> None:
    # AAA did five push laps (the third deleted: still an attempt), BBB two; two corners a lap
    laps = [("AAA", n) for n in (3, 5, 8, 12, 15)] + [("BBB", n) for n in (4, 6)]
    frame = pd.DataFrame(
        [
            {"year": YEAR, "round": 1, "session": "Q", "team": "Red", "driver": driver,
             "lap_number": lap, "lap_class": "push", "corner": corner}
            for driver, lap in laps
            for corner in (1, 2)
        ]
        + [{"year": YEAR, "round": 1, "session": "R", "team": "Red", "driver": "AAA",
            "lap_number": 30, "lap_class": "race", "corner": 1}]
    )  # fmt: skip
    keep = matched_attempts(frame)
    kept = frame[keep]
    assert sorted(set(kept.loc[kept["driver"] == "AAA", "lap_number"])) == [3, 5, 30]
    assert sorted(set(kept.loc[kept["driver"] == "BBB", "lap_number"])) == [4, 6]
    assert keep.groupby([frame["driver"], frame["lap_number"]]).nunique().eq(1).all()


def test_corner_types_find_who_owns_the_slowest_point(frame: pd.DataFrame) -> None:
    types = corner_types(clean_segments(frame))
    first = types[types["round"] == 1].set_index("corner")
    assert first["owns_min"].tolist() == [True, True, True, False]
    assert first["corner_type"].tolist() == ["slow", "fast", "medium", "medium"]
    assert one(types, round=1, corner=4)["corner_kph"] == pytest.approx(175.0)  # at its marker


def test_corner_medians_leave_borrowed_metrics_empty(corners: pd.DataFrame) -> None:
    unowned = corners[corners["corner"] == 4]
    for name in ("brake_start_d", "decel_entry", "v_min", "throttle_pickup_d"):
        assert unowned[name].isna().all()
    for name in ("coast_m", "v_exit", "segment_time_s"):
        assert unowned[name].notna().all()
    assert not corners.duplicated(
        ["year", "round", "session", "corner", "compound", "driver"]
    ).any()
    quali = corners[(corners["session"] == "Q") & (corners["driver"] == "AAA")]
    assert (quali["laps"] == 3).all()
    assert (corners.loc[corners["corner"] == 4, "own_laps"] == 0).all()


def test_corner_medians_use_laps_that_turn_at_the_corner() -> None:
    # One owned slow corner. AAA's third race lap has its slowest point 120 m early (at the
    # corner before) and its fourth a 150 m/s² deceleration spike; one lap never gets back to
    # full throttle in the window. BBB misses full throttle on two of four laps.
    base = {
        "year": YEAR,
        "round": 1,
        "session": "R",
        "event": "E",
        "team": "Red",
        "corner": 1,
        "corner_letter": "",
        "apex_m": 500.0,
        "compound": "HARD",
        "coast_m": 10.0,
        "v_exit": 200.0,
        "segment_time_s": 8.0,
        "v_apex": 91.0,
    }
    aaa = {
        "d_vmin": [0.0, 5.0, -120.0, 0.0, -5.0],
        "brake_start_d": [-100.0, -102.0, -200.0, -101.0, -99.0],
        "decel_entry": [30.0, 31.0, 29.0, 150.0, 32.0],
        "v_min": [90.0, 91.0, 150.0, 89.0, 90.0],
        "throttle_pickup_d": [40.0, np.nan, -250.0, 42.0, 44.0],
    }
    bbb = {
        "d_vmin": [0.0] * 4, "brake_start_d": [-100.0] * 4, "decel_entry": [30.0] * 4,
        "v_min": [90.0] * 4, "throttle_pickup_d": [40.0, np.nan, np.nan, 42.0],
    }  # fmt: skip
    laps = pd.concat(
        [pd.DataFrame(aaa).assign(driver="AAA"), pd.DataFrame(bbb).assign(driver="BBB")]
    ).assign(**base)
    types = corner_types(laps)
    assert types["owns_min"].all() and types["corner_type"].iloc[0] == "slow"
    medians = corner_medians(laps, types)
    a, b = one(medians, driver="AAA"), one(medians, driver="BBB")
    assert a["laps"] == 5 and a["own_laps"] == 4  # the early-minimum lap is left out
    assert a["brake_start_d"] == pytest.approx(-100.5)
    assert a["v_min"] == pytest.approx(90.0)
    assert a["decel_entry"] == pytest.approx(31.0)  # 30, 31, 32: the spike is left out
    # 40, 42, 44 and one lap later than the window: the median is 43, not 42
    assert a["throttle_pickup_d"] == pytest.approx(43.0)
    assert a["pickup_censored"] == pytest.approx(0.25)
    assert a["coast_m"] == pytest.approx(10.0)  # counted on every lap
    # half of BBB's laps are censored: the median isn't known
    assert np.isnan(b["throttle_pickup_d"]) and b["pickup_censored"] == pytest.approx(0.5)
    assert PICKUP_CENSORED_M > 145.0


def test_entry_decel_is_the_braking_into_the_corner() -> None:
    distance = np.arange(80) * 5.0
    # 300 -> 100 km/h by point 40 (gently), 100 -> 250 by 60, then hard braking to 120 at 79
    speed = np.concatenate(
        [np.linspace(300, 100, 41), np.linspace(100, 250, 20)[1:], np.linspace(250, 120, 20)]
    )
    speed[40] = 99.0  # the corner's slowest point
    time_s = np.concatenate([[0.0], np.cumsum(np.diff(distance) / (speed[:-1] / 3.6))])
    first = np.stack([speed, np.linspace(80, 250, 80)])  # the second starts at its slowest
    times = np.stack([time_s, time_s])
    decel = entry_decel(first, times)
    accel = np.diff(speed / 3.6) / np.diff(time_s)
    assert decel[0] == pytest.approx(-accel[:40].min())
    assert decel[0] < -accel.min()  # the whole-window peak is the braking after the corner
    assert np.isnan(decel[1])


def test_teammate_pairs_follow_mid_season_changes(corners: pd.DataFrame) -> None:
    pairs = teammate_pairs(corners)
    assert set(zip(pairs["driver"], pairs["teammate"], strict=True)) == {
        ("AAA", "BBB"), ("CCC", "DDD"), ("CCC", "EEE"),
    }  # fmt: skip
    assert one(pairs, driver="AAA", teammate="BBB")["events"] == 6
    assert one(pairs, driver="CCC", teammate="DDD")["events"] == 3
    assert one(pairs, driver="CCC", teammate="EEE")["team"] == "Blue"


def test_profiles_recover_a_driver_effect_both_ways_round(corners: pd.DataFrame) -> None:
    profiles, events = build_profiles(corners)
    overall = profiles[(profiles["kind"] == "all") & (profiles["corner_type"] == "all")]
    brake = one(overall, driver="AAA", teammate="BBB", metric="brake_start_d")
    assert brake["diff"] == pytest.approx(5.0, abs=0.7)
    assert brake["clear"] and brake["lo"] > 0
    assert brake["events"] == brake["same_sign"] == 6
    assert one(overall, driver="AAA", metric="v_min")["diff"] == pytest.approx(1.0, abs=0.3)
    assert abs(one(overall, driver="AAA", metric="v_exit")["diff"]) < 0.5  # no effect built in
    back = one(overall, driver="BBB", teammate="AAA", metric="brake_start_d")
    assert back["diff"] == pytest.approx(-brake["diff"])
    assert back["lo"] == pytest.approx(-brake["hi"])

    coast = one(overall, driver="EEE", teammate="CCC", metric="coast_m")
    assert coast["diff"] == pytest.approx(4.0, abs=0.7)
    assert coast["pair_events"] == 3
    assert np.isnan(coast["lo"]) and not coast["clear"]  # three events: no interval

    # braking points come only from corners that own their slowest point (3, not 4)
    medium = profiles[(profiles["driver"] == "AAA") & (profiles["corner_type"] == "medium")]
    braking = one(medium, kind="all", metric="brake_start_d")
    coasting = one(medium, kind="all", metric="coast_m")
    assert coasting["corners"] == 2 * braking["corners"]
    assert set(events["metric"]) == set(METRIC_NAMES)
    assert len(events[(events["driver"] == "AAA") & (events["metric"] == "v_min")]) == 6


def test_summarise_differences_of_a_constant_difference() -> None:
    n = 60
    diffs = pd.DataFrame(
        {
            "year": YEAR,
            "round": np.repeat(np.arange(1, 7), 10),
            "kind": np.tile(["quali", "race"], n // 2),
            "corner_type": np.tile(["slow", "medium", "fast"], n // 3),
            **{name: np.full(n, np.nan) for name in METRIC_NAMES},
        }
    )
    diffs["brake_start_d"] = 3.0
    table = summarise_differences(diffs)
    cell = one(table, kind="all", corner_type="all", metric="brake_start_d")
    assert cell["diff"] == cell["lo"] == cell["hi"] == pytest.approx(3.0)
    assert cell["p"] == 0.0
    assert cell["events"] == cell["same_sign"] == 6 and cell["corners"] == n and cell["clear"]
    empty = table[table["metric"] == "v_min"]
    assert (empty["corners"] == 0).all() and empty["diff"].isna().all() and not empty["clear"].any()
    slow_quali = one(table, kind="quali", corner_type="slow", metric="brake_start_d")
    assert slow_quali["corners"] == 10  # every 6th row is quali and slow
    per_event = event_differences(diffs.assign(event="E"))
    assert per_event[per_event["metric"] == "brake_start_d"]["diff"].eq(3.0).all()


def test_summarise_differences_is_a_t_interval_over_events() -> None:
    # five events of two corners each: with equal counts the interval is the one-sample
    # t-interval on the event means
    means = np.array([1.0, 3.0, 2.0, 6.0, -1.0])
    diffs = pd.DataFrame(
        {
            "year": YEAR,
            "round": np.repeat(np.arange(1, 6), 2),
            "kind": "race",
            "corner_type": "slow",
            **{name: np.full(10, np.nan) for name in METRIC_NAMES},
        }
    )
    diffs["v_min"] = np.repeat(means, 2) + np.tile([-0.5, 0.5], 5)
    cell = one(summarise_differences(diffs), kind="all", corner_type="all", metric="v_min")
    se = means.std(ddof=1) / np.sqrt(5)
    half = stats.t.ppf(0.975, 4) * se
    assert cell["diff"] == pytest.approx(means.mean())
    assert cell["se"] == pytest.approx(se)
    assert (cell["lo"], cell["hi"]) == pytest.approx((means.mean() - half, means.mean() + half))
    assert cell["p"] == pytest.approx(2 * stats.t.sf(means.mean() / se, 4))
    assert cell["clear"] == (cell["lo"] > 0)
    # three events: the mean, but no interval and no verdict
    few = summarise_differences(diffs[diffs["round"] <= 3])
    cell = one(few, kind="all", corner_type="all", metric="v_min")
    assert cell["diff"] == pytest.approx(2.0) and cell["events"] == 3
    assert np.isnan(cell["lo"]) and np.isnan(cell["se"]) and not cell["clear"]


def test_chance_rates_centre_and_flip_whole_events(corners: pd.DataFrame) -> None:
    chance = chance_rates(corners, reps=4, seed=1)
    pairs = chance[["driver", "teammate"]].drop_duplicates()
    assert len(pairs) == 3 and chance["rep"].nunique() == 4
    assert len(chance) == 4 * 3 * 3 * 4 * len(METRIC_NAMES)
    assert chance["clear"].dtype == bool
    # AAA's 5 m braking effect is centred away: what is left is noise
    brake = chance[(chance["driver"] == "AAA") & (chance["metric"] == "brake_start_d")]
    assert brake["clear"].mean() < 0.5
    assert not chance.loc[chance["teammate"] == "DDD", "clear"].any()  # three events


def test_compare_drivers_matches_the_stored_profile(corners: pd.DataFrame, tmp_path) -> None:
    profiles, _ = build_profiles(corners)
    result = compare_drivers("bbb", "AAA", YEAR, corners=corners, root=tmp_path)
    stored = profiles[(profiles["driver"] == "BBB") & (profiles["teammate"] == "AAA")]
    joined = result.table.merge(stored, on=["kind", "corner_type", "metric"], suffixes=("", "_p"))
    assert len(joined) == len(result.table)
    assert np.allclose(joined["diff"], joined["diff_p"], equal_nan=True)
    assert np.allclose(joined["lo"], joined["lo_p"], equal_nan=True)
    assert result.teammates and result.teams == ("Red", "Red") and result.events == 6
    assert "BBB brakes earlier than AAA" in result.summary
    assert result.embedding == {}  # no embeddings file under tmp_path


def test_compare_drivers_notes_and_errors(corners: pd.DataFrame, tmp_path) -> None:
    rivals = compare_drivers("AAA", "CCC", YEAR, corners=corners, root=tmp_path)
    assert not rivals.teammates and rivals.teams == ("Red", "Blue")
    assert "different cars" in rivals.summary
    partial = compare_drivers("CCC", "DDD", YEAR, corners=corners, root=tmp_path)
    assert partial.teammates and partial.events == 3
    assert "teammates at 3 of the 6" in partial.summary
    assert "3 events are too few for intervals" in partial.summary
    assert "Clear differences" not in partial.summary and "[" not in partial.summary
    with pytest.raises(ToolInputError, match="Drivers: AAA"):
        compare_drivers("XYZ", "AAA", YEAR, corners=corners, root=tmp_path)
    with pytest.raises(ToolInputError, match="Seasons: 2025"):
        compare_drivers("AAA", "BBB", 2019, corners=corners, root=tmp_path)
    with pytest.raises(ToolInputError, match="two different drivers"):
        compare_drivers("AAA", "aaa", YEAR, corners=corners, root=tmp_path)
    with pytest.raises(ToolInputError, match="hasn't been built"):
        compare_drivers("AAA", "BBB", YEAR, root=tmp_path)


def test_embedding_profiles_separate_car_from_driver(frame: pd.DataFrame) -> None:
    clean = clean_segments(frame)
    rows, pairs = embedding_profiles(clean, synthetic_embeddings(clean), min_events=3)
    a, b = one(rows, driver="AAA"), one(rows, driver="BBB")
    assert a["cos_teammate"] > 0.8 > a["cos_field"]
    assert a["teammate_nearest"] and a["nearest"] == "BBB"
    style_a, style_b = np.asarray(a["style"], float), np.asarray(b["style"], float)
    assert np.dot(style_a, style_b) / np.linalg.norm(style_a) / np.linalg.norm(style_b) < -0.99
    # a steady offset, in both halves of the season, well beyond what noise gives
    assert a["style_consistency"] > 0.8 and a["consistency_p"] < 0.05 and a["consistent"]
    assert a["consistency_null95"] < a["style_consistency"]
    assert np.isnan(one(rows, driver="DDD")["style_consistency"])  # rounds 1-3: too few
    # CCC had two teammates: consistency is per pair, and the row shows the main one (EEE and
    # DDD share as many corners, so the first found)
    assert one(rows, driver="CCC")["teammates"] == "DDD, EEE"
    assert set(zip(pairs["driver"], pairs["teammate"], strict=True)) == {
        ("AAA", "BBB"), ("BBB", "AAA"), ("CCC", "DDD"), ("DDD", "CCC"), ("CCC", "EEE"),
        ("EEE", "CCC"),
    }  # fmt: skip
    ab, ba = one(pairs, driver="AAA"), one(pairs, driver="BBB")
    assert ab["style_consistency"] == ba["style_consistency"] and ab["events"] == 6
    assert np.isnan(one(pairs, driver="CCC", teammate="DDD")["consistency_p"])
    assert rows.attrs["style_dims"] >= 1
    assert len(a["centroid"]) == 8
    assert rows[["car_pc1", "car_pc2", "style_pc1", "style_pc2"]].notna().all().all()
    assert {"car_explained", "style_explained"} <= set(rows.attrs)


def test_consistency_test_against_noise() -> None:
    rng = np.random.default_rng(3)
    odd = np.arange(16) % 2 == 1
    steady = np.array([1.0, 0.5, 0, 0]) + rng.normal(0, 0.3, (16, 4))
    ones = np.ones(16)
    cos, p, null95 = consistency_test(steady, ones, odd, np.random.default_rng(0), reps=500)
    assert cos > null95 and p < 0.01
    # pure noise: about one in twenty is called consistent
    ps = np.array(
        [consistency_test(rng.normal(0, 1, (16, 4)), ones, odd, rng, 200)[1] for _ in range(100)]
    )
    assert float(np.mean(ps < 0.05)) < 0.15
    # noise in few directions gives wide chance cosines: far beyond 1/sqrt(dimensions)
    flat = np.zeros((16, 64))
    flat[:, :2] = rng.normal(0, 1, (16, 2))
    assert consistency_test(flat, ones, odd, rng, 500)[2] > 0.5
    # a steady offset per corner, with events of unequal size: still found
    sizes = np.where(np.arange(16) % 3 == 0, 30.0, 10.0)
    uneven = sizes[:, None] * np.array([1.0, 0.5, 0, 0]) + rng.normal(0, 3, (16, 4))
    assert consistency_test(uneven, sizes, odd, rng, 500)[1] < 0.01


def test_comparison_summary_words_the_embedding_by_what_it_shows() -> None:
    table = summarise_differences(
        pd.DataFrame(
            {
                "year": YEAR, "round": np.arange(1, 7), "kind": "race", "corner_type": "slow",
                **{name: np.arange(6.0) - 2.5 for name in METRIC_NAMES},
            }
        )
    )  # fmt: skip
    base = {"cos_centroids": 0.95, "cos_style": 0.1, "season_cos_teammate": 0.9,
            "season_cos_field": -0.1, "consistency_null95": 0.7}  # fmt: skip

    def summary(**embedding: float) -> str:
        args = (YEAR, "AAA", "BBB", ("Red", "Red"), True, 60, 6, table)
        return comparison_summary(*args, {**base, **embedding}, [])

    steady = summary(style_consistency=0.9, consistency_p=0.001)
    assert "the shared car dominates" in steady and "points the same way" in steady
    unclear = summary(style_consistency=0.35, consistency_p=0.25)
    assert "isn't clearly steady" in unclear and "points the same way" not in unclear
    opposite = summary(style_consistency=-0.57, consistency_p=0.9)
    assert "points different ways" in opposite and "same way" not in opposite
    apart = summary(cos_centroids=0.27, style_consistency=0.9, consistency_p=0.001)
    assert "less alike than most teammates" in apart and "dominates" not in apart


def test_quali_gaps_and_pace_check() -> None:
    laps = pd.DataFrame(
        {
            "year": YEAR,
            "round": [1, 1, 1, 1, 2, 2],
            "session": "Q",
            "team": "Red",
            "driver": ["AAA", "AAA", "BBB", "BBB", "AAA", "BBB"],
            "lap_number": [3, 5, 4, 6, 3, 4],
            "lap_time_s": [80.0, 79.0, 79.79, 70.0, 90.0, 90.9],
            "lap_class": ["push", "push", "push", "push", "push", "push"],
            "deleted": [False, False, False, True, False, False],
        }
    )
    gaps = quali_gaps(laps)
    # AAA's best is 79.0 against BBB's 79.79 (the 70.0 was deleted), then 90.0 against 90.9
    assert one(gaps, driver="AAA")["gap_pct"] == pytest.approx(-1.0, abs=0.01)
    assert one(gaps, driver="BBB")["gap_pct"] == pytest.approx(1.0, abs=0.01)
    profiles = pd.DataFrame(
        {
            "year": YEAR, "team": "Red", "driver": ["AAA"], "teammate": ["BBB"],
            "kind": "quali", "corner_type": "all", "metric": "segment_time_s", "diff": [-0.02],
            "pair_events": 6,
        }
    )  # fmt: skip
    check = pace_check(profiles, quali_gaps(laps))
    assert check["gap_pct"].iloc[0] == pytest.approx(-1.0, abs=0.01)


def scored(root: Path, frame: pd.DataFrame, run_id: str = "run-1") -> None:
    """What `race-engineer-infer score` leaves for style: a complete run of `frame`."""
    root.mkdir(parents=True, exist_ok=True)
    write_parquet(frame[["segment_id"]], root / "segment_scores.parquet", run_id)
    manifest = {"run_id": run_id, "frame_key": cache_key(frame, sources=True)}
    (root / MANIFEST).write_text(json.dumps(manifest))


def test_build_style_writes_tables_and_report(frame: pd.DataFrame, tmp_path) -> None:
    clean = clean_segments(frame)
    tables = build_style(
        frame, clean["segment_id"].to_numpy(), synthetic_embeddings(clean), 3, null_reps=200
    )
    assert tables.embeddings is not None and tables.segments == len(clean)
    scored(tmp_path, frame)
    paths = write_tables(tables, tmp_path, "run-1")
    assert {p.name for p in paths} == {
        "style_corners.parquet",
        "style_profiles.parquet",
        "style_events.parquet",
        "style_embeddings.parquet",
        "style_embedding_pairs.parquet",
        "style_axes.parquet",
    }
    # from the files, as a tool would: the embedding part comes along
    result = compare_drivers("AAA", "BBB", YEAR, root=tmp_path)
    assert result.embedding["cos_centroids"] > 0.8
    assert result.embedding["consistency_p"] < 0.05
    assert "Style embedding" in result.summary and "points the same way" in result.summary
    report = write_report(
        tables, pd.DataFrame(columns=["diff", "gap_pct"]), result, tmp_path / "r.md"
    )
    text = report.read_text()
    assert "## 2025 teammates" in text and "AAA - BBB" in text
    assert "**AAA vs BBB** (Red): AAA brakes later than BBB" in text
    assert "CCC - DDD | Blue | 3 |" in text and "[n/a]" in text
    assert "How often chance says clear" in text
    assert "## Style embedding" in text and "## Caveats" in text

    # A later scoring run makes the tables stale: a tool must not mix them with its results.
    scored(tmp_path, frame, "run-2")
    with pytest.raises(ToolInputError, match=r"out of date.*another scoring run"):
        compare_drivers("AAA", "BBB", YEAR, root=tmp_path)
    # Tables this run doesn't have go, rather than stay from an earlier run.
    no_embeddings = build_style(frame, chance_reps=3)
    written = write_tables(no_embeddings, tmp_path, "run-2")
    assert (
        {p.name for p in tmp_path.glob("style_*.parquet")}
        == {p.name for p in written}
        == {
            "style_corners.parquet",
            "style_profiles.parquet",
            "style_events.parquet",
        }
    )
    assert compare_drivers("AAA", "BBB", YEAR, root=tmp_path).embedding == {}


def test_run_style_builds_only_on_the_current_scoring_run_of_this_frame(
    frame: pd.DataFrame, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "results"
    loaded = []

    def load_eval_frame() -> pd.DataFrame:
        loaded.append(True)
        return frame.drop(columns="decel_entry")

    monkeypatch.setattr(eval_data, "load_eval_frame", load_eval_frame)
    monkeypatch.setattr(style, "load_entry_decel", lambda f: frame["decel_entry"].to_numpy())

    def run() -> style.StyleTables:
        return style.run_style(root, report=None, chance_reps=2)

    with pytest.raises(StaleResultsError, match="No current scoring run"):
        run()  # never scored (or a new model selected since)
    scored(root, frame)
    write_parquet(frame[["segment_id"]], root / "segment_scores.parquet", "run-0")
    with pytest.raises(StaleResultsError, match="another scoring run"):
        run()  # `score` stopped partway through its files
    assert not loaded  # both refused before the eval frame is loaded

    scored(root, frame.iloc[1:])
    with pytest.raises(StaleResultsError, match="not the one the current scoring run scored"):
        run()  # features computed for another session since `score`
    scored(root, frame)
    ids = frame["segment_id"].to_numpy().astype(str)
    embeddings = synthetic_embeddings(frame)
    np.savez(root / "embeddings.npz", segment_id=ids[1:], embedding=embeddings[1:])
    with pytest.raises(StaleResultsError, match="1 segments of the eval frame have no embedding"):
        run()

    np.savez(root / "embeddings.npz", segment_id=ids, embedding=embeddings)
    tables = run()
    assert tables.embeddings is not None
    for name in ("corners", "profiles", "events", "embeddings", "embedding_pairs", "axes"):
        style.load_style_table(name, root)  # each records the run: no error
    assert len(style.load_corners(root)) == len(tables.corners)


def test_style_axes_label_the_plane_with_each_pair_once() -> None:
    rng = np.random.default_rng(0)
    n, rows, profile_rows = 12, [], []
    for k in range(n):  # twelve teams of two, each pair mirrored
        vec = rng.normal(0, [3, 2, 0.3, 0.3, 0.3, 0.3])
        coast, exit_ = vec[0] * 3, vec[1] * 2  # the metrics follow two style directions
        for driver, mate, sign in ((f"A{k:02d}", f"B{k:02d}", 1), (f"B{k:02d}", f"A{k:02d}", -1)):
            rows.append(
                {
                    "year": YEAR,
                    "team": f"T{k}",
                    "driver": driver,
                    "teammates": mate,
                    "events": 10,
                    "style": (sign * vec).astype(np.float32),
                }
            )
            for metric, value in (("coast_m", coast), ("v_exit", exit_), ("v_min", rng.normal())):
                profile_rows.append({"year": YEAR, "driver": driver, "teammate": mate,
                                     "kind": "all", "corner_type": "all", "metric": metric,
                                     "diff": sign * value})  # fmt: skip
    axes = style_axes(pd.DataFrame(rows), pd.DataFrame(profile_rows), resamples=50)
    assert (axes["pairs"] == n).all()  # each pair once, not both ways round
    r = dict(zip(axes["metric"], axes["R"].astype(float), strict=True))
    assert r["coast_m"] > 0.9 and r["v_exit"] > 0.9 and r["v_min"] < r["coast_m"]
    assert (axes["R_lo"] <= axes["R_hi"]).all()
