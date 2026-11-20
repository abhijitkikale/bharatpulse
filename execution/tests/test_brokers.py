"""The FYERS REST adapter against a scripted HTTP session (no network): payloads, answer classification, the orderbook normalisation, instrument validation and the dashboard reader."""
import json
import time

from execution import config, trade_source
from execution.brokers import BASE, FyersBroker, RateLimiter, norm_order, norm_pos, norm_tag
from execution.instrument import Instruments
from execution.models import make_signal_id, parse_intent

from .conftest import DAY, SYM, trade


class Resp:
    def __init__(self, status=200, body=None, headers=None, bad_json=False):
        self.status_code, self._b, self.headers, self.bad = status, body, headers or {}, bad_json

    def json(self):
        if self.bad:
            raise ValueError("no json")
        return self._b


class Sess:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "params": params, "headers": headers})
        a = self.answers.pop(0) if self.answers else Resp(200, {"s": "ok", "code": 200})
        if isinstance(a, Exception):
            raise a
        return a


def broker(*answers):
    s = Sess(*answers)
    return FyersBroker("APP-100", "TOKEN", config.load(), session=s, sleep=lambda x: None), s


def test_place_sends_the_documented_payload_to_the_sync_endpoint():
    b, s = broker(Resp(200, {"s": "ok", "code": 1101, "message": "ok", "id": "2611190001"}))
    r = b.place(SYM, 65, 1, 1, limit=102.0, tag="BPabc123E")
    c = s.calls[0]
    assert (c["method"], c["url"]) == ("POST", BASE + "/orders/sync") and c["headers"]["Authorization"] == "APP-100:TOKEN"
    assert c["json"] == {"symbol": SYM, "qty": 65, "type": 1, "side": 1, "productType": "INTRADAY", "limitPrice": 102.0, "stopPrice": 0.0, "validity": "DAY", "disclosedQty": 0,
                         "offlineOrder": False, "orderTag": "BPabc123E"}
    assert r.ok and r.id == "2611190001" and not r.uncertain


def test_a_stop_limit_modify_and_cancel_use_patch_and_delete():
    b, s = broker(Resp(200, {"s": "ok", "code": 1101, "id": "9"}), Resp(200, {"s": "ok", "code": 1102}), Resp(200, {"s": "ok", "code": 1103}))
    b.place(SYM, 65, -1, 4, limit=88.65, stop=90.0, tag="BPabcS")
    assert s.calls[0]["json"]["type"] == 4 and s.calls[0]["json"]["stopPrice"] == 90.0 and s.calls[0]["json"]["limitPrice"] == 88.65 and s.calls[0]["json"]["side"] == -1
    assert b.modify("9", limit=103.4, stop=105.0, qty=65, otype=4).ok and (s.calls[1]["method"], s.calls[1]["json"]) == ("PATCH", {"id": "9", "limitPrice": 103.4, "stopPrice": 105.0, "qty": 65, "type": 4})
    assert b.cancel("9").ok and (s.calls[2]["method"], s.calls[2]["json"]) == ("DELETE", {"id": "9"})


def test_every_kind_of_doubtful_answer_is_uncertain_not_failed():
    for ans in (TimeoutError("timed out"), Resp(500, {"s": "error", "message": "server error"}), Resp(429, {"s": "error", "message": "slow down"}, {"Retry-After": "1"}),
                Resp(200, {"s": "ok", "code": 201, "message": "sent"}), Resp(200, bad_json=True)):
        b, _ = broker(ans)
        r = b.place(SYM, 65, 1, 1, limit=102.0, tag="t")
        assert not r.ok and r.uncertain, ans


def test_a_definite_refusal_is_a_failure_and_a_bad_token_is_flagged():
    b, _ = broker(Resp(400, {"s": "error", "code": -50, "message": "RMS:Margin Shortfall"}))
    r = b.place(SYM, 65, 1, 1, limit=102.0, tag="t")
    assert not r.ok and not r.uncertain and not r.auth and "Margin" in r.msg
    b, _ = broker(Resp(401, {"s": "error", "code": -16, "message": "Could not authenticate the user"}))
    assert b.place(SYM, 65, 1, 1, limit=102.0, tag="t").auth
    b, _ = broker(Resp(403, {"s": "error", "code": -99, "message": "request from an IP that is not registered"}))
    assert b.place(SYM, 65, 1, 1, limit=102.0, tag="t").auth


def test_reads_are_retried_orders_are_not():
    b, s = broker(Resp(500, {"s": "error"}), TimeoutError("x"), Resp(200, {"s": "ok", "code": 200, "netPositions": [{"symbol": SYM, "netQty": 65, "netAvg": 102.0, "ltp": 110.0, "realized_profit": 0, "unrealized_profit": 520, "productType": "INTRADAY"}]}))
    pos = b.positions()
    assert len(s.calls) == 3 and pos[0].net_qty == 65 and pos[0].unrealized == 520
    b, s = broker(TimeoutError("x"))
    b.place(SYM, 65, 1, 1, limit=102.0, tag="t")
    assert len(s.calls) == 1


def test_the_orderbook_is_normalised_status_codes_partial_fills_and_tags():
    rows = [{"id": "1", "orderTag": "1:BPabcE", "symbol": SYM, "status": 2, "qty": 65, "filledQty": 65, "tradedPrice": 101.5, "side": 1, "type": 1},
            {"id": "2", "orderTag": "1:BPabcS", "symbol": SYM, "status": 6, "qty": 65, "filledQty": 0, "stopPrice": 90, "limitPrice": 88.65, "side": -1, "type": 4},
            {"id": "3", "orderTag": "", "symbol": SYM, "status": 6, "qty": 65, "filledQty": 30, "side": 1, "type": 1}, {"id": "4", "status": 5, "qty": 65}, {"id": "5", "status": 1, "qty": 65}]
    got = [norm_order(r) for r in rows]
    assert [o.status for o in got] == ["filled", "pending", "partial", "rejected", "cancelled"] and got[0].tag == "BPabcE" and got[0].avg == 101.5 and got[1].stop == 90.0
    assert norm_tag("1:Tag") == "Tag" and norm_tag("Tag") == "Tag" and norm_tag(None) == ""
    b, s = broker(Resp(200, {"s": "ok", "code": 200, "orderBook": rows}))
    assert b.order("1").status == "filled" and b.order("zzz") is None
    b, s = broker(Resp(200, {"s": "ok", "code": 200, "orderBook": []}), Resp(200, {"s": "ok", "code": 200, "orderBook": rows}))
    assert [o.id for o in b.order_by_tag("BPabcS")] == ["2"] and s.calls[0]["params"] == {"order_tag": "1:BPabcS"}        # filter first, then the whole book
    assert norm_pos({"symbol": "X", "netQty": -65}).net_qty == -65


def test_the_rate_limiter_keeps_under_the_brokers_operations_per_second():
    slept = []
    t = [0.0]
    rl = RateLimiter(3, clock=lambda: t[0], sleep=lambda s: slept.append(s))
    for _ in range(3):
        rl.wait()
    rl.wait()
    assert slept and slept[0] > 0


def test_instrument_validation_against_the_master():
    far = int(time.time()) + 86400 * 30
    inst = Instruments(rows={SYM: {"lot": 65, "tick": 0.05, "expiry": far}, "NSE:NIFTY26NOV25300PE": {"lot": 65, "tick": 0.05, "expiry": far}, "NSE:OLD25300CE": {"lot": 65, "tick": 0.05, "expiry": 1000}})
    assert inst.validate(SYM, 65, "CALL").ok and inst.validate(SYM, 130, "CALL").ok and inst.lot_size(SYM) == 65
    assert "whole number of lots" in inst.validate(SYM, 70, "CALL").reason
    assert "not a PE" in inst.validate(SYM, 65, "PUT").reason
    assert "not found" in inst.validate("NSE:NOPE", 65, "CALL").reason and "expired" in inst.validate("NSE:OLD25300CE", 65, "CALL").reason


def test_instrument_master_csv_is_searched_by_ticker(tmp_path):
    f = tmp_path / "NSE_FO.csv"
    f.write_text(f"1001,NIFTY 25300 CE,14,65,0.05,,0915-1530|1530-1540:,2026-11-19,1795000000,{SYM},10,11,1,NIFTY,26000,25300,CE,26000\n", encoding="utf-8")
    assert Instruments(path=f, fetch=False).lookup(SYM) == {"lot": 65, "tick": 0.05, "expiry": 1795000000} and Instruments(path=f, fetch=False).lookup("NSE:X") is None


def test_dashboard_trade_object_is_read_as_is_and_bad_ones_are_refused(tmp_path):
    p = tmp_path / "stream_state.json"
    p.write_text(json.dumps({"date": DAY, "trades": {"trades": [trade(), trade(n=2, target=None), trade(n=3, side="HOLD")]}}), encoding="utf-8")
    its, fresh, problems = trade_source.read(DAY, p, 90)
    assert len(its) == 1 and fresh and its[0].entry == 100.0 and its[0].stop_now == 90.0 and its[0].target == 140.0 and its[0].symbol == SYM
    assert any("no 'target'" in x for x in problems) and any("unknown side" in x for x in problems)
    assert trade_source.read("2026-11-20", p, 90)[1] is False and trade_source.read(DAY, tmp_path / "none.json", 90)[1] is False
    old = trade_source.read(DAY, p, -1)
    assert old[1] is False and old[0]                                                 # a stale file is still readable (to manage what we hold) but flagged
    a, _ = parse_intent(DAY, trade())
    b, _ = parse_intent(DAY, trade(n=9))
    assert a.signal_id == b.signal_id == make_signal_id(DAY, trade())              # the trade number is not part of the signal id


def test_the_fyers_session_connects_over_ipv4_only():
    """FYERS refuses orders that arrive from the PC's IPv6 address (20 Nov 2026): the default session must bind its source to IPv4."""
    from execution.brokers import ipv4_session
    s = ipv4_session()
    pm = s.get_adapter("https://api-t1.fyers.in/api/v3/orders/sync").poolmanager
    assert pm.connection_pool_kw.get("source_address") == ("0.0.0.0", 0)
    b = FyersBroker("APP-100", "TOKEN", config.load())
    assert b.s.get_adapter("https://api-t1.fyers.in").poolmanager.connection_pool_kw.get("source_address") == ("0.0.0.0", 0)
