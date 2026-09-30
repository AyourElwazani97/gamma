import pandas as pd

from gex.reactions import level_reactions, levels_from_row

NY = "America/New_York"


def make_bars(rows, start="2026-09-30 09:30"):
    """rows: (open, high, low, close) as consecutive 5-minute bars"""
    index = pd.date_range(start, periods=len(rows), freq="5min", tz=NY)
    return pd.DataFrame(rows, index=index, columns=["Open", "High", "Low", "Close"])


def react(rows, level=100.0):
    return level_reactions(make_bars(rows), [("Level", level)], threshold=5, tolerance=1, window=4)[0]


def test_support_held():
    r = react([(110, 111, 105, 106), (106, 107, 100.5, 102), (102, 104, 101, 103), (103, 108, 102, 107)])
    assert r.side == "support"
    assert r.outcome == "held"
    assert r.first_touch == pd.Timestamp("2026-09-30 09:35", tz=NY)
    assert r.bounce == 8
    assert r.through == 0


def test_support_broke():
    r = react([(110, 110, 104, 105), (105, 105, 99, 100), (100, 101, 94, 95)])
    assert (r.side, r.outcome) == ("support", "broke")
    assert r.through == 6


def test_resistance_held():
    r = react([(90, 95, 89, 94), (94, 100.5, 93, 99), (99, 99, 93, 94)])
    assert (r.side, r.outcome) == ("resistance", "held")
    assert r.bounce == 7


def test_stalled_when_neither_threshold_hit():
    r = react([(104, 105, 101, 102), (102, 103, 99, 101), (101, 103, 98, 102), (102, 104, 99, 100)])
    assert r.outcome == "stalled"


def test_not_reached():
    r = react([(110, 120, 108, 115), (115, 118, 110, 112)])
    assert (r.touched, r.outcome, r.touches) == (False, "not reached", 0)


def test_counts_separate_touches():
    r = react([(105, 106, 100, 104), (104, 110, 103, 109), (109, 110, 108, 108), (108, 109, 100.5, 101)])
    assert r.touches == 2


def test_window_limits_reaction():
    # bounce of 10 happens after the 4-bar window -> stalled
    r = react([(104, 105, 100, 102), (102, 103, 101, 102), (102, 103, 101, 102), (102, 103, 101, 102), (102, 110, 102, 110)])
    assert r.outcome == "stalled"


def test_levels_from_row_sorted_merged_and_skips_blanks():
    row = {
        "call_wall": "8059.75", "next_call_wall": "7769.75", "em_high": "7758.25", "flip": "",
        "magnet_1": "7659.75", "magnet_2": "7709.75", "magnet_3": "", "em_low": "7664.25",
        "next_put_wall": "7709.75", "put_wall": "7559.75",
    }  # fmt: skip
    assert levels_from_row(row) == [
        ("Call Wall", 8059.75),
        ("0DTE Call Wall", 7769.75),
        ("Expected Move High", 7758.25),
        ("Magnet 2 + 0DTE Put Wall", 7709.75),
        ("Expected Move Low", 7664.25),
        ("Magnet 1", 7659.75),
        ("Put Wall", 7559.75),
    ]
