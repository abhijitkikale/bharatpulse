"""A scripted FYERS stand-in for the Stage 2 tests and `python -m execution simulate`: no network, no real order. It behaves like the real service in the ways
that matter to the engine (acknowledged / rejected / lost acknowledgement / partial fills / cancel that fails / orderbook that cannot be read) and each behaviour is
switched on by the test."""
from .models import BOrder, BPos, Res


class MockBroker:
    name = "mock"

    def __init__(self, auto_fill_entry=True, auto_fill_exit=True):
        self.n = 0
        self.book = {}
        self.pos = {}
        self.fills = []
        self.calls = []                         # (action, details) of every call, to assert what was (not) sent
        self.auto_fill_entry, self.auto_fill_exit = auto_fill_entry, auto_fill_exit
        self.reject = set()                     # roles to reject: "entry", "sl", "exit" (by tag suffix E, S, X)
        self.lose_ack = set()                   # roles whose order is created at the broker but the answer is lost (uncertain)
        self.drop = set()                       # roles whose order never reaches the broker and the answer is lost (uncertain)
        self.partial_entry = None               # fill only this many on the entry
        self.cancel_fails = False               # cancel returns an error and the order stays
        self.cancel_uncertain = False           # cancel gets no answer but is applied
        self.reads_fail = False                 # the orderbook / positions cannot be read
        self.modify_fails = False
        self.auth_error = False
        self.fyers_avg = False                  # behave like FYERS: after a second trade in the same contract the position's average is the day's average of ALL buys
        self.ltp = {}                           # symbol -> last price shown in the positions

    @staticmethod
    def role(tag):
        base = tag[2 + 12:] if tag.startswith("BP") else tag
        return {"E": "entry", "S": "sl", "X": "exit"}.get(base[:1], "other")

    def _new(self, symbol, qty, side, otype, limit, stop, tag):
        self.n += 1
        oid = f"MOCK{self.n:05d}"
        o = BOrder(id=oid, tag=tag, symbol=symbol, side=side, type=otype, status="pending", qty=int(qty), filled=0, limit=limit, stop=stop)
        self.book[oid] = o
        return o

    def _fill(self, o, qty=None, price=None):
        qty = o.qty - o.filled if qty is None else qty
        price = price if price is not None else (o.limit or o.stop)
        total = o.avg * o.filled + price * qty
        o.filled += qty
        o.avg = total / o.filled
        o.status = "filled" if o.filled >= o.qty else "partial"
        p = self.pos.setdefault(o.symbol, {"qty": 0, "cost": 0.0, "realized": 0.0, "bq": 0, "bc": 0.0})
        if o.side == 1:
            p["cost"] += price * qty
            p["qty"] += qty
            p["bq"] += qty
            p["bc"] += price * qty
        else:
            avg = p["cost"] / p["qty"] if p["qty"] else price
            p["realized"] += (price - avg) * qty
            p["cost"] -= avg * qty
            p["qty"] -= qty
        self.fills.append({"orderNumber": o.id, "symbol": o.symbol, "side": o.side, "tradedQty": qty, "tradePrice": price, "orderTag": o.tag})

    # ---- broker interface ----
    def place(self, symbol, qty, side, otype, limit=0.0, stop=0.0, tag="", ref=None):
        role = self.role(tag)
        self.calls.append(("place", {"symbol": symbol, "qty": qty, "side": side, "type": otype, "limit": limit, "stop": stop, "tag": tag}))
        if self.auth_error:
            return Res(False, None, -16, "token expired", auth=True)
        if role in self.drop:
            return Res(False, None, None, "timeout", uncertain=True)
        if role in self.reject:
            return Res(False, None, -50, f"rejected by the exchange ({role})")
        o = self._new(symbol, qty, side, otype, limit, stop, tag)
        if side == 1 and otype in (1, 2) and self.auto_fill_entry:
            self._fill(o, self.partial_entry, limit)
        elif side == -1 and otype in (1, 2) and self.auto_fill_exit:
            self._fill(o, None, limit)
        if role in self.lose_ack:
            return Res(False, None, None, "timeout", uncertain=True)
        return Res(True, o.id, 1101, "ok")

    def modify(self, oid, limit=None, stop=None, qty=None, otype=None):
        self.calls.append(("modify", {"id": oid, "limit": limit, "stop": stop, "qty": qty}))
        o = self.book.get(oid)
        if self.modify_fails or o is None or o.terminal:
            return Res(False, oid, -51, "modify failed")
        if limit is not None:
            o.limit = limit
        if stop is not None:
            o.stop = stop
        if qty is not None:
            o.qty = int(qty)
        return Res(True, oid, 1102, "ok")

    def cancel(self, oid):
        self.calls.append(("cancel", {"id": oid}))
        o = self.book.get(oid)
        if self.cancel_fails or o is None or o.terminal:
            return Res(False, oid, -52, "cannot cancel")
        o.status = "cancelled"
        if self.cancel_uncertain:
            return Res(False, None, None, "timeout", uncertain=True)
        return Res(True, oid, 1103, "ok")

    def orders(self):
        return None if self.reads_fail else [self._copy(o) for o in self.book.values()]

    @staticmethod
    def _copy(o):
        return BOrder(**o.__dict__)

    def order(self, oid):
        if self.reads_fail:
            return None
        o = self.book.get(oid)
        return self._copy(o) if o else None

    def order_by_tag(self, tag):
        if self.reads_fail:
            return None
        return [self._copy(o) for o in self.book.values() if o.tag == tag]

    def trades(self):
        return None if self.reads_fail else list(self.fills)

    def positions(self):
        if self.reads_fail:
            return None
        out = []
        for s, p in self.pos.items():
            bq, bc = p.get("bq", 0), p.get("bc", 0.0)
            out.append(BPos(symbol=s, net_qty=p["qty"], avg=((bc / bq if bq else 0.0) if self.fyers_avg else (p["cost"] / p["qty"] if p["qty"] else 0.0)),
                            ltp=float(self.ltp.get(s, 0.0)), realized=p["realized"], buy_qty=bq, buy_avg=(bc / bq if bq else 0.0)))
        return out

    def funds(self):
        return {"s": "ok"}

    def profile(self):
        return {"s": "ok"}

    # ---- test controls ----
    def fill(self, oid, qty=None, price=None):
        self._fill(self.book[oid], qty, price)

    def trigger(self, oid, price=None):
        """The market reaches a resting stop order: it fills."""
        o = self.book[oid]
        self._fill(o, None, price if price is not None else o.limit)

    def external_close(self, symbol, price):
        """The user sells the position by hand at the broker; any open order stays (as it would)."""
        p = self.pos.get(symbol)
        if p and p["qty"]:
            self.n += 1
            o = BOrder(id=f"MAN{self.n}", tag="", symbol=symbol, side=-1, type=2, status="pending", qty=p["qty"], filled=0)
            self.book[o.id] = o
            self._fill(o, None, price)

    def of(self, role):
        return [o for o in self.book.values() if self.role(o.tag) == role]
