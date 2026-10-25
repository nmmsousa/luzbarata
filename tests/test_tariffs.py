from datetime import date, datetime, timedelta

import pytest

from luzbarata import omie, storage, tariffs
from luzbarata.omie import LOCAL_TZ


def lisbon(*args) -> datetime:
    return datetime(*args, tzinfo=LOCAL_TZ)


# 2026-01-14 is a winter Wednesday, 2026-07-15 a summer Wednesday,
# 2026-01-17 / 2026-07-18 Saturdays and 2026-01-18 a Sunday.
@pytest.mark.parametrize(
    ("moment", "cycle", "expected"),
    [
        (lisbon(2026, 1, 14, 7, 45), "daily", "vazio"),
        (lisbon(2026, 1, 14, 8, 0), "daily", "cheias"),
        (lisbon(2026, 1, 14, 9, 0), "daily", "ponta"),
        (lisbon(2026, 1, 14, 18, 0), "daily", "ponta"),
        (lisbon(2026, 1, 14, 20, 30), "daily", "cheias"),
        (lisbon(2026, 1, 14, 22, 0), "daily", "vazio"),
        (lisbon(2026, 7, 15, 9, 0), "daily", "cheias"),
        (lisbon(2026, 7, 15, 10, 30), "daily", "ponta"),
        (lisbon(2026, 7, 15, 20, 45), "daily", "ponta"),
        (lisbon(2026, 1, 14, 6, 45), "weekly", "vazio"),
        (lisbon(2026, 1, 14, 9, 15), "weekly", "cheias"),
        (lisbon(2026, 1, 14, 9, 30), "weekly", "ponta"),
        (lisbon(2026, 1, 14, 23, 45), "weekly", "cheias"),
        (lisbon(2026, 7, 15, 12, 0), "weekly", "ponta"),
        (lisbon(2026, 7, 15, 12, 15), "weekly", "cheias"),
        (lisbon(2026, 1, 17, 10, 0), "weekly", "cheias"),
        (lisbon(2026, 1, 17, 14, 0), "weekly", "vazio"),
        (lisbon(2026, 7, 18, 13, 45), "weekly", "cheias"),
        (lisbon(2026, 1, 18, 19, 0), "weekly", "vazio"),
    ],
)
def test_tri_period(moment, cycle, expected):
    assert tariffs.tri_period(moment, cycle) == expected


def test_tariff_period_by_option():
    peak = lisbon(2026, 1, 14, 9, 30)
    assert tariffs.tariff_period(peak, "simple", "daily") == "simples"
    assert tariffs.tariff_period(peak, "bi", "daily") == "fora_vazio"
    assert tariffs.tariff_period(peak, "tri", "daily") == "ponta"
    assert tariffs.tariff_period(lisbon(2026, 1, 14, 3, 0), "bi", "daily") == "vazio"


@pytest.mark.parametrize(
    ("option", "period", "published"),
    [
        ("simple", "simples", 0.1518),
        ("bi", "fora_vazio", 0.1746),
        ("bi", "vazio", 0.1069),
        ("tri", "ponta", 0.3363),
        ("tri", "cheias", 0.1323),
        ("tri", "vazio", 0.1069),
    ],
)
def test_coopernico_matches_published_prices(option, period, published):
    # Coopérnico's July 2026 table prices each option with an OMIE of 0.07025 EUR/kWh.
    provider = tariffs.providers()["coopernico-base"]
    tar = tariffs.access_tariff(date(2026, 7, 1)).prices[option][period]
    assert round(provider.energy(0.07025) + tar, 4) == published


def test_g9_counts_negative_omie_as_zero():
    g9 = tariffs.providers()["g9-smart-index"]
    assert g9.energy(-0.05) == pytest.approx(0.0265)
    assert g9.energy(0.1) == pytest.approx(0.1 * 1.02 * 1.16 + 0.0265)


@pytest.mark.parametrize(
    ("d", "hours"), [(date(2026, 10, 26), 24), (date(2026, 3, 29), 23), (date(2026, 10, 25), 25)]
)
def test_local_day_length(d, hours):
    start, end = tariffs.local_day_bounds(d)
    assert end - start == timedelta(hours=hours)


def test_access_tariff_missing_year():
    with pytest.raises(LookupError, match="2025-06-01"):
        tariffs.access_tariff(date(2025, 6, 1))


def test_final_prices(tmp_path, omie_file):
    d = date(2026, 10, 26)
    storage.save_day(omie.parse(omie_file(d), d), tmp_path)
    provider = tariffs.providers()["coopernico-base"]

    prices = tariffs.final_prices(storage.load_day(d, tmp_path), provider, "bi", "daily")

    assert prices.height == 96
    first = prices.row(0, named=True)
    # Period 1 is 23:00 Lisbon on the 25th: off-peak, OMIE 228.59 EUR/MWh.
    assert first["start_utc"].astimezone(LOCAL_TZ) == lisbon(2026, 10, 25, 23, 0)
    assert first["period"] == "vazio"
    assert first["tar_eur_kwh"] == 0.0158
    assert first["price_eur_kwh"] == pytest.approx((0.22859 + 0.009) * 1.15 + 0.0158)


def test_final_prices_rejects_hourly_data(tmp_path, omie_file):
    d = date(2025, 1, 1)
    storage.save_day(omie.parse(omie_file(d), d), tmp_path)
    provider = tariffs.providers()["coopernico-base"]
    with pytest.raises(ValueError, match="15-minute"):
        tariffs.final_prices(storage.load_day(d, tmp_path), provider, "simple", "daily")
