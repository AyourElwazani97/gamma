"""Second touch of a 0DTE wall: the double bottom / double top at today's expiry walls.

Price touches a 0DTE wall, bounces away, comes back for a second test and closes back on the
same side: trade the second bounce, stop beyond the retest. A test setup, not proven yet.
With an RSI series it also reports a divergence: the retest goes as far or further than the first touch
while RSI doesn't (sellers / buyers running out at the wall).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from gex.fakeouts import FAIL_WINDOW_BARS, _target

RSI_PERIOD = 14


@dataclass
class SecondTouch:
    name: str
    price: float
    direction: str  # "long" (level was support) / "short" (level was resistance)
    first_touch: pd.Timestamp
    time: pd.Timestamp  # bar of the second touch that closed back on the original side: the signal
    entry: float  # break of the signal bar's high (long) / low (short)
    stop: float  # beyond the furthest point of the retest
    target_name: str | None
    target: float | None
    positive_gamma: bool | None  # signal close above the Gamma Flip
    rsi_first: float | None = None  # RSI at the first touch's extreme bar
    rsi_second: float | None = None  # RSI at the retest's extreme bar
    divergence: bool | None = None  # None without RSI


def rsi(closes, period=RSI_PERIOD):
    """Wilder's RSI of a close series; NaN until `period` changes are in."""
    c = closes.to_numpy(dtype=float)
    out = np.full(len(c), np.nan)
    if len(c) > period:
        change = np.diff(c)
        gain, loss = np.clip(change, 0, None), np.clip(-change, 0, None)
        avg_gain, avg_loss = gain[:period].mean(), loss[:period].mean()
        for i in range(period, len(c)):
            if i > period:
                avg_gain = (avg_gain * (period - 1) + gain[i - 1]) / period
                avg_loss = (avg_loss * (period - 1) + loss[i - 1]) / period
            if avg_loss == 0:
                out[i] = 100.0 if avg_gain > 0 else 50.0
            else:
                out[i] = 100 - 100 / (1 + avg_gain / avg_loss)
    return pd.Series(out, index=closes.index)


def _value_at(series, when):
    if series is None:
        return None
    value = series.get(when)
    return None if value is None or pd.isna(value) else float(value)


def second_touches(bars, levels, flip, tolerance, threshold, window=FAIL_WINDOW_BARS, rsi_series=None):
    """First second-touch setup at each 0DTE wall in one session of bars.

    A touch comes within `tolerance` of the level; the first touch must bounce `threshold` away
    before going `threshold` through; the second touch must not go `threshold` through and must
    close back on the original side within `window` bars. `rsi_series`: RSI covering the bars' times.
    """
    o, h, l, c = (bars[col].to_numpy(dtype=float) for col in ("Open", "High", "Low", "Close"))
    setups = []
    for name, level in levels:
        if "0DTE" not in name:
            continue
        state, support, first, second, extreme = "wait", True, None, None, None
        first_extreme, first_at, second_at = None, None, None
        for i in range(len(c)):
            near = l[i] <= level + tolerance and h[i] >= level - tolerance
            through = level - l[i] if support else h[i] - level
            if state == "wait":
                if not near:
                    continue
                before = c[i - 1] if i > 0 else o[i]
                if before == level:
                    continue
                support, first, state = before > level, i, "first"
                first_extreme, first_at = (l[i] if support else h[i]), i
                through = level - l[i] if support else h[i] - level
                if through >= threshold:
                    break  # the first touch broke the level
                continue
            if through >= threshold:
                break  # broke before a bounce, or the retest broke it
            if state == "first":
                if l[i] < first_extreme if support else h[i] > first_extreme:
                    first_extreme, first_at = (l[i] if support else h[i]), i
                if (h[i] - level if support else level - l[i]) >= threshold:
                    state = "bounced"
                continue
            if state == "bounced":
                if not (l[i] <= level + tolerance if support else h[i] >= level - tolerance):
                    continue
                state, second, extreme, second_at = "second", i, (l[i] if support else h[i]), i
            if l[i] < extreme if support else h[i] > extreme:
                extreme, second_at = (l[i] if support else h[i]), i
            if c[i] > level if support else c[i] < level:
                direction = "long" if support else "short"
                entry = h[i] if support else l[i]
                target_name, target = _target(levels, entry, direction)
                rsi_first = _value_at(rsi_series, bars.index[first_at])
                rsi_second = _value_at(rsi_series, bars.index[second_at])
                divergence = None
                if rsi_first is not None and rsi_second is not None:
                    as_far = extreme <= first_extreme + tolerance if support else extreme >= first_extreme - tolerance
                    divergence = bool(as_far and (rsi_second > rsi_first if support else rsi_second < rsi_first))
                setups.append(
                    SecondTouch(
                        name=name,
                        price=level,
                        direction=direction,
                        first_touch=bars.index[first],
                        time=bars.index[i],
                        entry=float(entry),
                        stop=float(extreme),
                        target_name=target_name,
                        target=target,
                        positive_gamma=None if flip is None else bool(c[i] > flip),
                        rsi_first=rsi_first,
                        rsi_second=rsi_second,
                        divergence=divergence,
                    )
                )
                break
            if i - second >= window - 1:
                break  # the retest didn't close back in time
    return setups
