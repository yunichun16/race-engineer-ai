"""A scripted stand-in for Claude, so the chat runs and is tested without an API key or network.

`scripted_client()` builds a real `AsyncAnthropic` client whose HTTP transport is an
`httpx2.MockTransport` calling a `ScriptedClaude`. ScriptedClaude answers `POST /v1/messages`
with real Anthropic server-sent-event byte streams (`message_start`, `content_block_start`,
`content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`), so the SDK's own
stream parser and tool runner do all the work they do against the API.

How it answers:
- A new question gets a short progress note (a `thinking` block, as `display: "updates"`
  returns them), a line of text and a tool call picked by a rule table on the question's
  words: "mistakes" -> find_mistakes, "turn 4 on lap 12" or a follow-up about "that corner" ->
  explain_corner, "style" -> compare_driving_styles, "gain on" -> compare_laps, "won" or
  "safety car" -> get_race_summary, "which races" -> list_sessions, anything else ->
  find_session. A rule only picks a tool the request offers.
- Arguments come from the question: the first event or location from the session catalog,
  three-letter codes or drivers' surnames, a year, "quali", "race" or "sprint", "lap N" and
  "turn N". A follow-up without an event reuses the previous call's session.
- When a tool needs a session the question doesn't name, it calls find_session first, then
  the tool with the session on the match's "Use event=..., year=..., session=..." line (none
  when find_session found no single session). find_session gets fields as the model fills them:
  "round 5" as round, "before last" or "2 races ago" as recent=1, "last year" as the season
  before the newest processed one (it knows no date), the event the question names, else
  recent=0 (the latest session); counting back means races unless the question names another
  kind.
- After tool results it writes a final answer from the first line of each result, saying that
  it is scripted.
- Usage reports cache reads and writes as the API would for the request's cache breakpoints, so
  the caching checks work offline.

Tests (or a developer, with a tag in the question) can ask for the paths that are hard to reach:
`queue("refusal")` or "#refuse", "max_tokens" / "#truncate" (a cut-off tool call),
"broken_input" / "#broken" (tool input JSON the SDK can't parse), "rate_limit" / "#busy" (429),
"overloaded" / "#overloaded" (529) and "fallback" / "#fallback" (a mid-answer server-side
fallback).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx2
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from race_engineer.api.chat.session import is_question
from race_engineer.tools.registry import DataPaths
from race_engineer.tools.sessions import SessionInfo, list_sessions

SCRIPTED_MODEL = "scripted"
SCRIPTED_BASE_URL = "http://scripted.invalid"
SCRIPTED_NOTE = "(Scripted answer: no model was called.)"
SIGNATURE = "scripted-signature"

DIRECTIVES = {
    "refusal": "#refuse",
    "max_tokens": "#truncate",
    "broken_input": "#broken",
    "rate_limit": "#busy",
    "overloaded": "#overloaded",
    "fallback": "#fallback",
}

# Surnames of the 2022-2026 drivers, for questions that name drivers rather than codes.
DRIVER_CODES = {
    "albon": "ALB",
    "alonso": "ALO",
    "antonelli": "ANT",
    "bearman": "BEA",
    "bortoleto": "BOR",
    "bottas": "BOT",
    "colapinto": "COL",
    "de vries": "DEV",
    "doohan": "DOO",
    "gasly": "GAS",
    "hadjar": "HAD",
    "hamilton": "HAM",
    "hulkenberg": "HUL",
    "hülkenberg": "HUL",
    "latifi": "LAT",
    "lawson": "LAW",
    "leclerc": "LEC",
    "lindblad": "LIN",
    "magnussen": "MAG",
    "norris": "NOR",
    "ocon": "OCO",
    "perez": "PER",
    "pérez": "PER",
    "piastri": "PIA",
    "ricciardo": "RIC",
    "russell": "RUS",
    "sainz": "SAI",
    "sargeant": "SAR",
    "schumacher": "MSC",
    "stroll": "STR",
    "tsunoda": "TSU",
    "verstappen": "VER",
    "vettel": "VET",
    "zhou": "ZHO",
}
# Three-letter words that are never driver codes.
NOT_CODES = {
    "THE",
    "AND",
    "WHO",
    "WHY",
    "HOW",
    "WAS",
    "DID",
    "LAP",
    "GAP",
    "FIA",
    "DRS",
    "VSC",
    "DNF",
}

# Questions about the future or the standings get no tool.
NO_TOOL = re.compile(
    r"will win|championship|standings|next season|predict|weather|regulation", re.IGNORECASE
)


def _rule(*alternatives: str) -> re.Pattern[str]:
    return re.compile("|".join(alternatives), re.IGNORECASE)


TURN = r"\b(?:turn|corner|t)\s*\d+[a-z]?\b"
# (pattern, tool): the first rule that matches and whose tool the request offers wins.
RULES: list[tuple[re.Pattern[str], str]] = [
    (_rule(rf"{TURN}.*\blap\s*\d+", rf"\blap\s*\d+.*{TURN}"), "explain_corner"),
    (_rule(r"\b(?:that|the|this) (?:corner|turn)\b", r"\bcorner where\b"), "explain_corner"),
    (
        _rule("mistake", "went wrong", "lose time", "lost time", "lost the most", r"errors?\b"),
        "find_mistakes",
    ),
    (
        _rule("style", r"brakes? later", r"brakes? earlier", "driving differ"),
        "compare_driving_styles",
    ),
    (_rule(r"\bgain", "faster than", "quicker than", r"\bbeat\b", r"\bcompare"), "compare_laps"),
    (
        _rule(
            r"\bwon\b",
            r"\bwin\b",
            "winner",
            "summar",
            "safety car",
            r"\bvsc\b",
            r"pit ?stop",
            r"\bpits?\b",
            "retire",
            r"positions?",
        ),
        "get_race_summary",
    ),
    (
        _rule(r"which (?:races|sessions|events)", r"list (?:the |all )?(?:races|sessions)"),
        "list_sessions",
    ),
]
# Tools that need a session; without one in the question they wait for find_session.
NEEDS_EVENT = {"find_mistakes", "explain_corner", "compare_laps", "get_race_summary"}
ROW = re.compile(r"(?:\b(?P<driver>[A-Z]{3}) \[[^\]]*\] )?Lap (?P<lap>\d+), turn (?P<turn>\w+)")
# How a question names a session without its event, for find_session's fields.
ROUND = re.compile(r"\bround\s*(\d{1,2})\b", re.IGNORECASE)
BACK = re.compile(r"\bbefore last\b|\b(\d{1,2})\s+(?:race|session|weekend)s?\s+ago\b", re.I)
LAST_YEAR = re.compile(r"\b(?:last|previous) (?:year|season)\b", re.IGNORECASE)
LATEST = re.compile(r"\b(?:last|latest|most recent)\b", re.IGNORECASE)
# The line of a find_session match naming the session for the other tools.
USE_SESSION = re.compile(
    r"^Use event=(?P<q>['\"])(?P<event>.+?)(?P=q), year=(?P<year>\d{4}), "
    r"session='(?P<session>[A-Z]+)'",
    re.MULTILINE,
)


@dataclass
class ScriptedRequest:
    """One request ScriptedClaude received: the JSON body and the `anthropic-beta` header."""

    body: dict[str, Any]
    betas: list[str]
    headers: dict[str, str] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)  # what the reply reported


# Server-sent-event streams in the Messages API's wire format.


def sse_event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str, size: int = 40) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def sse_stream(
    blocks: Sequence[dict[str, Any]],
    stop_reason: str,
    usage: dict[str, Any],
    *,
    model: str = SCRIPTED_MODEL,
    message_id: str = "msg_scripted",
    stop_details: dict[str, Any] | None = None,
) -> bytes:
    """The SSE bytes of one streamed message. Blocks are `{"type": "thinking", "thinking"}`,
    `{"type": "text", "text"}`, `{"type": "tool_use", "id", "name", "input"}` (with an optional
    `"raw"` string sent in place of the input's JSON) or `{"type": "fallback", ...}`."""
    start_usage = {k: v for k, v in usage.items() if k != "iterations"} | {"output_tokens": 1}
    events = [
        sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": start_usage,
                },
            },
        )
    ]

    def delta(index: int, payload: dict[str, Any]) -> str:
        return sse_event(
            "content_block_delta", {"type": "content_block_delta", "index": index, "delta": payload}
        )

    for i, block in enumerate(blocks):
        kind = block["type"]
        if kind == "thinking":
            start: dict[str, Any] = {"type": "thinking", "thinking": "", "signature": ""}
        elif kind == "text":
            start = {"type": "text", "text": ""}
        elif kind == "tool_use":
            start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
        else:
            start = dict(block)
        events.append(
            sse_event(
                "content_block_start",
                {"type": "content_block_start", "index": i, "content_block": start},
            )
        )
        if kind == "thinking":
            if block["thinking"]:
                events.append(delta(i, {"type": "thinking_delta", "thinking": block["thinking"]}))
            events.append(delta(i, {"type": "signature_delta", "signature": SIGNATURE}))
        elif kind == "text":
            events += [delta(i, {"type": "text_delta", "text": c}) for c in _chunks(block["text"])]
        elif kind == "tool_use":
            raw = block.get("raw")
            raw = json.dumps(block["input"], ensure_ascii=False) if raw is None else raw
            half = len(raw) // 2
            for piece in (raw[:half], raw[half:]):
                if piece:
                    events.append(delta(i, {"type": "input_json_delta", "partial_json": piece}))
        events.append(sse_event("content_block_stop", {"type": "content_block_stop", "index": i}))
    end_usage = {"output_tokens": usage.get("output_tokens", 1)}
    if usage.get("iterations"):
        end_usage["iterations"] = usage["iterations"]
    events.append(
        sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                    "stop_details": stop_details,
                },
                "usage": end_usage,
            },
        )
    )
    events.append(sse_event("message_stop", {"type": "message_stop"}))
    return "".join(events).encode()


def error_response(status: int, kind: str, message: str) -> httpx2.Response:
    """An API error response as the Messages API sends it."""
    body = {"type": "error", "error": {"type": kind, "message": message}}
    return httpx2.Response(status, json=body, headers={"request-id": "req_scripted"})


# Reading a question.


@dataclass
class Asked:
    """What a question (or a tool result) names."""

    event: str | None = None
    year: int | None = None
    session: str | None = None
    drivers: list[str] = field(default_factory=list)
    lap: int | None = None
    turn: str | None = None


def _session_code(text: str) -> str | None:
    if re.search(r"sprint (?:qualifying|quali|shootout)", text, re.I):
        return "SQ"
    if re.search(r"\bsprint\b", text, re.I):
        return "S"
    if re.search(r"\bquali", text, re.I):
        return "Q"
    if re.search(r"\braces?\b", text, re.I):
        return "R"
    return None


class Reader:
    """Finds events, drivers, years and sessions in text, using the session catalog for the
    event names (loaded on first use, never at import)."""

    def __init__(self, catalog: Callable[[], Sequence[SessionInfo]]) -> None:
        self._catalog = catalog
        self._phrases: list[str] | None = None

    def phrases(self) -> list[str]:
        """Event names, the same without "Grand Prix", and locations, longest first."""
        if self._phrases is None:
            try:
                sessions = list(self._catalog())
            except Exception:  # no data: questions just name no event
                sessions = []
            found: set[str] = set()
            for s in sessions:
                event = s.event.lower()
                found |= {event, event.removesuffix(" grand prix").strip(), s.location.lower()}
            self._phrases = sorted((p for p in found if len(p) > 2), key=len, reverse=True)
        return self._phrases

    def newest_season(self) -> int | None:
        """The newest processed season, which the scripted model counts "last year" from."""
        try:
            return max((s.year for s in self._catalog()), default=None)
        except Exception:  # no data
            return None

    def event_in(self, text: str) -> str | None:
        lower = text.lower()
        for phrase in self.phrases():
            if re.search(rf"\b{re.escape(phrase)}\b", lower):
                return phrase
        return None

    def read(self, text: str) -> Asked:
        drivers: list[tuple[int, str]] = []
        for m in re.finditer(r"\b[A-Z]{3}\b", text):
            if m.group() not in NOT_CODES:
                drivers.append((m.start(), m.group()))
        lower = text.lower()
        for name, code in DRIVER_CODES.items():
            for m in re.finditer(rf"\b{re.escape(name)}\b", lower):
                drivers.append((m.start(), code))
        ordered = list(dict.fromkeys(code for _, code in sorted(drivers)))
        year = re.search(r"\b(20[12]\d)\b", text)
        lap = re.search(r"\blap\s*(\d+)", text, re.I)
        turn = re.search(r"\b(?:turn|corner|t)\s*(\d+[a-z]?)\b", text, re.I)
        return Asked(
            event=self.event_in(text),
            year=int(year.group(1)) if year else None,
            session=_session_code(text),
            drivers=ordered,
            lap=int(lap.group(1)) if lap else None,
            turn=turn.group(1) if turn else None,
        )


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, dict) and block.get("type") == "tool_result":
            parts.append(_text_of(block.get("content")))
    return "\n".join(parts)


def _first_line(text: str, limit: int = 240) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _drop_none(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if v is not None}


@dataclass
class Turn:
    """One scripted reply: a progress note and text, then tool calls or nothing."""

    text: str
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    update: str = ""


class ScriptedClaude:
    """The MockTransport handler that plays Claude. See the module docstring."""

    def __init__(self, catalog: Callable[[], Sequence[SessionInfo]] | None = None) -> None:
        self.requests: list[ScriptedRequest] = []
        self.directives: deque[str] = deque()
        self.reader = Reader(catalog or (lambda: []))
        self._cached: set[str] = set()
        self._count = 0
        self._prefix = secrets.token_hex(3)
        self._broken: set[str] = set()  # questions already answered with broken input

    def queue(self, *directives: str) -> None:
        """Make the next requests take these paths, one each (keys of DIRECTIVES)."""
        unknown = set(directives) - DIRECTIVES.keys()
        if unknown:
            raise ValueError(f"unknown directives {sorted(unknown)}; known: {sorted(DIRECTIVES)}")
        self.directives.extend(directives)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or not request.url.path.endswith("/v1/messages"):
            return error_response(404, "not_found_error", f"No route {request.url.path}.")
        body = json.loads(request.content)
        betas = [b for b in request.headers.get("anthropic-beta", "").split(",") if b]
        self.requests.append(ScriptedRequest(body, betas, dict(request.headers)))
        self._count += 1
        directive = self.directives.popleft() if self.directives else self._tagged(body)
        if directive == "rate_limit":
            return error_response(429, "rate_limit_error", "Scripted rate limit.")
        if directive == "overloaded":
            return error_response(529, "overloaded_error", "Scripted overload.")
        blocks, stop_reason, stop_details = self._reply(body, directive)
        usage = self._usage(body, blocks)
        if directive == "fallback":
            usage["iterations"] = [
                {**self._iteration(usage, 0.5), "type": "message", "model": "claude-sonnet-5-5"},
                {
                    **self._iteration(usage, 0.5),
                    "type": "fallback_message",
                    "model": "claude-sonnet-5",
                },
            ]
        self.requests[-1].usage = usage
        stream = sse_stream(
            blocks,
            stop_reason,
            usage,
            message_id=f"msg_{self._prefix}{self._count}",
            stop_details=stop_details,
        )
        headers = {"content-type": "text/event-stream", "request-id": f"req_{self._prefix}"}
        return httpx2.Response(200, headers=headers, content=stream)

    @staticmethod
    def _iteration(usage: dict[str, Any], share: float) -> dict[str, Any]:
        """A share of the usage, as one attempt's entry in `usage.iterations`."""
        keys = (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
        return {k: int(usage.get(k, 0) * share) for k in keys}

    def _tagged(self, body: dict[str, Any]) -> str | None:
        """A directive tagged in the question ("#refuse"), for the reply to that question only;
        a broken tool input is sent once, so the runner's retry gets a normal answer."""
        last = body["messages"][-1]
        if not is_question(last):
            return None
        text = _text_of(last.get("content"))
        for directive, tag in DIRECTIVES.items():
            if tag in text:
                if directive == "broken_input":
                    key = hashlib.sha256(json.dumps(body["messages"]).encode()).hexdigest()
                    if key in self._broken:
                        return None
                    self._broken.add(key)
                return directive
        return None

    # Usage, as the API reports it for the request's cache breakpoints.

    def _usage(self, body: dict[str, Any], blocks: Sequence[dict[str, Any]]) -> dict[str, Any]:
        def tokens(value: Any) -> int:
            return max(1, len(json.dumps(value, ensure_ascii=False)) // 4)

        prefix = [body.get("tools"), body.get("system")]
        prefix_key = hashlib.sha256(json.dumps(prefix, sort_keys=True).encode()).hexdigest()
        system = body.get("system")
        marked = isinstance(system, list) and any(
            isinstance(b, dict) and b.get("cache_control") for b in system
        )
        read = write = uncached = 0
        if marked:
            if prefix_key in self._cached:
                read += tokens(prefix)
            else:
                write += tokens(prefix)
                self._cached.add(prefix_key)
        else:
            uncached += tokens(prefix)
        messages = body["messages"]
        if body.get("cache_control"):
            # Automatic caching: read the longest conversation prefix written before, write
            # the rest, and remember the whole conversation for the next request.
            keys, sizes, chain = [prefix_key], [0], prefix_key
            for message in messages:
                chain = hashlib.sha256((chain + json.dumps(message, sort_keys=True)).encode())
                chain = chain.hexdigest()
                keys.append(chain)
                sizes.append(sizes[-1] + tokens(message))
            hit = max((k for k in range(len(keys)) if keys[k] in self._cached), default=0)
            read += sizes[hit]
            write += sizes[-1] - sizes[hit]
            self._cached.add(keys[-1])
        else:
            uncached += sum(tokens(m) for m in messages)
        return {
            "input_tokens": max(uncached, 1),
            "output_tokens": sum(tokens(b) for b in blocks) if blocks else 1,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write,
        }

    # The reply.

    def _reply(
        self, body: dict[str, Any], directive: str | None
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any] | None]:
        if directive == "refusal":
            details = {"type": "refusal", "category": "cyber", "explanation": "Scripted refusal."}
            return [], "refusal", details
        messages = body["messages"]
        offered = [str(t.get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        turn = self._next_turn(messages, offered)
        updates = (body.get("thinking") or {}).get("display") == "updates"
        # Like the model at medium effort, think briefly before every reply; only the notes
        # before tool calls have text (and only with display "updates").
        blocks: list[dict[str, Any]] = [
            {"type": "thinking", "thinking": turn.update if updates else ""},
            {"type": "text", "text": turn.text},
        ]
        for i, (name, args) in enumerate(turn.calls):
            blocks.append({"type": "tool_use", "id": self._tool_id(i), "name": name, "input": args})
        stop_reason = "tool_use" if turn.calls else "end_turn"
        # The tool a broken reply calls: the one the turn would have called, or any offered.
        name = turn.calls[0][0] if turn.calls else (offered[0] if offered else "tool")
        if directive == "max_tokens":
            cut = {"type": "tool_use", "id": self._tool_id(9), "name": name, "raw": '{"event": "mo'}
            return [b for b in blocks if b["type"] != "tool_use"] + [cut], "max_tokens", None
        if directive == "broken_input":
            bad = {"type": "tool_use", "id": self._tool_id(8), "name": name, "raw": '{"event": mo}'}
            return [blocks[0], bad], "tool_use", None
        if directive == "fallback":
            declined = [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": "Looking into it. "},
                {"type": "tool_use", "id": self._tool_id(7), "name": name, "raw": '{"eve'},
                {
                    "type": "fallback",
                    "from": {"model": body.get("model", "claude-sonnet-5-5")},
                    "to": {"model": "claude-sonnet-5"},
                    "trigger": {"type": "refusal", "category": "cyber"},
                },
            ]
            return [*declined, *blocks], stop_reason, None
        return blocks, stop_reason, None

    def _tool_id(self, i: int) -> str:
        return f"toolu_{self._prefix}{self._count}_{i}"

    def _next_turn(self, messages: list[dict[str, Any]], offered: list[str]) -> Turn:
        """A question gets its tool call; tool results get the next call of a chain or the
        final answer."""
        q_index = max((i for i, m in enumerate(messages) if is_question(m)), default=None)
        if q_index is None:
            return Turn(f"{SCRIPTED_NOTE} There is no question to answer.")
        question = _text_of(messages[q_index]["content"])
        earlier = messages[:q_index]
        since = messages[q_index + 1 :]
        tool = self._pick(question, earlier, offered)
        if not since:
            return self._first_call(question, tool, earlier, offered)
        called = self._calls(since)
        results = _text_of(since[-1].get("content")) if since[-1].get("role") == "user" else ""
        found = USE_SESSION.search(results)
        if (
            tool
            and found
            and called
            and called[-1].get("name") == "find_session"
            and tool != "find_session"
            and all(b.get("name") != tool for b in called)
        ):
            session = Asked(found["event"], int(found["year"]), found["session"])
            args = self._args(tool, question, earlier, session)
            return Turn(
                f"Found the session; now running {tool}.", [(tool, args)], "Running " + tool
            )
        return Turn(self._final(since))

    def _pick(self, question: str, earlier: list[dict[str, Any]], offered: list[str]) -> str | None:
        if NO_TOOL.search(question):
            return None
        for pattern, tool in RULES:
            if tool not in offered or not pattern.search(question):
                continue
            # A follow-up about "that corner" needs a find_mistakes result to read.
            follow_up = tool == "explain_corner" and not self.reader.read(question).lap
            if follow_up and not self._last_result(earlier, "find_mistakes"):
                continue
            return tool
        if "find_session" in offered:
            return "find_session"
        return "list_sessions" if "list_sessions" in offered else None

    def _first_call(
        self, question: str, tool: str | None, earlier: list[dict[str, Any]], offered: list[str]
    ) -> Turn:
        if tool is None:
            return Turn(
                f"{SCRIPTED_NOTE} The tools only cover sessions already processed (qualifying, "
                "sprints and races from 2022 on), so I can't answer that from the data."
            )
        asked = self.reader.read(question)
        # "last", "2 races ago" or "round 5" name a session loosely, even in a follow-up.
        loose = any(p.search(question) for p in (LATEST, BACK, ROUND))
        unnamed = not asked.event and (loose or not self._last_call(earlier).get("event"))
        if tool in NEEDS_EVENT and unnamed and "find_session" in offered:
            return Turn(
                "Finding the session first.",
                [("find_session", self._find_session_args(question, asked))],
                "Finding the session",
            )
        args = self._args(tool, question, earlier, Asked())
        where = args.get("event") or "the data"
        return Turn(f"Checking {where} with {tool}.", [(tool, args)], f"Checking {where}")

    def _find_session_args(self, question: str, asked: Asked) -> dict[str, Any]:
        """find_session's fields, as the model fills them: "round 5" -> round=5; "before last"
        or "2 races ago" -> recent=1 (the last race is one race ago); "last year" -> the season
        before the newest processed one; the event the question names; else recent=0, the
        latest session. Counting back ("last", "latest", "ago") means a race unless the question
        names another kind."""
        year = asked.year
        newest = self.reader.newest_season()
        if year is None and LAST_YEAR.search(question) and newest is not None:
            year = newest - 1
        round_ = ROUND.search(question)
        back = BACK.search(question)
        recent = None
        if round_ is None and (back or not asked.event):
            recent = max(int(back.group(1) or 2) - 1, 0) if back else 0
        counting = back or LATEST.search(LAST_YEAR.sub(" ", question))
        session = asked.session or ("R" if counting and round_ is None else None)
        return _drop_none(
            {
                "event": asked.event,
                "year": year,
                "session": session,
                "round": int(round_.group(1)) if round_ else None,
                "recent": recent,
            }
        )

    def _args(
        self, tool: str, question: str, earlier: list[dict[str, Any]], found: Asked
    ) -> dict[str, Any]:
        """Arguments for `tool`: the session the question names, else the one a find_session
        result names (`found`), else the previous call's (a follow-up); drivers from the question
        or the previous call."""
        asked = self.reader.read(question)
        context = self._last_call(earlier)
        if asked.event:
            event, year, session = asked.event, asked.year, asked.session
        elif found.event:
            event, year = found.event, asked.year or found.year
            session = asked.session or found.session
        else:
            event = context.get("event")
            year = asked.year or context.get("year")
            session = asked.session or context.get("session")
        drivers = asked.drivers or ([context["driver"]] if context.get("driver") else [])
        if tool == "find_session":
            return self._find_session_args(question, asked)
        if tool == "list_sessions":
            return _drop_none({"year": asked.year})
        if tool == "find_mistakes":
            driver = asked.drivers[0] if asked.drivers else None
            return _drop_none({"event": event, "driver": driver, "year": year, "session": session})
        if tool == "explain_corner":
            lap, turn, driver = asked.lap, asked.turn, drivers[0] if drivers else None
            row = ROW.search(self._last_result(earlier, "find_mistakes"))
            if row and (lap is None or turn is None):
                lap, turn = int(row["lap"]), row["turn"]
                driver = row["driver"] or driver
            corner: int | str | None = int(turn) if turn and turn.isdigit() else turn
            return _drop_none(
                {
                    "event": event,
                    "driver": driver,
                    "lap": lap,
                    "corner": corner,
                    "year": year,
                    "session": session,
                }
            )
        if tool == "compare_laps":
            a, b = [*drivers, None, None][:2]
            return _drop_none(
                {"event": event, "driver_a": a, "driver_b": b, "year": year, "session": session}
            )
        if tool == "compare_driving_styles":
            a, b = [*drivers, None, None][:2]
            return _drop_none({"driver_a": a, "driver_b": b, "year": asked.year})
        if tool == "get_race_summary":  # races and sprints only: else its default, the race
            summary = session if session in ("R", "S") else None
            return _drop_none({"event": event, "year": year, "session": summary})
        return {}

    @staticmethod
    def _calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            b
            for m in messages
            if m.get("role") == "assistant" and isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]

    def _last_call(self, earlier: list[dict[str, Any]]) -> dict[str, Any]:
        """The arguments of the conversation's latest call that named a session."""
        for block in reversed(self._calls(earlier)):
            args = block.get("input") or {}
            if isinstance(args, dict) and args.get("event"):
                return {
                    "event": args.get("event"),
                    "year": args.get("year"),
                    "session": args.get("session"),
                    "driver": args.get("driver") or args.get("driver_a"),
                }
        return {}

    def _last_result(self, earlier: list[dict[str, Any]], tool: str) -> str:
        """The text of the latest successful result of `tool`."""
        ids = {b.get("id") for b in self._calls(earlier) if b.get("name") == tool}
        for message in reversed(earlier):
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") in ids
                    and not block.get("is_error")
                ):
                    return _text_of(block.get("content"))
        return ""

    def _final(self, since: list[dict[str, Any]]) -> str:
        names = {b.get("id"): b.get("name") for b in self._calls(since)}
        lines = [f"{SCRIPTED_NOTE} Here is what the tools returned:"]
        for message in since:
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                name = names.get(block.get("tool_use_id"), "a tool")
                first = _first_line(_text_of(block.get("content")) or str(block.get("content")))
                if block.get("is_error"):
                    lines.append(f"- {name} returned an error: {first}")
                else:
                    lines.append(f"- {name}: {first}")
        return "\n".join(lines)


def scripted_client(
    claude: ScriptedClaude | None = None, *, paths: DataPaths | None = None
) -> AsyncAnthropic:
    """A real AsyncAnthropic client whose requests are answered by `claude` (by default a new
    ScriptedClaude reading event names from the session catalog under `paths`). It never retries,
    so a scripted 429 or 529 surfaces at once."""
    if claude is None:
        root = (paths or DataPaths()).processed
        claude = ScriptedClaude(catalog=lambda: list_sessions(root=root))
    http_client = DefaultAsyncHttpxClient(transport=httpx2.MockTransport(claude))
    return AsyncAnthropic(
        api_key="scripted", base_url=SCRIPTED_BASE_URL, max_retries=0, http_client=http_client
    )
