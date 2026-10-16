import json
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path

import pandas as pd
import requests

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"
NEW_YORK = "America/New_York"
# Standard monthly index options settle at the open; weeklies (SPXW, NDXP) at the close
AM_SETTLED_ROOTS = {"SPX", "NDX"}
SYMBOL_PATTERN = r"^(?P<root>[A-Z]+)(?P<expiry>\d{6})(?P<type>[CP])(?P<strike>\d{8})$"
OPEN, CLOSE = time(9, 30), time(16, 0)  # regular session, New York
STALE_PRICE = pd.Timedelta(minutes=30)  # CBOE runs 15 minutes late; older = the index isn't trading


@dataclass
class Chain:
    ticker: str
    spot: float
    timestamp: pd.Timestamp  # UTC
    iv30: float | None
    options: pd.DataFrame  # root, expiry, type, strike, iv, oi, bid, ask, multiplier
    price_time: pd.Timestamp | None = None  # UTC time of `spot`
    close: float | None = None  # last regular-session close; ETFs trade around it pre/post-market


def session_date(asof):
    """Regular session the levels are for: same day until the 16:00 close, else the next weekday."""
    day = asof.date()
    if asof.time() >= CLOSE:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def fetch_raw(ticker, timeout=30):
    """Download the delayed option chain from CBOE."""
    response = requests.get(
        CBOE_URL.format(ticker=ticker), timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    return response.json()


def load_chain(ticker, from_dir=None):
    """Fetch a fresh chain, or read `{from_dir}/{ticker}.json` when given."""
    if from_dir is None:
        raw = fetch_raw(ticker)
    else:
        with open(Path(from_dir) / f"{ticker}.json") as f:
            raw = json.load(f)
    return parse_chain(raw, ticker)


def parse_chain(raw, ticker):
    """Turn CBOE JSON into a Chain holding only live options with open interest and IV."""
    data = raw["data"]
    timestamp = pd.Timestamp(raw["timestamp"], tz="UTC")

    df = pd.DataFrame(data["options"])
    parts = df["option"].str.extract(SYMBOL_PATTERN)
    df = df.loc[parts["root"].notna()]
    parts = parts.loc[df.index]

    expiry_date = pd.to_datetime(parts["expiry"], format="%y%m%d")
    close_time = parts["root"].isin(AM_SETTLED_ROOTS).map(
        {True: pd.Timedelta(hours=9, minutes=30), False: pd.Timedelta(hours=16)}
    )
    options = pd.DataFrame(
        {
            "root": parts["root"],
            "expiry": (expiry_date + close_time).dt.tz_localize(NEW_YORK),
            "type": parts["type"],
            "strike": parts["strike"].astype(float) / 1000,
            "iv": df["iv"].astype(float),
            "oi": df["open_interest"].astype(float),
            "bid": df["bid"].astype(float),
            "ask": df["ask"].astype(float),
            "multiplier": 100.0,
        }
    )
    live = (options["expiry"] > timestamp) & (options["oi"] > 0) & (options["iv"] > 0)

    last_trade = data.get("last_trade_time")
    return Chain(
        ticker=ticker,
        spot=float(data["current_price"]),
        timestamp=timestamp,
        iv30=data.get("iv30"),
        options=options.loc[live].reset_index(drop=True),
        price_time=pd.Timestamp(last_trade, tz=NEW_YORK).tz_convert("UTC") if last_trade else None,
        close=float(data["close"]) if data.get("close") else None,
    )


def index_closed(chain):
    """True when the chain's index price is an old close (before the open, after the close)."""
    return chain.price_time is not None and chain.timestamp - chain.price_time > STALE_PRICE
