"""M4 driving style: how each driver takes corners compared with the teammate who shares the car.

Two drivers in different cars differ in car as much as in driving, so the cleanest measure of
style this data allows is a paired one: the same corner, in the same session, on the same tyre
compound, driven by two teammates. For every such pair we take each driver's median, over their
clean laps, of the hand-crafted features a race engineer reads off a trace (braking point,
minimum speed, throttle pickup, coasting, exit speed, peak braking deceleration, time through
the corner) and the difference between the two. Averaged over a season with a 95% interval over
events (corners of one weekend share track, weather and set-up, so the weekend is the unit of
evidence), that reads as "RUS brakes 3 m later than ANT, 95% interval 1 to 5 m, later at 11 of
15 events". How often such a difference would be called clear by chance is measured, not
assumed (chance_rates).

The style embedding gives a second view. M3 found that it mostly encodes the car (drivers group
by team), so embedding "style" here is within-team too: the mean difference between a driver's
and their teammate's embedding on shared corners, and whether its direction holds between the
odd and even rounds of a season, against what noise alone gives (consistency_test).

    uv run race-engineer-infer style [--chance-reps 20]   # after `race-engineer-infer score`

It builds on the current scoring run (inference/provenance.py) and refuses to run when there is
none, or when the eval frame is no longer the one that run scored (features computed or a
session reprocessed since): the profiles would then describe other data than the scores.

Writes, under data/results/: style_corners.parquet (each driver's medians per corner, session
and compound: the building block `compare_drivers` works from), style_profiles.parquet (every
teammate pair by session kind, corner type and metric, both ways round), style_events.parquet
(the same per event), style_embeddings.parquet (one row per driver, season and team),
style_embedding_pairs.parquet (one row per teammate pair, both ways round) and
style_axes.parquet (what the 2-D style map means), each recording the scoring run it was built
from; and report/m4_style.md.
"""

from __future__ import annotations

import argparse
import logging
import time
import warnings
from collections.abc import Hashable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse, stats
from sklearn.decomposition import PCA

from race_engineer.config import QUALI_CODES, REPORT_DIR, RESULTS_DIR
from race_engineer.data.qa import markdown_table
from race_engineer.data.store import fixed_list_to_numpy, session_key, table_path
from race_engineer.inference.provenance import (
    StaleResultsError,
    check_current,
    scoring_run,
    write_parquet,
)
from race_engineer.tools.sessions import ResultsUnavailableError, ToolInputError

log = logging.getLogger(__name__)

CLEAN_CLASSES = ("push", "race")
# Race laps started closer than this behind another car are left out: following closely, a
# driver brakes and lifts for the car in front, and the tow changes the speeds. The gap is only
# known at the timing line, so this is a coarse filter (it drops about a quarter of race laps).
FREE_AIR_S = 1.0
# How the feed spells a tyre it doesn't know. Unknown against unknown isn't the same tyre, so
# these laps can't be paired and are left out.
UNKNOWN_COMPOUNDS = frozenset({"", "NAN", "NONE", "<NA>", "UNKNOWN", "TEST_UNKNOWN"})
EVENT = ["year", "round"]
SESSION = [*EVENT, "session"]
CORNER = ["corner", "corner_letter"]
UNIT = [*SESSION, *CORNER, "compound"]  # what a comparison pairs: one corner, session and tyre
DRIVER_SEASON = ["year", "driver", "team"]
OWN_MIN_M = 100.0  # see corner_types
LAP_MIN_M = 50.0  # see corner_medians
PICKUP_CENSORED_M = 150.0  # not back on full throttle by the window's last sample (145 m)
DECEL_SPIKE = 80.0  # m/s², about 8 g: F1 cars peak at 5-6 g, so higher values are sample spikes
SLOW_KPH, FAST_KPH = 120.0, 200.0
CORNER_TYPES = ("all", "slow", "medium", "fast")
SESSION_KINDS = ("all", "quali", "race")
MIN_INTERVAL_EVENTS = 4  # fewer events give no interval, and nothing is called clear
MIN_EVENTS = 5  # profiles over fewer events are kept, but not ranked or used for checks
CHANCE_REPS = 20  # no-difference replicates per pair for the false-alarm rate (chance_rates)
NULL_REPS = 2000  # sign flips per pair for the embedding consistency test


@dataclass(frozen=True)
class Metric:
    name: str  # a hand-crafted feature (race_engineer.features.handcrafted), or decel_entry
    label: str
    unit: str
    digits: int
    more: str  # what a positive difference (driver minus teammate) means
    less: str
    own: bool  # about the corner's own braking zone and slowest point (see corner_types)


METRICS = (
    Metric("brake_start_d", "braking point", "m", 1, "brakes later", "brakes earlier", True),
    Metric("decel_entry", "peak braking deceleration", "m/s²", 2, "brakes harder",
           "brakes more gently", True),
    Metric("v_min", "minimum speed", "km/h", 1, "carries more minimum speed",
           "carries less minimum speed", True),
    Metric("throttle_pickup_d", "full-throttle point", "m", 1, "is back on full throttle later",
           "is back on full throttle earlier", True),
    Metric("coast_m", "coasting", "m", 1, "coasts more", "coasts less", False),
    Metric("v_exit", "exit speed", "km/h", 1, "exits faster", "exits slower", False),
    Metric("segment_time_s", "time through the corner", "s", 3, "is slower through the corner",
           "is quicker through the corner", False),
)  # fmt: skip
METRIC_NAMES = tuple(m.name for m in METRICS)
METRIC_BY_NAME = {m.name: m for m in METRICS}
# What run_style reads from the eval frame; decel_entry comes from the segments (entry_decel).
FRAME_COLUMNS = [
    "segment_id", "year", "round", "session", "event", "driver", "team", "lap_number", "corner",
    "corner_letter", "apex_m", "lap_class", "compound", "deleted", "gap_ahead_s", "d_vmin",
    "v_apex", *(name for name in METRIC_NAMES if name != "decel_entry"),
]  # fmt: skip


# --- Which laps and corners count ------------------------------------------------------------


def normalise_compound(compound: pd.Series) -> pd.Series:
    """Upper-case compound names, with every spelling of a missing one as UNKNOWN."""
    text = compound.astype("string").fillna("").str.strip().str.upper()
    return text.where(~text.isin(UNKNOWN_COMPOUNDS), "UNKNOWN").astype(str)


def matched_attempts(frame: pd.DataFrame) -> pd.Series:
    """True for every row except qualifying push laps beyond the teammate's number of attempts.

    Qualifying gets faster as the track rubbers in, and a driver who reaches Q3 adds laps on
    new tyres at the end: compared on all their push laps, they'd look quicker than a teammate
    knocked out in Q1 for reasons that aren't driving. So in each qualifying session a driver's
    push laps count up to the teammate's number, in the order they were driven (deleted laps
    count as attempts: the driver was on track at that point of the session). Rows are laps'
    segments, so every row of a lap shares its fate.
    """
    keep = pd.Series(True, index=frame.index)
    quali = frame["session"].isin(QUALI_CODES) & frame["lap_class"].eq("push")
    if not quali.any():
        return keep
    laps = frame.loc[quali, [*SESSION, "team", "driver", "lap_number"]]
    by_driver = laps.groupby([*SESSION, "driver"])["lap_number"]
    attempt = by_driver.rank(method="dense")
    attempts = by_driver.transform("nunique")
    fewest = attempts.groupby([laps[c] for c in [*SESSION, "team"]]).transform("min")
    keep.loc[laps.index] = (attempt <= fewest).to_numpy()
    return keep


def clean_segments(frame: pd.DataFrame) -> pd.DataFrame:
    """The segments style is measured on: push laps in qualifying (matched_attempts) and
    green-flag race laps, lap time not deleted, tyre compound known, and in races only laps
    started in free air (see FREE_AIR_S). Adds `kind` (quali or race)."""
    race = ~frame["session"].isin(QUALI_CODES)
    traffic = race & (frame["gap_ahead_s"] < FREE_AIR_S)  # NaN (no car ahead) counts as free
    compound = normalise_compound(frame["compound"])
    keep = (
        frame["lap_class"].isin(CLEAN_CLASSES)
        & ~frame["deleted"].eq(True)
        & ~traffic
        & compound.ne("UNKNOWN")
        & matched_attempts(frame)
    )
    out = frame[keep].copy()
    out["compound"] = compound[keep]
    out["corner_letter"] = out["corner_letter"].fillna("").astype(str)
    out["kind"] = np.where(out["session"].isin(QUALI_CODES), "quali", "race")
    return out.reset_index(drop=True)


def entry_decel(speed: np.ndarray, time_s: np.ndarray) -> np.ndarray:
    """Peak deceleration (m/s²) before each segment's slowest point: the braking into the corner.

    The hand-crafted decel_max takes the peak over the whole window, which after a fast corner
    is often the braking for the next one, past this corner's minimum. Same finite differences
    as decel_max; NaN where the window starts at its slowest point (no braking zone in it).
    """
    speed = np.asarray(speed, dtype=np.float64)
    dt = np.clip(np.diff(np.asarray(time_s, dtype=np.float64), axis=1), 1e-3, None)
    accel = np.diff(speed / 3.6, axis=1) / dt
    i_min = speed.argmin(axis=1)
    before = np.arange(accel.shape[1])[None, :] < i_min[:, None]
    peak = np.where(before, accel, np.inf).min(axis=1)
    return np.where(i_min > 0, np.clip(-peak, 0.0, None), np.nan)


def load_entry_decel(frame: pd.DataFrame) -> np.ndarray:
    """entry_decel for every row of `frame` (by segment_id), from the processed segments of its
    sessions; only the speed and time channels are read."""
    out = np.full(len(frame), np.nan)
    ids = frame["segment_id"].to_numpy()
    for key, idx in frame.groupby(SESSION, sort=True).indices.items():
        year, round_, code = cast(tuple[int, int, str], key)
        path = table_path("segments", session_key(int(year), int(round_), str(code)))
        table = pq.read_table(path, columns=["segment_id", "speed", "time_s"])
        values = entry_decel(
            fixed_list_to_numpy(table["speed"]), fixed_list_to_numpy(table["time_s"])
        )
        pos = pd.Index(table["segment_id"].to_pandas()).get_indexer(ids[idx])
        out[idx] = np.where(pos >= 0, values[np.maximum(pos, 0)], np.nan)
    return out


def corner_types(clean: pd.DataFrame) -> pd.DataFrame:
    """Per event and corner: whether it owns its window's slowest point, its speed and type.

    Corner windows run from 250 m before the turn marker to 150 m after it, so neighbouring
    windows overlap: the window of the first part of a chicane, or of a kink after a hairpin,
    often has its minimum speed (and so its braking zone and throttle pickup) at the other
    corner. A corner owns its minimum when, over the event, the median minimum (`d_vmin`, m from
    the marker) lies within OWN_MIN_M of its marker and no other corner's marker is nearer to it.
    Metrics about the braking zone and the slowest point are then counted once, at the right
    corner. (Positions aren't wrapped at the timing line; a first corner that close to the last
    is rare.)

    Corner speed is the median minimum speed at corners that own theirs, and the median speed
    at the marker at the rest (flat kinks, parts of complexes): slow below SLOW_KPH, fast above
    FAST_KPH, medium in between. Both teammates' laps count, so both get the same type.
    """
    key = [*EVENT, *CORNER]
    table = (
        clean.groupby(key, sort=True)
        .agg(
            event=("event", "first"),
            apex_m=("apex_m", "median"),
            d_vmin=("d_vmin", "median"),
            v_min=("v_min", "median"),
            v_apex=("v_apex", "median"),
        )
        .reset_index()
    )
    apex, d_vmin = table["apex_m"].to_numpy(float), table["d_vmin"].to_numpy(float)
    owns = np.zeros(len(table), dtype=bool)
    for idx in table.groupby(EVENT).indices.values():
        slowest = apex[idx] + d_vmin[idx]  # where each window's slowest point is on the lap
        nearest = np.abs(slowest[:, None] - apex[idx][None, :]).argmin(axis=1)
        owns[idx] = (nearest == np.arange(len(idx))) & (np.abs(d_vmin[idx]) <= OWN_MIN_M)
    table["owns_min"] = owns
    table["corner_kph"] = np.where(owns, table["v_min"], table["v_apex"])
    table["corner_type"] = np.select(
        [table["corner_kph"] < SLOW_KPH, table["corner_kph"] > FAST_KPH], ["slow", "fast"], "medium"
    )
    return table[[*key, "event", "apex_m", "d_vmin", "owns_min", "corner_kph", "corner_type"]]


def corner_medians(clean: pd.DataFrame, types: pd.DataFrame) -> pd.DataFrame:
    """Each driver's median of every metric per corner, session and tyre compound, over their
    clean laps: the unit every comparison pairs. Medians, so an odd mistake or a lap with a bad
    sample doesn't move it, and every corner counts once however many laps it had.

    Metrics about a corner's own braking zone and slowest point come only from corners that own
    theirs (see corner_types), and only from laps whose own slowest point is within LAP_MIN_M of
    the corner's: at a fast corner a lap's minimum can sit at the window's start or at the next
    corner, and its braking point and throttle pickup then belong there (`own_laps` counts the
    laps that do count). Peak braking deceleration above DECEL_SPIKE is a sampling spike and left
    out. A lap not back on full throttle within the window counts as later than any that is
    (PICKUP_CENSORED_M), so the driver who picks up later doesn't look earlier for having those
    laps dropped; the median is still known while fewer than half the laps are censored, and is
    left empty otherwise (`pickup_censored` is the censored share).
    """
    own = [m.name for m in METRICS if m.own]
    where = types[[*EVENT, *CORNER, "owns_min", "d_vmin"]].rename(columns={"d_vmin": "min_d"})
    laps = clean.merge(where, on=[*EVENT, *CORNER], how="left", validate="many_to_one")
    at_min = laps["owns_min"].astype(bool) & (laps["d_vmin"] - laps["min_d"]).abs().le(LAP_MIN_M)
    laps.loc[~at_min, own] = np.nan
    laps.loc[laps["decel_entry"] > DECEL_SPIKE, "decel_entry"] = np.nan
    censored = at_min & laps["throttle_pickup_d"].isna()
    laps.loc[censored, "throttle_pickup_d"] = PICKUP_CENSORED_M
    laps["own_lap"], laps["censored"] = at_min, censored

    grouped = laps.groupby([*UNIT, "driver", "team"], sort=True)
    table = grouped[list(METRIC_NAMES)].median()
    counts = grouped[["own_lap", "censored"]].sum()
    table.loc[2 * counts["censored"] >= counts["own_lap"], "throttle_pickup_d"] = np.nan
    table.insert(0, "laps", grouped.size())
    table.insert(1, "own_laps", counts["own_lap"].astype(int))
    table.insert(2, "pickup_censored", counts["censored"] / counts["own_lap"].replace(0, np.nan))
    table = table.reset_index().merge(
        types.drop(columns=["apex_m", "d_vmin"]),
        on=[*EVENT, *CORNER],
        how="left",
        validate="many_to_one",
    )
    table["kind"] = np.where(table["session"].isin(QUALI_CODES), "quali", "race")
    columns = [*UNIT, "event", "kind", "corner_type", "corner_kph", "owns_min", "driver", "team"]
    return table[[*columns, "laps", "own_laps", "pickup_censored", *METRIC_NAMES]]


# --- Paired differences ----------------------------------------------------------------------


def paired_differences(
    corners: pd.DataFrame, driver_a: str, driver_b: str, year: int, same_team: bool = False
) -> pd.DataFrame:
    """Driver A minus driver B on every corner both took in the same session of `year` on the
    same tyre compound (and, with `same_team`, in the same car): one row per corner."""
    season = corners[corners["year"] == year]
    on = [*UNIT, "event", "kind", "corner_type"] + (["team"] if same_team else [])
    a = season[season["driver"] == driver_a]
    b = season[season["driver"] == driver_b]
    both = a.merge(b, on=on, suffixes=("_a", "_b"))
    out = both[[c for c in on if c != "team"]].copy()
    for side in ("a", "b"):
        out[f"team_{side}"] = both["team"] if same_team else both[f"team_{side}"]
        out[f"laps_{side}"] = both[f"laps_{side}"]
    for name in METRIC_NAMES:
        out[name] = both[f"{name}_a"] - both[f"{name}_b"]
    return out.reset_index(drop=True)


def summarise_differences(diffs: pd.DataFrame) -> pd.DataFrame:
    """Mean difference of each metric by session kind and corner type, with a 95% interval over
    events.

    Every corner pair counts once. The corners of one weekend share track, weather and set-up,
    so the evidence is in how much the difference varies between weekends: `se` is the
    event-clustered standard error of the mean (each event's summed deviation, with the
    small-sample factor G/(G-1) for G events), `lo`/`hi` the t-interval with G-1 degrees of
    freedom and `p` its two-sided p-value. One or two weekends can't show that spread, so below
    MIN_INTERVAL_EVENTS events there is no interval and nothing is `clear` (the interval
    excluding zero). `same_sign` counts the events whose own mean difference points the same way
    as the overall one. One row per session kind, corner type and metric: diff, lo, hi, se, p,
    corners, events, same_sign, clear.
    """
    event_code, events = pd.factorize(
        diffs["year"].astype(str) + "-" + diffs["round"].astype(str).str.zfill(2), sort=True
    )
    n_events = len(events)
    kind, ctype = diffs["kind"].to_numpy(), diffs["corner_type"].to_numpy()
    cells, sums, counts = [], [], []
    for k in SESSION_KINDS:
        in_kind = np.ones(len(diffs), bool) if k == "all" else kind == k
        for t in CORNER_TYPES:
            mask = in_kind & (True if t == "all" else ctype == t)
            for name in METRIC_NAMES:
                values = diffs[name].to_numpy(float)
                ok = mask & np.isfinite(values)
                cells.append((k, t, name))
                sums.append(np.bincount(event_code[ok], values[ok], minlength=n_events))
                counts.append(np.bincount(event_code[ok], minlength=n_events).astype(float))
    s = np.stack(sums, axis=1).reshape(n_events, len(cells))  # (events, cells)
    n = np.stack(counts, axis=1).reshape(n_events, len(cells))
    total, g = n.sum(axis=0), (n > 0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        diff = s.sum(axis=0) / total
        resid = s - np.nan_to_num(diff)[None, :] * n
        se = np.sqrt(g / (g - 1) * (resid**2).sum(axis=0)) / total
        se = np.where(g >= MIN_INTERVAL_EVENTS, se, np.nan)
        dof = np.maximum(g - 1, 1)
        half = stats.t.ppf(0.975, dof) * se
        p = np.where(np.isfinite(se), 2 * stats.t.sf(np.abs(diff) / se, dof), np.nan)
        same = ((n > 0) & (np.sign(s) == np.sign(diff))).sum(axis=0)
    table = pd.DataFrame(cells, columns=["kind", "corner_type", "metric"])
    table["unit"] = [METRIC_BY_NAME[c[2]].unit for c in cells]
    table["diff"], table["lo"], table["hi"] = diff, diff - half, diff + half
    table["se"], table["p"] = se, p
    table["corners"] = total.astype(int)
    table["events"] = g
    table["same_sign"] = same
    table["clear"] = (table["lo"] > 0) | (table["hi"] < 0)
    return table


def event_differences(diffs: pd.DataFrame) -> pd.DataFrame:
    """The mean difference of each metric per event, over all its sessions and corners."""
    grouped = diffs.groupby([*EVENT, "event"], sort=True)
    means = grouped[list(METRIC_NAMES)].mean().melt(ignore_index=False, var_name="metric")
    counts = grouped[list(METRIC_NAMES)].count().melt(ignore_index=False, var_name="metric")
    out = means.rename(columns={"value": "diff"}).assign(corners=counts["value"].to_numpy())
    return out.reset_index()


def mirrored(table: pd.DataFrame) -> pd.DataFrame:
    """The same differences the other way round (teammate minus driver)."""
    out = table.copy()
    out["diff"] = -table["diff"]
    if "lo" in table:
        out["lo"], out["hi"] = -table["hi"], -table["lo"]
    return out


def teammate_pairs(corners: pd.DataFrame) -> pd.DataFrame:
    """(year, team, driver, teammate) for every two drivers who shared a car in at least one
    session, driver before teammate alphabetically, with the sessions and events they shared.
    Mid-season changes give one pair per teammate (Tsunoda 2023: de Vries, Ricciardo, Lawson),
    and a driver who moved team mid-season gets a pair at each team."""
    present = corners[[*SESSION, "team", "driver"]].drop_duplicates()
    both = present.merge(present, on=[*SESSION, "team"], suffixes=("", "_mate"))
    both = both[both["driver"] < both["driver_mate"]]
    pairs = (
        both.groupby(["year", "team", "driver", "driver_mate"], sort=True)
        .agg(sessions=("session", "size"), events=("round", "nunique"))
        .reset_index()
        .rename(columns={"driver_mate": "teammate"})
    )
    return pairs


def teammate_differences(corners: pd.DataFrame) -> Iterator[tuple[dict[str, Any], pd.DataFrame]]:
    """Every teammate pair's corner differences in their shared car (paired_differences, driver
    minus teammate, driver first alphabetically), with the pair's year, team, names and events."""
    for pair in teammate_pairs(corners).to_dict("records"):
        year, team = int(pair["year"]), str(pair["team"])
        driver, mate = str(pair["driver"]), str(pair["teammate"])
        diffs = paired_differences(corners, driver, mate, year, same_team=True)
        diffs = diffs[diffs["team_a"] == team].reset_index(drop=True)
        if diffs.empty:
            continue
        ident = {"year": year, "team": team, "driver": driver, "teammate": mate}
        yield {**ident, "pair_events": int(diffs["round"].nunique())}, diffs


def build_profiles(corners: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every teammate pair's profile (summarise_differences) and per-event differences, both
    ways round: each driver's rows read "driver minus teammate"."""
    profiles, per_event = [], []
    for ident, diffs in teammate_differences(corners):
        summary, by_event = summarise_differences(diffs), event_differences(diffs)
        driver, mate, team = ident["driver"], ident["teammate"], ident["team"]
        common = {"year": ident["year"], "team": team, "pair_events": ident["pair_events"]}
        for first, second, table, events in (
            (driver, mate, summary, by_event),
            (mate, driver, mirrored(summary), mirrored(by_event)),
        ):
            profiles.append(table.assign(driver=first, teammate=second, **common))
            per_event.append(events.assign(driver=first, teammate=second, team=team))
    lead = ["year", "team", "driver", "teammate"]
    profile_table = pd.concat(profiles, ignore_index=True)
    profile_table = profile_table[[*lead, *(c for c in profile_table if c not in lead)]]
    event_table = pd.concat(per_event, ignore_index=True)
    event_table = event_table[[*lead, *(c for c in event_table if c not in lead)]]
    return profile_table, event_table


def chance_rates(corners: pd.DataFrame, reps: int = CHANCE_REPS, seed: int = 0) -> pd.DataFrame:
    """How often `clear` fires when there is no difference to find.

    Each pair's corner differences are centred (the mean of their session kind and corner type
    taken off, so every cell's true difference is zero), given a random sign per event (a whole
    weekend at a time, the unit the intervals are over) and summarised again, `reps` times: a
    world with the pair's own noise and nothing else. The share of those cells called clear is
    the false-alarm rate. One row per replicate, pair (driver first alphabetically), session
    kind, corner type and metric.
    """
    rng = np.random.default_rng(seed)
    names = list(METRIC_NAMES)
    parts = []
    for ident, diffs in teammate_differences(corners):
        cell_mean = diffs.groupby(["kind", "corner_type"])[names].transform("mean")
        centred = (diffs[names] - cell_mean).to_numpy()
        code, uniques = pd.factorize(diffs["round"])
        for rep in range(reps):
            signs = rng.choice([-1.0, 1.0], size=len(uniques))[code]
            world = diffs.copy()
            world[names] = centred * signs[:, None]
            table = summarise_differences(world)
            columns = ["kind", "corner_type", "metric", "events", "clear"]
            parts.append(table[columns].assign(rep=rep, **ident))
    return pd.concat(parts, ignore_index=True)


# --- Style embeddings ------------------------------------------------------------------------


def group_means(x: np.ndarray, codes: np.ndarray, n_groups: int) -> tuple[np.ndarray, np.ndarray]:
    """Row means of `x` per group code (NaN for empty groups), and the group sizes."""
    counts = np.bincount(codes, minlength=n_groups)
    indicator = sparse.csr_matrix(
        (np.ones(len(codes), np.float32), (codes, np.arange(len(codes)))),
        shape=(n_groups, len(codes)),
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.asarray(indicator @ x, dtype=np.float64) / counts[:, None]
    return means, counts


def unit_vectors(x: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(x, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(norm > 0, x / norm, np.nan)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / denom if denom > 0 and np.isfinite(denom) else float("nan")


def participation_ratio(x: np.ndarray) -> float:
    """How many independent directions `x` (rows) really varies along: (sum of the covariance
    eigenvalues)² / sum of their squares. 64 for isotropic noise in 64 dimensions, 1 for a line."""
    eig = np.clip(np.linalg.eigvalsh(np.cov(x, rowvar=False)), 0, None)
    return float(eig.sum() ** 2 / (eig**2).sum()) if eig.sum() > 0 else float("nan")


def consistency_test(
    sums: np.ndarray,
    counts: np.ndarray,
    odd: np.ndarray,
    rng: np.random.Generator,
    reps: int = NULL_REPS,
) -> tuple[float, float, float]:
    """Whether a teammate difference holds its direction through a season.

    `sums` has one row per event, the summed embedding difference over its `counts` corners;
    `odd` marks the odd rounds. The statistic is the cosine between the odd and the even
    rounds' totals. What noise alone gives depends on how many directions the embedding really
    varies in (far fewer than 64, so a fixed 1/sqrt(64) is much too narrow), so the reference
    is measured: each event's deviation from the pair's mean difference (per corner, times its
    corners) gets a random sign, which keeps the noise's size and shape and takes out any
    steady direction, and the cosine is recomputed `reps` times. Returns the cosine, its
    p-value (the share of those at least as high) and the 95th percentile of the no-difference
    cosines.
    """
    observed = cosine(sums[odd].sum(axis=0), sums[~odd].sum(axis=0))
    counts = np.asarray(counts, dtype=np.float64)
    resid = sums - counts[:, None] * (sums.sum(axis=0) / max(float(counts.sum()), 1.0))
    signs = rng.choice([-1.0, 1.0], size=(reps, len(sums)))
    a, b = signs[:, odd] @ resid[odd], signs[:, ~odd] @ resid[~odd]
    with np.errstate(invalid="ignore", divide="ignore"):
        null = (a * b).sum(axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
    null = null[np.isfinite(null)]
    if not np.isfinite(observed) or not len(null):
        return float("nan"), float("nan"), float("nan")
    p = (1 + int((null >= observed - 1e-12).sum())) / (1 + len(null))
    return observed, p, float(np.percentile(null, 95))


def embedding_profiles(
    clean: pd.DataFrame,
    embeddings: np.ndarray,
    min_events: int = MIN_EVENTS,
    null_reps: int = NULL_REPS,
    seed: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Style from the [CLS] embeddings of the clean segments (`embeddings` row i belongs to
    `clean` row i): one table per driver, season and team, and one per teammate pair.

    Dimensions are standardised (as for M3's style map) and averaged per driver and corner
    unit, so every corner counts once however many laps it had. `centroid` is the driver's mean
    difference from the field on the same corners: car and driver together. `style` is their
    mean difference from their teammates on shared corners: the driver alone, relative to them.
    Cosines between centroids show the car's signature (cos_teammate against cos_field, the
    mean over drivers of other teams). `style_consistency` is per teammate pair: the cosine
    between their difference in odd and in even rounds, with a p-value against noise alone
    (consistency_test; `consistent` = p < 0.05, both halves need two events); a driver-season
    row carries the one for its main teammate (the most shared corners). `raw_*` cosines use
    the plain mean embedding, which is dominated by the corners themselves. The 2-D projections
    (PCA fitted on driver-seasons with at least `min_events` events) are for charts: their two
    axes explain similar shares, so label the plane (style_axes), not an axis.
    """
    x = np.asarray(embeddings, dtype=np.float32)
    mean = x.mean(axis=0, dtype=np.float64)
    std = np.maximum(x.std(axis=0, dtype=np.float64), 1e-6)
    x = ((x - mean.astype(np.float32)) / std.astype(np.float32)).astype(np.float32)

    keys = [*UNIT, "driver", "team"]
    codes = clean.groupby(keys, sort=False).ngroup().to_numpy()
    n_units = int(codes.max()) + 1 if len(codes) else 0
    m, segments = group_means(x, codes, n_units)
    units = clean.iloc[np.unique(codes, return_index=True)[1]][keys].reset_index(drop=True)
    units["segments"] = segments
    unit_code = units.groupby(UNIT, sort=False).ngroup().to_numpy()
    field, _ = group_means(m, unit_code, int(unit_code.max()) + 1)
    residual = m - field[unit_code]

    grouped = units.groupby(DRIVER_SEASON, sort=True)
    dt = grouped.ngroup().to_numpy()
    rows = grouped.agg(
        events=("round", "nunique"), corners=("round", "size"), segments=("segments", "sum")
    ).reset_index()
    n_rows = len(rows)
    centroid, _ = group_means(residual, dt, n_rows)
    raw, _ = group_means(m, dt, n_rows)

    # Teammates on the same corner units: the within-team difference.
    index = units[[*UNIT, "team", "driver"]].assign(i=np.arange(len(units)))
    pairs = index.merge(index, on=[*UNIT, "team"], suffixes=("", "_mate"))
    pairs = pairs[pairs["driver"] != pairs["driver_mate"]]
    i, j = pairs["i"].to_numpy(), pairs["i_mate"].to_numpy()
    d = m[i] - m[j]
    style, _ = group_means(d, dt[i], n_rows)
    rows.attrs["style_dims"] = participation_ratio(d) if len(d) > 2 else float("nan")

    shared = pd.DataFrame({"row": dt[i], "mate_row": dt[j], "mate": pairs["driver_mate"]})
    shared = shared.groupby(["row", "mate_row", "mate"]).size().rename("n").reset_index()
    shared = shared.sort_values(["row", "n"], ascending=[True, False])
    main_mate = shared.drop_duplicates("row").set_index("row")["mate_row"]
    teammates = shared.sort_values(["row", "mate"]).groupby("row")["mate"].agg(", ".join)
    rows["teammates"] = teammates.reindex(range(n_rows)).fillna("").to_numpy()

    pair_table = _pair_consistency(
        rows, dt[i], dt[j], pairs["round"].to_numpy(), d, null_reps, seed
    )
    tests = ["style_consistency", "consistency_p", "consistency_null95"]
    keys = [(r, int(main_mate[r])) if r in main_mate.index else (-1, -1) for r in range(n_rows)]
    found = pair_table.set_index(["row", "mate_row"])[tests]
    rows[tests] = found.reindex(pd.MultiIndex.from_tuples(keys)).to_numpy(float)
    rows["consistent"] = rows["consistency_p"] < 0.05
    pair_table = pair_table.drop(columns=["row", "mate_row"])

    rows["cos_teammate"] = [
        cosine(centroid[r], centroid[int(main_mate[r])]) if r in main_mate.index else np.nan
        for r in range(n_rows)
    ]
    rows["raw_cos_teammate"] = [
        cosine(raw[r], raw[int(main_mate[r])]) if r in main_mate.index else np.nan
        for r in range(n_rows)
    ]
    rows["style_rms"] = np.sqrt(np.mean(style**2, axis=1))  # NaN without a teammate

    for column in ("cos_field", "raw_cos_field", "nearest_cos", "style_twin_cos"):
        rows[column] = np.nan
    rows["nearest"], rows["style_twin"] = "", ""
    rows["teammate_nearest"] = False
    reliable = rows["events"].to_numpy() >= min_events
    unit_centroid, unit_raw, unit_style = (
        unit_vectors(centroid),
        unit_vectors(raw),
        unit_vectors(style),
    )
    for idx in rows.groupby("year").indices.values():
        team = rows["team"].to_numpy()[idx]
        driver = rows["driver"].to_numpy()[idx]
        other = (team[:, None] != team[None, :]) & (driver[:, None] != driver[None, :])
        other &= reliable[idx][None, :]
        mate = (team[:, None] == team[None, :]) & (driver[:, None] != driver[None, :])
        for column, vectors in (("cos_field", unit_centroid), ("raw_cos_field", unit_raw)):
            sims = vectors[idx] @ vectors[idx].T
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                rows.loc[idx, column] = np.nanmean(np.where(other, sims, np.nan), axis=1)
        sims = unit_centroid[idx] @ unit_centroid[idx].T
        candidates = np.where(other | (mate & reliable[idx][None, :]), sims, -np.inf)
        candidates = np.nan_to_num(candidates, nan=-np.inf)
        best = candidates.argmax(axis=1)
        found = np.isfinite(candidates.max(axis=1))
        rows.loc[idx, "nearest"] = np.where(found, driver[best], "")
        rows.loc[idx, "nearest_cos"] = np.where(found, candidates.max(axis=1), np.nan)
        rows.loc[idx, "teammate_nearest"] = found & mate[np.arange(len(idx)), best]
        twins = np.nan_to_num(
            np.where(other, unit_style[idx] @ unit_style[idx].T, -np.inf), nan=-np.inf
        )
        found = np.isfinite(twins.max(axis=1))
        rows.loc[idx, "style_twin"] = np.where(found, driver[twins.argmax(axis=1)], "")
        rows.loc[idx, "style_twin_cos"] = np.where(found, twins.max(axis=1), np.nan)

    for prefix, vectors in (("car", centroid), ("style", style)):
        fit = reliable & np.isfinite(vectors).all(axis=1)
        rows[f"{prefix}_pc1"], rows[f"{prefix}_pc2"] = np.nan, np.nan
        if fit.sum() >= 3:
            pca = PCA(n_components=2, random_state=0).fit(vectors[fit])
            ok = np.isfinite(vectors).all(axis=1)
            xy = pca.transform(vectors[ok])
            rows.loc[ok, f"{prefix}_pc1"], rows.loc[ok, f"{prefix}_pc2"] = xy[:, 0], xy[:, 1]
            rows.attrs[f"{prefix}_explained"] = [float(v) for v in pca.explained_variance_ratio_]
    rows["centroid"] = list(centroid.astype(np.float32))
    rows["style"] = list(style.astype(np.float32))
    return rows, pair_table


def _pair_consistency(
    rows: pd.DataFrame,
    row: np.ndarray,
    mate_row: np.ndarray,
    rounds: np.ndarray,
    d: np.ndarray,
    null_reps: int,
    seed: int,
) -> pd.DataFrame:
    """One row per teammate pair, both ways round, from the unit-level differences `d` (driver
    row minus mate row, round of each): events, corners, style_rms, and consistency_test's
    cosine, p-value and 95th null percentile (computed once per pair; mirroring doesn't change
    them)."""
    names = rows["driver"].to_numpy()
    first = names[row] < names[mate_row]
    key = pd.DataFrame({"row": row[first], "mate_row": mate_row[first], "round": rounds[first]})
    code = key.groupby(["row", "mate_row", "round"], sort=True).ngroup().to_numpy()
    means, counts = group_means(d[first], code, int(code.max()) + 1 if len(code) else 0)
    sums = means * counts[:, None]
    per_event = key.groupby(["row", "mate_row", "round"], sort=True).size().reset_index()
    rng = np.random.default_rng(seed)
    out = []
    years, teams = rows["year"].to_numpy(), rows["team"].to_numpy()
    for key, idx in per_event.groupby(["row", "mate_row"], sort=True).indices.items():
        r, q = cast(tuple[int, int], key)
        odd = per_event["round"].to_numpy()[idx] % 2 == 1
        cos, p, null95 = float("nan"), float("nan"), float("nan")
        if odd.sum() >= 2 and (~odd).sum() >= 2:
            cos, p, null95 = consistency_test(sums[idx], counts[idx], odd, rng, null_reps)
        total = sums[idx].sum(axis=0) / counts[idx].sum()
        common = {
            "year": int(years[r]),
            "team": str(teams[r]),
            "events": len(idx),
            "corners": int(counts[idx].sum()),
            "style_rms": float(np.sqrt(np.mean(total**2))),
            "style_consistency": cos,
            "consistency_p": p,
            "consistency_null95": null95,
        }
        out.append({**common, "row": r, "mate_row": q, "driver": names[r], "teammate": names[q]})
        out.append({**common, "row": q, "mate_row": r, "driver": names[q], "teammate": names[r]})
    columns = ["year", "team", "driver", "teammate", "row", "mate_row", "events", "corners",
               "style_rms", "style_consistency", "consistency_p", "consistency_null95"]  # fmt: skip
    table = pd.DataFrame(out, columns=columns)
    table["consistent"] = table["consistency_p"] < 0.05
    return table.sort_values(["year", "team", "driver", "teammate"]).reset_index(drop=True)


def _uncentred_r(x: np.ndarray, y: np.ndarray) -> float:
    """Correlation through the origin: a teammate difference has no natural sign (each pair is
    one point and its mirror image), so no mean is taken off."""
    denom = float(np.sqrt((x**2).sum() * (y**2).sum()))
    return float((x * y).sum()) / denom if denom > 0 else float("nan")


def _plane_r(xy: np.ndarray, y: np.ndarray) -> float:
    """Multiple correlation (through the origin) of y with the two columns of xy: how much of
    y the plane holds, whichever way its axes are turned."""
    beta, *_ = np.linalg.lstsq(xy, y, rcond=None)
    total = float((y**2).sum())
    return float(np.sqrt(max(0.0, 1 - float(((y - xy @ beta) ** 2).sum()) / total)))


def style_axes(
    embeddings: pd.DataFrame, profiles: pd.DataFrame, resamples: int = 200, seed: int = 0
) -> pd.DataFrame:
    """What the 2-D map of within-team style vectors (style_pc1/2) means in a race engineer's
    terms, over teammate pairs with a driver-season that had only that teammate, each pair once
    (a pair and its mirror image are the same evidence).

    The two principal axes explain similar shares of variance, so which comes first, and how
    they're turned within their plane, is arbitrary: refitting on resampled teams can swap them.
    So the plane is labelled instead: per metric, `r_pc1`/`r_pc2` (correlations with the axes,
    i.e. the direction to draw the metric as an arrow on the chart) and `R`, its multiple
    correlation with the plane, which doesn't depend on how the axes are turned. `R_lo`/`R_hi`
    are the 5th and 95th percentiles of R when the plane is refitted on teams resampled with
    replacement.
    """
    empty = pd.DataFrame(columns=["metric", "pairs", "r_pc1", "r_pc2", "R", "R_lo", "R_hi"])
    vectors = np.stack(embeddings["style"].to_numpy()).astype(np.float64)
    fit = np.isfinite(vectors).all(axis=1) & (embeddings["events"].to_numpy() >= MIN_EVENTS)
    if fit.sum() < 3:
        return empty
    mates = embeddings["teammates"].astype(str)
    single = fit & (mates != "").to_numpy() & ~mates.str.contains(",").to_numpy()
    wide = profiles[(profiles["kind"] == "all") & (profiles["corner_type"] == "all")]
    wide = wide.pivot_table(index=["year", "driver", "teammate"], columns="metric", values="diff")
    table = embeddings.assign(pos=np.arange(len(embeddings)))
    once = table[single]
    drivers, others = once["driver"].astype(str).to_numpy(), once["teammates"].to_numpy()
    once = once.assign(
        pair=["-".join(sorted(p)) for p in zip(drivers, others, strict=True)],
        second=drivers > others,
    )
    once = once.sort_values(["second", "pos"]).drop_duplicates(["year", "team", "pair"])
    joined = once.merge(
        wide.reset_index(), left_on=["year", "driver", "teammates"],
        right_on=["year", "driver", "teammate"],
    )  # fmt: skip
    if len(joined) < 3:
        return empty
    x = vectors[joined["pos"].to_numpy()]

    def fit_plane(rows: np.ndarray) -> np.ndarray:
        return np.asarray(PCA(n_components=2, random_state=0).fit(vectors[rows]).components_)

    groups = list(table[fit].groupby(["year", "team"]).indices.values())
    fit_rows = np.flatnonzero(fit)
    rng = np.random.default_rng(seed)
    resampled = []
    for _ in range(resamples):
        picks = rng.integers(0, len(groups), len(groups))
        resampled.append(x @ fit_plane(np.concatenate([fit_rows[groups[k]] for k in picks])).T)
    xy = x @ fit_plane(fit_rows).T
    out = []
    for metric in METRICS:
        if metric.name not in joined:
            continue
        y = joined[metric.name].to_numpy(float)
        ok = np.isfinite(y)
        spread = [_plane_r(r[ok], y[ok]) for r in resampled]
        out.append(
            {
                "metric": metric.name,
                "pairs": int(ok.sum()),
                "r_pc1": _uncentred_r(xy[ok, 0], y[ok]),
                "r_pc2": _uncentred_r(xy[ok, 1], y[ok]),
                "R": _plane_r(xy[ok], y[ok]),
                "R_lo": float(np.percentile(spread, 5)) if spread else np.nan,
                "R_hi": float(np.percentile(spread, 95)) if spread else np.nan,
            }
        )
    return pd.DataFrame(out)


# --- A comparison of two named drivers, for tools --------------------------------------------


@dataclass(frozen=True)
class StyleComparison:
    year: int
    driver_a: str
    driver_b: str
    teams: tuple[str, str]
    teammates: bool  # compared only where they shared a car
    events: int
    corners: int
    table: pd.DataFrame  # summarise_differences, A minus B
    by_event: pd.DataFrame  # event_differences, A minus B
    embedding: dict[str, float]
    notes: list[str]
    summary: str  # plain language, for the model to relay


def load_style_table(name: str, root: Path = RESULTS_DIR) -> pd.DataFrame | None:
    """style_<name>.parquet, or None if it hasn't been built. ResultsUnavailableError (a
    ToolInputError) if it was built from another scoring run than the current one: a tool would
    mix two runs otherwise."""
    path = root / f"style_{name}.parquet"
    if not path.exists():
        return None
    try:
        check_current(path, "style", root)
    except StaleResultsError as err:
        raise ResultsUnavailableError(f"The driving-style data is out of date. {err}") from None
    return pd.read_parquet(path)


def load_corners(root: Path = RESULTS_DIR) -> pd.DataFrame:
    corners = load_style_table("corners", root)
    if corners is None:
        raise ResultsUnavailableError(
            "Driving-style data hasn't been built yet: run `uv run race-engineer-infer style`."
        )
    return corners


def load_style_embeddings(root: Path = RESULTS_DIR) -> pd.DataFrame | None:
    return load_style_table("embeddings", root)


def load_embedding_pairs(root: Path = RESULTS_DIR) -> pd.DataFrame | None:
    return load_style_table("embedding_pairs", root)


def _driver_code(season: pd.DataFrame, text: str, year: int) -> str:
    code = text.strip().upper()
    if code not in set(season["driver"]):
        roster = season.groupby("driver")["team"].agg(lambda t: "/".join(sorted(set(t))))
        names = ", ".join(f"{d} ({t})" for d, t in roster.items())
        raise ToolInputError(
            f"No driver {text!r} in the {year} style data. Drivers: {names}. "
            "Use the three-letter code."
        )
    return code


def n_events(n: int) -> str:
    return f"{n} event" if n == 1 else f"{n} events"


def p_text(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}" if p < 0.01 else f"p = {p:.2f}"


def describe(row: pd.Series, a: str, b: str) -> str:
    """One metric's difference in words, from a summarise_differences row (A minus B):
    "RUS brakes later than ANT: by 3.1 m [1.2, 5.0], at 11 of 15 events" (without the interval
    below MIN_INTERVAL_EVENTS events)."""
    metric = METRIC_BY_NAME[str(row["metric"])]
    diff, lo, hi = float(row["diff"]), float(row["lo"]), float(row["hi"])
    phrase = metric.more if diff > 0 else metric.less
    if diff < 0:  # the interval of the size of the difference, in the same direction
        diff, lo, hi = -diff, -hi, -lo
    d = metric.digits
    interval = f" [{lo:.{d}f}, {hi:.{d}f}]" if np.isfinite(lo) and np.isfinite(hi) else ""
    return (
        f"{a} {phrase} than {b}: by {diff:.{d}f} {metric.unit}{interval}, "
        f"at {int(row['same_sign'])} of {n_events(int(row['events']))}"
    )


def breakdown(table: pd.DataFrame, metric: str) -> str:
    """A metric's difference by corner type and by session kind, "*" where it is clear:
    "slow +5.2*, medium +2.0, fast +0.4; qualifying +3.0*, races +2.8"."""
    digits = METRIC_BY_NAME[metric].digits
    rows = table[table["metric"] == metric].to_dict("records")
    cells = {(r["kind"], r["corner_type"]): r for r in rows}

    def part(kind: str, corner_type: str, label: str) -> str | None:
        cell = cells.get((kind, corner_type))
        if cell is None or cell["corners"] == 0 or not np.isfinite(cell["diff"]):
            return None
        return f"{label} {cell['diff']:+.{digits}f}{'*' if cell['clear'] else ''}"

    types = [part("all", t, t) for t in CORNER_TYPES[1:]]
    kinds = [part(k, "all", label) for k, label in (("quali", "qualifying"), ("race", "races"))]
    return "; ".join(", ".join(p for p in group if p) for group in (types, kinds) if any(group))


def embedding_sentence(
    year: int, a: str, b: str, teammates: bool, embedding: Mapping[str, float]
) -> str:
    """The style-embedding part of a comparison, worded by what its numbers show."""
    cos = embedding["cos_centroids"]
    mate_cos, field_cos = embedding["season_cos_teammate"], embedding["season_cos_field"]
    context = (
        f"median for {year} teammates {mate_cos:+.2f}, for drivers of different teams "
        f"{field_cos:+.2f}"
    )
    if not teammates:
        return (
            f"Style embedding: cosine {cos:+.2f} between their field-relative centroids (car "
            f"and driver together; {context}), and {embedding['cos_style']:+.2f} between "
            "their differences from their own teammates (positive: they differ from their "
            "teammates in similar ways)."
        )
    car = (
        "as for most teammates, the shared car dominates"
        if cos >= (mate_cos + field_cos) / 2
        else "less alike than most teammates (few shared corners, or a real difference in how "
        "they use the car)"
    )
    text = f"Style embedding: their field-relative centroids have cosine {cos:+.2f} ({context}): "
    text += f"{car}."
    consistency = embedding.get("style_consistency", float("nan"))
    p = embedding.get("consistency_p", float("nan"))
    if not (np.isfinite(consistency) and np.isfinite(p)):
        return text
    noise = f"noise alone reaches {embedding['consistency_null95']:+.2f} one time in 20"
    if p < 0.05:
        text += (
            f" The embedding difference between {a} and {b} points the same way in odd and "
            f"even rounds (cosine {consistency:+.2f}, {p_text(p)}; {noise}): a steady "
            "difference in style."
        )
    elif consistency > 0:
        text += (
            f" The embedding difference between {a} and {b} isn't clearly steady: odd and even "
            f"rounds agree no more than noise can (cosine {consistency:+.2f}, {p_text(p)}; "
            f"{noise})."
        )
    else:
        text += (
            f" The embedding difference between {a} and {b} points different ways in odd and "
            f"even rounds (cosine {consistency:+.2f}): no steady direction."
        )
    return text


def comparison_summary(
    year: int,
    a: str,
    b: str,
    teams: tuple[str, str],
    teammates: bool,
    corners: int,
    events: int,
    table: pd.DataFrame,
    embedding: dict[str, float],
    notes: list[str],
) -> str:
    overall = table[(table["kind"] == "all") & (table["corner_type"] == "all")]
    who = (
        f"{a} and {b}, {teams[0]} teammates"
        if teammates
        else f"{a} ({teams[0]}) and {b} ({teams[1]})"
    )
    lines = [
        f"{year}: {who}, compared on {corners:,} corners that both took in the same session on "
        f"the same tyre compound, over {n_events(events)}. Differences are {a} minus {b}, with 95% "
        "intervals from the spread between events.",
    ]
    if overall["lo"].isna().all():
        lines.append(
            f"{n_events(events)} {'is' if events == 1 else 'are'} too few for intervals "
            f"({MIN_INTERVAL_EVENTS} needed), so no difference is called clear. The raw "
            "differences, which one weekend can dominate:"
        )
        lines += [f"- {describe(row, a, b)}." for _, row in overall.iterrows() if row["corners"]]
    else:
        clear = overall[overall["clear"]].sort_values("p")
        if len(clear):
            lines.append(
                "Clear differences, strongest first (by corner type and session; * = clear):"
            )
        for _, row in clear.iterrows():
            lines.append(f"- {describe(row, a, b)} ({breakdown(table, str(row['metric']))}).")
        unclear = [METRIC_BY_NAME[str(m)].label for m in overall.loc[~overall["clear"], "metric"]]
        if unclear:
            lines.append(f"No clear difference in {', '.join(unclear)}.")
    if embedding:
        lines.append(embedding_sentence(year, a, b, teammates, embedding))
    lines.extend(notes)
    return "\n".join(lines)


def _embedding_comparison(
    emb: pd.DataFrame,
    pairs: pd.DataFrame | None,
    year: int,
    a: str,
    b: str,
    teams: tuple[str, str],
) -> dict[str, float]:
    in_year = emb[emb["year"] == year]
    picked = []
    for driver, team in ((a, teams[0]), (b, teams[1])):
        mine = in_year[in_year["driver"] == driver]
        exact = mine[mine["team"] == team]
        pick = exact if not exact.empty else mine.sort_values("corners", ascending=False)
        if pick.empty:
            return {}
        picked.append(pick.iloc[0])
    ra, rb = picked
    full = in_year[in_year["events"] >= MIN_EVENTS]
    out = {
        "cos_centroids": cosine(
            np.asarray(ra["centroid"], float), np.asarray(rb["centroid"], float)
        ),
        "cos_style": cosine(np.asarray(ra["style"], float), np.asarray(rb["style"], float)),
        "season_cos_teammate": float(full["cos_teammate"].median()),
        "season_cos_field": float(full["cos_field"].median()),
    }
    if pairs is not None:
        pair = pairs[
            (pairs["year"] == year)
            & (pairs["team"] == teams[0])
            & (pairs["driver"] == a)
            & (pairs["teammate"] == b)
        ]
        if not pair.empty:
            for column in ("style_consistency", "consistency_p", "consistency_null95"):
                out[column] = float(pair[column].iloc[0])
    return out


def _censored_share(corners: pd.DataFrame, diffs: pd.DataFrame, driver: str) -> float:
    """The share of a driver's laps at the compared corners not back on full throttle within
    the window."""
    mine = corners[corners["driver"] == driver].merge(diffs[UNIT].drop_duplicates(), on=UNIT)
    laps = float(mine["own_laps"].sum())
    return float((mine["pickup_censored"] * mine["own_laps"]).sum()) / laps if laps else np.nan


def compare_drivers(
    driver_a: str,
    driver_b: str,
    year: int,
    corners: pd.DataFrame | None = None,
    embeddings: pd.DataFrame | None = None,
    embedding_pairs: pd.DataFrame | None = None,
    root: Path = RESULTS_DIR,
) -> StyleComparison:
    """How driver A takes corners compared with driver B in one season (A minus B).

    Teammates are compared only where they shared a car, and the numbers then match their rows
    in style_profiles.parquet. Other drivers are compared on every corner both took, with a note
    that car and driver are mixed. `corners`, `embeddings` and `embedding_pairs` default to the
    files under `root`. Raises ToolInputError when the season or a driver isn't in the data.
    """
    corners = load_corners(root) if corners is None else corners
    season = corners[corners["year"] == year]
    if season.empty:
        seasons = ", ".join(str(y) for y in sorted(corners["year"].unique()))
        raise ToolInputError(f"No style data for {year}. Seasons: {seasons}.")
    a, b = _driver_code(season, driver_a, year), _driver_code(season, driver_b, year)
    if a == b:
        raise ToolInputError(f"Both sides are {a}. Pick two different drivers.")
    both = season[season["driver"].isin([a, b])]
    shared_car = bool(both.groupby([*SESSION, "team"])["driver"].nunique().eq(2).any())
    diffs = paired_differences(corners, a, b, year, same_team=shared_car)
    if diffs.empty:
        raise ToolInputError(
            f"{a} and {b} never took the same corner in the same {year} session on the same "
            "tyre compound."
        )
    table = summarise_differences(diffs)
    events = int(diffs["round"].nunique())
    teams = (
        str(diffs["team_a"].value_counts().index[0]),
        str(diffs["team_b"].value_counts().index[0]),
    )
    notes = []
    if not shared_car:
        notes.append(
            "Note: they drove different cars, so these differences mix car and driver; only "
            "teammates isolate driving style."
        )
    elif (total := int(both["round"].nunique())) > events:
        notes.append(
            f"Note: they were teammates at {events} of the {total} {year} events either of them "
            "drove; only those are compared."
        )
    if MIN_INTERVAL_EVENTS <= events < MIN_EVENTS:
        notes.append(f"Note: only {events} events, so the intervals are wide.")
    for driver in (a, b):
        share = _censored_share(season, diffs, driver)
        if np.isfinite(share) and share >= 0.1:
            notes.append(
                f"Note: {driver} wasn't back on full throttle within 145 m of the corner on "
                f"{share:.0%} of laps at the corners that own their slowest point; those count "
                "as later than the window."
            )
    if year >= 2026:
        notes.append(
            "Note: in 2026 drivers lift and coast to manage energy, often on team instructions, "
            "so coasting and braking-point differences can be strategy rather than style."
        )
    emb = load_style_embeddings(root) if embeddings is None else embeddings
    pairs = load_embedding_pairs(root) if embedding_pairs is None else embedding_pairs
    embedding = _embedding_comparison(emb, pairs, year, a, b, teams) if emb is not None else {}
    summary = comparison_summary(
        year, a, b, teams, shared_car, len(diffs), events, table, embedding, notes
    )
    return StyleComparison(
        year=year,
        driver_a=a,
        driver_b=b,
        teams=teams,
        teammates=shared_car,
        events=events,
        corners=len(diffs),
        table=table,
        by_event=event_differences(diffs),
        embedding=embedding,
        notes=notes,
        summary=summary,
    )


# --- Checks ----------------------------------------------------------------------------------


def pairs_once(profiles: pd.DataFrame, kind: str = "all", corner_type: str = "all") -> pd.DataFrame:
    """Profile rows of one session kind and corner type, each pair once (driver before teammate
    alphabetically), for pairs that shared a car at MIN_EVENTS events or more."""
    keep = (
        (profiles["kind"] == kind)
        & (profiles["corner_type"] == corner_type)
        & (profiles["driver"] < profiles["teammate"])
        & (profiles["pair_events"] >= MIN_EVENTS)
    )
    return profiles[keep]


def quali_gaps(laps: pd.DataFrame) -> pd.DataFrame:
    """Teammates' qualifying pace per season: the median over sessions of the gap between their
    best push laps over the same number of attempts (matched_attempts; lap time not deleted),
    driver minus teammate, in % of the teammate's lap."""
    quali = laps[laps["session"].isin(QUALI_CODES) & (laps["lap_class"] == "push")]
    quali = quali[matched_attempts(quali) & ~quali["deleted"].eq(True)]
    quali = quali[quali["lap_time_s"].notna()]
    best = quali.groupby([*SESSION, "team", "driver"])["lap_time_s"].min().reset_index()
    both = best.merge(best, on=[*SESSION, "team"], suffixes=("", "_mate"))
    both = both[both["driver"] != both["driver_mate"]]
    both["gap_pct"] = 100 * (both["lap_time_s"] / both["lap_time_s_mate"] - 1)
    gaps = both.groupby(["year", "driver", "driver_mate"])["gap_pct"].median().reset_index()
    return gaps.rename(columns={"driver_mate": "teammate"})


def pace_check(profiles: pd.DataFrame, gaps: pd.DataFrame) -> pd.DataFrame:
    """A sanity check: each pair-season's difference in time through the corner in qualifying
    next to their qualifying gap. Corner windows cover most of a lap, so the two should rank
    pairs alike if the segment times and their alignment add up; it says nothing about the
    braking point or the other metrics."""
    corner_time = pairs_once(profiles, kind="quali")
    corner_time = corner_time[corner_time["metric"] == "segment_time_s"]
    joined = corner_time.merge(gaps, on=["year", "driver", "teammate"])
    return joined[["year", "team", "driver", "teammate", "diff", "gap_pct"]]


def season_stability(profiles: pd.DataFrame) -> pd.DataFrame:
    """For pairs who stayed teammates into the next season: how well each metric's difference
    in one season predicts the next (Pearson r, and the share with the same sign)."""
    now = pairs_once(profiles)
    later = now.assign(year=now["year"] - 1)
    both = now.merge(later, on=["year", "driver", "teammate", "metric"], suffixes=("", "_next"))
    rows = []
    for metric in METRICS:
        m = both[both["metric"] == metric.name]
        r = m["diff"].corr(m["diff_next"]) if len(m) > 2 else float("nan")
        same = float((np.sign(m["diff"]) == np.sign(m["diff_next"])).mean()) if len(m) else np.nan
        rows.append({"metric": metric.name, "pairs": len(m), "r": r, "same_sign": same})
    return pd.DataFrame(rows)


def pace_links(profiles: pd.DataFrame) -> pd.DataFrame:
    """How each metric's teammate difference goes with the difference in time through the
    corner, across pairs: one correlation per season and metric (negative: more of it goes
    with being quicker)."""
    once = pairs_once(profiles)
    wide = once.pivot_table(index=["year", "driver", "teammate"], columns="metric", values="diff")
    wide = wide.reset_index()
    rows = []
    for year in sorted(wide["year"].unique()):
        part = wide[wide["year"] == year]
        corr = {
            m.name: part[m.name].corr(part["segment_time_s"])
            if len(part) > 2 and m.name in part and "segment_time_s" in part
            else np.nan
            for m in METRICS
            if m.name != "segment_time_s"
        }
        rows.append({"year": int(year), "pairs": len(part), **corr})
    return pd.DataFrame(rows)


# --- Running it ------------------------------------------------------------------------------


@dataclass(frozen=True)
class StyleTables:
    segments: int  # clean segments used
    types: pd.DataFrame  # corner_types
    corners: pd.DataFrame  # corner_medians
    profiles: pd.DataFrame
    events: pd.DataFrame
    chance: pd.DataFrame  # chance_rates
    embeddings: pd.DataFrame | None
    embedding_pairs: pd.DataFrame | None
    axes: pd.DataFrame | None  # style_axes
    coasting: pd.DataFrame  # mean coasting per corner (m), season x session kind


def build_style(
    frame: pd.DataFrame,
    ids: np.ndarray | None = None,
    embeddings: np.ndarray | None = None,
    chance_reps: int = CHANCE_REPS,
    null_reps: int = NULL_REPS,
) -> StyleTables:
    """Every table from the eval frame, with decel_entry (and the segment embeddings, when
    given)."""
    clean = clean_segments(frame)
    types = corner_types(clean)
    corners = corner_medians(clean, types)
    log.info("%d clean segments, %d driver-corner medians", len(clean), len(corners))
    profiles, events = build_profiles(corners)
    chance = chance_rates(corners, chance_reps)
    emb_table = emb_pairs = axes = None
    if ids is not None and embeddings is not None:
        pos = pd.Index(ids).get_indexer(pd.Index(clean["segment_id"]))
        has = pos >= 0
        if not has.all():
            log.warning("%d clean segments have no embedding", int((~has).sum()))
        emb_table, emb_pairs = embedding_profiles(
            clean[has].reset_index(drop=True), embeddings[pos[has]], null_reps=null_reps
        )
        axes = style_axes(emb_table, profiles)
    coasting = clean.groupby(["year", "kind"])["coast_m"].mean().unstack()
    return StyleTables(
        len(clean), types, corners, profiles, events, chance, emb_table, emb_pairs, axes, coasting
    )


def write_tables(
    tables: StyleTables, root: Path = RESULTS_DIR, run_id: str | None = None
) -> list[Path]:
    """Write every table, recording the scoring run `run_id` it was built from. The file of a
    table this run doesn't have (no embeddings) is removed, not left from an earlier run."""
    root.mkdir(parents=True, exist_ok=True)
    named = {
        "style_corners": tables.corners,
        "style_profiles": tables.profiles,
        "style_events": tables.events,
        "style_embeddings": tables.embeddings,
        "style_embedding_pairs": tables.embedding_pairs,
        "style_axes": tables.axes,
    }
    paths = []
    for name, table in named.items():
        path = root / f"{name}.parquet"
        if table is None:
            path.unlink(missing_ok=True)
            continue
        write_parquet(table, path, run_id)
        paths.append(path)
    return paths


def _cell(row: Mapping[Hashable, Any], digits: int) -> str:
    if not (np.isfinite(row["lo"]) and np.isfinite(row["hi"])):
        return f"{row['diff']:+.{digits}f} [n/a]"
    text = f"{row['diff']:+.{digits}f} [{row['lo']:+.{digits}f}, {row['hi']:+.{digits}f}]"
    return f"**{text}**" if row["clear"] else text


def season_table(profiles: pd.DataFrame, year: int) -> pd.DataFrame:
    """Every teammate pair of a season on every metric (all sessions and corners), for the
    report: A minus B [95% interval], bold where the interval excludes zero."""
    once = profiles[
        (profiles["year"] == year)
        & (profiles["kind"] == "all")
        & (profiles["corner_type"] == "all")
        & (profiles["driver"] < profiles["teammate"])
    ]
    rows = []
    for (driver, mate), part in once.groupby(["driver", "teammate"], sort=True):
        cells = {r["metric"]: r for r in part.to_dict("records")}
        row = {
            "A - B": f"{driver} - {mate}",
            "team": str(part["team"].iloc[0]),
            "events": str(int(part["pair_events"].iloc[0])),
        }
        for m in METRICS:
            row[f"{m.label} ({m.unit})"] = _cell(cells[m.name], m.digits)
        rows.append(row)
    return pd.DataFrame(rows)


def pair_highlights(profiles: pd.DataFrame, year: int, per_pair: int = 2) -> list[str]:
    """For each pair of a season, its strongest clear differences in words, pairs with the
    most reliable difference first."""
    once = pairs_once(profiles)
    once = once[(once["year"] == year) & once["clear"]]
    lines = []
    ranked = once.sort_values("p").groupby(["driver", "teammate"], sort=False)
    for (driver, mate), part in ranked:
        pair = profiles[
            (profiles["year"] == year)
            & (profiles["driver"] == driver)
            & (profiles["teammate"] == mate)
        ]
        texts = [
            f"{describe(row, str(driver), str(mate))} ({breakdown(pair, str(row['metric']))})"
            for _, row in part.head(per_pair).iterrows()
        ]
        lines.append(
            f"- **{driver} vs {mate}** ({part['team'].iloc[0]}): " + "; ".join(texts) + "."
        )
    return lines


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}" if np.isfinite(value) else "n/a"


def _pct(value: float) -> str:
    return f"{value:.0%}" if np.isfinite(value) else "n/a"


def _share(values: pd.Series) -> float:
    return float(values.mean()) if len(values) else float("nan")


def _weighted(frame: pd.DataFrame, value: str, weight: str) -> float:
    total = float(frame[weight].sum())
    return float((frame[value] * frame[weight]).sum()) / total if total else float("nan")


def write_report(
    tables: StyleTables, pace: pd.DataFrame, example: StyleComparison | None, path: Path
) -> Path:
    profiles, types, corners = tables.profiles, tables.types, tables.corners
    overall = pairs_once(profiles)
    latest = int(profiles["year"].max())
    counts = types["corner_type"].value_counts()
    n_pairs = profiles[["year", "driver", "teammate"]].drop_duplicates().shape[0] // 2
    n_full = overall[["year", "driver", "teammate"]].drop_duplicates().shape[0]

    # What chance alone would call clear: the no-difference worlds of chance_rates.
    chance = tables.chance
    reps = int(chance["rep"].nunique()) if len(chance) else 0
    chance_overall = chance[(chance["kind"] == "all") & (chance["corner_type"] == "all")]
    chance_full = chance_overall[chance_overall["pair_events"] >= MIN_EVENTS]
    with_interval = chance[chance["events"] >= MIN_INTERVAL_EVENTS]
    chance_cells = _share(with_interval["clear"])
    chance_few = _share(with_interval.loc[with_interval["events"] < 8, "clear"])
    chance_many = _share(with_interval.loc[with_interval["events"] >= 8, "clear"])
    pair_key = ["rep", "year", "team", "driver", "teammate"]
    chance_any = (
        float(chance_full.groupby(pair_key)["clear"].any().mean()) if len(chance_full) else np.nan
    )

    field_rows, shares, sizes, by_chance = [], {}, {}, {}
    for m in METRICS:
        part = overall[overall["metric"] == m.name]
        shares[m.name] = _share(part["clear"])
        by_chance[m.name] = _share(chance_full.loc[chance_full["metric"] == m.name, "clear"])
        sizes[m.name] = float(part["diff"].abs().median()) if len(part) else np.nan
        field_rows.append(
            {
                "metric": f"{m.label} ({m.unit})",
                "positive means": m.more,
                "pair-seasons with a clear difference": _pct(shares[m.name]),
                "by chance": _pct(by_chance[m.name]),
                "median size": _fmt(sizes[m.name], m.digits),
                "median standard error": _fmt(float(part["se"].median()), m.digits),
            }
        )
    any_clear = float(overall.groupby(["year", "driver", "teammate"])["clear"].any().mean())
    rho = pace["diff"].corr(pace["gap_pct"], method="spearman") if len(pace) > 2 else np.nan
    agree = (
        float((np.sign(pace["diff"]) == np.sign(pace["gap_pct"])).mean()) if len(pace) else np.nan
    )

    # How the lap filters bite, from the corner medians.
    owned = corners[corners["owns_min"].astype(bool)]
    fast = owned[owned["corner_type"] == "fast"]
    off_min_fast = 1 - float(fast["own_laps"].sum()) / max(float(fast["laps"].sum()), 1.0)
    slow = owned[owned["corner_type"] == "slow"]
    censored_slow = _weighted(slow, "pickup_censored", "own_laps")
    censored_all = _weighted(owned, "pickup_censored", "own_laps")
    now_owned = owned[owned["year"] == latest].assign(
        censored=lambda t: t["pickup_censored"].fillna(0) * t["own_laps"]
    )
    by_driver = now_owned.groupby(["driver", "team"]).agg(
        censored=("censored", "sum"), own_laps=("own_laps", "sum"), events=("round", "nunique")
    )
    by_driver = by_driver[by_driver["events"] >= MIN_EVENTS]
    by_driver = (by_driver["censored"] / by_driver["own_laps"]).dropna()
    names = by_driver.index.get_level_values("driver")
    least_censored = str(names[by_driver.to_numpy().argmin()]) if len(by_driver) else ""
    most_censored = str(names[by_driver.to_numpy().argmax()]) if len(by_driver) else ""

    stability = season_stability(profiles)
    stable = stability.assign(
        metric=[METRIC_BY_NAME[m].label for m in stability["metric"]],
        r=[_fmt(v) for v in stability["r"]],
        same_sign=[_pct(v) for v in stability["same_sign"]],
    )
    ranked = stability.dropna(subset=["r"]).sort_values("r", ascending=False)
    if len(ranked) >= 2:
        most, least = ranked.iloc[0], ranked.iloc[-1]
        persists = (
            f"- **Style persists.** For the {int(most['pairs'])} pairs who stayed teammates into "
            "the next season, one season's difference predicts the next: r = "
            f"{most['r']:.2f} for the {METRIC_BY_NAME[str(most['metric'])].label} (the most "
            f"stable) down to {least['r']:.2f} for the "
            f"{METRIC_BY_NAME[str(least['metric'])].label}."
        )
    else:
        persists = "- Too few pairs stayed together across seasons to check stability."

    links = pace_links(profiles)
    link_metrics = [m for m in METRICS if m.name != "segment_time_s"]
    link_table = pd.DataFrame(
        {
            "metric": [m.label for m in link_metrics],
            **{
                str(int(row["year"])): [_fmt(float(row[m.name])) for m in link_metrics]
                for row in links.to_dict("records")
            },
        }
    )
    earlier = links[links["year"] < latest]
    now = links[links["year"] == latest]
    pace_lines = []
    med = {
        m.name: float(links[m.name].dropna().median()) if links[m.name].notna().any() else np.nan
        for m in link_metrics
        if m.name in links
    }
    if any(np.isfinite(v) for v in med.values()):
        top = max(med, key=lambda k: abs(med[k]) if np.isfinite(med[k]) else -1)
        pace_lines.append(
            f"- **What goes with pace.** Across pairs, the difference most tied to time through "
            f"the corner is the {METRIC_BY_NAME[top].label} (median r over seasons "
            f"{med[top]:+.2f}; negative: more of it, quicker)."
        )
        if len(now) and len(earlier):
            r_now = float(now["coast_m"].iloc[0])
            lo, hi = float(earlier["coast_m"].min()), float(earlier["coast_m"].max())
            pace_lines[-1] += (
                f" Coasting correlates {r_now:+.2f} with the time difference in {latest} "
                f"({int(now['pairs'].iloc[0])} pairs), against {lo:+.2f} to {hi:+.2f} in "
                "earlier seasons"
            )
            if np.isfinite(r_now) and r_now < min(lo, 0):
                pace_lines[-1] += (
                    ": with the new cars the teammate who coasts more tends to be the quicker "
                    "one, consistent with energy management being part of pace. With this few "
                    "pairs it is a hint, not a finding."
                )
            else:
                pace_lines[-1] += "."

    coasting = tables.coasting
    coast_line = ""
    if {latest, latest - 1} <= set(coasting.index) and "quali" in coasting:
        coast_line = (
            "Even in qualifying, where nothing is saved for later laps, coasting per corner was "
            f"{coasting.loc[latest, 'quali']:.1f} m in {latest} against "
            f"{coasting.loc[latest - 1, 'quali']:.1f} m in {latest - 1} (table): the cars "
            "harvest energy within the lap. "
        )
    latest_pairs = overall[overall["year"] == latest]
    coast_share = _share(latest_pairs.loc[latest_pairs["metric"] == "coast_m", "clear"])
    coast_table = (
        coasting.round(1)
        .reset_index()
        .rename(columns={"quali": "qualifying (m per corner)", "race": "races (m per corner)"})
    )

    parts = [
        "# M4: driving-style profiles",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by "
        "`uv run python -m race_engineer.inference.style`._",
        "Teammates share a car, so the difference between two teammates on the same corner, in "
        "the same session, on the same tyre compound is the cleanest measure of driving style "
        "this data allows. Each driver's median over their clean laps is taken per corner, the "
        "teammates' medians are subtracted, and the differences are averaged over the season. "
        "Clean laps are push laps in qualifying, matched by attempt (each driver's first k "
        "push laps of a session, k being the teammate's count, so the one who reached Q3 "
        f"doesn't add laps on a faster track and new tyres), and green-flag race laps started "
        f"at least {FREE_AIR_S:.0f} s behind the car ahead; lap time not deleted and tyre "
        "compound known. A weekend's corners share track, weather and set-up, so the evidence "
        "is in how much a difference varies between weekends: the 95% interval is a t-interval "
        "on the event-clustered standard error with events - 1 degrees of freedom, and there "
        f"is none below {MIN_INTERVAL_EVENTS} events. A difference is **clear** when its "
        "interval excludes zero.",
        f"**How often chance says clear.** Every pair's differences were centred and given a "
        f"random sign per weekend ({reps} times per pair: the pair's own noise, no difference) "
        f"and summarised again: {_pct(chance_cells)} of cells come out clear by chance "
        f"({_pct(chance_few)} with 4 to 7 events, {_pct(chance_many)} with 8 or more), close "
        "to the nominal 5%. Across the seven metrics, a pair-season with no real difference "
        f"still shows at least one clear one {_pct(chance_any)} of the time, and the breakdowns "
        "by corner type and session multiply the chances: read single starred cells with that "
        "in mind.",
        f"**Data:** {tables.segments:,} clean segments, {len(corners):,} driver-corner "
        f"medians, {n_pairs} teammate pairs over {profiles['year'].nunique()} seasons, {n_full} "
        f"of them sharing a car at {MIN_EVENTS}+ events (the rest are substitutes and "
        "mid-season swaps: kept in the tables, left out of the rankings and checks). "
        f"**Corner types** come from each corner's speed over the event: {counts.get('slow', 0)} "
        f"slow (< {SLOW_KPH:.0f} km/h), {counts.get('medium', 0)} medium and "
        f"{counts.get('fast', 0)} fast (> {FAST_KPH:.0f} km/h) event-corners. Corner windows "
        "(250 m before to 150 m after the marker) overlap, so braking point, peak braking "
        "deceleration, minimum speed and the full-throttle point are counted only at the corner "
        f"that owns the window's slowest point ({types['owns_min'].mean():.0%} of corners; the "
        "rest are flat kinks and parts of complexes whose slowest point belongs to a "
        "neighbour), and only from laps whose own slowest point is within "
        f"{LAP_MIN_M:.0f} m of the corner's ({_pct(off_min_fast)} of laps at fast owned corners "
        "are left out: their minimum is at the window's start or the next corner). Peak "
        "braking deceleration is the peak before the lap's slowest point, from the speed trace "
        "(the whole-window peak is often the braking for the next corner), without values "
        f"above {DECEL_SPIKE:.0f} m/s², which are sampling spikes. A lap not back on full "
        "throttle within the window counts as later than any that is "
        f"({_pct(censored_all)} of laps at owned corners, {_pct(censored_slow)} at slow ones); "
        "a driver's median at a corner is kept while fewer than half their laps are like that. "
        "Coasting, exit speed and time through the corner count everywhere.",
        "## What the profiles say",
        f"- **Teammates drive measurably differently.** In {_pct(any_clear)} of pair-seasons at "
        f"least one of the seven metrics differs clearly, against {_pct(chance_any)} by chance; "
        f"per metric the share is {_pct(min(shares.values()))} to "
        f"{_pct(max(shares.values()))}, against {_pct(min(by_chance.values()))} to "
        f"{_pct(max(by_chance.values()))} by chance. The differences are small: median sizes of "
        f"{_fmt(sizes['brake_start_d'], 1)} m of braking point, {_fmt(sizes['v_min'], 1)} km/h "
        f"of minimum speed and {_fmt(sizes['segment_time_s'], 3)} s per corner.",
        "- **Sanity check.** The difference in time through the corner in qualifying ranks the "
        "pairs like their qualifying gap (median over sessions of the gap between their best "
        f"push laps over the same attempts): Spearman {_fmt(rho)} over {len(pace)} "
        f"pair-seasons, same sign in {_pct(agree)}. The corner windows cover most of a lap, so "
        "this mainly confirms that segment times and their alignment add up; it doesn't "
        "validate the braking point or the other metrics.",
        persists,
        *pace_lines,
        markdown_table(pd.DataFrame(field_rows)),
        "Season to season, for pairs who stayed together (r between one season's difference "
        "and the next's):",
        markdown_table(stable[["metric", "pairs", "r", "same_sign"]]),
        "Correlation across pairs between each difference and the difference in time through "
        "the corner, per season (pairs with at least "
        f"{MIN_EVENTS} events):",
        markdown_table(link_table),
        f"## {latest} teammates",
        "All sessions and corners, A minus B [95% interval]; bold where clear. Signs: braking "
        "point positive = A brakes later; full-throttle point positive = A is on full throttle "
        f"later; time positive = A is slower. Pairs with fewer than {MIN_INTERVAL_EVENTS} "
        "events (mid-season swaps) get no interval (n/a) and nothing is called clear for them.",
        markdown_table(season_table(profiles, latest)),
        "The strongest and most reliable differences, per pair (in brackets: A minus B by "
        "corner type and by session, * = clear):",
        "\n".join(pair_highlights(profiles, latest)),
    ]
    if example is not None:
        parts += [
            f"What a tool gets from `compare_drivers('{example.driver_a}', "
            f"'{example.driver_b}', {example.year})`:",
            "```text\n" + example.summary + "\n```",
        ]
    emb = tables.embeddings
    if emb is not None:
        full = emb[emb["events"] >= MIN_EVENTS]
        tested = full[full["consistency_p"].notna()]
        by_year = full.groupby("year").agg(
            driver_seasons=("driver", "size"),
            cos_teammate=("cos_teammate", "median"),
            cos_field=("cos_field", "median"),
            teammate_nearest=("teammate_nearest", "mean"),
            style_consistency=("style_consistency", "median"),
            noise_95th=("consistency_null95", "median"),
        )
        consistent = tested.groupby("year")["consistent"].mean()
        by_year = by_year.reset_index().assign(
            teammate_nearest=lambda t: [_pct(v) for v in t["teammate_nearest"]],
            consistent=lambda t: [_pct(float(consistent.get(y, np.nan))) for y in t["year"]],
        )
        parts += [
            "## Style embedding",
            "Each segment's 64-dim [CLS] embedding is standardised per dimension and averaged "
            "per driver and corner. A driver's **centroid** is their mean difference from the "
            "field on the same corners (car and driver together); their **style vector** is "
            "their mean difference from their teammate on shared corners (the driver alone, "
            "relative to that teammate).",
            f"**The embedding is mostly the car.** Teammates' centroids have a median cosine of "
            f"{_fmt(float(full['cos_teammate'].median()))}, drivers of different teams "
            f"{_fmt(float(full['cos_field'].median()))}, and the nearest driver is the teammate "
            f"for {_pct(float(full['teammate_nearest'].mean()))} of driver-seasons. (The plain "
            f"mean embeddings say the same: {_fmt(float(full['raw_cos_teammate'].median()))} "
            f"against {_fmt(float(full['raw_cos_field'].median()))}.) That is why style here is "
            "the within-team difference, not the centroid.",
            "**The within-team difference holds its direction for about half the "
            "driver-seasons.** Its cosine between the odd and the even rounds of a season "
            "(`style_consistency`, per teammate pair; a driver-season shows its main teammate) "
            f"has a median of {_fmt(float(tested['style_consistency'].median()))}. Noise alone "
            "gives wide cosines here, because the teammate differences vary along only about "
            f"{_fmt(float(emb.attrs.get('style_dims', np.nan)), 0)} independent directions "
            "(participation ratio), not 64, so the reference is measured per pair: each "
            "event's deviation from the pair's mean difference gets a random sign "
            f"({NULL_REPS:,} times), keeping the noise and removing any steady direction. The "
            "95th percentile of those cosines is about "
            f"{_fmt(float(tested['consistency_null95'].median()))} (median over driver-seasons), "
            f"and {_pct(_share(tested['consistent']))} of driver-seasons beat theirs "
            "(`consistent`, p < 0.05).",
            markdown_table(by_year),
        ]
        axes = tables.axes
        explained = emb.attrs.get("style_explained", [np.nan, np.nan])
        if axes is not None and len(axes):
            ranked_axes = axes.sort_values("R", ascending=False)
            held = [
                f"{METRIC_BY_NAME[str(r['metric'])].label} (R = {r['R']:.2f}; "
                f"{r['R_lo']:.2f} to {r['R_hi']:.2f})"
                for r in ranked_axes.to_dict("records")
                if r["R"] >= 0.5
            ]
            axis_rows = [
                {
                    "metric": METRIC_BY_NAME[str(r["metric"])].label,
                    "r_pc1": _fmt(float(r["r_pc1"])),
                    "r_pc2": _fmt(float(r["r_pc2"])),
                    "R (plane)": _fmt(float(r["R"])),
                    "R, 5-95% over resamples": f"{_fmt(float(r['R_lo']))} to "
                    f"{_fmt(float(r['R_hi']))}",
                }
                for r in axes.to_dict("records")
            ]
            parts += [
                "**What the style map means.** The two principal axes of the style vectors "
                f"(`style_pc1/2`) explain {_fmt(100 * explained[0], 0)}% and "
                f"{_fmt(100 * explained[1], 0)}% of their variance: so nearly the same that "
                "which comes first, and how the two are turned within their plane, isn't "
                "stable from one sample of teams to another, and neither axis should be "
                "labelled on its own. The plane as a whole is readable. Over the "
                f"{int(axes['pairs'].max())} teammate pairs where at least one side had no "
                "other teammate that season (each pair once; correlations through the origin, "
                "since a difference has no natural sign), it holds "
                + (", ".join(held) if held else "no single metric well (every R < 0.5)")
                + " (R: multiple correlation with the plane; the range is over 200 refits on "
                "resampled teams). To label a chart, draw each metric as an arrow along "
                "(r_pc1, r_pc2) (`style_axes.parquet`).",
                markdown_table(pd.DataFrame(axis_rows)),
            ]
        latest_emb = emb[emb["year"] == latest].sort_values(["team", "driver"])
        parts += [
            f"{latest} driver-seasons (`style_consistency` against the main teammate, with its "
            "p-value against noise; `style_twin`: the driver of another team whose difference "
            "from their own teammate points most the same way; teammates mirror each other, "
            "since one's style vector is minus the other's):",
            markdown_table(
                pd.DataFrame(
                    {
                        "driver": latest_emb["driver"],
                        "team": latest_emb["team"],
                        "teammates": latest_emb["teammates"],
                        "events": latest_emb["events"].astype(int),
                        "cos_teammate": [_fmt(v) for v in latest_emb["cos_teammate"]],
                        "cos_field": [_fmt(v) for v in latest_emb["cos_field"]],
                        "style_consistency": [_fmt(v) for v in latest_emb["style_consistency"]],
                        "p": [_fmt(v, 3) for v in latest_emb["consistency_p"]],
                        "style_twin": [
                            f"{t} ({_fmt(c)})" if t else ""
                            for t, c in zip(
                                latest_emb["style_twin"], latest_emb["style_twin_cos"], strict=True
                            )
                        ],
                    }
                )
            ),
        ]
    parts += [
        "## Caveats",
        "- **Sampling.** The car data comes at about 4 Hz, roughly 20 m apart at 300 km/h, and "
        "brake is on/off. A single lap's braking point is only good to a sample; the medians "
        "and season means get below that, but differences of a metre or two are at the limit. "
        "Qualifying gives few laps per corner, so its medians are noisier than the races'.",
        "- **Smoothed position feed.** Lateral offsets are about 0.5 m in every season, far less "
        "than real differences between lines, so the line through a corner isn't compared at "
        "all. Running wide shows up as a lift or a lower exit speed instead.",
        f"- **{latest} energy management.** Drivers lift and coast to recharge, often on team "
        "instructions, so coasting and braking-point differences can be strategy, not style. "
        f"{coast_line}Coasting is a clear difference for {_pct(coast_share)} of {latest} pairs. "
        f"Read {latest} coasting differences as how the pair managed energy until radio or "
        "deployment data can separate the two.",
        markdown_table(coast_table),
        "- **Full-throttle point.** Laps not back on full throttle within the window count as "
        "later than it, so the metric is a median over partly censored laps; in "
        f"{latest} the censored share at owned corners runs from "
        f"{_pct(float(by_driver.min()) if len(by_driver) else np.nan)} ({least_censored}) to "
        f"{_pct(float(by_driver.max()) if len(by_driver) else np.nan)} ({most_censored}) over "
        f"driver-seasons with {MIN_EVENTS}+ events. "
        "`pickup_censored` in style_corners.parquet has it per corner.",
        "- **Race conditions.** Race laps are paired on session, corner and compound, but not on "
        "tyre age, fuel or strategy, and the free-air filter only knows the gap at the timing "
        "line. Qualifying laps are matched by attempt, not by run or tyre age. The session-kind "
        "breakdown in the tables separates qualifying from races.",
        "- **Relative, not absolute.** Every profile is against one teammate: a driver's numbers "
        "change when the teammate does, and drivers of different teams can't be ranked from "
        "them. Chaining teammates across seasons (a teammate network) would be the way to "
        "absolute ratings.",
        "- **Few events.** Substitutes and mid-season swaps have one to three events with a "
        f"teammate: below {MIN_INTERVAL_EVENTS} events there is no interval and nothing is "
        "called clear, and with four or five the intervals are wide (`events` and "
        "`pair_events` say how many).",
        "## Outputs",
        "Under `data/results/`: `style_corners.parquet` (each driver's medians per corner, "
        "session and compound, with corner type, `own_laps` and `pickup_censored`: what "
        "`compare_drivers` pairs), `style_profiles.parquet` (one row per pair, both ways round, "
        "session kind (all, quali, race), corner type (all, slow, medium, fast) and metric: "
        "`diff` = driver minus teammate, `lo`/`hi` 95% interval, `se`, `p`, `corners`, "
        "`events`, `same_sign`, `clear`), `style_events.parquet` (the same per event), "
        "`style_embeddings.parquet` (one row per driver, season and team: cosines, "
        "`style_consistency` and its `consistency_p`, `style_twin`, the 2-D projections "
        "`car_pc1/2` and `style_pc1/2` for charts, and the 64-dim `centroid` and `style` "
        "vectors), `style_embedding_pairs.parquet` (consistency per teammate pair) and "
        "`style_axes.parquet` (how to label the style map). "
        "`race_engineer.inference.style.compare_drivers(a, b, year)` compares any two drivers "
        "of a season and returns the table and a summary for a tool to relay.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(parts) + "\n")
    return path


def run_style(
    root: Path = RESULTS_DIR,
    report: Path | None = REPORT_DIR / "m4_style.md",
    chance_reps: int = CHANCE_REPS,
) -> StyleTables:
    """Build every style table from the eval frame, the segments' speed traces and the M4
    embeddings, write them and the report. Raises StaleResultsError unless the files in `root`
    are a complete scoring run of this eval frame."""
    from race_engineer.data.store import connect
    from race_engineer.evaluation.data import load_eval_frame
    from race_engineer.inference.score import check_scored_frame, load_embeddings

    started = time.time()
    # The scores stand for the embeddings too: `score` writes both before the manifest.
    check_current(root / "segment_scores.parquet", "score", root)
    run_id = scoring_run(root)
    frame = load_eval_frame()[FRAME_COLUMNS]
    check_scored_frame(frame, root)
    frame = frame.assign(decel_entry=load_entry_decel(frame))
    log.info("peak braking deceleration from the segments after %.0f s", time.time() - started)
    ids, embeddings = load_embeddings(root)
    missing = int((~frame["segment_id"].isin(ids)).sum())
    if missing:
        raise StaleResultsError(
            f"{missing:,} segments of the eval frame have no embedding in {root}/embeddings.npz: "
            "run `race-engineer-infer score` (or `make m4`)."
        )
    tables = build_style(frame, ids, embeddings, chance_reps)
    del frame, embeddings
    for path in write_tables(tables, root, run_id):
        log.info("wrote %s", path)
    if report is not None:
        laps = (
            connect()
            .sql(
                "select year, round, session, team, driver, lap_number, lap_time_s, lap_class, "
                "deleted from laps where session in ('Q', 'SQ', 'SS')"
            )
            .df()
        )
        pace = pace_check(tables.profiles, quali_gaps(laps))
        latest = int(tables.profiles["year"].max())
        top = pairs_once(tables.profiles)
        top = top[(top["year"] == latest) & top["clear"]]
        example = None
        if not top.empty:
            best = top.loc[top["p"].idxmin()]
            example = compare_drivers(
                str(best["driver"]), str(best["teammate"]), latest, tables.corners,
                tables.embeddings, tables.embedding_pairs,
            )  # fmt: skip
        write_report(tables, pace, example, report)
        log.info("wrote %s", report)
    log.info("style profiles done in %.0f s", time.time() - started)
    return tables


def main(argv: Sequence[str] | None = None, prog: str | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog=prog or "python -m race_engineer.inference.style",
        description="Driving-style profiles: every driver against their teammate.",
    )
    parser.add_argument(
        "--chance-reps",
        type=int,
        default=CHANCE_REPS,
        help="no-difference replicates per pair for the false-alarm rate",
    )
    parser.add_argument("--no-report", action="store_true", help="write the tables only")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", force=True
    )
    try:
        run_style(
            report=None if args.no_report else REPORT_DIR / "m4_style.md",
            chance_reps=args.chance_reps,
        )
    except StaleResultsError as err:
        raise SystemExit(f"error: {err}") from None


if __name__ == "__main__":
    main()
