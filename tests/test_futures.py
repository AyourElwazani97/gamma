import pandas as pd
import pytest

from gex.futures import basis_from_closes


def test_basis_uses_last_common_timestamp():
    idx = pd.date_range("2026-09-30 19:57", periods=3, freq="min", tz="UTC")
    fut = pd.Series([7690.0, 7691.0, 7692.0, 7695.0], index=idx.append(pd.DatetimeIndex([idx[-1] + pd.Timedelta(minutes=5)])))
    cash = pd.Series([7650.0, 7650.5, 7651.5], index=idx)
    basis, when = basis_from_closes(fut, cash)
    assert basis == pytest.approx(40.5)
    assert when == idx[-1]


def test_basis_ignores_missing_values():
    idx = pd.date_range("2026-09-30 19:58", periods=2, freq="min", tz="UTC")
    fut = pd.Series([7690.0, float("nan")], index=idx)
    cash = pd.Series([7650.0, 7651.0], index=idx)
    assert basis_from_closes(fut, cash)[0] == pytest.approx(40.0)


def test_basis_none_without_overlap():
    fut = pd.Series([1.0], index=pd.DatetimeIndex(["2026-09-30 10:00"], tz="UTC"))
    cash = pd.Series([1.0], index=pd.DatetimeIndex(["2026-09-30 11:00"], tz="UTC"))
    assert basis_from_closes(fut, cash) is None
