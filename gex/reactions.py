"""How price reacted when it reached each level."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

# History CSV column -> level name, in the same order as the report ladder
LEVEL_FIELDS = [
    ("call_wall", "Call Wall"),
    ("next_call_wall", "0DTE Call Wall"),
    ("em_high", "Expected Move High"),
    ("flip", "Gamma Flip"),
    ("magnet_1", "Magnet 1"),
    ("magnet_2", "Magnet 2"),
    ("magnet_3", "Magnet 3"),
    ("em_low", "Expected Move Low"),
    ("next_put_wall", "0DTE Put Wall"),
    ("put_wall", "Put Wall"),
]

THRESHOLD_PCT = 0.001  # a 0.1% move away / through counts as a reaction (~8 ES, ~30 NQ points)
TOLERANCE_PCT = 0.0002  # coming within 0.02% counts as a touch (~1.5 ES, ~6 NQ points)
WINDOW_BARS = 12  # judge the reaction over 60 minutes of 5-minute bars


@dataclass
class Reaction:
    name: str
    price: float
    touched: bool
    first_touch: pd.Timestamp | None
    side: str | None  # "support" (reached from above) or "resistance" (reached from below)
    outcome: str  # "held", "broke", "stalled" or "not reached"
    bounce: float  # best move away from the level after the touch
    through: float  # deepest move past the level after the touch
    touches: int


def levels_from_row(row):
    """[(name, price)] from a history row, highest first; same-price levels merged."""
    merged = {}
    for field, name in LEVEL_FIELDS:
        value = row.get(field)
        if value in ("", None):
            continue
        price = float(value)
        merged[price] = f"{merged[price]} + {name}" if price in merged else name
    return [(name, price) for price, name in sorted(merged.items(), reverse=True)]


def _first(mask):
    hits = np.flatnonzero(mask)
    return int(hits[0]) if len(hits) else None


def level_reactions(bars, levels, threshold, tolerance, window=WINDOW_BARS):
    """Reaction at each (name, price) level for one session of OHLC bars.

    After the first touch, `held` = price moved `threshold` away from the level before moving
    `threshold` through it (within `window` bars, touch bar included); `broke` = the opposite.
    """
    o, h, l, c = (bars[col].to_numpy(dtype=float) for col in ("Open", "High", "Low", "Close"))
    results = []
    for name, level in levels:
        near = (l <= level + tolerance) & (h >= level - tolerance)
        touches = int(near[0]) + int(np.sum(near[1:] & ~near[:-1])) if len(near) else 0
        i = _first(near)
        if i is None:
            results.append(Reaction(name, level, False, None, None, "not reached", 0.0, 0.0, 0))
            continue

        before = c[i - 1] if i > 0 else o[i]
        side = "support" if before > level else "resistance"
        end = i + window
        if side == "support":
            through = level - l[i:end]
            bounce = h[i + 1 : end] - level
        else:
            through = h[i:end] - level
            bounce = level - l[i + 1 : end]

        broke_at = _first(through >= threshold)
        held_at = _first(bounce >= threshold)
        held_at = None if held_at is None else held_at + 1  # bounce starts one bar after the touch
        if broke_at is not None and (held_at is None or broke_at <= held_at):
            outcome = "broke"
        elif held_at is not None:
            outcome = "held"
        else:
            outcome = "stalled"

        results.append(
            Reaction(
                name=name,
                price=level,
                touched=True,
                first_touch=bars.index[i],
                side=side,
                outcome=outcome,
                bounce=float(max(bounce.max(initial=0.0), 0.0)),
                through=float(max(through.max(initial=0.0), 0.0)),
                touches=touches,
            )
        )
    return results
