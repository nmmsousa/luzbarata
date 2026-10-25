from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from luzbarata.windows import best_window

T0 = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)


def slots(values: list[float], skip: set[int] = frozenset()) -> pl.DataFrame:
    """One 15-minute slot per value starting at T0; indexes in `skip` are left out."""
    rows = [
        {"start_utc": T0 + SLOT * i, "end_utc": T0 + SLOT * (i + 1), "price_eur_kwh": v}
        for i, v in enumerate(values)
        if i not in skip
    ]
    return pl.DataFrame(rows)


def test_cheapest_and_most_expensive():
    prices = slots([5, 4, 1, 1, 2, 9, 9, 3])

    cheap = best_window(prices, 30)
    assert (cheap.start_utc, cheap.end_utc, cheap.avg_price) == (T0 + SLOT * 2, T0 + SLOT * 4, 1)

    dear = best_window(prices, 30, cheapest=False)
    assert (dear.start_utc, dear.avg_price) == (T0 + SLOT * 5, 9)


def test_window_does_not_span_a_gap():
    # Slots 2 and 4 are cheapest, but slot 3 is missing so they are not consecutive.
    prices = slots([5, 5, 1, 0, 1, 5], skip={3})
    w = best_window(prices, 30)
    assert w.start_utc in (T0 + SLOT, T0 + SLOT * 4)
    assert w.avg_price == 3


def test_tie_goes_to_earliest():
    w = best_window(slots([2, 1, 1, 3, 1, 1]), 30)
    assert w.start_utc == T0 + SLOT


def test_float_noise_does_not_break_ties():
    w = best_window(slots([0.1, 0.2, 0.3, 0.3, 0.2, 0.1]), 45)
    assert w.start_utc == T0


def test_unsorted_input():
    prices = slots([3, 1, 1, 3]).reverse()
    assert best_window(prices, 30).start_utc == T0 + SLOT


def test_no_window_long_enough():
    assert best_window(slots([1, 2, 3]), 60) is None


@pytest.mark.parametrize("minutes", [0, -15, 20])
def test_invalid_duration(minutes):
    with pytest.raises(ValueError):
        best_window(slots([1, 2, 3, 4]), minutes)
