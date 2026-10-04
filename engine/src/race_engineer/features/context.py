"""Lap-level context: what kind of lap it was, traffic, and the weather at the time."""

from __future__ import annotations

import numpy as np
import pandas as pd

from race_engineer.features.grid import to_seconds

# Lap classes. Only "push" (qualifying) and "race" laps are clean enough to model directly.
CLEAN_CLASSES = frozenset({"push", "race"})

SLOW_FACTOR_QUALI = 1.07  # slower than 107% of the session's best: a prep or cool-down lap
SLOW_FACTOR_RACE = 1.10  # slower than 110% of the driver's median green lap


def classify_laps(laps: pd.DataFrame, quali: bool) -> pd.Series:
    """One of: push, race, start, slow, yellow, neutralised, in, out, no_time."""
    status = laps["TrackStatus"].fillna("").astype(str)
    lap_time = to_seconds(laps["LapTime"])
    cls = pd.Series("push" if quali else "race", index=laps.index, dtype=object)

    if quali:
        # The session's best from real flying laps: pit laps and laps FastF1 marks as
        # inaccurate can carry impossible times (a 73.7 s pit-out lap in Jeddah 2022, where
        # pole was 88.2 s), which would make every genuine lap look slow.
        flying = laps["PitInTime"].isna() & laps["PitOutTime"].isna()
        if "IsAccurate" in laps:
            accurate = flying & laps["IsAccurate"].fillna(False).astype(bool)
            flying = accurate if accurate.any() else flying
        reference = np.where(flying, lap_time, np.nan) if flying.any() else lap_time
        slow = lap_time > SLOW_FACTOR_QUALI * np.nanmin(reference)
    else:
        green = status.eq("1") & laps["PitInTime"].isna() & laps["PitOutTime"].isna()
        median = (
            pd.Series(np.where(green, lap_time, np.nan), index=laps.index)
            .groupby(laps["Driver"])
            .transform("median")
        )
        slow = lap_time > SLOW_FACTOR_RACE * median.to_numpy()

    cls[slow] = "slow"
    if not quali:
        cls[laps["LapNumber"].eq(1)] = "start"  # a standing start is slow by nature, not a mistake
    cls[status.str.contains("2")] = "yellow"
    cls[status.str.contains("[4567]")] = "neutralised"  # safety car, red flag, virtual SC
    cls[laps["PitOutTime"].notna()] = "out"
    cls[laps["PitInTime"].notna()] = "in"
    cls[np.isnan(lap_time)] = "no_time"
    return cls


def gap_ahead_s(laps: pd.DataFrame) -> pd.Series:
    """Seconds to the previous car crossing the line at the start of each lap (any lap count).

    A lap-level traffic proxy: under ~1.5 s means the driver started the lap in another car's
    dirty air.
    """
    start = to_seconds(laps["LapStartTime"])
    order = np.argsort(start, kind="stable")
    drivers = laps["Driver"].to_numpy()[order]
    gaps = np.full(len(laps), np.nan)
    sorted_start = start[order]
    prev_gap = np.diff(sorted_start, prepend=np.nan)
    same_driver = np.concatenate([[False], drivers[1:] == drivers[:-1]])
    prev_gap[same_driver] = np.nan
    gaps[order] = prev_gap
    return pd.Series(gaps, index=laps.index)


def attach_weather(laps: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Nearest weather reading at each lap's start."""
    cols = ["AirTemp", "TrackTemp", "Humidity", "Rainfall", "WindSpeed"]
    if weather is None or weather.empty:
        return pd.DataFrame({c: np.nan for c in cols}, index=laps.index)
    w = weather.assign(_t=to_seconds(weather["Time"])).dropna(subset=["_t"]).sort_values("_t")
    left = pd.DataFrame({"_t": to_seconds(laps["LapStartTime"]), "_i": laps.index})
    left = left.dropna(subset=["_t"]).sort_values("_t")
    merged = pd.merge_asof(left, w[["_t", *cols]], on="_t", direction="nearest")
    return pd.DataFrame(merged.set_index("_i").reindex(laps.index), columns=pd.Index(cols))
