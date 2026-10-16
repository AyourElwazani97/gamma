"""The NinjaTrader strategy compiles against NinjaTrader 8 and finds the same failed breakouts as gex/fakeouts.py.

Runs only where NinjaTrader 8 is installed (it compiles with NinjaTrader's own Roslyn).
"""
import os
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from gex.bars import load_bars
from gex.data import NEW_YORK
from gex.fakeouts import failed_breakouts
from gex.output import fmt
from gex.reactions import TOLERANCE_PCT, levels_from_row
from gex.review import load_history, session_bars, session_date

ROOT = Path(__file__).resolve().parent.parent
NT_BIN = Path(r"C:\Program Files\NinjaTrader 8\bin")
CUSTOM = Path(os.path.expanduser("~")) / "Documents" / "NinjaTrader 8" / "bin" / "Custom" / "NinjaTrader.Custom.dll"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
STRATEGY = ROOT / "ninjatrader" / "GexFailedBreakout.cs"
FOOTPRINT = ROOT / "ninjatrader" / "GexFootprint.cs"
TOOLS = ROOT / "ninjatrader" / "tools"

pytestmark = pytest.mark.skipif(
    not (NT_BIN.exists() and CUSTOM.exists() and CSC.exists()), reason="needs NinjaTrader 8 on Windows"
)


@pytest.fixture(scope="module")
def ntcheck(tmp_path_factory):
    out = tmp_path_factory.mktemp("nt") / "NtCheck.exe"
    refs = [NT_BIN / "Microsoft.CodeAnalysis.dll", NT_BIN / "Microsoft.CodeAnalysis.CSharp.dll",
            NT_BIN / "System.Collections.Immutable.dll", CSC.parent / "netstandard.dll"]  # fmt: skip
    cmd = [str(CSC), "-nologo", "-platform:x64", f"-out:{out}", *[f"-r:{r}" for r in refs], str(TOOLS / "NtCheck.cs")]
    subprocess.run(cmd, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return out


def compile_(ntcheck, out, kind, *files):
    cmd = [str(ntcheck), str(out), kind, *map(str, files)]
    done = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return done.returncode, done.stdout


def test_strategy_compiles_against_ninjatrader(ntcheck):
    code, output = compile_(ntcheck, "-", "dll", STRATEGY)
    assert code == 0, output
    assert "warning" not in output, output


def python_signals():
    lines = []
    for row in load_history(ROOT / "history" / "levels_history.csv"):
        day = session_date(pd.Timestamp(row["asof_ny"], tz=NEW_YORK))
        path = ROOT / "history" / "bars" / f"{row['product']}_{day}.csv"
        if not path.exists():
            continue
        bars = session_bars(load_bars(path), day)
        if bars.empty:
            continue
        flip = float(row["flip"]) if row["flip"] else None
        tolerance = float(bars["Open"].iloc[0]) * TOLERANCE_PCT
        for s in failed_breakouts(bars, levels_from_row(row), flip, tolerance=tolerance):
            risk = abs(s.stop - s.entry)
            reward = None if s.target is None else abs(s.entry - s.target)
            code = "SKIP-flip" if s.positive_gamma is False else "SKIP-rr" if reward is not None and reward < risk else "OK"
            target = "" if s.target is None else fmt(s.target)
            lines.append("|".join([row["product"], f"{s.time:%Y-%m-%d %H:%M}", s.direction, s.name, fmt(s.price),
                                   fmt(s.entry), fmt(s.stop), target, s.target_name or "", code]))  # fmt: skip
    return sorted(lines)


def test_strategy_finds_the_same_failed_breakouts_as_python(ntcheck, tmp_path):
    exe = tmp_path / "Parity.exe"
    code, output = compile_(ntcheck, exe, "exe", STRATEGY, TOOLS / "Parity.cs")
    assert code == 0, output
    done = subprocess.run([str(exe), str(ROOT)], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    assert sorted(done.stdout.splitlines()) == python_signals()


def test_footprint_compiles_against_ninjatrader(ntcheck):
    code, output = compile_(ntcheck, "-", "dll", STRATEGY, FOOTPRINT)  # it reads the levels with the strategy's code
    assert code == 0, output
    assert "warning" not in output, output


def test_footprint_math(ntcheck, tmp_path):
    exe = tmp_path / "FootprintCheck.exe"
    code, output = compile_(ntcheck, exe, "exe", STRATEGY, FOOTPRINT, TOOLS / "FootprintCheck.cs")
    assert code == 0, output
    done = subprocess.run([str(exe)], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    assert done.stdout.splitlines() == [
        "side T F T F F T",
        "bar 0 0 1 2 2 -1 -1",
        "rows1 100:5/20:-- 101:70/10:B- 102:8/30:-S 103:2/0:--",
        "rows2 100:75/30:-S 102:10/30:--",
        "gap 100:9/0:-- 102:3/0:B-",
        "abs low 100-101 140",  # heavy selling at the low, closed in the upper half
        "abs none",  # closed in the middle
        "abs high 109-110 140",
        "abs none",  # more buyers than sellers at the low
        "abs none",  # bar too small
        "abs none",  # not heavy enough
        "rowsize 1 2 4 8 20 2000",
        "two low 97-100 500",  # sold hard, then no follow-through: absorbed over two bars
        "two none",  # went too far below the first low
        "two none",  # selling not heavy enough
        "two none",  # second bar kept selling
        "two none",  # first bar closed in its upper half
        "two high 120-123 500",
        "day 10-16 10-16 10-15 10-19",  # the evening session from 18:00 goes to the next day's file
        "line 2026-10-16 13:20:00-04:00,7697.5,7704.75,7695.5,7704.25,34506,18007,16499,1508",
        "line 2026-12-01 09:30:00-05:00,7800,7801,7799.25,7800.5,10,4,6,-2",
    ]
