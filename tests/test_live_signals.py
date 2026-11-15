import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
import stream_alerts  # noqa: E402
import stream_trades  # noqa: E402
import tg_control  # noqa: E402
import tg_style  # noqa: E402
import trading_pause  # noqa: E402

IST = "Asia/Kolkata"


def _ts(hm):
    return pd.Timestamp(f"2026-11-16 {hm}", tz=IST)


def _res(exited=True, shifts=2):
    tr = {"n": 1, "side": "PUT", "strike": 25150, "level": "Now VWAP 25172.65 (bounce)", "entry_time": _ts("11:40"), "entry": 140.55, "target": 196.77, "stop": 126.50,
          "tp_pct": 40, "sl_pct": 10, "status": "TRAIL" if exited else "OPEN", "exit_time": _ts("12:20") if exited else None, "exit": 177.56 if exited else None, "pnl_pct": 26.3,
          "shifts": [{"time": _ts("12:10"), "reach": 0.2, "lock": 0.05, "stop": 147.58}, {"time": _ts("12:13"), "reach": None, "lock": 0.18, "stop": 165.9, "why": "gamma regime turned POSITIVE"}][:shifts]}
    return {"trades": [tr], "signals": []}


def test_entry_message_carries_an_order_ticket_that_stays_out_of_the_channel_version():
    contracts = {(25150.0, "PE"): {"symbol": "NSE:NIFTY2611725150PE", "expiry": "2026-11-17"}}
    sent = set()
    msgs = stream_alerts.new_messages(_res(exited=False), sent, _ts("11:41"), contracts=contracts)
    op = next(m for m in msgs if m.startswith("TRADE 1 (paper)"))
    assert "ticket: BUY 1 lot (65) NSE:NIFTY2611725150PE" in op and "stop-loss order 126.50" in op and "reference price 140.55" in op and "Rs 9,136" in op
    assert tg_style.classify(op) == "trade_open"
    kind, html = tg_style.to_html(op)
    assert kind == "trade_open" and "ticket" not in html.lower() and "NIFTY2611725150PE" not in html            # friends get the trade, not your order ticket
    nocontract = stream_alerts.new_messages(_res(exited=False), set(), _ts("11:41"))
    assert "NIFTY 25150 PE (nearest weekly)" in nocontract[0]


def test_stop_moves_and_exit_messages_go_to_your_chat_only():
    sent = set()
    m1 = stream_alerts.new_messages(_res(exited=False, shifts=1), sent, _ts("12:11"))                                 # a minute after the first stop move
    mv1 = [m for m in m1 if m.startswith("STOP MOVED")]
    assert len(mv1) == 1 and "change your stop-loss order to 147.58 (+5% of the entry price) because the premium reached +20%" in mv1[0]
    m2 = stream_alerts.new_messages(_res(exited=False, shifts=2), sent, _ts("12:14"))
    mv2 = [m for m in m2 if m.startswith("STOP MOVED")]
    assert len(mv2) == 1 and "165.90 (+18% of the entry price) because gamma regime turned POSITIVE" in mv2[0]
    assert all(tg_style.classify(m) == "other" and not tg_style.is_public(tg_style.classify(m)) for m in mv1 + mv2)
    m3 = stream_alerts.new_messages(_res(), sent, _ts("12:21"))
    cl = next(m for m in m3 if m.startswith("TRADE 1 closed"))
    assert "EXIT: if you are still in this position, exit it now" in cl and tg_style.classify(cl) == "trade_closed"
    assert stream_alerts.new_messages(_res(), sent, _ts("12:21")) == []                       # nothing is sent twice


def test_pause_and_resume_files_and_the_replay_rule(tmp_path):
    f = tmp_path / "pause.json"
    now = _ts("10:05")
    assert trading_pause.pause(now, f) is True and trading_pause.pause(now, f) is False
    p = trading_pause.paused_fn(f)
    assert p(_ts("10:30")) and not p(_ts("09:50")) and not p(pd.Timestamp("2026-11-17 10:30", tz=IST))        # only from the pause on, and only that day
    assert trading_pause.resume(_ts("10:40"), f) is True and trading_pause.resume(_ts("10:41"), f) is False
    p = trading_pause.paused_fn(f)
    assert p(_ts("10:30")) and not p(_ts("10:45"))
    assert trading_pause.is_paused_now(_ts("10:30"), f) and not trading_pause.is_paused_now(_ts("11:00"), f)

    idx = pd.date_range("2026-11-16 10:00", periods=3, freq="min", tz=IST)
    df = pd.DataFrame([[100, 100, 100, 100], [100, 125, 100, 120], [120, 121, 104, 105]], columns=["o", "h", "l", "c"], index=idx)
    s = {"time": _ts("10:30").replace(hour=10, minute=0), "side": "CALL", "strike": 24700, "level": "VAH 1", "tag": ""}
    res = stream_trades.run_trades([s], lambda x: df, paused_at=lambda t: True)
    assert not res["trades"] and res["signals"][0]["status"] == "not taken: paused (you sent /pause)" and res["signals"][0]["would"]["pnl_pct"] == 5.0
    assert stream_trades.run_trades([s], lambda x: df, paused_at=lambda t: False)["signals"][0]["status"] == "TRADE 1"


def test_pause_resume_commands_from_telegram(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_pause, "FILE", tmp_path / "pause.json")
    said = []
    c = tg_control.Control(lambda: "ok", 111, said.append)
    c.handle("pause")
    c.handle("pause")
    c.handle("resume")
    c.handle("resume")
    assert "PAUSED" in said[0] and "Already paused" in said[1] and "ON again" in said[2] and "Nothing was paused" in said[3]
    assert "pause" in [x for x, _ in tg_control.COMMANDS] and "resume" in [x for x, _ in tg_control.COMMANDS]
