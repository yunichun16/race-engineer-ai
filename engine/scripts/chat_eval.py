"""Check the chat's answers to 15 questions: tool choice, arguments, grounding and behaviour
(M7 plan section 5; the questions are engine/evals/chat_questions.toml).

    uv run python scripts/chat_eval.py                                 # in-process, scripted chat
    uv run python scripts/chat_eval.py --url http://127.0.0.1:8000     # a running API
    uv run python scripts/chat_eval.py --url http://127.0.0.1:8000 --repeat 3

--app (the default) runs the API in this process with the scripted chat, always: it sets
RACE_ENGINEER_CHAT=fake whatever the environment says, and there is no option to ask a real
model in-process. --url asks a running API; a run against Claude needs an API started with a
key from your own terminal, where ANTHROPIC_BASE_URL is unset (M7 plan section 17).

A scripted run tests the harness, not the chat (the scripted model picks tools by word rules and
answers with the first line of each result): it writes data/evals/chat_eval-fake-<time>.md and
.json, and is refused anywhere under report/. A run whose chat is Claude on every answer writes
report/m7_chat_accuracy.md and data/evals/chat_eval-<time>.json (summaries, inputs and checks;
no chart data). --out writes the Markdown elsewhere.

--strict exits 1 when a question fails a check, after both files are written; the last line says
how many failed. With the scripted chat, which fails some checks by design, --strict exits 1 only
when the harness itself failed (a refused request, a stream error, no `done` event). Exit 2: the
API can't be reached or its chat is off, or a scripted run was aimed at report/.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from race_engineer.chat_eval import client, questions, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--app", action="store_true", help="the API in-process, scripted chat")
    where.add_argument("--url", help="a running API, e.g. http://127.0.0.1:8000")
    parser.add_argument("--repeat", type=int, default=1, help="ask everything N times")
    parser.add_argument("--strict", action="store_true", help="exit 1 when a question fails")
    parser.add_argument(
        "--questions", type=Path, default=questions.QUESTIONS_FILE, help="the questions file"
    )
    parser.add_argument("--out", type=Path, help="write the Markdown report here instead")
    parser.add_argument("--verbose", action="store_true", help="show the API's own log lines")
    parser.add_argument(
        "--evals-dir",
        type=Path,
        default=report.EVALS_DIR,
        help="where the JSON (and a scripted run's Markdown) go (default data/evals)",
    )
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    conversations = questions.load(args.questions)
    if not args.verbose:  # the app's per-request lines would bury the per-question ones
        for name in ("race_engineer", "httpx", "httpx2", "mcp"):
            logging.getLogger(name).setLevel(logging.WARNING)
    if not args.url:
        os.environ["RACE_ENGINEER_CHAT"] = "fake"  # and in_process() forces it in the settings
    try:
        with client.remote(args.url) if args.url else client.in_process() as api:
            run = client.run(api, conversations, repeat=args.repeat)
    except client.ChatUnavailable as exc:
        print(f"Not run: {exc}.", file=sys.stderr)
        return 2

    md, js = report.paths_for(run, args.evals_dir, args.out)
    try:
        report.write(run, md, js)
    except report.RefusedWrite as exc:
        print(str(exc), file=sys.stderr)
        return 2
    t = report.totals(run)
    print(
        f"\npassed {t.passed} of {t.questions}; tool choice {t.tool_choice}, arguments "
        f"{t.args_passed} of {t.args_checked}, fully grounded {t.grounded_questions}, behaviour "
        f"{t.behaviour_passed} of {t.behaviour_checked}; ${t.cost_usd:.4f}"
        f"{' (scripted, not spent)' if run.scripted else ''}, {run.seconds:.1f} s"
    )
    print(f"wrote {md}\nwrote {js}")
    failed, problems = len(run.failed), len(run.problems)
    if run.scripted:
        note = "; scripted chat, so not a finding" if failed else ""
        broken = f"; {problems} answers didn't complete" if problems else ""
        print(f"report written; {failed} questions failed a check{note}{broken}")
        return 1 if args.strict and problems else 0
    if failed:
        print(f"report written; {failed} questions failed a check")
    else:
        print("report written; every question passed")
    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    sys.exit(main())
