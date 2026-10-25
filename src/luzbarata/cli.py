"""Command line entry point: `luzbarata fetch | backfill | show`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from luzbarata import omie, storage

log = logging.getLogger("luzbarata")

LOCAL_TZ = ZoneInfo("Europe/Lisbon")

# Exit code for "not published yet", so a scheduler can tell it apart from a failure.
EXIT_NOT_AVAILABLE = 2


def tomorrow() -> date:
    return datetime.now(omie.MARKET_TZ).date() + timedelta(days=1)


def cmd_fetch(args: argparse.Namespace) -> int:
    d = args.date or tomorrow()
    try:
        periods = omie.fetch(d)
    except omie.DataNotAvailable as exc:
        log.warning("%s", exc)
        return EXIT_NOT_AVAILABLE
    path = storage.save_day(periods, args.data_dir)
    log.info("Saved %d periods for %s to %s", len(periods), d, path)
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    saved = skipped = missing = 0
    d = args.start
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        while d <= args.end:
            if not args.force and storage.has_day(d, args.data_dir):
                skipped += 1
            else:
                try:
                    storage.save_day(omie.fetch(d, client=client), args.data_dir)
                    saved += 1
                    log.info("Saved %s", d)
                except omie.DataNotAvailable as exc:
                    missing += 1
                    log.warning("%s", exc)
                time.sleep(args.delay)  # be polite to OMIE
            d += timedelta(days=1)
    log.info(
        "Backfill done: %d saved, %d already stored, %d not available", saved, skipped, missing
    )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    d = args.date or tomorrow()
    df = storage.load_day(d, args.data_dir)
    if df.is_empty():
        print(f"No prices stored for {d}. Run: luzbarata fetch --date {d}")
        return 1

    prices = df["price_eur_mwh"]
    lo, hi = prices.min(), prices.max()
    resolution = df["resolution_minutes"][0]
    print(f"OMIE Portugal prices for {d}, Lisbon time, {df.height} x {resolution} min")
    print(f"min {lo:.2f}  avg {prices.mean():.2f}  max {hi:.2f}  EUR/MWh\n")
    for row in df.iter_rows(named=True):
        start = row["start_utc"].astimezone(LOCAL_TZ)
        end = row["end_utc"].astimezone(LOCAL_TZ)
        price = row["price_eur_mwh"]
        bar = "#" * round(30 * (price - lo) / (hi - lo)) if hi > lo else ""
        flag = "  <- cheapest" if price == lo else "  <- most expensive" if price == hi else ""
        # The timezone name tells the two 01:00-02:00 hours apart when clocks go back.
        print(f"{start:%a %H:%M}-{end:%H:%M} {start:%Z}  {price:8.2f}  {bar}{flag}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="luzbarata", description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("LUZBARATA_DATA_DIR", "data")),
        help="Where the Parquet store lives (default: ./data or $LUZBARATA_DATA_DIR)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Download one delivery day (default: tomorrow)")
    fetch.add_argument("--date", type=date.fromisoformat)
    fetch.set_defaults(func=cmd_fetch)

    backfill = sub.add_parser("backfill", help="Download a range of days, skipping stored ones")
    backfill.add_argument("start", type=date.fromisoformat)
    backfill.add_argument("end", type=date.fromisoformat)
    backfill.add_argument("--force", action="store_true", help="Re-download stored days")
    backfill.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    backfill.set_defaults(func=cmd_backfill)

    show = sub.add_parser("show", help="Print a stored day in Lisbon time (default: tomorrow)")
    show.add_argument("--date", type=date.fromisoformat)
    show.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
