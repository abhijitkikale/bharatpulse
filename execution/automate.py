"""FYERS Automate executor: an ALTERNATIVE to the direct-API engine (engine.py), chosen with  "executor": "automate"  in execution/config.json (default "api").

Why: orders sent through the FYERS API must come from one registered static IP; a Qatar home line has none. Orders placed by FYERS Automate run on FYERS' own servers and need
no IP. The dashboard stays the brain (levels, entry, stop, target, stepped trailing, loss limit); Automate only places and closes the order:

  entry  -> POST the slot's ENTRY webhook  -> the Automate workflow buys the ATM option (1 lot, intraday) with a wide emergency stop-loss
  exit   -> POST the slot's EXIT webhook   -> the same workflow squares the position off (sent when the dashboard says STOP / TRAIL / TARGET / DAY END, at square-off time,
            or on an emergency exit)

One workflow = one "slot" = one trade per day (a workflow continues once per run): execution/automate.json lists the slots (side CALL / PUT, entry_url, exit_url). The URLs
are secrets (anyone with one can place an order): that file is git-ignored, the URLs are never printed or logged, and only https://*.fyers.in/ hosts are accepted.

The webhook answers nothing about the order, so the broker's POSITIONS (read-only API: reads need no static IP) are the truth: an entry is "filled" when a new long option
position of the right side appears, an exit when it is flat again. Automate chooses the strike when it runs, so the engine records the strike it actually got. A webhook is
never sent twice for the same entry (a lost answer is resolved by watching positions); an exit is re-sent once. Anything unexplained halts new entries and says so."""
import json
import re
import time
import urllib.parse

from . import config, levels
from .engine import Engine, _hm
from .models import Res

AUTOMATE_FILE = config.EXEC / "automate.json"
MODE = "AUTOMATE"
HOST_OK = re.compile(r"^[a-z0-9.-]+\.fyers\.in$")


def valid_url(u):
    try:
        p = urllib.parse.urlparse(str(u))
        return p.scheme == "https" and bool(HOST_OK.match(p.hostname or "")) and len(p.path) > 1
    except ValueError:
        return False


def load_config(path=None):
    """{"slots": [{"name", "side", "entry_url", "exit_url"}], "test": {"entry_url", "exit_url"}}. A slot or test URL that is not an https fyers.in address is dropped."""
    try:
        d = json.loads((path or AUTOMATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"slots": [], "test": {}}
    slots, seen = [], set()
    for s in d.get("slots") or []:
        if (s.get("name") and s.get("name") not in seen and s.get("side") in ("CALL", "PUT") and valid_url(s.get("entry_url")) and valid_url(s.get("exit_url"))
                and s["entry_url"] != s["exit_url"]):
            seen.add(s["name"])
            slots.append({k: str(s[k]) for k in ("name", "side", "entry_url", "exit_url")})
    t = d.get("test") or {}
    test = {k: t[k] for k in ("entry_url", "exit_url") if valid_url(t.get(k))}
    live_urls = {u for s in slots for u in (s["entry_url"], s["exit_url"])}
    test = {k: v for k, v in test.items() if v not in live_urls}              # a live order URL can never be fired by the test command
    return {"slots": slots, "test": test}


def fire(url, payload=None, session=None, timeout=10):
    """POST the webhook once. Never raises, never retries. ok = a 2xx answer; uncertain = no definite answer (timeout, 5xx, 429): the order may or may not have been started."""
    if not valid_url(url):
        return Res(False, None, None, "not a FYERS webhook address")
    try:
        r = (session or __import__("requests")).post(url, json=payload or {}, timeout=timeout)
    except Exception as e:
        return Res(False, None, None, f"{type(e).__name__}: {str(e)[:80]}", uncertain=True)
    body = (r.text or "")[:200]
    raw = {"http": r.status_code, "body": body}
    if 200 <= r.status_code < 300:
        return Res(True, None, r.status_code, body or "ok", raw=raw)
    if r.status_code >= 500 or r.status_code == 429:
        return Res(False, None, r.status_code, f"HTTP {r.status_code}", uncertain=True, raw=raw)
    return Res(False, None, r.status_code, f"HTTP {r.status_code}: {body[:80]}", raw=raw)


class AutomateEngine(Engine):
    """Same dashboard-in, records-out contract as Engine (the Runner and the status page use both alike); different hands: webhooks instead of API orders.
    Phases reuse the engine's names: ENTRY_SENT (entry webhook sent, waiting for the position), PROTECTED (position open; the Automate emergency stop is the safety net,
    the dashboard's stop / target / trailing decide the exit), EXIT_SENT, CLOSED, SKIPPED, REJECTED, CHECK_MANUALLY, STUCK."""

    def __init__(self, broker, store, cfg=None, notify=None, instruments=None, paused=None, sleep=time.sleep, slots_cfg=None, fire_fn=None, ticks=None):
        super().__init__(MODE, broker, store, cfg, notify, instruments=instruments, paused=paused, sleep=sleep)
        self.ticks = ticks                                                         # execution.tick_feed.TickFeed (or None): live prices of the contracts held
        self.ac = slots_cfg if slots_cfg is not None else load_config()
        self.fire = fire_fn or fire
        self.ws_state = {"connected": False, "last": 0.0, "error": "not used with Automate"}
        self._pos = (0.0, None)

    # ------------------------------------------------------------------ helpers
    def _say(self, sid, level, kind, text, data=None):
        self.store.event(self.mode, sid, level, kind, text, data)
        self.say_out(f"[LIVE] (Automate) {text}", level)

    def step(self, intents, now, **kw):
        self._pos = (0.0, None)                                                   # positions are read afresh once per pass
        return super().step(intents, now, **kw)

    def _positions(self, fresh=False):
        """The broker's positions: read once per pass (several records share the reading); fresh=True reads again (before a webhook, and when closing)."""
        t, p = self._pos
        if fresh or t == 0.0:
            p = self.b.positions()
            self._pos = (time.time() or 1.0, p)
        return p

    def _extra(self, rec):
        try:
            return json.loads(rec["extra_json"] or "{}")
        except ValueError:
            return {}

    def _slot(self, name):
        return next((s for s in self.ac["slots"] if s["name"] == name), None)

    def _free_slot(self, side, day):
        used = {self._extra(r).get("slot") for r in self.store.signals(day, self.mode)}
        return next((s for s in self.ac["slots"] if s["side"] == side and s["name"] not in used), None)

    def _find_new(self, rec, pos):
        """(symbol, qty, avg) of the long NIFTY option of the entry's side that appeared since the entry webhook was sent, else None."""
        ex = self._extra(rec)
        suffix = "CE" if ex.get("side") == "CALL" else "PE"
        before = ex.get("before") or {}
        best = None
        for p in pos or []:
            gain = p.net_qty - int(before.get(p.symbol, 0))
            if p.net_qty > 0 and gain > 0 and "NIFTY" in p.symbol and p.symbol.endswith(suffix) and (best is None or gain > best[1]):
                best = (p.symbol, gain, self._fill_price(ex, p))
        return best

    @staticmethod
    def _fill_price(ex, p):
        """What THIS trade paid. FYERS shows, for a contract traded more than once in a day, the average of ALL the day's buys (26 Nov: the second trade in the same contract showed 174.03,
        the average of 179.45 and the real 168.60). So the price is worked out from what the contract's buys were before this entry (kept in `buys_before`): the buys' total value after
        minus before, over the quantity added. Without those figures, the position's own average is all there is."""
        b = (ex.get("buys_before") or {}).get(p.symbol)
        bq, bavg = (int(b[0]), float(b[1])) if b else (0, 0.0)
        if p.buy_qty and p.buy_qty > bq and p.buy_avg:
            return round((p.buy_avg * p.buy_qty - bavg * bq) / (p.buy_qty - bq), 2)
        return p.avg

    def _fire(self, rec, role, slot, event, payload=None):
        res = self.fire(slot[f"{event}_url"], {"source": "bharatpulse", "event": event, "slot": slot["name"], **(payload or {})})
        self._log_order(rec["signal_id"], role, slot["name"], "webhook", {"slot": slot["name"], "event": event}, res)
        return res

    # ------------------------------------------------------------------ admission
    def _admit(self, it, now, day, accept_new, dash_fresh, auth_ok):
        cfg = self.cfg
        if it.status != "OPEN":
            if self.store.claim(it.signal_id, self.mode, day, self._intent_json(it), "SKIPPED"):
                self._rec_update(it.signal_id, note="already over when first seen", symbol=it.symbol)
                self.store.event(self.mode, it.signal_id, "info", "skip", f"{it.symbol}: already {it.status} when first seen, not ordered")
            return
        if not accept_new or not dash_fresh or not auth_ok:
            return
        reasons = []
        mins = now.hour * 60 + now.minute
        halted = self.store.halted(day, self.mode)
        if halted:
            reasons.append(f"orders halted ({halted})")
        if self.paused(now):
            reasons.append("new entries are paused (/pause)")
        if self.flatten_asked:
            reasons.append("an emergency exit was asked")
        if self.store.active(self.mode):
            reasons.append("another position or order is still open")
        age = self._age_min(it, now)
        if age > cfg["max_signal_age_min"]:
            reasons.append(f"the dashboard trade is {age:.0f} minutes old")
        if self.store.executed_entries(day, self.mode) >= cfg["max_entries_per_day"]:
            reasons.append("the executed-entries limit for the day was reached")
        if self.store.realized(day, self.mode) <= -cfg["max_daily_loss_inr"]:
            reasons.append("the day's loss limit was reached")
        if mins < _hm(cfg["first_order"]) or mins >= _hm(cfg["square_off"]):
            reasons.append("outside the order window")
        slot = self._free_slot(it.option_side, day)
        if slot is None:
            reasons.append(f"no unused Automate workflow for {it.option_side} is left today (execution/automate.json)")
        pos = self._positions(fresh=True)
        if pos is None:
            reasons.append("the broker's positions cannot be read, so a fill could not be confirmed")
        if not reasons:
            late = self._entry_guard(it)
            if late:
                reasons.append(late)
        if not self.store.claim(it.signal_id, self.mode, day, self._intent_json(it), "ENTRY_PENDING_SEND"):
            return
        lot = self.instruments.lot_size(it.symbol) if self.instruments is not None else None
        self._rec_update(it.signal_id, symbol=it.symbol, qty_req=(lot * cfg["lots"]) if lot else None)
        if reasons:
            self._rec_update(it.signal_id, phase="SKIPPED", note="; ".join(reasons))
            self._say(it.signal_id, "info", "skip", f"dashboard trade {it.n} ({it.option_side} {it.symbol}) NOT ordered: {'; '.join(reasons)}.")
            return
        extra = {"slot": slot["name"], "side": it.option_side, "before": {p.symbol: p.net_qty for p in pos if p.net_qty}, "realized_before": {p.symbol: p.realized for p in pos},
                 "buys_before": {p.symbol: [p.buy_qty, p.buy_avg] for p in pos if p.buy_qty}}
        self._rec_update(it.signal_id, extra_json=json.dumps(extra), entry_at=time.time(), entry_tag=slot["name"])
        rec = self.store.get(it.signal_id, self.mode)
        res = self._fire(rec, "ENTRY", slot, "entry", {"signal": it.tag_base})
        if res.ok or res.uncertain:
            self._rec_update(it.signal_id, phase="ENTRY_SENT", note=None if res.ok else res.msg)
            if res.ok:
                self._say(it.signal_id, "info", "entry_sent", f"trade {it.n}: entry webhook sent ({slot['name']}, {it.option_side}, dashboard entry {it.entry:.2f}); waiting for the position.")
            else:
                self._say(it.signal_id, "warn", "entry_uncertain", f"trade {it.n}: the entry webhook got no definite answer ({res.msg}). It will NOT be sent again; watching the broker's positions.")
        else:
            self._rec_update(it.signal_id, phase="REJECTED", note=res.msg)
            self._say(it.signal_id, "warn", "entry_rejected", f"trade {it.n}: FYERS refused the entry webhook ({res.msg}). Nothing was ordered. Is the workflow '{slot['name']}' started in Automate?")

    # ------------------------------------------------------------------ the phases
    def _advance(self, rec, it, now, day):
        ph = rec["phase"]
        if ph == "ENTRY_SENT":
            self._entry_sent(rec, it, now, day)
        elif ph == "PROTECTED":
            self._open(rec, it, now, day)
        elif ph == "EXIT_SENT":
            self._exit_sent(rec, it, now, day)

    def _entry_sent(self, rec, it, now, day):
        sid = rec["signal_id"]
        pos = self._positions()
        found = self._find_new(rec, pos)
        if found:
            sym, qty, avg = found
            extra = self._extra(rec)
            extra["protected_at"] = time.time()
            if self.cfg.get("levels_from_fill") and avg:
                extra["lv"] = levels.initial(avg, it.sl_pct, it.tp_pct)
            self._rec_update(sid, phase="PROTECTED", symbol=sym, qty_filled=qty, avg_entry=avg or it.entry, extra_json=json.dumps(extra))
            same = "" if sym == it.symbol else f" (the dashboard's contract was {it.symbol}: Automate chose the strike when it ran)"
            if "lv" in extra:
                lv = extra["lv"]
                rules = (f"The Automate emergency stop is the safety net; the engine's own stop {lv['stop']:.2f} (-{lv['sl_pct']:.0f}% of your fill) / target {lv['target']:.2f} (+{lv['tp_pct']:.0f}%) "
                         f"decide the exit, checked against the live price of {sym}.")
            else:
                rules = f"The Automate emergency stop is the safety net; the dashboard's stop {it.stop_now:.2f} / target {it.target:.2f} decide the exit."
            self._say(sid, "info", "protected", f"trade {it.n}: the position is open: BUY {qty} {sym} at {avg:.2f} (dashboard entry {it.entry:.2f}){same}. {rules}")
            if self.ticks is not None:
                self.ticks.watch(sym)
            return
        waited = time.time() - (rec["entry_at"] or time.time())
        if pos is not None and waited > self.cfg["automate_entry_watch_s"]:
            self._rec_update(sid, phase="CHECK_MANUALLY", note="no position appeared after the entry webhook")
            self._say(sid, "critical", "entry_unconfirmed", f"the entry webhook was sent {waited:.0f} s ago and NO position has appeared. Look in the FYERS app and in Automate: if an order is pending, "
                      "cancel it; if the workflow did not run, nothing is open. New entries are halted for today.")
            self._halt(day, "an Automate entry could not be confirmed")
        elif pos is None and waited > self.cfg["automate_entry_watch_s"] * 3:
            self._rec_update(sid, phase="CHECK_MANUALLY", note="the broker's positions cannot be read")
            self._say(sid, "critical", "entry_unconfirmed", "an entry webhook was sent and the broker's positions cannot be read. CHECK THE FYERS APP NOW.")
            self._halt(day, "positions cannot be read after an Automate entry")

    def _held(self, rec, pos):
        return sum(p.net_qty for p in pos if p.symbol == rec["symbol"])

    def _open(self, rec, it, now, day):
        sid = rec["signal_id"]
        extra = self._extra(rec)
        pos = self._positions()
        if pos is not None and self._held(rec, pos) == 0 and self._settled(rec):
            return self._close(rec, "position found closed at the broker (the Automate stop-loss, or a manual exit)", None, day)
        if self.ticks is not None:
            self.ticks.watch(rec["symbol"])
        px = self._price(rec["symbol"]) if self.cfg.get("levels_from_fill") else None
        due = self._judge(rec, it, extra, px[0]) if px else None
        own = px is not None                                                      # the engine's own levels (from the real fill, on the live price) are in charge: they need no dashboard
        if not self.dash_ok and not own and not extra.get("stale_told"):
            extra["stale_told"] = True
            self._rec_update(sid, extra_json=json.dumps(extra))
            self._say(sid, "warn", "dashboard_silent", f"the dashboard is silent while {rec['symbol']} is open: only the Automate emergency stop protects it until the dashboard is back.")
        late = now.hour * 60 + now.minute >= _hm(self.cfg["square_off"])
        reason = None
        if self.flatten_asked:
            reason = "emergency exit asked"
        elif late:
            reason = "square-off time"
        elif own:
            if due:
                reason = f"{due[1]} ({px[1]})"
            elif self.dash_ok and it.status == "DAY END":
                reason = "the dashboard day end"
        elif self.dash_ok and it.status != "OPEN":
            reason = {"TARGET": "the dashboard target was reached", "DAY END": "the dashboard day end"}.get(it.status, f"the dashboard trade ended ({it.status})")
        if reason:
            self._begin_exit(rec, it, reason, now, day)

    # ------------------------------------------------------------------ live price, levels from the real fill
    def _price(self, symbol):
        """(price, source) of the contract held: the live feed's last price when it is fresh, else the broker position's last price read this pass; None when neither is usable."""
        age = float(self.cfg.get("price_max_age_s", 10))
        if self.ticks is not None:
            hit = self.ticks.price(symbol, age)
            if hit is not None:
                return hit[0], "live feed"
        t, pos = self._pos
        if pos is not None and t and time.time() - t <= age:
            for p in pos:
                if p.symbol == symbol and p.ltp > 0:
                    return p.ltp, "broker price"
        return None

    def _levels(self, rec, extra):
        """The stop / target of an open trade measured from its real fill (kept in the record), or None when this feature is off or the fill is unknown."""
        if not self.cfg.get("levels_from_fill"):
            return None
        if extra.get("lv"):
            return extra["lv"]
        fill = float(rec["avg_entry"] or 0)
        if fill <= 0:
            return None
        it = self.last_intents.get(rec["signal_id"]) or self._intent_from_rec(rec)
        return levels.initial(fill, getattr(it, "sl_pct", None), getattr(it, "tp_pct", None))              # a trade opened before this feature, or before a restart

    def _judge(self, rec, it, extra, price):
        """Raises the stop if the price or the dashboard's own raised stop calls for it (saved and told once), then checks the price against the levels. (kind, text) when an exit is due."""
        lv = self._levels(rec, extra)
        if lv is None:
            return None
        new, why = levels.ratchet(lv, price, levels.dash_lock(it))
        if new is not lv or "lv" not in extra:
            extra["lv"] = new
            self._rec_update(rec["signal_id"], extra_json=json.dumps(extra))
            if why:
                self._say(rec["signal_id"], "info", "stop_raised", f"trade {it.n}: stop raised to {new['stop']:.2f}: {why}.")
        return levels.check(new, price)

    def _entry_guard(self, it):
        """None, or why this entry is refused: the option's live price is already more than entry_guard_pct above the dashboard's entry price (the order would buy it dearer than the trade assumed)."""
        g = float(self.cfg.get("entry_guard_pct") or 0)
        if g <= 0 or self.ticks is None:
            return None
        px = self.ticks.wait_price(it.symbol, float(self.cfg.get("entry_guard_wait_s", 1.5)), float(self.cfg.get("price_max_age_s", 10)))
        if px is None:
            return None
        if px > it.entry * (1 + g / 100):
            return f"the option is already at {px:.2f}, {(px / it.entry - 1) * 100:.1f}% above the dashboard's entry {it.entry:.2f} (limit {g:g}%): buying now would be much dearer than the trade assumes"
        return None

    def fast_check(self, now):
        """Between the full passes (called every half second, no broker calls): compare the live price of an open position with the stop / target measured from its real fill and send
        the exit webhook at once when one is hit. Only the live feed counts here (the broker's price is read in the full pass)."""
        if self.ticks is None or not self.cfg.get("levels_from_fill"):
            return
        for rec in self.store.active(self.mode):
            if rec["phase"] != "PROTECTED":
                continue
            hit = self.ticks.price(rec["symbol"], float(self.cfg.get("price_max_age_s", 10)))
            if hit is None:
                continue
            it = self.last_intents.get(rec["signal_id"]) or self._intent_from_rec(rec)
            if it is None:
                continue
            extra = self._extra(rec)
            try:
                due = self._judge(rec, it, extra, hit[0])
            except Exception:
                continue
            if due and not self.flatten_asked:
                self._begin_exit(rec, it, f"{due[1]} (live feed)", now, now.date().isoformat())

    def _begin_exit(self, rec, it, reason, now, day):
        sid = rec["signal_id"]
        slot = self._slot(self._extra(rec).get("slot"))
        self._rec_update(sid, exit_reason=reason)
        if slot is None:
            self._rec_update(sid, phase="STUCK", note="the Automate workflow of this trade is no longer configured")
            self._say(sid, "critical", "exit_failed", f"trade {it.n}: {reason}, but its Automate workflow is not configured. EXIT THE POSITION YOURSELF NOW.")
            return self._halt(day, "an exit could not be sent")
        res = self._fire(rec, "EXIT", slot, "exit")
        if res.ok or res.uncertain:
            self._rec_update(sid, phase="EXIT_SENT", exit_at=time.time(), tries=1)
            self._say(sid, "info" if res.ok else "warn", "exit_sent", f"trade {it.n}: {reason}: exit webhook sent ({slot['name']})" + ("" if res.ok else f" without a definite answer ({res.msg}); watching the position") + ".")
        else:
            self._rec_update(sid, phase="STUCK", note=f"exit webhook failed: {res.msg}")
            self._say(sid, "critical", "exit_failed", f"trade {it.n}: {reason}, and FYERS REFUSED THE EXIT WEBHOOK ({res.msg}). EXIT THE POSITION YOURSELF NOW in the FYERS app.")
            self._halt(day, "an exit webhook failed")

    def _exit_sent(self, rec, it, now, day):
        sid = rec["signal_id"]
        pos = self._positions()
        if pos is not None and self._held(rec, pos) == 0:
            return self._close(rec, rec["exit_reason"] or "exit", None, day)
        waited = time.time() - (rec["exit_at"] or time.time())
        if waited <= self.cfg["automate_exit_wait_s"]:
            return
        slot = self._slot(self._extra(rec).get("slot"))
        if rec["tries"] < 2 and slot is not None:                                          # a square-off of a flat position is harmless, so one repeat is safe
            res = self._fire(rec, "EXIT", slot, "exit")
            self._rec_update(sid, exit_at=time.time(), tries=rec["tries"] + 1)
            self._say(sid, "warn", "exit_resent", f"trade {it.n}: the position is still open {waited:.0f} s after the exit webhook; it was sent once more ({'ok' if res.ok else res.msg}).")
        else:
            self._rec_update(sid, phase="STUCK", note="the position is still open after the exit webhooks")
            self._say(sid, "critical", "exit_stuck", f"trade {it.n}: the position in {rec['symbol']} is STILL OPEN after the exit webhooks. EXIT IT YOURSELF NOW in the FYERS app.")
            self._halt(day, "an exit is not completing")

    def _close(self, rec, reason, price, day):
        sid = rec["signal_id"]
        qty, entry = int(rec["qty_filled"] or 0), float(rec["avg_entry"] or 0)
        pnl = None
        pos = self._positions(fresh=True)
        before = self._extra(rec).get("realized_before") or {}
        for p in pos or []:
            if p.symbol == rec["symbol"]:
                pnl = round(p.realized - float(before.get(p.symbol, 0.0)), 2)
        if pnl is None:
            price = float(price or self._exit_price_from_trades(rec["symbol"], rec) or entry)
            pnl = round((price - entry) * qty, 2)
        price = round(entry + pnl / qty, 2) if qty else entry
        self._rec_update(sid, phase="CLOSED", avg_exit=price, exit_at=time.time(), realized_pnl=pnl, exit_reason=reason)
        total = self.store.realized(day, self.mode)
        self._say(sid, "info", "closed", f"{rec['symbol']}: closed ({reason}) at about {price:.2f}; entry {entry:.2f} x {qty}: Rs {pnl:+,.0f} on this trade, Rs {total:+,.0f} today "
                  "(from the broker's realised profit, before charges).")
        if total <= -self.cfg["max_daily_loss_inr"]:
            self._halt(day, f"the day's loss reached Rs {-total:,.0f}")

    # ------------------------------------------------------------------ reconciliation
    def reconcile(self, now):
        day = now.date().isoformat()
        pos = self.b.positions()
        self._pos = (time.time(), pos)
        if pos is None:
            self.recon = {"ok": None, "at": time.time(), "text": "the broker's positions cannot be read right now"}
            return self.recon
        held = {p.symbol: p.net_qty for p in pos}
        known = set()
        for rec in self.store.active(self.mode):
            known.add(rec["symbol"])
            if rec["phase"] == "ENTRY_SENT":
                f = self._find_new(rec, pos)
                if f:
                    known.add(f[0])
        problems = []
        for sym, net in held.items():
            if net != 0 and sym not in known and sym not in self.orphans_told:
                self.orphans_told.add(sym)
                problems.append(f"{sym}: an open position of {net} that the engine does not manage")
                self._say(None, "critical", "orphan_position", f"the broker shows a position of {net} in {sym} that this engine did not open (or that is not a trade it is waiting for). "
                          "It will NOT touch it. New orders are halted: check the FYERS app.")
                self._halt(day, f"an unmanaged position in {sym}")
        self.recon = {"ok": not problems, "at": time.time(), "text": "matches the broker" if not problems else "; ".join(problems)}
        return self.recon
