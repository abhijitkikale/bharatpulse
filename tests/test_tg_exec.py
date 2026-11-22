import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pipeline"))
import tg_control as T  # noqa: E402


class Sync:
    """Runs the background step inline so the test can see the answer."""
    def __init__(self, monkeypatch):
        monkeypatch.setattr(T, "threading", types.SimpleNamespace(Thread=lambda target=None, **k: type("X", (), {"start": lambda self: target()})(), Timer=None))
        monkeypatch.setattr(T, "subprocess", types.SimpleNamespace(run=lambda *a, **k: None, Popen=lambda *a, **k: None))      # /cancel would call "shutdown /a"


def _ctl(monkeypatch, replies):
    Sync(monkeypatch)
    out = []
    c = T.Control(lambda: "status", "1", out.append)
    calls = []

    def fake(path, body=None):
        calls.append((path, body))
        return replies.get(path)
    monkeypatch.setattr(c, "_exec_call", fake)
    return c, out, calls


def test_live_and_exit_need_a_confirm_within_60_seconds_and_send_the_typed_phrases(monkeypatch):
    c, out, calls = _ctl(monkeypatch, {"/api/arm": {"ok": True, "msg": "LIVE is ON"}, "/api/emergency": {"ok": True, "msg": "emergency exit started"}})
    c.handle("exec_live", now=1000)
    assert calls == [] and "REAL orders" in out[-1]                                   # nothing is sent by the first message
    c.handle("confirm", now=1030)
    assert calls[-1] == ("/api/arm", {"phrase": "ACTIVATE LIVE"}) and "LIVE is ON" in out[-2]
    c.handle("exec_exit", now=2000)
    assert len(calls) == 1
    c.handle("confirm", now=2100)                                                     # too late: the confirmation expired
    assert len(calls) == 1 and "Nothing to confirm" in out[-1]
    c.handle("exec_exit", now=3000)
    c.handle("cancel", now=3010)
    c.handle("confirm", now=3020)
    assert len(calls) == 1
    c.handle("exec_exit", now=4000)
    c.handle("confirm", now=4010)
    assert calls[-1] == ("/api/emergency", {"phrase": "EXIT NOW"}) and "emergency exit" in out[-2]


def test_off_status_and_a_missing_engine_are_answered_plainly(monkeypatch):
    status = {"mode": "LIVE", "note": "", "auth": {"ok": True}, "connection": {"ok": True}, "dashboard": {"fresh": True}, "halted": None, "entries_today": 2, "realized": 1234.0, "unrealized": -200.0,
              "current": {"symbol": "NSE:NIFTY26NOV25800CE", "phase": "PROTECTED", "qty": 65, "avg_entry": 120.0, "sl_stop": 108.0}}
    c, out, calls = _ctl(monkeypatch, {"/api/disarm": {"ok": True, "msg": "x"}, "/api/status": status})
    c.handle("exec_off")
    assert calls[-1][0] == "/api/disarm" and "LIVE execution is OFF" in out[-2]
    c.handle("exec_status")
    assert "Mode: LIVE" in out[-2] and "PROTECTED" in out[-2] and "+Rs 1,234" in out[-2] and "-Rs 200" in out[-2]
    d, out2, _ = _ctl(monkeypatch, {})
    d.handle("exec_status")
    assert "NOT running" in out2[-2]
    d.handle("exec_off")
    assert "not running" in out2[-2]
    assert all(x in dict(T.COMMANDS) for x in ("exec_status", "exec_start", "exec_off", "exec_live", "exec_exit"))
