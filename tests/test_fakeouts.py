import pandas as pd

from gex.fakeouts import failed_breakouts

NY = "America/New_York"


def make_bars(rows, start="2026-10-12 09:30"):
    """rows: (open, high, low, close) as consecutive 5-minute bars"""
    index = pd.date_range(start, periods=len(rows), freq="5min", tz=NY)
    return pd.DataFrame(rows, index=index, columns=["Open", "High", "Low", "Close"])


def find(rows, levels, flip=None):
    return failed_breakouts(make_bars(rows), levels, flip, tolerance=1)


def test_wick_above_call_wall_is_a_short_setup():
    [s] = find([(95, 97, 94, 96), (96, 102, 95, 98)], [("Call Wall", 100), ("Magnet 1", 90)])
    assert (s.name, s.direction) == ("Call Wall", "short")
    assert s.time == pd.Timestamp("2026-10-12 09:35", tz=NY)
    assert (s.entry, s.stop) == (95, 102)
    assert (s.target_name, s.target) == ("Magnet 1", 90)


def test_close_back_inside_on_a_later_bar_counts():
    rows = [(95, 97, 94, 96), (96, 103, 95, 102), (102, 104, 99, 99.5)]
    [s] = find(rows, [("Call Wall", 100)])
    assert s.poke_time == pd.Timestamp("2026-10-12 09:35", tz=NY)
    assert s.time == pd.Timestamp("2026-10-12 09:40", tz=NY)
    assert (s.entry, s.stop) == (99, 104)  # stop above the highest point of the whole push
    assert s.target is None


def test_breakout_that_holds_for_three_bars_is_not_a_fakeout():
    rows = [(95, 97, 94, 96), (96, 103, 95, 102), (102, 106, 101, 105), (105, 108, 104, 107), (107, 108, 99.5, 99.5)]
    assert find(rows, [("Call Wall", 100)]) == []


def test_flush_below_put_wall_is_a_long_setup():
    [s] = find([(105, 106, 103, 104), (104, 105, 97, 101)], [("Magnet 2", 110), ("Put Wall", 100)])
    assert (s.name, s.direction) == ("Put Wall", "long")
    assert (s.entry, s.stop) == (105, 97)
    assert (s.target_name, s.target) == ("Magnet 2", 110)


def test_touch_within_tolerance_is_not_a_push_through():
    assert find([(95, 97, 94, 96), (96, 100.5, 95, 98)], [("Call Wall", 100)]) == []


def test_only_walls_and_expected_move_edges_are_faded():
    rows = [(95, 97, 94, 96), (96, 102, 95, 98)]
    assert find(rows, [("Magnet 1", 100)]) == []
    assert find(rows, [("Gamma Flip", 100)]) == []
    assert [s.name for s in find(rows, [("0DTE Call Wall + Magnet 1", 100)])] == ["0DTE Call Wall + Magnet 1"]
    assert [s.name for s in find(rows, [("Expected Move High", 100)])] == ["Expected Move High"]


def test_regime_comes_from_the_failure_close_vs_flip():
    rows = [(95, 97, 94, 96), (96, 102, 95, 98)]
    assert find(rows, [("Call Wall", 100)], flip=90)[0].positive_gamma is True
    assert find(rows, [("Call Wall", 100)], flip=99)[0].positive_gamma is False
    assert find(rows, [("Call Wall", 100)], flip=None)[0].positive_gamma is None


def test_one_setup_per_level_and_direction():
    rows = [(95, 97, 94, 96), (96, 102, 95, 98), (98, 99, 96, 97), (97, 103, 96, 98)]
    setups = find(rows, [("Call Wall", 100)])
    assert len(setups) == 1
    assert setups[0].time == pd.Timestamp("2026-10-12 09:35", tz=NY)


def test_open_already_above_level_is_not_a_push_from_below():
    assert find([(101, 103, 99.5, 99.5)], [("Call Wall", 100)]) == []


def test_failed_reclaim_of_a_put_wall_is_not_faded():
    # price below the Put Wall pushes back above it and fails: not a fade of a support level
    assert find([(95, 97, 94, 96), (96, 102, 95, 98)], [("Put Wall", 100)]) == []


def test_dip_below_a_call_wall_from_above_is_not_faded():
    assert find([(105, 106, 103, 104), (104, 105, 97, 101)], [("Call Wall", 100)]) == []
