"""Paths and dataset constants shared by the pipeline, models and tools."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    # engine/src/race_engineer/config.py -> the repository root is three levels up.
    return Path(__file__).resolve().parents[3]


DATA_DIR = Path(os.environ.get("RACE_ENGINEER_DATA", repo_root() / "data"))
FASTF1_CACHE = DATA_DIR / "fastf1-cache"
PROCESSED_DIR = DATA_DIR / "processed"
RESULTS_DIR = DATA_DIR / "results"  # M4 outputs: scores, mistakes, style profiles
SAVED_DIR = DATA_DIR / "saved"  # the chat's saved example answers (web/scripts/saved.ts)
REPORT_DIR = repo_root() / "report"

YEARS = tuple(range(2022, 2027))

# FastF1 session names -> short codes. Sprint Shootout was the 2023 name for Sprint Qualifying.
SESSION_CODES = {
    "Qualifying": "Q",
    "Sprint Qualifying": "SQ",
    "Sprint Shootout": "SS",
    "Sprint": "S",
    "Race": "R",
}
QUALI_CODES = frozenset({"Q", "SQ", "SS"})

GRID_STEP_M = 5.0  # every lap is resampled onto a 5 m distance grid
WINDOW_BEFORE_M = 250.0  # corner segment: from 250 m before the apex...
WINDOW_AFTER_M = 150.0  # ...to 150 m after it (80 grid points)
POSITION_UNITS_PER_M = 10.0  # FastF1 X/Y positions are in decimetres
