import json
from datetime import date

import pytest

from luzbarata import cli, omie, site, storage, tariffs


@pytest.fixture
def built(tmp_path, omie_file):
    data, out = tmp_path / "data", tmp_path / "site"
    for d in (date(2026, 10, 25), date(2026, 10, 26)):
        storage.save_day(omie.parse(omie_file(d), d), data)
    days = site.build(out, data)
    return data, out, days


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def test_index_lists_days_and_tariffs(built):
    _, out, days = built
    assert days == [date(2026, 10, 25), date(2026, 10, 26)]
    index = read_json(out / "api" / "v1" / "index.json")
    assert [(d["date"], d["complete"]) for d in index["days"]] == [
        ("2026-10-25", True),  # 25h DST day, 23:00-24:00 comes from the 26th
        ("2026-10-26", False),  # 23:00-24:00 needs the 27th
    ]
    ids = [t["id"] for t in index["tariffs"]]
    assert "coopernico-base-simple" in ids and "coopernico-base-tri-weekly" in ids
    assert not any(i.startswith("g9") for i in ids)  # unverified provider stays off
    for t in index["tariffs"]:
        assert (out / t["calendar"]).exists()
    assert (out / "index.html").exists()


def test_day_matches_windows_command(built):
    data, out, _ = built
    doc = read_json(out / "api" / "v1" / "days" / "2026-10-26.json")
    assert (doc["slots_known"], doc["slots_expected"]) == (92, 96)
    t = next(t for t in doc["tariffs"] if t["id"] == "coopernico-base-bi-daily")
    assert len(t["price_eur_kwh"]) == len(t["period"]) == len(doc["slots"])

    provider = tariffs.providers()["coopernico-base"]
    s = cli.day_summary(date(2026, 10, 26), provider, "bi", "daily", data)
    assert t["avg_eur_kwh"] == round(s.avg_price, 5)
    w = s.cheapest[2]
    assert t["cheapest"]["2h"]["start_utc"] == f"{w.start_utc:%Y-%m-%dT%H:%M:%SZ}"
    assert t["priciest_2h"]["avg_eur_kwh"] == round(s.priciest.avg_price, 5)


def test_dst_day_has_100_slots(built):
    _, out, _ = built
    doc = read_json(out / "api" / "v1" / "days" / "2026-10-25.json")
    assert doc["slots_known"] == doc["slots_expected"] == 100


def test_calendar_feed(built):
    _, out, _ = built
    raw = (out / "calendar" / "coopernico-base-bi-daily.ics").read_bytes()
    lines = raw.split(b"\r\n")
    assert lines[0] == b"BEGIN:VCALENDAR" and lines[-2] == b"END:VCALENDAR"
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert all(len(line) <= 75 for line in lines)
    text = raw.decode().replace("\r\n ", "")  # unfold
    assert text.count("BEGIN:VEVENT") == 2
    assert "UID:2026-10-26-coopernico-base-bi-daily@luzbarata" in text
    assert "SUMMARY:⚡ Luz mais barata (2h)" in text


def test_fold_keeps_multibyte_characters_whole():
    folded = site._fold("DESCRIPTION:" + "é" * 80)
    parts = folded.split("\r\n")
    assert all(len(p.encode()) <= 75 for p in parts)
    assert "".join(p.removeprefix(" ") for p in parts) == "DESCRIPTION:" + "é" * 80


def test_only_newest_days_are_published(tmp_path, built):
    data, _, _ = built
    assert site.build(tmp_path / "one", data, days=1) == [date(2026, 10, 26)]


def test_site_command_without_data(tmp_path):
    args = ["--data-dir", str(tmp_path), "site", "--out", str(tmp_path / "site")]
    assert cli.main(args) == 1
