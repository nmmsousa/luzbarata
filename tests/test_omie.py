from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from luzbarata import omie
from tests.conftest import FIXTURES


@pytest.mark.parametrize(
    ("d", "periods", "resolution"),
    [
        (date(2025, 1, 1), 24, 60),  # hourly, normal day
        (date(2025, 3, 30), 23, 60),  # hourly, clocks go forward
        (date(2024, 10, 27), 25, 60),  # hourly, clocks go back
        (date(2026, 10, 26), 96, 15),  # 15-minute, normal day
        (date(2026, 3, 29), 92, 15),  # 15-minute, clocks go forward
        (date(2026, 10, 25), 100, 15),  # 15-minute, clocks go back
    ],
)
def test_parse_real_files(omie_file, d, periods, resolution):
    result = omie.parse(omie_file(d), d)

    assert len(result) == periods
    assert {p.resolution_minutes for p in result} == {resolution}
    start, end = omie.market_day_bounds(d)
    assert result[0].start_utc == start
    assert result[-1].end_utc == end
    # No gaps or overlaps between consecutive periods.
    assert all(a.end_utc == b.start_utc for a, b in zip(result, result[1:], strict=False))


def test_period_one_starts_at_madrid_midnight(omie_file):
    d = date(2026, 10, 26)
    first = omie.parse(omie_file(d), d)[0]
    # Midnight in Madrid (UTC+1 in winter) is 23:00 UTC, and 23:00 in Lisbon, the day before.
    assert first.start_utc == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
    assert first.end_utc - first.start_utc == timedelta(minutes=15)
    assert first.price_eur_mwh == 228.59


def test_parse_rejects_row_for_other_day(omie_file):
    text = omie_file(date(2025, 1, 1))
    with pytest.raises(omie.InvalidFile, match="contains a row for"):
        omie.parse(text, date(2025, 1, 2))


def test_parse_rejects_missing_period(omie_file):
    d = date(2025, 1, 1)
    text = "\n".join(line for line in omie_file(d).splitlines() if ";01;01;7;" not in line)
    with pytest.raises(omie.InvalidFile, match="do not fit"):
        omie.parse(text, d)


def test_parse_rejects_hourly_count_on_dst_day(omie_file):
    # A 24-row file cannot be right for a 23-hour day.
    text = omie_file(date(2025, 1, 1)).replace("2025;01;01;", "2025;03;30;")
    with pytest.raises(omie.InvalidFile, match="do not fit a 23h day"):
        omie.parse(text, date(2025, 3, 30))


def test_parse_rejects_duplicate_period(omie_file):
    d = date(2025, 1, 1)
    text = omie_file(d).replace("2025;01;01;2;", "2025;01;01;1;")
    with pytest.raises(omie.InvalidFile, match="Duplicate period 1"):
        omie.parse(text, d)


def test_parse_rejects_price_out_of_bounds(omie_file):
    d = date(2026, 10, 26)
    text = omie_file(d).replace("2026;10;26;1;228.59;", "2026;10;26;1;9999;")
    with pytest.raises(omie.InvalidFile, match="outside"):
        omie.parse(text, d)


def test_parse_accepts_negative_prices():
    d = date(2025, 1, 1)
    rows = "\n".join(f"2025;01;01;{h};-1.5;-1.5;" for h in range(1, 25))
    result = omie.parse(f"MARGINALPDBCPT;\n{rows}\n*\n", d)
    assert result[0].price_eur_mwh == -1.5


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_html_page_means_not_available():
    html = (FIXTURES / "not_available.html").read_bytes()
    client = mock_client(lambda request: httpx.Response(200, content=html))
    with pytest.raises(omie.DataNotAvailable):
        omie.download(date(2026, 11, 20), client=client)


def test_download_404_means_not_available_without_retrying():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    with pytest.raises(omie.DataNotAvailable):
        omie.download(date(2026, 11, 20), client=mock_client(handler), backoff_seconds=0)
    assert len(calls) == 1


def test_download_retries_server_errors(omie_file):
    d = date(2025, 1, 1)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=omie_file(d).encode("latin-1"))

    text = omie.download(d, client=mock_client(handler), backoff_seconds=0)
    assert len(calls) == 3
    assert "marginalpdbcpt_20250101.1" in str(calls[0].url)
    assert text.startswith(omie.HEADER)


def test_download_gives_up_after_retries():
    client = mock_client(lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        omie.download(date(2025, 1, 1), client=client, retries=2, backoff_seconds=0)
