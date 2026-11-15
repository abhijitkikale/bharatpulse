"""Order manager for the live signals: turns the stream page's trades into broker orders. OFF BY DEFAULT.

Modes (data/orders_config.json, key "mode"):
  "off"      nothing happens (the default).
  "dry_run"  the full logic runs against a pretend broker and tells Telegram what it WOULD do; no order leaves the PC. Use it first.
  "live"     real orders through FyersBroker. Only you set this (edit the file at the PC); there is no Telegram command for it.

One position at a time, as the page's engine works: BUY limit just above the reference price -> when filled, a stop-loss limit order at the broker at the
engine's stop -> every trailing / regime stop move changes that order -> when the engine says the trade is over, the broker stop is checked, else it is
cancelled and a marketable sell goes out; a failed entry is cancelled (never chased); a failed stop order exits at once; nothing new after the daily loss
limit or the order cap; quantity = lots x lot size, never more than max_lots. All steps are idempotent (state in data/orders/<day>.json), so a restart
repeats nothing. Pure logic: the broker is passed in, so the tests use a fake one. FyersBroker is written from Fyers' API v3 documentation and has NOT been
exercised against the real service: check the first order by hand (small, with the Fyers app open) before trusting it."""
import json
import math
import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "orders_config.json"
STATE_DIR = ROOT / "data" / "orders"
TICK = 0.05
DEFAULTS = {"mode": "off", "lots": 1, "lot_size": 65, "max_lots": 2, "entry_slip_pct": 2.0, "exit_slip_pct": 2.0, "sl_gap_pct": 1.5, "entry_wait_s": 45,
            "max_daily_loss_inr": 5000, "max_orders_per_day": 40, "square_off": "15:15", "max_signal_age_min": 3}
ACTIVE = ("entry_sent", "protected", "exit_sent")


def load_config(path=None):
    cfg = dict(DEFAULTS)
    try:
        cfg.update({k: v for k, v in json.loads(pathlib.Path(path or CONFIG).read_text(encoding="utf-8")).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    cfg["lots"] = max(1, min(int(cfg["lots"]), int(cfg["max_lots"]), 2))          # hard ceiling of 2 lots whatever the file says
    return cfg


def save_mode(mode, path=None):
    """Write only the mode (used by the Telegram kill switch); every other setting stays as it is."""
    p = pathlib.Path(path or CONFIG)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d["mode"] = mode
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1), encoding="utf-8")


def tick_up(x):
    return round(math.ceil(x / TICK - 1e-9) * TICK, 2)


def tick_down(x):
    return round(math.floor(x / TICK + 1e-9) * TICK, 2)


class DryBroker:
    """Pretend broker for dry_run: every order is accepted; an entry counts as filled at its limit, a stop order stays open, an exit fills at its limit."""
    def __init__(self):
        self.orders, self.n = {}, 0

    def place(self, symbol, qty, side, otype, limit=0.0, stop=0.0, tag=""):
        self.n += 1
        oid = f"DRY-{self.n}"
        self.orders[oid] = {"symbol": symbol, "qty": qty, "side": side, "type": otype, "limit": limit, "stop": stop, "state": "open" if otype == 4 else "filled", "avg": limit, "tag": tag}
        return {"ok": True, "id": oid, "msg": "dry run"}

    def modify(self, oid, limit=None, stop=None, qty=None):
        if oid in self.orders:
            self.orders[oid].update({k: v for k, v in (("limit", limit), ("stop", stop)) if v is not None})
        return {"ok": True, "id": oid, "msg": "dry run"}

    def cancel(self, oid):
        if oid in self.orders:
            self.orders[oid]["state"] = "cancelled"
        return {"ok": True, "id": oid, "msg": "dry run"}

    def status(self, oid):
        o = self.orders.get(oid)
        return {"state": "unknown"} if not o else {"state": o["state"], "avg": o["avg"], "qty": o["qty"]}


class FyersBroker:
    """Real orders through Fyers API v3 (https://api-t1.fyers.in/api/v3). NOT exercised against the live service by the author; order types: 1 limit, 2 market,
    3 stop, 4 stop-limit; side 1 buy, -1 sell; order status 1 cancelled, 2 traded, 5 rejected, 6 pending, 7 expired. The app must be an activated TRADING app
    whose registered static IP is the address this PC reaches Fyers from."""
    BASE = "https://api-t1.fyers.in/api/v3"

    def __init__(self, app_id, token, session=None):
        import requests
        self.s = session or requests
        self.h = {"Authorization": f"{app_id}:{token}", "Content-Type": "application/json"}

    def _call(self, method, path, body=None, params=None):
        try:
            r = self.s.request(method, self.BASE + path, headers=self.h, json=body, params=params, timeout=10)
            return r.json()
        except Exception as e:
            return {"s": "error", "message": f"{type(e).__name__}: {str(e)[:120]}"}

    def place(self, symbol, qty, side, otype, limit=0.0, stop=0.0, tag=""):
        j = self._call("POST", "/orders/sync", {"symbol": symbol, "qty": int(qty), "type": otype, "side": side, "productType": "INTRADAY", "limitPrice": limit, "stopPrice": stop,
                                                "validity": "DAY", "disclosedQty": 0, "offlineOrder": False, "orderTag": tag[:20]})
        return {"ok": j.get("s") == "ok", "id": j.get("id"), "msg": str(j.get("message", ""))[:160]}

    def modify(self, oid, limit=None, stop=None, qty=None):
        body = {"id": oid, "type": 4}
        for k, v in (("limitPrice", limit), ("stopPrice", stop), ("qty", qty)):
            if v is not None:
                body[k] = v
        j = self._call("PATCH", "/orders/sync", body)
        return {"ok": j.get("s") == "ok", "id": oid, "msg": str(j.get("message", ""))[:160]}

    def cancel(self, oid):
        j = self._call("DELETE", "/orders/sync", {"id": oid})
        return {"ok": j.get("s") == "ok", "id": oid, "msg": str(j.get("message", ""))[:160]}

    def status(self, oid):
        j = self._call("GET", "/orders", params={"id": oid})
        rows = j.get("orderBook") or []
        if j.get("s") != "ok" or not rows:
            return {"state": "unknown"}
        r = rows[0]
        st = {1: "cancelled", 2: "filled", 5: "rejected", 6: "open", 7: "cancelled"}.get(r.get("status"), "open" if r.get("status") in (4, 3) else "unknown")
        return {"state": st, "avg": r.get("tradedPrice") or r.get("limitPrice"), "qty": r.get("qty"), "filled": int(r.get("filledQty") or 0)}


class OrderManager:
    def __init__(self, broker, cfg, state=None, label="DRY RUN"):
        self.b, self.cfg, self.label = broker, cfg, label
        self.state = state if state is not None else {"trades": {}, "orders": 0, "pnl_inr": 0.0, "halted": None}

    # ---- helpers ----
    def _place(self, *a, **k):
        self.state["orders"] = self.state.get("orders", 0) + 1
        return self.b.place(*a, **k)

    def _active(self):
        return any(t.get("phase") in ACTIVE for t in self.state["trades"].values())

    def _halt(self, why, msgs):
        if not self.state.get("halted"):
            self.state["halted"] = why
            msgs.append(f"[{self.label}] ORDERS HALTED for today: {why}. No new orders; an open stop-loss order stays at the broker.")

    # ---- one pass over the page's trades ----
    def step(self, trades, now, paused=False):
        """trades: the page's trades list ({n, side, strike, symbol, entry, stop_now, status, last, exit, entry_time (HH:MM), ...}); now: tz-aware Timestamp.
        Returns [Telegram text, ...]."""
        msgs, cfg, st = [], self.cfg, self.state
        qty = cfg["lots"] * cfg["lot_size"]
        for t in sorted(trades, key=lambda x: x["n"]):
            n = str(t["n"])
            rec = st["trades"].get(n)
            tag = f"{self.label}"
            if rec is None:
                if t["status"] != "OPEN":
                    st["trades"][n] = {"phase": "skipped", "why": "it was already over when the manager first saw it"}
                    continue
                age = (now - pd.Timestamp(f"{now.date()} {t['entry_time']}", tz=now.tz)).total_seconds() / 60
                why = (st.get("halted") and f"orders halted ({st['halted']})") or (paused and "new entries are paused (/pause)") or (self._active() and "another position is still open") or \
                      (not t.get("symbol") and "the contract symbol is unknown") or (age > cfg["max_signal_age_min"] and f"the signal is {age:.0f} minutes old") or \
                      (st.get("orders", 0) >= cfg["max_orders_per_day"] and "the daily order cap is used up")
                if why:
                    st["trades"][n] = {"phase": "skipped", "why": why}
                    msgs.append(f"[{tag}] trade {n} ({t['side']} {t['strike']}) NOT ordered: {why}.")
                    continue
                limit = tick_up(t["entry"] * (1 + cfg["entry_slip_pct"] / 100))
                r = self._place(t["symbol"], qty, 1, 1, limit=limit, tag=f"bp{n}entry")
                if not r["ok"]:
                    st["trades"][n] = {"phase": "rejected", "why": r["msg"]}
                    msgs.append(f"[{tag}] trade {n}: the BUY order was REJECTED ({r['msg']}). Nothing is open.")
                    continue
                st["trades"][n] = {"phase": "entry_sent", "symbol": t["symbol"], "qty": qty, "entry_id": r["id"], "sent_at": now.isoformat(), "stop": None}
                msgs.append(f"[{tag}] trade {n}: BUY {qty} {t['symbol']} limit {limit:.2f} (reference {t['entry']:.2f}) sent.")
                continue
            ph = rec["phase"]
            if ph == "entry_sent":
                sx = self.b.status(rec["entry_id"])
                filled = int(sx.get("filled", rec["qty"] if sx["state"] == "filled" else 0) or 0)
                timed_out = (now - pd.Timestamp(rec["sent_at"])).total_seconds() > cfg["entry_wait_s"]
                if sx["state"] == "filled" or (sx["state"] == "cancelled" and filled > 0):
                    self._protect(rec, t, now, msgs, n, rec["qty"] if sx["state"] == "filled" else filled, sx.get("avg"))
                elif sx["state"] in ("rejected", "cancelled") and filled == 0:
                    rec.update(phase="rejected", why=sx["state"])
                    msgs.append(f"[{tag}] trade {n}: the BUY order was {sx['state']}. Nothing is open.")
                elif timed_out:
                    self.b.cancel(rec["entry_id"])
                    s2 = self.b.status(rec["entry_id"])
                    f2 = int(s2.get("filled", rec["qty"] if s2["state"] == "filled" else 0) or 0)
                    if f2 > 0:                                   # a late or partial fill: protect whatever is held
                        self._protect(rec, t, now, msgs, n, f2, s2.get("avg"))
                    elif s2["state"] == "unknown":
                        rec.update(phase="check_manually", why="the order status could not be read")
                        msgs.append(f"[{tag}] trade {n}: the BUY order's status could not be confirmed after the wait. CHECK YOUR BROKER NOW: you may be holding a position without a stop.")
                    else:
                        rec.update(phase="missed", why="not filled in time")
                        msgs.append(f"[{tag}] trade {n}: the BUY was not filled within {cfg['entry_wait_s']} s and was cancelled (not chased).")
            elif ph == "protected":
                s = self.b.status(rec["sl_id"])
                square = now.strftime("%H:%M") >= cfg["square_off"]
                if s["state"] == "filled":
                    self._done(rec, float(s.get("avg") or rec["stop"]), msgs, f"[{tag}] trade {n}: the broker stop-loss order FILLED", n)
                elif t["status"] != "OPEN" or square:
                    self.b.cancel(rec["sl_id"])
                    self._exit(rec, t, now, msgs, "the page says the trade is over" if t["status"] != "OPEN" else "square-off time")
                elif t["stop_now"] - rec["stop"] >= 0.05:
                    new = tick_down(t["stop_now"])
                    lim = tick_down(new * (1 - cfg["sl_gap_pct"] / 100))
                    r = self.b.modify(rec["sl_id"], limit=lim, stop=new, qty=rec["qty"])
                    st["orders"] = st.get("orders", 0) + 1
                    msgs.append(f"[{tag}] trade {n}: stop-loss order moved to trigger {new:.2f}, limit {lim:.2f}." if r["ok"] else
                                f"[{tag}] trade {n}: MOVING THE STOP FAILED ({r['msg']}). Change the stop-loss order to {new:.2f} yourself.")
                    if r["ok"]:
                        rec["stop"] = new
            elif ph == "exit_sent":
                s = self.b.status(rec["exit_id"])
                if s["state"] == "filled":
                    self._done(rec, float(s.get("avg") or rec.get("exit_limit")), msgs, f"[{tag}] trade {n}: exit filled", n)
                elif (now - pd.Timestamp(rec["exit_at"])).total_seconds() > cfg["entry_wait_s"]:
                    rec["tries"] = rec.get("tries", 1) + 1
                    if rec["tries"] > 3:
                        rec["phase"] = "stuck"
                        msgs.append(f"[{tag}] trade {n}: the EXIT ORDER IS NOT FILLING after 3 tries. EXIT THE POSITION YOURSELF NOW.")
                    else:
                        self.b.cancel(rec["exit_id"])
                        lim = tick_down(rec["exit_limit"] * (1 - cfg["exit_slip_pct"] / 100))
                        r = self._place(rec["symbol"], rec["qty"], -1, 1, limit=lim, tag=f"bp{n}exit")
                        rec.update(exit_id=r["id"], exit_limit=lim, exit_at=now.isoformat())
                        msgs.append(f"[{tag}] trade {n}: exit re-priced to {lim:.2f} (try {rec['tries']}).")
        return msgs

    def _protect(self, rec, t, now, msgs, n, qty, avg):
        """The entry is filled (fully or in part): record it and place the broker stop-loss order for exactly what is held."""
        rec["qty"] = int(qty)
        rec["fill"] = float(avg or t["entry"])
        stop = tick_down(t["stop_now"])
        lim = tick_down(stop * (1 - self.cfg["sl_gap_pct"] / 100))
        r = self._place(rec["symbol"], rec["qty"], -1, 4, limit=lim, stop=stop, tag=f"bp{n}sl")
        if r["ok"]:
            rec.update(phase="protected", sl_id=r["id"], stop=stop)
            msgs.append(f"[{self.label}] trade {n}: BUY filled ({rec['qty']}) at {rec['fill']:.2f}. Stop-loss order placed: trigger {stop:.2f}, limit {lim:.2f}.")
        else:
            self._exit(rec, t, now, msgs, f"the stop-loss order was REJECTED ({r['msg']})")

    def _exit(self, rec, t, now, msgs, why):
        ref = float(t.get("exit") or t.get("last") or rec.get("fill") or t["entry"])
        lim = tick_down(ref * (1 - self.cfg["exit_slip_pct"] / 100))
        r = self._place(rec["symbol"], rec["qty"], -1, 1, limit=lim, tag=f"bp{t['n']}exit")
        if r["ok"]:
            rec.update(phase="exit_sent", exit_id=r["id"], exit_limit=lim, exit_at=now.isoformat(), tries=1)
            msgs.append(f"[{self.label}] trade {t['n']}: {why}: SELL {rec['qty']} limit {lim:.2f} sent.")
        else:
            rec["phase"] = "stuck"
            msgs.append(f"[{self.label}] trade {t['n']}: {why}, and the EXIT ORDER FAILED ({r['msg']}). EXIT THE POSITION YOURSELF NOW.")

    def _done(self, rec, price, msgs, text, n):
        pnl = (price - rec.get("fill", price)) * rec["qty"]
        rec.update(phase="done", exit_price=price, pnl_inr=round(pnl, 2))
        self.state["pnl_inr"] = round(self.state.get("pnl_inr", 0.0) + pnl, 2)
        msgs.append(f"{text} at {price:.2f}: about Rs {pnl:+,.0f} on this trade, Rs {self.state['pnl_inr']:+,.0f} today (before charges).")
        if self.state["pnl_inr"] <= -self.cfg["max_daily_loss_inr"]:
            self._halt(f"the day's loss reached Rs {-self.state['pnl_inr']:,.0f}", msgs)


def state_path(day):
    return STATE_DIR / f"{day}.json"


def load_state(day):
    try:
        return json.loads(state_path(day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(day, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(day).write_text(json.dumps(state, indent=1), encoding="utf-8")
