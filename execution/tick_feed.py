"""FYERS live price feed (market-data WebSocket, official SDK class fyers_apiv3.FyersWebsocket.data_ws.FyersDataSocket) for the option contracts the engine cares about.

The dashboard learns option prices from 1-minute candles it asks for every 15 seconds, which cost up to 1-2 minutes of delay on 26 Nov. This feed pushes every price change, so the engine sees the
last traded price of the contract it HOLDS within a second. It needs the market-data token (data/fyers_token.json, the data-only app), not the order token. Like the order feed it is an
ACCELERATOR, never a dependency: with the socket down, silent or the SDK missing, price() answers None and the engine falls back to the broker's positions (their last price is read every
pass) and then to the dashboard's own status.

Only the symbols asked for with watch() are subscribed (at most a handful). Subscribing is done in a small thread because the SDK sleeps 0.5 s inside it."""
import threading
import time

from . import config


class TickFeed:
    def __init__(self, app_id, token, state=None, log=print, socket_factory=None, clock=None):
        """state: a dict this feed keeps current ({"connected": bool, "last": epoch of the last price, "error": text})."""
        self.app_id, self.token, self.log = app_id, token, log
        self.state = state if state is not None else {}
        self.state.update(connected=False, last=0.0, error="")
        self.factory = socket_factory
        self.clock = clock or time.time
        self.sock = None
        self.thread = None
        self.symbols = []                      # everything asked for (resubscribed after every reconnect)
        self.last = {}                         # symbol -> (price, received at)
        self.lock = threading.Lock()

    # ---- SDK callbacks ----
    def _on_message(self, msg):
        try:
            if not isinstance(msg, dict):
                return
            sym, ltp = msg.get("symbol"), msg.get("ltp")
            if sym is None or ltp is None:
                return
            ltp = float(ltp)
            if ltp > 0:
                with self.lock:
                    self.last[sym] = (ltp, self.clock())
                self.state["last"] = self.clock()
        except Exception:
            pass

    def _on_open(self):
        self.state.update(connected=True, error="")
        self._subscribe(list(self.symbols))

    def _on_close(self, msg=None):
        self.state["connected"] = False

    def _on_error(self, msg=None):
        text = str(msg)[:160]
        self.state["error"] = text
        if "token" in text.lower() or "auth" in text.lower():
            self.state["connected"] = False

    # ---- control ----
    def start(self):
        try:
            factory = self.factory
            if factory is None:
                from fyers_apiv3.FyersWebsocket import data_ws
                factory = data_ws.FyersDataSocket
            config.LOGS.mkdir(parents=True, exist_ok=True)
            self.sock = factory(access_token=f"{self.app_id}:{self.token}", log_path=str(config.LOGS), litemode=True, write_to_file=False, reconnect=True,
                                on_connect=self._on_open, on_close=self._on_close, on_error=self._on_error, on_message=self._on_message)
        except Exception as e:
            self.state.update(connected=False, error=f"tick feed not started: {type(e).__name__}: {str(e)[:100]}")
            return False
        self.thread = threading.Thread(target=self._run, daemon=True, name="tick-feed")
        self.thread.start()
        return True

    def _run(self):
        try:
            self.sock.connect()
        except Exception as e:
            self.state.update(connected=False, error=f"tick feed stopped: {type(e).__name__}: {str(e)[:100]}")

    def _subscribe(self, symbols):
        if not symbols or self.sock is None:
            return

        def go():
            try:
                self.sock.subscribe(symbols=symbols, data_type="SymbolUpdate")
            except Exception as e:
                self.state["error"] = f"subscribe failed: {str(e)[:100]}"
        threading.Thread(target=go, daemon=True, name="tick-subscribe").start()

    def watch(self, symbol):
        """Ask for the live price of a contract (harmless to repeat)."""
        if not symbol or symbol in self.symbols:
            return
        self.symbols.append(symbol)
        if self.state.get("connected"):
            self._subscribe([symbol])

    def price(self, symbol, max_age_s=10.0):
        """(last traded price, age in seconds) when the feed has a price for it that is not older than max_age_s, else None."""
        with self.lock:
            hit = self.last.get(symbol)
        if not hit:
            return None
        age = self.clock() - hit[1]
        return (hit[0], age) if age <= max_age_s else None

    def wait_price(self, symbol, timeout=1.5, max_age_s=10.0):
        """The price, waiting up to `timeout` seconds for the first one after watch(); None when there is none."""
        self.watch(symbol)
        end = time.monotonic() + timeout
        while True:
            p = self.price(symbol, max_age_s)
            if p is not None:
                return p[0]
            if time.monotonic() >= end:
                return None
            time.sleep(0.05)

    def stop(self):
        try:
            if self.sock is not None:
                self.sock.close_connection()
        except Exception:
            pass
        self.state["connected"] = False


def load_data_token(path=None):
    """(app_id, access_token) of the market-data app from data/fyers_token.json, or None. Never printed."""
    import json
    try:
        j = json.loads((path or config.ROOT / "data" / "fyers_token.json").read_text(encoding="utf-8"))
        app, tok = str(j.get("app_id") or ""), str(j.get("access_token") or "")
        return (app, tok) if app and tok else None
    except (OSError, ValueError):
        return None
