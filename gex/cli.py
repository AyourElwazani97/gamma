import argparse
import sys
import webbrowser
from pathlib import Path

from gex.config import PRODUCTS
from gex.data import NEW_YORK, load_chain
from gex.futures import fetch_basis
from gex.levels import analyze
from gex.output import append_history, format_report, plot_levels, tradingview_string


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
    """(basis, note). Manual --basis wins; offline files never use a live basis."""
    if args.basis is not None:
        return args.basis, "manual"
    if args.from_dir:
        return None, None
    try:
        result = fetch_basis(product)
    except Exception as e:  # yfinance breaks from time to time; levels are still useful without it
        print(f"Warning: live basis unavailable ({e})", file=sys.stderr)
        return None, None
    if result is None:
        return None, None
    basis, when = result
    return basis, f"live, {when.tz_convert(NEW_YORK):%a %H:%M} NY"


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

    try:
        analysis = analyze(product, index_chain, etf_chain)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    lv = analysis.levels
    basis, basis_note = get_basis(product, args)
    print(format_report(lv, basis, basis_note))

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
