"""Same-contract display, stop / target / trailing from the REAL fill, live-price detection and the entry guard of the Automate executor. No network: a fake price feed, the mock broker."""
import json
import pathlib
import sys

import pytest

from execution import levels, tick_feed
from execution.automate import MODE
from execution.models import BPos

from .conftest import NOW, SYM, intent
from .test_automate import AEnv, entered

ROOT = pathlib.Path(__file__).resolve().parents[2]


class FakeTicks:
    """What execution.tick_feed.TickFeed offers the engine: price(symbol, max_age) -> (price, age) or None, watch(symbol), wait_price(symbol, timeout, max_age)."""
    def __init__(self, **prices):
        self.p = dict(prices)
        self.watched = []

    def set(self, sym, price):
        self.p[sym] = price

    def watch(self, sym):
        self.watched.append(sym)

    def price(self, sym, max_age_s=10):
        return (self.p[sym], 0.2) if self.p.get(sym) else None

    def wait_price(self, sym, timeout=1.5, max_age_s=10):
        return self.p.get(sym)


# ------------------------------------------------------------------ levels (pure)
def test_levels_are_measured_from_the_fill_with_the_dashboards_percentages():
    lv = levels.initial(179.45, 10, 40)
    assert lv["stop"] == pytest.approx(161.51, abs=0.01) and lv["target"] == pytest.approx(251.23, abs=0.01)
    assert levels.initial(100)["stop"] == 90.0 and levels.initial(100)["target"] == 140.0          # the dashboard's own numbers when the trade carries none


def test_the_trail_ladder_is_the_dashboards():
    sys.path.insert(0, str(ROOT / "pipeline"))
    import stream_trades
    assert levels.TRAIL == stream_trades.TRAIL


def test_the_stop_only_moves_up_and_check_names_stop_trail_and_target():
    lv = levels.initial(100, 10, 40)
    lv2, why = levels.ratchet(lv, 121)                                  # +21%: the stop goes to +5%
    assert lv2["stop"] == 105.0 and "+20%" in why
    lv3, _ = levels.ratchet(lv2, 110)                                   # a lower price never lowers it
    assert lv3["stop"] == 105.0
    lv4, _ = levels.ratchet(lv3, 136)                                   # +36%: +30%
    assert lv4["stop"] == 130.0
    assert levels.check(lv, 95) is None and levels.check(lv, 90)[0] == "STOP" and levels.check(lv, 140)[0] == "TARGET"
    assert levels.check(lv2, 104.9)[0] == "TRAIL"


def test_the_dashboards_raised_stop_is_mirrored_on_the_real_fill():
    lv = levels.initial(110, 10, 40)
    it = intent(entry=100.0, stop=90.0, stop_now=95.0)                  # the dashboard's regime rule moved its stop to -5% of its entry
    new, why = levels.ratchet(lv, 100.0, levels.dash_lock(it))
    assert new["stop"] == pytest.approx(104.5) and "mirrored" in why
    assert levels.dash_lock(intent()) is None                           # still the initial stop: nothing to mirror


# ------------------------------------------------------------------ same contract traded twice
def test_the_second_trade_in_the_same_contract_shows_its_own_fill_not_the_days_average(clock):
    a = AEnv(price=179.45, exit_price=172.0)
    a.b.fyers_avg = True                                                # FYERS shows the average of all the day's buys
    it1 = entered(a)
    a.step([intent(status="STOP", exit=160.0)])
    a.step([intent(status="STOP", exit=160.0)])
    assert a.rec(it1)["phase"] == "CLOSED" and a.rec(it1)["avg_entry"] == 179.45
    a.fake.price = 168.60
    it2 = intent(entry=169.1, stop=152.19, stop_now=152.19, target=236.74, entry_time="10:05", setup_time="10:04")
    a.step([it2], now=NOW.replace(minute=6))
    a.step([it2], now=NOW.replace(minute=6, second=40))
    r = a.rec(it2)
    assert r["phase"] == "PROTECTED" and r["avg_entry"] == pytest.approx(168.60, abs=0.01) and r["qty_filled"] == 65, dict(r)
    assert "168.60" in a.text() and "174.03" not in a.text()


def test_a_first_trade_and_positions_without_buy_figures_still_use_the_position_average(clock):
    a = AEnv(price=101.0)
    it = entered(a)
    assert a.rec(it)["avg_entry"] == 101.0
    ex = {"buys_before": {}}
    assert a.e._fill_price(ex, BPos(symbol=SYM, net_qty=65, avg=123.0)) == 123.0                  # no buy figures from the broker: its average


# ------------------------------------------------------------------ the engine's own stop from the real fill, on the live price
def test_the_stop_is_ten_percent_under_the_real_fill_not_under_the_dashboard_entry(clock):
    t = FakeTicks()
    a = AEnv(price=110.0, ticks=t)                                       # the dashboard assumed 100, the workflow bought at 110
    it = entered(a)
    lv = json.loads(a.rec(it)["extra_json"])["lv"]
    assert lv["fill"] == 110.0 and lv["stop"] == 99.0 and lv["target"] == 154.0
    t.set(SYM, 100.0)                                                    # above the dashboard's stop (90) but below the real one (99)... no: 100 > 99, still fine
    a.step([intent()])
    assert a.rec(it)["phase"] == "PROTECTED"
    t.set(SYM, 98.9)
    a.step([intent()])
    r = a.rec(it)
    assert r["phase"] in ("EXIT_SENT", "CLOSED") and "STOP" in r["exit_reason"] and "live feed" in r["exit_reason"] and "99.00" in r["exit_reason"]


def test_a_dashboard_stop_status_is_ignored_while_the_live_price_is_healthy_but_day_end_is_not(clock):
    t = FakeTicks()
    a = AEnv(price=101.0, ticks=t)
    it = entered(a)
    t.set(SYM, 100.0)                                                    # fine for the real levels (stop 90.9)
    a.step([intent(status="STOP", exit=90.0)])
    assert a.rec(it)["phase"] == "PROTECTED" and len([c for c in a.fake.calls if "exit" in c[0]]) == 0
    a.step([intent(status="DAY END", exit=100.0)])
    assert a.rec(it)["phase"] in ("EXIT_SENT", "CLOSED") and "day end" in a.rec(it)["exit_reason"]


def test_trailing_runs_on_the_live_price_and_the_stop_is_raised_once_and_told(clock):
    t = FakeTicks()
    a = AEnv(price=100.0, ticks=t)
    it = entered(a)
    t.set(SYM, 121.0)
    a.step([intent()])
    lv = json.loads(a.rec(it)["extra_json"])["lv"]
    assert lv["stop"] == 105.0 and a.rec(it)["phase"] == "PROTECTED"
    assert a.text().count("stop raised to 105.00") == 1
    a.step([intent()])
    assert a.text().count("stop raised to 105.00") == 1
    t.set(SYM, 104.0)
    a.step([intent()])
    assert "TRAIL" in a.rec(it)["exit_reason"]


def test_fast_check_exits_between_passes_and_never_sends_the_exit_twice(clock):
    t = FakeTicks()
    a = AEnv(price=100.0, ticks=t)
    it = entered(a)
    t.set(SYM, 89.0)
    a.e.fast_check(NOW)
    r = a.rec(it)
    assert r["phase"] == "EXIT_SENT" and "live feed" in r["exit_reason"] and len([c for c in a.fake.calls if "exit" in c[0]]) == 1
    a.e.fast_check(NOW)                                                  # a second look must not send the exit again
    assert len([c for c in a.fake.calls if "exit" in c[0]]) == 1


def test_no_live_price_means_the_dashboard_decides_as_before(clock):
    a = AEnv(price=101.0, ticks=FakeTicks())                             # a feed with no price for the contract, and no broker price either
    it = entered(a)
    a.step([intent(status="STOP", exit=90.0)])
    a.step([intent(status="STOP", exit=90.0)])
    assert a.rec(it)["phase"] == "CLOSED" and "ended (STOP)" in a.rec(it)["exit_reason"]


def test_the_broker_position_price_is_used_when_the_live_feed_has_nothing(clock):
    a = AEnv(price=100.0, ticks=FakeTicks())
    it = entered(a)
    a.b.ltp[SYM] = 85.0
    a.step([intent()])
    r = a.rec(it)
    assert r["phase"] in ("EXIT_SENT", "CLOSED") and "broker price" in r["exit_reason"]


def test_the_feature_can_be_switched_off(clock):
    t = FakeTicks()
    a = AEnv(cfg={"levels_from_fill": False}, price=100.0, ticks=t)
    it = entered(a)
    t.set(SYM, 50.0)
    a.step([intent()])
    assert a.rec(it)["phase"] == "PROTECTED" and "lv" not in json.loads(a.rec(it)["extra_json"])


def test_a_trade_open_before_a_restart_gets_its_levels_from_the_stored_fill(clock):
    t = FakeTicks()
    a = AEnv(price=100.0, ticks=t)
    it = entered(a)
    ex = json.loads(a.rec(it)["extra_json"])
    ex.pop("lv")
    a.store.update(it.signal_id, MODE, extra_json=json.dumps(ex))
    t.set(SYM, 89.0)
    a.step([intent()])
    assert "STOP" in a.rec(it)["exit_reason"]


# ------------------------------------------------------------------ the entry guard
def test_the_entry_guard_skips_an_entry_when_the_option_already_ran_away(clock):
    a = AEnv(ticks=FakeTicks(**{SYM: 105.0}))                            # dashboard entry 100, option at 105 = 5% above (limit 4%)
    a.step([intent()])
    r = a.rec(intent())
    assert r["phase"] == "SKIPPED" and "already at 105.00" in r["note"] and not a.fake.calls


def test_the_entry_guard_lets_a_normal_entry_through_and_does_nothing_without_a_price(clock):
    a = AEnv(ticks=FakeTicks(**{SYM: 103.0}))
    a.step([intent()])
    assert a.rec(intent())["phase"] == "ENTRY_SENT"
    b = AEnv(ticks=FakeTicks())
    b.step([intent()])
    assert b.rec(intent())["phase"] == "ENTRY_SENT"
    c = AEnv(cfg={"entry_guard_pct": 0}, ticks=FakeTicks(**{SYM: 150.0}))
    c.step([intent()])
    assert c.rec(intent())["phase"] == "ENTRY_SENT"


# ------------------------------------------------------------------ the feed itself (fake socket)
class FakeSock:
    def __init__(self, **kw):
        self.kw, self.subscribed, self.closed = kw, [], False

    def connect(self):
        self.kw["on_connect"]()

    def subscribe(self, symbols, data_type="SymbolUpdate"):
        self.subscribed.append((list(symbols), data_type))

    def close_connection(self):
        self.closed = True


def test_tick_feed_keeps_the_last_price_forgets_stale_ones_and_resubscribes_after_a_reconnect():
    now = [1000.0]
    f = tick_feed.TickFeed("APP", "TOK", socket_factory=FakeSock, clock=lambda: now[0])
    f.watch(SYM)
    assert f.start()
    f.thread.join(2)
    import time
    time.sleep(0.2)
    assert f.state["connected"] and f.sock.subscribed[0] == ([SYM], "SymbolUpdate")
    f.sock.kw["on_message"]({"symbol": SYM, "ltp": 101.5})
    f.sock.kw["on_message"]({"type": "cn", "s": "ok"})                         # a status message is ignored
    f.sock.kw["on_message"]({"symbol": SYM, "ltp": 0})                         # a zero price is ignored
    assert f.price(SYM) == (101.5, 0.0)
    now[0] += 30
    assert f.price(SYM) is None and f.price(SYM, 60)[0] == 101.5
    f.sock.kw["on_close"]()
    assert not f.state["connected"]
    f.sock.kw["on_connect"]()                                                  # the SDK reconnected: everything asked for is subscribed again
    time.sleep(0.2)
    assert f.sock.subscribed[-1][0] == [SYM]
    f.stop()
    assert f.sock.closed


def test_tick_feed_start_failure_is_reported_not_raised():
    def boom(**kw):
        raise ImportError("no sdk")
    f = tick_feed.TickFeed("A", "T", socket_factory=boom)
    assert f.start() is False and "not started" in f.state["error"]
    assert f.price(SYM) is None and f.wait_price(SYM, 0.1) is None


def test_the_data_token_is_read_from_its_file_and_missing_means_none(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"app_id": "X-100", "access_token": "abc"}))
    assert tick_feed.load_data_token(p) == ("X-100", "abc")
    assert tick_feed.load_data_token(tmp_path / "none.json") is None
