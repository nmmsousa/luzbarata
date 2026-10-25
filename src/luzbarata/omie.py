"""Download, parse and validate OMIE day-ahead marginal prices for Portugal.

OMIE publishes one file per delivery day. Delivery days and period numbers are in
Spanish local time (Europe/Madrid), so period 1 starts at 23:00 Lisbon time on the
previous day. Files are hourly up to 2025-09-30 and 15-minute from 2025-10-01, and
daylight-saving days have one hour less or more (23/25 or 92/100 periods).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("Europe/Madrid")
URL = (
    "https://www.omie.es/es/file-download"
    "?parents%5B0%5D=marginalpdbcpt&filename=marginalpdbcpt_{d:%Y%m%d}.1"
)
HEADER = "MARGINALPDBCPT;"

# Harmonised SDAC price limits, in EUR/MWh.
MIN_PRICE = -500.0
MAX_PRICE = 4000.0


class DataNotAvailable(Exception):
    """OMIE has not published (or does not have) prices for this day."""


class InvalidFile(Exception):
    """The file was downloaded but its contents fail validation."""


@dataclass(frozen=True)
class PricePeriod:
    delivery_date: date
    period: int
    resolution_minutes: int
    start_utc: datetime
    end_utc: datetime
    price_eur_mwh: float


def market_day_bounds(d: date) -> tuple[datetime, datetime]:
    """UTC start and end of a delivery day, which is defined in Madrid time."""
    start = datetime(d.year, d.month, d.day, tzinfo=MARKET_TZ)
    nxt = d + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=MARKET_TZ)
    return start.astimezone(UTC), end.astimezone(UTC)


def download(
    d: date,
    client: httpx.Client | None = None,
    retries: int = 3,
    backoff_seconds: float = 5.0,
) -> str:
    """Return the raw file for delivery day `d`.

    Raises DataNotAvailable for days that are not published yet: OMIE answers those
    with a 404, or sometimes with its HTML page instead of a file.
    """
    url = URL.format(d=d)
    own_client = client is None
    if client is None:
        client = httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": "luzbarata/0.1"}
        )
    try:
        for attempt in range(1, retries + 1):
            try:
                response = client.get(url)
                if response.status_code == 404:
                    raise DataNotAvailable(f"OMIE has no Portuguese prices for {d}")
                response.raise_for_status()
                break
            except httpx.HTTPError as exc:
                if attempt == retries:
                    raise
                log.warning("Download of %s failed (%s), retry %d/%d", d, exc, attempt, retries)
                time.sleep(backoff_seconds * attempt)
    finally:
        if own_client:
            client.close()

    text = response.content.decode("latin-1")
    if not text.startswith(HEADER):
        raise DataNotAvailable(f"OMIE has no Portuguese prices for {d}")
    return text


def parse(text: str, d: date) -> list[PricePeriod]:
    """Parse and validate a marginalpdbcpt file for delivery day `d`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != HEADER:
        raise InvalidFile(f"Unexpected header: {lines[0][:40] if lines else '<empty>'!r}")

    rows: dict[int, float] = {}
    for line in lines[1:]:
        line = line.strip()
        if not line or line == "*":
            continue
        fields = line.split(";")
        if len(fields) < 5:
            raise InvalidFile(f"Malformed line: {line!r}")
        row_date = date(int(fields[0]), int(fields[1]), int(fields[2]))
        if row_date != d:
            raise InvalidFile(f"File for {d} contains a row for {row_date}")
        period, price = int(fields[3]), float(fields[4])
        if period in rows:
            raise InvalidFile(f"Duplicate period {period}")
        if not MIN_PRICE <= price <= MAX_PRICE:
            raise InvalidFile(f"Period {period} price {price} outside [{MIN_PRICE}, {MAX_PRICE}]")
        rows[period] = price

    start, end = market_day_bounds(d)
    day_minutes = int((end - start).total_seconds()) // 60
    n = len(rows)
    if n == 0 or day_minutes % n or day_minutes // n not in (15, 60):
        raise InvalidFile(f"{n} periods do not fit a {day_minutes // 60}h day")
    if sorted(rows) != list(range(1, n + 1)):
        raise InvalidFile(f"Periods are not contiguous 1..{n}")

    resolution = day_minutes // n
    step = timedelta(minutes=resolution)
    return [
        PricePeriod(
            delivery_date=d,
            period=p,
            resolution_minutes=resolution,
            start_utc=start + step * (p - 1),
            end_utc=start + step * p,
            price_eur_mwh=rows[p],
        )
        for p in range(1, n + 1)
    ]


def fetch(d: date, client: httpx.Client | None = None) -> list[PricePeriod]:
    return parse(download(d, client=client), d)
