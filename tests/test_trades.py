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


def test_no_trade_in_the_first_ten_minutes_after_the_open():
    df = _opt([[100, 100, 100, 100], [100, 100, 99, 100]], start="2026-11-06 09:19")
    early = _setup(19, hour=9)
    res = stream_trades.run_trades([early], lambda s: df)
    assert not res["trades"] and res["signals"][0]["status"] == "no trade before 9:25"
    df2 = _opt([[100, 100, 100, 100], [100, 100, 99, 100]], start="2026-11-06 09:25")
    res = stream_trades.run_trades([_setup(25, hour=9)], lambda s: df2)
    assert res["signals"][0]["status"] == "TRADE 1"


def _res(rows):
    return stream_trades.run_trades([_setup(0)], lambda s: _opt([[100, 100, 100, 100]] + rows))["trades"][0]


def test_trailing_stop_moves_up_at_20_30_and_35_percent():
    t = _res([[100, 121, 100, 118], [118, 119, 106, 108], [108, 109, 104, 105]])             # +20% reached: stop -> +5%; later candle touches 105
    assert t["status"] == "TRAIL" and t["exit"] == 105 and t["pnl_pct"] == 5.0 and abs(t["stop_now"] - 105) < 1e-9
    t = _res([[100, 131, 100, 128], [128, 129, 114, 120]])                                    # +30% reached: stop -> +15%
    assert t["status"] == "TRAIL" and abs(t["exit"] - 115) < 1e-9 and t["pnl_pct"] == 15.0
    t = _res([[100, 136, 100, 134], [134, 139, 129, 131]])                                    # +35% reached: stop -> +30%
    assert t["status"] == "TRAIL" and abs(t["exit"] - 130) < 1e-9 and t["pnl_pct"] == 30.0


def test_trailing_stop_cautious_rules_and_target_still_works():
    t = _res([[100, 121, 99, 118]])                                                           # same candle touches +20% and the old -10% stop is not touched; stop moves for the NEXT candle
    assert t["status"] == "OPEN" and abs(t["stop_now"] - 105) < 1e-9
    t = _res([[100, 121, 89, 100]])                                                           # the old stop (-10%) was touched in the same candle: counted as the stop first
    assert t["status"] == "STOP" and t["pnl_pct"] == -10.0
    t = _res([[100, 121, 100, 118], [103, 104, 102, 103]])                                    # a gap below the raised stop (105) fills at the open
    assert t["status"] == "TRAIL" and t["exit"] == 103 and t["pnl_pct"] == 3.0
    t = _res([[100, 121, 100, 118], [118, 141, 110, 139]])                                    # still reaches +40% -> target
    assert t["status"] == "TARGET" and t["pnl_pct"] == 40.0


def test_trailing_stop_shifts_are_recorded_for_the_page_and_the_journal():
    res = stream_trades.run_trades([_setup(0)], lambda s: _opt([[100, 100, 100, 100], [100, 121, 100, 118], [118, 131, 117, 128], [128, 129, 126, 127]]))
    sh = res["trades"][0]["shifts"]
    assert [round(x["lock"] * 100) for x in sh] == [5, 15] and sh[0]["time"].strftime("%H:%M") == "10:01" and abs(sh[1]["stop"] - 115) < 1e-9
    j = stream_trades.to_json(res, 19800)
    assert [x["lock"] for x in j["trades"][0]["shifts"]] == [5, 15] and [m["text"] for m in j["markers"] if m["kind"] == "shift"] == ["T1 SL +5%", "T1 SL +15%"]


def _rg(regime, side=-1):
    return lambda ts: {"regime": regime, "side": side}


def test_contra_setups_are_not_traded_in_negative_gamma():
    df = _opt([[100, 100, 100, 100], [100, 100, 99, 100]])
    f = lambda s: df
    r = stream_trades.run_trades([_setup(0, "CALL")], f, regime_at=_rg("NEGATIVE", -1))      # below the flip in negative gamma: a CALL goes against the move
    assert not r["trades"] and r["signals"][0]["status"] == "not taken: against the move in negative gamma" and r["signals"][0]["regime"] == "NEGATIVE"
    assert stream_trades.run_trades([_setup(0, "PUT")], f, regime_at=_rg("NEGATIVE", -1))["signals"][0]["status"] == "TRADE 1"
    assert stream_trades.run_trades([_setup(0, "PUT")], f, regime_at=_rg("NEGATIVE", 1))["signals"][0]["status"].startswith("not taken")      # above the flip: PUT is against
    assert stream_trades.run_trades([_setup(0, "CALL")], f, regime_at=_rg("POSITIVE", -1))["signals"][0]["status"] == "TRADE 1"           # positive gamma: nothing is blocked
    assert stream_trades.run_trades([_setup(0, "CALL")], f)["signals"][0]["status"] == "TRADE 1"                                           # no regime data: nothing is blocked


def test_stop_tightens_to_5_percent_below_the_premium_when_the_regime_turns_against_a_trade():
    rows = [[100, 100, 100, 100], [100, 121, 100, 118], [118, 121, 116, 120], [115, 116, 113, 114]]
    reg = lambda ts: {"regime": "NEGATIVE" if ts.strftime("%H:%M") <= "10:01" else "POSITIVE", "side": -1}
    res = stream_trades.run_trades([_setup(0, "PUT")], lambda s: _opt(rows), regime_at=reg)
    t = res["trades"][0]
    assert [x.get("why") for x in t["shifts"]] == [None, "gamma regime turned POSITIVE"]
    assert abs(t["shifts"][1]["stop"] - 114.0) < 1e-9 and t["status"] == "TRAIL" and abs(t["exit"] - 114.0) < 1e-9 and t["pnl_pct"] == 14.0
    j = stream_trades.to_json(res, 19800)
    assert j["trades"][0]["shifts"][1]["why"] == "gamma regime turned POSITIVE" and "T1 SL +14% (regime)" in [m["text"] for m in j["markers"]]
    # a trade that is not in profit is not tightened, and a flip in the trade's favour does nothing
    losing = [[100, 100, 100, 100], [100, 101, 97, 98], [98, 99, 96, 97]]
    t2 = stream_trades.run_trades([_setup(0, "PUT")], lambda s: _opt(losing), regime_at=lambda ts: {"regime": "NEGATIVE" if ts.strftime("%H:%M") <= "10:00" else "POSITIVE", "side": -1})["trades"][0]
    assert t2["shifts"] == []
    reg0 = lambda ts: {"regime": "NEGATIVE" if ts.strftime("%H:%M") <= "10:01" else "POSITIVE", "side": 0}
    t3 = stream_trades.run_trades([_setup(0, "CALL")], lambda s: _opt(rows), regime_at=reg0)["trades"][0]               # CALL: the flip to POSITIVE is in its favour
    assert all(x.get("why") is None for x in t3["shifts"])


def _pm(rows, minute=5):
    s = _setup(minute, hour=13)
    df = _opt([[100, 100, 100, 100]] + rows, start=f"2026-11-06 13:{minute:02d}")
    return stream_trades.run_trades([s], lambda x: df)["trades"][0]


def test_afternoon_rules_from_13_00_target_20_stop_7_and_trail_10_15():
    t = _pm([[100, 121, 99, 118]])
    assert t["status"] == "TARGET" and t["pnl_pct"] == 20.0 and t["tp_pct"] == 20 and t["sl_pct"] == 7 and t["rules"] == "afternoon"
    t = _pm([[100, 100, 92.5, 95]])
    assert t["status"] == "STOP" and abs(t["exit"] - 93.0) < 1e-9 and t["pnl_pct"] == -7.0
    t = _pm([[100, 111, 100, 108], [108, 109, 104, 105]])                                   # +10% reached: stop -> +5%
    assert t["status"] == "TRAIL" and abs(t["exit"] - 105) < 1e-9 and t["pnl_pct"] == 5.0
    t = _pm([[100, 116, 100, 112], [112, 113, 109, 110]])                                   # +15% reached: stop -> +10%
    assert t["status"] == "TRAIL" and abs(t["exit"] - 110) < 1e-9 and t["pnl_pct"] == 10.0
    morning = stream_trades.run_trades([_setup(0, hour=12)], lambda x: _opt([[100, 100, 100, 100], [100, 121, 99, 118]], start="2026-11-06 12:00"))["trades"][0]
    assert morning["rules"] == "morning" and morning["status"] == "OPEN" and morning["tp_pct"] == 40 and morning["sl_pct"] == 10      # entered before 13:00: morning rules


def test_daily_limit_message_for_the_top_of_the_page():
    idx = pd.date_range("2026-11-09 09:30", periods=80, freq="min", tz="Asia/Kolkata")
    rows = []
    for i in range(80):
        rows.append([100, 100, 85, 90] if i % 2 else [100, 100, 100, 100])                  # every second candle hits the stop
    df = pd.DataFrame(rows, columns=["o", "h", "l", "c"], index=idx)
    sets = [{"time": idx[i], "side": "CALL", "strike": 24700, "level": "VAH 1", "tag": ""} for i in range(0, 20, 4)]
    res = stream_trades.run_trades(sets, lambda s: df)
    assert res["limit"] and ("limit" in res["limit"].lower())
    one = stream_trades.run_trades(sets[:1], lambda s: df)
    assert one["limit"] is None
    assert stream_trades.to_json(res, 19800)["limit"] == res["limit"]


def test_telegram_message_shows_the_afternoon_target_and_stop():
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
    import tg_style
    txt = "TRADE 5 (paper): CALL 25100 CE on VAH 1 (bounce)\nentry 13:10 at 100.00  target 120.00 (+20%)  stop 93.00 (-7%)"
    assert tg_style.classify(txt) == "trade_open"
    kind, html = tg_style.to_html(txt)
    assert "(+20%)" in html and "−7%" in html


def test_node_rules_are_off_by_default_and_can_skip_by_grade_or_flag():
    df = _opt([[100, 100, 100, 100], [100, 100, 99, 100]])
    f = lambda s: df
    gc = lambda s: {"grade": "C", "flags": ["blocked", "midpoint"]}
    ga = lambda s: {"grade": "A", "flags": []}
    r = stream_trades.run_trades([_setup(0)], f, grade_of=gc)                                    # grades are recorded, nothing is blocked without rules
    assert r["signals"][0]["status"] == "TRADE 1" and r["signals"][0]["grade"] == "C"
    assert stream_trades.run_trades([_setup(0)], f, grade_of=gc, rules={})["signals"][0]["status"] == "TRADE 1"
    assert stream_trades.run_trades([_setup(0)], f, grade_of=gc, rules={"min_grade": "B"})["signals"][0]["status"] == "not taken: grade C is below the minimum grade B"
    assert stream_trades.run_trades([_setup(0)], f, grade_of=ga, rules={"min_grade": "B"})["signals"][0]["status"] == "TRADE 1"
    assert "blocked" in stream_trades.run_trades([_setup(0)], f, grade_of=gc, rules={"skip_blocked": True})["signals"][0]["status"]
    assert "middle of the range" in stream_trades.run_trades([_setup(0)], f, grade_of=gc, rules={"skip_midpoint": True})["signals"][0]["status"]
    assert stream_trades.run_trades([_setup(0)], f, grade_of=lambda s: None, rules={"min_grade": "A"})["signals"][0]["status"] == "TRADE 1"      # no grade yet: not blocked
    j = stream_trades.to_json(r, 19800)
    assert j["signals"][0]["grade"] == "C"


def test_would_have_been_result_for_setups_a_rule_kept_out():
    # option: 100 at 10:00, +25% candle at 10:01 -> an untaken setup would have hit the +20% afternoon... use morning rules: +40% target not reached, trail +20% -> +5%
    df = _opt([[100, 100, 100, 100], [100, 125, 100, 120], [120, 121, 104, 105]])
    f = lambda s: df
    r = stream_trades.run_trades([_setup(0, "CALL")], f, regime_at=_rg("NEGATIVE", -1))             # a CALL below the flip in negative gamma: not taken, tested anyway
    sg = r["signals"][0]
    assert sg["status"].startswith("not taken") and sg["would"]["status"] == "TRAIL" and sg["would"]["pnl_pct"] == 5.0 and not r["trades"]
    gc = lambda s: {"grade": "C", "flags": []}
    r2 = stream_trades.run_trades([_setup(0, "CALL")], f, grade_of=gc, rules={"min_grade": "B"})
    assert r2["signals"][0]["would"]["pnl_pct"] == 5.0
    r3 = stream_trades.run_trades([_setup(0, "CALL"), _setup(1, "PUT")], f)                          # the second is only ignored while trade 1 is open: no test result for it
    assert r3["signals"][1]["status"].startswith("ignored") and r3["signals"][1]["would"] is None and r3["signals"][0]["would"] is None
    j = stream_trades.to_json(r, 19800)
    assert j["signals"][0]["would"] == {"pnl_pct": 5.0, "status": "TRAIL", "exit_time": "10:02"}
    early = stream_trades.run_trades([_setup(5, hour=9)], lambda s: _opt([[100, 100, 100, 100], [100, 100, 89, 90]], start="2026-11-06 09:05"))
    assert early["signals"][0]["status"].startswith("no trade before") and early["signals"][0]["would"]["pnl_pct"] == -10.0
