"""Grade saved levels against what the futures actually did in the regular session."""
import argparse
import csv
import sys
from collections import Counter
from datetime import time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from gex.bars import archived_bars
from gex.config import PRODUCTS
from gex.data import NEW_YORK
from gex.fakeouts import failed_breakouts
from gex.output import BG, GRAY, GREEN, GRID, RED, TEXT, YELLOW, _level_color, fmt
from gex.paper import simulate
from gex.reactions import THRESHOLD_PCT, TOLERANCE_PCT, WINDOW_BARS, level_reactions, levels_from_row
from gex.retests import second_touches

OPEN, CLOSE = time(9, 30), time(16, 0)
HOW_TO_READ = """
How to read it:
  Rng/EM < 1 = quieter day than options priced, > 1 = wilder.
  If negative gamma days keep coming in wilder, respect the regime: smaller size, don't fade.
  Walls that 'held' were good fade spots; 'BROKE' walls were breakout days."""
FAILED, RETEST = "Failed breakout", "2nd touch of 0DTE wall"
SCORE_FILTERS = ("all", "OK: above flip, reward >= risk", "below flip")


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
    levels = levels_from_row(row)
    tolerance, threshold = o * TOLERANCE_PCT, o * THRESHOLD_PCT
    setups = [(FAILED, s) for s in failed_breakouts(rth, levels, flip, tolerance=tolerance)]
    setups += [(RETEST, s) for s in second_touches(rth, levels, flip, tolerance=tolerance, threshold=threshold)]
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
        "em_width": em_high - em_low if has_em else None,
        "reactions": level_reactions(rth, levels, threshold=threshold, tolerance=tolerance),
        "trades": [(kind, s, simulate(s, rth)) for kind, s in sorted(setups, key=lambda ks: ks[1].time)],
        "bars": rth,
    }


def _passes(setup, name):
    if name == "below flip":
        return setup.positive_gamma is False
    if name == "all":
        return True
    reward_ok = setup.target is None or abs(setup.entry - setup.target) >= abs(setup.stop - setup.entry)
    return setup.positive_gamma is not False and reward_ok  # the rule track.py and the strategy call OK


def setup_scores(graded):
    """{(setup, filter): (trades, won, lost, net R)} over all sessions' paper trades; unfilled entries don't count."""
    scores = {}
    for kind in (FAILED, RETEST):
        for name in SCORE_FILTERS:
            taken = [
                t for g in graded for k, s, t in g.get("trades", [])
                if k == kind and t.outcome != "cancelled" and _passes(s, name)
            ]  # fmt: skip
            won, lost = sum(t.r > 0 for t in taken), sum(t.r < 0 for t in taken)
            scores[(kind, name)] = (len(taken), won, lost, sum(t.r for t in taken))
    return scores


def scorecard(graded):
    """{level name: Counter(outcome)} over all sessions; merged names count for each level."""
    cards = {}
    for g in graded:
        for r in g.get("reactions", []):
            for name in r.name.split(" + "):
                base = name.rstrip(" 123") if name.startswith("Magnet") else name
                cards.setdefault(base, Counter())[r.outcome] += 1
    return cards


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
        "Level scorecard (first touch, 60-min reaction):",
        f"  {'Level':<20}  {'held':>4}  {'broke':>5}  {'stalled':>7}  {'not reached':>11}",
    ]
    for level, counts in scorecard(graded).items():
        lines.append(
            f"  {level:<20}  {counts['held']:>4}  {counts['broke']:>5}  {counts['stalled']:>7}  "
            f"{counts['not reached']:>11}"
        )
    lines += [
        "",
        "Setup scoreboard (paper trades: stop entry, stop, target, stop to entry after 1R; R = each trade's risk):",
        f"  {'Setup':<23}  {'Filter':<30}  {'Trades':>6}  {'Won':>3}  {'Lost':>4}  {'Net R':>6}",
    ]
    for (kind, name), (trades, won, lost, net) in setup_scores(graded).items():
        lines.append(f"  {kind:<23}  {name:<30}  {trades:>6}  {won:>3}  {lost:>4}  {net:>+6.2f}")
    return "\n".join(lines)


def format_reactions(g):
    """Per-level reaction table for one graded session."""
    threshold = g["open"] * THRESHOLD_PCT
    rng = "-" if g["em_width"] is None else f"{g['high'] - g['low']:.2f} vs expected {g['em_width']:.2f}"
    lines = [
        "",
        f"--- {g['product']} {g['date']:%a %Y-%m-%d} | {g['regime']} gamma | range {rng} ---",
        f"  Open {fmt(g['open'])}  High {fmt(g['high'])}  Low {fmt(g['low'])}  Close {fmt(g['close'])}"
        f"   (reaction = {threshold:.1f} pts within {WINDOW_BARS * 5} min)",
        f"  {'Level':<26}  {'Price':>9}  {'Touch':>5}  {'Tested as':<10}  {'Result':<11}  "
        f"{'Bounce':>6}  {'Through':>7}  {'Touches':>7}",
    ]
    for r in g["reactions"]:
        touch = "-" if r.first_touch is None else f"{r.first_touch.tz_convert(NEW_YORK):%H:%M}"
        lines.append(
            f"  {r.name:<26}  {fmt(r.price):>9}  {touch:>5}  {r.side or '-':<10}  {r.outcome:<11}  "
            f"{r.bounce:>6.2f}  {r.through:>7.2f}  {r.touches:>7}"
        )
    return "\n".join(lines)


OUTCOME_COLOR = {"held": GREEN, "broke": RED, "stalled": YELLOW}


def plot_session(g, path):
    """5-minute candles for the session with the levels and where each was first touched."""
    bars = g["bars"]
    x = np.arange(len(bars))
    o, h, l, c = (bars[col].to_numpy(dtype=float) for col in ("Open", "High", "Low", "Close"))

    fig = Figure(figsize=(14, 7), facecolor=BG)
    ax = fig.subplots()
    ax.set_facecolor(BG)
    up = c >= o
    ax.vlines(x, l, h, color=np.where(up, GREEN, RED), lw=0.8)
    ax.bar(x, np.maximum(np.abs(c - o), 0.01), bottom=np.minimum(o, c), width=0.7, color=np.where(up, GREEN, RED))

    pad = g["open"] * 0.004
    lo, hi = l.min() - pad, h.max() + pad
    for r in g["reactions"]:
        if not lo <= r.price <= hi:
            continue
        color = _level_color(r.name)
        ax.axhline(r.price, color=color, lw=1, alpha=0.8)
        ax.text(len(x) + 0.5, r.price, f"{r.name} {fmt(r.price)}", color=color, fontsize=8, va="center",
                bbox={"facecolor": BG, "edgecolor": "none", "pad": 1.5})  # fmt: skip
        if r.first_touch is not None:
            i = bars.index.get_loc(r.first_touch)
            ax.plot(i, r.price, "o", ms=9, mfc="none", mew=2, color=OUTCOME_COLOR[r.outcome])
            ax.text(i, r.price, f" {r.outcome}", color=OUTCOME_COLOR[r.outcome], fontsize=8, va="bottom")

    ax.set_ylim(lo, hi)
    ax.set_xlim(-1, len(x) + 14)
    times = bars.index.tz_convert(NEW_YORK)
    ticks = x[::6]
    ax.set_xticks(ticks, [f"{t:%H:%M}" for t in times[::6]])
    ax.grid(color=GRID, lw=0.6)
    ax.tick_params(colors=TEXT)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    rng = "" if g["em_width"] is None else f"  |  range {g['high'] - g['low']:.1f} vs expected {g['em_width']:.1f}"
    ax.set_title(
        f"{g['product']} {g['date']:%a %Y-%m-%d}  |  {g['regime'].upper()} GAMMA{rng}",
        color=TEXT,
        fontweight="bold",
    )
    ax.text(0.01, 0.01, "circle = first touch: green held, red broke, yellow stalled", transform=ax.transAxes,
            color=GRAY, fontsize=8)  # fmt: skip
    fig.tight_layout()
    fig.savefig(path, dpi=120, facecolor=BG)


def main(argv=None, today=None):
    parser = argparse.ArgumentParser(
        prog="review.py", description="Check saved GEX levels against what ES / NQ actually did."
    )
    parser.add_argument("products", nargs="*", default=["ES", "NQ"], help="ES, NQ or both (default: both)")
    parser.add_argument("--history", default="history/levels_history.csv", help="CSV written by gex.py")
    parser.add_argument("--detail", action="store_true", help="show how price reacted at every level")
    parser.add_argument("--charts", metavar="DIR", help="save a 5-minute chart per session into DIR")
    args = parser.parse_args(argv)

    if not Path(args.history).exists():
        print(f"No history at {args.history} yet. Run `python gex.py` before each session first.", file=sys.stderr)
        return 1

    if today is None:
        now = pd.Timestamp.now(tz=NEW_YORK)
    else:
        now = pd.Timestamp.combine(today, time(23, 59)).tz_localize(NEW_YORK)
    today = now.date()
    saved = Path(args.history).parent / "bars"  # finished sessions' bars, kept after Yahoo drops them
    rows = load_history(args.history)
    code = 0
    reviewed = False
    for name in (p.upper() for p in args.products):
        if name not in PRODUCTS:
            print(f"Unknown product: {name}. Use ES or NQ.", file=sys.stderr)
            code = 2
            continue
        product_rows = [
            r for r in rows
            if r["product"] == name
            and session_date(pd.Timestamp(r["asof_ny"], tz=NEW_YORK)) <= today
        ]  # fmt: skip
        if not product_rows:
            print(f"\n{name}: no finished sessions to review yet.")
            continue

        days = [session_date(pd.Timestamp(r["asof_ny"], tz=NEW_YORK)) for r in product_rows]
        try:
            bars = archived_bars(name, days, now, saved, lambda start, end: fetch_bars(PRODUCTS[name], start, end))
        except Exception as e:  # network / yfinance
            print(f"Could not download {name} prices: {e}", file=sys.stderr)
            code = 1
            continue
        graded = [g for g in (grade(r, bars) for r in product_rows) if g is not None]
        if not graded:
            print(f"\n{name}: no price data yet for the saved sessions.")
            continue
        graded.sort(key=lambda g: g["date"])
        print(format_review(name, graded))
        for g in graded:
            if args.detail:
                print(format_reactions(g))
            if args.charts:
                Path(args.charts).mkdir(parents=True, exist_ok=True)
                chart = Path(args.charts) / f"{name}_{g['date']}_review.png"
                plot_session(g, chart)
                print(f"  Chart: {chart}")
        reviewed = True
    if reviewed:
        print(HOW_TO_READ)
    return code
