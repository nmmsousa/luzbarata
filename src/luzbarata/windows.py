"""Find the cheapest (or most expensive) block of consecutive time to run something."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import polars as pl

SLOT_MINUTES = 15


@dataclass(frozen=True)
class Window:
    start_utc: datetime
    end_utc: datetime
    avg_price: float


def best_window(prices: pl.DataFrame, minutes: int, cheapest: bool = True) -> Window | None:
    """Block of `minutes` with the lowest (or highest) average `price_eur_kwh`.

    Only consecutive slots count, so a gap in the data splits the day. Returns None when
    no run of slots is long enough. On a tie the earliest block wins.
    """
    if minutes <= 0 or minutes % SLOT_MINUTES:
        raise ValueError(f"Duration must be a positive multiple of {SLOT_MINUTES} minutes")
    n = minutes // SLOT_MINUTES
    prices = prices.sort("start_utc")
    starts = prices["start_utc"].to_list()
    ends = prices["end_utc"].to_list()
    values = prices["price_eur_kwh"].to_list()

    best: Window | None = None
    for i in range(len(values) - n + 1):
        if any(ends[j] != starts[j + 1] for j in range(i, i + n - 1)):
            continue
        # Rounding stops float noise from deciding between equally priced blocks.
        avg = round(sum(values[i : i + n]) / n, 9)
        if best is None or (avg < best.avg_price if cheapest else avg > best.avg_price):
            best = Window(starts[i], ends[i + n - 1], avg)
    return best
