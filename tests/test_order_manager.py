import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import order_manager as om  # noqa: E402

IST = "Asia/Kolkata"


def ts(hm, sec=0):
    return pd.Timestamp(f"2026-11-16 {hm}:{sec:02d}", tz=IST)


class FakeBroker:
    """Records every call; entry orders fill when told to; stop orders stay open until told they filled."""
    def __init__(self, reject_place=None, reject_modify=False):
        self.calls, self.orders, self.n = [], {}, 0
        self.reject_place, self.reject_modify = reject_place or [], reject_modify

    def place(self, symbol, qty, side, otype, limit=0.0, stop=0.0, tag=""):
        self.calls.append(("place", symbol, qty, side, otype, limit, stop))
        if otype in self.reject_place:
            return {"ok": False, "id": None, "msg": "rejected by test"}
        self.n += 1
        oid = f"F{self.n}"
        self.orders[oid] = {"state": "open", "avg": limit, "qty": qty, "type": otype, "side": side, "limit": limit, "stop": stop}
        return {"ok": True, "id": oid, "msg": ""}

    def modify(self, oid, limit=None, stop=None, qty=None):
        self.calls.append(("modify", oid, limit, stop, qty))
        if self.reject_modify:
            return {"ok": False, "id": oid, "msg": "modify rejected"}
        self.orders[oid].update(limit=limit, stop=stop)
        return {"ok": True, "id": oid, "msg": ""}

    def cancel(self, oid):
        self.calls.append(("cancel", oid))
        self.orders[oid]["state"] = "cancelled"
        return {"ok": True, "id": oid, "msg": ""}

    def status(self, oid):
        o = self.orders[oid]
        r = {"state": o["state"], "avg": o["avg"], "qty": o["qty"]}
        if "filled" in o:
            r["filled"] = o["filled"]
        return r

    def fill(self, oid, avg=None):
        self.orders[oid]["state"] = "filled"
        if avg is not None:
            self.orders[oid]["avg"] = avg


def trade(n=1, status="OPEN", entry=140.0, stop_now=126.0, symbol="NSE:NIFTY2611725150PE", entry_time="11:40", last=141.0, exit=None):
    return {"n": n, "side": "PUT", "strike": 25150, "symbol": symbol, "entry": entry, "stop_now": stop_now, "stop": 126.0, "status": status, "last": last, "exit": exit, "entry_time": entry_time}


def cfg(**k):
    c = dict(om.DEFAULTS)
    c.update(k)
    return c


def test_default_config_is_off_and_lots_are_capped(tmp_path):
    assert om.load_config(tmp_path / "none.json")["mode"] == "off"
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"mode": "live", "lots": 9, "max_lots": 50}), encoding="utf-8")
    assert om.load_config(p)["lots"] == 1                                                      # one lot, whatever the file says
    om.save_mode("off", p)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["mode"] == "off" and d["lots"] == 9                                                # the kill switch changes only the mode


def test_full_trade_entry_stop_moves_and_the_broker_stop_fills():
    b = FakeBroker()
    m = om.OrderManager(b, cfg(), label="LIVE")
    out = m.step([trade()], ts("11:41", 5))
    assert b.calls[0] == ("place", "NSE:NIFTY2611725150PE", 65, 1, 1, 142.8, 0.0) and "BUY 65" in out[0]                       # limit 2% above the reference, a lot of 65
    assert m.step([trade()], ts("11:41", 20)) == [] and len(b.calls) == 1                                                     # not filled yet: wait, send nothing twice
    b.fill("F1", avg=141.5)
    out = m.step([trade()], ts("11:41", 30))
    assert b.calls[1] == ("place", "NSE:NIFTY2611725150PE", 65, -1, 4, 124.1, 126.0) and "Stop-loss order placed" in out[0]    # stop-limit: trigger 126.00, limit 1.5% below
    out = m.step([trade(stop_now=147.58)], ts("12:11"))
    assert b.calls[2][:2] == ("modify", "F2") and b.calls[2][3] == 147.55 and "moved to trigger 147.55" in out[0]
    assert m.step([trade(stop_now=147.58)], ts("12:12")) == []                                                                # the same stop is not sent again
    b.fill("F2", avg=147.0)
    out = m.step([trade(status="TRAIL", stop_now=147.58, exit=147.58)], ts("12:20"))
    assert "broker stop-loss order FILLED at 147.00" in out[0] and "Rs +358" in out[0]                                       # (147.00 - 141.50) x 65
    assert m.state["trades"]["1"]["phase"] == "done" and m.state["pnl_inr"] == 357.5


def test_engine_says_over_but_broker_stop_has_not_filled_cancels_it_and_sells():
    b = FakeBroker()
    m = om.OrderManager(b, cfg(), label="LIVE")
    m.step([trade()], ts("11:41", 5))
    b.fill("F1", avg=140.5)
    m.step([trade()], ts("11:41", 30))
    out = m.step([trade(status="TARGET", exit=196.77)], ts("12:30"))
    assert ("cancel", "F2") in b.calls and b.calls[-1][:5] == ("place", "NSE:NIFTY2611725150PE", 65, -1, 1) and b.calls[-1][5] == 192.8 and "SELL 65" in out[0]
    b.fill("F3", avg=192.9)
    out = m.step([trade(status="TARGET", exit=196.77)], ts("12:31"))
    assert "exit filled at 192.90" in out[0] and m.state["trades"]["1"]["phase"] == "done"


def test_entry_not_filled_is_cancelled_and_not_chased_and_rejections_are_reported():
    b = FakeBroker()
    m = om.OrderManager(b, cfg(), label="LIVE")
    m.step([trade()], ts("11:41", 5))
    out = m.step([trade()], ts("11:42", 10))
    assert ("cancel", "F1") in b.calls and "not filled within 45 s" in out[0] and m.state["trades"]["1"]["phase"] == "missed"
    assert not [c for c in b.calls if c[0] == "place" and c[3] == -1]                         # nothing was sold: nothing was bought
    b2 = FakeBroker(reject_place=[1])
    m2 = om.OrderManager(b2, cfg(), label="LIVE")
    assert "REJECTED" in m2.step([trade()], ts("11:41", 5))[0] and m2.state["trades"]["1"]["phase"] == "rejected"


def test_a_failed_stop_order_exits_at_once_and_a_failed_exit_says_so():
    b = FakeBroker(reject_place=[4])
    m = om.OrderManager(b, cfg(), label="LIVE")
    m.step([trade()], ts("11:41", 5))
    b.fill("F1", avg=140.5)
    out = m.step([trade()], ts("11:41", 30))
    assert "stop-loss order was REJECTED" in out[0] and b.calls[-1][3] == -1 and b.calls[-1][4] == 1 and m.state["trades"]["1"]["phase"] == "exit_sent"
    b3 = FakeBroker(reject_place=[4, 1])
    m3 = om.OrderManager(b3, cfg(), label="LIVE")
    m3.orders = None
    m3.state["trades"]["1"] = {"phase": "entry_sent", "symbol": "S", "qty": 65, "entry_id": "X", "sent_at": ts("11:41").isoformat(), "stop": None}
    b3.orders["X"] = {"state": "filled", "avg": 140.0, "qty": 65}
    out = m3.step([trade()], ts("11:41", 30))
    assert "EXIT ORDER FAILED" in out[-1] and "YOURSELF" in out[-1] and m3.state["trades"]["1"]["phase"] == "stuck"
    bm = FakeBroker(reject_modify=True)
    mm = om.OrderManager(bm, cfg(), label="LIVE")
    mm.step([trade()], ts("11:41", 5))
    bm.fill("F1", avg=140.5)
    mm.step([trade()], ts("11:41", 30))
    assert "MOVING THE STOP FAILED" in mm.step([trade(stop_now=150.0)], ts("12:00"))[0]


def test_one_position_at_a_time_late_signals_pause_and_the_daily_loss_halt():
    b = FakeBroker()
    m = om.OrderManager(b, cfg(), label="LIVE")
    m.step([trade(1)], ts("11:41", 5))
    out = m.step([trade(1), trade(2, entry_time="11:41")], ts("11:41", 30))
    assert "another position is still open" in out[0] and len([c for c in b.calls if c[0] == "place" and c[3] == 1]) == 1
    late = om.OrderManager(FakeBroker(), cfg(), label="LIVE")
    assert "6 minutes old" in late.step([trade(1)], ts("11:45", 30))[0]
    done = om.OrderManager(FakeBroker(), cfg(), label="LIVE")
    assert "already over" in str(done.step([trade(1, status="STOP")], ts("11:45")) or done.state["trades"]["1"]["why"])
    pz = om.OrderManager(FakeBroker(), cfg(), label="LIVE")
    assert "paused" in pz.step([trade(1)], ts("11:41", 5), paused=True)[0] and not pz.b.calls
    nosym = om.OrderManager(FakeBroker(), cfg(), label="LIVE")
    assert "symbol is unknown" in nosym.step([trade(1, symbol=None)], ts("11:41", 5))[0]
    # a losing trade past the limit halts everything
    b2 = FakeBroker()
    m2 = om.OrderManager(b2, cfg(max_daily_loss_inr=500), label="LIVE")
    m2.step([trade(1)], ts("11:41", 5))
    b2.fill("F1", avg=140.0)
    m2.step([trade(1)], ts("11:41", 30))
    b2.fill("F2", avg=126.0)
    out = m2.step([trade(1, status="STOP", exit=126.0)], ts("11:50"))
    assert "ORDERS HALTED" in out[-1] and m2.state["halted"]
    assert "orders halted" in m2.step([trade(1, status="STOP"), trade(2, entry_time="12:01")], ts("12:01", 5))[0]


def test_square_off_time_exits_an_open_position_and_the_dry_broker_never_leaves_the_pc():
    b = FakeBroker()
    m = om.OrderManager(b, cfg(), label="LIVE")
    m.step([trade()], ts("11:41", 5))
    b.fill("F1", avg=140.0)
    m.step([trade()], ts("11:41", 30))
    out = m.step([trade(last=150.0)], ts("15:16"))
    assert "square-off time" in out[0] and ("cancel", "F2") in b.calls
    d = om.DryBroker()
    md = om.OrderManager(d, cfg(), label="DRY RUN")
    out = md.step([trade()], ts("11:41", 5))
    assert "[DRY RUN]" in out[0] and d.orders["DRY-1"]["state"] == "filled"
    out = md.step([trade()], ts("11:41", 30))
    assert "Stop-loss order placed" in out[0] and d.orders["DRY-2"]["state"] == "open"
    out = md.step([trade(status="STOP", exit=126.0)], ts("11:50"))
    assert "SELL 65" in out[0] and md.step([trade(status="STOP", exit=126.0)], ts("11:50", 30))[0].endswith("before charges).")


def test_ticks_and_state_files(tmp_path, monkeypatch):
    assert om.tick_up(142.801) == 142.85 and om.tick_down(124.119) == 124.1 and om.tick_up(100.0) == 100.0
    monkeypatch.setattr(om, "STATE_DIR", tmp_path)
    assert om.load_state("2026-11-16") is None
    om.save_state("2026-11-16", {"trades": {}, "orders": 1})
    assert om.load_state("2026-11-16")["orders"] == 1


def test_partial_fill_and_an_unreadable_status_never_leave_a_position_without_a_stop():
    b = FakeBroker()
    m = om.OrderManager(b, cfg(), label="LIVE")
    m.step([trade()], ts("11:41", 5))
    b.orders["F1"]["filled"] = 30                                                              # 30 of 65 filled, the rest still working
    out = m.step([trade()], ts("11:42", 10))                                                   # the wait is over: cancel, then protect what is held
    assert ("cancel", "F1") in b.calls and b.calls[-1] == ("place", "NSE:NIFTY2611725150PE", 30, -1, 4, 124.1, 126.0) and "BUY filled (30)" in out[0]
    assert m.state["trades"]["1"]["qty"] == 30 and m.state["trades"]["1"]["phase"] == "protected"

    class Blind(FakeBroker):
        def status(self, oid):
            return {"state": "unknown"}
    bb = Blind()
    mb = om.OrderManager(bb, cfg(), label="LIVE")
    mb.step([trade()], ts("11:41", 5))
    out = mb.step([trade()], ts("11:42", 10))
    assert "CHECK YOUR BROKER NOW" in out[0] and mb.state["trades"]["1"]["phase"] == "check_manually"


def test_the_daily_limit_counts_executed_entries_not_requests():
    b = FakeBroker()
    m = om.OrderManager(b, cfg(max_entries_per_day=2), label="LIVE")
    for n, (hm_entry, sec) in enumerate([("11:41", 5), ("12:41", 5)], start=1):
        m.step([trade(n, entry_time=hm_entry)], ts(hm_entry, sec))
        b.fill(f"F{b.n}", avg=140.0)                                                            # the BUY fills
        m.step([trade(n, entry_time=hm_entry)], ts(hm_entry, 30))
        b.fill(f"F{b.n}", avg=127.0)                                                            # the stop fills
        m.step([trade(n, status="STOP", entry_time=hm_entry, exit=126.0)], ts(hm_entry, 50))
    assert m.state["entries"] == 2
    out = m.step([trade(3, entry_time="13:41")], ts("13:41", 5))
    assert "2 orders were already executed today" in out[0] and not [c for c in b.calls if c[0] == "place" and c[3] == 1 and c[1] and len(b.calls) > 8]
    m2 = om.OrderManager(FakeBroker(), cfg(max_api_orders=3), label="LIVE")
    m2.state["orders"] = 3
    assert "runaway guard" in m2.step([trade(1)], ts("11:41", 5))[0]
    assert om.DEFAULTS["max_entries_per_day"] == 5 and om.DEFAULTS["max_lots"] == 1
