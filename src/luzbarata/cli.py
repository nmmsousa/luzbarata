"""Command line entry point: `luzbarata fetch | backfill | show | windows | post`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

from luzbarata import omie, storage, tariffs, telegram, windows
from luzbarata.omie import LOCAL_TZ

log = logging.getLogger("luzbarata")

# Exit code for "not published yet", so a scheduler can tell it apart from a failure.
EXIT_NOT_AVAILABLE = 2

OPTION_NAMES = {"simple": "simple", "bi": "bi-hourly", "tri": "tri-hourly"}

# What the public Telegram channel shows: section title -> (option, cycle).
CHANNEL_PROVIDER = "coopernico-base"
CHANNEL_TARIFFS: dict[str, tuple[tariffs.Option, tariffs.Cycle]] = {
    "Tarifa simples": ("simple", "daily"),
    "Bi-horária (ciclo diário)": ("bi", "daily"),
}


def tomorrow(tz=omie.MARKET_TZ) -> date:
    return datetime.now(tz).date() + timedelta(days=1)


def load_local_day(d: date, data_dir: Path) -> pl.DataFrame:
    """Stored OMIE prices covering calendar day `d` in Lisbon.

    A Lisbon day spans two delivery days: delivery day `d` starts at 23:00 the evening
    before, and 23:00-24:00 belongs to delivery day `d + 1`.
    """
    start, end = tariffs.local_day_bounds(d)
    both = pl.concat(
        [storage.load_day(d, data_dir), storage.load_day(d + timedelta(days=1), data_dir)]
    )
    return both.filter((pl.col("start_utc") >= start) & (pl.col("start_utc") < end))


def day_summary(
    d: date,
    provider: tariffs.Provider,
    option: tariffs.Option,
    cycle: tariffs.Cycle,
    data_dir: Path,
) -> windows.DaySummary | None:
    """Cheapest windows on Lisbon day `d`, or None if no prices are stored for it."""
    omie_prices = load_local_day(d, data_dir)
    if omie_prices.is_empty():
        return None
    prices = tariffs.final_prices(omie_prices, provider, option, cycle)
    start, end = tariffs.local_day_bounds(d)
    expected = int((end - start).total_seconds()) // (windows.SLOT_MINUTES * 60)
    return windows.summarise(prices, expected)


def cmd_fetch(args: argparse.Namespace) -> int:
    d = args.date or tomorrow()
    if not args.force and storage.has_day(d, args.data_dir):
        log.info("Prices for %s already stored", d)
        return 0
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


def cmd_windows(args: argparse.Namespace) -> int:
    d = args.date or tomorrow(LOCAL_TZ)
    provider = tariffs.providers()[args.provider]
    s = day_summary(d, provider, args.option, args.cycle, args.data_dir)
    if s is None:
        print(f"No prices stored for {d}. Run: luzbarata fetch --date {d}")
        return 1

    def span(w: windows.Window) -> str:
        return f"{w.start_utc.astimezone(LOCAL_TZ):%H:%M}-{w.end_utc.astimezone(LOCAL_TZ):%H:%M}"

    tariff = OPTION_NAMES[args.option]
    if args.option != "simple":
        tariff += f", {args.cycle} cycle"
    print(f"{provider.name} · {tariff} · {d:%a %d %b %Y} (Lisbon time)")
    print(f"EUR/kWh without VAT · {s.slots}/{s.expected_slots} slots known\n")
    print(f"{'Day average':<20}{'':13}{s.avg_price:.4f}")
    for hours, w in s.cheapest.items():
        saving = w.avg_price / s.avg_price - 1
        print(f"{f'Cheapest {hours}h':<20}{span(w):<13}{w.avg_price:.4f}  ({saving:+.0%})")
    if s.priciest:
        label = f"Most expensive {windows.PRICIEST_HOURS}h"
        print(f"{label:<20}{span(s.priciest):<13}{s.priciest.avg_price:.4f}")
    if s.slots < s.expected_slots:
        print("\nSlots not published yet (23:00-24:00 comes with the next day) are left out.")
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    d = args.date or tomorrow(LOCAL_TZ)
    if not args.dry_run and telegram.was_posted(d, args.data_dir):
        log.info("Message for %s already posted", d)
        return 0
    # Delivery day d covers 00:00-23:00 Lisbon; without it there is nothing worth posting.
    if not storage.has_day(d, args.data_dir):
        log.warning("No prices stored for %s yet", d)
        return EXIT_NOT_AVAILABLE

    provider = tariffs.providers()[CHANNEL_PROVIDER]
    sections = {
        title: day_summary(d, provider, option, cycle, args.data_dir)
        for title, (option, cycle) in CHANNEL_TARIFFS.items()
    }
    text = telegram.format_message(d, provider.name, sections)
    if args.dry_run:
        print(text)
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to post")
        return 1
    try:
        message_id = telegram.send_message(token, chat_id, text)
    except telegram.TelegramError as exc:
        log.error("%s", exc)
        return 1
    telegram.record_post(d, message_id, args.data_dir)
    log.info("Posted message %d for %s", message_id, d)
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
    fetch.add_argument("--force", action="store_true", help="Re-download if already stored")
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

    win = sub.add_parser(
        "windows", help="Cheapest times to use electricity on a Lisbon day (default: tomorrow)"
    )
    win.add_argument("--date", type=date.fromisoformat)
    win.add_argument("--provider", choices=sorted(tariffs.providers()), default="coopernico-base")
    win.add_argument("--option", choices=tariffs.OPTIONS, default="simple")
    win.add_argument("--cycle", choices=tariffs.CYCLES, default="daily")
    win.set_defaults(func=cmd_windows)

    post = sub.add_parser(
        "post", help="Post a Lisbon day's cheapest hours to Telegram, once (default: tomorrow)"
    )
    post.add_argument("--date", type=date.fromisoformat)
    post.add_argument("--dry-run", action="store_true", help="Print the message, don't post")
    post.set_defaults(func=cmd_post)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
