import csv

import json

import pandas as pd

from conftest import FIXTURES
from gex.cli import live_spot, main
from gex.config import PRODUCTS
from gex.data import parse_chain

NY = "America/New_York"


def closed_ndx(raw):
    """The fixture as CBOE serves it before the open: index price = the last close."""
    raw["data"]["last_trade_time"] = "2024-04-02T16:15:00"
    return raw


def test_full_run_on_fixture(tmp_path, capsys):
    history = tmp_path / "history.csv"
    code = main(
        [
            "NQ",
            "--from-dir", str(FIXTURES),
            "--no-etf",
            "--basis", "75",
            "--out", str(tmp_path),
            "--history", str(history),
        ]
    )
    assert code == 0

    out = capsys.readouterr().out
    assert "NQ" in out and "LAST PRICE" in out
    assert (tmp_path / "NQ_2024-04-03.png").stat().st_size > 10_000
    tv = (tmp_path / "NQ_tradingview.txt").read_text()
    assert "Call Wall" in tv and tv in out
    with open(history, newline="") as f:
        assert len(list(csv.DictReader(f))) == 1


def test_unknown_product_fails(capsys):
    assert main(["XX", "--no-history"]) == 2
    assert "Unknown product" in capsys.readouterr().err


def test_missing_chain_file_fails_cleanly(tmp_path, capsys):
    assert main(["ES", "--from-dir", str(tmp_path), "--basis", "0", "--no-history"]) == 1
    assert "_SPX" in capsys.readouterr().err


def test_report_shows_the_prior_session_volume_profile(tmp_path, capsys):
    import pandas as pd

    from gex.bars import save_bars

    prior = pd.DataFrame(
        {"Open": [18100.0], "High": [18100.0], "Low": [18100.0], "Close": [18100.0], "Volume": [500.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-04-02 10:00", tz="America/New_York")]),
    )
    save_bars(prior, tmp_path / "bars" / "NQ_2024-04-02.csv")  # fixture levels are for Wed Apr 3
    args = ["NQ", "--from-dir", str(FIXTURES), "--no-etf", "--basis", "75", "--out", str(tmp_path)]
    assert main(args + ["--history", str(tmp_path / "history.csv")]) == 0
    assert "Tue Apr 02 volume profile: POC 18100, VAH 18100, VAL 18100" in capsys.readouterr().out


def test_live_futures_price_while_the_index_is_closed(ndx_raw):
    chain = parse_chain(closed_ndx(ndx_raw), "_NDX")
    spot, note = live_spot(PRODUCTS["NQ"], chain, 75.0, (17900.0, pd.Timestamp("2024-04-03 08:00", tz=NY)))
    assert spot == 17825.0
    assert "live NQ futures 17900 at Wed 08:00 NY" in note


def test_cboe_price_while_the_index_trades(ndx_raw):
    chain = parse_chain(ndx_raw, "_NDX")
    assert live_spot(PRODUCTS["NQ"], chain, 75.0, (17900.0, pd.Timestamp("2024-04-03 09:47", tz=NY))) == (None, None)


def test_warns_when_the_index_is_closed_and_no_live_price(ndx_raw):
    spot, note = live_spot(PRODUCTS["NQ"], parse_chain(closed_ndx(ndx_raw), "_NDX"), 75.0, None)
    assert spot is None
    assert note.startswith("WARNING") and "close" in note


def test_pre_open_run_prices_off_live_futures(tmp_path, monkeypatch, capsys, ndx_raw):
    import gex.cli as cli

    raw = closed_ndx(ndx_raw)
    monkeypatch.setattr(cli, "load_chain", lambda ticker, from_dir=None: parse_chain(raw, ticker))
    when = pd.Timestamp("2024-04-03 08:00", tz=NY)
    monkeypatch.setattr(cli, "fetch_futures", lambda product: ((75.0, when), (17900.0, when)))
    monkeypatch.setattr(cli, "fetch_bars", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    history = tmp_path / "history.csv"
    assert main(["NQ", "--no-etf", "--out", str(tmp_path), "--history", str(history)]) == 0
    out = capsys.readouterr().out
    assert "live NQ futures 17900" in out
    assert "17900" in next(line for line in out.splitlines() if "LAST PRICE" in line)
    with open(history, newline="") as f:
        row = next(csv.DictReader(f))
    assert row["fut_spot"] == "17900" and row["index_spot"] == "17825"


def test_offline_replay_of_a_closed_index_warns(tmp_path, capsys, ndx_raw):
    (tmp_path / "_NDX.json").write_text(json.dumps(closed_ndx(ndx_raw)))
    args = ["NQ", "--from-dir", str(tmp_path), "--no-etf", "--basis", "75", "--out", str(tmp_path), "--no-history"]
    assert main(args) == 0
    assert "WARNING: NDX is closed" in capsys.readouterr().out
