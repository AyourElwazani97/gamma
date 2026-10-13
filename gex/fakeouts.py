"""Tom Hougaard's failed breakout, at GEX walls and expected-move edges.

Price pushes through a wall but can't follow through and closes back inside: trade back
toward the next level ("the magnet"), stop beyond the push. Works best in positive gamma.
"""
from dataclasses import dataclass

import pandas as pd

# Tops of the range are faded from above, bottoms from below; magnets and the flip are only targets
SHORT_LEVELS = ("Call Wall", "Expected Move High")
LONG_LEVELS = ("Put Wall", "Expected Move Low")
FAIL_WINDOW_BARS = 3  # the close back inside must come within 15 minutes of the push


@dataclass
class FailedBreakout:
    name: str
    price: float
    direction: str  # "short" (failed push above) or "long" (failed push below)
    poke_time: pd.Timestamp  # bar that pushed through the level
    time: pd.Timestamp  # bar that closed back inside: the signal
    entry: float  # break of the signal bar's low (short) / high (long)
    stop: float  # beyond the furthest point of the push
    target_name: str | None  # next level in the trade's direction
    target: float | None
    positive_gamma: bool | None  # signal close above the Gamma Flip


def _directions(name):
    """Trade directions a level can be faded in (a merged level can be both a top and a bottom)."""
    return [d for d, words in (("short", SHORT_LEVELS), ("long", LONG_LEVELS)) if any(w in name for w in words)]


def _target(levels, entry, direction):
    beyond = [(n, p) for n, p in levels if (p < entry if direction == "short" else p > entry)]
    if not beyond:
        return None, None
    return min(beyond, key=lambda lv: abs(lv[1] - entry))


def failed_breakouts(bars, levels, flip, tolerance, window=FAIL_WINDOW_BARS):
    """First failed push above and below each wall / expected-move edge in one session of bars."""
    o, h, l, c = (bars[col].to_numpy(dtype=float) for col in ("Open", "High", "Low", "Close"))
    setups = []
    for name, level in levels:
        for direction in _directions(name):
            for i in range(len(c)):
                before = c[i - 1] if i > 0 else o[i]
                if direction == "short":
                    pushed = before < level and h[i] > level + tolerance
                else:
                    pushed = before > level and l[i] < level - tolerance
                if not pushed:
                    continue
                end = min(i + window, len(c))
                back = [f for f in range(i, end) if (c[f] < level if direction == "short" else c[f] > level)]
                if not back:
                    continue
                f = back[0]
                if direction == "short":
                    entry, stop = l[f], h[i : f + 1].max()
                else:
                    entry, stop = h[f], l[i : f + 1].min()
                target_name, target = _target(levels, entry, direction)
                setups.append(
                    FailedBreakout(
                        name=name,
                        price=level,
                        direction=direction,
                        poke_time=bars.index[i],
                        time=bars.index[f],
                        entry=float(entry),
                        stop=float(stop),
                        target_name=target_name,
                        target=target,
                        positive_gamma=None if flip is None else bool(c[f] > flip),
                    )
                )
                break
    return setups
