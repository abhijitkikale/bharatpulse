import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
import nse_calendar  # noqa: E402
import tv_health  # noqa: E402
import tv_live_paste as tv  # noqa: E402

IST = "Asia/Kolkata"
NOW = pd.Timestamp("2026-11-17 07:32:00", tz=IST)


class FakePage:
    """A TradingView page as the check sees it: the alert service answers by path."""
    def __init__(self, alerts="ok", fires="ok", n_bp=1, url="https://www.tradingview.com/"):
        self.url, self.alerts, self.fires, self.n_bp = url, alerts, fires, n_bp
        self.visited = []

    def goto(self, url, **k):
        self.visited.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, args):
        path = args[0]
        if path == "list_alerts":
            if self.alerts != "ok":
                return {"s": "error", "errmsg": "not_authorized"}
            return {"s": "ok", "r": [{"name": "BP Live (2, 0.25)", "active": True} for _ in range(self.n_bp)] + [{"name": "price alert", "active": True}]}
        if path == "list_fires":
            return {"s": "ok", "r": [{"fire_time": "2026-11-16T09:44:02Z"}]} if self.fires == "ok" else {"s": "error", "errmsg": "no_such_endpoint"}
        raise AssertionError(path)


def _by_key(lines):
    return {l["key"]: l for l in lines}


def test_the_morning_check_reports_each_part_and_never_opens_the_chart():
    page = FakePage()
    lines = tv.preflight(page, NOW, eod_asof=lambda: "2026-11-16")
    k = _by_key(lines)
    assert [l["key"] for l in lines] == ["signin", "alert", "alert_log", "eod"] and all(k[x]["ok"] for x in ("signin", "alert", "alert_log", "eod"))
    assert page.visited == [] and tv_health.overall(lines)                                          # already on a TradingView page: no navigation at all
    off = FakePage(url="about:blank")
    tv.preflight(off, NOW, eod_asof=lambda: "2026-11-16")
    assert off.visited == [tv.HOME]                                                                  # the home page, never the chart (it would take your chart session)


def test_each_failure_is_a_clear_problem_with_a_fix():
    k = _by_key(tv.preflight(FakePage(alerts="no"), NOW, eod_asof=lambda: "2026-11-16"))
    assert k["signin"]["ok"] is False and "refused" in k["signin"]["text"] and "alert" not in k
    k = _by_key(tv.preflight(FakePage(fires="no"), NOW, eod_asof=lambda: "2026-11-16"))
    assert k["alert_log"]["ok"] is False and k["signin"]["ok"] is True
    k = _by_key(tv.preflight(FakePage(), NOW, eod_asof=lambda: "2026-11-13"))                        # the evening build did not run: Friday's levels on a Tuesday
    assert k["eod"]["ok"] is False and "2026-11-16" in k["eod"]["text"]
    k = _by_key(tv.preflight(FakePage(), NOW, eod_asof=lambda: None))
    assert k["eod"]["ok"] is False
    k = _by_key(tv.preflight(FakePage(n_bp=0), NOW, eod_asof=lambda: "2026-11-16"))
    assert k["alert"]["ok"] is None and "no BP Live alert" in k["alert"]["text"]                       # information only, not a problem
    k = _by_key(tv.preflight(FakePage(n_bp=3), NOW, eod_asof=lambda: "2026-11-16"))
    assert k["alert"]["ok"] is None and "3 BP Live alerts" in k["alert"]["text"]
    assert tv_health.last_trading_day(pd.Timestamp("2026-11-16").date(), nse_calendar.sessions).isoformat() == "2026-11-13"       # Monday: the Friday before


def test_status_file_message_updates_and_age(tmp_path):
    f = tmp_path / "tv.json"
    good = tv.preflight(FakePage(), NOW, eod_asof=lambda: "2026-11-16")
    st = tv_health.write(good, NOW, "sign-in check", f)
    assert st["ok"] and "ALL OK" in tv_health.format_message(st, "TradingView check 07:32 IST").splitlines()[0]
    assert tv_health.read(f, now=NOW + pd.Timedelta(seconds=90))["age_s"] == 90
    st = tv_health.update("renewal", False, "today's BP Live alert could not be made: boom", NOW + pd.Timedelta(minutes=100), f)
    assert st["ok"] is False and any(l["key"] == "renewal" for l in st["lines"])
    msg = tv_health.format_message(st, "TradingView check")
    assert msg.splitlines()[0].endswith("PROBLEM, fix before you leave") and "✗ today's BP Live alert could not be made" in msg and "→ Today's BP Live alert has not been made" in msg
    st = tv_health.update("renewal", True, "today's BP Live alert was made at 09:11 IST", NOW + pd.Timedelta(minutes=110), f)
    assert st["ok"] is True and sum(1 for l in st["lines"] if l["key"] == "renewal") == 1                      # a line is replaced, not repeated
    bad = tv_health.write(tv.preflight(FakePage(alerts="no"), NOW, eod_asof=lambda: "2026-11-16"), NOW, "x", f)
    assert "→ The hidden browser is signed OUT of TradingView" in tv_health.format_message(bad, "t") and tv_health.read(tmp_path / "none.json") == {}


def test_the_days_fallback_to_the_pages_own_setups(tmp_path):
    o = tmp_path / "src.json"
    assert tv_health.source_override("2026-11-17", o) is None
    tv_health.set_override("2026-11-17", "fyers", "feed not usable", o)
    assert tv_health.source_override("2026-11-17", o) == "fyers"
    assert tv_health.source_override("2026-11-18", o) is None                                          # it never carries over to the next day
    assert tv_health.REQUIRED == ("signin", "alert_log", "eod") and set(tv_health.REQUIRED) <= set(tv_health.HINTS)
