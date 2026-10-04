"""The shared tool registry: specs, validation, errors, the result cache, limits and warm-up."""

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import anyio
import pytest

from race_engineer.data import store
from race_engineer.inference import provenance
from race_engineer.mcp_server.server import create_server
from race_engineer.tools import registry
from race_engineer.tools.cache import file_signature, listing_signature, memo_by_files
from race_engineer.tools.definitions import EXPLAIN_CORNER, FIND_MISTAKES, TOOLS
from race_engineer.tools.params import Corner, Event, Lap, ToolParams
from race_engineer.tools.registry import (
    HEAVY_LIMIT,
    SUMMARY_MAX,
    DataPaths,
    ToolOutput,
    ToolSpec,
    UnknownToolError,
    data_version,
    run_tool,
    run_tool_async,
    without_titles,
)
from race_engineer.tools.sessions import ResultsUnavailableError, ToolInputError
from race_engineer.tools.synthetic import SyntheticSession, write_session

# Every tool, in the order the front ends list it (the chat's cached prompt depends on it).
FINAL_ORDER = [
    "find_session",
    "list_sessions",
    "get_race_summary",
    "find_mistakes",
    "explain_corner",
    "compare_laps",
    "compare_driving_styles",
]


@pytest.fixture(autouse=True)
def empty_result_cache() -> None:
    registry.RESULTS.clear()


class EchoParams(ToolParams):
    event: Event
    lap: Lap = 1
    corner: Corner = 1


class Counter:
    """A tool run function that counts its calls."""

    def __init__(self, summary: str = "ok") -> None:
        self.calls = 0
        self.summary = summary

    def __call__(self, p: EchoParams, paths: DataPaths) -> ToolOutput:
        self.calls += 1
        return ToolOutput(f"{self.summary} {p.event} {p.lap}", {"lap": p.lap})


def echo_spec(run=None, *, heavy: bool = False, name: str = "echo") -> ToolSpec:
    return ToolSpec(
        name=name,
        title="Echo",
        description="Echo the arguments.",
        params=EchoParams,
        run=run or Counter(),
        heavy=heavy,
    )


def write_manifest(results: Path, run_id: str) -> None:
    results.mkdir(parents=True, exist_ok=True)
    (results / provenance.MANIFEST).write_text(json.dumps({"run_id": run_id}))


# The specs


def test_specs_are_in_the_fixed_order() -> None:
    names = [s.name for s in TOOLS]
    assert len(set(names)) == len(names)
    assert names == [n for n in FINAL_ORDER if n in names]
    assert {"list_sessions", "find_mistakes", "explain_corner", "compare_laps"} <= set(names)


def test_input_schema_has_no_generated_titles() -> None:
    schema = EXPLAIN_CORNER.input_schema()
    assert "title" not in schema and "title" not in schema["properties"]["lap"]
    assert schema["required"] == ["event", "driver", "lap", "corner"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["lap"]["minimum"] == 1
    # A property that happens to be called "title" is kept.
    kept = without_titles({"title": "M", "properties": {"title": {"title": "T", "type": "string"}}})
    assert kept == {"properties": {"title": {"type": "string"}}}


@pytest.mark.anyio
async def test_mcp_lists_the_same_schemas_as_the_specs(tmp_path: Path) -> None:
    listed = {t.name: t for t in await create_server(tmp_path, tmp_path).list_tools()}
    for spec in TOOLS:
        tool = listed[spec.name]
        assert tool.description == spec.description and tool.title == spec.title
        # Unknown arguments are refused (RegistryServer), and the listing says so.
        assert spec.input_schema()["additionalProperties"] is False
        assert without_titles(tool.input_schema) == spec.input_schema(), spec.name
        ui = (tool.meta or {}).get("ui")
        assert (ui or {}).get("resourceUri") == spec.resource_uri


def test_get_spec_by_name() -> None:
    assert registry.get_spec("find_mistakes") is FIND_MISTAKES
    names = ", ".join(s.name for s in TOOLS)
    with pytest.raises(UnknownToolError, match=re.escape(f"No tool 'nope'. Tools: {names}.")):
        registry.get_spec("nope")


# Validation and errors


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            {"event": "x", "driver": "AAA", "lap": 0},
            "Invalid arguments for explain_corner: lap: must be at least 1; corner: required.",
        ),
        (
            {"event": "x", "driver": "AAA", "lap": "two", "corner": 3},
            "lap: must be a whole number.",
        ),
        ({"event": "", "driver": "AAA", "lap": 2, "corner": 3}, "event: must have at least 1"),
        ({"event": "x", "driver": "AAA", "lap": 2, "corner": 0}, "corner: must be at least 1."),
        (
            {"event": "x", "driver": "AAA", "lap": 2, "corner": "123456789"},
            "corner: must be at most 99 or must have at most 8 characters.",
        ),
        (
            {"event": "x", "driver": "AAA", "lap": 2, "corner": "turn 12345"},
            "corner: must have at most 8 characters.",  # not "must be a whole number" too
        ),
        (
            {"event": "x", "driver": "AAA", "lap": 2, "corner": 3, "year": 1950},
            "year: must be at least 2018.",
        ),
        (
            {"event": "x", "driver": "AAA", "lap": 2, "corner": 3, "turn": 3},
            "unknown parameter(s) turn (parameters: event, driver, lap, corner, year, session).",
        ),
    ],
)
def test_invalid_arguments_give_one_readable_line(args: dict, message: str) -> None:
    with pytest.raises(ToolInputError, match=re.escape(message)) as caught:
        run_tool(EXPLAIN_CORNER, args, DataPaths(Path("/nowhere"), Path("/nowhere")))
    assert "\n" not in str(caught.value)
    assert not isinstance(caught.value, ResultsUnavailableError)


def test_stale_results_become_results_unavailable(tmp_path: Path) -> None:
    def stale(p: EchoParams, paths: DataPaths) -> ToolOutput:
        raise provenance.StaleResultsError("style_corners.parquet not found: run `make m4`.")

    with pytest.raises(ResultsUnavailableError, match="run `make m4`") as caught:
        run_tool(echo_spec(stale), {"event": "x"}, DataPaths(tmp_path, tmp_path))
    assert isinstance(caught.value, ToolInputError)  # every `except ToolInputError` still works


def test_unknown_tool_name() -> None:
    with pytest.raises(UnknownToolError):
        run_tool("nope", {})


def test_summaries_are_capped(tmp_path: Path) -> None:
    long = echo_spec(Counter("x" * 20_000))
    summary = run_tool(long, {"event": "e"}, DataPaths(tmp_path, tmp_path)).summary
    assert len(summary) == SUMMARY_MAX
    assert summary.endswith("characters; the chart data has the full result.]")
    short = run_tool(echo_spec(), {"event": "e"}, DataPaths(tmp_path, tmp_path)).summary
    assert short == "ok e 1"


# The result cache


def test_results_are_cached_until_the_data_changes(tmp_path: Path) -> None:
    processed, results = tmp_path / "processed", tmp_path / "results"
    write_session(SyntheticSession(2026, 1, "Q", "Sample Grand Prix", "Nowhere"), processed)
    write_manifest(results, "run-1")
    paths = DataPaths(processed, results)
    run = Counter()
    spec = echo_spec(run)

    first = run_tool(spec, {"event": "e", "lap": 2}, paths)
    assert run_tool(spec, {"lap": 2, "event": "e"}, paths) is first  # same arguments, any order
    assert run_tool(spec, {"event": "e", "lap": 2, "corner": 1}, paths) is first  # the default
    assert run.calls == 1
    run_tool(spec, {"event": "e", "lap": 3}, paths)
    assert run.calls == 2

    write_manifest(results, "run-2")  # a new scoring run
    run_tool(spec, {"event": "e", "lap": 2}, paths)
    assert run.calls == 3
    (results / registry.MISTAKES_FILE).write_bytes(b"new")  # mistakes rebuilt
    run_tool(spec, {"event": "e", "lap": 2}, paths)
    assert run.calls == 4
    write_session(SyntheticSession(2026, 2, "Q", "Other Grand Prix", "Elsewhere"), processed)
    run_tool(spec, {"event": "e", "lap": 2}, paths)
    assert run.calls == 5
    run_tool(spec, {"event": "e", "lap": 2}, DataPaths(processed, tmp_path / "other"))
    assert run.calls == 6  # other folders are another dataset


def test_errors_are_not_cached(tmp_path: Path) -> None:
    calls = []

    def flaky(p: EchoParams, paths: DataPaths) -> ToolOutput:
        calls.append(p.event)
        if len(calls) == 1:
            raise ToolInputError("not yet")
        return ToolOutput("now")

    spec = echo_spec(flaky)
    with pytest.raises(ToolInputError):
        run_tool(spec, {"event": "e"}, DataPaths(tmp_path, tmp_path))
    assert run_tool(spec, {"event": "e"}, DataPaths(tmp_path, tmp_path)).summary == "now"


def test_data_version_follows_session_files(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "processed", tmp_path / "results")
    empty = data_version(paths)
    write_session(SyntheticSession(2026, 1, "Q", "Sample Grand Prix", "Nowhere"), paths.processed)
    one = data_version(paths)
    assert one != empty
    assert data_version(paths) == one


# Running from async code


@pytest.mark.anyio
async def test_run_tool_async(tmp_path: Path) -> None:
    out = await run_tool_async(echo_spec(), {"event": "e", "lap": 4}, DataPaths(tmp_path, tmp_path))
    assert out.summary == "ok e 4" and out.data == {"lap": 4}
    with pytest.raises(ToolInputError, match="lap: must be at least 1"):
        await run_tool_async(echo_spec(), {"event": "e", "lap": 0}, DataPaths(tmp_path, tmp_path))


@pytest.mark.anyio
async def test_heavy_tools_are_limited(tmp_path: Path) -> None:
    lock = threading.Lock()
    running, most = 0, 0

    def slow(p: EchoParams, paths: DataPaths) -> ToolOutput:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return ToolOutput(str(p.lap))

    spec = echo_spec(slow, heavy=True)
    paths = DataPaths(tmp_path, tmp_path)  # not the real results folder
    async with anyio.create_task_group() as tg:
        for lap in range(1, 7):
            tg.start_soon(run_tool_async, spec, {"event": "e", "lap": lap}, paths)
    assert most == HEAVY_LIMIT


# Warm-up


def test_warm_never_raises_and_times_each_step(tmp_path: Path) -> None:
    warmed = []
    spec = ToolSpec(
        name="warmable",
        title="Warmable",
        description="",
        params=EchoParams,
        run=Counter(),
        warm=lambda paths: warmed.append(paths),
    )
    paths = DataPaths(tmp_path / "processed", tmp_path / "results")  # no data at all
    seconds = registry.warm(paths, [spec])
    assert set(seconds) == {"catalog", "results", "warmable"}
    assert warmed == [paths]
    assert all(s >= 0 for s in seconds.values())


def test_warm_runs_the_steps_side_by_side(tmp_path: Path) -> None:
    # Each step waits for the other at the barrier: run one after the other, both would time out.
    barrier = threading.Barrier(2, timeout=5)
    met = []

    def step(name: str) -> ToolSpec:
        def warm(paths: DataPaths) -> None:
            barrier.wait()
            met.append(name)

        return ToolSpec(name, name, "", EchoParams, run=Counter(), warm=warm)

    paths = DataPaths(tmp_path / "processed", tmp_path / "results")
    seconds = registry.warm(paths, [step("a"), step("b")])
    assert sorted(met) == ["a", "b"]
    assert list(seconds) == ["catalog", "results", "a", "b"]  # step order, not finishing order


# memo_by_files


def test_memo_by_files_follows_the_files(tmp_path: Path) -> None:
    calls = []
    data = tmp_path / "data.txt"
    data.write_text("one")

    @memo_by_files(lambda a: a["path"], maxsize=2)
    def read(path: Path, upper: bool = False) -> str:
        calls.append(path)
        text = path.read_text()
        return text.upper() if upper else text

    assert read(data) == "one" and read(path=data) == "one"
    assert len(calls) == 1 and read.cache_info().hits == 1
    data.write_text("three")  # another size
    assert read(data) == "three" and len(calls) == 2
    read(data, upper=True)
    read(data, True)
    assert len(calls) == 3
    read.cache_clear()
    read(data)
    assert len(calls) == 4


def test_memo_by_files_follows_the_scoring_run(tmp_path: Path) -> None:
    calls = []

    @memo_by_files(results=lambda a: a["results"])
    def load(results: Path) -> int:
        calls.append(results)
        return len(calls)

    write_manifest(tmp_path, "run-1")
    assert load(tmp_path) == load(tmp_path) == 1
    write_manifest(tmp_path, "run-2")
    assert load(tmp_path) == 2


def test_file_signature(tmp_path: Path) -> None:
    assert file_signature(tmp_path / "missing") is None
    folder = tmp_path / "folder"
    folder.mkdir()
    empty = file_signature(folder)
    (folder / "a.parquet").write_bytes(b"1")
    assert file_signature(folder) != empty


class _NoEntryStat:
    """os.scandir whose entries refuse `stat()` and `inode()`."""

    def __init__(self, path: Path) -> None:
        self._entries = list(_scandir(path))

    def __enter__(self) -> "_NoEntryStat":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def __iter__(self) -> Iterator["_EntryWithoutStat"]:
        for entry in self._entries:
            yield _EntryWithoutStat(entry)


class _EntryWithoutStat:
    def __init__(self, entry: os.DirEntry) -> None:
        self.name, self.path = entry.name, entry.path

    def inode(self) -> int:  # a network or sandboxed filesystem may renumber them
        raise AssertionError(f"{self.name}'s inode number was read")

    def stat(self, **kwargs: object) -> os.stat_result:
        raise AssertionError(f"{self.name} was stat'ed")


_scandir = os.scandir


def _settle(folder: Path) -> os.stat_result:
    """Date `folder`'s last change an hour back (any write after this moves its time, whatever
    the clock's resolution) and return its stat."""
    then = time.time_ns() - 3_600_000_000_000
    os.utime(folder, ns=(then, then))
    return folder.stat()


def test_listing_signature_sees_a_rebuild_without_a_stat_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "sessions"
    assert listing_signature(folder) is None
    session = SyntheticSession(2026, 1, "Q", "Sample Grand Prix", "Nowhere")
    write_session(session, tmp_path)
    path = store.table_path("sessions", session.key, tmp_path)
    assert listing_signature(path) == file_signature(path)  # a file: its own stat
    settled = _settle(folder)
    monkeypatch.setattr(os, "scandir", _NoEntryStat)  # one listing, nothing read per entry
    one = listing_signature(folder)
    assert listing_signature(folder) == one
    monkeypatch.setattr(os, "scandir", _scandir)

    # The same session again, as `make data` writes it: a new file renamed over the old one,
    # with the old one's time put back. The same names, but creating and renaming the new file
    # moved the folder's time.
    info = path.stat()
    write_session(session, tmp_path)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert folder.stat().st_mtime_ns != settled.st_mtime_ns
    rebuilt = listing_signature(folder)
    assert rebuilt != one

    # A session added, then removed, each with the folder's time put back: the names show it.
    settled = _settle(folder)
    rebuilt = listing_signature(folder)
    other = SyntheticSession(2026, 2, "Q", "Other Grand Prix", "Elsewhere")
    write_session(other, tmp_path)
    os.utime(folder, ns=(settled.st_atime_ns, settled.st_mtime_ns))
    added = listing_signature(folder)
    assert added not in (one, rebuilt)
    store.table_path("sessions", other.key, tmp_path).unlink()
    os.utime(folder, ns=(settled.st_atime_ns, settled.st_mtime_ns))
    assert listing_signature(folder) == rebuilt  # the same files as before the addition

    # Not seen, as documented: a file rewritten in place, which leaves the folder's time alone.
    every_file = file_signature(folder)
    path.write_bytes(path.read_bytes() + b"\0")
    assert listing_signature(folder) == rebuilt and file_signature(folder) != every_file


@pytest.mark.parametrize("signature", ["file_signature", "listing_signature"])
def test_signatures_are_the_same_in_another_process(tmp_path: Path, signature: str) -> None:
    # data_version feeds the API's ETag, so it can't depend on Python's per-process text hash.
    (tmp_path / "a.parquet").write_bytes(b"1")
    code = (
        "import sys; from pathlib import Path; "
        f"from race_engineer.tools.cache import {signature} as signature; "
        "print(repr(signature(Path(sys.argv[1]))))"
    )
    env = os.environ | {"PYTHONHASHSEED": "12345"}
    other = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], env=env, capture_output=True, text=True
    )
    assert other.returncode == 0, other.stderr
    mine = file_signature if signature == "file_signature" else listing_signature
    assert other.stdout.strip() == repr(mine(tmp_path))
