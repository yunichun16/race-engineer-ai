"""Data quality report for the processed dataset (report/data_quality.md)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from race_engineer.config import PROCESSED_DIR, REPORT_DIR
from race_engineer.data.store import connect

CLEAN = "('push', 'race')"


PLAIN_COLUMNS = {"year", "round"}  # identifiers: no thousands separators


def markdown_table(df: pd.DataFrame) -> str:
    def fmt(column: str, v: object) -> str:
        if column in PLAIN_COLUMNS:
            return str(v)
        if isinstance(v, float):
            return f"{v:,.3g}" if abs(v) < 1000 else f"{v:,.0f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    columns = [str(c) for c in df.columns]
    head = "| " + " | ".join(columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    rows = [
        "| " + " | ".join(fmt(c, v) for c, v in zip(columns, row, strict=True)) + " |"
        for row in df.itertuples(index=False)
    ]
    return "\n".join([head, rule, *rows])


def failed_sessions(root: Path) -> pd.DataFrame:
    manifest = root / "manifest.jsonl"
    if not manifest.exists():
        return pd.DataFrame()
    records = [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
    latest = pd.DataFrame(records).drop_duplicates("key", keep="last")
    failed = latest[latest["status"] == "failed"]
    return failed[["key", "event", "session", "error"]] if not failed.empty else failed


def write_report(root: Path = PROCESSED_DIR, out: Path = REPORT_DIR / "data_quality.md") -> Path:
    con = connect(root)
    q = lambda sql: con.sql(sql).df()  # noqa: E731

    sessions = q(
        "select year, session, count(*) sessions, sum(n_laps) laps, sum(n_grid_ok) grids,"
        " sum(n_segments) segments, sum(n_track_limit_events) track_limits"
        " from sessions group by all order by year, session"
    )
    totals = q(
        "select count(*) sessions, sum(n_laps) laps, sum(n_grid_ok) grids,"
        " sum(n_segments) segments,"
        " sum(skipped_windows) skipped_windows, sum(invalid_windows) invalid_windows"
        " from sessions"
    ).iloc[0]
    classes = q(
        "select lap_class, count(*) laps, round(avg(grid_ok::int), 3) grid_ok_share"
        " from laps group by all order by laps desc"
    )
    reasons = q(
        "select grid_reason reason, count(*) laps from laps where not grid_ok"
        " group by all order by laps desc"
    )
    alignment = q(
        f"select round(median(p95_offset_m), 2) median_p95_offset_m,"
        f" round(quantile_cont(p95_offset_m, 0.95), 2) p95_p95_offset_m,"
        f" round(median(max_gap_m), 1) median_max_gap_m,"
        f" round(quantile_cont(max_gap_m, 0.95), 1) p95_max_gap_m,"
        f" round(median(max_fix_gap_m), 1) median_max_fix_gap_m,"
        f" round(quantile_cont(max_fix_gap_m, 0.95), 1) p95_max_fix_gap_m"
        f" from laps where grid_ok and lap_class in {CLEAN}"
    )
    timing = q(
        f"select count(*) laps,"
        f" round(median(abs(g.time_s[len(g.time_s)] - l.lap_time_s)), 3) median_abs_s,"
        f" round(quantile_cont(abs(g.time_s[len(g.time_s)] - l.lap_time_s), 0.99), 3) p99_abs_s"
        f" from lap_grids g join laps l using (lap_id) where l.lap_class in {CLEAN}"
    )
    segments = q(
        f"select session, count(*) segments, sum((lap_class in {CLEAN})::int) clean,"
        f" sum((stitched <> '')::int) stitched from segments group by all order by session"
    )
    labels = q(
        "with tl as (select * from events where kind = 'track_limits'),"
        " seg as (select distinct year, round, session, driver, lap_number, corner from segments)"
        " select coalesce(l.lap_class, 'unknown') lap_class, count(*) track_limit_events,"
        " sum((seg.corner is not null)::int) matched_to_a_segment,"
        " sum((tl.drivers_same_lap_turn >= 3)::int) in_clusters_of_3_plus"
        " from tl left join seg on tl.year = seg.year and tl.round = seg.round"
        " and tl.session = seg.session and tl.driver = seg.driver"
        " and tl.lap_number = seg.lap_number and tl.turn = seg.corner"
        " left join laps l on tl.year = l.year and tl.round = l.round and tl.session = l.session"
        " and tl.driver = l.driver and tl.lap_number = l.lap_number"
        " group by all order by track_limit_events desc"
    )
    incidents = q("select count(*) incidents from events where kind = 'incident'")
    invalid = q(
        "select l.year, l.round, l.session, s.event,"
        " sum((l.frozen_share > 0)::int) laps_frozen,"
        " sum((l.gap_share > 0)::int) laps_car_gaps,"
        " sum((l.sparse_share > 0)::int) laps_position_gaps,"
        " sum((l.mismatch_share > 0)::int) laps_speed_mismatch,"
        " round(avg(greatest(l.frozen_share, l.gap_share, l.sparse_share, l.mismatch_share)), 3)"
        " mean_share,"
        " max(s.invalid_windows) windows_dropped"
        " from laps l join sessions s using (year, round, session)"
        " where l.frozen_share is not null group by all having max(s.invalid_windows) > 0"
        " order by windows_dropped desc limit 12"
    )
    drs = q(
        "select year, round(avg(case when list_max(drs) > 0 then 1 else 0 end), 3) laps_with_drs"
        " from lap_grids group by year order by year"
    )
    failed = failed_sessions(root)

    parts = [
        "# Data quality report",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `race-engineer-data qa`._",
        "## Coverage",
        f"{int(totals['sessions'])} sessions, {int(totals['laps']):,} laps, "
        f"{int(totals['grids']):,} lap grids, {int(totals['segments']):,} corner segments. "
        f"{int(totals['skipped_windows']):,} corner windows skipped "
        "(the neighbouring lap had no grid) and "
        f"{int(totals['invalid_windows']):,} dropped for touching invalid data (below).",
        markdown_table(sessions),
        "## Failed sessions",
        markdown_table(failed) if not failed.empty else "None.",
        "## Laps by class",
        "Only `push` (qualifying) and `race` laps are used for modelling.",
        markdown_table(classes),
        "### Why laps have no grid",
        markdown_table(reasons),
        "## Alignment quality (clean laps)",
        "Distance from the reference line (p95 per lap), the largest gap between car-data "
        "samples and the largest gap between position fixes.",
        markdown_table(alignment),
        "Grid time at the end of the lap vs the official lap time:",
        markdown_table(timing),
        "## Corner segments",
        markdown_table(segments),
        "## Weak labels",
        "Track-limits labels by the class of the lap they fall on. Only labels on `push` and "
        "`race` laps that match a segment are used to evaluate mistake detection.",
        markdown_table(labels),
        f"Driving-related incidents (lap-level, approximate): {int(incidents.to_numpy()[0, 0]):,}.",
        "## Frozen feeds and data gaps",
        "The live-timing feeds sometimes fail while the car keeps moving. The car feed can "
        "freeze (speed, RPM, throttle and gear repeat exactly) or drop samples; the position "
        "feed can stall, repeating the last fix. Between position fixes, distance comes from "
        "integrated speed, so a short stall costs nothing. The speed channel can also stick on "
        "one value while the car moves on. Stretches that are frozen, longer than 120 m between "
        "car samples or between position fixes, or where distance from speed and from position "
        "fixes differs by more than 40% over 1.5 s, are marked invalid: corner windows touching "
        "them are dropped, and laps more than 30% affected by any one of them get no grid. "
        "Sessions with the most windows dropped (`mean_share`: the average share of a lap "
        "affected by the worst of the four):",
        markdown_table(invalid) if not invalid.empty else "None.",
        "## DRS signal by season",
        "DRS was removed for 2026, so the channel should be empty that year.",
        markdown_table(drs),
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(parts) + "\n")
    return out
