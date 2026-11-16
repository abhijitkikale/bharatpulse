import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
import fetch_fyers  # noqa: E402
import nodes_agent  # noqa: E402


def test_requests_are_counted_per_process_and_added_up(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_fyers, "USAGE_DIR", tmp_path / "usage")
    monkeypatch.setattr(fetch_fyers, "_USED", {"day": None, "n": 0})
    for _ in range(12):
        fetch_fyers._count()
    assert fetch_fyers.usage_today() >= 12                                                   # this process alone: 12 requests
    other = tmp_path / "usage" / "other-1.json"
    day = fetch_fyers._USED["day"]
    other.write_text(json.dumps({"day": day, "n": 1000}), encoding="utf-8")
    (tmp_path / "usage" / "old-2.json").write_text(json.dumps({"day": "2026-01-01", "n": 4000}), encoding="utf-8")
    assert fetch_fyers.usage_today() >= 1005 and fetch_fyers.usage_today() < 2000           # another process adds up; another day does not count
    assert fetch_fyers.DAILY_LIMIT == 5000


def test_the_nodes_agent_reads_candles_from_the_page_state_and_makes_no_request(tmp_path, monkeypatch):
    state = tmp_path / "stream_state.json"
    shift = 19800
    ist = pd.Timestamp("2026-11-13 15:29", tz="Asia/Kolkata")
    today = pd.Timestamp("2026-11-16 09:15", tz="Asia/Kolkata")
    candles = [{"time": int(ist.timestamp()) + shift, "open": 1, "high": 25250.0, "low": 25240.0, "close": 25244.0},
               {"time": int(today.timestamp()) + shift, "open": 1, "high": 25260.0, "low": 25230.0, "close": 25255.0},
               {"time": int((today + pd.Timedelta(minutes=1)).timestamp()) + shift, "open": 1, "high": 25270.0, "low": 25250.0, "close": 25265.0}]
    state.write_text(json.dumps({"candles": candles}), encoding="utf-8")
    monkeypatch.setattr(nodes_agent, "STATE", state)
    monkeypatch.setattr(nodes_agent.fetch_fyers, "fetch_intraday", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request expected")))
    df, prev = nodes_agent.today_candles()
    assert prev == 25244.0 and len(df) == 2 and df.index[0] == today and df["h"].iloc[1] == 25270.0 and list(df.columns) == ["l", "h", "c"]
    import os
    import time
    old = time.time() - 3600
    os.utime(state, (old, old))
    assert nodes_agent.today_candles() == (None, None)                                       # a stale page state is not used
    assert nodes_agent.CYCLE == 120
