from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    name: str  # futures product traded, e.g. "ES"
    index_label: str  # index the levels are computed on, e.g. "SPX"
    index_ticker: str  # CBOE chain for the index (indices need a leading "_")
    etf_ticker: str  # CBOE chain for the ETF tracking the index
    future_symbol: str  # yfinance symbol of the front-month future
    index_symbol: str  # yfinance symbol of the cash index
    strike_step: float  # strike bin width in index points


PRODUCTS = {
    "ES": Product("ES", "SPX", "_SPX", "SPY", "ES=F", "^GSPC", 5),
    "NQ": Product("NQ", "NDX", "_NDX", "QQQ", "NQ=F", "^NDX", 10),
}

TICK = 0.25  # ES and NQ minimum price increment
