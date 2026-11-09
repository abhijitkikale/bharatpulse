import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import stream_trades  # noqa: E402


def _opt(rows, start="2026-11-06 10:00"):
    idx = pd.date_range(start, periods=len(rows), freq="min", tz="Asia/Kolkata")
    return pd.DataFrame(rows, columns=["o", "h", "l", "c"], index=idx)


def _setup(minute, side="CALL", strike=24700, hour=10):
    return {"time": pd.Timestamp(f"2026-11-06 {hour:02d}:{minute:02d}", tz="Asia/Kolkata"), "side": side, "strike": strike, "level": "VAH 24575.00", "tag": " (bounce)"}


def test_first_setup_is_the_trade_target_hit_and_next_setup_after_exit_is_traded():
    # entry 100 at 10:00; 10:03 reaches 140 (+40%) -> TARGET; a setup at 10:02 is ignored, one at 10:05 becomes trade 2
    df = _opt([[100, 100, 100, 100], [100, 110, 99, 105], [105, 120, 104, 118], [118, 141, 117, 139], [139, 139, 130, 131], [130, 131, 128, 129], [129, 133, 120, 125]])
    res = stream_trades.run_trades([_setup(0), _setup(2), _setup(5, "PUT")], lambda s: df)
    t1, t2 = res["trades"]
    assert t1["status"] == "TARGET" and t1["entry"] == 100 and abs(t1["exit"] - 140) < 1e-9 and t1["pnl_pct"] == 40.0 and t1["exit_time"].strftime("%H:%M") == "10:03"
    assert [x["status"] for x in res["signals"]] == ["TRADE 1", "ignored: trade 1 is open", "TRADE 2"]
    assert t2["n"] == 2 and t2["status"] == "OPEN" and t2["entry"] == 129


def test_stop_loss_and_same_candle_counts_as_stop():
    df = _opt([[100, 100, 100, 100], [100, 101, 89.5, 95], [95, 95, 94, 94]])
    res = stream_trades.run_trades([_setup(0)], lambda s: df)
    assert res["trades"][0]["status"] == "STOP" and res["trades"][0]["pnl_pct"] == -10.0
    both = _opt([[100, 100, 100, 100], [100, 150, 85, 120]])                  # touches +40% and -10% in one candle: counted as the stop
    assert stream_trades.run_trades([_setup(0)], lambda s: both)["trades"][0]["status"] == "STOP"


def test_open_trade_shows_live_profit_and_blocks_later_setups():
    df = _opt([[100, 100, 100, 100], [100, 112, 99, 110]])
    res = stream_trades.run_trades([_setup(0), _setup(1, "PUT")], lambda s: df)
    t = res["trades"][0]
    assert t["status"] == "OPEN" and t["exit"] is None and t["pnl_pct"] == 10.0 and len(res["trades"]) == 1
    assert res["signals"][1]["status"].startswith("ignored")
    j = stream_trades.to_json(res, 19800)
    assert j["trades"][0]["entry_time"] == "10:00" and j["total_pct"] == 10.0 and j["markers"][0]["text"] == "T1 CALL"


def test_no_new_trade_after_the_cutoff_and_without_an_option_price():
    df = _opt([[100, 100, 100, 100], [100, 100, 99, 100]], start="2026-11-06 14:50")
    late = _setup(0, hour=14)
    late["time"] = pd.Timestamp("2026-11-06 14:50", tz="Asia/Kolkata")
    res = stream_trades.run_trades([late], lambda s: df)
    assert not res["trades"] and res["signals"][0]["status"] == "no new trade after 14:45"
    assert stream_trades.run_trades([_setup(0)], lambda s: None)["signals"][0]["status"] == "no option price"


def test_daily_limits():
    import pandas as pd
    idx = pd.date_range("2026-11-09 09:30", periods=60, freq="min", tz="Asia/Kolkata")
    # every trade hits its stop on the next candle: entry 100, then low 85
    rows = []
    for i in range(60):
        rows.append([100, 100, 85 if i % 2 else 100, 100, 1])
    df = pd.DataFrame(rows, index=idx, columns=["o", "h", "l", "c", "v"])
    sets = [{"time": idx[k], "side": "CALL", "strike": 25000, "level": "X", "tag": ""} for k in range(0, 40, 2)]
    res = stream_trades.run_trades(sets, lambda s: df)
    assert len(res["trades"]) == 3 and any("loss limit" in x["status"] for x in res["signals"])
