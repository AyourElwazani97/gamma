import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from gex.absorption import COLUMNS, absorptions, load_absorptions
from gex.review import ABSORB, format_review, grade

NY = "America/New_York"
ROOT = Path(__file__).resolve().parent.parent
LEVELS = [("Call Wall", 7800.0), ("Gamma Flip", 7740.0), ("Put Wall", 7650.0)]


def ts(text):
    return pd.Timestamp(text, tz=NY)


def logged(**overrides):
    row = {"product": "ES", "time_ny": "2026-09-30 10:00", "minutes": "5", "direction": "long", "level": "Put Wall",
           "level_price": "7650", "low": "7649", "high": "7656", "close": "7655", "extreme_volume": "1200",
           "bar_volume": "9000", "bar_delta": "-800"}  # fmt: skip
    row.update(overrides)
    return row


def test_absorption_at_the_low_is_a_long_on_the_break_of_the_bar_high():
    [s] = absorptions([logged()], "ES", date(2026, 9, 30), LEVELS, flip=7740.0)
    assert (s.name, s.direction, s.entry, s.stop) == ("Put Wall", "long", 7656, 7649)
    assert (s.target_name, s.target) == ("Gamma Flip", 7740.0)
    assert s.time == ts("2026-09-30 10:00")  # the paper trade starts with the 10:05 bar
    assert s.positive_gamma is False and s.volume == 1200


def test_absorption_at_the_high_is_a_short_on_the_break_of_the_bar_low():
    row = logged(direction="short", level="Call Wall", level_price="7800", low="7794", high="7801", close="7795")
    [s] = absorptions([row], "ES", date(2026, 9, 30), LEVELS, flip=7740.0)
    assert (s.direction, s.entry, s.stop, s.target) == ("short", 7794, 7801, 7740.0)
    assert s.positive_gamma is True


def test_one_absorption_per_five_minute_bar_level_and_direction():
    rows = [
        logged(),
        logged(time_ny="2026-09-30 10:02", minutes="1", low="7649.5", high="7653"),  # 1-minute chart, same 5 minutes
        logged(time_ny="2026-09-30 10:05", minutes="1", low="7650", high="7652"),  # ends 10:06: the next 5 minutes
        logged(product="NQ"),
        logged(time_ny="2026-10-01 10:00"),
    ]
    setups = absorptions(rows, "ES", date(2026, 9, 30), LEVELS, flip=None)
    assert [(s.time, s.entry) for s in setups] == [(ts("2026-09-30 10:00"), 7653), (ts("2026-09-30 10:05"), 7652)]
    assert setups[0].positive_gamma is None


def test_no_log_no_absorptions(tmp_path):
    assert load_absorptions(tmp_path / "absorption.csv") == []


def test_footprint_logs_the_columns_review_reads():
    source = (ROOT / "ninjatrader" / "GexFootprint.cs").read_text()
    header = re.search(r'LogHeader = "([^"]+)"', source).group(1)
    assert tuple(header.split(",")) == COLUMNS


def history_row():
    return {"asof_ny": "2026-09-30 08:00", "product": "ES", "regime": "positive", "flip": "7740",
            "call_wall": "7800", "put_wall": "7650", "em_low": "7660", "em_high": "7760"}  # fmt: skip


def test_grade_paper_trades_logged_absorptions():
    index = pd.DatetimeIndex([ts(f"2026-09-30 {t}") for t in ("09:30", "10:00", "10:05", "10:10")])
    bars = pd.DataFrame(
        [(7700, 7705, 7690, 7695),
         (7660, 7662, 7649, 7655),  # the absorption bar at the 7650 put wall
         (7655, 7658, 7654, 7657),  # breaks its 7656 high: long filled
         (7657, 7745, 7656, 7744)],  # reaches the 7660 target (Expected Move Low, the next level up)
        index=index, columns=["Open", "High", "Low", "Close"],
    )  # fmt: skip
    g = grade(history_row(), bars, [logged()])
    [(kind, setup, trade)] = [t for t in g["trades"] if t[0] == ABSORB]
    assert (setup.entry, setup.stop, setup.target) == (7656, 7649, 7660.0)
    assert trade.outcome == "target" and trade.r == pytest.approx(4 / 7)
    assert ABSORB in format_review("ES", [g])
