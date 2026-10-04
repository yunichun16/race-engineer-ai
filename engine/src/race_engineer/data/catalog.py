"""Which sessions exist: qualifying, sprint and race sessions from the FastF1 schedule."""

from __future__ import annotations

from dataclasses import dataclass

import fastf1
import pandas as pd

from race_engineer.config import SESSION_CODES


@dataclass(frozen=True)
class SessionRef:
    year: int
    round: int
    event: str
    location: str
    name: str  # FastF1 session name, e.g. "Sprint Qualifying"
    code: str  # short code, e.g. "SQ"
    date_utc: pd.Timestamp


def completed_sessions(year: int, codes: set[str] | None = None) -> list[SessionRef]:
    """Sessions of `year` that finished at least three hours ago, in calendar order."""
    schedule = fastf1.get_event_schedule(year, include_testing=False)
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=3)
    refs = []
    for _, event in schedule.iterrows():
        for i in range(1, 6):
            name = event.get(f"Session{i}")
            code = SESSION_CODES.get(str(name))
            raw = event.get(f"Session{i}DateUtc")
            if code is None or raw is None or pd.isna(raw):
                continue
            date = pd.Timestamp(raw)
            date = date.tz_localize("UTC") if date.tzinfo is None else date.tz_convert("UTC")
            if date > cutoff or (codes and code not in codes):
                continue
            refs.append(
                SessionRef(
                    year=year,
                    round=int(event["RoundNumber"]),
                    event=str(event["EventName"]),
                    location=str(event["Location"]),
                    name=str(name),
                    code=code,
                    date_utc=date,
                )
            )
    return refs
