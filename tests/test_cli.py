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
