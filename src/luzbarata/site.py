"""Static web page, JSON API and calendar feeds, built from the price store.

Output (served as-is by GitHub Pages):

    index.html                     chart and cheapest windows, in Portuguese
    api/v1/index.json              tariffs and the days available
    api/v1/days/YYYY-MM-DD.json    one Lisbon day: slots, final prices, cheapest windows
    calendar/<tariff>.ics          cheapest 2h of each day, one feed per tariff

The page works out today and tomorrow in the browser, so the site only needs rebuilding
when new prices arrive, not at midnight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from pathlib import Path

from luzbarata import storage, tariffs, windows
from luzbarata.tariffs import Cycle, Option, Provider

SITE_URL = "https://nmmsousa.github.io/luzbarata/"
# G9 Smart Index stays off until its prices are checked against a real bill (CLAUDE.md).
SITE_PROVIDERS = ("coopernico-base",)
TARIFF_LABELS: dict[tuple[Option, Cycle], str] = {
    ("simple", "daily"): "Tarifa simples",
    ("bi", "daily"): "Bi-horária, ciclo diário",
    ("bi", "weekly"): "Bi-horária, ciclo semanal",
    ("tri", "daily"): "Tri-horária, ciclo diário",
    ("tri", "weekly"): "Tri-horária, ciclo semanal",
}
DAYS = 30  # Lisbon days published, ending with the newest stored
CALENDAR_HOURS = 2
UNIT = "EUR/kWh, excluding VAT"


@dataclass(frozen=True)
class Tariff:
    provider: Provider
    option: Option
    cycle: Cycle

    @property
    def id(self) -> str:
        if self.option == "simple":
            return f"{self.provider.id}-simple"
        return f"{self.provider.id}-{self.option}-{self.cycle}"

    @property
    def label(self) -> str:
        return TARIFF_LABELS[self.option, self.cycle]


def site_tariffs() -> list[Tariff]:
    all_providers = tariffs.providers()
    return [
        Tariff(all_providers[p], option, cycle)
        for p in SITE_PROVIDERS
        for option, cycle in TARIFF_LABELS
    ]


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(w: windows.Window | None, day_avg: float) -> dict | None:
    if w is None:
        return None
    return {
        "start_utc": _ts(w.start_utc),
        "end_utc": _ts(w.end_utc),
        "avg_eur_kwh": round(w.avg_price, 5),
        "vs_day_avg": round(w.avg_price / day_avg - 1, 4),
    }


def day_data(
    d: date, data_dir: Path, shown: list[Tariff], generated_at: datetime
) -> tuple[dict, dict[str, windows.DaySummary]] | None:
    """JSON document for Lisbon day `d` and each tariff's summary, or None without prices."""
    omie_prices = storage.load_local_day(d, data_dir).sort("start_utc")
    if omie_prices.is_empty():
        return None
    expected = tariffs.local_day_slots(d, windows.SLOT_MINUTES)
    doc_tariffs = []
    summaries = {}
    for t in shown:
        prices = tariffs.final_prices(omie_prices, t.provider, t.option, t.cycle)
        s = windows.summarise(prices, expected)
        summaries[t.id] = s
        doc_tariffs.append(
            {
                "id": t.id,
                # Aligned with "slots"; "period" is the network tariff period of each slot.
                "price_eur_kwh": [round(p, 5) for p in prices["price_eur_kwh"]],
                "period": prices["period"].to_list(),
                "avg_eur_kwh": round(s.avg_price, 5),
                "cheapest": {f"{h}h": _window(w, s.avg_price) for h, w in s.cheapest.items()},
                f"priciest_{windows.PRICIEST_HOURS}h": _window(s.priciest, s.avg_price),
            }
        )
    doc = {
        "date": d.isoformat(),
        "timezone": "Europe/Lisbon",
        "unit": UNIT,
        "generated_at_utc": _ts(generated_at),
        "slots_known": omie_prices.height,
        "slots_expected": expected,
        "slots": [
            {
                "start_utc": _ts(row["start_utc"]),
                "end_utc": _ts(row["end_utc"]),
                "omie_eur_mwh": row["price_eur_mwh"],
            }
            for row in omie_prices.iter_rows(named=True)
        ],
        "tariffs": doc_tariffs,
    }
    return doc, summaries


def _ics_text(text: str) -> str:
    for char, escaped in (("\\", "\\\\"), (";", "\\;"), (",", "\\,"), ("\n", "\\n")):
        text = text.replace(char, escaped)
    return text


def _fold(line: str) -> str:
    """Split a content line into lines of at most 75 octets (RFC 5545 section 3.1)."""
    parts, current, size = [], "", 0
    for char in line:
        n = len(char.encode())
        if size + n > 75:
            parts.append(current)
            current, size = " ", 1
        current += char
        size += n
    parts.append(current)
    return "\r\n".join(parts)


def _price_pt(eur_kwh: float) -> str:
    return f"{eur_kwh:.4f}".replace(".", ",") + " €/kWh"


def calendar(t: Tariff, summaries: dict[date, windows.DaySummary], generated_at: datetime) -> str:
    """iCalendar feed with the cheapest CALENDAR_HOURS of each day for one tariff."""
    stamp = generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//luzbarata//luzbarata//PT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_text(f'Luz barata · {t.label}')}",
        "X-WR-TIMEZONE:Europe/Lisbon",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for d, s in sorted(summaries.items()):
        w = s.cheapest.get(CALENDAR_HOURS)
        if w is None:
            continue
        saving = round(100 * (w.avg_price / s.avg_price - 1))
        description = (
            f"Preço final estimado: {_price_pt(w.avg_price)} ({saving:+d}% face à média do dia, "
            f"{_price_pt(s.avg_price)}). {t.provider.name}, {t.label.lower()}, sem IVA.\n"
            f"Dados: OMIE. {SITE_URL}"
        )
        lines += [
            "BEGIN:VEVENT",
            # Stable per day and tariff, so an update (e.g. once 23:00-24:00 is known)
            # replaces the event instead of adding another.
            f"UID:{d.isoformat()}-{t.id}@luzbarata",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{w.start_utc.astimezone(UTC):%Y%m%dT%H%M%SZ}",
            f"DTEND:{w.end_utc.astimezone(UTC):%Y%m%dT%H%M%SZ}",
            f"SUMMARY:{_ics_text(f'⚡ Luz mais barata ({CALENDAR_HOURS}h)')}",
            f"DESCRIPTION:{_ics_text(description)}",
            f"URL:{SITE_URL}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(_fold(line) + "\r\n" for line in lines)


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), "utf-8")


def build(out_dir: Path, data_dir: Path, days: int = DAYS) -> list[date]:
    """Write the site for the newest `days` Lisbon days in the store; returns those days.

    Lisbon day d is published once delivery day d is stored (00:00-23:00); its last hour
    fills in when d + 1 arrives.
    """
    stored = storage.stored_days(data_dir)
    if not stored:
        raise ValueError(f"No prices stored in {data_dir}")
    first = stored[-1] - timedelta(days=days - 1)
    generated_at = datetime.now(UTC)
    shown = site_tariffs()

    published = []
    summaries: dict[str, dict[date, windows.DaySummary]] = {t.id: {} for t in shown}
    for d in (d for d in stored if d >= first):
        result = day_data(d, data_dir, shown, generated_at)
        if result is None:
            continue
        doc, day_summaries = result
        _write_json(out_dir / "api" / "v1" / "days" / f"{d.isoformat()}.json", doc)
        for tariff_id, s in day_summaries.items():
            summaries[tariff_id][d] = s
        published.append((d, doc["slots_known"] == doc["slots_expected"]))

    index = {
        "generated_at_utc": _ts(generated_at),
        "site": SITE_URL,
        "timezone": "Europe/Lisbon",
        "unit": UNIT,
        "market_data": "OMIE day-ahead marginal price for Portugal, https://www.omie.es/",
        "tariffs": [
            {
                "id": t.id,
                "provider": t.provider.id,
                "provider_name": t.provider.name,
                "option": t.option,
                "cycle": None if t.option == "simple" else t.cycle,
                "label": t.label,
                "calendar": f"calendar/{t.id}.ics",
                "formula_source": t.provider.source,
                "checked_on": t.provider.checked_on.isoformat(),
            }
            for t in shown
        ],
        "days": [
            {"date": d.isoformat(), "url": f"api/v1/days/{d.isoformat()}.json", "complete": c}
            for d, c in published
        ],
    }
    _write_json(out_dir / "api" / "v1" / "index.json", index)

    for t in shown:
        path = out_dir / "calendar" / f"{t.id}.ics"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(calendar(t, summaries[t.id], generated_at).encode())

    page = resources.files("luzbarata").joinpath("web", "index.html").read_text("utf-8")
    (out_dir / "index.html").write_text(page, "utf-8")
    # Serve files as they are; Pages would otherwise run them through Jekyll.
    (out_dir / ".nojekyll").touch()
    return [d for d, _ in published]
