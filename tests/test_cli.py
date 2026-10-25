from datetime import date

from luzbarata import cli, omie, storage


def store(tmp_path, omie_file, *days: date):
    for d in days:
        storage.save_day(omie.parse(omie_file(d), d), tmp_path)


def test_local_day_spans_two_delivery_days(tmp_path, omie_file):
    store(tmp_path, omie_file, date(2026, 10, 25), date(2026, 10, 26))
    # 25 Oct is 25h long in Lisbon: 99 slots from delivery day 25 plus 23:00-24:00 from the 26th.
    assert cli.load_local_day(date(2026, 10, 25), tmp_path).height == 100


def test_windows_command(tmp_path, omie_file, capsys):
    store(tmp_path, omie_file, date(2026, 10, 25), date(2026, 10, 26))
    args = ["--data-dir", str(tmp_path), "windows", "--date", "2026-10-25", "--option", "bi"]

    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "bi-hourly, daily cycle" in out
    assert "100/100 slots known" in out
    assert "Cheapest 4h" in out
    assert "not published yet" not in out


def test_windows_command_notes_missing_evening(tmp_path, omie_file, capsys):
    store(tmp_path, omie_file, date(2026, 10, 26))
    args = ["--data-dir", str(tmp_path), "windows", "--date", "2026-10-26"]

    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "92/96 slots known" in out
    assert "not published yet" in out


def test_windows_command_without_data(tmp_path, capsys):
    assert cli.main(["--data-dir", str(tmp_path), "windows", "--date", "2026-10-26"]) == 1
