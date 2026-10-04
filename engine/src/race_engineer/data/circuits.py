"""How each circuit's map is turned, worked out ahead of time: data/processed/circuits.json.

The charts draw the track turned like the official circuit maps (as on TV), not north up. The
angle is in FastF1's circuit info (`rotation`, from the MultiViewer API, which FastF1 keeps in
its cache), but reading it means loading a session, about half a second even offline: too slow
for a request. So `race-engineer-data circuits` reads it once for every round in the processed
catalog and writes

    {"2025_16": {"rotation_deg": 92.0, "source": "fastf1"}, ...}

The rotation is the circuit's, so the race is read first, then the round's qualifying. A round
with neither in FastF1's cache gets 0.0 (north up) with `source: "default"`; its map still
works. The file sits in the processed folder, which is safe because `data.store` lists its
tables by name and never globs the folder. `tools.track_map` reads it (0.0 for a missing key).

Running it again is harmless: it rereads every round, keeps an earlier FastF1 angle that can
no longer be read (FastF1's cache dropped), and `only_missing` skips the rounds already in the
file.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal, TypedDict

from race_engineer.config import FASTF1_CACHE, PROCESSED_DIR

log = logging.getLogger(__name__)

CIRCUITS_FILE = "circuits.json"
ROTATION_SESSIONS = ("R", "Q")  # read in this order; the rotation is the circuit's
SESSION_FILE = re.compile(r"^(\d{4})_(\d{2})_[A-Z]+\.parquet$")  # data.store.session_key


class Circuit(TypedDict):
    rotation_deg: float
    source: Literal["fastf1", "default"]


def circuits_path(root: Path = PROCESSED_DIR) -> Path:
    return root / CIRCUITS_FILE


def circuit_key(year: int, round_: int) -> str:
    return f"{year}_{round_:02d}"


def catalog_rounds(root: Path = PROCESSED_DIR) -> list[tuple[int, int]]:
    """Every (year, round) with a processed session, from the sessions folder's file names."""
    folder = root / "sessions"
    found = set()
    for path in folder.glob("*.parquet") if folder.is_dir() else ():
        m = SESSION_FILE.match(path.name)
        if m:
            found.add((int(m.group(1)), int(m.group(2))))
    return sorted(found)


def load_circuits(root: Path = PROCESSED_DIR) -> dict[str, Circuit]:
    """circuits.json as written (empty when it is missing or unreadable; entries that don't
    have the expected shape are dropped)."""
    try:
        raw = json.loads(circuits_path(root).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Circuit] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        angle, source = value.get("rotation_deg"), value.get("source")
        if isinstance(angle, int | float) and source in ("fastf1", "default"):
            out[str(key)] = {"rotation_deg": float(angle), "source": source}
    return out


def _offline_fastf1() -> None:
    """FastF1 reading from its cache only (no API calls), quietly."""
    import fastf1

    logging.getLogger("fastf1").setLevel(logging.ERROR)
    fastf1.set_log_level("ERROR")
    fastf1.Cache.enable_cache(str(FASTF1_CACHE))
    fastf1.Cache.offline_mode(True)


def fastf1_rotation(year: int, round_: int, code: str) -> float | None:
    """The circuit's map rotation (degrees) from one session in FastF1's cache, as the review
    page read it; None when the session or its circuit info isn't cached."""
    import fastf1

    try:
        session = fastf1.get_session(year, round_, code)
        session.load(laps=True, telemetry=False, weather=False, messages=False)
        info = session.get_circuit_info()
    except Exception as exc:  # not cached, or a schedule without that session
        log.debug("%s %s %s: no circuit info (%s)", year, round_, code, exc)
        return None
    return None if info is None else float(info.rotation)


def _no_rotation(year: int, round_: int, code: str) -> float | None:
    return None


def read_rotation(
    year: int, round_: int, read: Callable[[int, int, str], float | None] = fastf1_rotation
) -> float | None:
    """The round's rotation from its race, else its qualifying; None from neither."""
    for code in ROTATION_SESSIONS:
        angle = read(year, round_, code)
        if angle is not None:
            return angle
    return None


def build_circuits(
    root: Path = PROCESSED_DIR,
    only_missing: bool = False,
    rounds: Iterable[tuple[int, int]] | None = None,
    read: Callable[[int, int, str], float | None] | None = None,
) -> dict[str, Circuit]:
    """Write circuits.json for `rounds` (default: the processed catalog) and return it.

    A round FastF1 can't give an angle for keeps the one an earlier run wrote from FastF1, or
    else gets 0.0 with source "default"; without a FastF1 cache at all (processed data copied
    from elsewhere), that is every round. With `only_missing`, rounds already in the file are
    left as they are. `read` replaces FastF1 (tests)."""
    if read is None and not FASTF1_CACHE.is_dir():
        log.warning("No FastF1 cache at %s: no rotations to read", FASTF1_CACHE)
        read = _no_rotation
    if read is None:
        _offline_fastf1()
        read = fastf1_rotation
    before = load_circuits(root)
    out: dict[str, Circuit] = dict(before) if only_missing else {}
    for year, round_ in catalog_rounds(root) if rounds is None else rounds:
        key = circuit_key(year, round_)
        if only_missing and key in before:
            continue
        angle = read_rotation(year, round_, read)
        if angle is not None:
            out[key] = {"rotation_deg": round(angle, 1), "source": "fastf1"}
        elif before.get(key, {}).get("source") == "fastf1":
            log.info("%s: no circuit info now, keeping the earlier angle", key)
            out[key] = before[key]
        else:
            log.info("%s: no circuit info in FastF1's cache, north up", key)
            out[key] = {"rotation_deg": 0.0, "source": "default"}
    path = circuits_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(out.items())), indent=1) + "\n")
    tmp.replace(path)  # atomic, as data.store.write_table: a reader never sees half a file
    return out
