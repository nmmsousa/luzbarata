import json
from datetime import date

import httpx
import pytest

from luzbarata import cli, telegram
from tests.test_cli import store

TOKEN = "123:secret"


def test_message_from_stored_prices(tmp_path, omie_file, capsys):
    store(tmp_path, omie_file, date(2026, 10, 26))
    args = ["--data-dir", str(tmp_path), "post", "--date", "2026-10-26", "--dry-run"]

    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert out.startswith("<b>⚡ Horas mais baratas · Segunda, 26 de outubro</b>")
    assert "<b>Tarifa simples</b>" in out
    assert "<b>Bi-horária (ciclo diário)</b>" in out
    # Same windows as `luzbarata windows`, with Portuguese decimals.
    assert "🟢 1h: 13:00–14:00 · 0,1361 €/kWh (-54%)" in out
    assert "🔴 Evitar: 18:15–20:15 · 0,4159 €/kWh" in out
    assert "23:00–24:00 só é publicado amanhã" in out
    assert not telegram.log_path(tmp_path).exists()


def test_full_day_has_no_missing_hour_note(tmp_path, omie_file, capsys):
    store(tmp_path, omie_file, date(2026, 10, 25), date(2026, 10, 26))
    args = ["--data-dir", str(tmp_path), "post", "--date", "2026-10-25", "--dry-run"]

    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "Domingo, 25 de outubro" in out
    assert "só é publicado amanhã" not in out


def test_post_without_prices_is_not_available(tmp_path):
    args = ["--data-dir", str(tmp_path), "post", "--date", "2026-10-26"]
    assert cli.main(args) == cli.EXIT_NOT_AVAILABLE


def test_posts_once_per_day(tmp_path, omie_file, monkeypatch):
    store(tmp_path, omie_file, date(2026, 10, 26))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@luzbarata")
    sent = []

    def fake_send(token, chat_id, text):
        sent.append((token, chat_id, text))
        return 42

    monkeypatch.setattr(telegram, "send_message", fake_send)
    args = ["--data-dir", str(tmp_path), "post", "--date", "2026-10-26"]

    assert cli.main(args) == 0
    assert cli.main(args) == 0
    assert len(sent) == 1
    assert sent[0][:2] == (TOKEN, "@luzbarata")
    assert telegram.was_posted(date(2026, 10, 26), tmp_path)
    assert not telegram.was_posted(date(2026, 10, 27), tmp_path)
    rows = telegram.log_path(tmp_path).read_text().splitlines()
    assert rows[0] == "date,message_id,posted_at_utc"
    assert rows[1].startswith("2026-10-26,42,")


def test_failed_post_is_not_recorded(tmp_path, omie_file, monkeypatch):
    store(tmp_path, omie_file, date(2026, 10, 26))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@luzbarata")

    def fail(*args):
        raise telegram.TelegramError("Telegram refused the message: Forbidden")

    monkeypatch.setattr(telegram, "send_message", fail)
    assert cli.main(["--data-dir", str(tmp_path), "post", "--date", "2026-10-26"]) == 1
    assert not telegram.was_posted(date(2026, 10, 26), tmp_path)


def test_post_needs_credentials(tmp_path, omie_file, monkeypatch):
    store(tmp_path, omie_file, date(2026, 10, 26))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert cli.main(["--data-dir", str(tmp_path), "post", "--date", "2026-10-26"]) == 1


def test_send_message():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/bot{TOKEN}/sendMessage"
        body = json.loads(request.content)
        assert body["chat_id"] == "@luzbarata"
        assert body["parse_mode"] == "HTML"
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert telegram.send_message(TOKEN, "@luzbarata", "<b>olá</b>", client) == 7


def test_send_message_error_hides_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"ok": False, "description": "Forbidden: bot is not a member"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(telegram.TelegramError, match="bot is not a member") as exc:
        telegram.send_message(TOKEN, "@luzbarata", "x", client)
    assert TOKEN not in str(exc.value)


def test_network_error_hides_token():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(telegram.TelegramError) as exc:
        telegram.send_message(TOKEN, "@luzbarata", "x", client)
    assert TOKEN not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
