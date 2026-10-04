import numpy as np
import pandas as pd

from race_engineer.features.context import classify_laps, gap_ahead_s


def quali_laps() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Driver": ["VER"] * 6,
            "LapNumber": [1, 2, 3, 4, 5, 6],
            "LapTime": pd.to_timedelta(
                pd.Series([None, "0:01:19", "0:01:45", "0:01:19.5", "0:01:20", "0:01:22"])
            ),
            "TrackStatus": ["1", "1", "1", "12", "4", "1"],
            "PitInTime": pd.to_timedelta(pd.Series([None, None, None, None, None, "0:40:00"])),
            "PitOutTime": pd.to_timedelta(pd.Series(["0:30:00", None, None, None, None, None])),
        }
    )


def test_quali_lap_classes() -> None:
    cls = classify_laps(quali_laps(), quali=True).tolist()
    assert cls == ["no_time", "push", "slow", "yellow", "neutralised", "in"]


def test_gap_to_the_car_ahead_at_the_line() -> None:
    laps = pd.DataFrame(
        {
            "Driver": ["A", "B", "C", "A"],
            "LapStartTime": pd.to_timedelta(
                pd.Series(["0:10:00", "0:10:01.2", "0:10:05", "0:11:30"])
            ),
        }
    )
    gaps = gap_ahead_s(laps).to_numpy()
    assert np.isnan(gaps[0])
    assert np.allclose(gaps[1:], [1.2, 3.8, 85.0])


def test_a_race_start_stays_a_start_even_though_it_is_slow() -> None:
    laps = pd.DataFrame(
        {
            "Driver": ["VER"] * 4,
            "LapNumber": [1, 2, 3, 4],
            "LapTime": pd.to_timedelta(pd.Series(["0:01:45", "0:01:30", "0:01:30.2", "0:01:30.4"])),
            "TrackStatus": ["1"] * 4,
            "PitInTime": pd.to_timedelta(pd.Series([None] * 4)),
            "PitOutTime": pd.to_timedelta(pd.Series([None] * 4)),
        }
    )
    assert classify_laps(laps, quali=False).tolist() == ["start", "race", "race", "race"]
