"""The FYERS Automate executor: webhooks instead of API orders, the broker's positions as the truth. No network, no real webhook: a fake 'Automate' answers the webhooks and
changes the mock broker's positions the way the real workflows would."""
import json

import pytest

from execution import automate, config
from execution.automate import AutomateEngine, MODE
from execution.mock_broker import MockBroker
from execution.models import Res
from execution.store import Store

from .conftest import DAY, NOW, SYM, intent

PE = "NSE:NIFTY26NOV25300PE"
ENTRY = "https://api-t2.fyers.in/webhook/execute?id=entry-aaa"
EXIT = "https://api-t2.fyers.in/webhook/execute?id=exit-bbb"
SLOTS = {"slots": [{"name": "CE1", "side": "CALL", "entry_url": ENTRY, "exit_url": EXIT},
                   {"name": "CE2", "side": "CALL", "entry_url": ENTRY + "2", "exit_url": EXIT + "2"},
                   {"name": "PE1", "side": "PUT", "entry_url": ENTRY + "3", "exit_url": EXIT + "3"}], "test": {}}


class FakeAutomate:
    """What the workflows do: the entry webhook buys an option (the strike Automate chooses), the exit webhook sells it."""
    def __init__(self, broker, symbol=SYM, price=101.0, exit_price=95.0):
        self.b, self.symbol, self.price, self.exit_price = broker, symbol, price, exit_price
        self.calls, self.answer, self.fills = [], None, True

    def __call__(self, url, payload=None, **kw):
        self.calls.append((url, payload))
        if self.answer is not None:
            res = self.answer
            if not res.ok and not res.uncertain:
                return res
            if res.uncertain and not self.fills:
                return res
        if url.startswith(ENTRY[:-3]) and "entry" in url:
            if self.fills:
                self.b.place(self.symbol, 65, 1, 2, limit=self.price, tag="auto")
        elif "exit" in url:
            held = sum(p.net_qty for p in self.b.positions() if p.symbol == self.symbol)
            if held > 0 and self.fills:
                self.b.place(self.symbol, held, -1, 2, limit=self.exit_price, tag="auto")
        return self.answer or Res(True, None, 200, "ok")


class AEnv:
    def __init__(self, cfg=None, slots=SLOTS, **fake):
        self.msgs = []
        import pathlib
        self.cfg = config.load(pathlib.Path("/nonexistent"))
        self.cfg.update(cfg or {})
        self.b = MockBroker()
        self.store = Store(":memory:")
        self.fake = FakeAutomate(self.b, **fake)
        self.slots = slots
        self.e = self.make()

    def make(self):
        return AutomateEngine(self.b, self.store, self.cfg, notify=lambda t, level="info": self.msgs.append((level, t)), sleep=lambda s: None, slots_cfg=self.slots, fire_fn=self.fake)

    def step(self, its, now=NOW, **kw):
        self.e.step(its, now, **kw)

    def rec(self, it):
        return self.store.get(it.signal_id, MODE)

    def text(self):
        return "\n".join(t for _, t in self.msgs)


@pytest.fixture
def a(clock):
    return AEnv()


def entered(a, it=None, clock=None):
    """Drive one trade to an open position."""
    it = it or intent()
    a.step([it])
    a.step([it])
    return it


def test_entry_webhook_then_the_position_opens_and_the_actual_fill_is_recorded(a, clock):
    it = entered(a)
    r = a.rec(it)
    assert len(a.fake.calls) == 1 and "entry" in a.fake.calls[0][0]
    assert r["phase"] == "PROTECTED" and r["symbol"] == SYM and r["qty_filled"] == 65 and r["avg_entry"] == 101.0
    assert json.loads(r["extra_json"])["slot"] == "CE1"
    assert "Automate emergency stop" in a.text()


def test_the_dashboard_decides_the_exit_target_stop_and_trail_each_send_the_exit_webhook(a, clock):
    for status, word in (("TARGET", "target"), ("STOP", "ended (STOP)"), ("TRAIL", "ended (TRAIL)")):
        a = AEnv()
        it = entered(a)
        a.step([intent(status=status, exit=140.0)])
        a.step([intent(status=status, exit=140.0)])
        r = a.rec(it)
        assert r["phase"] == "CLOSED" and word in r["exit_reason"], (status, r["phase"], r["exit_reason"])
        assert [c for c in a.fake.calls if "exit" in c[0]] and len([c for c in a.fake.calls if "exit" in c[0]]) == 1
        assert r["realized_pnl"] == pytest.approx((95.0 - 101.0) * 65)           # the broker's realised profit, not the dashboard's number


def test_no_exit_while_the_dashboard_says_open_and_trailing_stays_in_the_dashboard(a, clock):
    it = entered(a)
    for stop in (90.0, 106.0, 120.0):                                           # the dashboard trails its stop: nothing is sent to Automate for that
        a.step([intent(stop_now=stop)])
    assert len(a.fake.calls) == 1 and a.rec(it)["phase"] == "PROTECTED"


def test_square_off_time_exits(a, clock):
    import datetime as dt
    from execution.engine import IST
    it = entered(a)
    a.step([intent()], now=dt.datetime(2026, 11, 19, 15, 16, tzinfo=IST))
    a.step([intent()], now=dt.datetime(2026, 11, 19, 15, 16, 5, tzinfo=IST))
    assert a.rec(it)["phase"] == "CLOSED" and "square-off" in a.rec(it)["exit_reason"]


def test_automate_picks_the_strike_the_engine_records_it_and_does_not_call_it_an_orphan(clock):
    a = AEnv(symbol="NSE:NIFTY26NOV25350CE")
    it = entered(a)
    r = a.rec(it)
    assert r["symbol"] == "NSE:NIFTY26NOV25350CE" and r["phase"] == "PROTECTED" and "chose the strike" in a.text()
    a.e.reconcile(NOW)
    assert not a.store.halted(DAY, MODE) and a.e.recon["ok"]


def test_a_signal_is_entered_once_also_after_a_restart(a, clock):
    it = intent()
    a.step([it])
    a.step([it])
    a.e = a.make()                                                                # restart: a new engine over the same records
    a.step([it])
    a.step([it])
    assert len(a.fake.calls) == 1


def test_one_workflow_one_trade_a_day_and_each_side_has_its_own(a, clock):
    ce = entered(a)
    a.step([intent(status="TARGET", exit=140.0)])                                 # trade 1 over
    a.step([intent(status="TARGET", exit=140.0)])
    ce2 = intent(n=2, entry=102.0, entry_time="10:00", setup_time="10:00")
    a.step([ce2])
    a.step([ce2])
    assert json.loads(a.rec(ce2)["extra_json"])["slot"] == "CE2"
    a.step([intent(n=2, entry=102.0, setup_time="10:00", status="STOP", exit=95.0)])
    a.step([intent(n=2, entry=102.0, setup_time="10:00", status="STOP", exit=95.0)])
    ce3 = intent(n=3, entry=103.0, setup_time="10:00")
    a.step([ce3])
    assert a.rec(ce3)["phase"] == "SKIPPED" and "no unused Automate workflow for CALL" in a.rec(ce3)["note"]
    n = len(a.fake.calls)
    put = intent(n=4, side="PUT", symbol=PE, entry=50.0, setup_time="10:00")
    a.fake.symbol = PE
    a.step([put])
    assert json.loads(a.rec(put)["extra_json"])["slot"] == "PE1" and len(a.fake.calls) == n + 1


def test_a_refused_entry_webhook_orders_nothing_and_says_so(a, clock):
    a.fake.answer = Res(False, None, 404, "HTTP 404")
    it = intent()
    a.step([it])
    assert a.rec(it)["phase"] == "REJECTED" and "refused the entry webhook" in a.text() and not a.b.positions()


def test_a_lost_answer_is_never_resent_and_a_late_position_is_still_picked_up(a, clock):
    a.fake.answer = Res(False, None, None, "timeout", uncertain=True)
    a.fake.fills = False
    it = intent()
    a.step([it])
    assert a.rec(it)["phase"] == "ENTRY_SENT" and "NOT be sent again" in a.text()
    clock.advance(30)
    a.step([it])
    assert len(a.fake.calls) == 1                                                 # not sent again
    a.b.place(SYM, 65, 1, 2, limit=101.0, tag="auto")                             # the workflow had run after all
    a.step([it])
    assert a.rec(it)["phase"] == "PROTECTED"


def test_no_position_after_the_watch_time_needs_a_human_and_halts_the_day(a, clock):
    a.fake.fills = False
    it = intent()
    a.step([it])
    clock.advance(a.cfg["automate_entry_watch_s"] + 5)
    a.step([it])
    assert a.rec(it)["phase"] == "CHECK_MANUALLY" and a.store.halted(DAY, MODE)
    assert any(lv == "critical" and "NO position" in t for lv, t in a.msgs)
    later = intent(n=2, entry=102.0, setup_time="10:00")
    a.step([later])
    assert a.rec(later)["phase"] == "SKIPPED" and "halted" in a.rec(later)["note"]


def test_an_exit_that_does_not_complete_is_sent_once_more_then_marked_stuck(a, clock):
    it = entered(a)
    a.fake.fills = False                                                          # the exit workflow does not close the position
    over = intent(status="STOP", exit=95.0)
    a.step([over])
    assert a.rec(it)["phase"] == "EXIT_SENT"
    clock.advance(a.cfg["automate_exit_wait_s"] + 1)
    a.step([over])
    assert len([c for c in a.fake.calls if "exit" in c[0]]) == 2
    clock.advance(a.cfg["automate_exit_wait_s"] + 1)
    a.step([over])
    assert a.rec(it)["phase"] == "STUCK" and a.store.halted(DAY, MODE) and any("EXIT IT YOURSELF" in t for _, t in a.msgs)


def test_a_refused_exit_webhook_is_stuck_at_once(a, clock):
    it = entered(a)
    a.fake.answer = Res(False, None, 404, "HTTP 404")
    a.step([intent(status="STOP", exit=95.0)])
    assert a.rec(it)["phase"] == "STUCK" and any("EXIT THE POSITION YOURSELF" in t for _, t in a.msgs)


def test_a_position_closed_by_the_automate_emergency_stop_is_recorded(a, clock):
    it = entered(a)
    clock.advance(60)
    a.b.external_close(SYM, 80.0)
    a.step([intent()])
    r = a.rec(it)
    assert r["phase"] == "CLOSED" and "closed at the broker" in r["exit_reason"] and r["realized_pnl"] == pytest.approx((80.0 - 101.0) * 65)


def test_the_day_loss_limit_blocks_the_next_entry(clock):
    a = AEnv(cfg={"max_daily_loss_inr": 300}, exit_price=95.0)
    it = entered(a)
    a.step([intent(status="STOP", exit=95.0)])
    a.step([intent(status="STOP", exit=95.0)])
    assert a.store.halted(DAY, MODE)                                              # (95-101)*65 = -390 <= -300


def test_an_unknown_open_position_halts_new_entries(a, clock):
    a.b.place("NSE:NIFTY26NOV25000CE", 65, 1, 2, limit=50.0, tag="manual")
    a.e.reconcile(NOW)
    assert a.store.halted(DAY, MODE) and any("orphan" in k or "did not open" in t for k, t in [(lv, t) for lv, t in a.msgs])


def test_positions_that_cannot_be_read_mean_no_entry(a, clock):
    a.b.reads_fail = True
    it = intent()
    a.step([it])
    assert a.rec(it)["phase"] == "SKIPPED" and "cannot be read" in a.rec(it)["note"] and not a.fake.calls


def test_a_stale_dashboard_sends_no_new_entry_and_an_open_trade_is_not_exited_blindly(a, clock):
    it = intent()
    a.step([it], dash_fresh=False)
    assert not a.fake.calls and a.rec(it) is None
    it = entered(a)
    a.step([intent(status="STOP", exit=95.0)], dash_fresh=False)                   # the dashboard is silent: keep what we have
    assert a.rec(it)["phase"] == "PROTECTED" and len(a.fake.calls) == 1 and "dashboard is silent" in a.text()


def test_emergency_exit_sends_the_exit_webhook(a, clock):
    it = entered(a)
    a.e.request_flatten()
    a.step([intent()])
    a.step([intent()])
    assert a.rec(it)["phase"] == "CLOSED" and "emergency" in a.rec(it)["exit_reason"]


def test_webhook_addresses_are_checked_and_never_logged(a, clock):
    assert automate.valid_url(ENTRY) and not automate.valid_url("http://api-t2.fyers.in/webhook/x") and not automate.valid_url("https://evil.example.com/webhook/x")
    assert not automate.valid_url("https://api-t2.fyers.in.evil.com/x") and not automate.valid_url("https://fyers.in/")
    it = entered(a)
    a.step([intent(status="TARGET", exit=140.0)])
    blob = json.dumps(a.store.orders()) + json.dumps(a.store.events()) + a.text()
    assert "entry-aaa" not in blob and "exit-bbb" not in blob


def test_fire_answers_and_refuses_a_foreign_address():
    class S:
        def __init__(self, code=200, text="ok", exc=None):
            self.code, self.text, self.exc = code, text, exc

        def post(self, url, json=None, timeout=None):
            if self.exc:
                raise self.exc
            return type("R", (), {"status_code": self.code, "text": self.text})()
    assert automate.fire(ENTRY, session=S(200)).ok
    r = automate.fire(ENTRY, session=S(502))
    assert not r.ok and r.uncertain
    r = automate.fire(ENTRY, session=S(404, "nope"))
    assert not r.ok and not r.uncertain
    assert automate.fire(ENTRY, session=S(exc=TimeoutError("t"))).uncertain
    r = automate.fire("https://evil.example.com/hook", session=S(200))
    assert not r.ok and not r.uncertain


def test_load_config_drops_bad_slots_and_keeps_live_urls_out_of_the_test_block(tmp_path):
    p = tmp_path / "automate.json"
    p.write_text(json.dumps({"slots": [SLOTS["slots"][0], {"name": "X", "side": "CALL", "entry_url": "https://evil.com/a", "exit_url": EXIT},
                                       {"name": "CE1", "side": "CALL", "entry_url": ENTRY + "9", "exit_url": EXIT + "9"}, {"name": "S", "side": "UP", "entry_url": ENTRY, "exit_url": EXIT}],
                             "test": {"entry_url": ENTRY, "exit_url": "https://api-t2.fyers.in/webhook/execute?id=test-exit"}}), encoding="utf-8")
    ac = automate.load_config(p)
    assert [s["name"] for s in ac["slots"]] == ["CE1"]                            # evil host, duplicate name and bad side dropped
    assert "entry_url" not in ac["test"] and "exit_url" in ac["test"]             # the test block can never point at a live order workflow
    assert automate.load_config(tmp_path / "nope.json") == {"slots": [], "test": {}}


def test_default_executor_is_the_direct_api_and_junk_falls_back_to_it(tmp_path):
    assert config.load(tmp_path / "none.json")["executor"] == "api"
    p = tmp_path / "c.json"
    p.write_text('{"executor": "automate"}')
    assert config.load(p)["executor"] == "automate"
    p.write_text('{"executor": "banana"}')
    assert config.load(p)["executor"] == "api"


# ---------------------------------------------------------------- the runner picks the executor
def _runner_for(monkeypatch, tmp_path, executor, slots=SLOTS):
    from execution import mode as modemod
    from execution.instrument import Instruments
    from execution.run import Runner
    from .conftest import trade
    monkeypatch.setattr(modemod, "KILL", tmp_path / "KILL")
    monkeypatch.setattr(modemod, "load_token", lambda now=None: ("APP-100", "TOKEN"))
    monkeypatch.setattr(modemod, "old_router_live", lambda: False)
    monkeypatch.setattr(automate, "load_config", lambda path=None: slots)
    p = tmp_path / "stream_state.json"
    p.write_text(json.dumps({"date": DAY, "trades": {"trades": [trade()]}}), encoding="utf-8")
    import pathlib
    cfg = {**config.load(pathlib.Path("/nonexistent")), "executor": executor}
    b = MockBroker()
    r = Runner(cfg=cfg, store=Store(":memory:"), dash_path=p, notify=lambda text, level="info": None, instruments=Instruments(rows={SYM: {"lot": 65, "tick": 0.05, "expiry": 4_000_000_000}}),
               broker_factory=lambda a_, t, c: b, feed_factory=lambda *a_, **k: type("F", (), {"start": lambda s: None})())
    return r, b


def test_runner_default_executor_is_unchanged_direct_api_with_the_order_feed(monkeypatch, tmp_path, clock):
    r, b = _runner_for(monkeypatch, tmp_path, "api")
    assert r.arm("ACTIVATE LIVE", NOW)[0]
    assert r.live.mode == "LIVE" and r.feed is not None
    r.cycle(NOW)
    r.cycle(NOW)
    assert [c for c in b.calls if c[0] == "place"]                                 # a real API order path, as before
    assert r.status(NOW)["executor"] == "api"


def test_runner_with_executor_automate_sends_webhooks_not_api_orders(monkeypatch, tmp_path, clock):
    r, b = _runner_for(monkeypatch, tmp_path, "automate")
    sent = []
    monkeypatch.setattr(automate, "fire", lambda url, payload=None, **k: sent.append(url) or Res(True, None, 200, "ok"))
    ok, msg = r.arm("ACTIVATE LIVE", NOW)
    assert ok and isinstance(r.live, AutomateEngine) and r.feed is None
    r.live.fire = automate.fire
    r.cycle(NOW)
    r.cycle(NOW)
    assert len(sent) == 1 and "entry" in sent[0] and not [c for c in b.calls if c[0] == "place"]    # a webhook, and NO API order
    s = r.status(NOW)
    assert s["executor"] == "automate" and s["engine"] == "AUTOMATE"
    json.dumps(s)
    assert not r.store.signals(DAY, "LIVE") and r.store.signals(DAY, "AUTOMATE")


def test_runner_refuses_live_with_executor_automate_when_no_workflow_is_configured(monkeypatch, tmp_path, clock):
    r, b = _runner_for(monkeypatch, tmp_path, "automate", slots={"slots": [], "test": {}})
    ok, msg = r.arm("ACTIVATE LIVE", NOW)
    assert not ok and "automate.json" in msg and not r.mode.live
