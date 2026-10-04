"""Ask a running API's chat a burst of questions from one client and check the limits stop it
(plan sections 3 and 14.4; `make limits-burst URL=... N=12`).

    uv run python scripts/limits_burst.py --url http://127.0.0.1:8001          # 12 questions
    uv run python scripts/limits_burst.py --url http://127.0.0.1:8810 -n 5 --origin http://localhost:3001

It reads /api/health first and exits without asking anything when the chat uses the real model:
a burst of real questions is never what a check needs (run it against the scripted chat). Then
/api/chat/status gives this client's questions left in the hour (10 on a fresh server), and each
question, a new conversation that needs no tool, must be answered (`done` last) until they run
out, after which every one must be refused 429 `rate_limited` with `limit: "hour"`, a
`retry_after_s` in the body matching the Retry-After header, and, with --origin, an
Access-Control-Expose-Headers naming Retry-After (so a browser on that origin can read it).

It prints a line per question and exits 1 when anything differs. It uses up this client's hour
on the server; restart a memory-store server to start again.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx2

QUESTION = "Who will win the 2027 championship?"  # the scripted chat answers it without a tool


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--url", required=True, help="the API, e.g. http://127.0.0.1:8001")
    parser.add_argument("-n", type=int, default=12, help="questions to ask (default 12)")
    parser.add_argument("--origin", help="send this Origin and check Retry-After is exposed")
    args = parser.parse_args()

    headers = {"Origin": args.origin} if args.origin else {}
    timeout = httpx2.Timeout(10, read=120)
    with httpx2.Client(base_url=args.url.rstrip("/"), timeout=timeout, headers=headers) as client:
        health = client.get("/api/health").json()
        mode = health.get("chat", {}).get("mode")
        print(f"{args.url}: chat {mode}, limits {json.dumps(health.get('limits'))}")
        if mode == "anthropic":
            print("The chat uses the real model here; a burst would spend money. Nothing asked.")
            return 1
        if mode != "fake":
            print(f"The chat isn't answering here (mode {mode}). Nothing asked.")
            return 1
        status = client.get("/api/chat/status").json()
        quota = status.get("quota")
        if quota is None:
            print("The limits are off on this server (status quota is null). Nothing asked.")
            return 1
        expected = quota["hour_left"]
        print(
            f"visitor {status.get('visitor')}: {quota['hour_left']} of {quota['per_hour']} left "
            f"this hour, {quota['day_left']} of {quota['per_day']} today; asking {args.n}"
        )
        problems = burst(client, args.n, expected, bool(args.origin))

    print(f"\n{len(problems)} problem(s)")
    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


def burst(client: httpx2.Client, n: int, expected: int, check_cors: bool) -> list[str]:
    """Ask `n` questions; the first `expected` must be answered and the rest refused."""
    problems: list[str] = []
    refusals = 0
    for i in range(1, n + 1):
        start = time.perf_counter()
        status, body, response_headers = ask(client)
        seconds = time.perf_counter() - start
        should_answer = i <= expected
        if status == 200:
            done = body
            if not done:
                problems.append(f"question {i}: the answer ended without a done event")
            quota = done.get("quota") or {}
            print(
                f"{i:3}. 200 answered in {seconds:.2f} s, quota "
                f"{quota.get('hour_left')}/{quota.get('day_left')} left"
            )
            if not should_answer:
                problems.append(f"question {i} was answered; expected 429 after {expected}")
            continue
        error = body.get("error", {})
        retry = error.get("retry_after_s")
        print(
            f"{i:3}. {status} {error.get('code')} limit={error.get('limit')} "
            f"retry_after_s={retry} ({seconds:.2f} s): {error.get('message')}"
        )
        if should_answer:
            problems.append(f"question {i} was refused ({status} {error.get('code')})")
            continue
        refusals += 1
        if (status, error.get("code"), error.get("limit")) != (429, "rate_limited", "hour"):
            problems.append(f"question {i}: {status} {error.get('code')}, not 429 rate_limited")
        if not isinstance(retry, int) or retry < 1:
            problems.append(f"question {i}: retry_after_s {retry!r} is not a positive integer")
        if response_headers.get("retry-after") != str(retry):
            problems.append(f"question {i}: Retry-After {response_headers.get('retry-after')!r}")
        exposed = response_headers.get("access-control-expose-headers", "").lower()
        if check_cors and "retry-after" not in exposed:
            problems.append(f"question {i}: Retry-After isn't exposed to the browser")
    if n > expected and refusals == 0:
        problems.append("no question was refused")
    return problems


def ask(client: httpx2.Client) -> tuple[int, dict[str, Any], dict[str, str]]:
    """One question in a new conversation: the status, the `done` event (or the error body)
    and the response headers."""
    with client.stream("POST", "/api/chat", json={"message": QUESTION}) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        if response.status_code != 200:
            response.read()
            return response.status_code, response.json(), headers
        last: dict[str, Any] = {}
        name = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:") and name == "done":
                last = json.loads(line[5:])
        return 200, last, headers


if __name__ == "__main__":
    sys.exit(main())
