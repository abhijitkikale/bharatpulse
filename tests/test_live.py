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


def test_next_slot_and_market_window():
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
    import live_loop
    t = lambda s: pd.Timestamp(s, tz="Asia/Kolkata")
    assert live_loop.next_slot(t("2026-10-30 08:00")).strftime("%H:%M") == "09:20"      # never before the first update of the day
    assert live_loop.next_slot(t("2026-10-30 09:46")).strftime("%H:%M") == "09:50"
    assert live_loop.next_slot(t("2026-10-30 10:15")).strftime("%H:%M") == "10:20"
    assert live_loop.market_day_window(t("2026-10-30 07:00")) and not live_loop.market_day_window(t("2026-10-30 15:45"))
    assert not live_loop.market_day_window(t("2026-10-31 10:00"))                          # Saturday


def _chain(spot, expiry, strikes, gamma_heavy_call=None):
    rows = []
    rng = np.random.default_rng(3)
    for k in strikes:
        for typ in ("CE", "PE"):
            intrinsic = max(spot - k, 0) if typ == "CE" else max(k - spot, 0)
            px = intrinsic + 60 * np.exp(-abs(k - spot) / 250.0) + 1.5
            oi = float(rng.integers(50_000, 200_000)) * (4 if (typ == "CE" and k == gamma_heavy_call) else 1)
            rows.append({"XpryDt": expiry, "StrkPric": float(k), "OptnTp": typ, "ltp": px, "oi": oi, "prev_oi": oi, "volume": 1000.0})
    return {"spot": spot, "rows": rows}


def test_live_gex_levels_text_and_move_rule():
    import live_gex
    now = pd.Timestamp("2026-10-30 10:30", tz="Asia/Kolkata")
    chains = {"NIFTY": _chain(24500.0, "2026-11-03", range(23500, 25550, 50), gamma_heavy_call=24700)}
    g = live_gex.build_gex(chains, now)
    b = g["NIFTY"]
    assert b["expiry"] == "2026-11-03" and b["levels"]["oi_call"] == 24700 and b["levels"]["oi_put"] <= 24500 <= b["levels"]["oi_call"]
    text = live_gex.tv_text("NIFTY", b, now)
    assert text.startswith("# BharatPulse Nifty LIVE 2026-10-30 10:30 IST") and "@time=10:30" in text and "OI call wall=24700" in text
    assert "@pos_above=" in text and "~ LIVE " in text and "GAMMA REGIME" in text
    assert live_gex.moved("NIFTY", b, None) and not live_gex.moved("NIFTY", b, b)
    shifted = {**b, "levels": {**b["levels"], "pain": b["levels"]["pain"] + 50}}
    assert live_gex.moved("NIFTY", shifted, b) and not live_gex.moved("BANKNIFTY", {**b, "levels": {**b["levels"], "pain": b["levels"]["pain"] + 50}}, b)


def test_telegram_only_when_a_level_moved():
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
    import live_loop, live_gex
    now = pd.Timestamp("2026-10-30 10:30", tz="Asia/Kolkata")
    b = {"spot": 24500.0, "expiry": "2026-11-03", "net_cr": 1.0, "pcr": 1.0, "levels": {"flip": 24480.0, "gex_call": 24700.0, "gex_put": 24300.0, "oi_call": 24700.0, "oi_put": 24300.0, "pain": 24500.0}}
    sent = []
    live_loop.SENT.update(date=None, blocks={})
    send = lambda t: (sent.append(t), (True, "sent"))[1]
    live_loop.telegram_moves({"NIFTY": b}, now, log=lambda *a: None, sender=send)
    live_loop.telegram_moves({"NIFTY": {**b, "levels": {**b["levels"], "flip": 24490.0}}}, now, log=lambda *a: None, sender=send)     # 10 points: ignored
    live_loop.telegram_moves({"NIFTY": {**b, "levels": {**b["levels"], "flip": 24550.0}}}, now, log=lambda *a: None, sender=send)     # one strike: sent
    assert len(sent) == 2 and "Gamma flip=24550" in sent[1]
