"""Live session tracker: prints one line each time price touches, holds or breaks a level."""
import argparse
import json
import sys
import time
from datetime import time as dtime
from pathlib import Path

import pandas as pd

from gex.config import PRODUCTS
from gex.data import NEW_YORK
from gex.reactions import THRESHOLD_PCT, TOLERANCE_PCT, level_reactions, levels_from_row
from gex.review import load_history, session_date

CLOSE = dtime(16, 0)
HEARTBEAT_MIN = 30


def flip_side(price, flip):
    if flip is None:
        return None
    return "above" if price > flip else "below"


def level_events(product, prev, reactions, last):
    """Lines for levels whose state changed since `prev` ({name: outcome}). Returns (lines, new state)."""
    lines, state = [], dict(prev)
    for r in reactions:
        before = prev.get(r.name, "not reached")
        state[r.name] = r.outcome
        if r.outcome == before:
            continue
        if before == "not reached" and r.touched:
            lines.append(
                f"{r.first_touch.tz_convert(NEW_YORK):%H:%M} {product} touched {r.name} {r.price:g} "
                f"as {r.side} (last {last:g})"
            )
        if r.outcome == "held":
            lines.append(f"{product} {r.name} {r.price:g} HELD: bounced {r.bounce:g} pts (last {last:g})")
        elif r.outcome == "broke":
            lines.append(f"{product} {r.name} {r.price:g} BROKE: went {r.through:g} pts through (last {last:g})")
    return lines, state


def load_state(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))


def fetch_today(product):
    import yfinance as yf

    bars = yf.Ticker(product.future_symbol).history(period="1d", interval="5m")
    bars.index = bars.index.tz_convert(NEW_YORK)
    return bars


def todays_rows(history, day):
    return {
        r["product"]: r
        for r in load_history(history)
        if session_date(pd.Timestamp(r["asof_ny"], tz=NEW_YORK)) == day
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="track.py", description="Live alerts when ES / NQ reach today's GEX levels.")
    parser.add_argument("products", nargs="*", default=["ES", "NQ"])
    parser.add_argument("--start", default="09:30", help="track bars from this New York time (default 09:30)")
    parser.add_argument("--interval", type=int, default=120, help="seconds between checks")
    parser.add_argument("--history", default="history/levels_history.csv")
    parser.add_argument("--state", help="state file (default output/track_<date>.json)")
    args = parser.parse_args(argv)

    day = pd.Timestamp.now(tz=NEW_YORK).date()
    rows = todays_rows(args.history, day)
    names = [p.upper() for p in args.products]
    missing = [n for n in names if n not in rows]
    if missing:
        print(f"ERROR no levels for {day} for {', '.join(missing)}. Run `python gex.py` first.", flush=True)
        return 1

    state_path = args.state or f"output/track_{day}.json"
    state = load_state(state_path)
    start = pd.Timestamp(f"{day} {args.start}", tz=NEW_YORK)
    levels = {n: levels_from_row(rows[n]) for n in names}
    flips = {n: float(rows[n]["flip"]) if rows[n]["flip"] else None for n in names}
    print(f"TRACKING {', '.join(names)} from {args.start} ET, checking every {args.interval}s", flush=True)
    last_beat = None

    while True:
        now = pd.Timestamp.now(tz=NEW_YORK)
        lasts = {}
        for n in names:
            try:
                bars = fetch_today(PRODUCTS[n])
            except Exception as e:  # network / yfinance hiccup: report and retry next round
                print(f"ERROR {n} price fetch failed: {e}", flush=True)
                continue
            bars = bars[(bars.index >= start) & (bars.index.time < CLOSE)]
            if bars.empty:
                continue
            last = float(bars["Close"].iloc[-1])
            lasts[n] = last
            ref = float(bars["Open"].iloc[0])
            reactions = level_reactions(bars, levels[n], threshold=ref * THRESHOLD_PCT, tolerance=ref * TOLERANCE_PCT)
            st = state.setdefault(n, {"levels": {}, "flip": None})
            lines, st["levels"] = level_events(n, st["levels"], reactions, last)
            side = flip_side(last, flips[n])
            if side and st["flip"] and side != st["flip"]:
                regime = "POSITIVE gamma (calmer, fade edges)" if side == "above" else "NEGATIVE gamma (faster, don't fade)"
                lines.append(f"{now:%H:%M} {n} crossed {side} Gamma Flip {flips[n]:g} (last {last:g}) -> {regime}")
            st["flip"] = side or st["flip"]
            for line in lines:
                print(line, flush=True)
        save_state(state_path, state)

        if lasts and (last_beat is None or (now - last_beat).total_seconds() >= HEARTBEAT_MIN * 60):
            parts = [
                f"{n} {v:g} ({v - flips[n]:+g} vs flip)" if flips[n] else f"{n} {v:g}" for n, v in lasts.items()
            ]
            print(f"{now:%H:%M} STATUS " + " | ".join(parts), flush=True)
            last_beat = now
        if now.time() >= CLOSE:
            print(f"SESSION END {now:%H:%M} ET", flush=True)
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
