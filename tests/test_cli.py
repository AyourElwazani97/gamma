import csv

from conftest import FIXTURES
from gex.cli import main


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
