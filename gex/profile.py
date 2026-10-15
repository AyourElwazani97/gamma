"""Volume profile of a session from 5-minute bars, and the GEX levels that sit on it.

Each bar's volume is spread evenly over its high-low range, one row per tick. On Oct 14, 2026 this
came within 0.5-2 ES points of NinjaTrader's tick-based FreeOrderFlow profile. A level close to the
prior session's POC or value-area edge has two reasons to matter: options hedging and traded volume.
"""
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from gex.config import TICK
from gex.output import fmt

VALUE_AREA = 0.7
CONFLUENCE_PCT = 0.0006  # within 0.06% of a profile line (~5 ES, ~18 NQ points)


@dataclass
class Profile:
    poc: float  # price with the most volume
    vah: float  # top of the value area (70% of the volume)
    val: float  # bottom of the value area


def value_area(prices, volumes, area=VALUE_AREA):
    """(POC, VAH, VAL) the way FreeOrderFlow computes them: from the POC, add two rows at a time from
    whichever side has more volume (ties go down) until `area` of the volume is inside. Rows looked at
    but not added still count for the edges, as in FreeOrderFlow."""
    total = float(sum(volumes))
    poc = int(np.argmax(volumes))
    inside, high, low, up, down = float(volumes[poc]), poc, poc, 0.0, 0.0
    while inside < total * area:
        if up == 0:
            for _ in range(2):
                if high < len(prices) - 1:
                    high += 1
                    up += volumes[high]
        if down == 0:
            for _ in range(2):
                if low > 0:
                    low -= 1
                    down += volumes[low]
        if up > down:
            inside, up = inside + up, 0.0
        else:
            inside, down = inside + down, 0.0
    return prices[poc], prices[high], prices[low]


def volume_profile(bars, tick=TICK, area=VALUE_AREA):
    """Profile of `bars` (one session), or None without volume."""
    if bars.empty or "Volume" not in bars or float(bars["Volume"].sum()) <= 0:
        return None
    rows = {}
    for low, high, volume in zip(bars["Low"], bars["High"], bars["Volume"]):
        ticks = np.arange(round(low / tick), round(high / tick) + 1)
        for t in ticks:
            rows[t] = rows.get(t, 0.0) + volume / len(ticks)
    keys = sorted(rows)
    poc, vah, val = value_area([k * tick for k in keys], [rows[k] for k in keys], area)
    return Profile(poc=float(poc), vah=float(vah), val=float(val))


def confluences(levels, profile, tolerance):
    """[(level name, level price, profile line, line price)] for levels within `tolerance` of the nearest line."""
    lines = [("POC", profile.poc), ("VAH", profile.vah), ("VAL", profile.val)]
    found = []
    for name, price in levels:
        line, at = min(lines, key=lambda lp: abs(lp[1] - price))
        if abs(at - price) <= tolerance:
            found.append((name, price, line, at))
    return found


def prior_trading_day(day):
    """The weekday before `day` (exchange holidays are not known here)."""
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def format_confluence(day, profile, matches, tolerance):
    head = f"{day:%a %b %d} volume profile: POC {fmt(profile.poc)}, VAH {fmt(profile.vah)}, VAL {fmt(profile.val)}"
    if not matches:
        return f"{head}\n  Confluence: no level within {fmt(tolerance)} points of them"
    pairs = " | ".join(f"{n} {fmt(p)} ~ {line} {fmt(at)} ({fmt(abs(at - p))} away)" for n, p, line, at in matches)
    return f"{head}\n  Confluence: {pairs}"
