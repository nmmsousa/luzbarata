"""Parquet price store: one file per month, rewritten atomically on each upsert.

Layout: <data_dir>/prices/year=YYYY/YYYY-MM.parquet
Saving a day replaces any rows already stored for it, so re-running is safe.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from luzbarata.omie import PricePeriod

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
