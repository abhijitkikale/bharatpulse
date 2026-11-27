"""Broker adapters with one interface: FyersBroker (real, FYERS API v3 over REST) and PaperBroker (in memory, mirrors the dashboard's paper trades).

Interface: place / modify / cancel return Res; orders() trades() positions() funds() profile() read the account.
Endpoints used (FYERS API v3, base https://api-t1.fyers.in/api/v3, header "Authorization: app_id:access_token"), checked against FYERS' published endpoint reference
(github.com/FyersDev/fyers-skills, skills/fyers-trading/references/endpoints.md and orders.md) and the official fyers-apiv3 SDK's own endpoint table:
  POST/PATCH/DELETE /orders/sync      place, modify, cancel one order (synchronous: returns the order id)
  GET /orders                         orderbook (?id=<order id> or ?order_tag=1:<tag>)
  GET /tradebook   GET /positions   GET /funds   GET /profile
Order types: 1 limit, 2 market, 3 SL-M, 4 SL-L. Side: 1 buy, -1 sell. Order status: 1 cancelled, 2 traded, 4 transit, 5 rejected, 6 pending, 7 expired (partly filled =
pending with filledQty > 0). Responses: s "ok" with code 1101 placed / 1102 modified / 1103 cancelled / 200 read; code 201 = sent but NOT acknowledged (look it up).
Only reads are retried. A place / modify / cancel that times out, returns 201, 429 or a server error is reported as UNCERTAIN: the engine looks the order up
by its tag in the orderbook before it does anything else, and never sends the same entry again on its own."""
import collections
import json
import re
import threading
import time

from .models import BOrder, BPos, Res

BASE = "https://api-t1.fyers.in/api/v3"
STATUS = {1: "cancelled", 2: "filled", 4: "transit", 5: "rejected", 6: "pending", 7: "expired"}
OK_CODES = (200, 1101, 1102, 1103)


def norm_tag(tag):
    """The orderbook shows tags as '1:<tag>'; the tag itself is what we sent."""
    return re.sub(r"^\d+:", "", str(tag or ""))


def norm_order(r):
    qty, filled = int(r.get("qty") or 0), int(r.get("filledQty") or 0)
    st = STATUS.get(r.get("status"), "unknown")
    if st == "pending" and 0 < filled < qty:
        st = "partial"
    return BOrder(id=str(r.get("id")), tag=norm_tag(r.get("orderTag")), symbol=r.get("symbol", ""), side=int(r.get("side") or 0), type=int(r.get("type") or 0), status=st, qty=qty,
                  filled=filled, limit=float(r.get("limitPrice") or 0), stop=float(r.get("stopPrice") or 0), avg=float(r.get("tradedPrice") or 0),
                  message=str(r.get("message") or "")[:160], time=str(r.get("orderDateTime") or ""))


def norm_pos(r):
    return BPos(symbol=r.get("symbol", ""), net_qty=int(r.get("netQty") or 0), avg=float(r.get("netAvg") or r.get("avgPrice") or 0), ltp=float(r.get("ltp") or 0),
                realized=float(r.get("realized_profit") or 0), unrealized=float(r.get("unrealized_profit") or 0), product=str(r.get("productType") or ""),
                buy_qty=int(r.get("buyQty") or 0), buy_avg=float(r.get("buyAvg") or 0))


class RateLimiter:
    """At most `per_s` operations in any rolling second (FYERS allows 10 order operations a second in total)."""
    def __init__(self, per_s=8, clock=time.monotonic, sleep=time.sleep):
        self.per_s, self.clock, self.sleep, self.t, self.lock = per_s, clock, sleep, collections.deque(), threading.Lock()

    def wait(self):
        with self.lock:
            now = self.clock()
            while self.t and now - self.t[0] >= 1.0:
                self.t.popleft()
            if len(self.t) >= self.per_s:
                self.sleep(max(0.0, 1.0 - (now - self.t[0])))
            self.t.append(self.clock())


def ipv4_session():
    """A requests session that connects over IPv4 ONLY. FYERS accepts orders only from the registered (IPv4) address; a PC that also has IPv6 can reach FYERS from its IPv6
    address instead, which FYERS refuses ("Orders are only allowed from whitelisted IP addresses", seen on 20 Nov 2026). Binding the source to 0.0.0.0 forces IPv4."""
    import requests
    from requests.adapters import HTTPAdapter

    class IPv4Adapter(HTTPAdapter):
        def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
            pool_kwargs["source_address"] = ("0.0.0.0", 0)
            super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
    s = requests.Session()
    s.mount("https://", IPv4Adapter())
    s.mount("http://", IPv4Adapter())
    return s


class FyersBroker:
    name = "fyers"

    def __init__(self, app_id, token, cfg, session=None, sleep=time.sleep, timeout=10):
        self.cfg = cfg
        self.s = session or ipv4_session()
        self.h = {"Authorization": f"{app_id}:{token}", "Content-Type": "application/json"}
        self.sleep, self.timeout = sleep, timeout
        self.limiter = RateLimiter(cfg.get("max_ops_per_s", 8), sleep=sleep)
        self.last_error = ""
        self.last_ok = 0.0

    # ---- transport ----
    def _raw(self, method, path, body=None, params=None):
        """(http status, json or None, error text). Never raises."""
        self.limiter.wait()
        try:
            r = self.s.request(method, BASE + path, headers=self.h, json=body, params=params, timeout=self.timeout)
        except Exception as e:                                           # timeout, connection reset, DNS ...
            self.last_error = f"{type(e).__name__}: {str(e)[:100]}"
            return None, None, self.last_error
        try:
            j = r.json()
        except ValueError:
            j = None
        if r.status_code == 429:
            try:
                self.sleep(min(5.0, float(r.headers.get("Retry-After", 1))))
            except (TypeError, ValueError):
                self.sleep(1.0)
        return r.status_code, j, ""

    @staticmethod
    def _res(http, j, exc, ok_codes=OK_CODES):
        msg = (j or {}).get("message") or exc or (f"HTTP {http}" if http else "")
        low = str(msg).lower()
        code = (j or {}).get("code")
        if exc or http is None or http >= 500 or http == 429 or j is None:
            return Res(False, None, code, str(msg)[:200], uncertain=True, raw=j)
        if http in (401, 403) or "token" in low or "authenticat" in low or "unauthor" in low or re.search(r"\bip\b", low):
            return Res(False, None, code, str(msg)[:200], auth=True, raw=j)
        if j.get("s") == "ok" and code in ok_codes:
            return Res(True, str(j.get("id") or j.get("id_fyers") or "") or None, code, str(msg)[:200], raw=j)
        if code == 201:                                                  # sent, not acknowledged: the order may exist
            return Res(False, str(j.get("id") or "") or None, code, "sent but not acknowledged (201)", uncertain=True, raw=j)
        return Res(False, None, code, str(msg)[:200], raw=j)

    def _get(self, path, params=None, tries=3):
        last = (None, None, "")
        for i in range(tries):                                           # reads are safe to retry
            last = self._raw("GET", path, params=params)
            if last[0] == 200 and last[1] and last[1].get("s") == "ok":
                self.last_ok = time.time()
                return last
            self.sleep(0.5 * (i + 1))
        return last

    # ---- orders ----
    def place(self, symbol, qty, side, otype, limit=0.0, stop=0.0, tag="", ref=None):
        body = {"symbol": symbol, "qty": int(qty), "type": int(otype), "side": int(side), "productType": self.cfg["product_type"], "limitPrice": float(limit), "stopPrice": float(stop),
                "validity": self.cfg["validity"], "disclosedQty": 0, "offlineOrder": False, "orderTag": tag}
        res = self._res(*self._raw("POST", "/orders/sync", body))
        res.raw = {**(res.raw or {}), "_request": body}
        return res

    def modify(self, oid, limit=None, stop=None, qty=None, otype=None):
        body = {"id": oid}
        for k, v in (("limitPrice", limit), ("stopPrice", stop), ("qty", qty), ("type", otype)):
            if v is not None:
                body[k] = v
        return self._res(*self._raw("PATCH", "/orders/sync", body))

    def cancel(self, oid):
        return self._res(*self._raw("DELETE", "/orders/sync", {"id": oid}))

    # ---- account ----
    def orders(self):
        http, j, exc = self._get("/orders")
        if not (j and j.get("s") == "ok"):
            return None
        return [norm_order(r) for r in j.get("orderBook") or []]

    def order(self, oid):
        for o in self.orders() or []:
            if o.id == str(oid):
                return o
        return None

    def order_by_tag(self, tag):
        http, j, exc = self._get("/orders", params={"order_tag": f"1:{tag}"})
        rows = [norm_order(r) for r in (j or {}).get("orderBook") or []] if j and j.get("s") == "ok" else None
        if rows:
            return [o for o in rows if o.tag == tag] or rows
        full = self.orders()                                             # the filter can fail or differ: look through the whole orderbook
        return None if full is None else [o for o in full if o.tag == tag]

    def trades(self):
        http, j, exc = self._get("/tradebook")
        return None if not (j and j.get("s") == "ok") else list(j.get("tradeBook") or [])

    def positions(self):
        http, j, exc = self._get("/positions")
        if not (j and j.get("s") == "ok"):
            return None
        return [norm_pos(r) for r in j.get("netPositions") or []]

    def funds(self):
        http, j, exc = self._get("/funds")
        return j if j and j.get("s") == "ok" else None

    def profile(self):
        http, j, exc = self._get("/profile")
        return j if j and j.get("s") == "ok" else None


class PaperBroker:
    """In-memory broker for PAPER mode. It mirrors the dashboard's own paper trades: an entry fills at the dashboard's entry price (`ref`), an exit at the dashboard's exit
    price. A stop-loss order simply rests; the dashboard decides when the paper trade is over and the engine then exits at the dashboard's price."""
    name = "paper"

    def __init__(self):
        self.n = 0
        self.book = {}
        self.pos = {}
        self.fills = []

    def _fill(self, o, price):
        o.status, o.filled, o.avg = "filled", o.qty, float(price)
        p = self.pos.setdefault(o.symbol, {"qty": 0, "cost": 0.0, "realized": 0.0})
        if o.side == 1:
            p["cost"] += price * o.qty
            p["qty"] += o.qty
        else:
            avg = p["cost"] / p["qty"] if p["qty"] else price
            p["realized"] += (price - avg) * o.qty
            p["cost"] -= avg * o.qty
            p["qty"] -= o.qty
        self.fills.append({"orderNumber": o.id, "symbol": o.symbol, "side": o.side, "tradedQty": o.qty, "tradePrice": price, "orderTag": o.tag})

    def place(self, symbol, qty, side, otype, limit=0.0, stop=0.0, tag="", ref=None):
        self.n += 1
        oid = f"PAPER-{self.n}"
        o = BOrder(id=oid, tag=tag, symbol=symbol, side=side, type=otype, status="pending", qty=int(qty), filled=0, limit=limit, stop=stop)
        self.book[oid] = o
        if otype in (1, 2):
            self._fill(o, ref if ref is not None else limit)
        return Res(True, oid, 1101, "paper")

    def modify(self, oid, limit=None, stop=None, qty=None, otype=None):
        o = self.book.get(oid)
        if o is None or o.terminal:
            return Res(False, oid, -1, "paper: order is not open")
        if limit is not None:
            o.limit = limit
        if stop is not None:
            o.stop = stop
        if qty is not None:
            o.qty = int(qty)
        return Res(True, oid, 1102, "paper")

    def cancel(self, oid):
        o = self.book.get(oid)
        if o is None or o.terminal:
            return Res(False, oid, -1, "paper: order is not open")
        o.status = "cancelled"
        return Res(True, oid, 1103, "paper")

    def orders(self):
        return list(self.book.values())

    def order(self, oid):
        return self.book.get(oid)

    def order_by_tag(self, tag):
        return [o for o in self.book.values() if o.tag == tag]

    def trades(self):
        return list(self.fills)

    def positions(self):
        return [BPos(symbol=s, net_qty=p["qty"], avg=(p["cost"] / p["qty"] if p["qty"] else 0.0), realized=p["realized"]) for s, p in self.pos.items()]

    def funds(self):
        return {"s": "ok", "paper": True}

    def profile(self):
        return {"s": "ok", "paper": True}
