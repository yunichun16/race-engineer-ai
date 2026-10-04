import importlib.util
import json
import math
from pathlib import Path

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_queue.py"
spec = importlib.util.spec_from_file_location("review_queue", SCRIPT)
assert spec is not None and spec.loader is not None
rq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rq)


def mistakes() -> pd.DataFrame:
    rows = []
    for rnd, session, driver, priority, kind in [
        (1, "R", "AAA", 9.0, "late_throttle"),
        (1, "R", "AAA", 8.0, "over_slowing"),  # same driver and session: skipped
        (1, "R", "BBB", 7.0, "over_slowing"),
        (1, "R", "CCC", 6.0, "early_braking"),  # third in the session: skipped
        (2, "Q", "AAA", 5.0, "lift_and_coast"),  # not a listed mistake
        (2, "Q", "DDD", 4.0, "track_limits"),
    ]:
        sid = f"2026_{rnd:02d}_{session}_{driver}_{int(priority)}"
        rows.append(
            {"year": 2026, "round": rnd, "session": session, "driver": driver,
             "priority": priority, "mistake_type": kind, "segment_id": sid, "merged_into": sid}
        )  # fmt: skip
    return pd.DataFrame(rows)


def test_select_follows_the_ranking_and_the_caps() -> None:
    picked = rq.select(mistakes(), 10)
    assert picked["driver"].tolist() == ["AAA", "BBB", "DDD"]
    assert rq.select(mistakes(), 2)["driver"].tolist() == ["AAA", "BBB"]


def test_wilson_stays_within_zero_and_one() -> None:
    lo, hi = rq.wilson(0, 5)
    assert lo == 0.0 and 0 < hi < 1
    lo, hi = rq.wilson(5, 5)
    assert hi == 1.0 and 0 < lo < 1
    assert all(math.isnan(v) for v in rq.wilson(0, 0))


def test_cohen_kappa() -> None:
    assert rq.cohen_kappa(["real", "not"], ["real", "not"]) == pytest.approx(1.0)
    assert rq.cohen_kappa(["real", "real", "not", "not"], ["real", "not", "real", "not"]) == 0.0


def test_qualifying_race_control_lap_count_is_dropped() -> None:
    text = "TRACK LIMITS AT TURN 7 LAP 3"
    assert rq.race_control_text(text, is_race=False) == "TRACK LIMITS AT TURN 7"
    assert rq.race_control_text(text, is_race=True) == text


def queue_file(tmp_path: Path, n: int = 4) -> None:
    findings = [
        {"id": f"f{i}", "rank": i + 1, "session": "R", "ref_kind": "driver",
         "type_label": "over-slowing", "inspected": i == 0, "driver": "AAA",
         "event": "E", "lap": i, "corner_label": "1"}
        for i in range(n)
    ]  # fmt: skip
    (tmp_path / "queue.json").write_text(json.dumps({"distance_m": [], "findings": findings}))


def test_score_handles_missing_and_odd_answers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rq, "REVIEW_DIR", tmp_path)
    queue_file(tmp_path)
    labels = {"f0": {"verdict": "real", "note": None}, "f1": {"verdict": "not"}, "f2": None}
    (tmp_path / "labels.json").write_text(json.dumps(labels))
    second = {"f0": {"verdict": "real"}, "f1": {"verdict": ""}, "f3": None}
    (tmp_path / "second.json").write_text(json.dumps(second))
    out = rq.score(tmp_path / "labels.json", tmp_path / "second.json", tmp_path / "r.md")
    text = out.read_text()
    assert "**2 of 4 reviewed.**" in text
    assert "agreed on 100% of 1 findings" in text  # f1's empty verdict is not an answer
    assert "None" not in text and "nan" not in text
    assert "not inspected while building the rules | 1 | 0 | 1" in text


def test_score_with_no_answers_says_so(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rq, "REVIEW_DIR", tmp_path)
    queue_file(tmp_path)
    (tmp_path / "labels.json").write_text("{}")
    text = rq.score(tmp_path / "labels.json", None, tmp_path / "r.md").read_text()
    assert "**0 of 4 reviewed.**" in text and "|  |" not in text
