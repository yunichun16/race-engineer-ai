"""The accuracy report (M7 plan 5.4) and the run's JSON record.

A run against Claude writes `report/m7_chat_accuracy.md` and `data/evals/chat_eval-<time>.json`.
A scripted run tests the harness, not the chat, so it writes `data/evals/chat_eval-fake-<time>.md`
and `.json`, and `write()` refuses to put it anywhere under `report/`, whatever path it is
given. The JSON keeps the summaries, inputs and checks, never chart data (it carries derived
positions, which stay out of the repository and out of anything shareable).
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from race_engineer.chat_eval.checks import ArgResult, QuestionResult, no_call, summarise_calls
from race_engineer.chat_eval.client import EvalRun
from race_engineer.chat_eval.grounding import KINDS
from race_engineer.chat_eval.questions import default_tools
from race_engineer.config import REPORT_DIR, repo_root

REPORT_FILE = REPORT_DIR / "m7_chat_accuracy.md"
EVALS_DIR = repo_root() / "data" / "evals"  # gitignored with the rest of data/


class RefusedWrite(ValueError):
    """A scripted run asked to be written under report/."""


def commit() -> str:
    """The repository's short commit, "+changes" when the tree has uncommitted edits."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_root(),
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_root(),
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "an unknown commit"
    return f"{head}+changes" if dirty else head


@dataclass(frozen=True)
class Totals:
    questions: int
    passed: int
    tool_choice: int
    args_passed: int
    args_checked: int
    grounded_questions: int
    numbers: dict[str, int]
    behaviour_passed: int
    behaviour_checked: int
    cost_usd: float
    question_seconds: float


def totals(run: EvalRun) -> Totals:
    rs = run.results
    args = [a for r in rs for a in r.args if a.passed is not None]
    behaviours = [r.behaviour for r in rs if r.behaviour is not None]
    return Totals(
        questions=len(rs),
        passed=sum(r.passed for r in rs),
        tool_choice=sum(r.tool_choice.passed for r in rs),
        args_passed=sum(bool(a.passed) for a in args),
        args_checked=len(args),
        grounded_questions=sum(r.grounding.passed for r in rs),
        numbers={k: sum(r.grounding.count(k) for r in rs) for k in KINDS},
        behaviour_passed=sum(b.passed for b in behaviours),
        behaviour_checked=len(behaviours),
        cost_usd=sum(float((r.turn.done or {}).get("cost_usd") or 0) for r in rs),
        question_seconds=sum(r.turn.seconds for r in rs),
    )


def paths_for(
    run: EvalRun, evals_dir: Path = EVALS_DIR, report: Path | None = None
) -> tuple[Path, Path]:
    """Where the run's Markdown and JSON go by default (module docstring); `report` replaces
    the Markdown path."""
    stamp = run.started.strftime("%Y%m%d-%H%M%S")
    stem = f"chat_eval-fake-{stamp}" if run.scripted else f"chat_eval-{stamp}"
    md = report or (evals_dir / f"{stem}.md" if run.scripted else REPORT_FILE)
    return md, evals_dir / f"{stem}.json"


def _inside(path: Path, folder: Path) -> bool:
    """Whether writing `path` would write into `folder`, however the path spells it: through
    "..", a symlink, another case on a case-insensitive disk (macOS: "../Report/x.md"), or a
    hard link to a file already in it. Folders are compared by identity on disk, not by name."""
    target = path.resolve()
    if target.is_relative_to(folder.resolve()):
        return True
    try:
        home = folder.stat()
    except OSError:
        return False  # no such folder: nothing can be written into it by another name
    for parent in target.parents:
        try:
            if os.path.samestat(parent.stat(), home):
                return True
        except OSError:
            continue  # a folder write() would create
    try:
        if target.stat().st_nlink > 1:
            return any(os.path.samefile(target, f) for f in folder.rglob("*") if f.is_file())
    except OSError:
        pass
    return False


def write(run: EvalRun, md: Path, js: Path, *, report_dir: Path = REPORT_DIR) -> None:
    """Write the report and the JSON; RefusedWrite for a scripted run aimed at `report_dir`."""
    if run.scripted:
        for path in (md, js):
            if _inside(path, report_dir):
                raise RefusedWrite(
                    f"refusing to write a scripted run to {path}: report/ holds only a run "
                    "against Claude (make chat-eval URL=... on an API with a real key)"
                )
    for path in (md, js):
        path.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render(run))
    js.write_text(json.dumps(record(run), indent=1, ensure_ascii=False) + "\n")


# ---- Markdown ----


def _cell(value: Any) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _args_cell(args: Iterable[ArgResult]) -> str:
    checked = [a for a in args if a.passed is not None]
    if not checked:
        notes = {a.note for a in args if a.note}
        return "n/a" + (f" ({'; '.join(sorted(notes))})" if notes else "")
    ok = sum(bool(a.passed) for a in checked)
    uncalled = sorted({a.note for a in checked if a.note == no_call(a.tool)})
    wrong = [f"{a.field} {a.got}" for a in checked if not a.passed and a.note != no_call(a.tool)]
    notes = uncalled + ([", ".join(wrong)] if wrong else [])
    return f"{ok}/{len(checked)}" + (f" ({'; '.join(notes)})" if notes else "")


def _expected(r: QuestionResult) -> str:
    q = r.question
    parts = [" or ".join(e) for e in q.expect_tools]
    if q.forbid_tools >= set(default_tools()):  # "*", which the loader expands to every tool
        parts.append("no tool")
    elif q.forbid_tools:
        parts.append("not " + ", ".join(sorted(q.forbid_tools)))
    if q.forbid_success:
        parts.append("no chart tool may succeed")
    return "; ".join(parts) or "any"


def _date(when: datetime) -> str:
    return when.strftime("%-d %b %Y %H:%M UTC")


def render(run: EvalRun) -> str:
    c, t = run.context, totals(run)
    n = t.questions
    lines = [
        f"_Generated by make chat-eval on {run.started:%Y-%m-%d} at {commit()}_",
        "",
        "# Chat accuracy checks",
        "",
    ]
    if run.scripted:
        lines += [
            "**Scripted run.** The chat here is the scripted stand-in for Claude, which picks "
            "tools by word rules and answers with the first line of each tool result, so this "
            "run tests the harness, not the chat: grounding passes by construction and some "
            "tool choices fail by design. Costs are the scripted tokens priced as Sonnet; "
            "nothing was spent.",
            "",
        ]
    numbers = t.numbers
    total_numbers = sum(numbers.values())
    lines += [
        "| | |",
        "|---|---|",
        f"| Chat | {_cell(c.mode)}, {_cell(', '.join(run.models) or c.model)} ({_cell(c.where)}) |",
        f"| Effort | {_cell(c.effort or 'not reported by the API (default medium)')} |",
        f"| Prompt version | {_cell(c.prompt_version or 'unknown')} |",
        f"| Data | latest session {_cell(c.latest_session)}; newest race "
        f"{_cell(_latest(run))}; scoring run {_cell(c.results_run)} |",
        f"| Questions | {len(run.results) // max(run.repeat, 1)} in "
        f"{len({r.question.conversation for r in run.results})} conversations, asked "
        f"{run.repeat} time{'s' if run.repeat != 1 else ''} |",
        f"| Run | {_date(run.started)}, {run.seconds:.1f} s in all |",
        "",
        "## Results",
        "",
        "| # | Question | Expected tools | Called tools | Arguments | Numbers: n / grounded / "
        "rounded / derived / ungrounded | Behaviour | Pass | s | $ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in run.results:
        g = r.grounding
        label = _label(r, run)
        behaviour = "—"
        if r.behaviour is not None:
            behaviour = f"{r.behaviour.name}: {'ok' if r.behaviour.passed else 'FAIL'}"
        counts = " / ".join(str(g.count(k)) for k in KINDS)
        cost = float((r.turn.done or {}).get("cost_usd") or 0)
        verdict = "yes" if r.passed else "**no**" + (f" ({r.problem})" if r.problem else "")
        lines.append(
            f"| {label} | {_cell(r.question.text)} | {_cell(_expected(r))} | "
            f"{_cell(summarise_calls(r.turn.calls))} | {_cell(_args_cell(r.args))} | "
            f"{len(g.verdicts)}: {counts} | {_cell(behaviour)} | {verdict} | "
            f"{r.turn.seconds:.1f} | {cost:.4f} |"
        )
    grounded_or_rounded = numbers["grounded"] + numbers["rounded"]
    lines += [
        "",
        "## Totals",
        "",
        f"- Passed every check: **{t.passed} of {n}**",
        f"- Tool choice: {t.tool_choice} of {n}",
        f"- Arguments: {t.args_passed} of {t.args_checked} fields",
        f"- Questions fully grounded: {t.grounded_questions} of {n}",
        f"- Numbers grounded or rounded: {grounded_or_rounded} of {total_numbers} "
        f"({numbers['grounded']} grounded, {numbers['rounded']} rounded, {numbers['derived']} "
        f"derived, {numbers['ungrounded']} ungrounded)",
        f"- Behaviour: {t.behaviour_passed} of {t.behaviour_checked}",
        f"- Cost ${t.cost_usd:.4f}{' (scripted, not spent)' if run.scripted else ''}; "
        f"{t.question_seconds:.1f} s answering, {run.seconds:.1f} s in all",
        "",
        "## Flagged numbers",
        "",
    ]
    flagged = [(r, v) for r in run.results for v in r.grounding.flagged()]
    if not flagged:
        lines.append("None: every number in every answer is in a source, or rounds from one.")
    for kind, heading in (("ungrounded", "Ungrounded"), ("derived", "Derived (a human look)")):
        rows = [(r, v) for r, v in flagged if v.kind == kind]
        if rows:
            lines += [f"{heading}:", ""]
            for r, v in rows:
                via = f" ({v.via})" if v.via else ""
                lines.append(f"- {_label(r, run)} **{_cell(v.number.text)}**{via}: {v.sentence}")
            lines.append("")
    lines += ["", "## Other findings", ""]
    found = _findings(run)
    lines += found or ["None."]
    lines += ["", "## How it checks", "", *_method(), "", "## Caveats", "", *_caveats(run), ""]
    return "\n".join(lines)


def _label(r: QuestionResult, run: EvalRun) -> str:
    """The question's label, "A4", with the run's number ("A4·2") when asked more than once."""
    return r.question.label if run.repeat == 1 else f"{r.question.label}·{r.run}"


def _latest(run: EvalRun) -> str:
    ref = run.context.latest.get("R")
    return f"{ref.year} {ref.event} ({ref.key})" if ref else "unknown"


def _findings(run: EvalRun) -> list[str]:
    out = []
    for r in run.results:
        label = _label(r, run)
        tc = r.tool_choice
        if r.problem:
            out.append(f"- {label}: the answer didn't complete ({r.problem}).")
        if tc.missing:
            out.append(f"- {label}: no successful call of {', '.join(tc.missing)}.")
        if tc.failed_only:
            failed = [c for c in r.turn.calls if c.name in tc.failed_only and c.is_error]
            first = failed[0].summary.splitlines()[0][:160] if failed else ""
            out.append(
                f"- {label}: {', '.join(tc.failed_only)} called but every call was an error "
                f'("{_cell(first)}").'
            )
        if tc.forbidden:
            out.append(f"- {label}: called {', '.join(tc.forbidden)}, which it shouldn't.")
        if tc.forbidden_success:
            out.append(
                f"- {label}: {', '.join(tc.forbidden_success)} answered, which it shouldn't."
            )
        for a in r.args:
            if a.passed is False and a.note != no_call(a.tool):  # else "no successful call" above
                note = f" ({a.note})" if a.note else ""
                out.append(
                    f"- {label}: {a.tool} {a.field} expected {_cell(a.expected)}, "
                    f"got {_cell(a.got)}{note}."
                )
        if r.behaviour is not None and not r.behaviour.passed:
            excerpt = _cell(r.turn.answer)[:300]
            out.append(
                f"- {label}: behaviour {r.behaviour.name} failed ({_cell(r.behaviour.note)}); "
                f'the answer, for a human look: "{excerpt}"'
            )
    return out


def _method() -> list[str]:
    return [
        "- **Tool choice:** each expected tool answered by a call whose result isn't an error; "
        "no forbidden tool called; no tool that must not succeed returned a result.",
        "- **Arguments:** compared as the tool resolved them (the session, drivers, lap and turn "
        "in its result), on the one successful call that matches the most fields.",
        "- **Grounding:** every number in the answer, as the page shows it, looked up among the "
        "numbers of the tool summaries and questions so far in the conversation, the system "
        "prompt and the tool definitions (the integers 0-3 always allowed). Rounded: a source "
        "rounds to it, or is a fraction written as a percentage. Derived: the sum or difference "
        "of two numbers of one summary or question, listed for a look and not counted as a "
        "failure. Anything else is ungrounded and fails the question.",
        "- **Behaviour:** named regular-expression checks on the answer.",
    ]


def _caveats(run: EvalRun) -> list[str]:
    return [
        f"- {len(run.results) // max(run.repeat, 1)} questions, asked {run.repeat} "
        f"time{'s' if run.repeat != 1 else ''}: the model's answers vary between runs, so a "
        "rerun can differ.",
        "- Behaviour checks are regular expressions: a failure may be a wording the pattern "
        "doesn't know, so each is listed with its answer.",
        "- Numbers written as words ('five corners') and list markers at the start of a line "
        "aren't checked. A rounded or derived number is accounted for, not proven right.",
        "- Arguments are checked on the call that matched best; a model that tries several "
        "sessions is judged on its best attempt.",
    ]


# ---- JSON ----


def record(run: EvalRun) -> dict[str, Any]:
    """The run as JSON: summaries, inputs and checks, no chart data and no history."""
    c = run.context
    t = totals(run)
    return {
        "v": 1,
        "generated_at": run.started.isoformat(),
        "commit": commit(),
        "scripted": run.scripted,
        "context": {
            "where": c.where,
            "mode": c.mode,
            "models": run.models,
            "effort": c.effort,
            "prompt_version": c.prompt_version,
            "latest_session": c.latest_session,
            "latest_race": _latest(run),
            "results_run": c.results_run,
        },
        "totals": dataclasses.asdict(t),
        "seconds": round(run.seconds, 2),
        "results": [_result(r) for r in run.results],
    }


def _result(r: QuestionResult) -> dict[str, Any]:
    done = r.turn.done or {}
    tc = r.tool_choice
    return {
        "run": r.run,
        "id": r.question.label,
        "question": r.question.text,
        "passed": r.passed,
        "problem": r.problem,
        "seconds": round(r.turn.seconds, 2),
        "model": done.get("model"),
        "stop_reason": done.get("stop_reason"),
        "usage": done.get("usage"),
        "cost_usd": done.get("cost_usd"),
        "calls": [
            {"name": c.name, "input": c.input, "is_error": c.is_error, "summary": c.summary}
            for c in r.turn.calls
        ],
        "answer": r.turn.answer,
        "tool_choice": {
            "passed": tc.passed,
            "missing": tc.missing,
            "failed_only": tc.failed_only,
            "forbidden": tc.forbidden,
            "forbidden_success": tc.forbidden_success,
        },
        "args": [
            {
                "tool": a.tool,
                "field": a.field,
                "expected": a.expected,
                "got": a.got,
                "passed": a.passed,
                "note": a.note,
            }
            for a in r.args
        ],
        "grounding": {
            "passed": r.grounding.passed,
            "counts": {k: r.grounding.count(k) for k in KINDS},
            "numbers": [
                {"text": v.number.text, "kind": v.kind, "via": v.via, "sentence": v.sentence}
                for v in r.grounding.verdicts
            ],
        },
        "behaviour": None
        if r.behaviour is None
        else {"name": r.behaviour.name, "passed": r.behaviour.passed, "note": r.behaviour.note},
    }
