"""Stage 2: mock-FYERS execution tests. No network, no real order. Each test is a scenario named in the implementation prompt."""
import datetime as dt
import json

from execution.engine import IST
from execution.models import BOrder

from .conftest import NOW, SYM, Env, intent, trade


def _entered(env, it=None, **kw):
    it = it or intent(**kw)
    env.step([it])                      # BUY sent (and filled by the mock)
    env.step([it])                      # fill seen: the stop-loss order goes out
    return it


def test_successful_order_uses_the_dashboards_values_and_the_brokers_fill(env):
    it = _entered(env)
    place = [d for a, d in env.b.calls if a == "place"]
    assert place[0] == {"symbol": SYM, "qty": 65, "side": 1, "type": 1, "limit": 102.0, "stop": 0.0, "tag": place[0]["tag"]} and place[0]["tag"].startswith("BP") and place[0]["tag"].endswith("E")
    rec = env.rec(it)
    assert rec["phase"] == "PROTECTED" and rec["qty_filled"] == 65 and rec["avg_entry"] == 102.0            # broker-confirmed fill, not the dashboard's 100
    sl = [d for a, d in env.b.calls if a == "place"][1]
    assert sl["side"] == -1 and sl["type"] == 4 and sl["qty"] == 65 and sl["stop"] == 90.0 and sl["limit"] == 88.65      # the dashboard's absolute stop: not rescaled to the 102 fill
    assert "Stop-loss order placed" in env.text() and "dashboard target 140.00" in env.text()


def test_rejected_entry_leaves_nothing_open(env):
    env.b.reject = {"entry"}
    it = intent()
    env.step([it])
    assert env.rec(it)["phase"] == "REJECTED" and len([1 for a, _ in env.b.calls if a == "place"]) == 1
    env.step([it])
    assert not env.store.active("LIVE") and "REJECTED" in env.text()


def test_partial_fill_protects_only_what_was_filled_and_cancels_the_rest(env, clock):
    env.b.partial_entry = 30
    it = intent()
    env.step([it])
    env.step([it])
    assert env.rec(it)["phase"] == "ENTRY_SENT"                                      # still within the wait: nothing is guessed
    clock.advance(env.cfg["entry_wait_s"] + 1)
    env.step([it])
    rec = env.rec(it)
    assert rec["phase"] == "PROTECTED" and rec["qty_filled"] == 30 and ("cancel", {"id": rec["entry_order_id"]}) in env.b.calls
    sl = env.b.of("sl")[0]
    assert sl.qty == 30 and env.store.executed_entries("2026-11-19", "LIVE") == 1


def test_a_lost_acknowledgement_is_looked_up_never_resent(env):
    env.b.lose_ack = {"entry"}                                                       # the order reaches the broker, the answer is lost
    it = intent()
    env.step([it])
    assert env.rec(it)["phase"] == "ENTRY_UNCERTAIN"
    env.step([it])                                                                   # found by its tag
    env.step([it])
    assert env.rec(it)["phase"] == "PROTECTED"
    assert len([1 for a, d in env.b.calls if a == "place" and d["side"] == 1]) == 1


def test_an_order_that_never_arrived_is_declared_unconfirmed_and_orders_halt(env, clock):
    env.b.drop = {"entry"}
    it = intent()
    env.step([it])
    clock.advance(env.cfg["uncertain_wait_s"] + 1)
    env.step([it])
    assert env.rec(it)["phase"] == "CHECK_MANUALLY" and env.store.halted("2026-11-19", "LIVE")
    assert len([1 for a, d in env.b.calls if a == "place"]) == 1 and "CHECK THE BROKER APP" in env.text()
    other = intent(entry_time="10:00", setup_time="09:58", entry=101.0)
    env.step([it, other])
    assert env.rec(other)["phase"] == "SKIPPED" and "halted" in env.rec(other)["note"]


def test_the_same_signal_never_enters_twice_also_after_a_restart(env):
    it = intent()
    env.step([it])
    env.step([it])
    env.step([it])
    assert len([1 for a, d in env.b.calls if a == "place" and d["side"] == 1]) == 1
    env2 = Env(broker=env.b, store=env.store)                                        # a restart: new engine, same records, same broker
    env2.step([it])
    env2.step([it])
    assert len([1 for a, d in env.b.calls if a == "place" and d["side"] == 1]) == 1
    # the dashboard renumbering its trades does not make a second signal
    renum = intent(n=3)
    env2.step([renum])
    assert len([1 for a, d in env.b.calls if a == "place" and d["side"] == 1]) == 1


def test_an_unfilled_entry_is_cancelled_and_not_chased(env, clock):
    env.b.auto_fill_entry = False
    it = intent()
    env.step([it])
    clock.advance(env.cfg["entry_wait_s"] + 1)
    env.step([it])
    assert env.rec(it)["phase"] == "MISSED" and not env.b.of("sl") and len([1 for a, d in env.b.calls if a == "place"]) == 1


def test_the_stop_loss_order_filling_closes_the_trade_with_the_brokers_price(env):
    it = _entered(env)
    sl = env.b.of("sl")[0]
    env.b.trigger(sl.id, 88.65)
    env.step([it])
    rec = env.rec(it)
    assert rec["phase"] == "CLOSED" and rec["exit_reason"] == "stop-loss order filled" and rec["avg_exit"] == 88.65
    assert rec["realized_pnl"] == round((88.65 - 102.0) * 65, 2)


def test_target_the_stop_is_cancelled_and_confirmed_before_the_sell(env):
    it = _entered(env)
    over = intent(status="TARGET", exit=140.0)
    env.step([over])
    kinds = [a for a, d in env.b.calls][2:]
    assert kinds[0] == "cancel" and kinds[1] == "place"                              # never two sells at once
    ex = env.b.of("exit")[0]
    assert ex.side == -1 and ex.qty == 65 and ex.limit == 137.2
    env.step([over])
    rec = env.rec(it)
    assert rec["phase"] == "CLOSED" and rec["avg_exit"] == 137.2 and rec["realized_pnl"] == round((137.2 - 102.0) * 65, 2) and "target" in rec["exit_reason"]
    assert env.rec(it) and env.e.recon["ok"] is True


def test_trailing_stop_follows_the_dashboard_up_and_never_down(env):
    it = _entered(env)
    env.step([intent(stop_now=105.0)])
    mods = [d for a, d in env.b.calls if a == "modify"]
    assert mods == [{"id": env.rec(it)["sl_order_id"], "limit": 103.4, "stop": 105.0, "qty": 65}]
    env.step([intent(stop_now=95.0)])                                                # a lower stop from the dashboard is never applied
    assert len([1 for a, _ in env.b.calls if a == "modify"]) == 1 and env.rec(it)["sl_stop"] == 105.0


def test_order_updates_from_the_websocket_are_used_and_rest_works_without_them(env):
    env.b.auto_fill_entry = False
    it = intent()
    env.step([it])
    oid = env.rec(it)["entry_order_id"]
    env.e.on_order_event({"s": "ok", "orders": {"id": oid, "status": 2, "qty": 65, "filledQty": 65, "tradedPrice": 101.5, "symbol": SYM, "orderTag": "1:" + env.rec(it)["entry_tag"], "side": 1, "type": 1}})
    env.step([it])
    assert env.rec(it)["phase"] == "PROTECTED" and env.rec(it)["avg_entry"] == 101.5       # the pushed fill was acted on
    assert env.e.ws_state["last"] > 0
    env2 = Env()                                                                     # a feed that never connects changes nothing: the REST path (every other test) carries on
    env2.e.ws_state["connected"] = False
    it2 = _entered(env2)
    assert env2.rec(it2)["phase"] == "PROTECTED"


def test_restart_while_protected_keeps_managing_the_same_stop(env):
    it = _entered(env)
    env2 = Env(broker=env.b, store=env.store)
    env2.step([it])
    assert len(env.b.of("sl")) == 1 and env2.rec(it)["phase"] == "PROTECTED"
    env2.step([intent(status="TARGET", exit=140.0)])
    env2.step([intent(status="TARGET", exit=140.0)])
    assert env2.rec(it)["phase"] == "CLOSED"


def test_a_stop_that_cannot_be_placed_means_an_immediate_exit(env):
    env.b.reject = {"sl"}
    it = intent()
    env.step([it])
    env.step([it])
    assert env.rec(it)["phase"] == "EXIT_SENT" and env.b.of("exit") and "NOT left unprotected" in env.text()
    env.step([it])
    assert env.rec(it)["phase"] == "CLOSED"


def test_if_the_stop_cancel_is_not_confirmed_no_sell_is_sent(env, clock):
    it = _entered(env)
    env.b.cancel_fails = True
    over = intent(status="TARGET", exit=140.0)
    env.step([over])
    clock.advance(env.cfg["uncertain_wait_s"] + 1)
    env.step([over])
    assert not env.b.of("exit") and env.rec(it)["phase"] == "PROTECTED" and "no exit is sent" in env.text()


def test_a_position_closed_by_hand_is_noticed_by_reconciliation(env, clock):
    it = _entered(env)
    clock.advance(30)
    env.b.external_close(SYM, 95.0)
    r = env.e.reconcile(NOW)
    rec = env.rec(it)
    assert rec["phase"] == "CLOSED" and "closed outside the engine" in rec["exit_reason"] and rec["avg_exit"] == 95.0
    assert env.b.of("sl")[0].status == "cancelled" and r["ok"] is True


def test_quantity_mismatch_resizes_the_stop_and_an_unmanaged_position_halts_orders(env, clock):
    it = _entered(env)
    clock.advance(30)
    env.b.pos[SYM]["qty"] = 40                                                       # the broker shows less than expected
    env.e.reconcile(NOW)
    assert env.b.of("sl")[0].qty == 40 and env.rec(it)["qty_filled"] == 40
    env.b.pos["NSE:NIFTY26NOV25400PE"] = {"qty": 65, "cost": 6500.0, "realized": 0.0}
    r = env.e.reconcile(NOW)
    assert r["ok"] is False and env.store.halted("2026-11-19", "LIVE") and "will NOT touch it" in env.text()


def test_unreadable_orderbook_is_not_mistaken_for_no_order(env, clock):
    it = _entered(env)
    env.b.reads_fail = True
    env.step([it])
    assert env.rec(it)["phase"] == "PROTECTED"                                       # nothing is concluded from a failed read
    assert env.e.reconcile(NOW)["ok"] is None


def test_trades_over_before_they_were_seen_and_old_ones_are_not_ordered(env):
    over = intent(status="STOP", exit=90.0)
    old = intent(entry_time="09:50", setup_time="09:49", entry=105.0)
    env.step([over, old])
    assert env.rec(over)["phase"] == "SKIPPED" and "already over" in env.rec(over)["note"]
    assert env.rec(old)["phase"] == "SKIPPED" and "minutes old" in env.rec(old)["note"] and not env.b.calls


def test_stale_dashboard_blocks_new_entries_and_freezes_existing_management(env):
    it = intent()
    env.step([it], dash_fresh=False)
    assert env.rec(it) is None and not env.b.calls                                   # nothing new from a silent dashboard
    env.step([it])
    env.step([it])
    env.step([intent(status="TARGET", exit=140.0)], dash_fresh=False)                # the exit instruction is not acted on while the dashboard is stale
    assert env.rec(it)["phase"] == "PROTECTED" and not env.b.of("exit")


def test_limits_one_position_cap_loss_window_pause_and_lot_validation(env, clock):
    it = _entered(env)
    second = intent(entry_time="10:00", setup_time="09:58", entry=101.0)
    env.step([it, second])
    assert "another position or order is still open" in env.rec(second)["note"]
    # the day's loss limit
    e2 = Env(cfg={"max_daily_loss_inr": 100})
    a = _entered(e2)
    e2.b.trigger(e2.b.of("sl")[0].id, 80.0)
    e2.step([a])
    assert e2.store.halted("2026-11-19", "LIVE")
    # outside the window / paused / unknown contract
    e3 = Env()
    early = NOW.replace(hour=9, minute=10)
    x = intent(entry_time="09:10", setup_time="09:09", entry=102.0)
    e3.step([x], now=early)
    assert "outside the order window" in e3.rec(x)["note"]
    e4 = Env()
    e4.e.paused = lambda now: True
    y = intent(entry=103.0)
    e4.step([y])
    assert "paused" in e4.rec(y)["note"]
    z = intent(symbol="NSE:NIFTY26NOV99999CE", entry=104.0)
    e4.e.paused = lambda now: False
    e4.step([z])
    assert "lot size is unknown" in e4.rec(z)["note"]


def test_emergency_exit_cancels_the_stop_then_sells_and_blocks_new_entries(env):
    it = _entered(env)
    env.e.request_flatten()
    env.step([it])
    assert env.b.of("exit") and env.rec(it)["phase"] == "EXIT_SENT" and "emergency" in env.rec(it)["exit_reason"]
    fresh = intent(entry=111.0, setup_time="10:00")
    env.step([it, fresh])
    assert env.rec(fresh)["phase"] == "SKIPPED" or env.rec(fresh) is None
    env.step([it])
    assert env.rec(it)["phase"] == "CLOSED"


def test_paper_mode_mirrors_the_dashboards_own_paper_trade_exactly(clock):
    from execution.brokers import PaperBroker
    e = Env(mode="PAPER", broker=PaperBroker())
    it = intent()
    e.step([it])
    e.step([it])
    rec = e.rec(it)
    assert rec["phase"] == "PROTECTED" and rec["avg_entry"] == 100.0                 # paper fills at the dashboard's entry price
    done = intent(status="TARGET", exit=140.0)
    e.step([done])
    e.step([done])
    rec = e.rec(it)
    assert rec["phase"] == "CLOSED" and rec["avg_exit"] == 137.2 or rec["avg_exit"] == 140.0
    assert rec["realized_pnl"] == round((rec["avg_exit"] - 100.0) * 65, 2) and rec["avg_exit"] == 140.0
    assert not e.store.signals("2026-11-19", "LIVE")                                 # PAPER and LIVE records are separate


def test_records_keep_the_dashboard_parameters_and_every_order(env):
    it = _entered(env)
    rec = env.rec(it)
    stored = json.loads(rec["intent_json"])
    assert stored["entry"] == 100.0 and stored["stop_now"] == 90.0 and stored["target"] == 140.0 and stored["symbol"] == SYM
    roles = [o["role"] for o in env.store.orders(it.signal_id)]
    assert roles == ["ENTRY", "STOP"] and all(o["request_json"] for o in env.store.orders(it.signal_id))
    assert env.store.events("LIVE")
