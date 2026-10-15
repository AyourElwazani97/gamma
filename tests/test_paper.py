import pandas as pd
import pytest

from gex.fakeouts import FailedBreakout
from gex.paper import simulate

NY = "America/New_York"
SIGNAL = pd.Timestamp("2026-10-15 10:00", tz=NY)


def after(rows):
    """5-minute bars starting right after the signal bar: (open, high, low, close)"""
    index = pd.date_range(SIGNAL, periods=len(rows) + 1, freq="5min", tz=NY)
    signal_bar = [(100, 101, 99, 100.5)]
    return pd.DataFrame(signal_bar + rows, index=index, columns=["Open", "High", "Low", "Close"])


def long(entry=101, stop=97, target=109.0):
    return FailedBreakout("Put Wall", 100, "long", SIGNAL, SIGNAL, entry, stop, "Magnet 1", target, True)


def short(entry=99, stop=103, target=91.0):
    return FailedBreakout("Call Wall", 100, "short", SIGNAL, SIGNAL, entry, stop, "Magnet 1", target, True)


def test_long_filled_then_target():
    t = simulate(long(), after([(100.5, 102, 100, 101.5), (101.5, 109.5, 101, 109)]))
    assert t.outcome == "target" and t.r == pytest.approx(2.0)  # 8 points on 4 of risk


def test_stop_before_the_entry_cancels():
    t = simulate(long(), after([(100.5, 100.8, 96.5, 97)]))
    assert t.outcome == "cancelled" and t.r == 0


def test_entry_not_filled_in_30_minutes_cancels():
    t = simulate(long(), after([(100.5, 100.8, 99.5, 100)] * 6 + [(100, 120, 100, 119)]))
    assert t.outcome == "cancelled"


def test_stop_moves_to_entry_after_one_risk():
    t = simulate(long(), after([(100.5, 105.5, 100, 105), (105, 105, 100.5, 101)]))  # +4.5 then back to entry
    assert t.outcome == "scratch" and t.r == 0


def test_bar_touching_stop_and_target_counts_the_stop():
    t = simulate(long(), after([(100.5, 101.5, 100, 101), (101, 110, 96, 100)]))
    assert t.outcome == "stop" and t.r == pytest.approx(-1.0)


def test_open_trade_closes_at_the_last_bar():
    t = simulate(long(), after([(100.5, 102, 100, 101.5), (101.5, 103, 101, 103)]))
    assert t.outcome == "close" and t.r == pytest.approx(0.5)


def test_short_mirrors_the_long():
    t = simulate(short(), after([(99.5, 100, 98, 98.5), (98.5, 99, 90.5, 91)]))
    assert t.outcome == "target" and t.r == pytest.approx(2.0)


def test_no_target_rides_to_the_close():
    t = simulate(long(target=None), after([(100.5, 102, 100, 101.5), (101.5, 112, 105.5, 111)]))
    assert t.outcome == "close" and t.r == pytest.approx(2.5)
