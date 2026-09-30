from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from gex.data import NEW_YORK
from gex.greeks import bs_gamma, dollar_gamma

PROFILE_RANGE = 0.10  # gamma profile / flip search: spot +- 10%
PROFILE_STEPS = 201
WALL_RANGE = 0.15  # walls searched within spot +- 15%
MAGNET_RANGE = 0.05  # magnets searched within spot +- 5%
N_MAGNETS = 3
MIN_HOURS = 1.0  # floor on time to expiry so 0DTE gamma doesn't blow up
HOURS_PER_YEAR = 365 * 24


@dataclass
class Levels:
    """Key levels, all in index points."""

    product: str
    index_label: str
    asof: pd.Timestamp
    spot: float
    total_gex_bn: float  # $bn dealers trade per 1% move
    regime: str  # "positive" or "negative"
    flip: float | None
    call_wall: float | None
    put_wall: float | None
    next_expiry: date | None
    next_call_wall: float | None
    next_put_wall: float | None
    magnets: list
    em_low: float
    em_high: float
    sources: list


@dataclass
class Analysis:
    levels: Levels
    by_strike: pd.DataFrame  # call_gex, put_gex, net_gex in $bn per 1%, by strike bin
    profile_x: np.ndarray  # index levels
    profile_y: np.ndarray  # net GEX in $bn per 1% if the index were at that level


def combine(index_chain, etf_chain=None):
    """Stack index and ETF options in index units and add time to expiry `t` in years.

    ETF strikes are scaled by index/ETF price and the contract multiplier shrinks by the same
    ratio, so each ETF option keeps its dollar gamma.
    """
    frames = [index_chain.options.assign(source="index")]
    if etf_chain is not None and not etf_chain.options.empty:
        ratio = index_chain.spot / etf_chain.spot
        etf = etf_chain.options.assign(source="etf")
        etf["strike"] = etf["strike"] * ratio
        etf["multiplier"] = etf["multiplier"] / ratio
        frames.append(etf)

    opts = pd.concat(frames, ignore_index=True)
    hours = (opts["expiry"] - index_chain.timestamp).dt.total_seconds() / 3600
    opts["t"] = np.maximum(hours, MIN_HOURS) / HOURS_PER_YEAR
    return opts


def signed_gex(opts, spot):
    """Dollar gamma per option if the index is at `spot`: calls +, puts - (dealers long calls, short puts)."""
    gamma = bs_gamma(spot, opts["strike"].to_numpy(), opts["iv"].to_numpy(), opts["t"].to_numpy())
    sign = np.where(opts["type"].to_numpy() == "C", 1.0, -1.0)
    return sign * dollar_gamma(gamma, opts["oi"].to_numpy(), opts["multiplier"].to_numpy(), spot)


def gex_by_strike(opts, spot, step):
    """GEX in $bn per 1% move, grouped into strike bins of width `step`."""
    gex = signed_gex(opts, spot) / 1e9
    is_call = opts["type"].to_numpy() == "C"
    by_strike = (
        pd.DataFrame(
            {
                "strike": (opts["strike"].to_numpy() / step).round() * step,
                "call_gex": np.where(is_call, gex, 0.0),
                "put_gex": np.where(is_call, 0.0, gex),
            }
        )
        .groupby("strike")
        .sum()
    )
    by_strike["net_gex"] = by_strike["call_gex"] + by_strike["put_gex"]
    return by_strike


def gamma_profile(opts, spot):
    """Net GEX ($bn per 1%) on a grid of hypothetical index levels around spot."""
    grid = np.linspace(spot * (1 - PROFILE_RANGE), spot * (1 + PROFILE_RANGE), PROFILE_STEPS)
    profile = np.array([signed_gex(opts, level).sum() for level in grid]) / 1e9
    return grid, profile


def find_flip(grid, profile, spot):
    """Zero crossing of the profile closest to spot, linearly interpolated. None if no crossing."""
    sign = np.where(profile >= 0, 1, -1)
    idx = np.where(sign[:-1] != sign[1:])[0]
    if len(idx) == 0:
        return None
    x0, x1, y0, y1 = grid[idx], grid[idx + 1], profile[idx], profile[idx + 1]
    crossings = x0 - y0 * (x1 - x0) / (y1 - y0)
    return float(crossings[np.abs(crossings - spot).argmin()])


def _near(by_strike, spot, pct):
    return by_strike[np.abs(by_strike.index.to_numpy() - spot) <= spot * pct]


def walls(by_strike, spot):
    """(call wall, put wall): strikes with the most positive and most negative net GEX near spot.

    Net, not calls/puts separately: round strikes often carry huge offsetting call and put
    open interest (conversions, collars) that would otherwise make one strike both walls.
    """
    net = _near(by_strike, spot, WALL_RANGE)["net_gex"]
    call_wall = float(net.idxmax()) if (net > 0).any() else None
    put_wall = float(net.idxmin()) if (net < 0).any() else None
    return call_wall, put_wall


def magnets(by_strike, spot, exclude=()):
    """Biggest abs(net GEX) strikes close to spot, strongest first, skipping `exclude`."""
    near = _near(by_strike, spot, MAGNET_RANGE)
    near = near.drop(index=[s for s in exclude if s is not None and s in near.index])
    ranked = near["net_gex"].abs().sort_values(ascending=False)
    return [float(s) for s in ranked[ranked > 0].index[:N_MAGNETS]]


def expected_move(opts, spot, iv30):
    """(low, high) = spot -+ ATM straddle of the nearest expiry; fallback: 1-day move from iv30.

    `opts` must hold index options only (ETF prices are in ETF dollars).
    """
    quoted = opts[(opts["bid"] > 0) & (opts["ask"] > 0)]
    if not quoted.empty:
        day = quoted["expiry"].dt.tz_convert(NEW_YORK).dt.date
        nearest = quoted[day == day.min()]
        # On monthly expiry days take the PM-settled weekly, not the AM-settled monthly
        nearest = nearest[nearest["expiry"] == nearest["expiry"].max()]
        mid = (nearest["bid"] + nearest["ask"]) / 2
        calls = mid[nearest["type"] == "C"].groupby(nearest["strike"]).mean()
        puts = mid[nearest["type"] == "P"].groupby(nearest["strike"]).mean()
        straddle = (calls + puts).dropna()
        if not straddle.empty:
            atm = straddle.index[np.abs(straddle.index.to_numpy() - spot).argmin()]
            move = float(straddle[atm])
            return spot - move, spot + move

    move = spot * (iv30 or 0) / 100 / np.sqrt(252)
    return spot - move, spot + move


def analyze(product, index_chain, etf_chain=None):
    """Compute all levels for a product from its index chain and optional ETF chain."""
    opts = combine(index_chain, etf_chain)
    if opts.empty:
        raise ValueError(f"No live options with open interest in {index_chain.ticker}")

    spot = index_chain.spot
    step = product.strike_step
    by_strike = gex_by_strike(opts, spot, step)
    grid, profile = gamma_profile(opts, spot)
    total = float(by_strike["net_gex"].sum())
    call_wall, put_wall = walls(by_strike, spot)

    expiry_day = opts["expiry"].dt.tz_convert(NEW_YORK).dt.date
    next_expiry = expiry_day.min()
    next_call_wall, next_put_wall = walls(
        gex_by_strike(opts[expiry_day == next_expiry], spot, step), spot
    )
    em_low, em_high = expected_move(opts[opts["source"] == "index"], spot, index_chain.iv30)

    sources = [index_chain.ticker]
    if (opts["source"] == "etf").any():
        sources.append(etf_chain.ticker)

    levels = Levels(
        product=product.name,
        index_label=product.index_label,
        asof=index_chain.timestamp,
        spot=spot,
        total_gex_bn=total,
        regime="positive" if total > 0 else "negative",
        flip=find_flip(grid, profile, spot),
        call_wall=call_wall,
        put_wall=put_wall,
        next_expiry=next_expiry,
        next_call_wall=next_call_wall,
        next_put_wall=next_put_wall,
        magnets=magnets(by_strike, spot, exclude=(call_wall, put_wall)),
        em_low=em_low,
        em_high=em_high,
        sources=sources,
    )
    return Analysis(levels=levels, by_strike=by_strike, profile_x=grid, profile_y=profile)
