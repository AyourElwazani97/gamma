import numpy as np

SQRT_2PI = np.sqrt(2 * np.pi)


def bs_gamma(spot, strike, iv, t):
    """Black-Scholes gamma (r = q = 0). Broadcasts; returns 0 where inputs are invalid."""
    spot, strike, iv, t = np.broadcast_arrays(
        *(np.asarray(x, dtype=float) for x in (spot, strike, iv, t))
    )
    valid = (spot > 0) & (strike > 0) & (iv > 0) & (t > 0)
    vol_sqrt_t = np.where(valid, iv * np.sqrt(np.where(valid, t, 1.0)), 1.0)
    safe_spot = np.where(valid, spot, 1.0)
    d1 = (np.log(safe_spot / np.where(valid, strike, 1.0)) + 0.5 * vol_sqrt_t**2) / vol_sqrt_t
    gamma = np.exp(-0.5 * d1**2) / (SQRT_2PI * safe_spot * vol_sqrt_t)
    return np.where(valid, gamma, 0.0)


def dollar_gamma(gamma, oi, multiplier, spot):
    """Dollars dealers must trade per 1% move in the underlying."""
    return gamma * oi * multiplier * spot**2 * 0.01
