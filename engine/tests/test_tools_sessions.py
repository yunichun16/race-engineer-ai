"""Finding a session from loose text: names, locations, countries, initials, session aliases and
keys, and the errors."""

import os
import re
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from race_engineer.data import store
from race_engineer.tools.registry import DataPaths, data_version
from race_engineer.tools.sessions import (
    CLOSE,
    ResultsUnavailableError,
    ToolInputError,
    _catalog,
    event_query,
    likeness,
    list_sessions,
    match_tier,
    normalize,
    resolve_session,
    session_codes,
    session_like,
    suggest_sessions,
    unknown_words,
)
from race_engineer.tools.synthetic import (
    SyntheticEvent,
    SyntheticSession,
    sample_comparison_session,
    write_session,
)

# (year, round, code, event, location, date)
SESSIONS = [
    (2023, 4, "SS", "Azerbaijan Grand Prix", "Baku", "2023-04-29"),
    (2023, 17, "Q", "Qatar Grand Prix", "Lusail", "2023-10-07"),
    (2023, 21, "Q", "Las Vegas Grand Prix", "Las Vegas", "2023-11-18"),
    (2024, 2, "Q", "Saudi Arabian Grand Prix", "Jeddah", "2024-03-08"),
    (2024, 19, "Q", "United States Grand Prix", "Austin", "2024-10-19"),
    (2024, 19, "R", "United States Grand Prix", "Austin", "2024-10-20"),
    (2025, 1, "Q", "Australian Grand Prix", "Melbourne", "2025-03-15"),
    (2025, 1, "R", "Australian Grand Prix", "Melbourne", "2025-03-16"),
    (2025, 7, "Q", "Emilia Romagna Grand Prix", "Imola", "2025-05-17"),
    (2025, 19, "Q", "United States Grand Prix", "Austin", "2025-10-18"),  # as FastF1 names them
    (2025, 20, "Q", "Mexico City Grand Prix", "Mexico City", "2025-10-25"),
    (2025, 21, "Q", "São Paulo Grand Prix", "São Paulo", "2025-11-08"),
    (2026, 1, "Q", "Australian Grand Prix", "Melbourne", "2026-03-07"),
    (2026, 1, "R", "Australian Grand Prix", "Melbourne", "2026-03-08"),
    (2026, 2, "SQ", "Chinese Grand Prix", "Shanghai", "2026-03-13"),
    (2026, 2, "S", "Chinese Grand Prix", "Shanghai", "2026-03-14"),
    (2026, 5, "Q", "Canadian Grand Prix", "Montréal", "2026-05-23"),
    (2026, 7, "Q", "Barcelona Grand Prix", "Barcelona", "2026-06-13"),
    (2026, 9, "Q", "British Grand Prix", "Silverstone", "2026-07-04"),
    (2026, 10, "Q", "Belgian Grand Prix", "Spa-Francorchamps", "2026-07-18"),
    (2026, 14, "Q", "Spanish Grand Prix", "Madrid", "2026-09-12"),
]


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("processed")
    for year, round_, code, event, location, date in SESSIONS:
        write_session(SyntheticSession(year, round_, code, event, location, date=date), root)
    return root


@pytest.mark.parametrize(
    ("event", "year", "session", "key"),
    [
        ("Australian Grand Prix", None, "Q", "2026_01_Q"),
        ("melbourne", None, "race", "2026_01_R"),
        ("AUSTRALIA", 2025, "qualifying", "2025_01_Q"),
        ("Australian GP 2025", None, "R", "2025_01_R"),  # the year inside the event text
        ("montreal", None, "quali", "2026_05_Q"),  # accents don't matter
        ("china", None, "sprint", "2026_02_S"),  # country name
        ("shanghai", None, "Sprint Qualifying", "2026_02_SQ"),
        ("baku", None, "sprint qualifying", "2023_04_SS"),  # 2023's Sprint Shootout
        ("spa", None, "Q", "2026_10_Q"),  # a whole word beats "Spanish"
        ("spain", None, "Q", "2026_14_Q"),
        ("imola", None, "Q", "2025_07_Q"),  # latest season that has the event
        ("Australian Grand Prix 2026", 2026, None, "2026_01_Q"),  # the year given twice
        ("melbourne race", None, None, "2026_01_R"),  # the session inside the event text
        ("Chinese GP sprint", None, None, "2026_02_S"),
        ("shanghai sprint qualifying", None, "SQ", "2026_02_SQ"),
        ("Australian GP 2025 race", None, None, "2025_01_R"),
        ("Brazilian Grand Prix", None, "Q", "2025_21_Q"),  # adjectives FastF1 doesn't use
        ("Mexican GP", None, "Q", "2025_20_Q"),
        ("American Grand Prix", None, "Q", "2025_19_Q"),
        ("U.S. Grand Prix", None, "Q", "2025_19_Q"),  # dotted initials
        # Initials with or without the last dot, or spaced, leave no "s" to read as the sprint.
        ("U.S Grand Prix", 2024, "R", "2024_19_R"),
        ("U S Grand Prix", 2024, None, "2024_19_Q"),
        ("U.S", None, None, "2025_19_Q"),
        ("U. S. GP", 2024, "race", "2024_19_R"),
        ("u.s.a.", None, "Q", "2025_19_Q"),
        ("U.S.GP 2024 R", None, None, "2024_19_R"),
        ("U S race 2024", None, None, "2024_19_R"),
        ("USA", 2024, None, "2024_19_Q"),
        ("US GP", None, "R", "2024_19_R"),  # the newest US race
        # Nor does a first word cut to its "S".
        ("S. Paulo", 2025, None, "2025_21_Q"),
        ("S Paulo", None, "Q", "2025_21_Q"),
        ("S. Arabia", None, None, "2024_02_Q"),
        # More qualifying spellings and the parts of sprint qualifying.
        ("melbourne qually 2025", None, None, "2025_01_Q"),
        ("Melbourne quals", 2025, None, "2025_01_Q"),
        ("shanghai SQ2", None, None, "2026_02_SQ"),
        # Session keys, as find_session's data.key gives them (2023's SQ is the Shootout, SS).
        ("2025_19_Q", None, None, "2025_19_Q"),
        ("2024_19_R", 2024, "race", "2024_19_R"),
        ("2023_04_SQ", None, None, "2023_04_SS"),
        ("2026_2_s", None, None, "2026_02_S"),
        # Words of five letters or more still match inside a name; "LV" is Las Vegas, not the
        # "lv" of Silverstone.
        ("rstone", None, None, "2026_09_Q"),
        ("LV GP", None, None, "2023_21_Q"),
        # Session codes as words of their own; possessives don't leave a stray "s".
        ("Australian GP 2025 R", None, None, "2025_01_R"),
        ("melbourne Q", 2025, None, "2025_01_Q"),
        ("shanghai sq", None, None, "2026_02_SQ"),
        ("Shanghai S", None, "sprint", "2026_02_S"),
        ("Melbourne's", 2025, "R", "2025_01_R"),
        ("Melbourne\u2019s", 2025, "R", "2025_01_R"),  # a typographic apostrophe
        # Circuit names and spellings that aren't FastF1's.
        ("Catalunya", None, None, "2026_07_Q"),
        ("Montmelo", None, None, "2026_07_Q"),
        ("Gilles Villeneuve", None, None, "2026_05_Q"),
        ("Hermanos Rodriguez", None, None, "2025_20_Q"),
        ("brasil", None, None, "2025_21_Q"),
        ("Losail", None, None, "2023_17_Q"),
        ("KSA", None, None, "2024_02_Q"),
        ("San Marino", None, None, "2025_07_Q"),
        ("CDMX", None, None, "2025_20_Q"),
        ("USGP 2024", None, "R", "2024_19_R"),
        ("Baku City Circuit", None, "SS", "2023_04_SS"),
        # Three letters may be the start of a name.
        ("mel", 2025, None, "2025_01_Q"),
    ],
)
def test_resolves_loose_names(
    root: Path, event: str, year: int | None, session: str | None, key: str
) -> None:
    assert resolve_session(event, year, session, root).key == key


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("Q", ("Q",)),
        ("Quali", ("Q",)),
        ("race", ("R",)),
        ("Sprint", ("S",)),
        ("sprint shootout", ("SQ", "SS")),
        ("SQ", ("SQ", "SS")),
        ("Q3", ("Q",)),
        ("qually", ("Q",)),
        ("qualies", ("Q",)),
        ("quals", ("Q",)),
        ("qualis", ("Q",)),
        ("SQ1", ("SQ", "SS")),
        ("sq3", ("SQ", "SS")),
    ],
)
def test_session_aliases(text: str, codes: tuple[str, ...]) -> None:
    assert session_codes(text) == codes


@pytest.mark.parametrize(
    ("event", "year", "session", "message"),
    [
        ("melborne", None, "Q", "Did you mean: Australian Grand Prix (Melbourne)"),
        ("grand prix", None, "Q", "matches several 2026 events"),
        ("imola", 2026, "Q", "Available season(s): 2025"),
        ("imola", None, "sprint", "Available: Qualifying (Q)"),
        ("melbourne", None, "fp2", "Unknown session 'fp2'"),
        ("melbourne", None, "FP2", "Practice sessions are not in the dataset."),
        ("2025 Australian Grand Prix", 2026, "Q", "The event text says 2025 but year is 2026"),
        ("melbourne race", None, "Q", "names the 'race' session but session is 'Q'"),
        ("melbourne R", None, "Q", "names the 'r' session but session is 'Q'"),
        # A single letter is never part of a name: not the "r" of "Romagna".
        ("imola x", None, "Q", "No processed event matches 'imola x'"),
        ("r", None, None, "Give an event name or location"),
        # A driver code (or another short word) is never part of a name: not the "ver" of
        # Silverstone, the "gas" of Las Vegas, the "sai" of Lusail or the "audi" of Saudi.
        ("VER", None, None, "No processed event matches 'VER'"),
        ("GAS", None, None, "No processed event matches 'GAS'"),
        ("SAI", None, None, "No processed event matches 'SAI'"),
        ("Audi", None, None, "No processed event matches 'Audi'"),
        ("Silverstone VER", None, None, "Did you mean: British Grand Prix (Silverstone)?"),
        ("Las Vegas GAS", None, None, "No processed event matches 'Las Vegas GAS'"),
        # One or two letters are not the start of a name: not the "me" of Melbourne.
        ("me", None, None, "No processed event matches 'me'"),
        ("Melbourne me", None, None, "No processed event matches 'Melbourne me'"),
        # The US initials don't make a sprint weekend of the US GP, nor the "S" of "S. Paulo"
        # a sprint weekend of São Paulo.
        ("U.S sprint", 2024, None, "The 2024 United States Grand Prix has no processed 'sprint'"),
        ("S. Paulo", None, "R", "The 2025 São Paulo Grand Prix has no processed 'R' session"),
        # Text that fits several events of the season is ambiguous even when only one of them
        # has the session: "aus" is Melbourne and Austin, and only Melbourne had a 2025 race.
        (
            "aus",
            2025,
            "R",
            "'aus' matches several 2025 events: Australian Grand Prix (Melbourne); United States "
            "Grand Prix (Austin).",
        ),
        # Session keys must name a processed session and agree with year and session.
        ("2025_19_Q", 2024, None, "The event text says 2025 but year is 2024"),
        ("2025_19_Q", None, "R", "names the 'Q' session but session is 'R'"),
        ("2025_30_Q", None, None, "Processed rounds in 2025: 1, 7, 19-21."),
        ("2025_07_R", None, None, "The 2025 Emilia Romagna Grand Prix has no processed 'R'"),
        # A season alone: processed, it still needs an event; not processed, it says so.
        ("2021", None, None, "No processed session in 2021. Processed seasons: 2023, 2024, 2025,"),
        ("2025", None, None, "Give an event name or location"),
        ("", 2019, None, "No processed session in 2019."),
    ],
)
def test_errors_say_what_is_available(
    root: Path, event: str, year: int | None, session: str, message: str
) -> None:
    with pytest.raises(ToolInputError, match=re.escape(message)):
        resolve_session(event, year, session, root)


@pytest.mark.parametrize(
    ("location", "query", "tier"),
    [
        ("Imola", ["romagna"], 0),
        ("Imola", ["rom"], 1),
        ("Imola", ["ro"], None),  # a prefix only from three letters
        ("Melbourne", ["me"], None),
        ("Imola", ["omagna"], 2),
        ("Imola", ["omag"], None),  # inside a name only from five letters
        ("Imola", ["r"], None),
        ("Imola", ["imola", "a"], None),
        ("Silverstone", ["ver"], None),
        ("Silverstone", ["rstone"], 2),
        ("Las Vegas", ["gas"], None),
        ("Lusail", ["sai"], None),
        ("Jeddah", ["audi"], None),
        ("Jeddah", ["saudi"], 0),
    ],
)
def test_match_tiers(root: Path, location: str, query: list[str], tier: int | None) -> None:
    info = next(s for s in list_sessions(root=root) if s.location == location)
    assert match_tier(query, info) == tier


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("Montréal", "montreal"),
        ("Hamilton's", "hamilton"),
        ("U.S.", "us"),
        ("U.S", "us"),
        ("U. S.", "us"),
        ("U S", "us"),
        ("u s a", "usa"),
        ("U.S.A.", "usa"),
        ("U.K.", "uk"),
        ("U.S.GP", "us gp"),
        ("U.S. G.P. 2024", "us gp 2024"),
        ("U. S. A.", "usa"),
        ("Monaco G.P.", "monaco gp"),
        ("U.S Grand Prix", "us grand prix"),
        ("S. Perez", "s perez"),  # a first name's initial is not joined to the next word
        ("Monza R S", "monza r s"),  # nor are session codes
        ("S. Paulo", "sao paulo"),  # a place's first word cut to its "S" is written out
        ("S Arabian GP", "saudi arabian gp"),
    ],
)
def test_normalize(text: str, normalized: str) -> None:
    assert normalize(text) == normalized


def test_a_season_missing_names_each_event_once(tmp_path: Path) -> None:
    # Monaco's location is "Monaco" until 2025 and "Monte Carlo" in 2026.
    for year, location in ((2025, "Monaco"), (2026, "Monte Carlo")):
        write_session(SyntheticSession(year, 8, "Q", "Monaco Grand Prix", location), tmp_path)
    with pytest.raises(ToolInputError) as err:
        resolve_session("monaco", 2019, root=tmp_path)
    assert str(err.value) == (
        "Monaco Grand Prix: no processed session in 2019. Available season(s): 2025, 2026."
    )


def test_a_renamed_location_names_the_same_circuit(tmp_path: Path) -> None:
    """FastF1 names Monaco's location "Monaco" until 2025 and "Monte Carlo" from 2026: the same
    event at the same circuit (track length), so "Monte Carlo" finds 2025 too. The Spanish GP
    moved from Barcelona to Madrid, a different circuit, so "Madrid" doesn't find 2025."""
    for year, round_, event, location, length in [
        (2025, 8, "Monaco Grand Prix", "Monaco", 3270.0),
        (2025, 9, "Spanish Grand Prix", "Barcelona", 4610.0),
        (2026, 6, "Monaco Grand Prix", "Monte Carlo", 3275.0),
        (2026, 14, "Spanish Grand Prix", "Madrid", 5345.0),
    ]:
        session = SyntheticSession(year, round_, "Q", event, location, track_length_m=length)
        write_session(session, tmp_path)
    assert resolve_session("Monte Carlo", 2025, root=tmp_path).key == "2025_08_Q"
    assert resolve_session("Monte Carlo", root=tmp_path).key == "2026_06_Q"
    with pytest.raises(ToolInputError) as err:
        resolve_session("Madrid", 2025, root=tmp_path)
    # By name and location: the Spanish GP itself was held in 2025.
    assert str(err.value) == (
        "Spanish Grand Prix (Madrid): no processed session in 2025. Available season(s): 2026."
    )
    assert resolve_session("Spain", 2025, root=tmp_path).key == "2025_09_Q"


def test_suggestion_ties_go_to_the_whole_name(tmp_path: Path) -> None:
    """ "monze" is as close to "monza" as to the "monte" of Monte Carlo (0.8 each): the whole
    name comes first, whatever the calendar order."""
    for round_, event, location in [
        (6, "Monaco Grand Prix", "Monte Carlo"),
        (13, "Italian Grand Prix", "Monza"),
    ]:
        write_session(SyntheticSession(2026, round_, "Q", event, location), tmp_path)
    found = suggest_sessions(["monze"], list_sessions(root=tmp_path))
    assert [s.location for s in found] == ["Monza", "Monte Carlo"]


def test_lists_sessions_in_calendar_order(root: Path) -> None:
    keys = [s.key for s in list_sessions(2025, root)]
    assert keys == ["2025_01_Q", "2025_01_R", "2025_07_Q", "2025_19_Q", "2025_20_Q", "2025_21_Q"]
    assert list_sessions(2026, root)[0].date == "2026-03-07"


def test_missing_dataset_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ToolInputError, match="No processed sessions"):
        list_sessions(root=tmp_path)


def test_catalog_is_read_once_until_a_session_is_added(tmp_path: Path) -> None:
    write_session(SyntheticSession(2026, 1, "Q", "Australian Grand Prix", "Melbourne"), tmp_path)
    _catalog.cache_clear()
    assert [s.key for s in list_sessions(root=tmp_path)] == ["2026_01_Q"]
    list_sessions(2026, tmp_path)
    resolve_session("melbourne", root=tmp_path)
    assert _catalog.cache_info().misses == 1 and _catalog.cache_info().hits == 2
    session = SyntheticSession(2026, 2, "Q", "Chinese Grand Prix", "Shanghai", date="2026-03-14")
    write_session(session, tmp_path)
    assert [s.key for s in list_sessions(root=tmp_path)] == ["2026_01_Q", "2026_02_Q"]
    assert _catalog.cache_info().misses == 2


def _settle(folder: Path) -> os.stat_result:
    """Date `folder`'s last change an hour back (any write after this moves its time, whatever
    the clock's resolution) and return its stat."""
    then = time.time_ns() - 3_600_000_000_000
    os.utime(folder, ns=(then, then))
    return folder.stat()


def test_catalog_sees_a_session_replaced_added_or_removed(tmp_path: Path) -> None:
    # The catalog's memo and data_version (the tool results' cache, the API's ETag) check the
    # sessions folder's time and names, not each file (cache.listing_signature).
    folder, paths = tmp_path / "sessions", DataPaths(tmp_path, tmp_path / "results")
    melbourne = SyntheticSession(2026, 1, "Q", "Australian Grand Prix", "Melbourne")
    write_session(melbourne, tmp_path)
    _catalog.cache_clear()
    _settle(folder)
    assert [s.track_length_m for s in list_sessions(root=tmp_path)] == [melbourne.track_length_m]
    version = data_version(paths)
    assert list_sessions(root=tmp_path) and data_version(paths) == version
    assert _catalog.cache_info().misses == 1 and _catalog.cache_info().hits == 1

    # Replaced, as `make data --force` does: renamed over the old file, its time put back.
    info = store.table_path("sessions", melbourne.key, tmp_path).stat()
    write_session(replace(melbourne, track_length_m=5300.0, location="Albert Park"), tmp_path)
    path = store.table_path("sessions", melbourne.key, tmp_path)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    found = list_sessions(root=tmp_path)
    assert [(s.track_length_m, s.location) for s in found] == [(5300.0, "Albert Park")]
    assert data_version(paths) != version

    # Added, then removed, each with the folder's time put back: the names show it.
    settled = _settle(folder)
    version = data_version(paths)
    shanghai = SyntheticSession(2026, 2, "Q", "Chinese Grand Prix", "Shanghai", date="2026-03-14")
    write_session(shanghai, tmp_path)
    os.utime(folder, ns=(settled.st_atime_ns, settled.st_mtime_ns))
    assert [s.key for s in list_sessions(root=tmp_path)] == ["2026_01_Q", "2026_02_Q"]
    assert data_version(paths) != version
    store.table_path("sessions", shanghai.key, tmp_path).unlink()
    os.utime(folder, ns=(settled.st_atime_ns, settled.st_mtime_ns))
    assert list_sessions(root=tmp_path) == found
    assert data_version(paths) == version  # the same files as before the addition
    assert _catalog.cache_info().misses == 4  # the first read, then one per change


# The audit hook below sees Python's own opens and directory reads ("open", "os.scandir",
# "os.listdir"), from every thread, while `audited` records. Hooks can't be removed, so one is
# added for the whole test session and does nothing outside `audited`.
_audit: dict[str, Any] = {"under": None, "seen": []}
_audit_lock = threading.Lock()


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    under = _audit["under"]
    if under is None or event not in {"open", "os.scandir", "os.listdir"} or not args:
        return
    try:
        path = os.path.abspath(os.fsdecode(args[0]))
    except TypeError:  # a file descriptor
        return
    if path.startswith(under):
        with _audit_lock:
            _audit["seen"].append((event.removeprefix("os."), path))


sys.addaudithook(_audit_hook)


@contextmanager
def audited(folder: Path) -> Iterator[list[tuple[str, str]]]:
    """The opens and directory reads under `folder` made inside the block, as (event, path)."""
    seen: list[tuple[str, str]] = []
    _audit["seen"], _audit["under"] = seen, os.path.abspath(folder)
    try:
        yield seen
    finally:
        _audit["under"] = None


def test_catalog_reads_the_sessions_table_only(tmp_path: Path, monkeypatch) -> None:
    # The API's first warm-up step. On the deployed API's network volume every file touched is
    # round trips, and the other six tables are 1,579 more files: the catalog lists the sessions
    # folder and opens each session file once, and nothing else.
    sample = sample_comparison_session()  # laps, lap grids, corners and a track
    for year, round_, code, event, location, date in SESSIONS[:6]:
        fields = {"year": year, "round": round_, "code": code, "event": event, "date": date}
        incident = SyntheticEvent("AAA", 7, 1)
        session = replace(sample, location=location, events=(incident,), **fields)
        write_session(session, tmp_path)
    assert set(os.listdir(tmp_path)) == set(store.TABLES) - {"segments"}
    files = sorted(str(p) for p in (tmp_path / "sessions").glob("*.parquet"))
    folder = str(tmp_path / "sessions")

    def no_duckdb(*args: object, **kwargs: object) -> None:  # its file access isn't audited
        raise AssertionError("the catalog reads the sessions table without DuckDB")

    monkeypatch.setattr(duckdb, "connect", no_duckdb)
    # Nor pyarrow, whose file access isn't audited either: the other tables can't be listed or
    # opened while the catalog is read (unless running as root, which permissions don't stop).
    others = [tmp_path / table for table in os.listdir(tmp_path) if table != "sessions"]
    _catalog.cache_clear()
    _settle(tmp_path / "sessions")  # long written, as on the deployed volume
    try:
        for other in others:
            other.chmod(0)
        with audited(tmp_path) as seen:
            found = list_sessions(root=tmp_path)
    finally:
        for other in others:
            other.chmod(0o755)
    assert len(found) == len(files) == 6
    assert sorted(path for event, path in seen if event == "open") == files  # each file once
    # The memo key's listing, then the read's; no stat per file (cache.listing_signature).
    assert [(event, path) for event, path in seen if event != "open"] == [("scandir", folder)] * 2
    with audited(tmp_path) as seen:
        assert list_sessions(root=tmp_path) == found  # cached: the listing only
    assert seen == [("scandir", folder)]


def test_missing_dataset_is_not_cached(tmp_path: Path) -> None:
    with pytest.raises(ToolInputError, match="No processed sessions"):
        list_sessions(root=tmp_path)
    write_session(SyntheticSession(2026, 1, "Q", "Australian Grand Prix", "Melbourne"), tmp_path)
    assert len(list_sessions(root=tmp_path)) == 1


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("melborne", ["Australian Grand Prix (Melbourne)"]),
        ("shangai", ["Chinese Grand Prix (Shanghai)"]),
        ("emilia romanga", ["Emilia Romagna Grand Prix (Imola)"]),
        ("sao paolo", ["São Paulo Grand Prix (São Paulo)"]),
        ("interlagoss", ["São Paulo Grand Prix (São Paulo)"]),  # an alias, misspelt
        # The closest first: Qatar is spelt right, Montréal isn't.
        ("montral qatar", ["Qatar Grand Prix (Lusail)", "Canadian Grand Prix (Montréal)"]),
        ("zzzz", []),
        # Ordinary words are not misspelt names: "last" is close to the "las" of Las Vegas and
        # "car" to Carlo only by chance, too short to compare.
        ("last", []),
        ("Was there a safety car in the last race", []),
        ("Tell us about the last race", ["United States Grand Prix (Austin)"]),  # "us" is USA
    ],
)
def test_suggest_sessions(root: Path, text: str, names: list[str]) -> None:
    found = suggest_sessions(event_query(text), list_sessions(root=root))
    assert [f"{s.event} ({s.location})" for s in found] == names
    # The newest session of each event.
    if text == "melborne":
        assert found[0].key == "2026_01_R"


def test_suggestions_are_distinct_events(root: Path) -> None:
    found = suggest_sessions(["australain", "melborne"], list_sessions(root=root), n=10)
    assert [s.key for s in found] == ["2026_01_R"]  # both words name the same event
    assert len(suggest_sessions(["melborne", "shangai"], list_sessions(root=root), n=1)) == 1


@pytest.mark.parametrize(
    ("text", "unknown"),
    [
        # Names, misspelt or not, have none.
        ("Australian Grand Prix", []),
        ("melborne", []),
        ("emilia romanga", []),
        ("U.S. GP", []),
        ("brazil", []),
        # A question or a driver in the event text has its ordinary words ("car" too: Monte
        # Carlo isn't in this catalog).
        (
            "Was there a safety car in the last",
            ["was", "there", "a", "safety", "car", "in", "the", "last"],
        ),
        ("Silverstone VER", ["ver"]),
        ("Lewis", ["lewis"]),
        ("me", ["me"]),
        # One letter added, dropped, changed or swapped is a misspelling; so is a name of two
        # words with its short word misspelt, as a whole.
        ("baky", []),
        ("lusial", []),
        ("mexico cty", []),
        ("Melbourne me", ["me"]),
    ],
)
def test_unknown_words(root: Path, text: str, unknown: list[str]) -> None:
    assert unknown_words(event_query(text), list_sessions(root=root)) == unknown


def test_likeness_and_session_words() -> None:
    assert likeness("melborne", "melbourne") > 0.9
    assert likeness("maimi", "miami") == CLOSE  # a swap, which difflib scores 0.4
    assert likeness("baky", "baku") == CLOSE  # a changed letter in a short word, 0.75
    assert likeness("japn", "japan") > CLOSE
    assert likeness("last", "vegas") < CLOSE
    assert likeness("mistakes", "states") < CLOSE
    assert session_like("sprnt") == "sprint"
    assert session_like("qualifyng") == "qualifying"
    assert session_like("race") is None  # a session word already
    assert session_like("melbourne") is None


def test_results_unavailable_is_a_tool_input_error() -> None:
    with pytest.raises(ToolInputError, match="rebuild"):
        raise ResultsUnavailableError("mistakes.parquet is stale: rebuild with `make m4`.")


def test_synthetic_session_lies_on_a_circle(tmp_path: Path) -> None:
    corners = ((1, "", 500.0), (2, "a", 2500.0))
    session = SyntheticSession(2026, 1, "Q", "Sample Grand Prix", "Nowhere", corners=corners)
    write_session(session, tmp_path)
    radius = session.track_length_m / (2 * np.pi)
    track = pd.read_parquet(store.table_path("tracks", session.key, tmp_path)).iloc[0]
    assert track["length_m"] == session.track_length_m
    x, y = np.asarray(track["x_m"]), np.asarray(track["y_m"])
    assert len(x) == len(y) == session.n_grid
    assert np.allclose(np.hypot(x, y), radius, rtol=1e-5)
    assert np.allclose(track["curvature"], 1 / radius)
    turns = pd.read_parquet(store.table_path("corners", session.key, tmp_path))
    assert np.allclose(np.hypot(turns["x_m"], turns["y_m"]), radius)
    assert turns["x_m"].iloc[1] == pytest.approx(-radius)  # halfway round
    assert turns["angle"].tolist() == pytest.approx([36.0, 180.0])  # pointing outwards
