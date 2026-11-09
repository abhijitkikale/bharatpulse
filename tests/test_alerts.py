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
