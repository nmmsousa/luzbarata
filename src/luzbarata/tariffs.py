"""Turn OMIE prices into what a customer on an indexed tariff pays per kWh.

    final price = provider energy price (a formula on OMIE) + network access tariff (TAR)

The TAR depends on the tariff option (simple, bi-hourly, tri-hourly), the metering cycle
(daily or weekly) and which period of that cycle a moment falls in. All prices are EUR/kWh
without VAT. Prices and providers live in tariffs.toml; the period schedules are below.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cache
from importlib import resources
from typing import Literal

import polars as pl

from luzbarata.omie import LOCAL_TZ

Option = Literal["simple", "bi", "tri"]
Cycle = Literal["daily", "weekly"]
OPTIONS: tuple[Option, ...] = ("simple", "bi", "tri")
CYCLES: tuple[Cycle, ...] = ("daily", "weekly")

# Mainland BTN schedules currently in force (ERSE). Each entry is (start, period) in
# Portuguese legal time, and a period lasts until the next entry starts. "Summer" is
# legal summer time (last Sunday of March to last Sunday of October).
# ERSE approved new schedules that each BTN customer moves to on a date between
# 2027-07-01 and 2027-12-31; they need adding here, chosen per customer, before then.
_V, _C, _P = "vazio", "cheias", "ponta"
DAILY = {
    "winter": [
        ("00:00", _V), ("08:00", _C), ("09:00", _P), ("10:30", _C),
        ("18:00", _P), ("20:30", _C), ("22:00", _V),
    ],
    "summer": [
        ("00:00", _V), ("08:00", _C), ("10:30", _P), ("13:00", _C),
        ("19:30", _P), ("21:00", _C), ("22:00", _V),
    ],
}  # fmt: skip
WEEKLY = {
    "weekday": {
        "winter": [
            ("00:00", _V), ("07:00", _C), ("09:30", _P), ("12:00", _C),
            ("18:30", _P), ("21:00", _C),
        ],
        "summer": [("00:00", _V), ("07:00", _C), ("09:15", _P), ("12:15", _C)],
    },
    "saturday": {
        "winter": [("00:00", _V), ("09:30", _C), ("13:00", _V), ("18:30", _C), ("22:00", _V)],
        "summer": [("00:00", _V), ("09:00", _C), ("14:00", _V), ("20:00", _C), ("22:00", _V)],
    },
    "sunday": {"winter": [("00:00", _V)], "summer": [("00:00", _V)]},
}  # fmt: skip


def tri_period(local: datetime, cycle: Cycle) -> str:
    """Tri-hourly period (vazio, cheias or ponta) for a Europe/Lisbon datetime."""
    season = "summer" if local.dst() else "winter"
    if cycle == "daily":
        schedule = DAILY[season]
    else:
        day = {5: "saturday", 6: "sunday"}.get(local.weekday(), "weekday")
        schedule = WEEKLY[day][season]
    hhmm = local.strftime("%H:%M")
    period = schedule[0][1]
    for start, name in schedule:
        if hhmm < start:
            break
        period = name
    return period


def tariff_period(local: datetime, option: Option, cycle: Cycle) -> str:
    """Period name used to look up the TAR: simples, vazio/fora_vazio, or vazio/cheias/ponta."""
    if option == "simple":
        return "simples"
    period = tri_period(local, cycle)
    if option == "bi":
        return "vazio" if period == _V else "fora_vazio"
    return period


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    source: str
    losses: float
    checked_on: date
    factor: float = 1.0
    k_before_losses: float = 0.0
    k_after_losses: float = 0.0
    omie_floor: float | None = None

    def energy(self, omie_eur_kwh: float) -> float:
        omie = omie_eur_kwh if self.omie_floor is None else max(omie_eur_kwh, self.omie_floor)
        return (omie * self.factor + self.k_before_losses) * (1 + self.losses) + self.k_after_losses


@dataclass(frozen=True)
class AccessTariff:
    valid_from: date
    valid_to: date
    source: str
    prices: dict[str, dict[str, float]]  # option -> period -> EUR/kWh


@cache
def _config() -> dict:
    text = resources.files("luzbarata").joinpath("tariffs.toml").read_text("utf-8")
    return tomllib.loads(text)


@cache
def providers() -> dict[str, Provider]:
    return {key: Provider(id=key, **values) for key, values in _config()["providers"].items()}


def access_tariff(d: date) -> AccessTariff:
    for entry in _config()["tar"]:
        if entry["valid_from"] <= d <= entry["valid_to"]:
            prices = {option: entry[option] for option in OPTIONS}
            return AccessTariff(entry["valid_from"], entry["valid_to"], entry["source"], prices)
    raise LookupError(f"No network access tariff for {d}; add it to tariffs.toml")


PRICE_SCHEMA = {
    "start_utc": pl.Datetime("us", "UTC"),
    "end_utc": pl.Datetime("us", "UTC"),
    "period": pl.String,
    "omie_eur_kwh": pl.Float64,
    "energy_eur_kwh": pl.Float64,
    "tar_eur_kwh": pl.Float64,
    "price_eur_kwh": pl.Float64,
}


def final_prices(
    omie_prices: pl.DataFrame, provider: Provider, option: Option, cycle: Cycle
) -> pl.DataFrame:
    """Final price per slot for stored OMIE prices (see storage.SCHEMA)."""
    if (omie_prices["resolution_minutes"] != 15).any():
        raise ValueError("Final prices need 15-minute OMIE data (from 2025-10-01)")
    rows = []
    for row in omie_prices.sort("start_utc").iter_rows(named=True):
        local = row["start_utc"].astimezone(LOCAL_TZ)
        period = tariff_period(local, option, cycle)
        tar = access_tariff(local.date()).prices[option][period]
        omie = row["price_eur_mwh"] / 1000
        energy = provider.energy(omie)
        rows.append(
            {
                "start_utc": row["start_utc"],
                "end_utc": row["end_utc"],
                "period": period,
                "omie_eur_kwh": omie,
                "energy_eur_kwh": energy,
                "tar_eur_kwh": tar,
                "price_eur_kwh": energy + tar,
            }
        )
    return pl.DataFrame(rows, schema=PRICE_SCHEMA)


def local_day_bounds(d: date) -> tuple[datetime, datetime]:
    """UTC start and end of a calendar day in Lisbon.

    Returned in UTC because subtracting two datetimes that share a zone uses wall-clock
    time, which would make every day 24h long, including the 23h and 25h DST days.
    """
    start = datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ)
    nxt = d + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=LOCAL_TZ)
    return start.astimezone(UTC), end.astimezone(UTC)


def local_day_slots(d: date, slot_minutes: int = 15) -> int:
    """Number of slots in a Lisbon calendar day: 92, 96 or 100 at 15 minutes."""
    start, end = local_day_bounds(d)
    return int((end - start).total_seconds()) // (slot_minutes * 60)
