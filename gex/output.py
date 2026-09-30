import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from gex.config import TICK
from gex.data import NEW_YORK

CHART_RANGE = 0.06  # chart shows spot +- 6%
STALE_DAYS = 3

BG, GRID, TEXT = "#131722", "#2a2e39", "#d1d4dc"
GREEN, RED, YELLOW, PURPLE, GRAY, WHITE = "#26a69a", "#ef5350", "#f5c542", "#b388ff", "#9e9e9e", "#ffffff"

PLAYBOOK = {
    "positive": [
        "Dealers DAMPEN moves: expect ranges and mean reversion.",
        "- Fade moves into the Call Wall / Put Wall and the expected-move edges.",
        "- Take profits at magnets. Breakouts often fail while above the flip.",
        "- Losing the Gamma Flip = volatility can expand fast.",
    ],
    "negative": [
        "Dealers AMPLIFY moves: expect bigger, faster swings and trends.",
        "- Don't fade strong moves blindly; trade breaks/retests with the trend.",
        "- Smaller size, wider stops. Walls can break instead of holding.",
        "- Reclaiming the Gamma Flip = market usually calms down.",
    ],
}


@dataclass
class Row:
    name: str
    index_price: float
    meaning: str


def to_futures(price, basis):
    """Index price -> futures price, rounded to the futures tick. basis None = no shift."""
    return round((price + (basis or 0.0)) / TICK) * TICK


def fmt(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


def level_rows(lv):
    """Levels as a price ladder, highest first. Levels at the same price are merged."""
    day = f"{lv.next_expiry:%a %b %d}" if lv.next_expiry else "next expiry"
    candidates = [
        ("Call Wall", lv.call_wall, "Biggest resistance: rallies tend to stall or reject here"),
        ("0DTE Call Wall", lv.next_call_wall, f"Resistance for {day} (that day's expiry only)"),
        ("Expected Move High", lv.em_high, f"Top of the options-implied range for {day}"),
        ("Gamma Flip", lv.flip, "Regime line: above = calm/ranging, below = fast/trending"),
        *[(f"Magnet {i}", m, "Price gets pulled here and often pins") for i, m in enumerate(lv.magnets, 1)],
        ("Expected Move Low", lv.em_low, f"Bottom of the options-implied range for {day}"),
        ("0DTE Put Wall", lv.next_put_wall, f"Support for {day} (that day's expiry only)"),
        ("Put Wall", lv.put_wall, "Biggest support: if it breaks, selling can speed up"),
    ]
    merged = {}
    for name, price, meaning in candidates:
        if price is None:
            continue
        if price in merged:
            merged[price].name += " + " + name
            merged[price].meaning += " | " + meaning
        else:
            merged[price] = Row(name, price, meaning)
    return sorted(merged.values(), key=lambda r: r.index_price, reverse=True)


def tradingview_string(lv, basis):
    """`price=Name;price=Name` in futures points, read by tradingview/gex_levels.pine."""
    return ";".join(f"{fmt(to_futures(r.index_price, basis))}={r.name}" for r in level_rows(lv))


def format_report(lv, basis, basis_note=None):
    asof = lv.asof.tz_convert(NEW_YORK)
    fut_spot = to_futures(lv.spot, basis)
    lines = [
        "",
        f"=== {lv.product} gamma levels (from {' + '.join(s.lstrip('_') for s in lv.sources)} options) ===",
        f"Data: {asof:%a %Y-%m-%d %H:%M} New York (CBOE, 15-min delayed)",
    ]
    age_days = (pd.Timestamp.now(tz="UTC") - lv.asof).days
    if age_days >= STALE_DAYS:
        lines.append(f"WARNING: this data is {age_days} days old - do not trade it.")
    if basis is None:
        lines.append(
            f"WARNING: no {lv.product}-{lv.index_label} basis. {lv.product} column = {lv.index_label} points, NOT real "
            f"{lv.product} prices. Rerun with --basis <{lv.product} minus {lv.index_label}>."
        )
    else:
        note = f" ({basis_note})" if basis_note else ""
        lines.append(f"Basis {lv.product} - {lv.index_label}: {basis:+.2f}{note}")
    lines.append(f"Total GEX: {lv.total_gex_bn:+.2f} $bn per 1% move")

    if lv.flip is None:
        where = "no flip within +-10%"
    else:
        side = "above" if lv.spot > lv.flip else "below"
        where = f"price {side} the Gamma Flip"
    lines += ["", f"REGIME: {lv.regime.upper()} GAMMA ({where})"]
    lines += ["  " + line for line in PLAYBOOK[lv.regime]]

    name_w = max([len(r.name) for r in level_rows(lv)] + [12])
    header = f"  {'Level':<{name_w}}  {lv.product:>9}  {lv.index_label:>9}  {'Dist':>6}  Meaning"
    lines += ["", header, "  " + "-" * (len(header) + 30)]
    now_row = f"  {'>>> LAST PRICE':<{name_w}}  {fmt(fut_spot):>9}  {fmt(lv.spot):>9}  {0:>6}  <<<"
    printed_now = False
    for r in level_rows(lv):
        if not printed_now and r.index_price < lv.spot:
            lines.append(now_row)
            printed_now = True
        fut = to_futures(r.index_price, basis)
        lines.append(
            f"  {r.name:<{name_w}}  {fmt(fut):>9}  {fmt(r.index_price):>9}  {fut - fut_spot:>+6.0f}  {r.meaning}"
        )
    if not printed_now:
        lines.append(now_row)
    return "\n".join(lines)


HISTORY_FIELDS = [
    "asof_ny", "product", "index_spot", "basis", "fut_spot", "regime", "total_gex_bn", "flip",
    "call_wall", "put_wall", "next_expiry", "next_call_wall", "next_put_wall",
    "magnet_1", "magnet_2", "magnet_3", "em_low", "em_high",
]  # fmt: skip


def append_history(lv, basis, path):
    """Append one row of levels (futures points) so you can later review how price reacted."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def fut(x):
        return "" if x is None else fmt(to_futures(x, basis))

    magnets = lv.magnets + [None] * (3 - len(lv.magnets))
    row = {
        "asof_ny": f"{lv.asof.tz_convert(NEW_YORK):%Y-%m-%d %H:%M}",
        "product": lv.product,
        "index_spot": fmt(lv.spot),
        "basis": "" if basis is None else round(basis, 2),
        "fut_spot": fut(lv.spot),
        "regime": lv.regime,
        "total_gex_bn": round(lv.total_gex_bn, 3),
        "flip": fut(lv.flip),
        "call_wall": fut(lv.call_wall),
        "put_wall": fut(lv.put_wall),
        "next_expiry": lv.next_expiry or "",
        "next_call_wall": fut(lv.next_call_wall),
        "next_put_wall": fut(lv.next_put_wall),
        "magnet_1": fut(magnets[0]),
        "magnet_2": fut(magnets[1]),
        "magnet_3": fut(magnets[2]),
        "em_low": fut(lv.em_low),
        "em_high": fut(lv.em_high),
    }
    is_new = not path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _level_color(name):
    for key, color in (("Call", GREEN), ("Put", RED), ("Flip", YELLOW), ("Expected", PURPLE)):
        if key in name:
            return color
    return GRAY


def plot_levels(analysis, basis, path):
    """Save a 2-panel chart (GEX by strike + gamma profile) in futures points."""
    lv = analysis.levels
    shift = basis or 0.0
    lo, hi = lv.spot * (1 - CHART_RANGE), lv.spot * (1 + CHART_RANGE)
    by_strike = analysis.by_strike
    by_strike = by_strike[(by_strike.index >= lo) & (by_strike.index <= hi)]

    fig = Figure(figsize=(14, 9), facecolor=BG)
    ax_bar, ax_prof = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 2]})

    x = by_strike.index.to_numpy() + shift
    width = np.min(np.diff(x)) * 0.8 if len(x) > 1 else 1.0
    net = by_strike["net_gex"].to_numpy()
    ax_bar.bar(x, net, width=width, color=np.where(net >= 0, GREEN, RED))
    ax_bar.set_ylabel("Net GEX ($bn / 1%)")

    px, py = analysis.profile_x + shift, analysis.profile_y
    ax_prof.plot(px, py, color=WHITE, lw=1.5)
    ax_prof.fill_between(px, py, 0, where=py >= 0, color=GREEN, alpha=0.25)
    ax_prof.fill_between(px, py, 0, where=py < 0, color=RED, alpha=0.25)
    ax_prof.set_ylabel("GEX if price moves here ($bn / 1%)")
    ax_prof.set_xlabel(f"{lv.product} price" if basis is not None else f"{lv.index_label} price")

    for ax in (ax_bar, ax_prof):
        ax.set_facecolor(BG)
        ax.grid(color=GRID, lw=0.6)
        ax.tick_params(colors=TEXT)
        ax.yaxis.label.set_color(TEXT)
        ax.xaxis.label.set_color(TEXT)
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.axhline(0, color=GRAY, lw=0.8)
        ax.axvline(to_futures(lv.spot, basis), color=WHITE, lw=1.5, ls="--")
        for r in level_rows(lv):
            ax.axvline(to_futures(r.index_price, basis), color=_level_color(r.name), lw=1.2, alpha=0.9)
    ax_bar.set_xlim(lo + shift, hi + shift)

    top = ax_bar.get_ylim()[1]
    for i, r in enumerate(level_rows(lv)):
        price = to_futures(r.index_price, basis)
        # Alternate label heights so neighbouring levels don't overlap
        ax_bar.text(price, top if i % 2 == 0 else top * 0.45, f" {r.name} {fmt(price)}", rotation=90,
                    va="top", ha="right", fontsize=8, color=_level_color(r.name))  # fmt: skip
    ax_bar.text(to_futures(lv.spot, basis), top * 0.02, f" LAST {fmt(to_futures(lv.spot, basis))}",
                rotation=90, va="bottom", ha="left", fontsize=8, color=WHITE)  # fmt: skip

    asof = lv.asof.tz_convert(NEW_YORK)
    fig.suptitle(
        f"{lv.product} gamma levels  |  {lv.regime.upper()} GAMMA  |  total {lv.total_gex_bn:+.1f} $bn/1%"
        f"  |  {asof:%Y-%m-%d %H:%M} NY",
        color=TEXT,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=BG)
