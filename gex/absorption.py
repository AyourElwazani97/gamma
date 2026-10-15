"""Absorption at a GEX level, as the NinjaTrader footprint (ninjatrader/GexFootprint.cs) logs it to
history/absorption.csv.

Heavy selling into a level that could not push price lower (buyers soaking it up): go long on the break of the
signal bar's high, stop under the absorbed low, next level as target. The mirror image at the high for shorts.
Inside one bar (heavy selling at its low, closed in its upper half) or over two (a bar sells hard, the next can't
follow through, turns to buying and closes in its upper half): each is scored on its own.
A test setup. The footprint needs tick data, so only sessions a NinjaTrader chart was open for have signals.
"""
import csv
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from gex.data import NEW_YORK
from gex.fakeouts import _target

COLUMNS = ("product", "time_ny", "minutes", "bars", "direction", "level", "level_price", "low", "high", "close",
           "effort", "bar_volume", "bar_delta")  # fmt: skip


@dataclass
class Absorption:
    name: str  # the level
    price: float
    bars: int  # 1 = inside one bar, 2 = over two
    direction: str  # "long" (sellers absorbed at the low) / "short" (buyers absorbed at the high)
    time: pd.Timestamp  # 5-minute bar the signal bar ended in: the paper trade starts after it
    entry: float  # break of the signal bar's high (long) / low (short)
    stop: float  # beyond the absorbed low (long) / high (short)
    target_name: str | None
    target: float | None
    positive_gamma: bool | None  # signal close above the Gamma Flip
    effort: float  # 1 bar: volume sold into the low / bought into the high; 2 bars: size of the first bar's delta


def load_absorptions(path):
    """Rows of absorption.csv; [] if the footprint never logged any."""
    if not Path(path).exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def absorptions(rows, product, day, levels, flip):
    """Setups logged for one product and session, one per 5-minute bar, pattern, level and direction (a 1- and a
    5-minute chart open at once log the same absorption twice)."""
    setups, seen = [], set()
    for r in sorted(rows, key=lambda r: _end(r)):
        start = pd.Timestamp(r["time_ny"], tz=NEW_YORK)
        if r["product"] != product or start.date() != day:
            continue
        time = (_end(r) - pd.Timedelta(minutes=1)).floor("5min")
        key = (time, r["bars"], r["direction"], r["level"])
        if key in seen:
            continue
        seen.add(key)
        long = r["direction"] == "long"
        entry, stop = (float(r["high"]), float(r["low"])) if long else (float(r["low"]), float(r["high"]))
        target_name, target = _target(levels, entry, r["direction"])
        close = float(r["close"])
        setups.append(
            Absorption(
                name=r["level"],
                price=float(r["level_price"]),
                bars=int(r["bars"]),
                direction=r["direction"],
                time=time,
                entry=entry,
                stop=stop,
                target_name=target_name,
                target=target,
                positive_gamma=None if flip is None else close > flip,
                effort=float(r["effort"]),
            )
        )
    return setups


def _end(row):
    """When the signal bar closed."""
    return pd.Timestamp(row["time_ny"], tz=NEW_YORK) + pd.Timedelta(minutes=int(row["minutes"]))
