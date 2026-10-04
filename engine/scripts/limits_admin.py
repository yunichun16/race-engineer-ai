"""The chat's kill switch and the limits store, from your own terminal (plan section 3.7):
`make chat-pause`, `make chat-resume`, `make chat-status` and `make limits-check`.

    export UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN  # read with `read -rs`, never echoed
    uv run python scripts/limits_admin.py status  # today's spend, reservations, questions, switch
    uv run python scripts/limits_admin.py pause   # the chat answers 503 chat_paused (saved answers)
    uv run python scripts/limits_admin.py resume
    uv run python scripts/limits_admin.py check   # the store's contract on the real database

`check` runs the cases `tests/test_limits_store.py` runs (`api/limits/contract.py`) against the
Upstash database, each under its own `re:test:<uuid>:<case>:` prefix on a day in 2031, then
deletes every key under `re:test:<uuid>:`. It costs a few thousand of the free plan's 500K
monthly commands. `--memory` runs any subcommand against an in-process store instead (a
self-test of this script; nothing is sent anywhere).

The deployed API remembers a pause for up to 60 s after it first sees it, and a resume takes up to
60 s to reach it. The URL and the token come from the environment and are never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
import uuid
from typing import Any

import anyio

from race_engineer.api.limits.contract import CASES, Clock, run_case
from race_engineer.api.limits.memory import MemoryStore
from race_engineer.api.limits.policy import MICRO, Policy, utc_day
from race_engineer.api.limits.store import KEY_PREFIX, CommandStore, StoreError
from race_engineer.api.limits.upstash import UpstashClient, UpstashStore

URL_VAR, TOKEN_VAR = "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"


def credentials() -> tuple[str, str]:
    url, token = os.environ.get(URL_VAR, "").strip(), os.environ.get(TOKEN_VAR, "").strip()
    if not (url and token):
        raise SystemExit(
            f"Set {URL_VAR} and {TOKEN_VAR} in this shell first (read -rs NAME && export NAME), "
            "or pass --memory for a self-test."
        )
    return url, token


def open_store(memory: bool, prefix: str = KEY_PREFIX, clock: Any = time.time) -> CommandStore:
    if memory:
        return MemoryStore(prefix, clock)
    url, token = credentials()
    return UpstashStore(UpstashClient(url, token), prefix, clock)


async def status(store: CommandStore) -> None:
    now = time.time()
    found = await store.status(None, now, Policy())
    print(f"store {store.name}, day {utc_day(now)} (UTC)")
    print(f"  kill switch   {'ON (the chat is paused)' if found.paused else 'off'}")
    print(f"  spent today   ${found.spend_micro / MICRO:.4f}")
    print(f"  reserved now  ${found.reserved_micro / MICRO:.4f} (questions in flight)")
    print(f"  questions     {found.questions} today, everyone together")


async def pause(store: CommandStore, paused: bool) -> None:
    await store.set_paused(paused)
    if paused:
        print("Paused: new questions get 503 chat_paused (within 60 s); the tools keep working.")
    else:
        print("Resumed: the API admits questions again within 60 s.")


async def check(memory: bool) -> int:
    """The contract on the store, each case under its own prefix; the keys deleted after."""
    run_prefix = f"re:test:{uuid.uuid4().hex}:"
    print(f"contract cases under {run_prefix} ({'memory' if memory else 'upstash'})")
    memory_store = MemoryStore(run_prefix) if memory else None  # one store, so cleanup sees it

    def factory(clock: Clock, name: str) -> CommandStore:
        if memory_store is not None:
            memory_store.clock = clock
            return _Unclosed(memory_store, f"{run_prefix}{name}:")
        return open_store(False, f"{run_prefix}{name}:", clock)

    failures = 0
    try:
        for case in CASES:
            start = time.perf_counter()
            try:
                await run_case(case, factory)
            except Exception as exc:
                failures += 1
                print(f"  FAIL {case.__name__}: {type(exc).__name__} {exc}")
                traceback.print_exc(limit=2)
            else:
                print(f"  ok   {case.__name__} ({time.perf_counter() - start:.2f} s)")
    finally:
        cleaner = memory_store or open_store(False)
        deleted = await delete_prefix(cleaner, run_prefix)
        print(f"deleted {deleted} test key(s)")
        await cleaner.aclose()
    print(f"{failures} case(s) failed" if failures else f"all {len(CASES)} cases passed")
    return 1 if failures else 0


async def delete_prefix(store: CommandStore, prefix: str) -> int:
    """Delete every key under `prefix` (SCAN, then DEL in batches)."""
    cursor, deleted = "0", 0
    while True:
        (found,) = await store.run(
            [["SCAN", cursor, "MATCH", f"{prefix}*", "COUNT", 500]], atomic=False
        )
        cursor, keys = str(found[0]), list(found[1])
        for i in range(0, len(keys), 100):
            (count,) = await store.run([["DEL", *keys[i : i + 100]]], atomic=False)
            deleted += int(count)
        if cursor == "0":
            return deleted


class _Unclosed(CommandStore):
    """A view of one memory store, under its own prefix, that a case may close without losing
    the keys (so the cleanup has something to delete)."""

    def __init__(self, inner: MemoryStore, prefix: str) -> None:
        super().__init__(prefix, inner.clock)
        self.inner = inner
        self.name = inner.name

    async def run(self, commands: Any, *, atomic: bool, now: float | None = None) -> list[Any]:
        return await self.inner.run(commands, atomic=atomic, now=now)


async def run(args: argparse.Namespace) -> int:
    if args.command == "check":
        return await check(args.memory)
    store = open_store(args.memory)
    try:
        if args.command == "status":
            await status(store)
        else:
            await pause(store, args.command == "pause")
    finally:
        await store.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("command", choices=["status", "pause", "resume", "check"])
    parser.add_argument(
        "--memory", action="store_true", help="an in-process store instead of Upstash (self-test)"
    )
    args = parser.parse_args()
    try:
        return anyio.run(run, args)
    except StoreError as exc:
        print(f"The limits store failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
