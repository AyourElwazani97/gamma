from datetime import date

import pandas as pd

from gex.profile import Profile, confluences, format_confluence, prior_trading_day, value_area, volume_profile

NY = "America/New_York"


def make_bars(rows, start="2026-10-14 09:30"):
    """rows: (low, high, volume) as consecutive 5-minute bars"""
    index = pd.date_range(start, periods=len(rows), freq="5min", tz=NY)
    frame = pd.DataFrame(rows, index=index, columns=["Low", "High", "Volume"])
    frame["Open"] = frame["Close"] = frame["Low"]
    return frame


def test_bar_volume_is_spread_evenly_over_its_range():
    # 50 over 100..101 (5 ticks) = 10 a tick; 30 all at 100.5 -> POC 100.5 with 40
    p = volume_profile(make_bars([(100, 101, 50), (100.5, 100.5, 30)]))
    assert p.poc == 100.5


def test_value_area_grows_two_rows_at_a_time_toward_the_bigger_side_like_freeorderflow():
    prices = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    volumes = [1, 1, 2, 5, 10, 4, 3, 1, 1]  # 28 in all, 70% = 19.6
    # POC 5 (10). Up 6+7 = 7 vs down 4+3 = 7: tie goes down -> 17. Up 7 vs down 2+1 = 2: up -> 24, done.
    # VAL keeps the down rows it looked at (1), as FreeOrderFlow does
    assert value_area(prices, volumes, 0.7) == (5, 7, 1)


def test_empty_bars_have_no_profile():
    assert volume_profile(make_bars([])) is None


def test_levels_near_the_profile_lines():
    levels = [("Expected Move High", 7808.25), ("Gamma Flip", 7798.25), ("Magnet 2", 7749.0)]
    found = confluences(levels, Profile(poc=7767.75, vah=7807.75, val=7744.75), tolerance=5)
    assert found == [("Expected Move High", 7808.25, "VAH", 7807.75), ("Magnet 2", 7749.0, "VAL", 7744.75)]


def test_a_level_matches_its_nearest_profile_line():
    found = confluences([("Magnet 1", 100.0)], Profile(poc=103, vah=104, val=98), tolerance=5)
    assert found == [("Magnet 1", 100.0, "VAL", 98)]


def test_prior_trading_day_skips_the_weekend():
    assert prior_trading_day(date(2026, 10, 15)) == date(2026, 10, 14)
    assert prior_trading_day(date(2026, 10, 12)) == date(2026, 10, 9)


def test_format_confluence():
    text = format_confluence(date(2026, 10, 14), Profile(7767.75, 7807.75, 7744.75),
                             [("Magnet 2", 7749.0, "VAL", 7744.75)], tolerance=4.7)  # fmt: skip
    assert "Wed Oct 14 volume profile: POC 7767.75, VAH 7807.75, VAL 7744.75" in text
    assert "Magnet 2 7749 ~ VAL 7744.75 (4.25 away)" in text
    none = format_confluence(date(2026, 10, 14), Profile(7767.75, 7807.75, 7744.75), [], tolerance=4.7)
    assert "no level within 4.7 points" in none
