import pandas as pd

from gex.retests import second_touches

NY = "America/New_York"


def make_bars(rows, start="2026-10-15 09:30"):
    """rows: (open, high, low, close) as consecutive 5-minute bars"""
    index = pd.date_range(start, periods=len(rows), freq="5min", tz=NY)
    return pd.DataFrame(rows, index=index, columns=["Open", "High", "Low", "Close"])


def find(rows, levels, flip=None):
    return second_touches(make_bars(rows), levels, flip, tolerance=1, threshold=5)


ABOVE = (110, 111, 105, 106)
FIRST = (106, 106, 100.5, 103)  # first touch of 100 from above
BOUNCE = (103, 108, 102, 107)  # 8 points away: the level held
BACK = (107, 107, 103, 104)
SECOND = (104, 105, 99.8, 102)  # second touch, closes back above


def test_double_bottom_at_a_0dte_put_wall_is_a_long():
    [s] = find([ABOVE, FIRST, BOUNCE, BACK, SECOND], [("Magnet 2", 110), ("0DTE Put Wall", 100)])
    assert (s.name, s.direction) == ("0DTE Put Wall", "long")
    assert s.first_touch == pd.Timestamp("2026-10-15 09:35", tz=NY)
    assert s.time == pd.Timestamp("2026-10-15 09:50", tz=NY)
    assert (s.entry, s.stop) == (105, 99.8)  # break of the signal bar's high, stop under the retest low
    assert (s.target_name, s.target) == ("Magnet 2", 110)


def test_close_back_above_on_a_later_bar_counts():
    dip = (104, 104.5, 99.2, 99.5)  # second touch closes a little under the level
    reclaim = (99.5, 102, 99.6, 101.5)
    [s] = find([ABOVE, FIRST, BOUNCE, BACK, dip, reclaim], [("0DTE Put Wall", 100)])
    assert s.time == pd.Timestamp("2026-10-15 09:55", tz=NY)
    assert (s.entry, s.stop) == (102, 99.2)
    assert s.target is None


def test_no_bounce_between_touches_is_not_a_double_touch():
    hover = (103, 104, 100.4, 102)  # never moves 5 points away
    assert find([ABOVE, FIRST, hover, hover, SECOND], [("0DTE Put Wall", 100)]) == []


def test_retest_that_breaks_through_is_not_a_signal():
    crash = (104, 104, 94, 95)  # 6 points through the level
    assert find([ABOVE, FIRST, BOUNCE, BACK, crash, (95, 101, 95, 100.5)], [("0DTE Put Wall", 100)]) == []


def test_first_touch_that_breaks_is_not_a_setup():
    through = (106, 106, 94.5, 96)
    assert find([ABOVE, through, BOUNCE, BACK, SECOND], [("0DTE Put Wall", 100)]) == []


def test_retest_must_close_back_within_15_minutes():
    under = (104, 104, 99.3, 99.5)
    rows = [ABOVE, FIRST, BOUNCE, BACK, under, under, under, (99.5, 103, 99.5, 102)]
    assert find(rows, [("0DTE Put Wall", 100)]) == []


def test_double_top_at_a_0dte_call_wall_is_a_short():
    rows = [(90, 95, 89, 94), (94, 99.5, 93, 97), (97, 98, 92, 93), (93, 96, 92, 95), (95, 100.4, 94, 97)]
    [s] = find(rows, [("0DTE Call Wall", 100), ("Magnet 1", 90)])
    assert (s.direction, s.entry, s.stop) == ("short", 94, 100.4)
    assert (s.target_name, s.target) == ("Magnet 1", 90)


def test_only_0dte_walls_are_traded():
    rows = [ABOVE, FIRST, BOUNCE, BACK, SECOND]
    assert find(rows, [("Put Wall", 100)]) == []
    assert find(rows, [("Magnet 3", 100)]) == []
    assert [s.name for s in find(rows, [("Magnet 3 + 0DTE Put Wall", 100)])] == ["Magnet 3 + 0DTE Put Wall"]


def test_one_setup_per_level_per_day():
    rows = [ABOVE, FIRST, BOUNCE, BACK, SECOND, BOUNCE, BACK, SECOND]
    assert len(find(rows, [("0DTE Put Wall", 100)])) == 1


def test_regime_comes_from_the_signal_close_vs_flip():
    rows = [ABOVE, FIRST, BOUNCE, BACK, SECOND]
    assert find(rows, [("0DTE Put Wall", 100)], flip=95)[0].positive_gamma is True
    assert find(rows, [("0DTE Put Wall", 100)], flip=103)[0].positive_gamma is False
    assert find(rows, [("0DTE Put Wall", 100)])[0].positive_gamma is None
