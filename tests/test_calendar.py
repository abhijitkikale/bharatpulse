import datetime as dt
import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
import nse_calendar  # noqa: E402

MASTER = {"CM": [
    {"tradingDate": "20-Oct-2026", "description": "Dussehra", "morning_session": None, "evening_session": None},
    {"tradingDate": "08-Nov-2026", "description": "Diwali Laxmi Pujan*", "morning_session": None, "evening_session": None},
    {"tradingDate": "10-Nov-2026", "description": "Diwali-Balipratipada", "morning_session": None, "evening_session": None},
    {"tradingDate": "01-Nov-2027", "description": "Diwali Laxmi Pujan*", "morning_session": None, "evening_session": "06:15 PM - 07:15 PM"}]}


def test_parse_master_holidays_pending_and_announced_special_sessions():
    cal = nse_calendar.parse_master(MASTER)
    assert cal["holidays"]["2026-10-20"] == "Dussehra" and cal["holidays"]["2026-11-10"] == "Diwali-Balipratipada"
    assert "2026-11-08" in cal["pending"] and "2026-11-08" not in cal["special"]               # timing not announced yet
    assert cal["special"]["2027-11-01"] == {"open": "18:15", "close": "19:15", "name": "Diwali Laxmi Pujan"}


def test_sessions_follow_holidays_weekends_and_special_days():
    cal = {**nse_calendar.parse_master(MASTER), "special": {"2026-11-08": {"open": "18:15", "close": "19:15", "name": "Muhurat trading"}}}
    d = dt.date.fromisoformat
    assert nse_calendar.sessions(d("2026-11-09"), cal) == [((9, 15), (15, 30))]              # an ordinary Monday
    assert nse_calendar.sessions(d("2026-11-10"), cal) == [] and nse_calendar.sessions(d("2026-11-07"), cal) == []     # holiday, Saturday
    assert nse_calendar.sessions(d("2026-11-08"), cal) == [((18, 15), (19, 15))]            # a Sunday with a Muhurat session
    t = lambda s: pd.Timestamp(s, tz="Asia/Kolkata")
    assert nse_calendar.in_session(t("2026-11-09 09:10"), cal=cal) and not nse_calendar.in_session(t("2026-11-09 09:05"), cal=cal)
    assert nse_calendar.in_session(t("2026-11-09 15:39"), cal=cal) and not nse_calendar.in_session(t("2026-11-09 15:41"), cal=cal)
    assert nse_calendar.in_session(t("2026-11-08 18:20"), cal=cal) and not nse_calendar.in_session(t("2026-11-08 10:00"), cal=cal)
    assert not nse_calendar.in_session(t("2026-11-10 10:00"), cal=cal)


def test_weekday_rule_when_there_is_no_calendar():
    empty = {"holidays": {}, "special": {}, "pending": {}}
    assert nse_calendar.is_trading_day(dt.date(2026, 11, 9), empty) and not nse_calendar.is_trading_day(dt.date(2026, 11, 8), empty)


def test_saved_end_of_day_levels_are_replayed_after_the_evening_build(tmp_path, monkeypatch):
    import stream_server
    monkeypatch.setattr(stream_server, "STORE", tmp_path)
    for close in ("2026-11-04", "2026-11-05", "2026-11-06"):
        (tmp_path / (close + ".json")).write_text(json.dumps({"close": close, "levels": {"POC": 1.0}}), encoding="utf-8")
    assert stream_server.eod_for(dt.date(2026, 11, 6))["close"] == "2026-11-05"     # during Friday: Thursday's close levels
    assert stream_server.eod_for(dt.date(2026, 11, 9))["close"] == "2026-11-06"     # Monday morning: Friday's
    assert stream_server.eod_for(dt.date(2026, 11, 4)) is None
    stream_server.save_eod({"close": "2026-11-09", "levels": {}})
    assert (tmp_path / "2026-11-09.json").exists()


def test_option_candles_fall_back_to_the_journal(tmp_path, monkeypatch):
    import stream_server
    monkeypatch.setattr(stream_server, "JOURNAL", tmp_path)
    doc = {"trades": [{"strike": 24500, "side": "CALL", "option_candles_hm_ohlcv": [["09:26", 150, 152, 149, 151, 10], ["09:27", 151, 160, 150, 158, 12]]},
                      {"strike": 24700, "side": "PUT", "option_candles_hm_ohlcv": [["12:50", 160, 165, 159, 164, 9]]}]}
    (tmp_path / "2026-11-06.json").write_text(json.dumps(doc), encoding="utf-8")
    df = stream_server.journal_frame(dt.date(2026, 11, 6), (24500.0, "CE"))
    assert list(df.index.strftime("%H:%M")) == ["09:26", "09:27"] and df["c"].iloc[-1] == 158 and df.index.tz is not None
    assert stream_server.journal_frame(dt.date(2026, 11, 6), (24500.0, "PE")) is None           # no such contract in the journal
    assert stream_server.journal_frame(dt.date(2026, 11, 9), (24500.0, "CE")) is None           # no journal for that day
