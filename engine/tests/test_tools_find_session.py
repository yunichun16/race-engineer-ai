"""find_session on a small synthetic catalog: the fields the model fills in (event, year,
session, round, recent), their defaults and errors, misspelt and ambiguous names, event text
that isn't a name, and the flags saying what the M4 results cover.

Three seasons. Two sprint weekends (the 2024 and 2026 Chinese GPs), Monaco in 2025 and 2026
(its location "Monte Carlo" in 2026, as FastF1 names it), and two events whose names share a
prefix (the Australian and Austrian Grands Prix in 2026). The newest session is the 2026
Austrian Grand Prix race, so 2026 is the newest processed season.
"""

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from race_engineer.inference import provenance
from race_engineer.tools.find_session import (
    EVENT_IS_A_NAME,
    FIND_SESSION,
    NOTHING_GIVEN,
    find_session,
    lookup,
    scored_sessions,
    style_seasons,
)
from race_engineer.tools.registry import DataPaths, run_tool
from race_engineer.tools.sessions import ToolInputError
from race_engineer.tools.synthetic import SyntheticSession, write_session

# (year, round, code, event, location, date)
SESSIONS = [
    (2024, 1, "Q", "Bahrain Grand Prix", "Sakhir", "2024-03-01"),
    (2024, 1, "R", "Bahrain Grand Prix", "Sakhir", "2024-03-02"),
    (2024, 5, "SQ", "Chinese Grand Prix", "Shanghai", "2024-04-19"),
    (2024, 5, "S", "Chinese Grand Prix", "Shanghai", "2024-04-20"),
    (2024, 5, "Q", "Chinese Grand Prix", "Shanghai", "2024-04-20"),
    (2024, 5, "R", "Chinese Grand Prix", "Shanghai", "2024-04-21"),
    (2024, 7, "Q", "Emilia Romagna Grand Prix", "Imola", "2024-05-18"),
    (2024, 7, "R", "Emilia Romagna Grand Prix", "Imola", "2024-05-19"),
    (2025, 1, "Q", "Australian Grand Prix", "Melbourne", "2025-03-15"),
    (2025, 1, "R", "Australian Grand Prix", "Melbourne", "2025-03-16"),
    (2025, 8, "Q", "Monaco Grand Prix", "Monaco", "2025-05-24"),
    (2025, 8, "R", "Monaco Grand Prix", "Monaco", "2025-05-25"),
    (2025, 16, "Q", "Italian Grand Prix", "Monza", "2025-09-06"),
    (2025, 16, "R", "Italian Grand Prix", "Monza", "2025-09-07"),
    (2026, 1, "Q", "Australian Grand Prix", "Melbourne", "2026-03-07"),
    (2026, 1, "R", "Australian Grand Prix", "Melbourne", "2026-03-08"),
    (2026, 2, "SQ", "Chinese Grand Prix", "Shanghai", "2026-03-12"),
    (2026, 2, "S", "Chinese Grand Prix", "Shanghai", "2026-03-13"),
    (2026, 2, "Q", "Chinese Grand Prix", "Shanghai", "2026-03-14"),
    (2026, 2, "R", "Chinese Grand Prix", "Shanghai", "2026-03-15"),
    (2026, 6, "Q", "Monaco Grand Prix", "Monte Carlo", "2026-06-06"),
    (2026, 6, "R", "Monaco Grand Prix", "Monte Carlo", "2026-06-07"),
    (2026, 11, "Q", "Austrian Grand Prix", "Spielberg", "2026-06-27"),
    (2026, 11, "R", "Austrian Grand Prix", "Spielberg", "2026-06-28"),
]
RUN = "run-1"
SCORED = [(2025, 16, "Q"), (2025, 16, "R"), (2026, 11, "R")]


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("processed")
    for year, round_, code, event, location, date in SESSIONS:
        write_session(SyntheticSession(year, round_, code, event, location, date=date), root)
    return root


def write_results(results: Path, run: str = RUN, scores_run: str = RUN) -> None:
    """A results folder as `make m4` leaves it: a manifest, the scores covering SCORED, the
    mistakes and the style tables (2025 only), each recording its scoring run."""
    results.mkdir(parents=True, exist_ok=True)
    (results / provenance.MANIFEST).write_text(json.dumps({"run_id": run}))
    scores = pd.DataFrame(SCORED, columns=["year", "round", "session"]).assign(score_pct=50.0)
    provenance.write_parquet(scores, results / "segment_scores.parquet", scores_run)
    provenance.write_parquet(scores.head(1), results / "mistakes.parquet", run)
    style = pd.DataFrame({"year": [2025, 2025], "driver": ["AAA", "BBB"]})
    provenance.write_parquet(style, results / "style_corners.parquet", run)


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> Path:
    results = tmp_path_factory.mktemp("results")
    write_results(results)
    return results


def fields(**given: Any) -> dict[str, Any]:
    """lookup's keyword arguments from the tool's field names (round is `round_` in Python)."""
    if "round" in given:
        given["round_"] = given.pop("round")
    return given


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # The mapping the description teaches, question -> fields.
        ({"recent": 0, "session": "R"}, "2026_11_R"),  # "last race"
        ({"recent": 0, "session": "Q"}, "2026_11_Q"),  # "latest qualifying"
        ({"recent": 1, "session": "R"}, "2026_06_R"),  # "the race before last"
        ({"event": "Monza", "year": 2025, "session": "Q"}, "2025_16_Q"),  # "Monza 2025 quali"
        ({"round": 5, "year": 2024}, "2024_05_R"),  # "round 5 2024": the race
        ({"event": "Monaco", "year": 2025, "session": "R"}, "2025_08_R"),  # "last year's Monaco"
        # recent counts back among the session kind given, else among every session.
        ({"recent": 2, "session": "R"}, "2026_02_R"),
        ({"recent": 0, "session": "sprint"}, "2026_02_S"),
        ({"recent": 1, "session": "S"}, "2024_05_S"),
        ({"recent": 0, "session": "SQ"}, "2026_02_SQ"),
        ({"recent": 0, "session": "quali"}, "2026_11_Q"),
        ({"recent": 0}, "2026_11_R"),  # the newest session, whatever its kind
        ({"recent": 1}, "2026_11_Q"),  # the one before it
        # With year, only that season's; with event, only that event's.
        ({"recent": 0, "year": 2024, "session": "R"}, "2024_07_R"),  # "last race of 2024"
        ({"recent": 1, "year": 2025, "session": "R"}, "2025_08_R"),
        ({"recent": 0, "event": "Monaco", "session": "R"}, "2026_06_R"),
        ({"recent": 1, "event": "Monaco", "session": "R"}, "2025_08_R"),
        ({"recent": 0, "event": "Shanghai"}, "2026_02_R"),
        ({"recent": 1, "event": "shanghai", "session": "sprint"}, "2024_05_S"),
        ({"recent": 0, "event": "Monaco", "year": 2025}, "2025_08_R"),
        # Round: in year, else the newest processed season (with an event, its newest season);
        # the race unless the session is given.
        ({"round": 5, "year": 2024, "session": "SQ"}, "2024_05_SQ"),
        ({"round": 2}, "2026_02_R"),
        ({"round": 16, "event": "Monza"}, "2025_16_R"),
        ({"round": 8, "event": "monaco", "year": 2025, "session": "Q"}, "2025_08_Q"),
        # Event alone: the newest season with it, qualifying unless the session is given.
        ({"event": "Monaco"}, "2026_06_Q"),
        ({"event": "monte carlo"}, "2026_06_Q"),
        # Monaco's location is "Monaco" until 2025: "Monte Carlo" names the same circuit then.
        ({"event": "Monte Carlo", "year": 2025, "session": "R"}, "2025_08_R"),
        ({"recent": 1, "event": "Monte Carlo", "session": "R"}, "2025_08_R"),
        ({"event": "Shanghai", "session": "sprint"}, "2026_02_S"),
        ({"event": "melbourne", "year": 2025}, "2025_01_Q"),
        ({"event": "Italy", "session": "R"}, "2025_16_R"),  # a country
        ({"event": "AUSTRIA"}, "2026_11_Q"),
        # A year or session in the event text still reads, as in the other tools.
        ({"event": "Monza 2025 quali"}, "2025_16_Q"),
        ({"event": "monaco race", "year": 2025}, "2025_08_R"),
        ({"event": "Monza R 2025", "session": "race"}, "2025_16_R"),
        # Session keys, as data.key gives them.
        ({"event": "2025_16_Q"}, "2025_16_Q"),
        ({"event": "2026_2_SQ"}, "2026_02_SQ"),
        ({"event": "2025_16_q", "year": 2025, "session": "qualifying"}, "2025_16_Q"),
        # An empty event is no event.
        ({"event": "", "recent": 0, "session": "R"}, "2026_11_R"),
    ],
)
def test_resolves(root: Path, given: dict[str, Any], expected: str) -> None:
    found = lookup(**fields(**given), root=root)
    assert found.match is not None and found.match.key == expected, found.problem
    assert found.candidates == [] and found.problem == ""


def test_reads_are_labelled(root: Path) -> None:
    assert lookup(recent=0, session="R", root=root).how == "recent"
    assert lookup(round_=7, year=2024, root=root).how == "round"
    assert lookup("monza", root=root).how == "name"
    assert lookup("2025_16_Q", root=root).how == "key"


@pytest.mark.parametrize(
    ("given", "message", "candidates"),
    [
        # "austr" fits the Australian and the Austrian Grands Prix of 2026.
        ({"event": "austr"}, "matches several 2026 events", ["2026_11_Q", "2026_01_Q"]),
        (
            {"event": "austr", "recent": 0, "session": "R"},
            "matches several 2026 events",
            ["2026_11_R", "2026_01_R"],
        ),
        (
            {"event": "grand prix", "year": 2025, "session": "R"},
            "matches several 2025 events",
            ["2025_16_R", "2025_08_R", "2025_01_R"],
        ),
        # Near names give their newest weekend's qualifying, or the session asked for, the
        # closest first: Imola is spelt right, Monaco isn't, so the older Imola comes first.
        ({"event": "monzza"}, "Did you mean: Italian Grand Prix (Monza)?", ["2025_16_Q"]),
        ({"event": "monzza", "session": "R"}, "(Monza)", ["2025_16_R"]),
        ({"event": "spielbrg"}, "Did you mean: Austrian Grand Prix (Spielberg)", ["2026_11_Q"]),
        ({"event": "monacco imola"}, "No processed event matches", ["2024_07_Q", "2026_06_Q"]),
        # Equally close to "monza" and to the "monte" of Monte Carlo: the whole name first.
        (
            {"event": "monze"},
            "Did you mean: Italian Grand Prix (Monza); Monaco Grand Prix (Monte Carlo)?",
            ["2025_16_Q", "2026_06_Q"],
        ),
        # A near name with a season: that season's weekend, else the event's newest.
        ({"event": "melbuorne"}, "Australian Grand Prix (Melbourne)", ["2026_01_Q"]),
        ({"event": "melbuorne", "year": 2025}, "Australian Grand Prix (Melbourne)", ["2025_01_Q"]),
        ({"event": "melbuorne 2025 race"}, "Australian Grand Prix (Melbourne)", ["2025_01_R"]),
        ({"event": "monzza", "year": 2025}, "(Monza)", ["2025_16_Q"]),
        ({"event": "imola", "year": 2026}, "Available season(s): 2024", ["2024_07_Q"]),
        # Monza never had a sprint: no candidate of another kind (see test_a_missing_kind_...).
        ({"event": "monza", "session": "sprint"}, "no processed 'sprint' session", []),
        # Rounds and counts the data doesn't have.
        ({"round": 30, "year": 2024}, "Processed rounds in 2024: 1, 5, 7", []),
        ({"round": 3}, "No processed session in 2026 round 3. Processed rounds in 2026: 1-2,", []),
        (
            {"round": 5, "event": "Monza"},
            "No processed session of 'Monza' in 2025 round 5: Italian Grand Prix (Monza) was "
            "round 16.",
            ["2025_16_Q"],
        ),
        ({"round": 1, "year": 2025, "session": "S"}, "no processed 'S' session", []),
        (
            {"recent": 10, "session": "R"},
            "Only 10 processed race (R) session(s): recent=10 goes back too far (0 is the "
            "latest, 9 the earliest).",
            [],
        ),
        ({"recent": 2, "session": "S"}, "Only 2 processed sprint (S) session(s)", []),
        (
            {"recent": 2, "event": "monaco", "session": "R"},
            "Only 2 processed race (R) session(s) of the Monaco Grand Prix",
            ["2026_06_R"],
        ),
        (
            {"recent": 0, "year": 2025, "session": "SQ"},
            "No processed sprint qualifying (SQ/SS) session in 2025.",
            [],
        ),
        # Seasons the data doesn't have.
        ({"recent": 0, "year": 2021}, "No processed session in 2021. Processed seasons: 2024,", []),
        ({"round": 1, "year": 2021}, "No processed session in 2021.", []),
        ({"event": "2025_30_R"}, "No processed session in 2025 round 30", []),
        ({"event": "2025_16_S"}, "The 2025 Italian Grand Prix has no processed 'S' session", []),
    ],
)
def test_no_single_match_gives_candidates(
    root: Path, given: dict[str, Any], message: str, candidates: list[str]
) -> None:
    found = lookup(**fields(**given), root=root)
    assert found.match is None
    assert message in found.problem
    assert [s.key for s in found.candidates] == candidates


@pytest.mark.parametrize(
    ("given", "problem"),
    [
        # The questions free-text parsing answered with a wrong session: "car" read as Monte
        # Carlo, "us" as the United States, a surname as a place. Put whole in event, as the
        # description says not to, they match nothing, and get no candidates either: the
        # closest names to "car" or "last" would be guesses.
        (
            {"event": "Was there a safety car in the last race?"},
            "'Was there a safety car in the last race?' is not an event name: 'was', 'there', "
            "'a', 'safety' and 3 more are in no Grand Prix, location or country name, nor close "
            "to one.",
        ),
        ({"event": "Tell us about the last race"}, "'tell', "),
        ({"event": "Where did Franco make mistakes in the last race?"}, "'where', 'did', "),
        ({"event": "Who won in Monaco?"}, "'who', 'won' and 'in' are in no Grand Prix"),
        ({"event": "last race"}, "'last' is in no Grand Prix, location or country name, nor"),
        ({"event": "last race", "recent": 0}, "'last' is in no Grand Prix"),
        # A name with other words, a driver, a short word, practice.
        ({"event": "Max at Monza"}, "'max' and 'at' are in no Grand Prix"),
        ({"event": "Lewis", "year": 2025}, "'lewis' is in no Grand Prix"),
        ({"event": "Monaco me", "session": "R"}, "'me' is in no Grand Prix"),
        (
            {"event": "melbourne fp2"},
            "'fp2' is in no Grand Prix, location or country name, nor close to one. Practice "
            "sessions are not in the dataset.",
        ),
        # A misspelt session word is named as one.
        (
            {"event": "melbourne sprnt"},
            "'sprnt' is in no Grand Prix, location or country name, nor close to one ('sprnt' "
            "looks like 'sprint': the session goes in session).",
        ),
        # Only a session or a season.
        ({"event": "race", "recent": 0}, "Give an event name or location"),
        ({"event": "2021"}, "No processed session in 2021. Processed seasons: 2024, 2025, 2026."),
    ],
)
def test_event_text_that_is_not_a_name(
    root: Path, results: Path, given: dict[str, Any], problem: str
) -> None:
    found = find_session(**fields(**given), root=root, results=results)
    assert found.data["match"] is None and found.data["weekend"] == []
    assert found.data["candidates"] == [] and found.found.not_a_name
    assert problem in found.found.problem
    lines = found.summary.splitlines()
    assert lines[0] == f"No single session matches: {found.found.problem}"
    assert lines[1] == EVENT_IS_A_NAME  # how to fill the fields, then call again
    assert "ask the user" not in found.summary.lower()


@pytest.mark.parametrize(
    ("given", "message"),
    [
        ({}, NOTHING_GIVEN),
        ({"year": 2025}, NOTHING_GIVEN),
        ({"session": "R"}, NOTHING_GIVEN),
        ({"event": "   ", "year": 2025}, NOTHING_GIVEN),
        ({"round": 5, "recent": 0}, "Give round or recent, not both"),
        ({"event": "2025_16_Q", "year": 2024}, "The event text says 2025 but year is 2024"),
        ({"event": "2025_16_Q", "session": "R"}, "names the 'Q' session but session is 'R'"),
        ({"event": "2025_16_Q", "recent": 0}, "'2025_16_Q' is a session key"),
        ({"event": "2025_16_Q", "round": 16}, "'2025_16_Q' is a session key"),
        ({"event": "monza 2025", "year": 2024}, "The event text says 2025 but year is 2024"),
        ({"event": "monza 2024 2025"}, "names more than one season"),
        ({"event": "monza race", "session": "Q"}, "names the 'race' session but session is 'Q'"),
        ({"recent": 0, "session": "fp2"}, "Practice sessions are not in the dataset"),
        ({"round": 1, "session": "warm-up"}, "Unknown session 'warm-up'"),
    ],
)
def test_missing_or_contradicting_fields_are_errors(
    root: Path, given: dict[str, Any], message: str
) -> None:
    with pytest.raises(ToolInputError, match=re.escape(message)) as err:
        lookup(**fields(**given), root=root)
    assert "\n" not in str(err.value)  # one line


def test_match_data_and_summary(root: Path, results: Path) -> None:
    found = find_session("Monza", 2025, "Q", root=root, results=results)
    match = found.data["match"]
    assert match == {
        "key": "2025_16_Q",
        "year": 2025,
        "round": 16,
        "session_code": "Q",
        "session_name": "Qualifying",
        "event": "Italian Grand Prix",
        "location": "Monza",
        "date": "2025-09-06",
        "scored": True,
        "style": True,
    }
    assert [s["key"] for s in found.data["weekend"]] == ["2025_16_Q", "2025_16_R"]
    assert found.data["candidates"] == []
    assert found.data["coverage"] == {
        "first": "2024 Bahrain Grand Prix (Q)",
        "last": "2026 Austrian Grand Prix (R)",
        "newest_season": 2026,
        "sessions": 24,
    }
    assert found.summary.splitlines() == [
        "2025 Italian Grand Prix, Qualifying (Q), 2025-09-06, Monza: round 16. "
        "That weekend: Qualifying (Q), Race (R).",
        "Mistake scores: yes. Driving-style comparisons for 2025: yes.",
        "Use event='Italian Grand Prix', year=2025, session='Q' in other tools.",
        "The data ends with the 2026 Austrian Grand Prix, Race (2026-06-28), so 2026 is the "
        "newest processed season; recent counts back from that session.",
    ]


def test_recent_summary_names_the_newest_session(root: Path, results: Path) -> None:
    found = find_session(recent=0, session="R", root=root, results=results)
    assert "The data ends with the 2026 Austrian Grand Prix, Race (2026-06-28)" in found.summary
    assert found.data["match"] is not None and found.data["match"]["scored"] is True
    assert found.data["match"]["style"] is False  # 2026 isn't in the style tables


def test_sprint_weekend_lists_every_session(root: Path, results: Path) -> None:
    found = find_session("shanghai", session="sprint", root=root, results=results)
    assert [s["session_code"] for s in found.data["weekend"]] == ["SQ", "S", "Q", "R"]
    assert found.data["match"] is not None and found.data["match"]["scored"] is False
    assert "Mistake scores: no (not in the last scoring run)." in found.summary


def test_no_match_summary_lists_candidates(root: Path, results: Path) -> None:
    found = find_session("austr", root=root, results=results)
    assert found.data["match"] is None and found.data["weekend"] == []
    assert [c["key"] for c in found.data["candidates"]] == ["2026_11_Q", "2026_01_Q"]
    lines = found.summary.splitlines()
    assert lines[0].startswith("No single session matches: 'austr' matches several 2026 events")
    assert lines[1] == (
        "Candidates, best match first: 2026 Austrian Grand Prix, Qualifying (Q, 2026-06-27); "
        "2026 Australian Grand Prix, Qualifying (Q, 2026-03-07)."
    )
    assert lines[2].startswith(
        "If one is plainly the event the user named, call again with its event and year; "
        "otherwise ask the user which they mean."
    )
    assert "2026 is the newest processed season" in lines[2]
    assert len(found.summary) < 1200
    # A misspelt name is a name: candidates, and no lesson on what event takes.
    misspelt = find_session("monzza", root=root, results=results)
    assert [c["key"] for c in misspelt.data["candidates"]] == ["2025_16_Q"]
    assert EVENT_IS_A_NAME not in misspelt.summary and not misspelt.found.not_a_name


def test_another_season_is_not_offered_as_the_answer(root: Path, results: Path) -> None:
    """An event without a session in the season asked for gets its other seasons' sessions, to
    tell the user about: calling again with one would answer for a season they didn't ask
    for."""
    found = find_session("imola", 2026, root=root, results=results)
    assert [c["key"] for c in found.data["candidates"]] == ["2024_07_Q"]
    assert found.found.other_season == 2026
    lines = found.summary.splitlines()
    assert lines[0] == (
        "No single session matches: Emilia Romagna Grand Prix: no processed session in 2026. "
        "Available season(s): 2024."
    )
    assert lines[1] == (
        "In other seasons: 2024 Emilia Romagna Grand Prix, Qualifying (Q, 2024-05-18)."
    )
    assert lines[2].startswith(
        "None is in 2026, the season asked for: tell the user the data has no such session "
        "then, or ask whether another season will do; don't answer for another season unless "
        "they agree."
    )
    assert "call again" not in found.summary
    # The same with recent, and for a misspelt name the season asked for doesn't have.
    assert lookup("imola", 2025, recent=0, root=root).other_season == 2025
    misspelt = lookup("monzza", 2024, root=root)
    assert [s.key for s in misspelt.candidates] == ["2025_16_Q"]
    assert misspelt.other_season == 2024
    # Candidates in the season asked for are candidates.
    assert lookup("melbuorne", 2025, root=root).other_season is None
    assert lookup("austr", root=root).other_season is None


def test_a_missing_kind_of_session_is_not_offered_as_another(
    root: Path, tmp_path: Path, results: Path
) -> None:
    """The event is plain but its weekend had no session of the kind asked for (a Monza
    sprint): the answer says so and offers only that event's weekends that had one, never the
    weekend's qualifying, and doesn't tell the model to call again."""
    for given in (
        {"event": "monza", "session": "S"},
        {"event": "monza", "recent": 0, "session": "S"},
    ):
        found = find_session(**fields(**given), root=root, results=results)
        assert found.data["match"] is None and found.data["candidates"] == []
        assert found.found.missing_kind
        assert "Tell the user there was no such session, or ask which session they mean" in (
            found.summary
        )
        assert "call again" not in found.summary
    # An older weekend of the event without the session, when newer ones had it: those.
    for year, round_, codes in [
        (2022, 5, ["Q", "R"]),
        (2024, 6, ["S", "Q", "R"]),
        (2025, 6, ["S", "Q", "R"]),
    ]:
        for code in codes:
            write_session(
                SyntheticSession(year, round_, code, "Miami Grand Prix", "Miami"), tmp_path
            )
    for given in (
        {"event": "Miami", "year": 2022, "session": "sprint"},
        {"event": "Miami", "year": 2022, "recent": 0, "session": "S"},
    ):
        found = find_session(**fields(**given), root=tmp_path, results=results)
        assert [c["key"] for c in found.data["candidates"]] == ["2025_06_S", "2024_06_S"]
        assert found.found.missing_kind
        assert "Weekends of that event that had one: 2025 Miami Grand Prix, Sprint (S" in (
            found.summary
        )
        assert "(or which of these weekends)" in found.summary
        assert "call again" not in found.summary


def test_a_newer_event_at_the_same_place_is_named(tmp_path: Path, results: Path) -> None:
    """Without a season, a name gives the newest season it matches, which may not be the newest
    event held there: the answer says so."""
    for year, round_, event in [(2025, 9, "Spanish Grand Prix"), (2026, 7, "Barcelona Grand Prix")]:
        write_session(SyntheticSession(year, round_, "Q", event, "Barcelona"), tmp_path)
    found = find_session("Barcelona Spain", root=tmp_path, results=results)
    assert found.data["match"] is not None and found.data["match"]["key"] == "2025_09_Q"
    assert found.found.newer_here is not None and found.found.newer_here.key == "2026_07_Q"
    assert (
        "The 2026 Barcelona Grand Prix was also held at Barcelona: event='Barcelona Grand "
        "Prix', year=2026." in found.summary.splitlines()
    )
    # Not when the season was asked for, or the newest event there was found.
    assert lookup("Barcelona Spain", 2025, root=tmp_path).newer_here is None
    assert lookup("Spain 2025", root=tmp_path).newer_here is None
    newest = lookup("Barcelona", root=tmp_path)
    assert newest.match is not None and newest.match.key == "2026_07_Q"
    assert newest.newer_here is None


def test_other_grands_prix_in_the_country_are_named(tmp_path: Path, results: Path) -> None:
    """A country with two Grands Prix in a season gives the one named after it, and the answer
    names the other ("Spain" in 2026 is the Spanish GP in Madrid; the Barcelona GP was too)."""
    for round_, event, location in [
        (7, "Barcelona Grand Prix", "Barcelona"),
        (14, "Spanish Grand Prix", "Madrid"),
    ]:
        write_session(SyntheticSession(2026, round_, "R", event, location), tmp_path)
    found = find_session("Spain", 2026, "R", root=tmp_path, results=results)
    assert found.data["match"] is not None and found.data["match"]["key"] == "2026_14_R"
    assert [s.key for s in found.found.same_country] == ["2026_07_R"]
    assert (
        "The 2026 Barcelona Grand Prix (Barcelona) was also held in that country: "
        "event='Barcelona Grand Prix', year=2026." in found.summary.splitlines()
    )
    # Not for a Grand Prix or a place.
    assert lookup("Spanish Grand Prix", root=tmp_path).same_country == ()
    assert lookup("Madrid", root=tmp_path).same_country == ()


def test_stale_results_mean_nothing_is_scored(root: Path, tmp_path: Path) -> None:
    write_results(tmp_path, run="run-2", scores_run="run-1")  # rescored, not explained yet
    write_results(tmp_path, run="run-2", scores_run="run-2")
    (tmp_path / "mistakes.parquet").unlink()  # explain failed after the new scoring run
    found = find_session("Monza", 2025, "Q", root=root, results=tmp_path)
    assert found.data["match"] is not None and found.data["match"]["scored"] is False
    assert re.search(
        r"Mistake scores: unavailable, the M4 results need rebuilding \(.*explain", (found.summary)
    )
    assert style_seasons(tmp_path) == frozenset({2025})


def test_missing_results(root: Path, tmp_path: Path) -> None:
    assert scored_sessions(tmp_path)[0] == frozenset()
    assert style_seasons(tmp_path) == frozenset()
    found = find_session(recent=0, session="R", root=root, results=tmp_path)
    assert found.data["match"] is not None and found.data["match"]["key"] == "2026_11_R"


def test_scored_sets_follow_a_new_scoring_run(tmp_path: Path) -> None:
    write_results(tmp_path)
    assert (2025, 16, "Q") in scored_sessions(tmp_path)[0]
    write_results(tmp_path, run="run-2", scores_run="run-1")  # the old scores are stale now
    scored, problem = scored_sessions(tmp_path)
    assert scored == frozenset() and "another scoring run" in problem


def test_through_the_registry(root: Path, results: Path) -> None:
    paths = DataPaths(root, results)
    out = run_tool(FIND_SESSION, {"recent": 0, "session": "R"}, paths)
    assert out.data is not None and out.data["match"]["key"] == "2026_11_R"
    assert out.data == json.loads(json.dumps(out.data))  # JSON-ready
    out = run_tool(FIND_SESSION, {"round": 5, "year": 2024}, paths)
    assert out.data is not None and out.data["match"]["key"] == "2024_05_R"
    schema = FIND_SESSION.input_schema()
    assert list(schema["properties"]) == ["event", "year", "session", "round", "recent"]
    assert "required" not in schema
    assert FIND_SESSION.chart is None
    assert FIND_SESSION.warm is not None
    FIND_SESSION.warm(paths)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"recent": -1}, "recent: must be at least 0"),
        ({"recent": 31}, "recent: must be at most 30"),
        ({"round": 0}, "round: must be at least 1"),
        ({"round": 31}, "round: must be at most 30"),
        ({"year": 2017, "recent": 0}, "year: must be at least 2018"),
        ({"event": "x" * 101}, "event: must have at most 100 characters"),
        # true is not 1: recent=true would have been the session before last.
        ({"recent": True, "session": "R"}, "recent: must be a whole number"),
        ({"round": True, "year": 2024}, "round: must be a whole number"),
        ({"year": False, "recent": 0}, "year: must be a whole number"),
        # The old free-text parameter is refused, not dropped.
        ({"query": "last race"}, "unknown parameter(s) query (parameters: event, year, session"),
        ({}, NOTHING_GIVEN),
    ],
)
def test_registry_refuses_bad_fields(
    root: Path, results: Path, args: dict[str, Any], message: str
) -> None:
    with pytest.raises(ToolInputError, match=re.escape(message)):
        run_tool(FIND_SESSION, args, DataPaths(root, results))


def test_descriptions_teach_the_fields() -> None:
    """The model learns the mapping from the descriptions alone, so they carry the examples."""
    text = FIND_SESSION.description
    for example in (
        "'last race' -> recent=0, session='R'",
        "'latest qualifying' -> recent=0, session='Q'",
        "'the race before last' -> recent=1, session='R'",
        "'Monza 2025 quali' -> event='Monza', year=2025, session='Q'",
        "'round 5 2024' -> round=5, year=2024",
        "'last year's Monaco race' -> event='Monaco', year=<last year>, session='R'",
        "never put the rest of the question in event",
        "recent counts every session kind unless session is given",
        "count a relative year from today's date when you know it, else from that season",
    ):
        assert example in text, example
    properties = FIND_SESSION.input_schema()["properties"]
    for name, example in (
        ("event", "'Monza', 'Silverstone', 'Abu Dhabi', 'USA', '2025_16_Q'"),
        ("event", "Never the rest of the question"),
        ("event", "Spell a name you recognise correctly ('silverston' -> 'Silverstone')"),
        (
            "year",
            "'last year's Monaco race', asked in 2026 -> event='Monaco', year=2025, session='R'",
        ),
        ("year", "else from the newest processed season, which every answer names"),
        ("session", "every session kind with recent"),
        ("round", "'round 5 2024' -> round=5, year=2024"),
        ("round", "with event, of the newest season with that event"),
        ("recent", "'last race' -> recent=0, session='R'"),
        ("recent", "'the race before last' -> recent=1, session='R'"),
        ("recent", "'N races ago' -> recent=N-1 ('2 races ago' is the race before last)"),
        ("recent", "without it, every session"),
    ):
        assert example in properties[name]["description"], (name, example)
