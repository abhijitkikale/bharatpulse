import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import live  # noqa: E402


def _frame(days, start_price=23000.0, minutes=60):
    rows, idx = [], []
    rng = np.random.default_rng(1)
    p = start_price
    for d in days:
        t0 = pd.Timestamp(f"{d} 09:15", tz="Asia/Kolkata")
        for m in range(minutes):
            o = p
            c = o + rng.normal(0, 3)
            rows.append([o, max(o, c) + 1, min(o, c) - 1, c, 1000 + 10 * m])
            idx.append(t0 + pd.Timedelta(minutes=m))
            p = c
    return pd.DataFrame(rows, columns=["o", "h", "l", "c", "v"], index=pd.DatetimeIndex(idx))


DAYS = ["2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]


def test_live_snapshot_has_today_profile_and_weekly():
    now = pd.Timestamp("2026-10-30 10:30", tz="Asia/Kolkata")
    out = live.build_live({"NIFTY": _frame(DAYS)}, now)
    b = out["symbols"]["NIFTY"]
    assert out["date"] == "2026-10-30" and b["vp"]["1"]["to"] == "2026-10-30"
    assert b["vp"]["5"]["days"] == 5 and b["vp"]["5"]["to"] == "2026-10-30"
    assert b["session"]["date"] == "2026-10-30" and b["vp"]["1"]["val"] <= b["vp"]["1"]["poc"] <= b["vp"]["1"]["vah"]
    assert b["spot"] == round(float(_frame(DAYS)["c"].iloc[-1]), 2)


def test_no_snapshot_when_there_are_no_candles_for_today():
    now = pd.Timestamp("2026-10-31 10:30", tz="Asia/Kolkata")          # candles end on the 30th: a holiday / stale token
    assert live.build_live({"NIFTY": _frame(DAYS)}, now) is None
