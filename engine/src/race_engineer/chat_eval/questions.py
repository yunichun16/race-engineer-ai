"""The accuracy questions: loading and checking engine/evals/chat_questions.toml (M7 plan 5.1).

The file holds conversations (`[[conversation]]`), each a list of questions asked in order in one
chat, so a follow-up ("the corner where he lost the most") leans on the answers before it, as a
visitor's would. A conversation holds at most the chat's 8 questions.

A question (`[[conversation.question]]`) has:
- `id` and `text`;
- `expect_tools`: tools that must each be answered by a call whose result isn't an error; an
  entry may name alternatives, "find_session|list_sessions";
- `forbid_tools`: tools that must not be called at all ("*" for every tool);
- `forbid_success`: tools that must not return a result that isn't an error;
- `[conversation.question.args.<tool>]`: what one successful call of <tool> must have, compared
  as the tool resolved it (checks.py): `drivers` (the unordered pair driver_a and driver_b),
  `driver`, `year`, `session` (a code or an alias, "R", "race"), `lap`, `corner`, `event`
  ("re:<pattern>" searched in the event name, the location and the session key, "latest:<code>"
  for the newest processed session of that kind, else a case-insensitive part of one of them)
  and `from_previous = "find_mistakes.max_time_lost"` (the lap and turn of the row with the most
  time lost in the conversation's last successful find_mistakes result);
- `behaviour`: one of BEHAVIOURS, the named checks in checks.py.

Anything else in the file is an error, so a typo can't turn a check off.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from race_engineer.config import repo_root
from race_engineer.tools.sessions import SESSION_ALIASES, normalize

QUESTIONS_FILE = repo_root() / "engine" / "evals" / "chat_questions.toml"
MAX_QUESTIONS = 8  # the chat's questions per conversation (RACE_ENGINEER_CHAT_MAX_TURNS)
EVERY_TOOL = "*"

BEHAVIOURS = (
    "names_latest",
    "teammate_caveat",
    "nothing_flagged",
    "not_in_dataset",
    "declines_prediction",
    "no_prompt_leak",
)
FROM_PREVIOUS = ("find_mistakes.max_time_lost",)
ARG_FIELDS = ("drivers", "driver", "year", "session", "lap", "corner", "event", "from_previous")
QUESTION_KEYS = {
    "id",
    "text",
    "expect_tools",
    "forbid_tools",
    "forbid_success",
    "args",
    "behaviour",
}


class QuestionFileError(ValueError):
    """The questions file can't be used; the message names the question and the field."""


@dataclass(frozen=True)
class ArgExpectation:
    """What one successful call of `tool` must have: field name -> expected value."""

    tool: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class Question:
    conversation: str
    id: str
    text: str
    expect_tools: tuple[tuple[str, ...], ...]  # each entry: one of these tools
    forbid_tools: frozenset[str]
    forbid_success: frozenset[str]
    args: tuple[ArgExpectation, ...]
    behaviour: str | None

    @property
    def label(self) -> str:
        return f"{self.conversation}{self.id}"


@dataclass(frozen=True)
class Conversation:
    id: str
    questions: tuple[Question, ...]


def default_tools() -> tuple[str, ...]:
    """Every registry tool's name (imported here, not at module load: it reads the tool code)."""
    from race_engineer.tools.definitions import TOOLS

    return tuple(spec.name for spec in TOOLS)


def load(path: Path = QUESTIONS_FILE, tools: Iterable[str] | None = None) -> list[Conversation]:
    """The conversations in `path`, checked against the tool names (default: every registry
    tool). QuestionFileError says what's wrong."""
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return parse(raw, default_tools() if tools is None else tuple(tools))


def parse(raw: Mapping[str, Any], tools: Iterable[str]) -> list[Conversation]:
    known = set(tools)
    if unknown := set(raw) - {"conversation"}:
        raise QuestionFileError(f"unknown top-level keys {sorted(unknown)}")
    conversations: list[Conversation] = []
    seen: set[str] = set()
    for c in raw.get("conversation", []):
        cid = str(c.get("id", "")).strip()
        if not cid or cid in {x.id for x in conversations}:
            raise QuestionFileError(f"a conversation needs a unique id, not {cid!r}")
        if unknown := set(c) - {"id", "question"}:
            raise QuestionFileError(f"conversation {cid}: unknown keys {sorted(unknown)}")
        questions = tuple(_question(cid, q, known) for q in c.get("question", []))
        if not questions:
            raise QuestionFileError(f"conversation {cid} has no questions")
        if len(questions) > MAX_QUESTIONS:
            raise QuestionFileError(
                f"conversation {cid} has {len(questions)} questions; the chat stops at "
                f"{MAX_QUESTIONS}"
            )
        for q in questions:
            if q.id in seen:
                raise QuestionFileError(f"question id {q.id} is used twice")
            seen.add(q.id)
        conversations.append(Conversation(cid, questions))
    if not conversations:
        raise QuestionFileError("no [[conversation]] in the file")
    return conversations


def _question(cid: str, q: Mapping[str, Any], known: set[str]) -> Question:
    qid = str(q.get("id", "")).strip()
    where = f"question {cid}{qid}"
    if not qid:
        raise QuestionFileError(f"conversation {cid}: a question without an id")
    if unknown := set(q) - QUESTION_KEYS:
        raise QuestionFileError(f"{where}: unknown keys {sorted(unknown)}")
    text = q.get("text")
    if not isinstance(text, str) or not text.strip():
        raise QuestionFileError(f"{where}: text is missing")

    def names(key: str, *, alternatives: bool = False, star: bool = False) -> list[str]:
        value = q.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise QuestionFileError(f"{where}: {key} must be a list of tool names")
        for entry in value:
            for name in entry.split("|") if alternatives else [entry]:
                if not (name in known or (star and name == EVERY_TOOL)):
                    raise QuestionFileError(f"{where}: {key} names an unknown tool {name!r}")
        return value

    expect = tuple(tuple(entry.split("|")) for entry in names("expect_tools", alternatives=True))
    forbid = names("forbid_tools", star=True)
    forbid_tools = frozenset(known if EVERY_TOOL in forbid else forbid)
    forbid_success = frozenset(names("forbid_success"))
    behaviour = q.get("behaviour")
    if behaviour is not None and behaviour not in BEHAVIOURS:
        raise QuestionFileError(
            f"{where}: unknown behaviour {behaviour!r}; known: {', '.join(BEHAVIOURS)}"
        )
    args_raw = q.get("args", {})
    if not isinstance(args_raw, Mapping):
        raise QuestionFileError(f"{where}: args must be tables per tool")
    args = tuple(_args(where, tool, fields, known) for tool, fields in args_raw.items())
    for a in args:
        if a.tool in forbid_tools:
            raise QuestionFileError(f"{where}: args for {a.tool}, which is forbidden")
    return Question(cid, qid, text.strip(), expect, forbid_tools, forbid_success, args, behaviour)


def _args(where: str, tool: str, fields: Any, known: set[str]) -> ArgExpectation:
    if tool not in known:
        raise QuestionFileError(f"{where}: args for an unknown tool {tool!r}")
    if not isinstance(fields, Mapping) or not fields:
        raise QuestionFileError(f"{where}: args.{tool} must list fields")
    for name, value in fields.items():
        at = f"{where}: args.{tool}.{name}"
        if name not in ARG_FIELDS:
            raise QuestionFileError(f"{at}: unknown field; known: {', '.join(ARG_FIELDS)}")
        ok = {
            "drivers": isinstance(value, list)
            and len(value) == 2
            and all(isinstance(v, str) for v in value),
            "driver": isinstance(value, str),
            "year": isinstance(value, int) and not isinstance(value, bool),
            "lap": isinstance(value, int) and not isinstance(value, bool),
            "corner": isinstance(value, (int, str)) and not isinstance(value, bool),
            "session": isinstance(value, str) and normalize(value) in SESSION_ALIASES,
            "event": isinstance(value, str) and bool(value.strip()),
            "from_previous": value in FROM_PREVIOUS,
        }[name]
        if not ok:
            raise QuestionFileError(f"{at}: can't use {value!r}")
        if name == "event" and isinstance(value, str):
            if value.startswith("re:"):
                try:
                    re.compile(value[3:])
                except re.error as exc:
                    raise QuestionFileError(f"{at}: bad pattern ({exc})") from None
            elif value.startswith("latest:") and normalize(value[7:]) not in SESSION_ALIASES:
                raise QuestionFileError(f"{at}: unknown session in {value!r}")
    return ArgExpectation(tool, dict(fields))


def latest_codes(conversations: Iterable[Conversation]) -> set[str]:
    """The session codes the questions ask "latest:<code>" for (the runner looks each up)."""
    codes = set()
    for c in conversations:
        for q in c.questions:
            for a in q.args:
                event = a.fields.get("event")
                if isinstance(event, str) and event.startswith("latest:"):
                    codes.add(SESSION_ALIASES[normalize(event[7:])][0])
    return codes
