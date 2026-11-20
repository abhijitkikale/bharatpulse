"""FYERS order WebSocket (wss://socket.fyers.in/trade/v3) through the official SDK class fyers_apiv3.FyersWebsocket.order_ws.FyersOrderSocket.

It subscribes to OnOrders, OnTrades and OnPositions and hands every order update to the engine (Engine.on_order_event) so fills and cancels are seen within a moment. The
socket is an ACCELERATOR, never a dependency: the engine confirms important facts through the REST orderbook, reconciles positions by REST every few seconds, and keeps
working when this socket is down, silent or the SDK is missing (the status page shows 'WebSocket: down'). The SDK reconnects by itself (reconnect=True)."""
import threading
import time

from . import config


class OrderFeed:
    def __init__(self, app_id, token, on_order, state, log=print, socket_factory=None):
        """state: a dict the feed keeps current ({"connected": bool, "last": epoch of the last message, "error": text})."""
        self.app_id, self.token, self.on_order, self.state, self.log = app_id, token, on_order, state, log
        self.factory = socket_factory
        self.sock = None
        self.thread = None

    def _cb_order(self, msg):
        self.state["last"] = time.time()
        if isinstance(msg, dict) and "orders" in msg:
            self.on_order(msg)

    def _cb_other(self, msg):
        self.state["last"] = time.time()

    def _cb_open(self):
        self.state.update(connected=True, error="")
        try:
            self.sock.subscribe(data_type="OnOrders,OnTrades,OnPositions")
        except Exception as e:
            self.state["error"] = f"subscribe failed: {e}"

    def _cb_close(self, msg=None):
        self.state["connected"] = False

    def _cb_error(self, msg=None):
        self.state.update(connected=False, error=str(msg)[:160])

    def start(self):
        try:
            factory = self.factory
            if factory is None:
                from fyers_apiv3.FyersWebsocket import order_ws
                factory = order_ws.FyersOrderSocket
            config.LOGS.mkdir(parents=True, exist_ok=True)
            self.sock = factory(access_token=f"{self.app_id}:{self.token}", write_to_file=False, log_path=str(config.LOGS), on_trades=self._cb_other, on_positions=self._cb_other,
                                on_orders=self._cb_order, on_general=self._cb_other, on_error=self._cb_error, on_connect=self._cb_open, on_close=self._cb_close, reconnect=True)
        except Exception as e:
            self.state.update(connected=False, error=f"WebSocket not started: {type(e).__name__}: {str(e)[:100]}")
            return False
        self.thread = threading.Thread(target=self._run, daemon=True, name="order-feed")
        self.thread.start()
        return True

    def _run(self):
        try:
            self.sock.connect()
        except Exception as e:
            self.state.update(connected=False, error=f"WebSocket stopped: {type(e).__name__}: {str(e)[:100]}")

    def stop(self):
        try:
            if self.sock is not None:
                self.sock.close_connection()
        except Exception:
            pass
        self.state["connected"] = False
