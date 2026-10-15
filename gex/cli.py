import argparse
import sys
import webbrowser
from pathlib import Path

import pandas as pd

from gex.bars import archived_bars, bars_path, load_bars, trading_day
from gex.config import PRODUCTS
from gex.data import NEW_YORK, index_closed, load_chain
from gex.futures import fetch_futures
from gex.levels import analyze
from gex.output import append_history, fmt, format_report, level_rows, plot_levels, to_futures, tradingview_string
from gex.profile import CONFLUENCE_PCT, confluences, format_confluence, prior_trading_day, volume_profile
from gex.review import fetch_bars, session_date


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="gex.py", description="Dealer gamma (GEX) levels for ES / NQ futures from CBOE option data."
    )
    parser.add_argument("products", nargs="*", default=["ES", "NQ"], help="ES, NQ or both (default: both)")
    parser.add_argument("--basis", type=float, help="futures minus index, e.g. 59.5 (default: live from yfinance)")
    parser.add_argument("--no-etf", action="store_true", help="index options only (skip SPY / QQQ)")
    parser.add_argument("--from-dir", help="read saved CBOE files ({ticker}.json) instead of downloading")
    parser.add_argument("--out", default="output", help="folder for the chart and TradingView file")
    parser.add_argument("--history", default="history/levels_history.csv", help="CSV log of daily levels")
    parser.add_argument("--no-history", action="store_true", help="don't append to the history CSV")
    parser.add_argument("--show", action="store_true", help="open the chart when done")
    return parser.parse_args(argv)


def get_basis(product, args):
    """(basis, note, live futures (price, time) or None). Manual --basis wins; offline files never
    use live prices."""
    if args.basis is not None:
        return args.basis, "manual", None
    if args.from_dir:
        return None, None, None
    try:
        basis, live = fetch_futures(product)
    except Exception as e:  # yfinance breaks from time to time; levels are still useful without it
        print(f"Warning: live basis unavailable ({e})", file=sys.stderr)
        return None, None, None
    if basis is None:
        return None, None, live
    value, when = basis
    return value, f"live, {when.tz_convert(NEW_YORK):%a %H:%M} NY", live


def live_spot(product, index_chain, basis, live):
    """(index price to compute the levels at, report line). While the index is closed its CBOE price
    is the last close, so use live futures minus the basis; (None, None) while it trades."""
    if not index_closed(index_chain):
        return None, None
    if basis is None or live is None:
        return None, (
            f"WARNING: {product.index_label} is closed: LAST PRICE, Dist, Touch and the Expected Move are from "
            f"its last close, not where {product.name} trades now. Check live {product.name} futures."
        )
    price, when = live
    return price - basis, (
        f"Last price: live {product.name} futures {fmt(price)} at {when.tz_convert(NEW_YORK):%a %H:%M} NY "
        f"({product.index_label} closed at {fmt(index_chain.spot)})"
    )


def prior_profile_note(product, lv, basis, bars_dir, download):
    """The prior session's volume profile and the levels near it, or None without its bars.
    Saved bars first; downloads (and saves) them only when `download`."""
    prior = prior_trading_day(session_date(lv.asof.tz_convert(NEW_YORK)))
    if download:
        now = pd.Timestamp.now(tz=NEW_YORK)
        bars = archived_bars(product.name, [prior], now, bars_dir, lambda start, end: fetch_bars(product, start, end))
    else:
        path = bars_path(bars_dir, product.name, prior)
        bars = load_bars(path) if path.exists() else None
    profile = None if bars is None else volume_profile(trading_day(bars, prior))
    if profile is None:
        return None
    levels = [(r.name, to_futures(r.index_price, basis)) for r in level_rows(lv)]
    tolerance = to_futures(lv.spot, basis) * CONFLUENCE_PCT
    return format_confluence(prior, profile, confluences(levels, profile, tolerance), tolerance)


def run_product(product, args):
    try:
        index_chain = load_chain(product.index_ticker, args.from_dir)
    except Exception as e:  # network error, missing file, bad JSON
        print(f"Could not load {product.index_ticker} options: {e}", file=sys.stderr)
        return 1

    etf_chain = None
    if not args.no_etf:
        try:
            etf_chain = load_chain(product.etf_ticker, args.from_dir)
        except Exception as e:
            print(
                f"Warning: {product.etf_ticker} options unavailable ({e}); using {product.index_label} only.",
                file=sys.stderr,
            )

    basis, basis_note, live = get_basis(product, args)
    spot, spot_note = live_spot(product, index_chain, basis, live)
    try:
        analysis = analyze(product, index_chain, etf_chain, spot=spot)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    lv = analysis.levels
    print(format_report(lv, basis, basis_note, spot_note))
    if basis is not None:  # the profile is in futures prices
        try:
            note = prior_profile_note(product, lv, basis, Path(args.history).parent / "bars", not args.from_dir)
        except Exception as e:  # network / yfinance: the levels are still fine without it
            note = None
            print(f"Warning: prior session volume profile unavailable ({e})", file=sys.stderr)
        if note:
            print("\n" + note)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chart = out / f"{product.name}_{lv.asof.tz_convert(NEW_YORK).date()}.png"
    plot_levels(analysis, basis, chart)
    tv = tradingview_string(lv, basis)
    (out / f"{product.name}_tradingview.txt").write_text(tv)

    print("\nTradingView (paste into the GEX Levels indicator settings):")
    print(tv)
    print(f"\nChart: {chart}")
    if not args.no_history:
        append_history(lv, basis, args.history)
        print(f"History: {args.history}")
    if args.show:
        webbrowser.open(chart.resolve().as_uri())
    return 0


def main(argv=None):
    args = parse_args(argv)
    names = [p.upper() for p in args.products]
    unknown = [n for n in names if n not in PRODUCTS]
    if unknown:
        print(f"Unknown product: {', '.join(unknown)}. Use ES or NQ.", file=sys.stderr)
        return 2
    if args.basis is not None and len(names) > 1:
        print("--basis only works with one product (ES and NQ have different bases).", file=sys.stderr)
        return 2
    return max(run_product(PRODUCTS[name], args) for name in names)
