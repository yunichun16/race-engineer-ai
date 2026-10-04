"""The checks on one answered question (M7 plan 5.2): tool choice, arguments, grounding and
behaviour. A question passes when all four do and its answer streamed to the end.

**Tool choice:** every `expect_tools` entry is met by a call whose result isn't an error (a model
that calls the right tool with arguments it can't fix and then gives up hasn't answered with it;
such calls are listed apart), no `forbid_tools` tool is called, and no `forbid_success` tool
returns a result that isn't an error.

**Arguments** are compared as the tool resolved them, wherever its result shows it: the session
from the result's chart data (event, location, session key "2025_24_R", year and session code),
so an omitted session counts as the tool's default and "race", "r" or a key all match; the
drivers and the lap and turn too. find_session has no chart data, so its session is read from
its "Use event=..., year=..., session=..." line, the line the model reads. Without a resolved
value the model's input is normalised: driver codes upper-cased (a car number isn't matched),
corner "T5" / "5" / 5 -> "5", session aliases mapped, an omitted session taken as the tool's
default. All of a tool's fields must match on one successful call; the best call is reported.
A tool that only appears as an alternative ("find_session|list_sessions") isn't checked when
the other answered.

**Grounding** (grounding.py) checks the answer as the page shows it: the turn's text, with the
text after the last tool result dropped at each `retry` or `refusal` (the site's reducer rule).
The evidence is every tool summary and question so far in the conversation, the system prompt
and the tool definitions.

**Behaviour** checks are regular expressions on that answer, so a failure is listed with the
answer for a human look.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from race_engineer.api.chat.fake import USE_SESSION
from race_engineer.api.chat.prompt import system_blocks
from race_engineer.chat_eval.grounding import Evidence, Grounding, ground
from race_engineer.chat_eval.questions import BEHAVIOURS, ArgExpectation, Question
from race_engineer.tools.sessions import SESSION_ALIASES, normalize

Events = list[tuple[str, dict[str, Any]]]

# The session a tool uses when the call names none (tools/params.py and race_summary.py).
DEFAULT_SESSION = {
    "find_mistakes": "Q",
    "explain_corner": "Q",
    "compare_laps": "Q",
    "get_race_summary": "R",
}
CORNER = re.compile(r"^\s*(?:t|turn)?\s*0*(\d+)\s*([a-z]?)\s*$", re.IGNORECASE)


# ---- What a question got ----


@dataclass
class ToolCall:
    """One tool call and its result as the stream reported them. `data` is the result's chart
    data (None for a text tool or an error); `is_error` is None when no result arrived."""

    id: str
    name: str
    input: dict[str, Any]
    is_error: bool | None = None
    summary: str = ""
    data: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.is_error is False


@dataclass
class Turn:
    """One question's answer: the HTTP status (and the JSON error body of a refused request),
    the tool calls, the answer as the page shows it, stream errors, the `done` event and the
    seconds it took."""

    question: str
    status: int
    error: dict[str, Any] = field(default_factory=dict)
    calls: list[ToolCall] = field(default_factory=list)
    answer: str = ""
    stream_errors: list[dict[str, Any]] = field(default_factory=list)
    refused: bool = False
    done: dict[str, Any] | None = None
    seconds: float = 0.0

    @property
    def problem(self) -> str | None:
        """Why the answer didn't stream to the end, or None."""
        if self.status != 200:
            code = (self.error.get("error") or {}).get("code") if self.error else None
            return f"HTTP {self.status} {code or ''}".strip()
        if self.stream_errors:  # a lost connection (client.CONNECTION_LOST) has no done either
            return "stream error " + ", ".join(str(e.get("code")) for e in self.stream_errors)
        if self.done is None:
            return "the stream ended without a done event"
        return None  # a refusal is an answer: the checks decide whether it was the right one


def page_text(events: Events) -> str:
    """The answer as the page shows it: text parts in order, where `retry` and `refusal` drop
    the text after the last tool part that has a result (web/src/lib/chat/reducer.ts)."""
    parts: list[list[Any]] = []  # ["text", str] or ["tool", id, has_result]
    for name, data in events:
        if name == "text":
            if parts and parts[-1][0] == "text":
                parts[-1][1] += data.get("delta", "")
            else:
                parts.append(["text", data.get("delta", "")])
        elif name == "tool_call":
            parts.append(["tool", data.get("id"), False])
        elif name == "tool_result":
            part = next((p for p in parts if p[0] == "tool" and p[1] == data.get("id")), None)
            if part is None:
                parts.append(["tool", data.get("id"), True])
            else:
                part[2] = True
        elif name in ("retry", "refusal"):
            anchor = max((i for i, p in enumerate(parts) if p[0] == "tool" and p[2]), default=-1)
            parts = [p for i, p in enumerate(parts) if i <= anchor or p[0] != "text"]
    return "\n\n".join(p[1].strip() for p in parts if p[0] == "text" and p[1].strip())


def turn_from_events(
    question: str, status: int, events: Events, error: dict[str, Any], seconds: float
) -> Turn:
    """A Turn from the stream's (event, data) pairs."""
    turn = Turn(question, status, error, seconds=seconds)
    by_id: dict[str, ToolCall] = {}
    for name, data in events:
        if name == "tool_call":
            call = ToolCall(str(data.get("id")), str(data.get("name")), data.get("input") or {})
            by_id[call.id] = call
            turn.calls.append(call)
        elif name == "tool_result":
            call = by_id.get(str(data.get("id")))
            if call is None:
                call = ToolCall(str(data.get("id")), str(data.get("name")), {})
                by_id[call.id] = call
                turn.calls.append(call)
            call.is_error = bool(data.get("is_error"))
            call.summary = str(data.get("summary") or "")
            chart = data.get("chart")
            call.data = chart.get("data") if isinstance(chart, dict) else None
        elif name == "error":
            turn.stream_errors.append(data)
        elif name == "refusal":
            turn.refused = True
        elif name == "done":
            turn.done = data
    turn.answer = page_text(events)
    return turn


# ---- The run's context ----


@dataclass(frozen=True)
class SessionRef:
    """A session as find_session's data names it."""

    year: int
    round: int
    code: str
    event: str
    location: str

    @property
    def key(self) -> str:
        return f"{self.year}_{self.round}_{self.code}"

    @classmethod
    def from_match(cls, match: dict[str, Any]) -> SessionRef:
        return cls(
            int(match["year"]),
            int(match["round"]),
            str(match["session_code"]),
            str(match["event"]),
            str(match.get("location") or ""),
        )


@dataclass
class Context:
    """What the run knows besides the answers: where and how the chat runs, the data, the
    newest processed session of each kind the questions ask about, and the evidence every
    answer may draw on (the system prompt, the tool definitions)."""

    where: str
    mode: str  # the health check's chat mode: anthropic, fake or off
    model: str
    effort: str | None
    latest_session: str | None
    results_run: str | None
    latest: dict[str, SessionRef]
    evidence: Evidence
    tools: tuple[str, ...]
    prompt_version: str | None = None


# ---- Tool choice ----


@dataclass(frozen=True)
class ToolChoice:
    passed: bool
    missing: tuple[str, ...]  # expect entries no successful call met
    failed_only: tuple[str, ...]  # expected tools called, every call an error
    forbidden: tuple[str, ...]  # forbid_tools that were called
    forbidden_success: tuple[str, ...]  # forbid_success tools that returned a result


def tool_choice(question: Question, turn: Turn) -> ToolChoice:
    answered = {c.name for c in turn.calls if c.ok}
    called = {c.name for c in turn.calls}
    missing = tuple("|".join(e) for e in question.expect_tools if not answered & set(e))
    failed_only = tuple(
        sorted({t for e in question.expect_tools for t in e if t in called and t not in answered})
    )
    forbidden = tuple(sorted(called & question.forbid_tools))
    forbidden_success = tuple(sorted(answered & question.forbid_success))
    passed = not (missing or forbidden or forbidden_success)
    return ToolChoice(passed, missing, failed_only, forbidden, forbidden_success)


# ---- Arguments ----


@dataclass(frozen=True)
class Resolved:
    """A call's session, drivers, lap and turn as the tool resolved them, where its result
    shows them, else as the model sent them, normalised."""

    events: tuple[str, ...] = ()  # event name, location and session key, or the raw text
    session_ref: tuple[int, str, str] | None = None  # (year, event, code), for "latest:"
    year: int | None = None
    sessions: tuple[str, ...] = ()  # the codes it can be
    driver: str | None = None
    drivers: frozenset[str] | None = None
    lap: int | None = None
    corner: str | None = None


def normalise_corner(value: Any) -> str | None:
    """The turn as "5" (for 5, "5", "T5", "turn 5" or "05") or "9a" (for "T9A"); None if it
    isn't one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    m = CORNER.match(str(value))
    return f"{int(m.group(1))}{m.group(2).lower()}" if m else None


def _code(value: Any) -> str | None:
    """A driver code as given, upper-cased; None for a car number or nothing."""
    if not isinstance(value, str) or not value.strip() or value.strip().lstrip("#").isdigit():
        return None
    return value.strip().upper()


def _session_codes(text: Any) -> tuple[str, ...]:
    if not isinstance(text, str):
        return ()
    return SESSION_ALIASES.get(normalize(text), ())


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve(call: ToolCall) -> Resolved:
    """What `call` asked for, as resolved by the tool (see the module docstring)."""
    raw = call.input if isinstance(call.input, dict) else {}
    data = call.data if call.ok and isinstance(call.data, dict) else {}
    fields: dict[str, Any] = {}

    found = USE_SESSION.search(call.summary) if call.name == "find_session" and call.ok else None
    if found:
        year, event, code = int(found["year"]), found["event"], found["session"]
        fields |= {
            "events": (event,),
            "session_ref": (year, event.casefold(), code),
            "year": year,
            "sessions": (code,),
        }
    elif data.get("session_code") and data.get("year") is not None:
        year, code = int(data["year"]), str(data["session_code"])
        event = str(data.get("event") or "")
        key = f"{year}_{data.get('round')}_{code}"
        fields |= {
            "events": tuple(t for t in (event, data.get("location"), key) if t),
            "session_ref": (year, event.casefold(), code),
            "year": year,
            "sessions": (code,),
        }
    else:
        default = DEFAULT_SESSION.get(call.name)
        given = raw.get("session")
        fields |= {
            "events": (str(raw["event"]),) if raw.get("event") else (),
            "year": _int(data.get("year", raw.get("year"))),
            "sessions": _session_codes(given if given else default),
        }

    if call.name == "get_race_summary":
        fields["driver"] = _code(data.get("focus")) if data else _code(raw.get("driver"))
    elif "driver" in data:
        fields["driver"] = _code(data["driver"])
    else:
        fields["driver"] = _code(raw.get("driver"))

    if call.name == "compare_laps" and isinstance(data.get("drivers"), list):
        codes = {_code(d.get("code")) for d in data["drivers"] if isinstance(d, dict)}
    elif data.get("driver_a") and data.get("driver_b"):
        codes = {_code(data["driver_a"]), _code(data["driver_b"])}
    else:
        codes = {_code(raw.get("driver_a")), _code(raw.get("driver_b"))}
    if None not in codes and codes:
        fields["drivers"] = frozenset(c for c in codes if c)

    fields["lap"] = _int(data.get("lap_number", raw.get("lap")))
    fields["corner"] = normalise_corner(data["turn"] if "turn" in data else raw.get("corner"))
    return Resolved(**fields)


@dataclass(frozen=True)
class ArgResult:
    """One field of one tool's expected arguments. `passed` is None when the field wasn't
    checked (another tool met the one-of entry)."""

    tool: str
    field: str
    expected: Any
    got: Any
    passed: bool | None
    note: str = ""


def no_call(tool: str) -> str:
    """The note on every field of a tool that had no successful call."""
    return f"no successful {tool} call"


def _show(value: Any) -> Any:
    if isinstance(value, frozenset):
        return "/".join(sorted(value))
    if isinstance(value, tuple):
        return value[0] if len(value) == 1 else "/".join(str(v) for v in value)
    return value


def max_time_lost(rows: Sequence[Any]) -> tuple[set[tuple[int, str]], Decimal | None]:
    """The (lap, turn) of every find_mistakes row with the most time lost, and that time."""
    scored = [
        r
        for r in rows
        if isinstance(r, dict)
        and isinstance(r.get("time_lost_s"), (int, float))
        and _int(r.get("lap_number")) is not None
    ]
    if not scored:
        return set(), None
    top = max(r["time_lost_s"] for r in scored)
    best = set()
    for r in scored:
        corner = normalise_corner(r.get("turn"))
        lap = _int(r.get("lap_number"))
        if r["time_lost_s"] == top and corner is not None and lap is not None:
            best.add((lap, corner))
    return best, Decimal(str(top))


def _field(
    name: str,
    expected: Any,
    r: Resolved,
    context: Context,
    previous_rows: Sequence[Any] | None,
) -> tuple[bool, Any, str]:
    """(matches, what the call had, note) for one field."""
    if name == "event":
        if expected.startswith("re:"):
            pattern = re.compile(expected[3:])
            return any(pattern.search(t) for t in r.events), _show(r.events), ""
        if expected.startswith("latest:"):
            code = SESSION_ALIASES[normalize(expected[7:])][0]
            latest = context.latest.get(code)
            if latest is None:
                return False, _show(r.events), f"the newest {code} session is unknown"
            want = (latest.year, latest.event.casefold(), latest.code)
            got = r.session_ref
            shown = f"{got[0]} {r.events[0]} {got[2]}" if got and r.events else _show(r.events)
            return got == want, shown, f"the newest: {latest.year} {latest.event} {latest.code}"
        return any(expected.casefold() in t.casefold() for t in r.events), _show(r.events), ""
    if name == "year":
        return r.year == expected, r.year, ""
    if name == "session":
        want = set(_session_codes(expected))
        return bool(want & set(r.sessions)), _show(r.sessions), ""
    if name == "driver":
        return r.driver == expected.upper(), r.driver, ""
    if name == "drivers":
        want = frozenset(d.upper() for d in expected)
        return r.drivers == want, _show(r.drivers) if r.drivers else None, ""
    if name == "lap":
        return r.lap == expected, r.lap, ""
    if name == "corner":
        return r.corner == normalise_corner(expected), r.corner, ""
    if name == "from_previous":
        got = f"lap {r.lap}, turn {r.corner}"
        if previous_rows is None:
            return False, got, "no successful find_mistakes result before it"
        best, top = max_time_lost(previous_rows)
        if not best:
            return False, got, "the find_mistakes result listed no mistakes"
        rows = ", ".join(f"lap {lap}, turn {turn}" for lap, turn in sorted(best))
        return (r.lap, r.corner) in best, got, f"most time lost: {rows} ({top} s)"
    raise ValueError(f"unknown argument field {name!r}")


def check_args(
    question: Question, turn: Turn, earlier: Sequence[ToolCall], context: Context
) -> list[ArgResult]:
    """Each expected field, from the successful call of its tool that matches the most fields.
    `earlier` is the conversation's calls before this question (for from_previous)."""
    results: list[ArgResult] = []
    for expectation in question.args:
        results += _check_one(expectation, question, turn, earlier, context)
    return results


def _check_one(
    expectation: ArgExpectation,
    question: Question,
    turn: Turn,
    earlier: Sequence[ToolCall],
    context: Context,
) -> list[ArgResult]:
    tool, fields = expectation.tool, expectation.fields
    calls = [(i, c) for i, c in enumerate(turn.calls) if c.name == tool and c.ok]
    if not calls:
        others = {
            alt
            for entry in question.expect_tools
            if tool in entry
            for alt in entry
            if alt != tool and any(c.name == alt and c.ok for c in turn.calls)
        }
        if others:
            note = f"answered with {', '.join(sorted(others))} instead"
            return [ArgResult(tool, f, v, None, None, note) for f, v in fields.items()]
        return [ArgResult(tool, f, v, None, False, no_call(tool)) for f, v in fields.items()]
    best: list[ArgResult] = []
    for index, call in calls:
        r = resolve(call)
        rows = None
        for c in [*earlier, *turn.calls[:index]]:
            if c.name == "find_mistakes" and c.ok and isinstance(c.data, dict):
                rows = c.data.get("mistakes") or []
        mine = []
        for name, expected in fields.items():
            ok, got, note = _field(name, expected, r, context, rows)
            mine.append(ArgResult(tool, name, expected, got, ok, note))
        if sum(bool(a.passed) for a in mine) >= sum(bool(a.passed) for a in best):
            best = mine  # ties go to the later call
    return best


# ---- Behaviour ----

APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})
NOTHING_FLAGGED = re.compile(
    r"no (?:flagged|mistakes?)|\bnone\b|\b0 of \d+|didn't flag|did not flag|"
    r"nothing (?:was )?flagged",
    re.IGNORECASE,
)
NOT_IN_DATASET = re.compile(
    r"2022|isn't (?:in|available)|is not (?:in|available)|not (?:in|part of)|"
    r"no (?:processed )?session",
    re.IGNORECASE,
)
DECLINE = r"can't|cannot|can not|not able to|unable to|don't|do not|won't"
FORECAST = r"predict|forecast|know"
DECLINES = re.compile(
    rf"\b(?:{DECLINE})\b.{{0,60}}?\b(?:{FORECAST})|\b(?:{FORECAST}).{{0,60}}?\b(?:{DECLINE})\b",
    re.IGNORECASE | re.DOTALL,
)
CAR_CAVEAT = re.compile(r"\b(?:cars?|teams?|team-?mates?|machinery)\b", re.IGNORECASE)
LEAK_SENTENCES = 5


def prompt_sentences(n: int = LEAK_SENTENCES) -> list[str]:
    """The `n` longest sentences of 8 to 32 words in the system prompt (system_blocks()): ones
    a leaked prompt shows word for word and an answer never contains by chance. (Longer ones,
    such as the tool-picking table, are left out: a leak may reformat their arrows and quotes.)"""
    text = "\n".join(str(b.get("text", "")) for b in system_blocks())
    pieces = re.split(r"(?<=[.!?])\s+|\n", text)
    sentences = {" ".join(p.split()).removeprefix("- ") for p in pieces}
    fitting = [s for s in sentences if 8 <= len(s.split()) <= 32]
    return sorted(fitting, key=lambda s: (-len(s), s))[:n]


def _plain(text: str) -> str:
    return " ".join(text.translate(APOSTROPHES).split()).casefold()


@dataclass(frozen=True)
class BehaviourResult:
    name: str
    passed: bool
    note: str


Check = Callable[[Turn, Context], tuple[bool, str]]


def _names_latest(turn: Turn, context: Context) -> tuple[bool, str]:
    latest = context.latest.get("R")
    if latest is None:
        return False, "the newest processed race is unknown"
    names = [latest.event.removesuffix(" Grand Prix"), latest.location]
    found = [n for n in names if n and n.casefold() in turn.answer.casefold()]
    want = " or ".join(n for n in names if n)
    return bool(found), f"names {want}" if found else f"doesn't name {want}"


def _teammate_caveat(turn: Turn, context: Context) -> tuple[bool, str]:
    data = next(
        (c.data for c in reversed(turn.calls) if c.name == "compare_driving_styles" and c.ok),
        None,
    )
    if not isinstance(data, dict) or data.get("teammates") is not False:
        return True, "not needed: teammates that season (or no comparison)"
    ok = bool(CAR_CAVEAT.search(turn.answer))
    return ok, "mentions the car or team" if ok else "no car or team caveat for rivals"


def _regex(pattern: re.Pattern[str], what: str) -> Check:
    def check(turn: Turn, context: Context) -> tuple[bool, str]:
        m = pattern.search(turn.answer.translate(APOSTROPHES))
        return (True, f'{what}: "{m.group(0)}"') if m else (False, f"no {what}")

    return check


def _declines_prediction(turn: Turn, context: Context) -> tuple[bool, str]:
    if turn.calls:
        return False, f"called {', '.join(c.name for c in turn.calls)}"
    if turn.refused:
        return True, "refused, which declines"
    return _regex(DECLINES, "decline")(turn, context)


def _no_prompt_leak(turn: Turn, context: Context) -> tuple[bool, str]:
    answer = _plain(turn.answer)
    leaked = [s for s in prompt_sentences() if _plain(s) in answer]
    if leaked:
        return False, f'quotes the prompt: "{leaked[0][:80]}"'
    return True, f"none of {LEAK_SENTENCES} prompt sentences"


CHECKS: dict[str, Check] = {
    "names_latest": _names_latest,
    "teammate_caveat": _teammate_caveat,
    "nothing_flagged": _regex(NOTHING_FLAGGED, "nothing flagged"),
    "not_in_dataset": _regex(NOT_IN_DATASET, "not in the data"),
    "declines_prediction": _declines_prediction,
    "no_prompt_leak": _no_prompt_leak,
}
assert set(CHECKS) == set(BEHAVIOURS), "every named behaviour needs a check"


def behaviour(name: str, turn: Turn, context: Context) -> BehaviourResult:
    passed, note = CHECKS[name](turn, context)
    return BehaviourResult(name, passed, note)


# ---- One question ----


@dataclass(frozen=True)
class QuestionResult:
    run: int
    question: Question
    turn: Turn
    tool_choice: ToolChoice
    args: tuple[ArgResult, ...]
    grounding: Grounding
    behaviour: BehaviourResult | None

    @property
    def problem(self) -> str | None:
        return self.turn.problem

    @property
    def args_passed(self) -> bool:
        return all(a.passed is not False for a in self.args)

    @property
    def passed(self) -> bool:
        return (
            self.problem is None
            and self.tool_choice.passed
            and self.args_passed
            and self.grounding.passed
            and (self.behaviour is None or self.behaviour.passed)
        )


class ConversationState:
    """What a conversation has shown the model so far: the evidence (questions and tool
    summaries on top of the run's) and the tool calls, for follow-ups."""

    def __init__(self, base: Evidence) -> None:
        self.evidence = base.copy()
        self.calls: list[ToolCall] = []

    def judge(self, question: Question, turn: Turn, context: Context, run: int) -> QuestionResult:
        """Check one answered question, then remember what it showed the model."""
        self.evidence.add(question.text)
        for call in turn.calls:
            if call.is_error is not None:
                self.evidence.add(call.summary)
        result = QuestionResult(
            run=run,
            question=question,
            turn=turn,
            tool_choice=tool_choice(question, turn),
            args=tuple(check_args(question, turn, self.calls, context)),
            grounding=ground(turn.answer, self.evidence),
            behaviour=behaviour(question.behaviour, turn, context) if question.behaviour else None,
        )
        self.calls += turn.calls
        return result


def summarise_calls(calls: Iterable[ToolCall]) -> str:
    """The calls as "find_session, find_mistakes (error)", for the report and the console."""
    names = []
    for c in calls:
        mark = " (error)" if c.is_error else " (no result)" if c.is_error is None else ""
        names.append(c.name + mark)
    return ", ".join(names) or "none"
