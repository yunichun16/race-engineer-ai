import pandas as pd

from race_engineer.data.labels import incident_events, mark_clusters, track_limit_events


def laps_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Driver": ["NOR"] * 3 + ["SAI"] * 2,
            "DriverNumber": ["4"] * 3 + ["55"] * 2,
            "LapNumber": [5, 9, 12, 1, 2],
            "LapTime": pd.to_timedelta(
                ["0:01:40.393", "0:01:19.9", "0:01:20.1", "0:01:30", "0:01:20.470"]
            ),
            "Deleted": [True, False, False, False, False],
            "DeletedReason": ["TRACK LIMITS AT TURN 1 LAP 6 ", "", "", "", ""],
            "LapStartDate": pd.to_datetime(
                [
                    "2025-09-06 14:10",
                    "2025-09-06 14:20",
                    "2025-09-06 14:30",
                    "2025-09-06 14:00",
                    "2025-09-06 14:02",
                ]
            ),
        }
    )


def messages(*texts: str, times: list[str] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        {"Message": list(texts), "Time": pd.to_datetime(times or ["2025-09-06 14:40"] * len(texts))}
    )


def test_qualifying_lap_offset_is_learned_from_timed_messages() -> None:
    msgs = messages(
        "CAR 55 (SAI) TIME 1:20.470 DELETED - TRACK LIMITS AT TURN 7 LAP 3 16:03:35",
        "CAR 4 (NOR) LAP DELETED - TRACK LIMITS AT TURN 1 LAP 10 16:32:21 (PIT)",
    )
    events = track_limit_events(laps_frame(), msgs, quali=True)
    got = set(zip(events["driver"], events["lap_number"], events["turn"], strict=True))
    # NOR lap 5 from FastF1's own deleted flag; SAI matched by lap time; NOR "LAP 10" is lap 9.
    assert got == {("NOR", 5, 1), ("SAI", 2, 7), ("NOR", 9, 1)}


def test_race_messages_use_fastf1_lap_numbers() -> None:
    msgs = messages("CAR 4 (NOR) LAP DELETED - TRACK LIMITS AT TURN 4 LAP 12 15:56:33")
    laps = laps_frame().assign(Deleted=False, DeletedReason="")
    events = track_limit_events(laps, msgs, quali=False)
    assert list(zip(events["lap_number"], events["turn"], strict=True)) == [(12, 4)]


def test_only_driving_incidents_are_kept() -> None:
    msgs = messages(
        "INCIDENT INVOLVING CAR 55 (SAI) NOTED - LEAVING THE TRACK AND GAINING AN ADVANTAGE",
        "PIT LANE INCIDENT INVOLVING CAR 55 (SAI) NOTED - "
        "FAILING TO FOLLOW RACE DIRECTORS INSTRUCTIONS",
        "FIA STEWARDS: INCIDENT INVOLVING CAR 55 (SAI) REVIEWED NO FURTHER INVESTIGATION",
        "TURN 15 INCIDENT INVOLVING CAR 4 (NOR) NOTED (16:42:31)",
        times=["2025-09-06 14:05", "2025-09-06 14:06", "2025-09-06 14:07", "2025-09-06 14:25"],
    )
    events = incident_events(laps_frame(), msgs)
    assert list(events["driver"]) == ["SAI", "NOR"]
    assert events["lap_number"].tolist() == [2, 9]  # the lap in progress when noted
    assert events["turn"].tolist() == [-1, 15]


def test_many_drivers_at_one_turn_are_marked_as_a_cluster() -> None:
    events = pd.DataFrame(
        {
            "driver": ["A", "B", "C", "D"],
            "lap_number": [51, 51, 51, 12],
            "turn": [15, 15, 15, 4],
            "kind": ["track_limits"] * 4,
        }
    )
    assert mark_clusters(events)["drivers_same_lap_turn"].tolist() == [3, 3, 3, 1]
