import pandas as pd

BASIS_MINUTES = 15  # median over the last N common minutes to smooth out quote noise


def basis_from_closes(fut, cash):
    """(futures - index, timestamp) over the last common minutes; None if they never overlap."""
    both = pd.concat([fut, cash], axis=1, join="inner").dropna()
    if both.empty:
        return None
    recent = both.tail(BASIS_MINUTES)
    return float((recent.iloc[:, 0] - recent.iloc[:, 1]).median()), both.index[-1]


def last_price(series):
    """(last price, its time), or None if the series has no prices."""
    series = series.dropna()
    if series.empty:
        return None
    return float(series.iloc[-1]), series.index[-1]


def fetch_futures(product):
    """(basis, futures price) from yfinance 1-minute bars: basis_from_closes() and last_price() of
    the futures, each None when missing. While the index is closed the basis is from its last close."""
    import yfinance as yf

    # One symbol at a time: yfinance's parallel download can lock its cache database
    closes = [
        yf.Ticker(symbol).history(period="5d", interval="1m")["Close"]
        for symbol in (product.future_symbol, product.index_symbol)
    ]
    return basis_from_closes(*closes), last_price(closes[0])
