from datetime import date

import pandas as pd

from gex.bars import archived_bars, bars_path, is_complete, load_bars, save_bars, trading_day

NY = "America/New_York"


def make_bars(stamps):
    """One 5-minute bar per 'YYYY-MM-DD HH:MM' stamp; prices count up so bars are easy to tell apart."""
    index = pd.DatetimeIndex([pd.Timestamp(s, tz=NY) for s in stamps])
    n = len(stamps)
    return pd.DataFrame(
        {"Open": range(n), "High": range(1, n + 1), "Low": range(n), "Close": range(n), "Volume": [10] * n},
        index=index,
        dtype=float,
    )


def at(day, hhmm="23:59"):
    return pd.Timestamp(f"{day} {hhmm}", tz=NY)


TUESDAY = ["2026-10-12 17:55", "2026-10-12 18:00", "2026-10-13 09:30", "2026-10-13 15:55", "2026-10-13 16:00"]


def test_trading_day_runs_from_the_evening_before_to_the_close():
    kept = trading_day(make_bars(TUESDAY), date(2026, 10, 13))
    assert [f"{t:%m-%d %H:%M}" for t in kept.index] == ["10-12 18:00", "10-13 09:30", "10-13 15:55"]


def test_complete_only_with_the_last_regular_bar():
    assert is_complete(make_bars(TUESDAY[:4]), date(2026, 10, 13))
    assert not is_complete(make_bars(TUESDAY[:3]), date(2026, 10, 13))


def test_save_and_load_keep_new_york_times_across_daylight_saving(tmp_path):
    original = make_bars(["2026-10-30 15:55", "2026-11-02 09:30"])  # EDT, then EST
    path = tmp_path / "ES_x.csv"
    save_bars(original, path)
    loaded = load_bars(path)
    assert list(loaded.index) == list(original.index)
    assert str(loaded.index.tz) == NY
    assert loaded["Close"].tolist() == [0.0, 1.0]


def fetch_from(frame, calls):
    def fetch(start, end):
        calls.append((start, end))
        return frame

    return fetch


def test_finished_sessions_are_saved_then_read_back_without_downloading(tmp_path):
    calls = []
    first = archived_bars("ES", [date(2026, 10, 13)], at("2026-10-13"), tmp_path, fetch_from(make_bars(TUESDAY), calls))
    assert len(first) == 3
    assert calls == [(date(2026, 10, 12), date(2026, 10, 13))]  # from the evening before
    assert bars_path(tmp_path, "ES", date(2026, 10, 13)).exists()

    again = archived_bars("ES", [date(2026, 10, 13)], at("2026-10-14"), tmp_path, fetch_from(None, calls))
    assert len(calls) == 1
    assert again["Close"].tolist() == first["Close"].tolist()


def test_session_still_running_is_used_but_not_saved(tmp_path):
    got = archived_bars("ES", [date(2026, 10, 13)], at("2026-10-13", "16:10"), tmp_path, fetch_from(make_bars(TUESDAY), []))
    assert len(got) == 3
    assert not bars_path(tmp_path, "ES", date(2026, 10, 13)).exists()


def test_incomplete_download_is_not_saved(tmp_path):
    archived_bars("ES", [date(2026, 10, 13)], at("2026-10-14"), tmp_path, fetch_from(make_bars(TUESDAY[:3]), []))
    assert not bars_path(tmp_path, "ES", date(2026, 10, 13)).exists()


def test_sessions_older_than_the_download_limit_are_not_requested(tmp_path):
    calls = []
    got = archived_bars("ES", [date(2026, 6, 1)], at("2026-10-14"), tmp_path, fetch_from(make_bars(TUESDAY), calls))
    assert calls == [] and got.empty
