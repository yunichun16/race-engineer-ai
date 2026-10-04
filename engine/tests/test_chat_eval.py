"""The chat accuracy harness (chat_eval/, scripts/chat_eval.py; M7 plan 5.5), offline.

Grounding, argument, tool-choice and behaviour checks on hand-made answers; the questions file
and its loader; the report and the JSON record; the refusal to write a scripted run into
report/; and a whole scripted run of the 15 questions in-process, on the synthetic session the
chat tests use (CI has no real data), writing only under a temporary folder.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from race_engineer.api.chat.prompt import SYSTEM_PROMPT
from race_engineer.api.settings import Settings
from race_engineer.chat_eval import client, report
from race_engineer.chat_eval.checks import (
    CHECKS,
    Context,
    ConversationState,
    SessionRef,
    ToolCall,
    Turn,
    behaviour,
    check_args,
    normalise_corner,
    page_text,
    prompt_sentences,
    resolve,
    tool_choice,
    turn_from_events,
)
from race_engineer.chat_eval.client import EvalRun
from race_engineer.chat_eval.grounding import Evidence, evidence_from, ground, numbers
from race_engineer.chat_eval.questions import (
    BEHAVIOURS,
    MAX_QUESTIONS,
    QuestionFileError,
    default_tools,
    latest_codes,
    load,
    parse,
)
from race_engineer.tools import registry
from race_engineer.tools.registry import DataPaths
from race_engineer.tools.synthetic import sample_comparison_session, write_session

TOOLS = default_tools()
CHART_MARKER = "CHART-ONLY-91c2"  # in chart data, never in a JSON record


@pytest.fixture(autouse=True)
def empty_result_cache() -> None:
    registry.RESULTS.clear()


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> DataPaths:
    """The chat tests' synthetic data (tests/test_chat_runner.py): one made-up session and no
    M4 results, so most tools answer with an error, which a run must survive."""
    root = tmp_path_factory.mktemp("chat_eval")
    (root / "processed").mkdir()
    (root / "results").mkdir()
    write_session(sample_comparison_session(), root / "processed")
    return DataPaths(root / "processed", root / "results")


def kinds(answer: str, *sources: str) -> list[tuple[str, str]]:
    evidence = Evidence()
    for text in sources:
        evidence.add(text)
    return [(v.number.text, v.kind) for v in ground(answer, evidence).verdicts]


def question(**fields: Any) -> Any:
    """One question parsed from a dict, as the file would hold it."""
    raw = {"conversation": [{"id": "T", "question": [{"id": "1", "text": "Q?", **fields}]}]}
    return parse(raw, TOOLS)[0].questions[0]


def context(**latest: SessionRef) -> Context:
    return Context(
        where="test",
        mode="fake",
        model="scripted",
        effort="medium",
        latest_session=None,
        results_run=None,
        latest=dict(latest),
        evidence=evidence_from([SYSTEM_PROMPT]),
        tools=TOOLS,
    )


AZERBAIJAN = SessionRef(2026, 15, "R", "Azerbaijan Grand Prix", "Baku")
GAS_ROWS = [
    {"lap_number": 5, "turn": "8", "time_lost_s": 0.65},
    {"lap_number": 2, "turn": "6", "time_lost_s": -0.73},  # ranked second on a penalty bonus
    {"lap_number": 39, "turn": "13", "time_lost_s": 0.47},
]


def session_data(event: str, location: str, year: int, rnd: int, code: str, **more: Any) -> dict:
    return {
        "event": event,
        "location": location,
        "year": year,
        "round": rnd,
        "session_code": code,
        "marker": CHART_MARKER,
        **more,
    }


def ok(name: str, args: dict, summary: str = "", data: dict | None = None) -> ToolCall:
    return ToolCall(f"t_{name}_{len(args)}", name, args, False, summary, data)


def failed(name: str, args: dict, summary: str = "No such event.") -> ToolCall:
    return ToolCall(f"e_{name}", name, args, True, summary, None)


def turn(*calls: ToolCall, answer: str = "", refused: bool = False) -> Turn:
    return Turn("Q?", 200, calls=list(calls), answer=answer, refused=refused, done={"model": "m"})


# ---- Grounding ----


def test_exact_numbers_and_thousands_separators() -> None:
    assert kinds("He lost 0.37 s over 13,945 corners (13945).", "0.37 s lost; 13,945 scored") == [
        ("0.37", "grounded"),
        ("13,945", "grounded"),
        ("13945", "grounded"),
    ]


def test_rounding_at_the_answers_precision() -> None:
    assert kinds("0.8 s", "0.79 s") == [("0.8", "rounded")]
    assert kinds("27.9 s", "27.921 s") == [("27.9", "rounded")]
    assert kinds("0.7 and 0.6", "0.65") == [("0.7", "rounded"), ("0.6", "rounded")]  # both ways
    assert kinds("0.9 s", "0.79 s") == [("0.9", "ungrounded")]
    assert kinds("0.80 s and 05", "0.8 s, lap 5") == [("0.80", "grounded"), ("05", "grounded")]


def test_a_fraction_written_as_a_percentage() -> None:
    assert kinds("44% of laps", "share 0.44") == [("44%", "rounded")]
    assert kinds("44 % of laps", "share 0.437") == [("44 %", "rounded")]
    assert kinds("44% of laps", "44% less") == [("44%", "grounded")]
    assert kinds("45% of laps", "share 0.44") == [("45%", "ungrounded")]


def test_letter_prefixes_are_dropped() -> None:
    assert kinds("At T15 he was P12 in Q2.", "turn 15; finished 12th") == [
        ("15", "grounded"),
        ("12", "grounded"),
        ("2", "grounded"),  # 0-3 are always allowed
    ]
    assert kinds("turn 7", "T7: 0.2 s") == [("7", "grounded")]


def test_derived_from_two_numbers_of_one_summary() -> None:
    summary = "NOR lap 16: 1:22.408; PIA lap 16: 1:22.437."
    assert kinds("NOR was 0.029 s faster.", summary) == [("0.029", "derived")]
    # Not across two summaries: each pair comes from one text.
    assert kinds("0.029 s", "NOR 1:22.408", "PIA 1:22.437") == [("0.029", "ungrounded")]
    found = ground("0.029", evidence_from([summary], derive=True)).verdicts[0]
    assert found.via == "22.437 - 22.408"
    # A number doubles only when the text has it twice: one 0.65 s row doesn't make 1.30.
    assert kinds("1.30 s in all", "lap 5, turn 8: 0.65 s") == [("1.30", "ungrounded")]
    assert kinds("1.30 s in all", "T8: 0.65 s; T9: 0.65 s") == [("1.30", "derived")]


def test_a_fabricated_number_is_flagged() -> None:
    evidence = Evidence()
    evidence.add("Lap 5, turn 8: 0.37 s lost, mostly on entry.")
    result = ground("Gasly lost 0.73 s at turn 8 on lap 5. It was mostly on entry.", evidence)
    assert not result.passed
    [flag] = list(result.flagged())
    assert (flag.number.text, flag.kind) == ("0.73", "ungrounded")
    assert flag.sentence == "Gasly lost 0.73 s at turn 8 on lap 5."


def test_tokens_are_looked_up_whole_never_as_substrings() -> None:
    assert kinds("37 corners and 7 laps", "0.37 s") == [("37", "ungrounded"), ("7", "ungrounded")]


def test_dates_and_signs() -> None:
    assert [(n.text, str(n.value)) for n in numbers("on 2026-07-05")] == [
        ("2026", "2026"),
        ("07", "7"),
        ("05", "5"),
    ]
    assert [n.text for n in numbers("gap -0.3, +0.4, \u22120.5; lap-5, T-2")] == [
        "-0.3",
        "+0.4",
        "\u22120.5",
        "5",
        "2",
    ]
    # Compared by absolute value: a source's +0.3 grounds "0.3 s slower".
    assert kinds("0.3 s slower", "delta +0.3 s") == [("0.3", "grounded")]


def test_a_number_only_in_the_system_prompt_is_grounded() -> None:
    result = ground("The data starts in 2022, so there's no 2021 race.", context().evidence)
    assert [(v.number.text, v.kind) for v in result.verdicts] == [
        ("2022", "grounded"),
        ("2021", "ungrounded"),
    ]
    with_question = context().evidence.copy()
    with_question.add("What happened in the 2021 Abu Dhabi race?")
    assert ground("The data starts in 2022, not 2021.", with_question).passed


def test_list_markers_are_not_numbers() -> None:
    assert kinds("Mistakes:\n1. one\n4. four\n5) lost 0.5 s", "0.5 s") == [("0.5", "grounded")]


# ---- What a question got ----


def test_page_text_drops_the_text_a_retry_or_refusal_voids() -> None:
    events = [
        ("text", {"delta": "Checking "}),
        ("text", {"delta": "Monza."}),
        ("tool_call", {"id": "a", "name": "find_mistakes", "input": {"event": "monza"}}),
        ("tool_result", {"id": "a", "name": "find_mistakes", "is_error": False, "summary": "s"}),
        ("text", {"delta": "Garbled 9.9"}),
        ("retry", {"message": "again"}),
        ("text", {"delta": "Nothing was flagged."}),
    ]
    assert page_text(events) == "Checking Monza.\n\nNothing was flagged."
    assert page_text([("text", {"delta": "Sure, 1.5"}), ("refusal", {"message": "no"})]) == ""


def test_turn_from_events_keeps_calls_results_and_chart_data() -> None:
    chart = {"bundle": "find-mistakes", "resource_uri": "ui://x", "data": {"k": 1}}
    events = [
        ("tool_call", {"id": "a", "name": "find_mistakes", "input": {"event": "monza"}}),
        ("tool_result", {"id": "a", "name": "find_mistakes", "is_error": False, "summary": "S"}),
        ("tool_call", {"id": "b", "name": "explain_corner", "input": {}}),
        ("tool_result", {"id": "b", "is_error": True, "summary": "Bad lap.", "chart": None}),
        ("done", {"model": "scripted", "history": [], "signature": "v.s"}),
    ]
    events[1][1]["chart"] = chart
    got = turn_from_events("Q?", 200, events, {}, 0.5)
    assert [(c.name, c.is_error, c.data) for c in got.calls] == [
        ("find_mistakes", False, {"k": 1}),
        ("explain_corner", True, None),
    ]
    assert got.problem is None
    assert turn_from_events("Q?", 409, [], {"error": {"code": "turn_limit"}}, 0).problem == (
        "HTTP 409 turn_limit"
    )
    assert turn_from_events("Q?", 200, [], {}, 0).problem == "the stream ended without a done event"


# ---- Tool choice ----


def test_an_expected_tool_that_only_errored_fails_tool_choice() -> None:
    q = question(expect_tools=["find_mistakes"])
    result = tool_choice(q, turn(failed("find_mistakes", {"event": "atlantis"})))
    assert not result.passed
    assert result.missing == ("find_mistakes",)
    assert result.failed_only == ("find_mistakes",)
    later = turn(failed("find_mistakes", {"event": "x"}), ok("find_mistakes", {"event": "monza"}))
    assert tool_choice(q, later).passed


def test_forbidden_tools_and_forbidden_successes() -> None:
    no_tool = question(forbid_tools=["*"])
    assert tool_choice(no_tool, turn()).passed
    assert tool_choice(no_tool, turn(failed("find_session", {}))).forbidden == ("find_session",)
    q = question(forbid_success=["get_race_summary"], expect_tools=["find_session|list_sessions"])
    assert tool_choice(q, turn(ok("list_sessions", {}), failed("get_race_summary", {}))).passed
    blocked = tool_choice(q, turn(ok("find_session", {}), ok("get_race_summary", {})))
    assert blocked.forbidden_success == ("get_race_summary",)
    assert not blocked.passed


# ---- Arguments ----


def test_arguments_are_compared_as_the_tool_resolved_them() -> None:
    q = question(
        expect_tools=["find_mistakes"],
        args={
            "find_mistakes": {
                "driver": "GAS",
                "event": "re:(?i)abu ?dhabi|yas|2025_24_",
                "year": 2025,
                "session": "race",
            }
        },
    )
    data = session_data("Abu Dhabi Grand Prix", "Yas Island", 2025, 24, "R", driver="GAS")
    # The model typed a circuit name, a lower-case code and an alias: all resolved by the tool.
    call = ok("find_mistakes", {"event": "yas marina", "driver": "gas", "session": "r"}, "", data)
    results = check_args(q, turn(call), [], context())
    assert [(a.field, a.passed) for a in results] == [
        ("driver", True),
        ("event", True),
        ("year", True),
        ("session", True),
    ]
    # An omitted session is the tool's default, read from the result: qualifying here.
    quali = ok("find_mistakes", {"event": "abu dhabi", "driver": "GAS", "year": 2025}, "")
    quali.data = session_data("Abu Dhabi Grand Prix", "Yas Island", 2025, 24, "Q", driver="GAS")
    wrong = {a.field: a for a in check_args(q, turn(quali), [], context())}
    assert not wrong["session"].passed
    assert wrong["session"].got == "Q"


def test_raw_inputs_are_normalised_when_the_result_shows_nothing() -> None:
    raw = resolve(failed("explain_corner", {"driver": "nor", "corner": "T5", "session": "race"}))
    assert (raw.driver, raw.corner, raw.sessions) == ("NOR", "5", ("R",))
    assert resolve(failed("explain_corner", {"driver": "10", "event": "x"})).driver is None
    assert resolve(failed("compare_laps", {"event": "x"})).sessions == ("Q",)  # its default
    assert resolve(failed("get_race_summary", {"event": "x"})).sessions == ("R",)
    assert [normalise_corner(c) for c in (5, "5", "T5", "turn 05", "T9A", "x", True)] == [
        "5",
        "5",
        "5",
        "5",
        "9a",
        None,
        None,
    ]
    # Only successful calls count for arguments.
    q = question(expect_tools=["explain_corner"], args={"explain_corner": {"corner": 5}})
    [result] = check_args(q, turn(failed("explain_corner", {"corner": 5})), [], context())
    assert result.passed is False
    assert result.note == "no successful explain_corner call"


def test_events_as_keys_and_the_newest_race() -> None:
    q = question(
        expect_tools=["find_mistakes"],
        args={"find_mistakes": {"event": "latest:R", "session": "R"}},
    )
    data = session_data("Azerbaijan Grand Prix", "Baku", 2026, 15, "R")
    call = ok("find_mistakes", {"event": "2026_15_R"}, "", data)
    assert all(a.passed for a in check_args(q, turn(call), [], context(R=AZERBAIJAN)))
    unknown = check_args(q, turn(call), [], context())
    assert unknown[0].passed is False
    assert unknown[0].note == "the newest R session is unknown"
    older = ok("find_mistakes", {"event": "baku"}, "", {**data, "year": 2025, "round": 17})
    assert not check_args(q, turn(older), [], context(R=AZERBAIJAN))[0].passed
    # find_session has no chart data: its session is read from the line the model reads.
    summary = (
        "2026 Azerbaijan Grand Prix, Race (R), 2026-09-26, Baku: round 15.\n"
        "Use event='Azerbaijan Grand Prix', year=2026, session='R' in other tools."
    )
    q1 = question(
        expect_tools=["find_session|list_sessions"],
        args={"find_session": {"event": "latest:R", "session": "R"}},
    )
    found = ok("find_session", {"recent": 0, "session": "R"}, summary)
    assert all(a.passed for a in check_args(q1, turn(found), [], context(R=AZERBAIJAN)))
    # Answered with list_sessions instead: find_session's arguments aren't checked.
    listed = check_args(q1, turn(ok("list_sessions", {})), [], context(R=AZERBAIJAN))
    assert {a.passed for a in listed} == {None}


def test_drivers_are_an_unordered_pair() -> None:
    q = question(expect_tools=["compare_laps"], args={"compare_laps": {"drivers": ["NOR", "PIA"]}})
    data = session_data("British Grand Prix", "Silverstone", 2024, 12, "Q")
    data["drivers"] = [{"code": "PIA", "number": "81"}, {"code": "NOR", "number": "4"}]
    call = ok(
        "compare_laps", {"event": "silverstone", "driver_a": "81", "driver_b": "nor"}, "", data
    )
    [result] = check_args(q, turn(call), [], context())
    assert (result.passed, result.got) == (True, "NOR/PIA")


def test_from_previous_reads_the_row_with_the_most_time_lost() -> None:
    q = question(
        expect_tools=["explain_corner"],
        args={"explain_corner": {"driver": "GAS", "from_previous": "find_mistakes.max_time_lost"}},
    )
    mistakes = ok("find_mistakes", {"event": "abu dhabi"}, "", {"mistakes": GAS_ROWS})
    data = session_data("Abu Dhabi Grand Prix", "Yas Island", 2025, 24, "R", driver="GAS")
    right = ok("explain_corner", {}, "", {**data, "lap_number": 5, "turn": "8"})
    assert all(a.passed for a in check_args(q, turn(right), [mistakes], context()))
    # The second row ranks above the third on its penalty bonus, but lost no time.
    wrong = ok("explain_corner", {}, "", {**data, "lap_number": 2, "turn": "6"})
    result = check_args(q, turn(wrong), [mistakes], context())[1]
    assert (result.passed, result.got) == (False, "lap 2, turn 6")
    assert result.note == "most time lost: lap 5, turn 8 (0.65 s)"
    # A find_mistakes call earlier in the same answer counts too; none at all fails.
    assert all(a.passed for a in check_args(q, turn(mistakes, right), [], context()))
    assert check_args(q, turn(right), [], context())[1].note == (
        "no successful find_mistakes result before it"
    )


# ---- Behaviour ----


def judged(name: str, answer: str, *calls: ToolCall, **latest: SessionRef) -> bool:
    return behaviour(name, turn(*calls, answer=answer), context(**latest)).passed


def test_behaviour_checks_on_positive_and_negative_answers() -> None:
    assert set(CHECKS) == set(BEHAVIOURS)
    cases = {
        "nothing_flagged": (
            [
                "No mistakes were flagged for Leclerc.",
                "0 of 66 scored corners flagged.",
                "The detectors didn\u2019t flag any corner.",
            ],
            ["Leclerc made two mistakes, at turns 4 and 7."],
        ),
        "not_in_dataset": (
            [
                "The data starts in 2022.",
                "That race isn\u2019t in the data.",
                "There's no processed session for 2021.",
            ],
            ["Verstappen won by 5 seconds."],
        ),
        "declines_prediction": (
            ["I can't predict the 2027 championship.", "Nobody can know that; I don't know."],
            ["Verstappen will win it.", "The 2027 favourite is clear."],
        ),
    }
    for name, (good, bad) in cases.items():
        for text in good:
            assert judged(name, text), (name, text)
        for text in bad:
            assert not judged(name, text), (name, text)
    assert not judged("declines_prediction", "I can't predict it.", ok("find_session", {}))
    assert behaviour("declines_prediction", turn(refused=True), context()).passed


def test_names_latest_and_the_teammate_caveat() -> None:
    assert judged("names_latest", "The newest is the 2026 Azerbaijan Grand Prix.", R=AZERBAIJAN)
    assert judged("names_latest", "The race in Baku.", R=AZERBAIJAN)
    assert not judged("names_latest", "Monza, in 2025.", R=AZERBAIJAN)
    assert not judged("names_latest", "Azerbaijan.")  # the newest race is unknown
    rivals = ok("compare_driving_styles", {}, "", {"teammates": False})
    assert judged("teammate_caveat", "Different cars, so it mixes car and driver.", rivals)
    assert not judged("teammate_caveat", "Hamilton brakes later.", rivals)
    teammates = ok("compare_driving_styles", {}, "", {"teammates": True})
    assert judged("teammate_caveat", "Hamilton brakes later.", teammates)


def test_no_prompt_leak() -> None:
    sentences = prompt_sentences()
    assert len(sentences) == 5
    assert all(s in " ".join(SYSTEM_PROMPT.split()) for s in sentences)
    assert judged("no_prompt_leak", "I can't share my instructions, but I can analyse races.")
    leaked = "Sure. " + sentences[0].replace("'", "\u2019")
    assert not judged("no_prompt_leak", leaked)


# ---- The questions file ----


def test_the_questions_file() -> None:
    conversations = load()
    questions = [q for c in conversations for q in c.questions]
    assert len(questions) == 15
    assert [q.id for q in questions] == [str(n) for n in range(1, 16)]
    assert all(len(c.questions) <= MAX_QUESTIONS for c in conversations)
    assert {q.behaviour for q in questions} - {None} <= set(BEHAVIOURS)
    for q in questions:
        named = {t for e in q.expect_tools for t in e} | q.forbid_tools | q.forbid_success
        assert named <= set(TOOLS)
        assert {a.tool for a in q.args} <= set(TOOLS)
    assert latest_codes(conversations) == {"R"}
    by_id = {q.id: q for q in questions}
    assert by_id["14"].forbid_tools == frozenset(TOOLS)  # "*"
    assert by_id["1"].expect_tools == (("find_session", "list_sessions"),)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"expect_tools": ["find_mistake"]}, "unknown tool 'find_mistake'"),
        ({"behaviour": "polite"}, "unknown behaviour 'polite'"),
        ({"expected_tools": ["find_mistakes"]}, "unknown keys ['expected_tools']"),
        ({"args": {"find_mistakes": {"drivers": "NOR"}}}, "can't use 'NOR'"),
        ({"args": {"find_mistakes": {"session": "practice"}}}, "can't use 'practice'"),
        ({"args": {"find_mistakes": {"event": "re:(abu"}}}, "bad pattern"),
        ({"args": {"find_mistakes": {"stint": 2}}}, "unknown field"),
        ({"args": {"compare_laps": {"lap": 2}}, "forbid_tools": ["*"]}, "which is forbidden"),
    ],
)
def test_the_loader_refuses_mistakes(fields: dict, message: str) -> None:
    with pytest.raises(QuestionFileError, match=message.replace("[", r"\[").replace("(", r"\(")):
        question(**fields)


def test_a_conversation_holds_at_most_eight_questions() -> None:
    many = [{"id": str(n), "text": f"Q{n}?"} for n in range(MAX_QUESTIONS + 1)]
    with pytest.raises(QuestionFileError, match="the chat stops at 8"):
        parse({"conversation": [{"id": "A", "question": many}]}, TOOLS)


# ---- The report ----


def synthetic_run(mode: str = "fake", model: str = "scripted") -> EvalRun:
    """Two judged answers: one right, one with a fabricated number and the wrong tool."""
    q1 = question(
        expect_tools=["find_mistakes"],
        args={"find_mistakes": {"driver": "GAS", "year": 2025}},
        behaviour="nothing_flagged",
    )
    q2 = question(expect_tools=["explain_corner"])
    data = session_data("Abu Dhabi Grand Prix", "Yas Island", 2025, 24, "R", driver="GAS")
    summary = "GAS: 0 of 820 scored corners flagged; lap 5, turn 8: 0.37 s lost."
    ctx = context(R=AZERBAIJAN)
    ctx.mode, ctx.model, ctx.prompt_version = mode, model, "abc123def456"
    state = ConversationState(ctx.evidence)
    good = turn(
        ok("find_mistakes", {"event": "yas"}, summary, data), answer="No mistakes: 0 of 820."
    )
    bad = turn(failed("explain_corner", {"lap": 5}), answer="He lost 0.73 s at turn 8.")
    for t in (good, bad):
        t.done = {"model": model, "cost_usd": 0.01, "signature": "abc123def456.x"}
    results = [state.judge(q1, good, ctx, 1), state.judge(q2, bad, ctx, 1)]
    return EvalRun(ctx, results, datetime(2026, 10, 4, 3, 0, tzinfo=UTC), 1.5, 1)


def test_the_report_renders_from_a_synthetic_run(tmp_path: Path) -> None:
    run = synthetic_run()
    assert run.scripted
    text = report.render(run)
    first = text.splitlines()[0]
    assert first.startswith("_Generated by make chat-eval on 2026-10-04 at ")
    assert "**Scripted run.**" in text
    row = "| T1 | Q? | find_mistakes | find_mistakes | 2/2 | 2: 2 / 0 / 0 / 0 | "
    assert row + "nothing_flagged: ok | yes | 0.0 | 0.0100 |" in text
    assert "- Passed every check: **1 of 2**" in text
    assert "- T1 **0.73**: He lost 0.73 s at turn 8." in text
    assert "explain_corner called but every call was an error" in text
    md, js = report.paths_for(run, tmp_path)
    assert md.name.startswith("chat_eval-fake-20261004-") and md.parent == tmp_path
    report.write(run, md, js)
    record = json.loads(js.read_text())
    assert record["scripted"] is True
    assert record["results"][1]["grounding"]["counts"]["ungrounded"] == 1
    assert CHART_MARKER not in js.read_text()  # chart data never leaves the run


def test_the_report_lists_a_field_the_call_left_out() -> None:
    run = synthetic_run()
    whole_field = session_data("Abu Dhabi Grand Prix", "Yas Island", 2025, 24, "R", driver=None)
    first = run.results[0]
    first.turn.calls[0].data = whole_field  # find_mistakes for every driver, not GAS
    state = ConversationState(run.context.evidence)
    run.results[0] = state.judge(first.question, first.turn, run.context, 1)
    text = report.render(run)
    assert "| 1/2 (driver None) |" in text
    assert "- T1: find_mistakes driver expected GAS, got None." in text


def test_a_scripted_run_is_never_written_into_report(tmp_path: Path) -> None:
    scripted = synthetic_run()
    with pytest.raises(report.RefusedWrite):
        report.write(scripted, report.REPORT_DIR / "chat.md", tmp_path / "chat.json")
    folder = tmp_path / "report"
    with pytest.raises(report.RefusedWrite):
        report.write(scripted, tmp_path / "a.md", folder / "sub" / "a.json", report_dir=folder)
    assert not folder.exists() and not (tmp_path / "a.md").exists()
    real = synthetic_run("anthropic", "claude-sonnet-5-5")
    assert not real.scripted
    assert report.paths_for(real, tmp_path)[0] == report.REPORT_FILE
    report.write(real, folder / "m7.md", tmp_path / "real.json", report_dir=folder)
    assert "**Scripted run.**" not in (folder / "m7.md").read_text()
    # A scripted answer in an otherwise real run makes the whole run scripted.
    real.results[0].turn.done = {"model": "scripted"}
    assert real.scripted


def test_report_is_refused_by_any_other_name(tmp_path: Path) -> None:
    scripted = synthetic_run()
    folder = tmp_path / "report"
    folder.mkdir()
    (folder / "m7.md").write_text("placeholder\n")
    (tmp_path / "link").symlink_to(folder)
    (tmp_path / "hard.md").hardlink_to(folder / "m7.md")
    other = [tmp_path / "link" / "a.md", tmp_path / "hard.md", tmp_path / "x" / ".." / "report"]
    if (tmp_path / "REPORT").exists():  # a case-insensitive disk, as on macOS
        other.append(tmp_path / "REPORT" / "sub" / "a.md")
    for path in other:
        with pytest.raises(report.RefusedWrite):
            report.write(scripted, path, tmp_path / "a.json", report_dir=folder)
    assert (folder / "m7.md").read_text() == "placeholder\n"
    assert sorted(p.name for p in folder.iterdir()) == ["m7.md"]


# ---- A running API ----


class CutStream(httpx2.SyncByteStream):
    """An answer whose connection drops in the middle of an event."""

    def __iter__(self) -> Iterator[bytes]:
        yield b'event: text\ndata: {"delta": "Checking"}\n\nevent: tool_call\n'
        raise httpx2.ReadError("connection reset")


def test_a_lost_connection_is_a_problem_not_a_crash() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/chat":
            return httpx2.Response(200, stream=CutStream())
        raise httpx2.ConnectError("connection refused")

    http = httpx2.Client(transport=httpx2.MockTransport(answer), base_url="http://api.test")
    api = client._Remote(http, "http://api.test")
    status, events, _ = api.chat({"message": "Q?"})
    got = turn_from_events("Q?", status, events, {}, 0.1)
    assert got.answer == "Checking"  # the unfinished tool_call event is left out
    assert got.problem == f"stream error {client.CONNECTION_LOST}"
    with pytest.raises(client.ChatUnavailable, match=r"can't reach http://api\.test"):
        api.get("/api/health")


def test_a_refused_key_stops_the_run_before_anything_is_written(
    paths: DataPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The server's Anthropic key refused (401 upstream): every answer would be chat_not_configured,
    # so the run stops at the first one and main() says "Not run" (exit 2), writing nothing.
    settings = Settings(processed=paths.processed, results=paths.results, chat="auto")
    with client.in_process(settings) as api:
        monkeypatch.setattr(api, "chat", lambda body: (200, [], {}))
        monkeypatch.setattr(
            client,
            "turn_from_events",
            lambda q, *a: Turn(q, 200, stream_errors=[{"code": "chat_not_configured"}]),
        )
        with pytest.raises(client.ChatUnavailable, match="answered chat_not_configured"):
            client.run(api, load(), log=lambda line: None)
    assert not any(tmp_path.rglob("*"))


# ---- A whole run ----


def test_a_whole_scripted_run_on_synthetic_data(
    paths: DataPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Whatever the environment and the settings ask for, the in-process chat is the scripted one,
    # with the limits off (on, 10 an hour would refuse questions 11-15).
    monkeypatch.setenv("RACE_ENGINEER_CHAT", "auto")
    settings = Settings(processed=paths.processed, results=paths.results, chat="auto", limits="on")
    before = report.REPORT_FILE.stat().st_mtime_ns if report.REPORT_FILE.exists() else None
    lines: list[str] = []
    with client.in_process(settings) as api:
        run = client.run(api, load(), log=lines.append)
    assert run.scripted and run.models == ["scripted"]
    assert len(run.results) == 15 and len(lines) == 16
    assert run.problems == []  # every answer streamed to its done event, tool errors and all
    assert any(c.is_error for r in run.results for c in r.turn.calls)  # no M4 results here
    assert run.context.prompt_version
    md, js = report.paths_for(run, tmp_path / "evals")
    report.write(run, md, js)
    text = md.read_text()
    b9 = "| B9 | Did Leclerc make any mistakes in 2025 Monza qualifying? | find_mistakes; not "
    assert b9 + "explain_corner |" in text
    assert "| B14 | Who will win the 2027 championship? | no tool |" in text  # "*"
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == [
        Path("evals"),
        Path("evals") / js.name,
        Path("evals") / md.name,
    ]
    after = report.REPORT_FILE.stat().st_mtime_ns if report.REPORT_FILE.exists() else None
    assert after == before
