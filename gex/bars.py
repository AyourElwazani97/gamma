"""Saved 5-minute futures bars, one CSV per product and session.

Yahoo keeps 5-minute bars for about 60 days, so each finished session is saved next to the
levels history; old sessions stay reviewable and testable after Yahoo drops them.
"""
from datetime import time, timedelta
from pathlib import Path

import pandas as pd

from gex.data import NEW_YORK

GLOBEX_OPEN = time(18, 0)  # the session for day D trades from 18:00 the evening before
CLOSE = time(16, 0)
LAST_BAR = time(15, 55)
SETTLE = timedelta(minutes=30)  # the feed runs late: give it time to finish the last bar
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
MAX_DOWNLOAD_DAYS = 59


def _at(day, t):
    return pd.Timestamp.combine(day, t).tz_localize(NEW_YORK)


def bars_path(root, name, day):
    return Path(root) / f"{name}_{day}.csv"


def trading_day(bars, day):
    """Bars from 18:00 the evening before `day` to its 16:00 close: overnight + regular session."""
    local = bars.index.tz_convert(NEW_YORK)
    kept = bars[(local >= _at(day - timedelta(days=1), GLOBEX_OPEN)) & (local < _at(day, CLOSE))]
    return kept.set_axis(kept.index.tz_convert(NEW_YORK))


def is_complete(day_bars, day):
    """True once the 15:55 bar of `day` is there."""
    return any(t.date() == day and t.time() == LAST_BAR for t in day_bars.index.tz_convert(NEW_YORK))


def save_bars(bars, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars[[c for c in COLUMNS if c in bars]].to_csv(path, index_label="Datetime")


def load_bars(path):
    bars = pd.read_csv(path, index_col="Datetime")
    # stamps carry their own -04:00 / -05:00 offset, so parse as UTC then convert
    return bars.set_axis(pd.to_datetime(bars.index, utc=True).tz_convert(NEW_YORK))


def archived_bars(name, days, now, root, fetch, max_days=MAX_DOWNLOAD_DAYS):
    """5-minute bars for the sessions in `days`: saved files first, the rest from `fetch(start, end)`.

    Downloaded sessions that are over and complete get saved. Sessions older than `max_days`
    without a file are skipped (Yahoo no longer has them).
    """
    frames, missing = [], []
    for day in sorted(set(days)):
        path = bars_path(root, name, day)
        if path.exists():
            frames.append(load_bars(path))
        elif day >= now.date() - timedelta(days=max_days):
            missing.append(day)
    if missing:
        fetched = fetch(min(missing) - timedelta(days=1), max(missing))
        for day in missing:
            day_bars = trading_day(fetched, day)
            if now >= _at(day, CLOSE) + SETTLE and is_complete(day_bars, day):
                save_bars(day_bars, bars_path(root, name, day))
            frames.append(day_bars)
    if not frames:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz=NEW_YORK), dtype=float)
    return pd.concat(frames)
