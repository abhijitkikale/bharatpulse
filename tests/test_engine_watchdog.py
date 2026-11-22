import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pipeline"))
import refresh_app as R  # noqa: E402

IST = "Asia/Kolkata"


def test_the_engine_is_started_in_paper_on_a_trading_morning_and_a_later_restart_is_reported(monkeypatch):
    monkeypatch.setattr(R.obs_control, "_trading_day", lambda t: t.weekday() < 5)
    starts, said, st = [], [], {}
    run = lambda running: R.engine_watch_step(pd.Timestamp("2026-11-23 06:00", tz=IST), st, lambda: running, lambda: starts.append(1), said.append)      # Monday
    assert run(False) == "started" and len(starts) == 1 and said == []                         # the morning start: silent
    assert run(True) is None and len(starts) == 1                                              # running: nothing to do
    assert run(False) == "restarted" and len(starts) == 2 and "PAPER" in said[0] and "LIVE" in said[0]       # stopped again: restarted and Telegram told
    sat = lambda: R.engine_watch_step(pd.Timestamp("2026-11-21 10:00", tz=IST), {}, lambda: False, lambda: starts.append(1), said.append)
    assert sat() is None and len(starts) == 2                                                  # weekend: left alone
    late = R.engine_watch_step(pd.Timestamp("2026-11-23 16:30", tz=IST), {}, lambda: False, lambda: starts.append(1), said.append)
    assert late is None and len(starts) == 2                                                   # after the close: left alone
