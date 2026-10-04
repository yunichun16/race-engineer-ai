"""Check the API as it is deployed: the serve-only install, or a running deployment (plan M7 6.6).

`--local` checks this Python environment, which should be a serve-only install (`uv sync
--locked --no-default-groups` into a fresh venv; never the repository's .venv, which every other
command shares):

1. the training packages (torch, MLflow, FastF1, matplotlib, ONNX Runtime) aren't installed, and
   importing the app, every tool definition and the MCP server, in a fresh interpreter, loads
   none of them;
2. `race-engineer-api` from this environment starts on an empty data folder, with the scripted
   chat and stateless MCP, and answers: /api/health 200 and degraded (no data), /api/ready 503,
   /api/chat/status 200, and /mcp `initialize` and `tools/list` (8 tools), each a request of its
   own, answered as JSON.

`--remote <api> --origin <site>` checks a deployment without asking the chat anything:
health ok with the limits on (the Upstash store, no problems) and the chat available;
/api/ready 200; CORS preflights allowed from the site and refused from https://example.com and
http://localhost:3000; Retry-After exposed to the site; a tool's JSON gzipped; /api/saved
listing the saved answers, all recorded with Claude; the client address the limits see (the
`visitor` tag of /api/chat/status, unchanged by spoofed X-Forwarded-For and X-Real-IP headers);
the time of the first request (a cold start if the API was idle) and of one heavy
explain_corner call (more than 2 s on the 0.5-core request means asking for 1 core: plan 6.1).
It prints your visitor tag, to compare with the one your phone gets.

    # make serve-venv-check: a fresh serve-only venv, then the check with its own Python
    UV_PROJECT_ENVIRONMENT=../data/serve-venv uv sync --locked --no-default-groups
    ../data/serve-venv/bin/python scripts/serve_check.py --local [--port 8811]

    uv run python scripts/serve_check.py --origin https://<project>.vercel.app \\
        --remote https://<workspace>--race-engineer-api.modal.run     # part of make deploy-check

Not `uv run` for --local: it would first sync the default groups, training included, into
whichever venv it runs in. Standard library only, so it runs in the serve-only venv. Exits 1 on
any failed check.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TRAINING = ("torch", "mlflow", "fastf1", "matplotlib", "onnxruntime")
SERVING = (
    "race_engineer.api.app",
    "race_engineer.tools.definitions",
    "race_engineer.mcp_server.server",
)
TOOLS = 8
MCP_PROTOCOL = "2025-06-18"  # what initialize asks for; the server answers with the one it uses
HEAVY_SLOW_S = 2.0
STARTUP_S = 90.0
SPOOFED = ({"X-Forwarded-For": "203.0.113.7"}, {"X-Real-IP": "198.51.100.9"})
REFUSED_ORIGINS = ("https://example.com", "http://localhost:3000")


@dataclass
class Report:
    rows: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, ok, detail))
        print(f"{'ok  ' if ok else 'FAIL'}  {name}{f': {detail}' if detail else ''}", flush=True)
        return ok

    @property
    def failed(self) -> int:
        return sum(not ok for _, ok, _ in self.rows)


@dataclass(frozen=True)
class Answer:
    status: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes
    seconds: float

    def json(self) -> Any:
        data = (
            gzip.decompress(self.body)
            if self.headers.get("content-encoding") == "gzip"
            else self.body
        )
        return json.loads(data)


def fetch(
    url: str,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: Any = None,
    timeout: float = 30.0,
) -> Answer:
    """One request; an HTTP error status is an answer too. Bodies aren't decompressed."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        request.add_header("Content-Type", "application/json")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw_headers, content = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, raw_headers, content = error.code, error.headers, error.read()
    found = {k.lower(): v for k, v in raw_headers.items()}
    return Answer(status, found, content, time.perf_counter() - started)


def _json(answer: Answer) -> Any:
    try:
        return answer.json()
    except (ValueError, OSError):
        return None


# --- --local ------------------------------------------------------------------------------------

IMPORT_PROBE = f"""
import importlib, importlib.util, json, sys
training = {TRAINING!r}
installed = [name for name in training if importlib.util.find_spec(name) is not None]
for module in {SERVING!r}:
    importlib.import_module(module)
from race_engineer.tools.definitions import TOOLS
loaded = sorted({{m.split(".")[0] for m in sys.modules}} & set(training))
print(json.dumps({{"installed": installed, "loaded": loaded, "tools": len(TOOLS)}}))
"""


def check_imports(report: Report) -> None:
    done = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE], capture_output=True, text=True, timeout=120
    )
    if done.returncode != 0:
        tail = done.stderr.strip().splitlines()[-1:] or ["no output"]
        report.check("the app, the tools and the MCP server import", False, tail[0])
        return
    found = json.loads(done.stdout.strip().splitlines()[-1])
    report.check(
        "no training package installed",
        not found["installed"],
        f"installed: {', '.join(found['installed'])}"
        if found["installed"]
        else ", ".join(TRAINING),
    )
    report.check(
        "the app, the tools and the MCP server import without them",
        not found["loaded"] and found["tools"] > 0,
        f"{found['tools']} tool definitions"
        + (f"; loaded {', '.join(found['loaded'])}" if found["loaded"] else ""),
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(url: str, process: subprocess.Popen[bytes], timeout: float) -> float | None:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if process.poll() is not None:
            return None
        try:
            fetch(url, timeout=2)
            return time.perf_counter() - started
        except OSError:
            time.sleep(0.25)
    return None


def mcp(base: str, method: str, request_id: int, protocol: str | None) -> tuple[Answer, Any]:
    """One JSON-RPC request to /mcp on its own, as a stateless server takes them: the answer,
    and its JSON body (None unless the server answered with JSON)."""
    headers = {"Accept": "application/json, text/event-stream"}
    if protocol:
        headers["MCP-Protocol-Version"] = protocol
    params: dict[str, Any] = {}
    if method == "initialize":
        params = {
            "protocolVersion": MCP_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "serve_check", "version": "1"},
        }
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    answer = fetch(f"{base}/mcp", "POST", headers, body)
    is_json = answer.headers.get("content-type", "").startswith("application/json")
    return answer, _json(answer) if is_json else None


def check_mcp(base: str, report: Report) -> None:
    answer, found = mcp(base, "initialize", 1, None)
    result = (found or {}).get("result") or {}
    report.check(
        "/mcp initialize answered as JSON",
        answer.status == 200 and "protocolVersion" in result,
        f"{answer.status} {answer.headers.get('content-type', '')}, "
        f"protocol {result.get('protocolVersion')}",
    )
    session = answer.headers.get("mcp-session-id")
    report.check(
        "/mcp is stateless (no session id)",
        session is None,
        "a session id was issued: set RACE_ENGINEER_MCP_STATELESS=1" if session else "",
    )
    answer, found = mcp(base, "tools/list", 2, result.get("protocolVersion") or MCP_PROTOCOL)
    tools = ((found or {}).get("result") or {}).get("tools") or []
    report.check(
        f"/mcp tools/list on its own lists {TOOLS} tools",
        answer.status == 200 and len(tools) == TOOLS,
        f"{answer.status}, {len(tools)} tools",
    )


def run_local(report: Report, port: int = 0) -> None:
    check_imports(report)
    script = Path(sys.executable).with_name("race-engineer-api")
    if not report.check("race-engineer-api is installed here", script.exists(), str(script)):
        return
    port = port or _free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="serve-check-") as empty:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("ANTHROPIC_", "RACE_ENGINEER_", "UPSTASH_"))
        }
        env.pop("PORT", None)
        env |= {
            "RACE_ENGINEER_DATA": empty,
            "RACE_ENGINEER_CHAT": "fake",
            "RACE_ENGINEER_CHAT_SECRET": secrets.token_hex(16),
            "RACE_ENGINEER_MCP_STATELESS": "1",
        }
        log = Path(empty) / "api.log"
        with log.open("wb") as out:
            process = subprocess.Popen(
                [str(script), "--port", str(port), "--no-access-log"],
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
            try:
                started = _wait_for(f"{base}/api/health", process, STARTUP_S)
                if not report.check(
                    "race-engineer-api starts on an empty data folder",
                    started is not None,
                    f"answered after {started:.1f} s"
                    if started is not None
                    else log.read_text()[-600:],
                ):
                    return
                health = fetch(f"{base}/api/health")
                status = (_json(health) or {}).get("status")
                report.check(
                    "/api/health 200, degraded",
                    health.status == 200 and status == "degraded",
                    f"{health.status} {status}",
                )
                ready = fetch(f"{base}/api/ready")
                report.check("/api/ready 503 (no data)", ready.status == 503, str(ready.status))
                chat = fetch(f"{base}/api/chat/status")
                body = _json(chat) or {}
                report.check(
                    "/api/chat/status 200",
                    chat.status == 200 and "available" in body,
                    f"{chat.status}, mode {body.get('mode')}, available {body.get('available')}",
                )
                check_mcp(base, report)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


# --- --remote -----------------------------------------------------------------------------------


def _preflight(api: str, origin: str) -> Answer:
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    return fetch(f"{api}/api/chat", "OPTIONS", headers)


def _heavy_call(api: str, origin: str) -> tuple[str, Answer | None]:
    """explain_corner on the top row of the latest race's find_mistakes (as mcp_smoke does)."""
    headers = {"Origin": origin}

    def tool(name: str, **arguments: Any) -> Any:
        query = urllib.parse.urlencode(arguments)
        return _json(fetch(f"{api}/api/tools/{name}?{query}", headers=headers, timeout=60))

    match = ((tool("find_session", recent=0, session="R") or {}).get("data") or {}).get("match")
    if not match:
        return "find_session found no last race", None
    found = (
        tool("find_mistakes", event=match["event"], year=match["year"], session="R") or {}
    ).get("data") or {}
    if not found.get("mistakes"):
        return "find_mistakes listed nothing to explain", None
    row = found["mistakes"][0]
    arguments = {
        "event": match["event"],
        "year": match["year"],
        "session": "R",
        "driver": row["driver"],
        "lap": row["lap_number"],
        "corner": row["turn"],
    }
    query = urllib.parse.urlencode(arguments)
    answer = fetch(f"{api}/api/tools/explain_corner?{query}", headers=headers, timeout=120)
    return f"{row['driver']} lap {row['lap_number']} turn {row['turn']}, {match['key']}", answer


def run_remote(api: str, origin: str, store: str, saved: int, report: Report) -> None:
    api, origin = api.rstrip("/"), origin.rstrip("/")
    first = fetch(f"{api}/api/health", timeout=120)
    print(f"first request: {first.seconds:.1f} s (a cold start if the API was idle)")
    health = _json(first) or {}
    limits, chat = health.get("limits") or {}, health.get("chat") or {}
    report.check(
        "/api/health 200, ok",
        first.status == 200 and health.get("status") == "ok",
        f"{first.status} {health.get('status')} {health.get('problems') or ''}".strip(),
    )
    report.check(
        f"limits on, store {store}, no problems",
        limits.get("enabled") is True
        and limits.get("store") == store
        and not limits.get("problems"),
        f"enabled {limits.get('enabled')}, store {limits.get('store')}, "
        f"problems {limits.get('problems')}",
    )
    report.check(
        "the chat available",
        chat.get("available") is True,
        f"mode {chat.get('mode')}, model {chat.get('model')}, reason {chat.get('reason')}",
    )
    ready = fetch(f"{api}/api/ready")
    report.check("/api/ready 200", ready.status == 200, str(ready.status))

    allowed = _preflight(api, origin)
    report.check(
        f"CORS preflight from {origin} allowed",
        allowed.status == 200 and allowed.headers.get("access-control-allow-origin") == origin,
        f"{allowed.status}, allow-origin {allowed.headers.get('access-control-allow-origin')}",
    )
    for other in REFUSED_ORIGINS:
        refused = _preflight(api, other)
        report.check(
            f"CORS preflight from {other} refused",
            refused.headers.get("access-control-allow-origin") not in (other, "*"),
            f"{refused.status}, allow-origin {refused.headers.get('access-control-allow-origin')}",
        )
    status = fetch(f"{api}/api/chat/status", headers={"Origin": origin})
    exposed = status.headers.get("access-control-expose-headers", "")
    report.check(
        "Retry-After exposed to the site",
        "retry-after" in exposed.lower(),
        exposed or "no Access-Control-Expose-Headers",
    )

    sessions = fetch(
        f"{api}/api/tools/list_sessions?year=2025",
        headers={"Origin": origin, "Accept-Encoding": "gzip"},
    )
    report.check(
        "a tool's JSON gzipped",
        sessions.status == 200 and sessions.headers.get("content-encoding") == "gzip",
        f"{sessions.status}, {len(sessions.body):,} bytes, "
        f"content-encoding {sessions.headers.get('content-encoding')}",
    )

    listed = fetch(f"{api}/api/saved")
    answers = (_json(listed) or {}).get("answers") or []
    modes = sorted({a.get("mode") for a in answers})
    report.check(
        f"/api/saved lists {saved} answers, all recorded with Claude",
        listed.status == 200 and len(answers) == saved and set(modes) <= {"anthropic"},
        f"{listed.status}, {len(answers)} answers, modes {modes}",
    )

    tags = []
    for extra in ({}, *SPOOFED):
        body = _json(fetch(f"{api}/api/chat/status", headers=extra)) or {}
        tags.append(body.get("visitor"))
    report.check(
        "the client address: one visitor tag, spoofed headers ignored",
        tags[0] is not None and len(set(tags)) == 1,
        f"tags {tags} (plain, X-Forwarded-For, X-Real-IP)",
    )
    print(
        f"your visitor tag: {tags[0]} (open {api}/api/chat/status on your phone: it should differ)"
    )

    what, heavy = _heavy_call(api, origin)
    if heavy is None:
        report.check("explain_corner timed", False, what)
    else:
        slow = "; slower than that: redeploy with cpu=1.0" if heavy.seconds >= HEAVY_SLOW_S else ""
        report.check(
            f"explain_corner under {HEAVY_SLOW_S:g} s",
            heavy.status == 200 and not slow,
            f"{heavy.status} in {heavy.seconds:.2f} s ({what}){slow}",
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument(
        "--local", action="store_true", help="this environment, a serve-only install"
    )
    where.add_argument("--remote", metavar="API", help="a deployed API's origin")
    parser.add_argument("--origin", help="the site's origin (with --remote)")
    parser.add_argument("--store", default="upstash", help="the limits store expected (--remote)")
    parser.add_argument("--saved", type=int, default=7, help="saved answers expected (--remote)")
    parser.add_argument(
        "--port", type=int, default=0, help="the API's port (--local; default: a free one)"
    )
    args = parser.parse_args(argv)
    if args.remote and not args.origin:
        parser.error("--remote needs --origin, the site's origin")
    report = Report()
    try:
        if args.local:
            run_local(report, args.port)
        else:
            run_remote(args.remote, args.origin, args.store, args.saved, report)
    except OSError as exc:  # unreachable, refused or timed out: a failed check, not a traceback
        reason = getattr(exc, "reason", None) or exc
        report.check("the API answered", False, f"{type(exc).__name__}: {reason}")
    print(f"\n{report.failed} failed check(s) of {len(report.rows)}")
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
