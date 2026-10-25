"""Parquet price store: one file per month, rewritten atomically on each upsert.

Layout: <data_dir>/prices/year=YYYY/YYYY-MM.parquet
Saving a day replaces any rows already stored for it, so re-running is safe.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from luzbarata.omie import PricePeriod
from luzbarata.tariffs import local_day_bounds

SCHEMA = {
    "delivery_date": pl.Date,
    "period": pl.Int16,
    "resolution_minutes": pl.Int16,
    "start_utc": pl.Datetime("us", "UTC"),
    "end_utc": pl.Datetime("us", "UTC"),
    "price_eur_mwh": pl.Float64,
    "ingested_at_utc": pl.Datetime("us", "UTC"),
}


def month_path(data_dir: Path, d: date) -> Path:
    return data_dir / "prices" / f"year={d.year}" / f"{d:%Y-%m}.parquet"


def save_day(periods: list[PricePeriod], data_dir: Path) -> Path:
    if not periods:
        raise ValueError("Nothing to save")
    d = periods[0].delivery_date
    now = datetime.now(UTC)
    new = pl.DataFrame([asdict(p) | {"ingested_at_utc": now} for p in periods], schema=SCHEMA)

    path = month_path(data_dir, d)
    if path.exists():
        old = pl.read_parquet(path).filter(pl.col("delivery_date") != d)
        new = pl.concat([old, new])
    new = new.sort("delivery_date", "period")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    new.write_parquet(tmp)
    tmp.replace(path)
    return path


def load_day(d: date, data_dir: Path) -> pl.DataFrame:
    path = month_path(data_dir, d)
    if not path.exists():
        return pl.DataFrame(schema=SCHEMA)
    return pl.read_parquet(path).filter(pl.col("delivery_date") == d)


def has_day(d: date, data_dir: Path) -> bool:
    return load_day(d, data_dir).height > 0


def stored_days(data_dir: Path) -> list[date]:
    """Every delivery day in the store, oldest first."""
    files = sorted((data_dir / "prices").glob("year=*/*.parquet"))
    if not files:
        return []
    days = pl.concat([pl.read_parquet(f, columns=["delivery_date"]) for f in files])
    return sorted(days["delivery_date"].unique().to_list())


def load_local_day(d: date, data_dir: Path) -> pl.DataFrame:
    """Stored OMIE prices covering calendar day `d` in Lisbon.

    A Lisbon day spans two delivery days: delivery day `d` starts at 23:00 the evening
    before, and 23:00-24:00 belongs to delivery day `d + 1`.
    """
    start, end = local_day_bounds(d)
    both = pl.concat([load_day(d, data_dir), load_day(d + timedelta(days=1), data_dir)])
    return both.filter((pl.col("start_utc") >= start) & (pl.col("start_utc") < end))
