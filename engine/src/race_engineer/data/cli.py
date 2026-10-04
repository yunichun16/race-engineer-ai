"""Command line for the data pipeline.

uv run race-engineer-data catalog --years 2025 2026
uv run race-engineer-data build --years 2024 2025 --sessions Q R --limit 5
uv run race-engineer-data qa
uv run race-engineer-data circuits [--only-missing]
"""

from __future__ import annotations

import argparse
import logging

from race_engineer.config import SESSION_CODES, YEARS


def main() -> None:
    parser = argparse.ArgumentParser(prog="race-engineer-data")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_selection(p: argparse.ArgumentParser) -> None:
        p.add_argument("--years", type=int, nargs="+", default=list(YEARS))
        p.add_argument("--rounds", type=int, nargs="+", default=None)
        p.add_argument(
            "--sessions", nargs="+", choices=sorted(set(SESSION_CODES.values())), default=None
        )
        p.add_argument("--limit", type=int, default=None, help="process at most N sessions")

    add_selection(sub.add_parser("catalog", help="list completed sessions"))
    build_p = sub.add_parser("build", help="download and process sessions")
    add_selection(build_p)
    build_p.add_argument("--force", action="store_true", help="reprocess finished sessions")
    build_p.add_argument(
        "--reprocess",
        action="store_true",
        help="only redo sessions already processed, offline from FastF1's cache (no API calls)",
    )
    build_p.add_argument(
        "--drop-cache", action="store_true", help="delete FastF1's raw download after each session"
    )
    build_p.add_argument(
        "--pace", type=float, default=110.0, help="seconds between downloaded sessions (API limits)"
    )
    sub.add_parser("qa", help="write the data quality report")
    circuits_p = sub.add_parser(
        "circuits", help="write each circuit's map rotation (processed/circuits.json)"
    )
    circuits_p.add_argument(
        "--only-missing", action="store_true", help="skip rounds already in the file"
    )

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )
    import fastf1

    fastf1.set_log_level("ERROR")

    if args.command in ("catalog", "build"):
        from race_engineer.config import FASTF1_CACHE
        from race_engineer.data.catalog import completed_sessions

        FASTF1_CACHE.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(FASTF1_CACHE))
        fastf1.Cache.offline_mode(getattr(args, "reprocess", False))
        codes = set(args.sessions) if args.sessions else None
        refs = [ref for year in args.years for ref in completed_sessions(year, codes)]
        if args.rounds:
            refs = [ref for ref in refs if ref.round in args.rounds]
        refs = refs[: args.limit] if args.limit else refs
        if args.command == "catalog":
            for ref in refs:
                when = f"{ref.date_utc:%Y-%m-%d}"
                print(f"{ref.year} R{ref.round:02d} {ref.code:<2} {ref.event} ({when})")
            print(f"{len(refs)} sessions")
            return

        from race_engineer.data.build import build
        from race_engineer.data.store import session_key, table_path

        if args.reprocess:
            refs = [
                r
                for r in refs
                if table_path("sessions", session_key(r.year, r.round, r.code)).exists()
            ]
        force = args.force or args.reprocess
        build(
            refs,
            force=force,
            drop_cache=args.drop_cache,
            pace_s=args.pace,
            offline=args.reprocess,
        )
    elif args.command == "qa":
        from race_engineer.data.qa import write_report

        print(f"wrote {write_report()}")
    elif args.command == "circuits":
        from race_engineer.data.circuits import build_circuits, circuits_path

        circuits = build_circuits(only_missing=args.only_missing)
        north_up = sorted(k for k, c in circuits.items() if c["source"] == "default")
        print(f"wrote {circuits_path()}: {len(circuits)} rounds, {len(north_up)} north up")
        if north_up:
            print("no circuit info in FastF1's cache: " + ", ".join(north_up))


if __name__ == "__main__":
    main()
