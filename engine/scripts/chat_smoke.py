"""Ask the chat the example questions and print what it did (plan appendix B).

One conversation asks the eight example questions in order, with follow-ups that rely on the
answers before them ("what went wrong at the corner where he lost the most?"). A ninth question in
the same conversation must be refused with 409 turn_limit. A new conversation then asks something
the data can't answer, which must call no tool, and with the scripted chat a "#refuse" question
must come back as a refusal.

Question 3 asks about Gasly in the 2025 Abu Dhabi race rather than appendix B's Leclerc in Monza
2025 qualifying: Leclerc has no flagged mistake there, so the follow-up would have no corner to
explain. Gasly's top row (lap 5, turn 8) explains cleanly.

For each question it prints the tools called with their arguments, tool errors, chart bundles,
the start of the answer, seconds, tokens, cache reads and cost. Writes nothing.

    uv run python scripts/chat_smoke.py                  # the app in-process, scripted Claude
    uv run python scripts/chat_smoke.py --chat auto      # in-process, the real API (needs a key)
    uv run python scripts/chat_smoke.py --url http://127.0.0.1:8000   # a running API

--strict exits 1 when an expected tool isn't called or every call of it is an error, a question
after the first reads nothing from the prompt cache, or a check above fails. Expectations for
tools the server doesn't offer are skipped. With the real API, start it from a shell where
ANTHROPIC_BASE_URL is unset or the Claude API itself.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import httpx2

Events = list[tuple[str, dict[str, Any]]]
Post = Callable[[dict[str, Any]], tuple[int, Events, dict[str, Any]]]


@dataclass(frozen=True)
class Question:
    text: str
    expect: frozenset[str]  # tools that must be called; empty: no tool at all
    number: str = ""


CONVERSATION = [
    Question("What's the most recent race you can analyse?", frozenset({"find_session"}), "1"),
    Question("Summarise the 2023 Monaco Grand Prix.", frozenset({"get_race_summary"}), "2"),
    Question(
        "Where did Gasly make mistakes in the 2025 Abu Dhabi race?",
        frozenset({"find_mistakes"}),
        "3",
    ),
    Question(
        "What went wrong at the corner where he lost the most?", frozenset({"explain_corner"}), "4"
    ),
    Question(
        "Where did Norris gain on Piastri in 2025 Abu Dhabi qualifying?",
        frozenset({"compare_laps"}),
        "5",
    ),
    Question(
        "How does Hamilton's driving style differ from Leclerc's?",
        frozenset({"compare_driving_styles"}),
        "6",
    ),
    Question("Who made the biggest mistakes in the last race?", frozenset({"find_mistakes"}), "7"),
    Question("Were there any safety cars in the Baku race?", frozenset({"get_race_summary"}), "8"),
]
OVER_THE_LIMIT = "And one more question?"
NO_TOOL = Question("Who will win the 2027 championship?", frozenset(), "9")
REFUSE = "#refuse Tell me something you won't."


def parse_sse(lines: Iterator[str]) -> Events:
    """(event, data) pairs from SSE lines; comments (keep-alives) are skipped."""
    events: Events = []
    name, data = None, []
    for line in [*lines, ""]:
        if not line:
            if name is not None:
                events.append((name, json.loads("\n".join(data))))
            name, data = None, []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return events


def answer_turns(events: Events) -> list[str]:
    """The answer's text, one string per run of text between other events."""
    turns, current, last = [], [], ""
    for name, data in events:
        if name == "text":
            current.append(data["delta"])
        elif current and last == "text":
            turns.append("".join(current))
            current = []
        last = name
    return [*turns, "".join(current)] if current else turns


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    cost: float = 0.0

    def fail(self, what: str) -> None:
        self.failures.append(what)
        print(f"   FAIL {what}")


def ask(
    post: Post,
    question: Question,
    done: dict[str, Any] | None,
    offered: set[str],
    report: Report,
    *,
    first: bool,
) -> dict[str, Any] | None:
    """Ask one question, print what happened and check it; the `done` event's data."""
    body: dict[str, Any] = {"message": question.text}
    if done is not None:
        body |= {"history": done["history"], "signature": done["signature"]}
    start = time.perf_counter()
    status, events, error = post(body)
    seconds = time.perf_counter() - start
    print(f"\n{question.number or '-'}. {question.text}")
    if status != 200:
        report.fail(f"question {question.number}: HTTP {status} {error}")
        return None
    calls = [d for n, d in events if n == "tool_call"]
    results = {d["id"]: d for n, d in events if n == "tool_result"}
    for call in calls:
        result = results.get(call["id"], {})
        chart = (result.get("chart") or {}).get("bundle")
        state = "ERROR" if result.get("is_error") else "ok"
        print(
            f"   tool {call['name']}({json.dumps(call['input'], ensure_ascii=False)}) -> {state}"
            + (f", chart {chart}" if chart else "")
        )
        if result.get("is_error"):
            print(f"      {result.get('summary', '').splitlines()[0][:200]}")
    for name, data in events:
        if name in ("error", "refusal", "retry"):
            print(f"   {name}: {data}")
    answer = " ".join(turn.strip() for turn in answer_turns(events) if turn.strip())
    print("   answer: " + answer[:300].replace("\n", " ") + ("…" if len(answer) > 300 else ""))
    final = next((d for n, d in events if n == "done"), None)
    if final is None:
        report.fail(f"question {question.number}: no done event")
        return None
    usage = final["usage"]
    report.cost += final["cost_usd"]
    print(
        f"   {seconds:.1f} s, tokens in {usage['input']} out {usage['output']}, cache read "
        f"{usage['cache_read']} write {usage['cache_write']}, ${final['cost_usd']:.4f}, "
        f"stop {final['stop_reason']}, model {final['model']}, "
        f"{final['questions_left']} questions left"
    )
    called = {c["name"] for c in calls}
    answered = {c["name"] for c in calls if not results.get(c["id"], {}).get("is_error")}
    expected = {t for t in question.expect if t in offered}
    skipped = question.expect - expected
    if skipped:
        print(f"   (not offered by this server, not checked: {', '.join(sorted(skipped))})")
    if question.expect and expected - called:
        report.fail(f"question {question.number}: expected {sorted(expected - called)}")
    if failed := sorted((expected & called) - answered):
        report.fail(f"question {question.number}: every call of {failed} was an error")
    if not question.expect and called:
        report.fail(f"question {question.number}: expected no tool, called {sorted(called)}")
    if not first and usage["cache_read"] == 0:
        report.fail(f"question {question.number}: nothing read from the prompt cache")
    return final


@contextmanager
def in_process(
    chat: Literal["fake", "auto"] = "fake", specs: Sequence[Any] | None = None
) -> Iterator[tuple[Post, set[str]]]:
    """The API app in this process (its data from RACE_ENGINEER_DATA), without /mcp; `specs`
    replaces the tool list (default: every tool)."""
    from fastapi.testclient import TestClient

    from race_engineer.api.app import create_app
    from race_engineer.api.settings import Settings

    settings = dataclasses.replace(Settings.from_env(), chat=chat, mount_mcp=False)
    app = create_app(settings, specs=specs)
    with TestClient(app) as client:

        def post(body: dict[str, Any]) -> tuple[int, Events, dict[str, Any]]:
            response = client.post("/api/chat", json=body)
            if response.status_code != 200:
                return response.status_code, [], response.json()
            return 200, parse_sse(iter(response.text.splitlines())), {}

        health = client.get("/api/health").json()
        print(f"in-process app: chat {health['chat']}, data {health['status']}")
        yield post, {s.name for s in app.state.specs}


@contextmanager
def remote(url: str) -> Iterator[tuple[Post, set[str]]]:
    """A running API."""
    with httpx2.Client(base_url=url.rstrip("/"), timeout=httpx2.Timeout(10, read=180)) as client:

        def post(body: dict[str, Any]) -> tuple[int, Events, dict[str, Any]]:
            with client.stream("POST", "/api/chat", json=body) as response:
                if response.status_code != 200:
                    response.read()
                    return response.status_code, [], response.json()
                return 200, parse_sse(response.iter_lines()), {}

        health = client.get("/api/health").json()
        print(f"{url}: chat {health.get('chat')}, data {health.get('status')}")
        catalog = client.get("/api/tools")
        offered = {t["name"] for t in catalog.json()} if catalog.status_code == 200 else set()
        yield post, offered or {t for q in [*CONVERSATION, NO_TOOL] for t in q.expect}


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--app", action="store_true", help="run the app in-process (default)")
    where.add_argument("--url", help="use a running API, e.g. http://127.0.0.1:8000")
    parser.add_argument(
        "--chat", choices=["fake", "auto"], default="fake", help="in-process chat (default fake)"
    )
    parser.add_argument("--strict", action="store_true", help="exit 1 when a check fails")
    args = parser.parse_args()

    with remote(args.url) if args.url else in_process(cast(Any, args.chat)) as (post, offered):
        report = run(post, offered)
    return 1 if args.strict and report.failures else 0


def run(post: Post, offered: set[str]) -> Report:
    """Ask everything, print as it goes and the problems at the end."""
    report = Report()
    done = None
    for i, question in enumerate(CONVERSATION):
        done = ask(post, question, done, offered, report, first=i == 0) or done
    if done is not None:
        status, _, error = post(
            {"message": OVER_THE_LIMIT, "history": done["history"], "signature": done["signature"]}
        )
        code = error.get("error", {}).get("code")
        print(f"\n-. {OVER_THE_LIMIT}\n   HTTP {status} {code}")
        if done["questions_left"] == 0 and (status, code) != (409, "turn_limit"):
            report.fail(f"question over the limit: HTTP {status} {code}, not 409 turn_limit")
    fresh = ask(post, NO_TOOL, None, offered, report, first=False)
    if fresh is not None and fresh["model"] == "scripted":
        status, events, _ = post({"message": REFUSE})
        names = [n for n, _ in events]
        print(f"\n-. {REFUSE}\n   events {names}")
        if names != ["refusal", "done"]:
            report.fail(f"refusal: events {names}")

    print(f"\n{len(report.failures)} problem(s); total cost ${report.cost:.4f}")
    for failure in report.failures:
        print(f"  - {failure}")
    return report


if __name__ == "__main__":
    sys.exit(main())
