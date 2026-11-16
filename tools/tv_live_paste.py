"""Pastes the newest live GEX text into the 'Live levels' box of the BP Live indicator on a TradingView chart, by itself.

The 5-minute live loop (tools/live_loop.py) writes data/live_levels_NIFTY.txt after every update. This script keeps its own small
browser (a separate profile, data/tv_profile, so your normal Chrome is untouched), opens your chart, and whenever that file changes it
opens the indicator's settings, replaces the 'Live levels' text and presses OK.

  python tools/tv_live_paste.py --login   one time: a window opens, sign in to TradingView, then close it
  python tools/tv_live_paste.py --once    paste the current text now (a test)
  python tools/tv_live_paste.py           the morning mode: runs 09:10 - 15:40 IST on weekdays, pasting every time the text changes

Normally 'Refresh BharatPulse.bat' starts the last one for you after your Fyers sign-in, once --login has been done.
The chart link is in data/tv_chart.json ({"url": "...", "indicator": "BP Live", "symbol": "NIFTY"}); results go to data/tv_paste_log.txt."""
import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
PROFILE = ROOT / "data" / "tv_profile"
CFG = ROOT / "data" / "tv_chart.json"
LOG = ROOT / "data" / "tv_paste_log.txt"
DEFAULT = {"url": "https://in.tradingview.com/chart/yKs7UQDu/", "indicator": "BP Live", "symbol": "NIFTY"}
START, END = (9, 10), (15, 40)
DASH = "https://abhijitkikale.github.io/bharatpulse/"
EOD_FILE = ROOT / "data" / "tv_eod_pasted.txt"       # the end-of-day text last pasted, so it is pasted once a day, not every cycle


def cfg():
    c = dict(DEFAULT)
    try:
        c.update(json.loads(CFG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return c


def log(*a):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def text_file(c):
    return ROOT / "data" / f"live_levels_{c['symbol']}.txt"


def open_browser(p, headless):
    PROFILE.mkdir(parents=True, exist_ok=True)
    try:                                                          # Google Chrome only (never Edge): its own profile folder, your normal Chrome is untouched
        return p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=headless, viewport={"width": 1500, "height": 900})
    except Exception as e:
        raise RuntimeError("Google Chrome could not be started: " + str(e)[:150])


def open_chart(page, c):
    page.goto(c["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector('[data-qa-id="legend-source-item"]', timeout=60000)
    page.wait_for_timeout(3000)                                   # let the indicators finish loading


def paste(page, c, text=None, eod=None):
    """Replace the 'Live levels' box (text) and / or the end-of-day 'Levels' box (eod) of the indicator and press OK.
    Raises if anything is missing or the text did not stick."""
    if page.get_by_text("Session disconnected").count():           # TradingView allows one live session per user: another browser took it
        raise RuntimeError("TradingView session was taken by another browser (one live session per user); not reconnecting by itself")
    item = page.locator('[data-qa-id="legend-source-item"]', has_text=c["indicator"]).first
    item.wait_for(timeout=20000)
    item.hover()
    item.locator('[data-qa-id="legend-settings-action"]').click(force=True, timeout=10000)
    dlg = page.locator('[data-name="indicator-properties-dialog"]')
    dlg.wait_for(timeout=15000)
    for idx, val in ((0, eod), (1, text)):                         # 0 = Levels (end of day), 1 = Live levels
        if val is None:
            continue
        box = dlg.locator("textarea").nth(idx)
        box.fill(val)
        if box.input_value().replace("\r", "") != val:
            dlg.locator('button[name="cancel"]').click()
            raise RuntimeError("the text did not stay in the box")
    dlg.locator('button[name="submit"]').click()
    dlg.wait_for(state="hidden", timeout=15000)
    if c.get("save", True):                                        # save the layout so it carries the newest text (use a layout of its own for this)
        page.wait_for_timeout(1500)
        page.keyboard.press("Control+s")
        page.wait_for_timeout(2500)


def poll_fires(page, seen):
    """Read TradingView's own alert log (the list the Alerts panel shows) and hand every NEW BP Live message to the stream page through data/tv_inbox.jsonl.
    The relay (ntfy.sh) answers 429 to many of the alert's webhook messages; the log keeps all of them. `seen`: a set of fire ids already handed over.
    Returns how many messages were handed over. Works from any logged-in TradingView page, even when the chart session is taken by another browser."""
    import tv_inbox
    j = _alerts_api(page, "list_fires", {"payload": {"limit": 80}})
    rows = j.get("r") or []
    new = []
    for r in sorted(rows, key=lambda x: x.get("fire_time", "")):
        fid = str(r.get("fire_id"))
        if fid in seen:
            continue
        seen.add(fid)
        if str(r.get("name", "")).startswith("BP Live") and r.get("message"):
            new.append({"fire_id": fid, "bar_time": r.get("bar_time"), "message": r["message"], "http": (r.get("webhook") or {}).get("http_code")})
    return tv_inbox.append(new)


ALERT_DAY = ROOT / "data" / "tv_alert_day.txt"       # the day the BP Live alert was last made fresh, and its id


def _alerts_api(page, path, body=None):
    """Call TradingView's alert service from the open chart page (it uses your signed-in session). Returns the parsed JSON."""
    js = """async ([path, body]) => {
        const r = await fetch('https://pricealerts.tradingview.com/' + path, {method: body ? 'POST' : 'GET', credentials: 'include',
                              headers: body ? {'Content-Type': 'text/plain;charset=UTF-8'} : {}, body: body ? JSON.stringify(body) : undefined});
        return await r.json();
    }"""
    return page.evaluate(js, [path, body])


def _bp_alert_ids(page):
    r = _alerts_api(page, "list_alerts")
    rows = r.get("r") or r.get("result") or r.get("alerts") or []
    out = []
    for a in rows:
        name = str(a.get("name") or "")
        if name.startswith("BP Live") and a.get("active", True) and a.get("resolution") == "1":
            out.append(int(a.get("alert_id")))
    return out


def renew_alert(page, c):
    """Make ONE fresh BP Live alert. A TradingView alert keeps the level text the indicator had when it was saved (the later 5-minute pastes never
    reach it) and the indicator draws and sends setups only for the trading day after the close date in that text. So each morning, once the
    end-of-day and live text are pasted, a new alert is created from this chart (it copies today's text) and the older BP Live alerts are deleted."""
    before = _bp_alert_ids(page)
    page.keyboard.press("Escape")
    page.keyboard.press("Alt+a")
    dlg_title = page.get_by_text("Create alert on")
    dlg_title.wait_for(timeout=15000)
    page.get_by_text("Price", exact=True).first.click()
    page.locator("[role=option]", has_text=c["indicator"]).first.click()
    page.wait_for_timeout(1500)
    if not page.get_by_text("Any alert() function call").count():
        raise RuntimeError("the alert dialog did not offer 'Any alert() function call'")
    page.locator('button[data-qa-id="submit"]').click()
    page.wait_for_timeout(4000)
    after = _bp_alert_ids(page)
    new = [i for i in after if i not in before]
    if len(new) != 1:
        raise RuntimeError(f"expected one new alert, found {len(new)} (before {before}, after {after})")
    if before:
        r = _alerts_api(page, "delete_alerts", {"payload": {"alert_ids": before}})
        page.wait_for_timeout(1500)
        left = _bp_alert_ids(page)
        if left != new:
            raise RuntimeError(f"old alerts were not all removed: {left} (delete answered {str(r)[:120]})")
    ALERT_DAY.write_text(f"{time.strftime('%Y-%m-%d')} {new[0]}", encoding="utf-8")
    return new[0], before


def run_once(page, c, text=None, eod=None):
    try:
        paste(page, c, text, eod)
    except Exception as e:
        log("paste failed, reloading the chart and trying once more:", str(e)[:160])
        open_chart(page, c)
        paste(page, c, text, eod)


HOME = "https://www.tradingview.com/"


def _eod_asof():
    """The close date of the published dashboard (its 'asof'), read from the page itself."""
    import re
    import requests
    txt = requests.get(DASH, timeout=90).text
    m = re.search(r'"asof":"(\d{4}-\d{2}-\d{2})"', txt)
    return m.group(1) if m else None


def preflight(page, now, eod_asof=None):
    """The morning health check of the TradingView connection, run from the hidden browser right after your sign-in (and again before the open). It uses
    TradingView's home page, not the chart, so it never takes the chart session from your own TradingView. Returns the status lines (see pipeline/tv_health.py)."""
    import nse_calendar
    import tv_health
    lines = []
    try:
        if "tradingview.com" not in (page.url or ""):
            page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
    except Exception as e:
        lines.append({"key": "signin", "ok": False, "text": "the hidden browser could not open TradingView (" + str(e)[:80] + ")"})
        return lines
    rows, err = None, ""
    try:
        j = _alerts_api(page, "list_alerts")
        if j.get("s") == "ok":
            rows = j.get("r") or []
        else:
            err = str(j.get("errmsg") or j.get("s") or "no answer")[:90]
    except Exception as e:
        err = str(e)[:90]
    if rows is None:
        lines.append({"key": "signin", "ok": False, "text": "TradingView refused the hidden browser: " + err})
    else:
        lines.append({"key": "signin", "ok": True, "text": "the hidden browser is signed in to TradingView"})
        bp = [a for a in rows if str(a.get("name") or "").startswith("BP Live") and a.get("active", True)]
        lines.append({"key": "alert", "ok": True if len(bp) == 1 else None,
                      "text": ("yesterday's BP Live alert is in place (it is replaced by today's at about 09:11 IST)" if len(bp) == 1 else
                               "no BP Live alert exists yet (today's is made at about 09:11 IST)" if not bp else
                               f"{len(bp)} BP Live alerts exist (the 09:11 renewal removes the extras)")})
    try:
        j = _alerts_api(page, "list_fires", {"payload": {"limit": 5}})
        if j.get("s") == "ok":
            r = j.get("r") or []
            lines.append({"key": "alert_log", "ok": True, "text": "TradingView's alert log is readable" + (f" (newest entry {r[0].get('fire_time', '')[:16]} UTC)" if r else "")})
        else:
            lines.append({"key": "alert_log", "ok": False, "text": "TradingView's alert log did not answer: " + str(j.get("errmsg") or j.get("s"))[:90]})
    except Exception as e:
        lines.append({"key": "alert_log", "ok": False, "text": "TradingView's alert log could not be read: " + str(e)[:90]})
    try:
        asof = (eod_asof or _eod_asof)()
        expected = tv_health.last_trading_day(now.date(), nse_calendar.sessions)
        if asof and expected and asof >= str(expected):
            lines.append({"key": "eod", "ok": True, "text": f"the published end-of-day levels are from the {asof} close (the latest)"})
        elif asof:
            lines.append({"key": "eod", "ok": False, "text": f"the published end-of-day levels are from the {asof} close, but the latest trading day is {expected}"})
        else:
            lines.append({"key": "eod", "ok": False, "text": "the published dashboard's close date could not be read"})
    except Exception as e:
        lines.append({"key": "eod", "ok": False, "text": "the end-of-day levels could not be checked: " + str(e)[:90]})
    return lines


def tell(text):
    """A Telegram message to your chat (never raises)."""
    try:
        import telegram_notify
        telegram_notify.send(text)
    except Exception:
        pass


def run_check(page, title, live_loop, send=True):
    """One health check: run it, save it, tell Telegram (unless send is False). Returns the saved status."""
    import tv_health
    now = live_loop.now_ist()
    st = tv_health.write(preflight(page, now), now, phase=title)
    log("TradingView check:", "ALL OK" if st["ok"] else "PROBLEM: " + "; ".join(l["text"] for l in st["lines"] if l["ok"] is False))
    if send:
        tell(tv_health.format_message(st, f"TradingView check {now:%H:%M} IST") + ("" if not st["ok"] else
             "\nNext: about 09:11 IST the levels are pasted and today's alert is made; a final check follows at 09:05 and the READY message at 09:31."))
    return st


def fetch_eod(p):
    """The end-of-day text the dashboard's Options tab gives for Nifty (the same text you used to copy by hand each evening), read from the
    published dashboard in a plain browser. The live file is blocked so the text is the close, never today's developing profile."""
    b = p.chromium.launch(channel="chrome", headless=True)
    try:
        pg = b.new_page(viewport={"width": 1500, "height": 900})
        pg.route("**/live-data/live.json*", lambda r: r.abort())
        pg.goto(DASH, wait_until="networkidle", timeout=90000)
        pg.click('[data-tab="options"]')
        pg.wait_for_function("document.querySelector('#op-tv-text') && document.querySelector('#op-tv-text').value.length > 50", timeout=30000)
        t = pg.eval_on_selector("#op-tv-text", "e => e.value").replace(chr(13), "").strip()
        if not t.startswith("# BharatPulse") or " close " not in t.splitlines()[0] or "LIVE profile" in t.splitlines()[0]:
            raise RuntimeError("unexpected end-of-day text: " + t[:80])
        return t
    finally:
        b.close()


def login():
    from playwright.sync_api import sync_playwright
    c = cfg()
    with sync_playwright() as p:
        ctx = open_browser(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(c["url"], wait_until="commit")
        except Exception as e:
            print("(could not open the chart link yet:", str(e)[:80], ")")
        print("Sign in to TradingView in the window that opened (use the person icon, top right).\nWhen your chart shows, CLOSE the window.")
        try:                                                        # done as soon as the chart itself is on screen (after sign-in and 2-step code)
            while ctx.pages:                                        # a sign-in popup or a redirect may replace the first tab: watch all of them
                time.sleep(2)
                hit = False
                for pg in list(ctx.pages):
                    try:
                        if pg.locator('[data-qa-id="legend-source-item"]').count():
                            hit = True
                    except Exception:
                        pass
                try:
                    signed = any(x["name"] == "sessionid" for x in ctx.cookies())
                except Exception:
                    signed = False
                if hit and signed:
                    time.sleep(5)
                    print("Signed in: chart is showing.")
                    break
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
    print("Saved. Test it with:  python tools/tv_live_paste.py --once")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--show", action="store_true", help="show the browser window instead of running hidden")
    ap.add_argument("--alert", action="store_true", help="make one fresh BP Live alert from the chart now and delete the older BP Live alerts")
    a = ap.parse_args()
    if a.login:
        return login()
    if a.alert:
        from playwright.sync_api import sync_playwright
        c = cfg()
        with sync_playwright() as p:
            ctx = open_browser(p, headless=not a.show)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                open_chart(page, c)
                new, old = renew_alert(page, c)
                log("fresh BP Live alert", new, "(replaced", old, ")")
            finally:
                ctx.close()
        return 0
    import live_loop
    from playwright.sync_api import sync_playwright
    c = cfg()
    f = text_file(c)
    with sync_playwright() as p:
        ctx = open_browser(p, headless=not a.show)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        done = ""
        try:
            eod_last = EOD_FILE.read_text(encoding="utf-8").strip() if EOD_FILE.exists() else ""
            if a.once:
                eod = None
                try:
                    e = fetch_eod(p)
                    eod = e
                except Exception as ex:
                    log("end-of-day text not available:", str(ex)[:160])
                if not f.exists() and eod is None:
                    log("nothing to paste:", f)
                    return 1
                open_chart(page, c)
                run_once(page, c, f.read_text(encoding="utf-8").strip() if f.exists() else None, eod)
                if eod:
                    EOD_FILE.write_text(eod, encoding="utf-8")
                log("pasted once ok" + (" (with the end-of-day levels: " + eod.splitlines()[0][:60] + ")" if eod else ""))
                return 0
            eod_checked = False
            eod_retry = 0.0
            alert_retry = 0.0
            fires_seen, fires_err = set(), 0
            import tv_health
            first_t = live_loop.now_ist()
            if first_t.weekday() < 5 and (first_t.hour, first_t.minute) < END:
                pre = {"st": run_check(page, "sign-in check", live_loop), "next": time.time() + 600, "told": time.time(), "final": False, "renew_fail": 0, "session_told": False}
            else:
                pre = {"st": {"ok": True}, "next": 1e18, "told": 0, "final": True, "renew_fail": 0, "session_told": False}
            while True:
                t = live_loop.now_ist()
                hm = (t.hour, t.minute)
                if t.weekday() >= 5 or hm >= END:
                    log("done for today")
                    return 0
                if hm < START and time.time() >= pre["next"]:               # before the open: re-check while there is a problem, quietly otherwise; a final check at 09:05
                    was_ok = pre["st"].get("ok", True)
                    final = hm >= (9, 5) and not pre["final"]
                    quiet = was_ok and not final
                    st = run_check(page, "final check" if final else "re-check", live_loop, send=False)
                    pre["next"] = time.time() + (1800 if st["ok"] else 600)
                    if final:
                        pre["final"] = True
                        if st["ok"]:
                            tell(tv_health.format_message(st, f"TradingView final check {t:%H:%M} IST") + "\nThe open is in a few minutes.")
                        else:
                            tv_health.set_override(t.date(), "fyers", "TradingView feed not usable at the final check")
                            tell(tv_health.format_message(st, f"TradingView final check {t:%H:%M} IST") +
                                 "\nFallback: today the page books trades from its OWN setups (the TradingView feed is switched off for today).")
                    elif st["ok"] and not was_ok:
                        tell(tv_health.format_message(st, f"TradingView re-check {t:%H:%M} IST"))
                    elif not st["ok"] and time.time() - pre["told"] > 1700:
                        pre["told"] = time.time()
                        tell(tv_health.format_message(st, f"TradingView re-check {t:%H:%M} IST"))
                    pre["st"] = st
                if hm >= START:
                    eod = None
                    if not eod_checked and time.time() >= eod_retry:        # the end-of-day levels: once a day, as the first thing
                        try:
                            e = fetch_eod(p)
                            eod_checked = True
                            if e != eod_last:
                                eod = e
                            else:
                                log("end-of-day levels unchanged since the last paste:", e.splitlines()[0][:60])
                        except Exception as ex:
                            eod_retry = time.time() + 600
                            log("end-of-day text not available, trying again in 10 minutes:", str(ex)[:160])
                    txt = f.read_text(encoding="utf-8").strip() if f.exists() else ""
                    if (txt and txt != done) or eod:
                        try:
                            if "tradingview.com/chart" not in page.url or not page.locator('[data-qa-id="legend-source-item"]').count():
                                open_chart(page, c)
                            run_once(page, c, txt if (txt and txt != done) else None, eod)
                            if eod:
                                eod_last = eod
                                EOD_FILE.write_text(eod, encoding="utf-8")
                                log("pasted the end-of-day levels:", eod.splitlines()[0][:70])
                            if txt and txt != done:
                                done = txt
                                log("pasted", txt.splitlines()[0][:60])
                        except Exception as ex:
                            log("paste FAILED:", str(ex)[:200])
                            if "session was taken" in str(ex):
                                tv_health.update("session", False, "another TradingView session took the hidden browser's chart session (live-level pasting is paused; the alert keeps working)", t)
                                if not pre["session_told"]:
                                    pre["session_told"] = True
                                    tell("TradingView: another session of yours took the hidden browser's chart session at " + t.strftime("%H:%M") + " IST. The alert and the setup feed keep working; only the "
                                         "live-level pasting into the hidden chart is paused. To restore it, close TradingView on your other devices.")
                            done = txt                              # do not hammer the same text; the next 5-minute text gets a fresh try
                    # once a day, after today's end-of-day text is on the chart: a fresh alert, so its text is today's (see renew_alert)
                    today_s = t.strftime("%Y-%m-%d")
                    renewed = ALERT_DAY.exists() and ALERT_DAY.read_text(encoding="utf-8").startswith(today_s)
                    if eod_checked and not renewed and time.time() >= alert_retry:
                        try:
                            if "tradingview.com/chart" not in page.url or not page.locator('[data-qa-id="legend-source-item"]').count():
                                open_chart(page, c)
                            new, old = renew_alert(page, c)
                            log("fresh BP Live alert", new, "(replaced", old, ")")
                            tv_health.update("renewal", True, f"today's BP Live alert was made at {t:%H:%M} IST", t)
                            try:
                                import telegram_notify
                                telegram_notify.send(f"TradingView alert renewed for today (alert {new}). The BP Live alert now carries today's levels.")
                            except Exception:
                                pass
                        except Exception as ex:
                            alert_retry = time.time() + 120
                            log("alert renewal FAILED, trying again in 2 minutes:", str(ex)[:200])
                            pre["renew_fail"] += 1
                            tv_health.update("renewal", False, "today's BP Live alert could not be made: " + str(ex)[:100], t)
                            if pre["renew_fail"] == 3:
                                tell(f"TradingView: today's BP Live alert could NOT be made ({str(ex)[:100]}); three tries failed by {t:%H:%M} IST, it keeps trying every 2 minutes. "
                                     "Until it is made TradingView sends no setups. " + tv_health.HINTS["renewal"])
                            try:
                                page.keyboard.press("Escape")
                            except Exception:
                                pass
                if hm >= START:                                              # TradingView's own alert log -> the stream page (no relay, no 429)
                    try:
                        n = poll_fires(page, fires_seen)
                        if n:
                            log("alert log:", n, "new message(s) handed to the stream page")
                        fires_err = 0
                        if n or t.second < 20:                                           # a heartbeat for the page: written about once a minute and whenever a message arrives
                            tv_health.update("feed", True, f"the alert-log reader is running (last read {t:%H:%M:%S} IST)", t)
                    except Exception as ex:
                        fires_err += 1
                        if fires_err in (1, 10, 100):
                            log("alert log read failed:", str(ex)[:160])
                time.sleep(20)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main() or 0)
