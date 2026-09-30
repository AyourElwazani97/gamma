"""Grade saved levels against what the futures actually did in the regular session."""
import argparse
import csv
import sys
from datetime import date, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from gex.config import PRODUCTS
from gex.data import NEW_YORK

OPEN, CLOSE = time(9, 30), time(16, 0)
MAX_BAR_DAYS = 59  # yfinance keeps 5-minute bars for about 60 days


def session_date(asof):
    """Regular session the levels are for: same day until the 16:00 close, else the next weekday."""
    day = asof.date()
    if asof.time() >= CLOSE:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def session_bars(bars, day):
    """Bars of `day` between 09:30 and 16:00 New York."""
    local = bars.index.tz_convert(NEW_YORK)
    return bars[(local.date == day) & (local.time >= OPEN) & (local.time < CLOSE)]


def _num(value):
    return None if value in ("", None) else float(value)


def grade(row, bars):
    """Compare one history row (futures points) with that session's bars. None if no bars."""
    day = session_date(pd.Timestamp(row["asof_ny"], tz=NEW_YORK))
    rth = session_bars(bars, day)
    if rth.empty:
        return None

    o, h, l, c = (
        float(rth["Open"].iloc[0]),
        float(rth["High"].max()),
        float(rth["Low"].min()),
        float(rth["Close"].iloc[-1]),
    )
    flip, call_wall, put_wall, em_low, em_high = (
        _num(row.get(k)) for k in ("flip", "call_wall", "put_wall", "em_low", "em_high")
    )
    has_em = em_low is not None and em_high is not None and em_high > em_low
    hit_call = None if call_wall is None else h >= call_wall
    hit_put = None if put_wall is None else l <= put_wall
    return {
        "date": day,
        "product": row["product"],
        "regime": row["regime"],
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "range_vs_em": (h - l) / (em_high - em_low) if has_em else None,
        "closed_inside_em": em_low <= c <= em_high if has_em else None,
        "hit_call_wall": hit_call,
        "call_wall_held": c < call_wall if hit_call else None,  # touched but closed back below
        "hit_put_wall": hit_put,
        "put_wall_held": c > put_wall if hit_put else None,  # touched but closed back above
        "closed_above_flip": None if flip is None else c > flip,
    }


def summarize(graded):
    ranges = {}
    for g in graded:
        if g["range_vs_em"] is not None:
            ranges.setdefault(g["regime"], []).append(g["range_vs_em"])
    return {
        "days": len(graded),
        "closed_inside_em": sum(1 for g in graded if g["closed_inside_em"]),
        "avg_range_vs_em": {regime: float(np.mean(v)) for regime, v in ranges.items()},
        "regime_days": {regime: len(v) for regime, v in ranges.items()},
        "call_wall_touched": sum(1 for g in graded if g["hit_call_wall"]),
        "call_wall_held": sum(1 for g in graded if g["call_wall_held"]),
        "put_wall_touched": sum(1 for g in graded if g["hit_put_wall"]),
        "put_wall_held": sum(1 for g in graded if g["put_wall_held"]),
    }


def load_history(path):
    """History rows, keeping only the last run per product and session."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    latest = {}
    for row in rows:
        latest[(row["product"], session_date(pd.Timestamp(row["asof_ny"], tz=NEW_YORK)))] = row
    return list(latest.values())


def fetch_bars(product, start, end):
    import yfinance as yf

    return yf.Ticker(product.future_symbol).history(
        start=start, end=end + timedelta(days=1), interval="5m"
    )


def _wall(hit, held):
    if not hit:
        return "-"
    return "held" if held else "BROKE"


def _yes_no(value):
    return "-" if value is None else ("yes" if value else "no")


def format_review(product, graded):
    lines = [
        "",
        f"=== {product} review: {len(graded)} session(s) ===",
        f"  {'Date':<10}  {'Regime':<8}  {'Open':>9}  {'High':>9}  {'Low':>9}  {'Close':>9}  "
        f"{'Rng/EM':>6}  {'In EM':>5}  {'CallWall':>8}  {'PutWall':>8}  {'>Flip':>5}",
    ]
    for g in graded:
        rng = "-" if g["range_vs_em"] is None else f"{g['range_vs_em']:.2f}"
        lines.append(
            f"  {g['date']:%Y-%m-%d}  {g['regime']:<8}  {g['open']:>9.2f}  {g['high']:>9.2f}  {g['low']:>9.2f}  "
            f"{g['close']:>9.2f}  {rng:>6}  {_yes_no(g['closed_inside_em']):>5}  "
            f"{_wall(g['hit_call_wall'], g['call_wall_held']):>8}  {_wall(g['hit_put_wall'], g['put_wall_held']):>8}  "
            f"{_yes_no(g['closed_above_flip']):>5}"
        )

    s = summarize(graded)
    regimes = " | ".join(
        f"{r} gamma {s['avg_range_vs_em'][r]:.2f}x ({s['regime_days'][r]} days)"
        for r in ("positive", "negative")
        if r in s["avg_range_vs_em"]
    )
    lines += [
        "",
        "Summary:",
        f"  Closed inside expected move: {s['closed_inside_em']}/{s['days']} sessions",
        f"  Day range vs expected move: {regimes or '-'}",
        f"  Call wall: touched {s['call_wall_touched']}, held (closed below) {s['call_wall_held']}",
        f"  Put wall:  touched {s['put_wall_touched']}, held (closed above) {s['put_wall_held']}",
        "",
        "How to read it:",
        "  Rng/EM < 1 = quieter day than options priced, > 1 = wilder.",
        "  If negative gamma days keep coming in wilder, respect the regime: smaller size, don't fade.",
        "  Walls that 'held' were good fade spots; 'BROKE' walls were breakout days.",
    ]
    return "\n".join(lines)


def main(argv=None, today=None):
    parser = argparse.ArgumentParser(
        prog="review.py", description="Check saved GEX levels against what ES / NQ actually did."
    )
    parser.add_argument("products", nargs="*", default=["ES", "NQ"], help="ES, NQ or both (default: both)")
    parser.add_argument("--history", default="history/levels_history.csv", help="CSV written by gex.py")
    args = parser.parse_args(argv)

    if not Path(args.history).exists():
        print(f"No history at {args.history} yet. Run `python gex.py` before each session first.", file=sys.stderr)
        return 1

    today = today or date.today()
    oldest = today - timedelta(days=MAX_BAR_DAYS)
    rows = load_history(args.history)
    code = 0
    for name in (p.upper() for p in args.products):
        if name not in PRODUCTS:
            print(f"Unknown product: {name}. Use ES or NQ.", file=sys.stderr)
            code = 2
            continue
        product_rows = [
            r for r in rows
            if r["product"] == name
            and oldest <= session_date(pd.Timestamp(r["asof_ny"], tz=NEW_YORK)) <= today
        ]  # fmt: skip
        if not product_rows:
            print(f"\n{name}: no finished sessions in the last {MAX_BAR_DAYS} days to review yet.")
            continue

        days = [session_date(pd.Timestamp(r["asof_ny"], tz=NEW_YORK)) for r in product_rows]
        try:
            bars = fetch_bars(PRODUCTS[name], min(days), max(days))
        except Exception as e:  # network / yfinance
            print(f"Could not download {name} prices: {e}", file=sys.stderr)
            code = 1
            continue
        graded = [g for g in (grade(r, bars) for r in product_rows) if g is not None]
        if not graded:
            print(f"\n{name}: no price data yet for the saved sessions.")
            continue
        print(format_review(name, sorted(graded, key=lambda g: g["date"])))
    return code
