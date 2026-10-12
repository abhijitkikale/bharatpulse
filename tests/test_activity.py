import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import numpy as np
import pandas as pd

from compute import risk_stats
from compute_activity import build_activity


def _bhav():
    days = pd.bdate_range("2026-09-01", periods=25)
    rows = []
    for i, d in enumerate(days):
        last = i == len(days) - 1
        # AAA: steady volume, then a 5x volume spike on the last day with a price rise
        rows.append(dict(SYMBOL="AAA", SERIES="EQ", PREV_CLOSE=100, CLOSE_PRICE=106 if last else 101, TTL_TRD_QNTY=500000 if last else 100000, TURNOVER_LACS=530 if last else 101, DELIV_PER=60, date=d))
        # BBB: tiny turnover but a huge volume ratio -> must be ignored as noise
        rows.append(dict(SYMBOL="BBB", SERIES="EQ", PREV_CLOSE=10, CLOSE_PRICE=9 if last else 10, TTL_TRD_QNTY=900000 if last else 1000, TURNOVER_LACS=81 if last else 1, DELIV_PER=20, date=d))
        # CCC: falls, normal volume
        rows.append(dict(SYMBOL="CCC", SERIES="EQ", PREV_CLOSE=50, CLOSE_PRICE=49, TTL_TRD_QNTY=200000, TURNOVER_LACS=98, DELIV_PER=40, date=d))
        # SME stock must be excluded from everything
        rows.append(dict(SYMBOL="SMEX", SERIES="SM", PREV_CLOSE=20, CLOSE_PRICE=21, TTL_TRD_QNTY=10 ** 7, TURNOVER_LACS=2100, DELIV_PER=10, date=d))
    return pd.DataFrame(rows)


def test_breadth_counts_exclude_sme_and_use_prev_close():
    a = build_activity(_bhav())
    assert a["breadth"]["adv"][-1] == 1 and a["breadth"]["dec"][-1] == 2 and a["breadth"]["unch"][-1] == 0
    assert a["n_stocks"] == 3 and a["asof"] == str(pd.bdate_range("2026-09-01", periods=25)[-1].date())


def test_spike_found_and_noise_filtered():
    a = build_activity(_bhav())
    syms = [s["s"] for s in a["spikes"]]
    assert syms == ["AAA"]                       # BBB has a huge ratio but under the turnover floor; SME excluded
    assert a["spikes"][0]["x"] == 5.0 and a["spikes"][0]["chg"] == 6.0


def test_leaders_sorted_by_turnover_and_volume():
    a = build_activity(_bhav())
    assert a["leaders"]["turnover"][0]["s"] == "AAA"
    assert a["leaders"]["volume"][0]["s"] == "BBB"        # most shares traded


def test_beta_of_a_levered_stock_and_drawdown():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2025-06-01", periods=300)
    bench = pd.Series(100 * np.cumprod(1 + rng.normal(0.0004, 0.01, 300)), index=idx)
    stock = bench.pct_change().fillna(0) * 1.5
    stock = pd.Series(100 * np.cumprod(1 + stock), index=idx)           # exactly 1.5x the benchmark's daily moves
    s = risk_stats(stock, bench)
    assert abs(s["beta"] - 1.5) < 0.02 and s["corr"] > 0.99
    assert s["mdd"] < 0 and 5 < s["vol"] < 40
    assert risk_stats(stock.head(40), bench)["beta"] is None            # too little history
