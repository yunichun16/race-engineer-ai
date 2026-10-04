"""Weak labels for driver mistakes, taken from race-control messages.

Two kinds, from most to least precise:

- **Track limits**: "CAR 81 (PIA) TIME 1:58.595 DELETED - TRACK LIMITS AT TURN 1 LAP 40".
  Gives the driver, lap and turn, so it can be matched to a single corner segment.
- **Incidents**: "INCIDENT INVOLVING CAR 55 (SAI) NOTED - LEAVING THE TRACK AND GAINING AN
  ADVANTAGE". Gives the driver and time only; mapped to the lap in progress.

Race messages number laps like FastF1; qualifying messages count one extra lap. Track-limits
messages with a lap time are matched on that time, and the rest use the offset learned from
the matched ones.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from race_engineer.features.grid import to_seconds

TRACK_LIMITS_RE = re.compile(
    r"CAR (?P<number>\d+) \((?P<driver>[A-Z]{3})\) "
    r"(?:TIME (?P<time>\d+:\d{2}\.\d{3}) |LAP )DELETED - "
    r"TRACK LIMITS AT TURN (?P<turn>\d+) LAP (?P<lap>\d+)"
)
DELETED_REASON_RE = re.compile(r"TRACK LIMITS AT TURN (?P<turn>\d+)")
CAR_RE = re.compile(r"(?:CARS?|AND) (?P<number>\d+) \((?P<driver>[A-Z]{3})\)")
TURN_RE = re.compile(r"TURN (?P<turn>\d+)")

# Incident reasons that describe how the car was driven (as opposed to pit or procedure rules).
DRIVING_REASONS = (
    "LEAVING THE TRACK",
    "CAUSING A COLLISION",
    "FORCING ANOTHER DRIVER OFF",
    "MORE THAN ONE CHANGE OF DIRECTION",
    "TRACK LIMITS",
    "SPUN",
    "OFF TRACK",
)

EVENT_COLUMNS = pd.Index(
    ["driver_number", "driver", "lap_number", "turn", "kind", "source", "message"]
)


def mark_clusters(events: pd.DataFrame) -> pd.DataFrame:
    """Count drivers flagged on the same lap and turn.

    Many drivers at once usually means something on track (a crash, debris) pushed them wide,
    not independent driver errors, so the evaluation can set these apart.
    """
    size = events.groupby(["kind", "lap_number", "turn"])["driver"].transform("nunique")
    return events.assign(drivers_same_lap_turn=size.astype(int))


def _lap_seconds(text: str) -> float:
    minutes, seconds = text.split(":")
    return int(minutes) * 60 + float(seconds)


def track_limit_events(laps: pd.DataFrame, messages: pd.DataFrame, quali: bool) -> pd.DataFrame:
    """One row per (driver, lap, turn) track-limits violation."""
    drivers = laps["Driver"].astype(str).to_numpy()
    lap_numbers = laps["LapNumber"].to_numpy()
    lap_times = to_seconds(laps["LapTime"])

    def lap_with_time(driver: str, time_text: str) -> int | None:
        hit = np.flatnonzero(
            (drivers == driver) & np.isclose(lap_times, _lap_seconds(time_text), atol=5e-4)
        )
        return int(lap_numbers[hit[0]]) if len(hit) == 1 else None

    rows = []
    deleted = laps["Deleted"].fillna(False).astype(bool).to_numpy()
    reasons = laps["DeletedReason"].fillna("").astype(str).to_numpy()
    numbers = laps["DriverNumber"].astype(str).to_numpy()
    for i in np.flatnonzero(deleted):
        m = DELETED_REASON_RE.search(reasons[i])
        if m:
            rows.append(
                (
                    numbers[i],
                    drivers[i],
                    int(lap_numbers[i]),
                    int(m["turn"]),
                    "track_limits",
                    "fastf1_deleted",
                    reasons[i].strip(),
                )
            )

    texts = messages["Message"].astype(str) if "Message" in messages else []
    parsed = [m for m in map(TRACK_LIMITS_RE.search, texts) if m]

    # Learn the message-lap offset from messages that carry a lap time.
    offsets = []
    for m in parsed:
        matched = lap_with_time(m["driver"], m["time"]) if m["time"] else None
        if matched is not None:
            offsets.append(int(m["lap"]) - matched)
    offset = int(np.median(offsets)) if offsets else (1 if quali else 0)

    for m in parsed:
        matched = lap_with_time(m["driver"], m["time"]) if m["time"] else None
        lap_number = matched if matched is not None else int(m["lap"]) - offset
        rows.append(
            (
                m["number"],
                m["driver"],
                lap_number,
                int(m["turn"]),
                "track_limits",
                "race_control",
                m.group(0),
            )
        )

    events = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    return events.drop_duplicates(subset=["driver", "lap_number", "turn"]).reset_index(drop=True)


def incident_events(laps: pd.DataFrame, messages: pd.DataFrame) -> pd.DataFrame:
    """Driving-related incidents ('NOTED' messages), mapped to the lap in progress."""
    if messages is None or messages.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    drivers = laps["Driver"].astype(str).to_numpy()
    starts = pd.to_datetime(laps["LapStartDate"]).to_numpy()
    lap_numbers = laps["LapNumber"].to_numpy()

    rows = []
    for text, when in zip(
        messages["Message"].astype(str), pd.to_datetime(messages["Time"]), strict=True
    ):
        if "INCIDENT INVOLVING" not in text or "NOTED" not in text or "STEWARDS" in text:
            continue
        if text.startswith(("PIT LANE", "PIT ENTRY", "PIT EXIT")):
            continue
        reason = text.split("NOTED - ", 1)[1] if "NOTED - " in text else ""
        turn = TURN_RE.search(text)
        # Keep incidents with a driving reason, and turn-specific ones posted without a reason
        # ("TURN 15 INCIDENT INVOLVING CAR 77 (BOT) NOTED").
        if not (any(r in reason for r in DRIVING_REASONS) or (turn and not reason)):
            continue
        for car in CAR_RE.finditer(text.split("NOTED", 1)[0]):
            mine = np.flatnonzero((drivers == car["driver"]) & (starts <= np.datetime64(when)))
            lap_number = int(lap_numbers[mine[np.argmax(starts[mine])]]) if len(mine) else -1
            rows.append(
                (
                    car["number"],
                    car["driver"],
                    lap_number,
                    int(turn["turn"]) if turn else -1,
                    "incident",
                    "race_control",
                    text,
                )
            )
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)
