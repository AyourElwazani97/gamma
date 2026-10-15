"""Second touch of a 0DTE wall: the double bottom / double top at today's expiry walls.

Price touches a 0DTE wall, bounces away, comes back for a second test and closes back on the
same side: trade the second bounce, stop beyond the retest. A test setup, not proven yet.
"""
from dataclasses import dataclass

import pandas as pd

from gex.fakeouts import FAIL_WINDOW_BARS, _target


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


def second_touches(bars, levels, flip, tolerance, threshold, window=FAIL_WINDOW_BARS):
    """First second-touch setup at each 0DTE wall in one session of bars.

    A touch comes within `tolerance` of the level; the first touch must bounce `threshold` away
    before going `threshold` through; the second touch must not go `threshold` through and must
    close back on the original side within `window` bars.
    """
    o, h, l, c = (bars[col].to_numpy(dtype=float) for col in ("Open", "High", "Low", "Close"))
    setups = []
    for name, level in levels:
        if "0DTE" not in name:
            continue
        state, support, first, second, extreme = "wait", True, None, None, None
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
                through = level - l[i] if support else h[i] - level
                if through >= threshold:
                    break  # the first touch broke the level
                continue
            if through >= threshold:
                break  # broke before a bounce, or the retest broke it
            if state == "first":
                if (h[i] - level if support else level - l[i]) >= threshold:
                    state = "bounced"
                continue
            if state == "bounced":
                if not (l[i] <= level + tolerance if support else h[i] >= level - tolerance):
                    continue
                state, second, extreme = "second", i, l[i] if support else h[i]
            extreme = min(extreme, l[i]) if support else max(extreme, h[i])
            if c[i] > level if support else c[i] < level:
                direction = "long" if support else "short"
                entry = h[i] if support else l[i]
                target_name, target = _target(levels, entry, direction)
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
                    )
                )
                break
            if i - second >= window - 1:
                break  # the retest didn't close back in time
    return setups
