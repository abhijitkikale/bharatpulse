import json
import os
import pathlib
import sys
import time

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
import order_manager as om  # noqa: E402
import order_router as rt  # noqa: E402

IST = "Asia/Kolkata"
T = pd.Timestamp("2026-11-16 11:41:20", tz=IST)


def _setup(tmp_path, monkeypatch, mode, trades=None, state_age=0, date="2026-11-16"):
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"date": date, "trades": {"trades": trades if trades is not None else [
        {"n": 1, "side": "PUT", "strike": 25150, "symbol": "NSE:NIFTY2611725150PE", "entry": 140.0, "stop_now": 126.0, "status": "OPEN", "last": 141.0, "exit": None, "entry_time": "11:40"}]}}), encoding="utf-8")
    if state_age:
        old = time.time() - state_age
        os.utime(st, (old, old))
    cfgf = tmp_path / "cfg.json"
    cfgf.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    monkeypatch.setattr(rt, "STATE", st)
    monkeypatch.setattr(rt, "TOKEN", tmp_path / "token.json")
    monkeypatch.setattr(om, "CONFIG", cfgf)
    monkeypatch.setattr(om, "STATE_DIR", tmp_path / "orders")
    monkeypatch.setattr(rt, "tell", lambda text: None)
    monkeypatch.setattr(rt.trading_pause, "FILE", tmp_path / "pause.json")


def test_off_does_nothing_and_dry_run_reports_without_sending(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "off")
    assert rt.Router().once(T, force_session=True) == [] and not (tmp_path / "orders").exists()
    _setup(tmp_path, monkeypatch, "dry_run")
    r = rt.Router()
    out = r.once(T, force_session=True)
    assert out and "[DRY RUN]" in out[0] and "BUY 65 NSE:NIFTY2611725150PE" in out[0]
    assert r.once(T + pd.Timedelta(seconds=5), force_session=True)[0].startswith("[DRY RUN] trade 1: BUY filled")           # next pass: the pretend broker fills it
    assert (tmp_path / "orders" / "2026-11-16-dry_run.json").exists() and (tmp_path / "orders" / "2026-11-16.log").exists()
    assert r.once(T + pd.Timedelta(seconds=10), force_session=True) == []                                                    # nothing is repeated


def test_stale_wrong_day_or_missing_page_state_means_no_orders(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "dry_run", state_age=300)
    assert rt.Router().once(T, force_session=True) == []
    _setup(tmp_path, monkeypatch, "dry_run", date="2026-11-13")
    assert rt.Router().once(T, force_session=True) == []
    _setup(tmp_path, monkeypatch, "dry_run")
    (tmp_path / "state.json").unlink()
    assert rt.Router().once(T, force_session=True) == []


def test_live_without_a_valid_order_token_sends_nothing_and_says_so_once(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "live")
    told = []
    monkeypatch.setattr(rt, "tell", told.append)
    r = rt.Router()
    assert r.once(T, force_session=True) == [] and len(told) == 1 and "no valid order token" in told[0]
    assert r.once(T, force_session=True) == [] and len(told) == 1                                                            # said once, not every 5 seconds
    (tmp_path / "token.json").write_text(json.dumps({"app_id": "A", "access_token": "tok", "expires": "2026-11-16T06:30:00+05:30"}), encoding="utf-8")
    assert rt.order_token(T) is None                                                                                         # an expired token is no token
    (tmp_path / "token.json").write_text(json.dumps({"app_id": "A", "access_token": "tok", "expires": "2026-11-17T06:30:00+05:30"}), encoding="utf-8")
    assert rt.order_token(T) == ("A", "tok")
