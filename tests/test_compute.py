import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd, numpy as np
from compute import period_returns, trend_flags


def series(n, f):
    idx = pd.bdate_range(end="2026-10-01", periods=n)
    return pd.Series([f(i) for i in range(n)], index=idx)


def test_simple_1m_return():
    s = pd.Series([100.0, 110.0], index=pd.to_datetime(["2026-09-01", "2026-10-01"]))
    assert period_returns(s)["1M"] == 10.0


def test_short_history_gives_none():
    r = period_returns(series(30, lambda i: 100 + i))
    assert r["1W"] is not None and r["1Y"] is None and r["5Y"] is None


def test_annualised_3y():
    idx = pd.to_datetime(["2023-10-01", "2026-10-01"])
    r = period_returns(pd.Series([100.0, 133.1], index=idx))
    assert abs(r["3Y"] - 10.0) < 0.05          # 1.1^3 = 1.331 -> 10% p.a.


def test_trend_up_is_brutal_strength():
    f = trend_flags(series(300, lambda i: 100 + i))
    assert f["bs"] and f["gc"] and f["a50"] and f["a200"] and not f["bw"]


def test_trend_down_is_brutal_weakness():
    f = trend_flags(series(300, lambda i: 500 - i))
    assert f["bw"] and not f["gc"] and not f["a50"] and not f["bs"]


def test_too_short_for_flags():
    assert trend_flags(series(20, lambda i: i + 1)) is None
