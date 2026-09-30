import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
ASOF = pd.Timestamp("2026-09-30 13:00:00", tz="UTC")


def make_opts(rows, source="index"):
    """Build an options frame like levels.combine() returns.

    rows: (type, strike, oi, iv, days_to_expiry[, bid, ask])
    """
    records = []
    for row in rows:
        opt_type, strike, oi, iv, days = row[:5]
        bid, ask = row[5:7] if len(row) > 5 else (0.0, 0.0)
        records.append(
            {
                "root": "TEST",
                "expiry": ASOF + pd.Timedelta(days=days),
                "type": opt_type,
                "strike": float(strike),
                "iv": iv,
                "oi": float(oi),
                "bid": bid,
                "ask": ask,
                "multiplier": 100.0,
                "source": source,
                "t": days / 365,
            }
        )
    return pd.DataFrame(records)


@pytest.fixture
def ndx_raw():
    import json

    with open(FIXTURES / "NDX.json") as f:
        return json.load(f)
