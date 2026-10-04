"""Every analysis tool as a ToolSpec, in the order the MCP server, the REST API and the chat list
them.

The order is fixed because the chat sends the tool list at the start of every request, where it
is part of the cached prompt prefix: reordering it invalidates the cache (and changes the chat's
prompt version, so conversations from before the change are refused as expired). The order is
find_session, list_sessions, get_race_summary, find_mistakes, explain_corner, compare_laps,
compare_driving_styles. The specs for list_sessions, find_mistakes, explain_corner and
compare_laps are defined here; find_session, get_race_summary and compare_driving_styles live in
their own modules, next to their Params models.

Five tools draw a chart (an MCP App bundle, web/src/mcp-app/<chart>.html): get_race_summary,
find_mistakes, explain_corner, compare_laps and compare_driving_styles. find_session and
list_sessions answer in text (find_session's data is structured, but has no chart). The chart
data of find_mistakes (its track map) and explain_corner (its map and replay) is built inside
the tools (tools.mistakes, tools.mistake_charts), so their specs need no wrapper.

The descriptions are what the model reads to choose a tool and fill in its arguments, so they
say what each tool answers, with example questions, and what its result can and can't tell.
"""

from __future__ import annotations

from itertools import groupby
from pathlib import Path

from race_engineer.config import PROCESSED_DIR
from race_engineer.tools.find_session import FIND_SESSION
from race_engineer.tools.lap_comparison import compare_laps
from race_engineer.tools.mistakes import explain_corner, find_mistakes
from race_engineer.tools.params import (
    CompareLapsParams,
    ExplainCornerParams,
    FindMistakesParams,
    ListSessionsParams,
)
from race_engineer.tools.race_summary import RACE_SUMMARY
from race_engineer.tools.registry import DataPaths, ToolOutput, ToolSpec
from race_engineer.tools.sessions import SessionInfo, list_sessions
from race_engineer.tools.styles import COMPARE_DRIVING_STYLES


def sessions_text(year: int | None = None, root: Path = PROCESSED_DIR) -> str:
    """Processed sessions grouped by season and race weekend."""
    everything = list_sessions(root=root)
    sessions = [s for s in everything if year is None or s.year == year]
    if not sessions:
        seasons = ", ".join(str(y) for y in sorted({s.year for s in everything}))
        return f"No processed sessions in {year}. Processed seasons: {seasons}."
    scope = f" in {year}" if year is not None else ""
    lines = [
        f"{len(sessions)} processed sessions{scope}. Give compare_laps the event name or "
        "location, the year and the session code."
    ]

    def weekend(s: SessionInfo) -> tuple[int, int, str, str]:
        return s.year, s.round, s.event, s.location

    for season, in_season in groupby(sessions, key=lambda s: s.year):
        lines.append(f"{season}:")
        for (_, round_, event, location), group in groupby(in_season, key=weekend):
            parts = ", ".join(f"{s.name} ({s.code}, {s.date})" for s in group)
            lines.append(f"  Round {round_} {event} ({location}): {parts}")
    return "\n".join(lines)


def _list_sessions(p: ListSessionsParams, paths: DataPaths) -> ToolOutput:
    return ToolOutput(sessions_text(p.year, paths.processed))


def _find_mistakes(p: FindMistakesParams, paths: DataPaths) -> ToolOutput:
    found = find_mistakes(
        p.event, p.driver, p.year, p.session, p.limit, paths.processed, paths.results
    )
    return ToolOutput(found.summary, found.data)


def _explain_corner(p: ExplainCornerParams, paths: DataPaths) -> ToolOutput:
    report = explain_corner(
        p.event, p.driver, p.lap, p.corner, p.year, p.session, paths.processed, paths.results
    )
    return ToolOutput(report.summary, report.chart)


def _compare_laps(p: CompareLapsParams, paths: DataPaths) -> ToolOutput:
    comparison = compare_laps(
        p.event, p.driver_a, p.driver_b, p.year, p.session, p.lap_a, p.lap_b, paths.processed
    )
    return ToolOutput(comparison.summary, comparison.chart)


LIST_SESSIONS = ToolSpec(
    name="list_sessions",
    title="List available sessions",
    description=(
        "List the F1 sessions available for analysis: season, round, Grand Prix name, "
        "location, session (Qualifying Q, Race R, Sprint S, Sprint Qualifying SQ) and date. "
        "Use it to answer 'which races can you analyse?' or to find the right event name "
        "and year before calling compare_laps."
    ),
    params=ListSessionsParams,
    run=_list_sessions,
)

FIND_MISTAKES = ToolSpec(
    name="find_mistakes",
    title="Find driver mistakes",
    description=(
        "List the biggest driver mistakes in one F1 session (qualifying, race, sprint or "
        "sprint qualifying), for the whole field or one driver. Use it for questions like "
        "'what mistakes did Colapinto make in Australia qualifying?', 'where did Verstappen "
        "lose time in the Monza race?' or 'who made the biggest mistakes in Baku?'. The "
        "candidates are the corners two anomaly detectors (a Transformer on the telemetry "
        "and Isolation Forest on hand-crafted features) found most unusual for that driver "
        "and turn: the top 1% of the session. Each is compared with the driver's usual way "
        "through that turn in the same session and comes with the lap, turn, a mistake type "
        "(early braking, over-slowing (a possible lock-up when the braking was also longer "
        "or harder), late throttle / wide exit, hesitation, track limits or a race-control "
        "incident naming the driver alone, or unclear: slower than usual, but no single "
        "feature explains how), the time lost and a one-sentence explanation. "
        "Ranked by time lost (only the part beyond the field's when the field was slow there "
        "too), discounted when the detectors were less sure, with 0.5 s added when race "
        "control named the driver alone at that turn. Neighbouring turns flagged on the same "
        "lap are one mistake, listed once. Traffic is read from where every car was: a "
        "corner lost to other cars isn't a mistake (being lapped and letting a faster car "
        "by, racing another car for a place or side by side, held up by a slower car ahead "
        "such as one on its out-lap in qualifying; a car placed only roughly, from sector "
        "times on a pit or slow lap, is named but not counted), and neither is a qualifying "
        "lap that was already slow before the corner; a car that was not already close and got "
        "past because of a mistake leaves the mistake listed, with the lost place in the "
        "explanation. Flags that aren't mistakes (not clearly slower than usual, traffic, "
        "abandoned laps, energy management, data problems) are counted, not listed. Only "
        "push laps in qualifying and green-flag laps in races and sprints are "
        "scored: opening laps, in and out laps and laps under yellow flags or a safety car "
        "never are, nor are laps without usable telemetry. The result says which laps were "
        "scored; a driver with none scored has no verdict, which is not the same as no "
        "mistakes. Follow up with explain_corner for details."
    ),
    params=FindMistakesParams,
    run=_find_mistakes,
    chart="find-mistakes",
)

EXPLAIN_CORNER = ToolSpec(
    name="explain_corner",
    title="Explain one corner",
    description=(
        "Explain how a driver took one corner on one lap compared with their usual way "
        "through it in the same session: time lost, split into entry (braking), apex and "
        "exit; the braking point, minimum and exit speeds, throttle pickup and coasting "
        "against usual; the mistake type and a plain-English explanation; any race-control "
        "note or data problem; the cars around (who passed whom, gaps, laps ahead) and any "
        "traffic that explains the loss; whether the detectors flagged it, or why they have no "
        "verdict (a lap they don't score, or results that need rebuilding). Works for any "
        "lap and turn with telemetry, flagged or not. Use it for "
        "'what happened to Colapinto at turn 7 on lap 2?' or to follow up a find_mistakes "
        "result. The result data also has this lap's and the usual speed, throttle and "
        "brake traces from 250 m before to 145 m after the apex."
    ),
    params=ExplainCornerParams,
    run=_explain_corner,
    chart="explain-corner",
    heavy=True,
)

COMPARE_LAPS = ToolSpec(
    name="compare_laps",
    title="Compare two drivers' laps",
    description=(
        "Compare two drivers' laps from one F1 session (qualifying, race, sprint or sprint "
        "qualifying) and show an interactive chart: both speed traces, the running time "
        "gap, throttle, braking and corner markers. Use it for questions like 'where did "
        "Russell beat Antonelli in Australia qualifying?', 'compare VER and NOR at Monza' "
        "or 'how did LEC's lap 30 compare with HAM's lap 31 in the race?'. Returns both lap "
        "times, the gap, official sector gaps, the three turns where most time was gained "
        "or lost (with braking points and apex speeds) and the biggest lead during the lap. "
        "By default each driver's fastest clean lap is used: the fastest push lap in "
        "qualifying, the fastest green-flag lap in a race. Call list_sessions to see which "
        "events are available."
    ),
    params=CompareLapsParams,
    run=_compare_laps,
    chart="compare-laps",
    heavy=True,
)

TOOLS: tuple[ToolSpec, ...] = (
    FIND_SESSION,
    LIST_SESSIONS,
    RACE_SUMMARY,
    FIND_MISTAKES,
    EXPLAIN_CORNER,
    COMPARE_LAPS,
    COMPARE_DRIVING_STYLES,
)
