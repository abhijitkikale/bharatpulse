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


def test_forward_pe_matches_hand_calc():
    from compute import build_forward
    idx = pd.date_range("2026-08-01", periods=60, freq="D")
    px = pd.DataFrame({"A.NS": 100.0, "B.NS": 200.0, "C.NS": 50.0}, index=idx)
    mcap = {"A.NS": {"v": 1000}, "B.NS": {"v": 3000}, "C.NS": {"v": 1000}}
    feps = {"A.NS": {"e": 10.0}, "B.NS": {"e": 10.0}, "C.NS": {"e": 5.0}}   # P/E 10, 20, 10
    out = build_forward({"X": ["A", "B", "C"]}, px, mcap, feps)
    # earnings = 1000/10 + 3000/20 + 1000/10 = 350 ; mcap = 5000 ; forward P/E = 14.29
    assert abs(out["X"]["now"] - 14.29) < 0.01 and out["X"]["cov"] == 100
    assert abs(out["X"]["est"]["v"][-1] - 14.29) < 0.01      # latest estimate equals today's value
