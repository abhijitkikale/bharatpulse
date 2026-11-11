import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd
import stream_alerts


def ts(hm):
    return pd.Timestamp(f"2026-11-09 {hm}", tz="Asia/Kolkata")


def _res():
    tr = {"n": 1, "side": "PUT", "strike": 24950, "level": "Now VWAP 24964.24 (cross)", "entry_time": ts("09:27"), "entry": 100.0, "target": 140.0, "stop": 90.0,
          "status": "STOP", "exit_time": ts("09:28"), "exit": 90.0, "pnl_pct": -10.0}
    sig = [{"time": ts("09:27"), "side": "PUT", "strike": 24950, "level": "Now VWAP 24964.24 (cross)", "status": "TRADE 1"},
           {"time": ts("09:27"), "side": "PUT", "strike": 24950, "level": "Now Weekly VWAP 24964.24 (cross)", "status": "ignored: trade 1 is open"}]
    return {"trades": [tr], "signals": sig}


def test_fresh_events_are_sent_once():
    sent = set()
    m = stream_alerts.new_messages(_res(), sent, ts("09:28"))
    assert len(m) == 3 and m[0].startswith("TRADE 1 (paper)") and "closed: STOP -10.0%" in "".join(m)
    assert stream_alerts.new_messages(_res(), sent, ts("09:29")) == []


def test_old_events_are_marked_but_not_sent():
    sent = set()
    assert stream_alerts.new_messages(_res(), sent, ts("12:00")) == []
    assert len(sent) == 3


def test_drifting_vwap_value_does_not_repeat_a_setup_message():
    sent = set()
    one = {"time": ts("10:09"), "side": "PUT", "strike": 25350, "level": "Now VWAP 25351.83 (break)", "status": "ignored: trade 2 is open"}
    assert len(stream_alerts.new_messages({"trades": [], "signals": [one]}, sent, ts("10:10"))) == 1
    drift = dict(one, level="Now VWAP 25351.85 (break)")
    assert stream_alerts.new_messages({"trades": [], "signals": [drift]}, sent, ts("10:11")) == []


def test_journal_page_builds_with_rupee_settings(tmp_path):
    import json, journal_dashboard
    day = {"date": "2026-11-11", "symbol": "NIFTY", "total_pct": 20.0, "trades": [{"n": 1, "side": "PUT", "strike": 25300, "expiry": "2026-11-17", "setup": "x", "entry_time": "09:25",
           "entry": 200.0, "target": 280.0, "stop": 180.0, "exit_time": "09:34", "exit": 180.0, "result": "STOP", "pnl_pct": -10.0, "setups_ignored_while_open": [], "option_candles_hm_ohlcv": [["09:25", 1, 2, 3, 200.0, 5]]}],
           "setups": [], "index_candles_hm_ohlcv": [["09:15", 1, 2, 3, 4, 5]], "levels_used": {"close": "2026-11-09", "levels": {"POC": 25000}}, "opening_candles": "Fyers"}
    (tmp_path / "2026-11-11.json").write_text(json.dumps(day), encoding="utf-8")
    html = journal_dashboard.build_html(tmp_path)
    assert '"capital":25000' in html and '"lot":65' in html and "2026-11-11" in html and html.count("</script>") == 1


def test_telegram_styles_and_routing():
    import tg_style
    open_ = "TRADE 3 (paper): PUT 25350 PE on Now VWAP 25360.45 (bounce)\nentry 12:19 at 229.95  target 321.93 (+40%)  stop 206.95 (-10%)"
    assert tg_style.classify(open_) == "trade_open" and tg_style.is_public("trade_open")
    k, h = tg_style.to_html(open_)
    assert h.startswith("\U0001F7E0") and "Stop-loss" in h and "Not investment advice" in h
    t = "TRADE 3 closed: TARGET +40.0%  (PUT 25350 PE, out 13:48 at 321.93)"
    assert tg_style.to_html(t)[1].startswith("\U0001F7E2")
    s = "TRADE 1 closed: STOP -10.0%  (PUT 25300 PE, out 09:34 at 190.66)"
    assert tg_style.to_html(s)[1].startswith("\U0001F534")
    skip = "Setup not taken (ignored: trade 2 is open): PUT 25350 PE on Now VWAP 1 (break) at 10:09"
    assert tg_style.classify(skip) == "setup_skip" and not tg_style.is_public("setup_skip")
    assert tg_style.classify("BharatPulse stream: READY") == "other" and tg_style.to_html("BharatPulse stream: READY")[1] is None


def test_telegram_commands_only_from_the_owner():
    import tg_control
    owner = 111

    def upd(chat_id, from_id, text, chat_type="private", date=1000):
        return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": from_id}, "text": text, "date": date}}
    assert tg_control.parse(upd(111, 111, "/status"), owner, now=1010) == "status"
    assert tg_control.parse(upd(111, 111, "/Shutdown@GEXINDICATORBOT now"), owner, now=1010) == "shutdown"
    assert tg_control.parse(upd(222, 222, "/shutdown"), owner, now=1010) is None                  # a friend writing to the bot
    assert tg_control.parse(upd(-100, 111, "/shutdown", chat_type="channel"), owner, now=1010) is None
    assert tg_control.parse(upd(111, 222, "/shutdown"), owner, now=1010) is None
    assert tg_control.parse(upd(111, 111, "/shutdown", date=100), owner, now=1010) is None         # an old message is never run later
    assert tg_control.parse({"update_id": 2, "channel_post": {"text": "/shutdown"}}, owner, now=1010) is None
    said = []
    c = tg_control.Control(lambda: "ok", owner, said.append)
    c.handle("shutdown", now=0)
    assert "/confirm" in said[-1] and c.pending[0] == "shutdown"
    c.handle("cancel", now=1)
    assert c.pending is None
    c.handle("confirm", now=2)
    assert said[-1] == "Nothing to confirm."
