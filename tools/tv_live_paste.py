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


def run_once(page, c, text=None, eod=None):
    try:
        paste(page, c, text, eod)
    except Exception as e:
        log("paste failed, reloading the chart and trying once more:", str(e)[:160])
        open_chart(page, c)
        paste(page, c, text, eod)


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
    a = ap.parse_args()
    if a.login:
        return login()
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
            while True:
                t = live_loop.now_ist()
                hm = (t.hour, t.minute)
                if t.weekday() >= 5 or hm >= END:
                    log("done for today")
                    return 0
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
                            done = txt                              # do not hammer the same text; the next 5-minute text gets a fresh try
                time.sleep(20)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main() or 0)
