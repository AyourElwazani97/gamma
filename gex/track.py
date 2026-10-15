"""Live session tracker: prints one line each time price touches, holds or breaks a level,
each time a 5-minute bar closes back through one, when a wall gives a failed-breakout trade, when a
0DTE wall holds a second touch (a test setup), and when GEX re-computed from a fresh chain moves the
Gamma Flip (logged to history/gex_refresh.csv; levels and setups keep the morning run)."""
import argparse
import csv
import json
import sys
import time
from datetime import time as dtime
from pathlib import Path

import pandas as pd

from gex.cli import live_spot
from gex.config import PRODUCTS
from gex.data import NEW_YORK, load_chain
from gex.fakeouts import failed_breakouts
from gex.futures import fetch_futures
from gex.levels import analyze
from gex.output import fmt, to_futures
from gex.reactions import THRESHOLD_PCT, TOLERANCE_PCT, level_crosses, level_reactions, levels_from_row
from gex.retests import SecondTouch, rsi, second_touches
from gex.review import load_history, session_date

CLOSE = dtime(16, 0)
HEARTBEAT_MIN = 30
REFRESH_MIN = 15  # re-compute GEX this often (CBOE's chain runs 15 minutes late anyway)
FLIP_MOVE_PCT = 0.001  # a refreshed Gamma Flip that moved 0.1% (~8 ES, ~30 NQ points) is news
REGIME = {
    "above": "POSITIVE gamma (calmer, fade edges)",
    "below": "NEGATIVE gamma (faster, don't fade)",
}
REFRESH_FIELDS = ["time_ny", "product", "fut_last", "flip", "total_gex_bn"]


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
                f"{r.first_touch.tz_convert(NEW_YORK):%H:%M} {product} touched {r.name} {fmt(r.price)} "
                f"as {r.side} (last {fmt(last)})"
            )
        if r.outcome == "held":
            lines.append(f"{product} {r.name} {fmt(r.price)} HELD: bounced {fmt(r.bounce)} pts (last {fmt(last)})")
        elif r.outcome == "broke":
            lines.append(f"{product} {r.name} {fmt(r.price)} BROKE: went {fmt(r.through)} pts through (last {fmt(last)})")
    return lines, state


def closed_bars(bars):
    """Bars that have finished. The feed's newest bar is still forming, and Yahoo futures run
    ~10 minutes late, so the bar's timestamp alone can't tell."""
    return bars.iloc[:-1]


def cross_events(product, counts, crosses, reactions, last):
    """Lines for 5-minute closes through a level not alerted yet (`counts` = {name: crosses seen}).

    Skips the first cross when the first touch already printed BROKE that way, and the Gamma Flip
    (it has its own regime alert). Returns (lines, new counts).
    """
    lines, counts = [], dict(counts)
    broke_to = {
        r.name: "above" if r.side == "resistance" else "below" for r in reactions if r.outcome == "broke"
    }
    for x in crosses:
        if x.count <= counts.get(x.name, 0):
            continue
        counts[x.name] = x.count
        if "Gamma Flip" in x.name or (x.count == 1 and broke_to.get(x.name) == x.direction):
            continue
        back = "back " if x.count > 1 else ""
        lines.append(
            f"{x.time.tz_convert(NEW_YORK):%H:%M} {product} closed {back}{x.direction.upper()} {x.name} "
            f"{fmt(x.price)} at {fmt(x.close)} (cross #{x.count} today, last {fmt(last)})"
        )
    return lines, counts


def setup_events(product, seen, setups):
    """Lines for failed breakouts and 0DTE second touches not alerted yet (`seen` = keys already alerted).
    Returns (lines, new seen)."""
    lines, seen = [], list(seen)
    for s in setups:
        retest = isinstance(s, SecondTouch)
        key = f"{'2nd|' if retest else ''}{s.name}|{s.direction}"
        if key in seen:
            continue
        seen.append(key)
        if s.direction == "short":
            head, order, stop = f"FAILED BREAKOUT above {s.name}", "SHORT below", "stop above"
        else:
            head, order, stop = f"FAILED BREAKDOWN below {s.name}", "LONG above", "stop below"
        if retest:
            head = f"2ND TOUCH of {s.name}"
            if s.divergence is not None:
                rsi_move = f"(RSI {s.rsi_first:.0f} -> {s.rsi_second:.0f})"
                head += f" with RSI divergence {rsi_move}" if s.divergence else f" no RSI divergence {rsi_move}"
        risk = abs(s.stop - s.entry)
        reward = None if s.target is None else abs(s.entry - s.target)
        be_at = s.entry - risk if s.direction == "short" else s.entry + risk  # one risk in our favour
        if s.target is None:
            plan = f"no level beyond, trail the stop (risk {fmt(risk)})"
        else:
            plan = f"target {s.target_name} {fmt(s.target)} (risk {fmt(risk)}, reward {fmt(reward)})"
        if retest:  # still being tested in every regime: review.py's scoreboard decides if it earns an OK
            regime = {True: "above", False: "below", None: "no"}[s.positive_gamma] + " Gamma Flip"
            verdict = "SKIP: reward smaller than risk" if reward is not None and reward < risk else (
                f"TEST setup, not proven: paper-trade only ({regime})"
            )
        elif s.positive_gamma is False:
            verdict = "SKIP: below Gamma Flip, walls break more often"
        elif reward is not None and reward < risk:
            verdict = "SKIP: reward smaller than risk"
        elif s.positive_gamma is None:
            verdict = "no Gamma Flip today, check the regime"
        else:
            verdict = "OK: positive gamma, walls tend to hold"
        lines.append(
            f"{s.time.tz_convert(NEW_YORK):%H:%M} {product} {head} {fmt(s.price)}: "
            f"{order} {fmt(s.entry)}, {stop} {fmt(s.stop)}, {plan}, at {fmt(be_at)} move stop to {fmt(s.entry)} -> {verdict}"
        )
    return lines, seen


def refresh_events(product, prev, fresh, morning_flip, last, now):
    """Lines when GEX re-computed from a fresh chain changes the picture: price is now on the other side
    of the Gamma Flip, or the flip moved FLIP_MOVE_PCT since it was last reported. `prev` = {"flip",
    "side"} last reported ({} before the first refresh, which is compared with the morning flip).
    Returns (lines, new prev)."""
    flip = fresh["flip"]
    if flip is None:
        return [], prev
    if not prev:
        prev = {"flip": morning_flip, "side": flip_side(last, morning_flip)}
    side = flip_side(last, flip)
    moved = prev["flip"] is None or abs(flip - prev["flip"]) >= last * FLIP_MOVE_PCT
    if side == prev["side"] and not moved:
        return [], prev
    where = side.upper() if side != prev["side"] else f"still {side.upper()}"
    morning = "none" if morning_flip is None else fmt(morning_flip)
    line = (
        f"{now:%H:%M} {product} GEX REFRESH: Gamma Flip {fmt(flip)} (morning {morning}), price {fmt(last)} {where}"
        f" -> {REGIME[side]}; setups still use the morning flip"
    )
    return [line], {"flip": flip, "side": side}


def fresh_gex(product):
    """{"flip": Gamma Flip in futures points (None without one or without a basis), "total": GEX $bn}
    from fresh CBOE chains, priced the way gex.py prices them."""
    index_chain = load_chain(product.index_ticker)
    try:
        etf_chain = load_chain(product.etf_ticker)
    except Exception:  # the index chain alone still gives a flip
        etf_chain = None
    basis, live = fetch_futures(product)
    basis = None if basis is None else basis[0]
    spot, _ = live_spot(product, index_chain, basis, live)
    lv = analyze(product, index_chain, etf_chain, spot=spot).levels
    flip = None if lv.flip is None or basis is None else to_futures(lv.flip, basis)
    return {"flip": flip, "total": lv.total_gex_bn}


def append_refresh(path, now, product, last, fresh):
    """One row per refresh, to check later whether the refreshed flip beats the morning one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(REFRESH_FIELDS)
        flip = "" if fresh["flip"] is None else fmt(fresh["flip"])
        writer.writerow([f"{now:%Y-%m-%d %H:%M}", product, fmt(last), flip, round(fresh["total"], 3)])


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
    parser.add_argument("--refresh", type=int, default=REFRESH_MIN, help="minutes between GEX refreshes (0 = off)")
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
    refresh_path = Path(args.history).parent / "gex_refresh.csv"
    print(f"TRACKING {', '.join(names)} from {args.start} ET, checking every {args.interval}s", flush=True)
    last_beat = last_refresh = None

    while True:
        now = pd.Timestamp.now(tz=NEW_YORK)
        refresh = args.refresh > 0 and now >= start and (
            last_refresh is None or (now - last_refresh).total_seconds() >= args.refresh * 60
        )
        lasts = {}
        for n in names:
            try:
                bars = fetch_today(PRODUCTS[n])
            except Exception as e:  # network / yfinance hiccup: report and retry next round
                print(f"ERROR {n} price fetch failed: {e}", flush=True)
                continue
            rsi_series = rsi(bars["Close"])  # over all fetched bars, so it is warmed up by the open
            bars = bars[(bars.index >= start) & (bars.index.time < CLOSE)]
            if bars.empty:
                continue
            last = float(bars["Close"].iloc[-1])
            lasts[n] = last
            ref = float(bars["Open"].iloc[0])
            reactions = level_reactions(bars, levels[n], threshold=ref * THRESHOLD_PCT, tolerance=ref * TOLERANCE_PCT)
            st = state.setdefault(n, {"levels": {}, "flip": None})
            lines, st["levels"] = level_events(n, st["levels"], reactions, last)
            done = closed_bars(bars)
            crosses = level_crosses(done, levels[n], tolerance=ref * TOLERANCE_PCT)
            cross_lines, st["crosses"] = cross_events(n, st.get("crosses", {}), crosses, reactions, last)
            lines += cross_lines
            setups = failed_breakouts(done, levels[n], flips[n], tolerance=ref * TOLERANCE_PCT)
            tolerance, threshold = ref * TOLERANCE_PCT, ref * THRESHOLD_PCT
            setups += second_touches(
                done, levels[n], flips[n], tolerance=tolerance, threshold=threshold, rsi_series=rsi_series
            )
            setups.sort(key=lambda s: s.time)
            setup_lines, st["setups"] = setup_events(n, st.get("setups", []), setups)
            lines += setup_lines
            side = flip_side(last, flips[n])
            if side and st["flip"] and side != st["flip"]:
                lines.append(f"{now:%H:%M} {n} crossed {side} Gamma Flip {fmt(flips[n])} (last {fmt(last)}) -> {REGIME[side]}")
            st["flip"] = side or st["flip"]
            if refresh:
                try:
                    fresh = fresh_gex(PRODUCTS[n])
                except Exception as e:  # CBOE / yfinance hiccup: the morning levels still stand
                    print(f"ERROR {n} GEX refresh failed: {e}", flush=True)
                else:
                    append_refresh(refresh_path, now, n, last, fresh)
                    refresh_lines, st["refresh"] = refresh_events(n, st.get("refresh", {}), fresh, flips[n], last, now)
                    lines += refresh_lines
            for line in lines:
                print(line, flush=True)
        save_state(state_path, state)
        if refresh:
            last_refresh = now

        if lasts and (last_beat is None or (now - last_beat).total_seconds() >= HEARTBEAT_MIN * 60):
            parts = [
                f"{n} {fmt(v)} ({'+' if v >= flips[n] else '-'}{fmt(abs(v - flips[n]))} vs flip)" if flips[n] else f"{n} {fmt(v)}" for n, v in lasts.items()
            ]
            print(f"{now:%H:%M} STATUS " + " | ".join(parts), flush=True)
            last_beat = now
        if now.time() >= CLOSE:
            print(f"SESSION END {now:%H:%M} ET", flush=True)
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
