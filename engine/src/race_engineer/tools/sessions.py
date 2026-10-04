"""Find a processed session from the loose way people name it ("melbourne quali", "monza race").

The MCP tools receive event and session names as free text from the model, so matching is
forgiving (case, accents, initials, partial words, country and circuit names, session keys such
as "2025_16_Q"), and a miss lists the nearest options rather than just failing. The event text
is a name, not a question: every word of it must match an event or location name.
"""

from __future__ import annotations

import difflib
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from race_engineer.config import PROCESSED_DIR, QUALI_CODES
from race_engineer.data import store
from race_engineer.tools.cache import listing_signature, memo_by_files


class ToolInputError(ValueError):
    """A request the data can't answer (unknown event, driver or lap); the message says why."""


class ResultsUnavailableError(ToolInputError):
    """The M4 results a tool needs are missing or come from another scoring run; the message
    says how to rebuild them. A ToolInputError, so every `except ToolInputError` still relays it
    to the user; the REST API tells them apart and answers 503 (the server's data needs
    rebuilding) instead of 422 (the request needs changing)."""


@dataclass(frozen=True)
class SessionInfo:
    year: int
    round: int
    code: str  # Q, SQ, SS, S or R
    event: str  # e.g. "Australian Grand Prix"
    location: str  # e.g. "Melbourne"
    name: str  # FastF1 session name, e.g. "Sprint Qualifying"
    date: str  # UTC date, YYYY-MM-DD
    track_length_m: float
    grid_step_m: float

    @property
    def key(self) -> str:
        return store.session_key(self.year, self.round, self.code)

    @property
    def is_quali(self) -> bool:
        return self.code in QUALI_CODES

    @property
    def label(self) -> str:
        return f"{self.year} {self.event} {self.name}"


# Session codes each phrase can mean. Sprint qualifying was called the Sprint Shootout (SS)
# in 2023, so every sprint qualifying phrase accepts either code.
_SPRINT_QUALI = ("SQ", "SS")
SESSION_ALIASES: dict[str, tuple[str, ...]] = {
    "q": ("Q",),
    "quali": ("Q",),
    "qualy": ("Q",),
    "qually": ("Q",),
    "qualis": ("Q",),
    "qualies": ("Q",),
    "quals": ("Q",),
    "qualifying": ("Q",),
    "q1": ("Q",),  # the parts of qualifying, which the data holds as one session
    "q2": ("Q",),
    "q3": ("Q",),
    "r": ("R",),
    "race": ("R",),
    "gp": ("R",),
    "grand prix": ("R",),
    "s": ("S",),
    "sprint": ("S",),
    "sprint race": ("S",),
    "sq": _SPRINT_QUALI,
    "sq1": _SPRINT_QUALI,  # the parts of sprint qualifying
    "sq2": _SPRINT_QUALI,
    "sq3": _SPRINT_QUALI,
    "ss": _SPRINT_QUALI,
    "sprint quali": _SPRINT_QUALI,
    "sprint qualy": _SPRINT_QUALI,
    "sprint qualifying": _SPRINT_QUALI,
    "sprint shootout": _SPRINT_QUALI,
    "shootout": _SPRINT_QUALI,
}
SESSION_CHOICES = "Q (qualifying), R (race), S (sprint) or SQ (sprint qualifying)"
PRACTICE = re.compile(r"practice|\bfp ?[123]\b")  # in normalized text; not in the dataset
# Session phrases that may ride along in the event text ("melbourne quali"). Not the one- and
# two-letter codes (SESSION_CODE finds those), nor "gp" / "grand prix", which are part of every
# event name.
EVENT_TEXT_SESSIONS = sorted(
    (k for k in SESSION_ALIASES if len(k) > 2 and k not in {"grand prix"}), key=len, reverse=True
)
# The codes, as words of their own in normalized text ("monza r 2025", "q3", "sq1"); not the
# "r" of "r 5", which is round 5.
SESSION_CODE = re.compile(r"\b(q[123]?|sq[123]?|ss|s|r(?!\s*\d{1,2}\b))\b")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# A session key as find_session returns it in data.key ("2025_16_Q"), as the whole event text.
SESSION_KEY = re.compile(r"\s*(20\d{2})_(\d{1,2})_(q|r|s|sq|ss)\s*", re.IGNORECASE)
# Dotted initials ("U.S.", "U.S", "U.S.A.", "G.P."): single letters joined by dots, the last
# dot optional. Not a first name's initial ("S. Perez"), which no other single letter follows,
# nor two sets of initials ("U.S. G.P." is "us gp").
DOTTED_INITIALS = re.compile(r"\b[a-z](?:\.[a-z]\b)+\.?")
# Place initials written with spaces ("U S", "U. S.", "U S A", "U K", "U A E"), in normalized
# text. No other single letters are joined: they may be session codes ("Monza R S").
SPACED_INITIALS = re.compile(r"\bu (s a|s|k|a e)\b")
# Place names whose first word is cut to an "S", which would read as the sprint ("S. Paulo",
# "S Arabia"), in normalized text: the word the "s" stands for, by the word after it.
CUT_WORDS = {"paulo": "sao", "arabia": "saudi", "arabian": "saudi"}
CUT_INITIAL = re.compile(rf"\bs (?=({'|'.join(CUT_WORDS)})\b)")

# Country, adjective, state and circuit names that don't appear in FastF1's event or location
# names ("china" -> "Chinese Grand Prix", "brazilian" -> "São Paulo", "hungaroring" ->
# "Budapest"). A country that is the start of its event's name ("japan", "austria") is here too,
# as a whole name a misspelling ("japn") is compared with.
PLACE_ALIASES = {
    "albert park": "melbourne",
    "america": "united states",
    "american": "united states",
    "australia": "australian",
    "austria": "austrian",
    "azerbaijani": "azerbaijan",
    "bahraini": "bahrain",
    "baku city": "baku",
    "baku city circuit": "baku",
    "belgium": "belgian",
    "brasil": "sao paulo",
    "brazil": "sao paulo",
    "brazilian": "sao paulo",
    "britain": "british",
    "canada": "canadian",
    "catalonia": "barcelona",
    "catalunya": "barcelona",
    "cdmx": "mexico city",
    "china": "chinese",
    "circuit of the americas": "austin",
    "cota": "austin",
    "emirati": "abu dhabi",
    "england": "british",
    "florida": "miami",
    "france": "french",
    "gilles villeneuve": "montreal",
    "great britain": "british",
    "hermanos rodriguez": "mexico city",
    "holland": "dutch",
    "hungary": "hungarian",
    "hungaroring": "budapest",
    "interlagos": "sao paulo",
    "italy": "italian",
    "japan": "japanese",
    "ksa": "saudi arabian",
    "losail": "lusail",
    "lv": "las vegas",  # "LV GP", which matched inside "Silverstone" before INSIDE_MIN
    "madring": "madrid",
    "marina bay": "singapore",
    "mexican": "mexico city",
    "monegasque": "monaco",
    "montmelo": "barcelona",
    "netherlands": "dutch",
    "nevada": "las vegas",
    "paul ricard": "le castellet",
    "qatari": "qatar",
    "red bull ring": "spielberg",
    "san marino": "imola",
    "saudi arabia": "saudi arabian",
    "singaporean": "singapore",
    "spain": "spanish",
    "texas": "austin",
    "uae": "abu dhabi",
    "uk": "british",
    "us": "united states",
    "usa": "united states",
    "usgp": "united states grand prix",
    "yas marina": "abu dhabi",
}


def normalize(text: str) -> str:
    """Lower case, no accents, words separated by single spaces ("Montréal" -> "montreal"),
    without possessives ("Hamilton's" -> "hamilton", not a stray "s" that reads as the sprint)
    and with initials joined, the last dot optional ("U.S.", "U.S" -> "us"; "U.S.GP" -> "us
    gp") and place initials also when spaced ("U S", "U. S." -> "us"), and a first word cut to
    its initial written out ("S. Paulo" -> "sao paulo"), so no single letter is left over to
    read as a session."""
    text = re.sub(r"(?<=\w)['\u2019]s\b", "", text, flags=re.IGNORECASE)
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    plain = DOTTED_INITIALS.sub(lambda m: f" {re.sub('[^a-z]', '', m.group())} ", plain)
    plain = " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
    plain = SPACED_INITIALS.sub(lambda m: "u" + m.group(1).replace(" ", ""), plain)
    return CUT_INITIAL.sub(lambda m: f"{CUT_WORDS[m.group(1)]} ", plain)


def session_codes(text: str | None) -> tuple[str, ...]:
    """The session codes a phrase like "Quali" or "sprint shootout" can mean."""
    key = normalize(text or "q")
    if key in SESSION_ALIASES:
        return SESSION_ALIASES[key]
    raise ToolInputError(f"Unknown session {text!r}. Use {SESSION_CHOICES}.{practice_note(key)}")


def practice_note(text: str) -> str:
    """ " Practice sessions are not in the dataset." when normalized text names one ("fp2"), else
    ""."""
    return " Practice sessions are not in the dataset." if PRACTICE.search(text) else ""


def session_phrase(text: str) -> str | None:
    """The session phrase in normalized text: the most specific one ("sprint qualifying" before
    "sprint"), else a code as a word of its own ("monza r" -> "r"). None when there's none."""
    padded = f" {text} "
    named = next((k for k in EVENT_TEXT_SESSIONS if f" {k} " in padded), None)
    code = SESSION_CODE.search(text)
    return named or (code.group(1) if code else None)


def split_event_text(event: str) -> tuple[str, int | None, str | None]:
    """The event text without a year or session phrase in it, and what they were:
    "Australian GP 2025 race" -> ("australian gp", 2025, "race"); "Monza R 2025" -> ("monza",
    2025, "r")."""
    years = {int(y) for y in YEAR.findall(event)}
    if len(years) > 1:
        raise ToolInputError(f"{event!r} names more than one season. Give one year.")
    text = normalize(YEAR.sub(" ", event))
    named = session_phrase(text)
    if named in EVENT_TEXT_SESSIONS:
        text = f" {text} ".replace(f" {named} ", " ", 1)
    elif named:
        text = SESSION_CODE.sub(" ", text, count=1)
    return " ".join(text.split()), years.pop() if years else None, named


def event_query(text: str) -> list[str]:
    """The words to look for in event and location names, with place aliases applied."""
    query = f" {normalize(text)} ".replace(" gp ", " grand prix ")
    for alias in sorted(PLACE_ALIASES, key=len, reverse=True):  # "great britain" before "uk"
        query = query.replace(f" {alias} ", f" {PLACE_ALIASES[alias]} ")
    return query.split()


# The shortest word that may match the start of a name's word: one or two letters are the start
# of a name by chance ("it" of Italian, "me" of Melbourne, a stray "r" of Romagna).
PREFIX_MIN = 3
# The shortest word that may match inside a name rather than at the start of one of its words:
# a driver code or another short word is part of a name by chance ("VER" in Silverstone, "GAS"
# in Las Vegas, "SAI" in Lusail, "Audi" in Saudi).
INSIDE_MIN = 5


def match_tier(query: list[str], info: SessionInfo) -> int | None:
    """How well the query words match a session's event and location names: 0 = whole words,
    1 = word prefixes from PREFIX_MIN letters ("australia"), 2 = anywhere, from INSIDE_MIN
    letters ("rstone"); None = no match. Keeps "spa" from matching "Spanish" when
    Spa-Francorchamps is there. Shorter words only match a whole word: "it" is not the start
    of "Italian", nor a stray "r" part of "Qatar"."""
    words = f"{normalize(info.event)} {normalize(info.location)}".split()
    text = " ".join(words)
    if all(q in words for q in query):
        return 0
    if all(
        q in words or (len(q) >= PREFIX_MIN and any(w.startswith(q) for w in words)) for q in query
    ):
        return 1
    if all(q in words or (len(q) >= INSIDE_MIN and q in text) for q in query):
        return 2
    return None


# What the catalog reads of each session's row in the sessions table.
CATALOG_COLUMNS = [
    "year",
    "round",
    "session",
    "event",
    "location",
    "session_name",
    "session_date",
    "track_length_m",
    "grid_step_m",
]
# Session files read at once. On a network filesystem (the deployed API's data volume) each file
# costs a few round trips, which one reader would wait for in turn, file after file.
READ_THREADS = 32


def _session_rows(path: str) -> pa.Table:
    """The catalog's columns of one session file (one row, about 14 KB), read whole: an open
    and a read, where reading the footer and then each column would be a round trip each."""
    with open(path, "rb") as file:
        data = file.read()
    return pq.ParquetFile(pa.BufferReader(data)).read(columns=CATALOG_COLUMNS)


def _read_sessions(folder: Path) -> pd.DataFrame:
    """The catalog's columns of every session file in `folder`, READ_THREADS files at a time.
    Only the sessions table: none of the other tables' files is listed or opened. Files whose
    column types differ (an int32 year beside int64 ones) are unified, as the DuckDB view's
    union_by_name did."""
    try:
        with os.scandir(folder) as entries:
            files = sorted(e.path for e in entries if e.name.endswith(".parquet"))
    except OSError:  # no sessions folder
        files = []
    if not files:
        raise ToolInputError(
            f"No processed sessions found in {folder.parent}. Build the dataset first (make data)."
        )
    with ThreadPoolExecutor(max_workers=min(READ_THREADS, len(files))) as pool:
        tables = list(pool.map(_session_rows, files))
    return pa.concat_tables(tables, promote_options="permissive").to_pandas()


# The sessions folder's time and listing, not a stat of every file: `make data` adds a session's
# file or renames a new one over it (data.store.write_table), which both show.
@memo_by_files(lambda a: a["root"] / "sessions", maxsize=4, signature=listing_signature)
def _catalog(root: Path) -> tuple[SessionInfo, ...]:
    """Every processed session in calendar order, read once per change of the sessions folder
    (0.05-0.1 s for 263 files locally, and every tool call resolves its session from it)."""
    frame = _read_sessions(root / "sessions")
    frame["session_date"] = pd.to_datetime(frame["session_date"], utc=True)
    frame = frame.sort_values(["year", "round", "session_date"])
    frame["date"] = frame["session_date"].dt.strftime("%Y-%m-%d")
    return tuple(
        SessionInfo(
            year=int(row["year"]),
            round=int(row["round"]),
            code=str(row["session"]),
            event=str(row["event"]),
            location=str(row["location"]),
            name=str(row["session_name"]),
            date=str(row["date"]),
            track_length_m=float(row["track_length_m"]),
            grid_step_m=float(row["grid_step_m"]),
        )
        for row in frame.to_dict("records")
    )


def list_sessions(year: int | None = None, root: Path = PROCESSED_DIR) -> list[SessionInfo]:
    """Every processed session (optionally of one season), in calendar order."""
    return [s for s in _catalog(root) if year is None or s.year == year]


def event_names(sessions: list[SessionInfo]) -> list[str]:
    """Distinct "Event (Location)" names, in order."""
    return list(dict.fromkeys(f"{s.event} ({s.location})" for s in sessions))


# Misspellings (suggest_sessions, unknown_words): how alike a text and a name must be
# (likeness), and the fewest letters either may have. A misspelt name scores 0.8 or more
# ("melborne" 0.94 to Melbourne, "vagas" 0.80 to Vegas), the ordinary words of a question less
# ("about" 0.55 to Austin, "car" 0.75 to Carlo), and a short word is close to a short name by
# chance ("last" is 0.86 to the "las" of Las Vegas). Session words are never compared: "race"
# is 0.8 to "france".
CLOSE = 0.8
FUZZY_MIN = 4


def _without_grand_prix(name: str) -> str:
    """A normalized name without "grand prix", which every event name shares."""
    return " ".join(w for w in name.split() if w not in {"grand", "prix"})


def _newest_of_each_event(sessions: list[SessionInfo]) -> list[SessionInfo]:
    """The newest session of each distinct event and location, in calendar order."""
    newest = {(s.event, s.location): s for s in sessions}  # in calendar order: the newest wins
    return sorted(newest.values(), key=lambda s: (s.year, s.round))


def _one_edit(a: str, b: str) -> bool:
    """Whether one letter added, dropped, changed or swapped with the next turns `a` into `b`."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
        swapped = len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]]
        return len(diff) == 1 or (swapped and a[diff[1]] == b[diff[0]])
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def likeness(text: str, name: str) -> float:
    """How alike a text and a name are, 0 to 1: difflib's ratio, and at least CLOSE when one
    letter added, dropped, changed or swapped with the next makes the name ("maimi" -> "miami",
    "baky" -> "baku"), which the ratio scores low in a short word (0.4 and 0.75)."""
    ratio = difflib.SequenceMatcher(None, text, name).ratio()
    return max(ratio, CLOSE) if _one_edit(text, name) else ratio


def _fuzzy_names(sessions: list[SessionInfo]) -> dict[str, SessionInfo]:
    """Names a misspelling may be of, each with the newest session it names: event names without
    "grand prix" and locations, the place aliases ("brazil", "cota"), then the words of the
    names ("carlo"); none with fewer than FUZZY_MIN letters. Whole names come first, so a tie
    in likeness goes to them: "monze" is as close to "monza" as to the "monte" of Monte Carlo."""
    wholes: dict[str, SessionInfo] = {}
    words: dict[str, SessionInfo] = {}
    newest = _newest_of_each_event(sessions)
    for s in newest:  # in calendar order: a name two events share names the newer
        for name in (_without_grand_prix(normalize(s.event)), normalize(s.location)):
            wholes[name] = s
            words.update({w: s for w in name.split()})
    names = {name: s for name, s in wholes.items() if len(name) >= FUZZY_MIN}
    for alias, target in PLACE_ALIASES.items():
        named = [s for s in newest if match_tier(target.split(), s) == 0]
        if named and len(alias) >= FUZZY_MIN:
            names.setdefault(alias, named[-1])
    for word, s in words.items():
        if len(word) >= FUZZY_MIN:
            names.setdefault(word, s)
    return names


def _fuzzy_texts(query_words: list[str]) -> list[str]:
    """What suggestions compare with names: the whole query, then each of its words, without
    "grand prix" and session words, from FUZZY_MIN letters."""
    words = [w for w in _without_grand_prix(" ".join(query_words)).split() if not _is_session(w)]
    return [t for t in dict.fromkeys([" ".join(words), *words]) if len(t) >= FUZZY_MIN]


def suggest_sessions(
    query_words: list[str], sessions: list[SessionInfo], n: int = 4
) -> list[SessionInfo]:
    """Up to `n` sessions of distinct events whose names, locations or aliases look like the
    query (misspellings: "melborne" -> Melbourne, "interlagoss" -> São Paulo), the newest session
    of each event, the closest first. The whole query and each of its words is compared, from
    FUZZY_MIN letters; only a likeness of CLOSE or more counts."""
    names = _fuzzy_names(sessions)
    alike = [(likeness(text, name), name) for text in _fuzzy_texts(query_words) for name in names]
    found: dict[str, SessionInfo] = {}  # "Event (Location)" -> its session, closest first
    for score, name in sorted(alike, key=lambda pair: -pair[0]):  # stable: whole names first
        if score < CLOSE:
            break
        found.setdefault(event_names([names[name]])[0], names[name])
    return list(found.values())[:n]


def unknown_words(query_words: list[str], sessions: list[SessionInfo]) -> list[str]:
    """The words of an event query that are in no event or location name, not even misspelt:
    no session matches the word alone (match_tier) and no name looks like it (CLOSE). Empty for
    a name, misspelt or not ("melborne", "abu dhabi"), a name of several words included when
    the whole query looks like it ("mexico cty"); for a question put in the event text, its
    ordinary words ("Was there a safety car" -> was, there, a, safety)."""
    newest = _newest_of_each_event(sessions)
    names = list(_fuzzy_names(sessions))

    def misspelt(text: str) -> bool:
        if len(text) < FUZZY_MIN or _is_session(text):
            return False
        return any(likeness(text, name) >= CLOSE for name in names)

    whole = " ".join(_fuzzy_texts(query_words)[:1])
    if " " in whole and any(likeness(whole, name) >= CLOSE for name in names if " " in name):
        return []
    return [
        word
        for word in dict.fromkeys(query_words)
        if not any(match_tier([word], s) is not None for s in newest) and not misspelt(word)
    ]


def session_like(word: str) -> str | None:
    """The session word a word looks misspelt from ("sprnt" -> "sprint"), else None."""
    if len(word) < FUZZY_MIN or _is_session(word):
        return None
    alike = [(likeness(word, k), k) for k in SESSION_ALIASES if len(k) >= FUZZY_MIN]
    score, closest = max(alike)
    return closest if score >= CLOSE else None


def _is_session(word: str) -> bool:
    """Whether a word is a session word ("race", "quali", "q"), which is no misspelt name."""
    return word in SESSION_ALIASES


def _no_event_error(event: str, query: list[str], sessions: list[SessionInfo]) -> ToolInputError:
    suggestions = event_names(suggest_sessions(query, sessions))
    hint = f" Did you mean: {'; '.join(suggestions)}?" if suggestions else ""
    return ToolInputError(
        f"No processed event matches {event!r}.{hint} Call list_sessions to see every event."
    )


def number_ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 5] -> "1-3, 5"."""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return ", ".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in runs)


def no_season_error(year: int | None, sessions: list[SessionInfo]) -> ToolInputError:
    """A season with no processed session, and the seasons that have one."""
    seasons = ", ".join(str(y) for y in sorted({s.year for s in sessions}))
    return ToolInputError(f"No processed session in {year}. Processed seasons: {seasons}.")


def no_round_error(year: int, round_: int, sessions: list[SessionInfo]) -> ToolInputError:
    """A round with no processed session in a season, and the rounds that have one."""
    rounds = sorted({s.round for s in sessions if s.year == year})
    if not rounds:
        return no_season_error(year, sessions)
    return ToolInputError(
        f"No processed session in {year} round {round_}. Processed rounds in {year}: "
        f"{number_ranges(rounds)}."
    )


class MissingSessionError(ToolInputError):
    """The event names one weekend, but that weekend has no session of the kind asked for (a
    Monza sprint). find_session tells the model to say so rather than offer another kind."""

    def __init__(self, message: str, weekend: list[SessionInfo], session: str) -> None:
        super().__init__(message)
        self.weekend = weekend
        self.session = session


def missing_session_error(weekend: list[SessionInfo], session: str) -> MissingSessionError:
    """A weekend without the session asked for, and the sessions it has."""
    available = ", ".join(f"{s.name} ({s.code})" for s in weekend)
    return MissingSessionError(
        f"The {weekend[0].year} {weekend[0].event} has no processed {session!r} session. "
        f"Available: {available}.",
        weekend,
        session,
    )


def several_events_error(event: str, sessions: list[SessionInfo]) -> ToolInputError:
    """The event text fits more than one weekend of a season (`sessions` are of that season)."""
    names = "; ".join(event_names(sessions))
    return ToolInputError(
        f"{event.strip()!r} matches several {sessions[0].year} events: {names}. "
        "Use a more specific name."
    )


def given_once(
    event: str, year: int | None = None, session: str | None = None
) -> tuple[int | None, str | None]:
    """The season and session meant: `year` and `session`, else what the event text names
    ("Monza 2025 quali", "2025_16_Q"). Given in both, they must agree: raises for "Monza 2025"
    with year=2024, "melbourne race" with session="Q" or "2025_16_Q" with year=2024."""
    key = SESSION_KEY.fullmatch(event)
    if key is not None:
        text_year, named = int(key.group(1)), key.group(3)
    else:
        _, text_year, named = split_event_text(event)
    if text_year is not None and year is not None and text_year != year:
        raise ToolInputError(
            f"The event text says {text_year} but year is {year}. Give the season once."
        )
    if named and session is not None and session_codes(session) != session_codes(named):
        raise ToolInputError(
            f"The event text names the {named!r} session but session is {session!r}. "
            "Give the session once."
        )
    return (year if year is not None else text_year), session or named


def by_key(
    event: str,
    year: int | None = None,
    session: str | None = None,
    root: Path = PROCESSED_DIR,
) -> SessionInfo | None:
    """The session a key names ("2025_16_Q", as find_session's data.key gives it), or None when
    `event` isn't a key. `year` and `session`, when given, must agree with it."""
    key = SESSION_KEY.fullmatch(event)
    if key is None:
        return None
    given_once(event, year, session)
    key_year, round_, codes = int(key.group(1)), int(key.group(2)), session_codes(key.group(3))
    sessions = list_sessions(root=root)
    weekend = [s for s in sessions if (s.year, s.round) == (key_year, round_)]
    if not weekend:
        raise no_round_error(key_year, round_, sessions)
    found = [s for s in weekend if s.code in codes]
    if not found:
        raise missing_session_error(weekend, key.group(3).upper())
    return found[0]


# How much a circuit's measured length may differ between seasons and still be the same
# circuit: 0.1-0.7% on the real data, while a Grand Prix that moved is far off (the Spanish GP's
# Barcelona and Madrid circuits differ by 16%).
SAME_TRACK = 0.02


def _same_circuit(matched: list[SessionInfo], everything: list[SessionInfo]) -> list[SessionInfo]:
    """`matched` and the other seasons of its events held at the same circuit under another
    location name, in calendar order. FastF1 names Monaco's location "Monte Carlo" from 2026 and
    Miami's "Miami Gardens" from 2025, so "Monte Carlo" with 2025 is the 2025 Monaco GP. The
    same circuit: the same event name and a track length within SAME_TRACK."""
    lengths: dict[str, list[float]] = {}
    for s in matched:
        lengths.setdefault(s.event, []).append(s.track_length_m)

    def same_circuit(s: SessionInfo) -> bool:
        return any(abs(s.track_length_m - m) <= SAME_TRACK * m for m in lengths.get(s.event, []))

    have = {id(s) for s in matched}
    return [s for s in everything if id(s) in have or same_circuit(s)]


def matched_names(matched: list[SessionInfo], everything: list[SessionInfo]) -> list[str]:
    """The events matched, by name, with the locations matched when the event was also held
    elsewhere: "Spanish Grand Prix (Madrid)" for "Madrid", since the Spanish GP was in Barcelona
    until 2025; "Monaco Grand Prix" for "Monaco", every season of it matched."""
    have = {id(s) for s in matched}
    names = []
    for event in dict.fromkeys(s.event for s in matched):
        if all(id(s) in have for s in everything if s.event == event):
            names.append(event)
        else:
            places = dict.fromkeys(s.location for s in matched if s.event == event)
            names.append(f"{event} ({' / '.join(places)})")
    return names


@dataclass(frozen=True)
class EventMatch:
    """What event text names: the sessions of the best-matching events (in `year`, when one is
    given), the season and session phrase given or found in the text, and the session codes
    (qualifying's when no session is named)."""

    sessions: list[SessionInfo]  # calendar order
    year: int | None
    session: str | None
    codes: tuple[str, ...]


def match_event(
    event: str,
    year: int | None = None,
    session: str | None = None,
    root: Path = PROCESSED_DIR,
) -> EventMatch:
    """The sessions `event` (name, location or country) names, in `year` when given. The event
    text may carry the year and session too ("Australian GP 2025 race"); given twice, they must
    agree. Raises with suggestions for an unknown name, and with the event's seasons for a
    season it has none in."""
    year, session = given_once(event, year, session)
    codes = session_codes(session)
    query = event_query(split_event_text(event)[0])
    if not query:
        if year is not None and year not in {s.year for s in list_sessions(root=root)}:
            raise no_season_error(year, list_sessions(root=root))  # a season alone ("2021")
        raise ToolInputError("Give an event name or location, e.g. 'melbourne' or 'monza'.")

    everything = list_sessions(root=root)
    tiers = {id(s): match_tier(query, s) for s in everything}
    found_tiers = [t for t in tiers.values() if t is not None]
    if not found_tiers:
        raise _no_event_error(event, query, everything)
    matched = [s for s in everything if tiers[id(s)] == min(found_tiers)]
    matched = _same_circuit(matched, everything)

    if year is not None:
        in_year = [s for s in matched if s.year == year]
        if not in_year:
            seasons = ", ".join(str(y) for y in sorted({s.year for s in matched}))
            names = "; ".join(matched_names(matched, everything))
            raise ToolInputError(
                f"{names}: no processed session in {year}. Available season(s): {seasons}."
            )
        matched = in_year
    return EventMatch(matched, year, session, codes)


def resolve_session(
    event: str,
    year: int | None = None,
    session: str | None = None,
    root: Path = PROCESSED_DIR,
) -> SessionInfo:
    """The one processed session meant by `event` (name, location, country or a session key
    such as "2025_16_Q"), `year` and `session`. The event text may carry the year and session
    too ("Australian GP 2025 race"). Without a year, the most recent season that has the event
    is used; without a session, qualifying."""
    keyed = by_key(event, year, session, root)
    if keyed is not None:
        return keyed
    found = match_event(event, year, session, root)
    wanted = [s for s in found.sessions if s.code in found.codes]
    latest = max(s.year for s in wanted or found.sessions)
    # Every weekend of that season the text fits, not only those with the session: "Aus GP" with
    # the sprint fits the Australian, Austrian and United States GPs of 2025, though only Austin
    # had a sprint.
    weekends = {s.round: s for s in found.sessions if s.year == latest}
    if len(weekends) > 1:
        raise several_events_error(event, list(weekends.values()))
    (round_,) = weekends
    chosen = [s for s in wanted if s.year == latest and s.round == round_]
    if not chosen:
        weekend = [s for s in found.sessions if s.year == latest and s.round == round_]
        raise missing_session_error(weekend, found.session or "Q")
    return chosen[0]
