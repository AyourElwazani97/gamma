import numpy as np
import pandas as pd
import pytest

from conftest import ASOF, make_opts
from gex.config import PRODUCTS
from gex.data import Chain, parse_chain
from gex.greeks import bs_gamma, dollar_gamma
from gex.levels import (
    analyze,
    combine,
    expected_move,
    find_flip,
    gamma_profile,
    gex_by_strike,
    magnets,
    walls,
)


def chain_of(opts, spot, ticker="_SPX", iv30=15.0):
    cols = ["root", "expiry", "type", "strike", "iv", "oi", "bid", "ask", "multiplier"]
    return Chain(ticker=ticker, spot=spot, timestamp=ASOF, iv30=iv30, options=opts[cols].copy())


def test_gex_by_strike_signs_calls_positive_puts_negative():
    opts = make_opts([("C", 110, 1000, 0.2, 30), ("P", 90, 1000, 0.2, 30)])
    by_strike = gex_by_strike(opts, spot=100.0, step=5)
    assert by_strike.loc[110.0, "call_gex"] > 0
    assert by_strike.loc[90.0, "put_gex"] < 0
    assert by_strike.loc[110.0, "net_gex"] == by_strike.loc[110.0, "call_gex"]


def test_strikes_are_binned_to_step():
    opts = make_opts([("C", 101.2, 10, 0.2, 30), ("C", 99.1, 10, 0.2, 30)])
    by_strike = gex_by_strike(opts, spot=100.0, step=5)
    assert by_strike.index.tolist() == [100.0]


def test_walls_pick_largest_call_and_put():
    opts = make_opts(
        [("C", 105, 100, 0.2, 30), ("C", 110, 5000, 0.2, 30), ("P", 90, 5000, 0.2, 30), ("P", 95, 100, 0.2, 30)]
    )
    by_strike = gex_by_strike(opts, spot=100.0, step=5)
    assert walls(by_strike, spot=100.0) == (110.0, 90.0)


def test_walls_use_net_gex_so_one_strike_cannot_be_both():
    # 110 has huge calls and puts that mostly cancel; 105 is the real net call strike
    opts = make_opts(
        [
            ("C", 110, 50000, 0.2, 30),
            ("P", 110, 49000, 0.2, 30),
            ("C", 105, 3000, 0.2, 30),
            ("P", 95, 3000, 0.2, 30),
        ]
    )
    by_strike = gex_by_strike(opts, spot=100.0, step=5)
    assert walls(by_strike, spot=100.0) == (105.0, 95.0)


def test_walls_none_when_no_options_in_range():
    opts = make_opts([("C", 500, 100, 0.2, 30)])
    by_strike = gex_by_strike(opts, spot=100.0, step=5)
    assert walls(by_strike, spot=100.0) == (None, None)


def test_find_flip_interpolates_closest_crossing():
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([1.0, -1.0, -2.0, -1.0, 1.0, 2.0])
    # crossings at 0.5 and 3.5; spot 3 is closer to 3.5
    assert find_flip(x, y, spot=3.0) == pytest.approx(3.5)
    assert find_flip(x, y, spot=0.2) == pytest.approx(0.5)


def test_find_flip_none_without_crossing():
    x = np.linspace(0, 1, 5)
    assert find_flip(x, np.ones(5), spot=0.5) is None


def test_profile_flip_between_put_and_call_walls():
    opts = make_opts([("C", 105, 1000, 0.2, 30), ("P", 95, 1000, 0.2, 30)])
    grid, profile = gamma_profile(opts, spot=100.0)
    assert profile[0] < 0 < profile[-1]
    flip = find_flip(grid, profile, spot=100.0)
    assert 97 < flip < 103


def test_magnets_exclude_walls_and_rank_by_size():
    opts = make_opts(
        [
            ("C", 104, 9000, 0.2, 30),
            ("P", 96, 9000, 0.2, 30),
            ("C", 101, 3000, 0.2, 30),
            ("P", 99, 2000, 0.2, 30),
            ("C", 102, 1000, 0.2, 30),
            ("C", 103, 500, 0.2, 30),
        ]
    )
    by_strike = gex_by_strike(opts, spot=100.0, step=1)
    assert magnets(by_strike, spot=100.0, exclude=(104.0, 96.0)) == [101.0, 99.0, 102.0]


def test_expected_move_from_nearest_atm_straddle():
    opts = make_opts(
        [
            ("C", 100, 10, 0.2, 1, 9.0, 11.0),
            ("P", 100, 10, 0.2, 1, 7.0, 9.0),
            ("C", 105, 10, 0.2, 1, 5.0, 6.0),
            ("P", 105, 10, 0.2, 1, 9.0, 10.0),
            ("C", 100, 10, 0.2, 30, 40.0, 42.0),  # later expiry, ignored
            ("P", 100, 10, 0.2, 30, 40.0, 42.0),
        ]
    )
    assert expected_move(opts, spot=101.0, iv30=15.0) == pytest.approx((83.0, 119.0))


def test_expected_move_falls_back_to_iv30():
    opts = make_opts([("C", 100, 10, 0.2, 1)])  # no quotes
    low, high = expected_move(opts, spot=100.0, iv30=16.0)
    move = 100 * 0.16 / np.sqrt(252)
    assert (low, high) == pytest.approx((100 - move, 100 + move))


def test_etf_conversion_keeps_dollar_gamma():
    index = chain_of(make_opts([("C", 7650, 1, 0.15, 10)]), spot=7651.54)
    etf = chain_of(make_opts([("C", 765, 10, 0.15, 10)]), spot=765.154, ticker="SPY")

    opts = combine(index, etf)
    etf_row = opts[opts["source"] == "etf"].iloc[0]
    assert etf_row["strike"] == pytest.approx(7650.0)

    t = etf_row["t"]
    direct = dollar_gamma(bs_gamma(765.154, 765.0, 0.15, t), 10, 100, 765.154)
    converted = dollar_gamma(
        bs_gamma(7651.54, etf_row["strike"], 0.15, t), 10, etf_row["multiplier"], 7651.54
    )
    assert converted == pytest.approx(direct)


def test_combine_computes_time_from_chain_timestamp():
    index = chain_of(make_opts([("C", 7650, 1, 0.15, 2)]), spot=7651.54)
    opts = combine(index)
    assert opts["t"].iloc[0] == pytest.approx(2 / 365)
    assert opts["source"].tolist() == ["index"]


def test_next_expiry_walls_use_only_nearest_expiry():
    opts = make_opts(
        [
            ("C", 105, 1000, 0.2, 1),
            ("P", 95, 1000, 0.2, 1),
            ("C", 110, 90000, 0.2, 30),
            ("P", 90, 90000, 0.2, 30),
        ]
    )
    result = analyze(PRODUCTS["ES"], chain_of(opts, spot=100.0))
    lv = result.levels
    assert (lv.next_call_wall, lv.next_put_wall) == (105.0, 95.0)
    assert (lv.call_wall, lv.put_wall) == (110.0, 90.0)
    assert lv.next_expiry == (ASOF + pd.Timedelta(days=1)).tz_convert("America/New_York").date()


def test_analyze_real_ndx_fixture(ndx_raw):
    result = analyze(PRODUCTS["NQ"], parse_chain(ndx_raw, "_NDX"))
    lv = result.levels
    spot = lv.spot
    assert lv.regime in ("positive", "negative")
    assert (lv.total_gex_bn > 0) == (lv.regime == "positive")
    for level in [lv.call_wall, lv.put_wall, lv.next_call_wall, lv.next_put_wall, *lv.magnets]:
        assert abs(level - spot) <= spot * 0.15
    assert lv.flip is None or abs(lv.flip - spot) <= spot * 0.10
    assert lv.em_low < spot < lv.em_high
    assert len(result.profile_x) == len(result.profile_y)
