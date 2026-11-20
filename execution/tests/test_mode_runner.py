"""PAPER / LIVE selection, the runner with a mock broker, the status page's controls, and the restart rule (a restart is always PAPER)."""
import datetime as dt
import json

from execution import config, mode as modemod
from execution.engine import IST
from execution.instrument import Instruments
from execution.mock_broker import MockBroker
from execution.run import Runner
from execution.store import Store
from execution.ui_server import make_handler

from .conftest import DAY, NOW, SYM, trade


def _setup(monkeypatch, tmp_path, token=True, old_live=False):
    monkeypatch.setattr(modemod, "KILL", tmp_path / "KILL")
    monkeypatch.setattr(modemod, "load_token", lambda now=None: ("APP-100", "TOKEN") if token else None)
    monkeypatch.setattr(modemod, "old_router_live", lambda: old_live)
    monkeypatch.setattr(config, "OLD_ROUTER_CONFIG", tmp_path / "orders_config.json")


class Feed:
    def __init__(self, *a, **k):
        self.started = False

    def start(self):
        self.started = True


def _runner(tmp_path, broker, trades, now=NOW):
    p = tmp_path / "stream_state.json"
    p.write_text(json.dumps({"date": DAY, "trades": {"trades": trades}}), encoding="utf-8")
    far = 4_000_000_000
    r = Runner(cfg={**config.load(pathlib_none()), "ws_enabled": True}, store=Store(":memory:"), dash_path=p, notify=lambda text, level="info": None,
               instruments=Instruments(rows={SYM: {"lot": 65, "tick": 0.05, "expiry": far}}), broker_factory=lambda a, t, c: broker, feed_factory=Feed)
    return r


def pathlib_none():
    import pathlib
    return pathlib.Path("/nonexistent")


def test_mode_starts_in_paper_and_live_needs_the_phrase_a_token_and_no_other_live_router(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    m = modemod.ModeManager()
    assert m.mode == "PAPER" and not m.live
    assert not m.arm("activate live")[0] and not m.arm("")[0] and m.mode == "PAPER"
    assert m.arm("ACTIVATE LIVE", NOW)[0] and m.live
    assert modemod.ModeManager().mode == "PAPER"                                      # a new process (a restart) never starts in LIVE
    _setup(monkeypatch, tmp_path, token=False)
    assert "token" in modemod.ModeManager().arm("ACTIVATE LIVE", NOW)[1]
    _setup(monkeypatch, tmp_path, old_live=True)
    assert "order router" in modemod.ModeManager().arm("ACTIVATE LIVE", NOW)[1]
    _setup(monkeypatch, tmp_path)
    (tmp_path / "KILL").write_text("x")
    assert "KILL" in modemod.ModeManager().arm("ACTIVATE LIVE", NOW)[1]


def test_paper_cycle_mirrors_trades_and_sends_nothing_to_fyers(monkeypatch, tmp_path, clock):
    _setup(monkeypatch, tmp_path)
    b = MockBroker()
    r = _runner(tmp_path, b, [trade()])
    r.cycle(NOW)
    r.cycle(NOW)
    assert r.store.signals(DAY, "PAPER") and not r.store.signals(DAY, "LIVE") and not b.calls
    s = r.status(NOW)
    assert s["mode"] == "PAPER" and s["current"]["symbol"] == SYM and s["current"]["dashboard"]["target"] == 140.0 and s["dashboard"]["fresh"]
    json.dumps(s)                                                                    # the status page's data is plain JSON


def test_arming_live_then_the_dashboards_trade_goes_to_the_broker_once(monkeypatch, tmp_path, clock):
    _setup(monkeypatch, tmp_path)
    b = MockBroker()
    r = _runner(tmp_path, b, [trade()])
    ok, msg = r.arm("ACTIVATE LIVE", NOW)
    assert ok and r.mode.live and r.feed.started
    r.cycle(NOW)
    r.cycle(NOW)
    r.cycle(NOW)
    entries = [d for a, d in b.calls if a == "place" and d["side"] == 1]
    assert len(entries) == 1 and entries[0]["qty"] == 65 and entries[0]["limit"] == 102.0
    assert r.store.signals(DAY, "LIVE")[0]["phase"] == "PROTECTED" and not r.store.signals(DAY, "PAPER")        # LIVE and PAPER never both act
    assert r.status(NOW)["mode"] == "LIVE"


def test_switching_live_off_stops_new_entries_but_keeps_managing_the_open_position(monkeypatch, tmp_path, clock):
    _setup(monkeypatch, tmp_path)
    b = MockBroker()
    r = _runner(tmp_path, b, [trade()])
    r.arm("ACTIVATE LIVE", NOW)
    r.cycle(NOW)
    r.cycle(NOW)
    r.disarm()
    assert not r.mode.live
    (tmp_path / "stream_state.json").write_text(json.dumps({"date": DAY, "trades": {"trades": [trade(status="TARGET", exit=140.0)]}}), encoding="utf-8")
    r.cycle(NOW)
    r.cycle(NOW)
    assert r.store.signals(DAY, "LIVE")[0]["phase"] == "CLOSED"                      # the open live position was still taken to its exit


def test_emergency_exit_writes_the_kill_file_exits_and_blocks_live(monkeypatch, tmp_path, clock):
    _setup(monkeypatch, tmp_path)
    b = MockBroker()
    r = _runner(tmp_path, b, [trade()])
    r.arm("ACTIVATE LIVE", NOW)
    r.cycle(NOW)
    r.cycle(NOW)
    r.emergency()
    assert (tmp_path / "KILL").exists() and not r.mode.live
    r.cycle(NOW)
    assert b.of("exit")                                                              # stop cancelled, then the sell
    assert "KILL" in r.arm("ACTIVATE LIVE", NOW)[1]


def test_a_failed_profile_call_refuses_to_arm(monkeypatch, tmp_path, clock):
    _setup(monkeypatch, tmp_path)

    class Down(MockBroker):
        def profile(self):
            return None
    r = _runner(tmp_path, Down(), [trade()])
    ok, msg = r.arm("ACTIVATE LIVE", NOW)
    assert not ok and not r.mode.live and "profile" in msg


def test_the_status_page_controls_need_the_header_and_the_typed_phrases(monkeypatch, tmp_path, clock):
    import io
    _setup(monkeypatch, tmp_path)
    b = MockBroker()
    r = _runner(tmp_path, b, [trade()])
    H = make_handler(r)

    def call(method, path, body=None, headers=None):
        h = H.__new__(H)
        raw = json.dumps(body or {}).encode()
        h.path, h.headers, h.rfile, h.wfile = path, {"Host": "127.0.0.1:8780", "Content-Length": str(len(raw)), **(headers or {})}, io.BytesIO(raw), io.BytesIO()
        out = {}
        h.send_response = lambda code: out.__setitem__("code", code)
        h.send_header = lambda *a: None
        h.end_headers = lambda: None
        getattr(h, "do_" + method)()
        return out["code"], json.loads(h.wfile.getvalue() or b"{}") if path.startswith("/api") else h.wfile.getvalue()
    code, body = call("GET", "/api/status")
    assert code == 200 and body["mode"] == "PAPER"
    assert call("POST", "/api/arm", {"phrase": "ACTIVATE LIVE"})[0] == 403               # no header: refused (blocks a web page from clicking it)
    hdr = {"X-Requested-With": "execution-ui"}
    assert call("POST", "/api/arm", {"phrase": "nope"}, hdr)[1]["ok"] is False
    assert call("POST", "/api/arm", {"phrase": "ACTIVATE LIVE"}, hdr)[1]["ok"] is True and r.mode.live
    assert call("POST", "/api/emergency", {"phrase": "exit"}, hdr)[1]["ok"] is False and r.mode.live
    assert call("POST", "/api/disarm", {}, hdr)[1]["ok"] is True and not r.mode.live
    code, page = call("GET", "/")
    assert code == 200 and b"EMERGENCY EXIT" in page and b"ACTIVATE LIVE" in page
    h = H.__new__(H)
    h.path, h.headers, h.rfile, h.wfile = "/api/status", {"Host": "evil.example.com"}, io.BytesIO(), io.BytesIO()
    out = {}
    h.send_response = lambda c: out.__setitem__("c", c)
    h.send_header = lambda *a: None
    h.end_headers = lambda: None
    h.do_GET()
    assert out["c"] == 403                                                           # only 127.0.0.1 / localhost hosts are answered
