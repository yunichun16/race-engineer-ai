"""Write the sample tool results to JSON for the MCP App dev host (web/src/mcp-app/dev-host.ts).

Every fixture is synthetic (real F1 data isn't committed) and is answered the way the MCP server
answers: the registry tool run on made-up data (`mcp_server.server.spec_result`), with a tool's
error message as an error result. Fixtures by chart page:

- telemetry: sample-telemetry.json, show_sample_telemetry.
- compare-laps: sample-compare-laps.json, AAA against BBB on a made-up session of two drivers
  built from the sample lap (race_engineer.tools.synthetic).
- find-mistakes, on the qualifying session of tests/test_tools_mistakes.py with a corners table
  (so the map has its turns): sample-find-mistakes.json for the whole field, -driver for AAA
  from a copy without the tracks table (so there is no map), -empty for DDD (no lap scored) and
  -error for results from another scoring run.
- explain-corner: sample-explain-corner.json, AAA braking 50 m early at turn 3 on lap 10 (map,
  replay and ghost lap); -traffic, AAA lapped by BBB at turn 3 of lap 9 in a race
  (tests/test_tools_mistakes.traffic_dataset), with BBB still close enough at the apex to be on
  the map; -noreplay, CCC's lap without a lap time (the map, but no replay); -error, a lap that
  doesn't exist.
- compare-styles, on the synthetic style tables (race_engineer.tools.synthetic_style):
  sample-compare-styles.json for teammates AAA and BBB, -rivals for AAA and CCC of another team,
  -sparse for AAA and GGG in 2024 (too few events for intervals) and -error for an unknown code.
- race-summary, on the made-up race of race_engineer.tools.synthetic.sample_race_session:
  sample-race-summary.json for AAA's race, -small for the whole field (no focus driver) and
  -error for the qualifying session of the same weekend.

The find-mistakes and explain-corner data are the test datasets in engine/tests, imported through
sys.path (pyproject's pyright extraPaths covers them); move them into race_engineer.tools.synthetic
once M4's test helpers can be shared. Temporary folders in a message are written as data/...,
so a second run writes the same bytes.

Usage: uv run python scripts/export_sample_result.py ../web/src/mcp-app
(a path to sample-telemetry.json also works; the other fixtures go next to it)
"""

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp_types import CallToolResult

from race_engineer.mcp_server.server import sample_telemetry_result, spec_result
from race_engineer.tools.definitions import (
    COMPARE_DRIVING_STYLES,
    COMPARE_LAPS,
    EXPLAIN_CORNER,
    FIND_MISTAKES,
    RACE_SUMMARY,
)
from race_engineer.tools.registry import DataPaths, ToolSpec
from race_engineer.tools.synthetic import (
    sample_comparison_session,
    sample_race_session,
    write_session,
)
from race_engineer.tools.synthetic_style import write_style_results

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_tools_mistake_charts import write_corners
from test_tools_mistakes import copy_results, mistakes_dataset, traffic_dataset

SAMPLE = "Sample Grand Prix"
OLD_RUN = "2026-01-01T00:00:00+00:00/old"  # a scoring run that isn't the current one


def write_fixture(
    result: CallToolResult, out: Path, arguments: dict | None = None, tmp: Path | None = None
) -> None:
    payload: dict[str, Any] = {
        "content": [
            c.model_dump(mode="json", by_alias=True, exclude_none=True) for c in result.content
        ],
        "structuredContent": result.structured_content,
    }
    if result.is_error:
        payload = {"content": payload["content"], "isError": True}
    if arguments is not None:  # the dev host replays them as the tool input
        payload = {"arguments": arguments} | payload
    text = json.dumps(payload, allow_nan=False)
    if tmp is not None:  # a temporary data folder named in a message: data/processed, data/results
        for spelling in sorted({str(tmp), str(tmp.resolve())}, key=len, reverse=True):
            text = re.sub(re.escape(json.dumps(spelling)[1:-1]) + r"/[^/\"\s]+", "data", text)
    out.write_text(text)
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


class Writer:
    """Runs registry tools on one temporary folder of synthetic data and writes the results."""

    def __init__(self, folder: Path, tmp: Path) -> None:
        self.folder, self.tmp = folder, tmp

    def __call__(
        self,
        name: str,
        spec: ToolSpec,
        arguments: dict[str, Any],
        paths: DataPaths,
        *,
        error: bool = False,
    ) -> None:
        result = spec_result(spec, arguments, paths)
        if result.is_error != error:
            text = getattr(result.content[0], "text", "")
            raise SystemExit(f"{name}: expected {'an error' if error else 'a result'}: {text}")
        write_fixture(result, self.folder / f"{name}.json", arguments, self.tmp)


def mistake_fixtures(write: Writer, tmp: Path) -> None:
    root, results = mistakes_dataset(tmp / "mistakes")
    write_corners(root, "2026_01_Q")
    paths = DataPaths(root, results)
    quali = {"event": SAMPLE, "session": "Q"}
    write("sample-find-mistakes", FIND_MISTAKES, quali, paths)
    write("sample-find-mistakes-empty", FIND_MISTAKES, quali | {"driver": "DDD"}, paths)
    no_track = tmp / "no-track" / "processed"  # as a session processed before the tracks table
    shutil.copytree(root, no_track, ignore=shutil.ignore_patterns("tracks"))
    write(
        "sample-find-mistakes-driver",
        FIND_MISTAKES,
        quali | {"driver": "AAA"},
        DataPaths(no_track, results),
    )
    stale = copy_results(results, tmp / "stale" / "results", run_id=OLD_RUN)
    write("sample-find-mistakes-error", FIND_MISTAKES, quali, DataPaths(root, stale), error=True)

    corner = quali | {"driver": "AAA", "lap": 10, "corner": 3}
    write("sample-explain-corner", EXPLAIN_CORNER, corner, paths)
    crash_lap = quali | {"driver": "CCC", "lap": 2, "corner": 3}
    write("sample-explain-corner-noreplay", EXPLAIN_CORNER, crash_lap, paths)
    write("sample-explain-corner-error", EXPLAIN_CORNER, corner | {"lap": 42}, paths, error=True)

    root, results = traffic_dataset(tmp / "traffic", lift_mps=40.0)  # BBB on the map too
    write_corners(root, "2026_02_R")
    lapped = {"event": "Traffic Grand Prix", "session": "R", "driver": "AAA", "lap": 9}
    lapped |= {"corner": 3}
    write("sample-explain-corner-traffic", EXPLAIN_CORNER, lapped, DataPaths(root, results))


def style_fixtures(write: Writer, tmp: Path) -> None:
    results = tmp / "styles" / "results"
    write_style_results(results)
    paths = DataPaths(tmp / "styles" / "processed", results)
    teammates = {"driver_a": "AAA", "driver_b": "BBB"}
    write("sample-compare-styles", COMPARE_DRIVING_STYLES, teammates, paths)
    rivals = {"driver_a": "AAA", "driver_b": "CCC", "year": 2025}
    write("sample-compare-styles-rivals", COMPARE_DRIVING_STYLES, rivals, paths)
    sparse = {"driver_a": "AAA", "driver_b": "GGG"}
    write("sample-compare-styles-sparse", COMPARE_DRIVING_STYLES, sparse, paths)
    unknown = {"driver_a": "AAA", "driver_b": "XYZ"}
    write("sample-compare-styles-error", COMPARE_DRIVING_STYLES, unknown, paths, error=True)


def race_fixtures(write: Writer, tmp: Path) -> None:
    root = tmp / "race" / "processed"
    write_session(sample_race_session(), root)
    write_session(sample_comparison_session(), root)  # the same weekend's qualifying
    paths = DataPaths(root, tmp / "race" / "results")
    race = {"event": SAMPLE, "session": "R"}
    write("sample-race-summary", RACE_SUMMARY, race | {"driver": "AAA"}, paths)
    write("sample-race-summary-small", RACE_SUMMARY, race, paths)
    quali = {"event": SAMPLE, "session": "Q"}
    write("sample-race-summary-error", RACE_SUMMARY, quali, paths, error=True)


def main(out: Path) -> None:
    folder, telemetry = (out.parent, out) if out.suffix == ".json" else (out, None)
    write_fixture(sample_telemetry_result(), telemetry or folder / "sample-telemetry.json")

    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        write = Writer(folder, tmp)
        laps = tmp / "laps" / "processed"
        write_session(sample_comparison_session(), laps)
        arguments: dict[str, Any] = {"event": SAMPLE, "driver_a": "AAA", "driver_b": "BBB"}
        write("sample-compare-laps", COMPARE_LAPS, arguments, DataPaths(laps, tmp / "laps"))
        mistake_fixtures(write, tmp)
        style_fixtures(write, tmp)
        race_fixtures(write, tmp)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
