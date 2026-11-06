"""09:20 IST weekday check (run by the Windows task made by install_morning_task.bat): if the helper is not running or there is no valid Fyers
sign-in, send a warning to Telegram. Silent when everything is fine and on weekends."""
import pathlib
import socket
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import live_loop  # noqa: E402
import refresh_app  # noqa: E402
import telegram_notify  # noqa: E402


def listening(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def problems():
    tok = refresh_app.token_info()
    out = []
    if not listening(refresh_app.PORT):
        out.append("the helper is not running (start Refresh BharatPulse.bat)")
    elif not listening(8766):
        out.append("the stream page is not running (the Fyers sign-in has not been done)")
    if not tok.get("valid"):
        out.append("no valid Fyers sign-in (sign in after 06:30 IST)")
    elif tok.get("early"):
        out.append("the Fyers sign-in was done before 06:30 IST and has expired or is about to (sign in again)")
    return out


def main():
    import nse_calendar
    t = live_loop.now_ist()
    if not nse_calendar.is_trading_day(t.date()):          # weekends and NSE holidays: nothing to check
        return
    p = problems()
    if p:
        text = f"BharatPulse {t:%H:%M} IST: PROBLEM: " + "; ".join(p) + "."
        print(text, telegram_notify.send(text))
    else:
        print("all fine")


if __name__ == "__main__":
    main()
