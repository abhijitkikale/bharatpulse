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


def test_tradingview_opening_candles_replace_the_fyers_ones():
    day = pd.Timestamp("2026-11-06").date()
    t0 = pd.Timestamp("2026-11-06 09:15", tz="Asia/Kolkata")
    ms = int(t0.timestamp() * 1000)
    ok = stream_levels.parse_open_bar(json.dumps({"t": ms, "o": 24516.65, "h": 24518.2, "l": 24407.45, "c": 24440.05, "v": 20414083}), day)
    assert ok[0] == t0 and ok[1]["v"] == 20414083
    bad = [{"t": ms, "o": 1, "h": 0.5, "l": 1, "c": 1, "v": 5},                                  # high below the open
           {"t": ms + 3600 * 1000, "o": 24500, "h": 24510, "l": 24490, "c": 24500, "v": 5},      # outside the opening window
           {"t": ms + 1000, "o": 24500, "h": 24510, "l": 24490, "c": 24500, "v": 5}]             # not on a minute
    assert all(stream_levels.parse_open_bar(json.dumps(b), day) is None for b in bad) and stream_levels.parse_open_bar("junk", day) is None
    fy = _frame(["2026-11-06"], minutes=5, start=24500.0)
    fy.index = pd.date_range(t0, periods=5, freq="min")
    merged, used = stream_levels.merge_open(fy, {ok[0]: ok[1]})
    assert used == 1 and merged.loc[t0, "v"] == 20414083 and merged.loc[t0, "h"] == 24518.2 and len(merged) == 5
    far = {t0: {"o": 30000, "h": 30010, "l": 29990, "c": 30000, "v": 1}}
    assert stream_levels.merge_open(fy, far)[1] == 0                                              # a wild price is ignored


def test_vwap_bounce_needs_a_touch_and_a_confirming_candle_and_respects_the_cooldown(monkeypatch):
    import stream_setups
    monkeypatch.setattr(stream_setups, "BOUNCE_ON", True)      # rule D is off by default; this test checks the rule itself
    nan = [(np.nan, np.nan, np.nan)]

    def signals(rows, tag=None):
        df = _bars(rows)
        out = stream_setups.find_setups(df, {}, pd.Series(100.0, index=df.index), pd.Series(1e9, index=df.index), nan * len(df))
        return [(x["idx"], x["side"]) for x in out if x["level"].startswith("Now VWAP") and (tag is None or x["tag"] == tag)]

    base = [[105, 106, 104, 105, 100]] * 6                                      # six candles above the VWAP (100)
    touch = [[105, 105.5, 99.9, 104, 100], [104, 106, 103.5, 105, 100]]         # a wick touches 100 and closes above; the next candle closes above
    quiet = [[105, 106, 104, 105, 100]]
    B = " (VWAP bounce)"
    assert signals(base + touch + quiet * 5 + touch + quiet * 3, B) == [(7, "CALL")]         # the second bounce confirms 7 candles later: inside the cooldown
    assert signals(base + touch + quiet * 7 + touch, B) == [(7, "CALL")]                      # 9 candles later: still blocked
    assert (17, "CALL") in signals(base + touch + quiet * 8 + touch)                         # exactly 10 candles later: a signal is allowed again
    assert signals(base + [[105, 106, 102, 105, 100], [105, 106, 104, 105, 100]], B) == []   # never touches the VWAP: no bounce
    below = [[95, 96, 94, 95, 100]] * 6 + [[95, 100.1, 94.5, 96, 100], [96, 96.5, 94, 95, 100]]
    assert signals(below, B) == [(7, "PUT")]                                                 # the mirror image from below


def test_opening_candles_of_the_last_few_days_are_still_accepted():
    t0 = pd.Timestamp("2026-11-06 09:16", tz="Asia/Kolkata")
    msg = json.dumps({"t": int(t0.timestamp() * 1000), "o": 24444.7, "h": 24452.25, "l": 24388.05, "c": 24452.25, "v": 8497542})
    assert stream_levels.parse_open_bar(msg, pd.Timestamp("2026-11-07").date()) is None                      # strict by default
    assert stream_levels.parse_open_bar(msg, pd.Timestamp("2026-11-07").date(), max_days=4)[0] == t0          # the relay's next-day replay
    assert stream_levels.parse_open_bar(msg, pd.Timestamp("2026-11-12").date(), max_days=4) is None           # too old
    assert stream_levels.parse_open_bar(msg, pd.Timestamp("2026-11-05").date(), max_days=4) is None           # from the future


def test_vwap_bounce_rule_is_off_by_default():
    import stream_setups
    assert stream_setups.BOUNCE_ON is False


def test_setup_message_from_tradingview_is_parsed():
    import stream_levels
    t = int(pd.Timestamp("2026-11-11 10:34", tz="Asia/Kolkata").timestamp() * 1000)
    msg = json.dumps({"k": "sig", "t": t, "s": "PUT", "l": "Now VWAP", "v": 24949.83, "g": " (bounce)", "c": 24941.2})
    sg = stream_levels.parse_signal(msg, pd.Timestamp("2026-11-11").date())
    assert sg["side"] == "PUT" and sg["strike"] == 24950 and sg["level"] == "Now VWAP 24949.83" and sg["tag"] == " (bounce)"
    assert sg["time"] == pd.Timestamp("2026-11-11 10:34", tz="Asia/Kolkata")
    assert stream_levels.parse_signal(json.dumps({"k": "sig", "t": t, "s": "HOLD", "l": "x", "v": 1, "c": 1}), pd.Timestamp("2026-11-11").date()) is None
    assert stream_levels.parse_signal('{"o":1,"h":2}', pd.Timestamp("2026-11-11").date()) is None          # a candle message is not a setup
