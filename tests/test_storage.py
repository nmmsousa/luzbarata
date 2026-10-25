from datetime import date

from luzbarata import omie, storage


def test_save_and_load_day(tmp_path, omie_file):
    d = date(2026, 10, 26)
    storage.save_day(omie.parse(omie_file(d), d), tmp_path)

    df = storage.load_day(d, tmp_path)
    assert df.height == 96
    assert df["period"].to_list() == list(range(1, 97))
    assert storage.month_path(tmp_path, d).exists()


def test_saving_same_day_twice_does_not_duplicate(tmp_path, omie_file):
    d = date(2026, 10, 26)
    periods = omie.parse(omie_file(d), d)
    storage.save_day(periods, tmp_path)
    storage.save_day(periods, tmp_path)

    assert storage.load_day(d, tmp_path).height == 96


def test_days_in_same_month_share_a_file(tmp_path, omie_file):
    for d in (date(2026, 10, 25), date(2026, 10, 26)):
        storage.save_day(omie.parse(omie_file(d), d), tmp_path)

    assert storage.load_day(date(2026, 10, 25), tmp_path).height == 100
    assert storage.load_day(date(2026, 10, 26), tmp_path).height == 96
    assert len(list(tmp_path.rglob("*.parquet"))) == 1


def test_has_day(tmp_path, omie_file):
    d = date(2025, 1, 1)
    assert not storage.has_day(d, tmp_path)
    storage.save_day(omie.parse(omie_file(d), d), tmp_path)
    assert storage.has_day(d, tmp_path)
    assert not storage.has_day(date(2025, 1, 2), tmp_path)
