import numpy as np
import pytest

from gex.greeks import bs_gamma, dollar_gamma, touch_probability


def test_atm_gamma_matches_black_scholes():
    # S=K=100, vol 20%, 1 year: d1 = 0.1, gamma = pdf(0.1) / (100 * 0.2)
    expected = np.exp(-0.1**2 / 2) / np.sqrt(2 * np.pi) / 20
    assert bs_gamma(100.0, 100.0, 0.2, 1.0) == pytest.approx(expected)


def test_gamma_is_zero_for_bad_inputs():
    out = bs_gamma(100.0, np.array([100.0, 100.0, 100.0]), np.array([0.0, 0.2, 0.2]), np.array([1.0, 0.0, -1.0]))
    assert out.tolist() == [0.0, 0.0, 0.0]


def test_gamma_broadcasts_over_spot_grid():
    spots = np.array([[90.0], [100.0], [110.0]])
    strikes = np.array([95.0, 100.0, 105.0, 110.0])
    out = bs_gamma(spots, strikes, 0.2, 0.25)
    assert out.shape == (3, 4)
    # ATM option has the highest gamma on each row's own strike
    assert out[1].argmax() == 1


def test_dollar_gamma_per_one_percent_move():
    # gamma 0.01, 10 contracts x 100, spot 100 -> 0.01 * 1000 * 10000 * 0.01
    assert dollar_gamma(0.01, 10, 100, 100.0) == pytest.approx(1000.0)


def test_touch_probability_is_twice_the_chance_of_finishing_beyond():
    # Level 1 sd above spot (sd 1 point = 1% of 100): 2 * N(-1)
    assert touch_probability(100.0, 100.0 * np.exp(0.01), sd=1.0) == pytest.approx(0.3173, abs=1e-4)


def test_touch_probability_same_both_sides_in_log_terms():
    up = touch_probability(100.0, 100.0 * np.exp(0.015), sd=1.0)
    down = touch_probability(100.0, 100.0 * np.exp(-0.015), sd=1.0)
    assert up == pytest.approx(down)


def test_touch_probability_certain_at_spot_and_none_without_sd():
    assert touch_probability(100.0, 100.0, sd=1.0) == 1.0
    assert touch_probability(100.0, 101.0, sd=None) is None
    assert touch_probability(100.0, 101.0, sd=0.0) is None
