import pandas as pd
import pytest

from gex.data import parse_chain


def raw_chain(options, timestamp="2026-09-30 13:00:00", spot=7651.54):
    return {
        "timestamp": timestamp,
        "symbol": "_SPX",
        "data": {"symbol": "^SPX", "current_price": spot, "iv30": 13.4, "options": options},
    }


def opt(symbol, oi=100.0, iv=0.15, bid=1.0, ask=1.2):
    return {"option": symbol, "open_interest": oi, "iv": iv, "bid": bid, "ask": ask, "gamma": 0.0}


def test_parse_symbol_fields_and_expiry_times():
    chain = parse_chain(
        raw_chain([opt("SPXW260930C07650000"), opt("SPX261016P07600500")]), "_SPX"
    )
    df = chain.options.set_index("root")

    assert chain.spot == 7651.54
    assert chain.iv30 == 13.4
    assert chain.timestamp == pd.Timestamp("2026-09-30 13:00:00", tz="UTC")

    assert df.loc["SPXW", "type"] == "C"
    assert df.loc["SPXW", "strike"] == 7650.0
    # PM-settled weekly: 16:00 New York
    assert df.loc["SPXW", "expiry"] == pd.Timestamp("2026-09-30 16:00", tz="America/New_York")

    assert df.loc["SPX", "type"] == "P"
    assert df.loc["SPX", "strike"] == 7600.5
    # AM-settled monthly: 09:30 New York
    assert df.loc["SPX", "expiry"] == pd.Timestamp("2026-10-16 09:30", tz="America/New_York")
    assert (df["multiplier"] == 100.0).all()


def test_drops_expired_zero_oi_zero_iv_and_bad_symbols():
    chain = parse_chain(
        raw_chain(
            [
                opt("SPXW260930C07650000"),  # live until 16:00 ET today
                opt("SPXW260929C07650000"),  # expired yesterday
                opt("SPXW261001C07650000", oi=0),
                opt("SPXW261001P07650000", iv=0),
                opt("garbage"),
            ]
        ),
        "_SPX",
    )
    assert chain.options["root"].tolist() == ["SPXW"]


def test_same_day_expiry_dropped_after_close():
    # 21:00 UTC = 17:00 New York, after the 16:00 expiry
    chain = parse_chain(raw_chain([opt("SPXW260930C07650000")], timestamp="2026-09-30 21:00:00"), "_SPX")
    assert chain.options.empty


def test_parses_real_ndx_fixture(ndx_raw):
    chain = parse_chain(ndx_raw, "_NDX")
    assert chain.spot == pytest.approx(18052.9629)
    assert len(chain.options) > 1000
    assert chain.options["strike"].notna().all()
    assert (chain.options["expiry"] > chain.timestamp).all()
    assert set(chain.options["type"]) == {"C", "P"}
