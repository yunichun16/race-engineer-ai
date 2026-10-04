"""Write a made-up data tree the API serves with health ok, for CI's end-to-end check (plan M7 9).

    uv run python scripts/synthetic_tree.py /tmp/syn
    RACE_ENGINEER_DATA=/tmp/syn RACE_ENGINEER_CHAT=fake race-engineer-api       # serves it

The tree is laid out like data/ (RACE_ENGINEER_DATA):

- processed/ and results/: the synthetic weekend of tests/test_tools_mistakes.py (a qualifying
  session with known mistakes and a race, on a made-up circuit) with the M4 results of one
  scoring run (manifest.json and parquet files recording it), plus the synthetic style tables
  (race_engineer.tools.synthetic_style) recorded as built from the same run. So /api/health is
  ok, the catalog lists two sessions, and every tool answers.
- saved/last-race-mistakes.json: the scripted chat answering that example question on this data,
  in the format `web/scripts/saved.ts record` writes (plan 4.1), with mode "fake": the API serves
  it only with RACE_ENGINEER_SAVED_ALLOW_FAKE=1.

Nothing in it comes from F1 data. The test datasets are imported from engine/tests (they import
pytest), so it needs the dev group but no training package: CI's e2e job installs
`uv sync --locked --no-default-groups --group dev`. It checks the result in-process (health ok, a
recording with `done` last) and exits 1 otherwise; it refuses a folder that isn't empty.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from race_engineer.api.settings import Settings
from race_engineer.chat_eval.client import in_process
from race_engineer.tools.synthetic_style import write_style_results

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_tools_mistakes import RUN_ID, mistakes_dataset

SAVED_ID = "last-race-mistakes"  # web/src/content/questions.ts: its id, text and expected tool
SAVED_QUESTION = "Who made the biggest mistakes in the last race?"
SAVED_TOOL = "find_mistakes"


def recording(api: Any, health: dict[str, Any]) -> dict[str, Any]:
    """The scripted chat's answer to SAVED_QUESTION as a saved answer (plan 4.1)."""
    status, events, error = api.chat({"message": SAVED_QUESTION, "history": [], "signature": None})
    if status != 200:
        raise SystemExit(f"synthetic_tree: the chat answered HTTP {status}: {error}")
    names = [name for name, _ in events]
    if not names or names[-1] != "done" or names.count("done") != 1:
        raise SystemExit(f"synthetic_tree: the answer didn't end with done: {names}")
    if {"error", "retry", "refusal"} & set(names):
        raise SystemExit(f"synthetic_tree: the answer has an error, retry or refusal: {names}")
    if not any(name == "tool_result" and data.get("name") == SAVED_TOOL for name, data in events):
        raise SystemExit(f"synthetic_tree: {SAVED_TOOL} didn't answer: {names}")
    done = events[-1][1]
    # As saved.ts writes it: the history and its signature emptied (of no use outside the
    # conversation), the prompt version kept (the part of the signature before the dot).
    kept = [{"type": name} | data for name, data in events[:-1]]
    kept.append({"type": "done"} | done | {"history": [], "signature": ""})
    data = health["data"]
    return {
        "v": 1,
        "id": SAVED_ID,
        "question": SAVED_QUESTION,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "fake",
        "model": done["model"],
        "prompt_version": str(done.get("signature") or "").split(".")[0],
        "data": {"latest": data["latest"], "results_run": data["results_run"]},
        "events": kept,
    }


def build(out: Path) -> dict[str, Any]:
    """Writes the tree into `out` and returns its health."""
    processed, results = mistakes_dataset(out)
    write_style_results(results, RUN_ID)
    settings = Settings(processed=processed, results=results, saved_dir=out / "saved")
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # a line per scripted call otherwise
    with in_process(settings) as api:
        status, health = api.get("/api/health")
        if status != 200 or health["status"] != "ok":
            problems = "; ".join(health.get("problems", [])) if health else f"HTTP {status}"
            raise SystemExit(f"synthetic_tree: health isn't ok: {problems}")
        saved = recording(api, health)
    (out / "saved").mkdir()
    (out / "saved" / f"{SAVED_ID}.json").write_text(json.dumps(saved, indent=1) + "\n")
    return health


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("out", type=Path, help="an empty or new folder, e.g. /tmp/syn")
    args = parser.parse_args(argv)
    out: Path = args.out.resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        sys.exit(f"synthetic_tree: {out} isn't an empty folder")
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    health = build(out)
    files = sorted(p for p in out.rglob("*") if p.is_file())
    data = health["data"]
    print(
        f"{out}: {len(files)} files, {sum(p.stat().st_size for p in files) / 1024:.0f} KiB, "
        f"{time.perf_counter() - started:.1f} s"
    )
    print(
        f"health ok: {data['sessions']} sessions (latest {data['latest']}), "
        f"{data['mistakes']} mistakes, run {data['results_run']}, saved answer {SAVED_ID}"
    )


if __name__ == "__main__":
    main()
