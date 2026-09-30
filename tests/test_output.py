import csv
from datetime import date

import pandas as pd

from gex.levels import Levels
from gex.output import append_history, format_report, level_rows, to_futures, tradingview_string


def sample_levels(**overrides):
    values = dict(
        product="ES",
        index_label="SPX",
        asof=pd.Timestamp("2026-09-30 12:00", tz="UTC"),
        spot=7651.54,
        total_gex_bn=-27.4,
        regime="negative",
        flip=7681.2,
        call_wall=8000.0,
        put_wall=7500.0,
        next_expiry=date(2026, 10, 1),
        next_call_wall=7710.0,
        next_put_wall=7650.0,
        magnets=[7600.0, 7800.0],
        em_low=7604.54,
        em_high=7698.54,
        sources=["_SPX", "SPY"],
    )
    values.update(overrides)
    return Levels(**values)


def test_to_futures_rounds_to_quarter_tick():
    assert to_futures(7651.54, 40.3) == 7691.75
    assert to_futures(7651.54, None) == 7651.5


def test_level_rows_sorted_high_to_low_and_skip_missing():
    rows = level_rows(sample_levels(flip=None))
    names = [r.name for r in rows]
    prices = [r.index_price for r in rows]
    assert prices == sorted(prices, reverse=True)
    assert "Gamma Flip" not in names
    assert names[0] == "Call Wall"
    assert names[-1] == "Put Wall"
    assert "Magnet 1" in names and "Magnet 2" in names


def test_tradingview_string_in_futures_points():
    text = tradingview_string(sample_levels(), basis=40.0)
    parts = text.split(";")
    assert parts[0] == "8040=Call Wall"
    assert "7721.25=Gamma Flip" in parts
    assert parts[-1] == "7540=Put Wall"


def test_report_mentions_regime_price_and_levels():
    report = format_report(sample_levels(), basis=40.0)
    assert "NEGATIVE GAMMA" in report
    assert "8040" in report and "8000" in report
    assert "LAST PRICE" in report


def test_report_warns_without_basis():
    report = format_report(sample_levels(), basis=None)
    assert "--basis" in report


def test_append_history_writes_header_once(tmp_path):
    path = tmp_path / "hist" / "levels.csv"
    append_history(sample_levels(), 40.0, path)
    append_history(sample_levels(regime="positive"), 41.0, path)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["call_wall"] == "8040"
    assert rows[1]["regime"] == "positive"
    assert rows[1]["basis"] == "41.0"
