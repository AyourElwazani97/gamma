import pandas as pd

BASIS_MINUTES = 15  # median over the last N common minutes to smooth out quote noise


def basis_from_closes(fut, cash):
    """(futures - index, timestamp) over the last common minutes; None if they never overlap."""
    both = pd.concat([fut, cash], axis=1, join="inner").dropna()
    if both.empty:
        return None
    recent = both.tail(BASIS_MINUTES)
    return float((recent.iloc[:, 0] - recent.iloc[:, 1]).median()), both.index[-1]


def fetch_basis(product):
    """Live basis from yfinance 1-minute bars. Returns (basis, timestamp) or None."""
    import yfinance as yf

    # One symbol at a time: yfinance's parallel download can lock its cache database
    closes = [
        yf.Ticker(symbol).history(period="5d", interval="1m")["Close"]
        for symbol in (product.future_symbol, product.index_symbol)
    ]
    return basis_from_closes(*closes)
