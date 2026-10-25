"""Daily message for the public Telegram channel, in Portuguese, and posting it once per day.

Days already posted are recorded in a CSV next to the price store, which CI commits, so a
rerun of the workflow never posts the same day twice.
"""

from __future__ import annotations

import csv
import html
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from luzbarata.omie import LOCAL_TZ
from luzbarata.windows import DaySummary, Window

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
WEEKDAYS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")
MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip
LOG_FIELDS = ("date", "message_id", "posted_at_utc")


class TelegramError(Exception):
    """The Bot API refused or failed to deliver a message."""


def _price(eur_kwh: float) -> str:
    return f"{eur_kwh:.4f}".replace(".", ",") + " €/kWh"


def _span(w: Window) -> str:
    return f"{w.start_utc.astimezone(LOCAL_TZ):%H:%M}–{w.end_utc.astimezone(LOCAL_TZ):%H:%M}"


def _section(title: str, s: DaySummary) -> list[str]:
    lines = [f"<b>{html.escape(title)}</b>"]
    for hours, w in s.cheapest.items():
        saving = round(100 * (w.avg_price / s.avg_price - 1))
        lines.append(f"🟢 {hours}h: {_span(w)} · {_price(w.avg_price)} ({saving:+d}%)")
    if s.priciest:
        w = s.priciest
        lines.append(f"🔴 Evitar: {_span(w)} · {_price(w.avg_price)}")
    lines.append(f"Média do dia: {_price(s.avg_price)}")
    return lines


def format_message(d: date, provider_name: str, sections: dict[str, DaySummary]) -> str:
    """HTML message for Lisbon day `d`, one section per tariff (title -> summary)."""
    lines = [
        f"<b>⚡ Horas mais baratas · {WEEKDAYS[d.weekday()]}, {d.day} de {MONTHS[d.month - 1]}</b>"
    ]
    for title, summary in sections.items():
        lines += ["", *_section(title, summary)]
    lines += [
        "",
        f"<i>Preço final estimado num tarifário indexado ({html.escape(provider_name)}), "
        "sem IVA. % face à média do dia. Hora de Portugal continental.</i>",
    ]
    if any(s.slots < s.expected_slots for s in sections.values()):
        lines.append("<i>O preço das 23:00–24:00 só é publicado amanhã.</i>")
    lines.append("<i>Dados: OMIE.</i>")
    return "\n".join(lines)


def send_message(token: str, chat_id: str, text: str, client: httpx.Client | None = None) -> int:
    """Post `text` (HTML) to a chat or channel and return the new message id."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    own_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        response = client.post(API_URL.format(token=token), json=payload)
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Not chained: the exception text would include the URL, which holds the token.
        raise TelegramError(f"Telegram request failed: {type(exc).__name__}") from None
    finally:
        if own_client:
            client.close()
    if not body.get("ok"):
        raise TelegramError(f"Telegram refused the message: {body.get('description')}")
    return body["result"]["message_id"]


def log_path(data_dir: Path) -> Path:
    return data_dir / "telegram_posts.csv"


def was_posted(d: date, data_dir: Path) -> bool:
    path = log_path(data_dir)
    if not path.exists():
        return False
    with path.open(newline="") as f:
        return any(row["date"] == d.isoformat() for row in csv.DictReader(f))


def record_post(d: date, message_id: int, data_dir: Path) -> None:
    path = log_path(data_dir)
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(LOG_FIELDS)
        writer.writerow(
            [d.isoformat(), message_id, datetime.now(UTC).isoformat(timespec="seconds")]
        )
