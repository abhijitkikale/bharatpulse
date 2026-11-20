"""The execution card on the dashboard's refresh page: the card logic, and the thin glue added to tools/refresh_app.py."""
import datetime as dt
import io
import json
import pathlib
import sys

from execution import config, helper_card

ROOT = config.ROOT


def test_token_card_valid_expired_and_missing(monkeypatch, tmp_path):
    f = tmp_path / "t.json"
    monkeypatch.setattr(config, "ORDER_TOKEN", f)
    assert helper_card.token_card()["have"] is False
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    f.write_text(json.dumps({"app_id": "A-200", "access_token": "x", "expires": (dt.datetime.now(ist) + dt.timedelta(hours=5)).isoformat()}))
    assert helper_card.token_card()["valid"] is True and "valid until" in helper_card.token_card()["text"]
    f.write_text(json.dumps({"app_id": "A-200", "access_token": "x", "expires": (dt.datetime.now(ist) - dt.timedelta(hours=1)).isoformat()}))
    assert helper_card.token_card()["valid"] is False and "expired" in helper_card.token_card()["text"]


def test_engine_card_when_nothing_answers_and_when_the_page_does(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "get", boom)
    assert helper_card.engine_card() == {"running": False, "mode": None, "live": False}

    class R:
        def json(self):
            return {"mode": "LIVE", "halted": None, "auth": {"ok": True}, "ws": {"connected": True}, "recon": {"text": "matches the broker"}, "current": {"phase": "PROTECTED"}, "realized": 120.0}
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    c = helper_card.engine_card()
    assert c["running"] and c["live"] and c["mode"] == "LIVE" and c["current"] == "PROTECTED"


def test_the_engine_is_started_once_and_always_in_paper(monkeypatch, tmp_path):
    started = []

    class P:
        def poll(self):
            return None
    monkeypatch.setattr(config, "LOGS", tmp_path)
    monkeypatch.setattr(helper_card, "engine_card", lambda timeout=0.8: {"running": False})
    helper_card._ENGINE["proc"] = None
    monkeypatch.setattr(helper_card.subprocess, "Popen", lambda cmd, **k: started.append(cmd) or P())
    assert "PAPER" in helper_card.start_engine()
    assert started[0][1:] == ["-m", "execution", "run"] and "--live" not in started[0]
    assert "starting" in helper_card.start_engine() and len(started) == 1                 # not started twice
    monkeypatch.setattr(helper_card, "engine_card", lambda timeout=0.8: {"running": True})
    assert "already running" in helper_card.start_engine() and len(started) == 1


def test_order_login_reports_missing_details_and_the_result(monkeypatch):
    monkeypatch.setattr(helper_card, "order_app_ready", lambda: False)
    helper_card._LOGIN.update(busy=False, msg="")
    assert "not set on this PC" in helper_card.order_login()
    monkeypatch.setattr(helper_card, "order_app_ready", lambda: True)

    class Done:
        returncode, stdout, stderr = 0, "Opening...\nOrder token saved to fyers_order_token.json; valid until 2026-11-20T06:30:00+05:30.\n", ""
    monkeypatch.setattr(helper_card.subprocess, "run", lambda *a, **k: Done())
    assert "started" in helper_card.order_login()
    import time
    for _ in range(50):
        if not helper_card._LOGIN["busy"] and helper_card._LOGIN["msg"].startswith("order token saved"):
            break
        time.sleep(0.05)
    assert helper_card._LOGIN["msg"].startswith("order token saved") and "token" in helper_card.card()["login"]["msg"]


def test_environment_variables_set_after_start_are_still_found(monkeypatch):
    monkeypatch.delenv("FYERS_ORDER_APP_ID", raising=False)
    monkeypatch.setenv("FYERS_ORDER_APP_SECRET", "s")
    monkeypatch.setattr(helper_card, "user_env", lambda n: {"FYERS_ORDER_APP_ID": "A-200"}.get(n) or helper_card.os.environ.get(n, ""))
    assert helper_card.order_app_ready() is True and helper_card.child_env()["FYERS_ORDER_APP_ID"] == "A-200"


def _helper():
    for p in (ROOT / "tools", ROOT / "pipeline"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import refresh_app
    return refresh_app


def test_the_refresh_page_has_the_card_and_its_buttons_are_guarded():
    ra = _helper()
    for needle in ('id="s-ex"', 'id="b-esign"', 'id="b-estart"', 'id="b-eopen"', "/exec/login", "/exec/start", "never starts in LIVE"):
        assert needle in ra.PAGE, needle
    assert "ACTIVATE LIVE" not in ra.PAGE                                             # LIVE cannot be switched on from the helper page
    c = ra.exec_card()
    assert set(c) >= {"token", "engine", "login"}
    json.dumps(c)

    H = ra.Handler
    h = H.__new__(H)
    h.path, h.headers, h.rfile, h.wfile = "/exec/start", {}, io.BytesIO(), io.BytesIO()
    out = {}
    h.send_response = lambda code: out.__setitem__("code", code)
    h.send_header = lambda *a: None
    h.end_headers = lambda: None
    h.do_POST()
    assert out["code"] == 403                                                         # no header: another web page cannot press it
    called = []
    ra.exec_post = lambda path: called.append(path)
    h.headers = {"X-BP": "1"}
    h.do_POST()
    import time
    time.sleep(0.1)
    assert out["code"] == 200 and called == ["/exec/start"]
