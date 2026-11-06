import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import stream_levels  # noqa: E402


def _frame(days, minutes=60, start=23000.0):
    rng = np.random.default_rng(5)
    rows, idx, p = [], [], start
    for d in days:
        t0 = pd.Timestamp(f"{d} 09:15", tz="Asia/Kolkata")
        for m in range(minutes):
            o = p
            c = o + rng.normal(0, 3)
            rows.append([o, max(o, c) + 1, min(o, c) - 1, c, 1000 + 7 * m])
            idx.append(t0 + pd.Timedelta(minutes=m))
            p = c
    return pd.DataFrame(rows, columns=["o", "h", "l", "c", "v"], index=pd.DatetimeIndex(idx))


def _D(total_cr=5.0):
    ks = [23800.0 + 50 * i for i in range(9)]
    return {"options": {"symbols": {"NIFTY": {
        "chain": {"asof": "2026-11-05", "spot": 24000.0, "expiries": [{"e": "2026-11-10", "pain": 24000.0, "k": ks,
                  "coi": [10, 10, 10, 10, 10, 90, 10, 10, 10], "poi": [10, 70, 10, 10, 10, 10, 10, 10, 10]}]},
        "gex": {"nearest": {"flip": 23950.0, "total_cr": total_cr, "walls": {"call_gex": 24100, "put_gex": 23900}}},
        "vp": {"1": {"poc": 23990.0, "vah": 24020.0, "val": 23960.0}, "5": {"poc": 23900.0, "vah": 24050.0, "val": 23800.0}}}}}}


def test_eod_levels_follow_the_dashboard_text_rules():
    e = stream_levels.eod_levels(_D(), "NIFTY")
    lv = e["levels"]
    assert e["close"] == "2026-11-05" and lv["OI call wall"] == 24050.0 and lv["OI put wall"] == 23850.0     # biggest OI at / above and at / below spot
    assert lv["GEX call wall"] == 24100 and lv["Gamma flip"] == 23950.0 and lv["Weekly VAL"] == 23800.0 and lv["POC"] == 23990.0
    assert e["positive"] is True and e["pos_above"] is True                  # spot 24000 is above the flip and net GEX is positive
    assert stream_levels.eod_levels(_D(-3.0))["positive"] is False and stream_levels.eod_levels(_D(-3.0))["pos_above"] is False


def test_parse_pages_reads_the_embedded_data():
    html = "<script>const x=1;const D = " + json.dumps({"a": [1, 2], "b": "}"}) + ";\nconst S = {};</script>"
    assert stream_levels.parse_pages(html) == {"a": [1, 2], "b": "}"}


def test_daily_vwap_restarts_each_session_and_weekly_restarts_on_monday():
    df = _frame(["2026-11-05", "2026-11-06", "2026-11-09"])            # Thu, Fri, Mon
    daily, weekly = stream_levels.vwaps(df)
    first = lambda s, day: s[[str(x) == day for x in s.index.date]].iloc[0]
    tp = lambda day: ((df["h"] + df["l"] + df["c"]) / 3)[[str(x) == day for x in df.index.date]].iloc[0]
    assert abs(first(daily, "2026-11-06") - tp("2026-11-06")) < 1e-9          # a new session starts from its own first candle
    assert abs(first(weekly, "2026-11-09") - tp("2026-11-09")) < 1e-9          # the new week (Monday) too
    assert abs(first(weekly, "2026-11-06") - tp("2026-11-06")) > 1e-9                                            # Friday's weekly VWAP still carries Thursday


def test_now_levels_and_state_regime():
    df = _frame(["2026-11-04", "2026-11-05", "2026-11-06"])
    n = stream_levels.now_levels(df)
    assert n["Now VAL"] <= n["Now POC"] <= n["Now VAH"] and n["Now Weekly VAL"] <= n["Now Weekly POC"] <= n["Now Weekly VAH"]
    eod = stream_levels.eod_levels(_D())
    last = float(df["c"].iloc[-1])
    live = {"spot": last, "net_cr": -4.0, "expiry": "2026-11-10", "time": "10:30",
            "levels": {"flip": last - 40.0, "gex_call": last + 100.0, "gex_put": last - 100.0, "oi_call": None, "oi_put": None, "pain": last}}
    st = stream_levels.build_state(df, eod, live, pd.Timestamp("2026-11-06 10:30", tz="Asia/Kolkata"), "live")
    names = {l["name"] for l in st["levels"]}
    assert {"Live Gamma flip", "Live GEX call wall", "Now VWAP", "Now Weekly VWAP", "Now POC", "GEX call wall"} <= names and "Live OI call wall" not in names
    assert st["regime"]["eod"] == "POSITIVE" and st["regime"]["now"] == "NEGATIVE" and st["regime"]["now_time"] == "10:30"
    assert len(st["candles"]) == 120 and st["candles"][0]["time"] % 60 == 0              # two sessions of one-minute candles
    # message made with spot below the flip and net GEX negative: positive gamma applies ABOVE the flip; price has since moved above it
    live["spot"] = last - 100.0
    live["levels"]["flip"] = last - 50.0
    st2 = stream_levels.build_state(df, eod, live, pd.Timestamp("2026-11-06 10:30", tz="Asia/Kolkata"), "live")
    assert st2["regime"]["now"] == "POSITIVE"


def _bars(rows):
    idx = pd.date_range("2026-11-06 09:15", periods=len(rows), freq="min", tz="Asia/Kolkata")
    return pd.DataFrame(rows, columns=["o", "h", "l", "c", "v"], index=idx)


def test_setup_touch_doji_retest_then_next_candle_decides():
    import stream_setups
    df = _bars([[110, 111, 109, 110, 100],            # above the level
                [105, 105, 99.9, 104, 100],           # touches 100
                [100.01, 100.5, 99.5, 100.02, 100],   # doji at the level
                [103, 105, 103, 105, 100],            # price leaves
                [103, 103, 99.9, 101, 100],           # retest: touches again
                [101, 103, 101, 102, 100]])           # the next candle closes above 100: CALL
    far = pd.Series(1e9, index=df.index)
    out = stream_setups.find_setups(df, {"POC": 100.0}, far, far, [(np.nan, np.nan, np.nan)] * len(df))
    poc = [x for x in out if x["level"].startswith("POC")]
    assert len(poc) == 1 and poc[0]["side"] == "CALL" and poc[0]["idx"] == 5 and poc[0]["tag"] == " (bounce)" and poc[0]["strike"] == 100


def test_vwap_break_signal_needs_the_next_candle_to_confirm():
    import stream_setups
    rows = [[90, 91, 89, 90, 100]] * 9 + [[95, 102, 94, 101, 100], [101.5, 103, 101.2, 102, 100]]
    df = _bars(rows)
    vw = pd.Series(100.0, index=df.index)
    far = pd.Series(1e9, index=df.index)
    out = stream_setups.find_setups(df, {}, vw, far, [(np.nan, np.nan, np.nan)] * len(df))
    br = [x for x in out if x["tag"] == " (break)"]
    assert len(br) == 1 and br[0]["side"] == "CALL" and br[0]["idx"] == 10 and br[0]["level"].startswith("Now VWAP")
    rows[10] = [101, 101.2, 98, 99.5, 100]                  # the next candle falls back below the VWAP: no signal
    out = stream_setups.find_setups(_bars(rows), {}, vw, far, [(np.nan, np.nan, np.nan)] * len(df))
    assert not [x for x in out if x["tag"] == " (break)"]


def test_developing_profile_is_ordered():
    import stream_setups
    df = _frame(["2026-11-06"], minutes=120)
    va = stream_setups.developing_va(df)
    poc, vah, val = va[-1]
    assert val <= poc <= vah and len(va) == 120
