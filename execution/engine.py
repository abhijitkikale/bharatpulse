"""The execution engine: carries out the dashboard's trade objects at a broker (PAPER or FYERS) and reports what the broker actually did.

It makes NO trading decision. What to trade, when, how it ends and where the stop is all come from the dashboard's trade object (Intent):
  * a trade appears (status OPEN)      -> BUY the dashboard's contract (limit = dashboard entry + cushion), quantity = lots x the contract's lot size
  * the dashboard's stop_now moves up  -> the broker stop-loss order is modified to that absolute value (never lowered)
  * the dashboard says the trade is over (TARGET / STOP / TRAIL / DAY END) -> the broker stop is cancelled (and confirmed) and the position is sold
The stop-loss is a real order at the broker from the moment the entry fills; the target is managed (the dashboard's TARGET status sends the exit), because FYERS GTT / OCO
does not support INTRADAY and a second resting sell order next to the stop could reverse the position.

Phases of a signal: ENTRY_PENDING_SEND -> ENTRY_SENT | ENTRY_UNCERTAIN -> PROTECTED -> EXIT_SENT -> CLOSED; ends without a position as SKIPPED / REJECTED / MISSED;
problems that need a human: STUCK, CHECK_MANUALLY (the engine halts new entries for the day and says so). The record is claimed before any order is sent, so one dashboard
signal can never produce two entries, also across restarts.
Safety policy: an entry that fills is never left without a stop (a stop that cannot be placed means an immediate exit); an order whose answer is lost is looked up in the
orderbook by its tag and never simply sent again; the broker's positions are the truth and are reconciled."""
import datetime as dt
import json
import time

from . import config
from .brokers import norm_order
from .models import BOrder, make_signal_id, parse_intent, tick_down, tick_up  # noqa: F401

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ACTIVE = ("ENTRY_UNCERTAIN", "ENTRY_SENT", "PROTECTED", "EXIT_SENT")


def now_ist():
    return dt.datetime.now(IST)


def _hm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


class Engine:
    def __init__(self, mode, broker, store, cfg=None, notify=None, instruments=None, paused=None, sleep=time.sleep):
        self.mode, self.b, self.store = mode, broker, store
        self.cfg = cfg or config.load()
        self.say_out = notify or (lambda text, level="info": print(text, flush=True))
        self.instruments = instruments
        self.paused = paused or (lambda now: False)
        self.sleep = sleep
        self.ws = {}                              # order id -> (BOrder, received at): pushed by the order WebSocket
        self.ws_state = {"connected": False, "last": 0.0}
        self.recon = {"ok": None, "at": None, "text": "not run yet"}
        self.flatten_asked = False
        self.orphans_told = set()
        self.last_intents = {}
        self.dash_ok = True
        self.lot_fallback = None                  # PAPER only: the dashboard's own lot (journal_settings.json) when the instrument master has no answer

    # ------------------------------------------------------------------ helpers
    def _say(self, sid, level, kind, text, data=None):
        self.store.event(self.mode, sid, level, kind, text, data)
        self.say_out(f"[{self.mode}] {text}", level)

    def _rec_update(self, sid, **kw):
        self.store.update(sid, self.mode, **kw)

    def _tag(self, it, role, k=""):
        return f"BP{it.tag_base}{role}{k}"

    def _order(self, oid, tag=None):
        """Latest known state of an order: the WebSocket's if it is fresh, else the orderbook; None when it cannot be read."""
        hit = self.ws.get(str(oid))
        if hit and time.time() - hit[1] < 5 and hit[0].terminal:
            return hit[0]
        o = self.b.order(oid) if oid else None
        if o is None and tag:
            rows = self.b.order_by_tag(tag)
            o = rows[0] if rows else None
        if o is None and hit:
            return hit[0]
        return o

    def on_order_event(self, msg):
        """An order update from the WebSocket ({'s':'ok','orders':{...}} as the SDK delivers it)."""
        try:
            self.ws_state["last"] = time.time()
            o = norm_order(msg["orders"])
            self.ws[o.id] = (o, time.time())
        except Exception:
            pass

    def _log_order(self, sid, role, tag, action, request, res):
        self.store.order(self.mode, sid, role, tag, action, request, res)

    def _halt(self, day, why):
        if not self.store.halted(day, self.mode):
            self.store.halt(day, self.mode, why)
            self._say(None, "critical", "halt", f"NEW ORDERS HALTED for today: {why}. An existing stop-loss order stays at the broker; nothing new is sent.")

    def _age_min(self, it, now):
        t = dt.datetime.combine(dt.date.fromisoformat(it.day), dt.time(*divmod(_hm(it.entry_time), 60)), tzinfo=IST)
        return (now - t).total_seconds() / 60.0

    @staticmethod
    def _settled(rec, secs=20):
        """True once the stop has been in place for a while. A position read right after a fill can still show zero (the broker's position feed lags), so a flat reading
        is only believed after this."""
        try:
            return time.time() - float(json.loads(rec["extra_json"] or "{}").get("protected_at", 0)) > secs
        except (TypeError, ValueError):
            return False

    def _confirm(self, oid, tag, want=("cancelled", "filled", "rejected", "expired"), polls=3):
        """Read an order a few times until it is in one of the states in `want`; the last reading (or None)."""
        o = None
        for i in range(polls):
            o = self._order(oid, tag)
            if o is not None and o.status in want:
                return o
            self.sleep(0.5)
        return o

    # ------------------------------------------------------------------ one pass
    def step(self, intents, now, accept_new=True, dash_fresh=True, auth_ok=True):
        """intents: the dashboard's trades as Intent objects (invalid ones were already refused by the source). Returns nothing; everything is recorded and told."""
        day = now.date().isoformat()
        self.dash_ok = dash_fresh
        by_id = {it.signal_id: it for it in intents}
        self.last_intents.update(by_id)
        for rec in self.store.active(self.mode):                           # first the positions and orders we already have (also after a restart)
            it = by_id.get(rec["signal_id"]) or self._intent_from_rec(rec)
            if it is None:
                continue
            if not dash_fresh and rec["phase"] in ("ENTRY_SENT", "PROTECTED"):
                it = self._frozen(it, rec)                                  # the dashboard is silent: keep what we have, do not follow it blindly
            try:
                self._advance(rec, it, now, day)
            except Exception as e:                                          # one bad record must never stop the others
                self._say(rec["signal_id"], "warn", "error", f"internal error while managing {rec['symbol']}: {type(e).__name__}: {str(e)[:120]}")
        for it in sorted(intents, key=lambda x: x.n):
            if self.store.get(it.signal_id, self.mode) is None:
                self._admit(it, now, day, accept_new, dash_fresh, auth_ok)
        if self.flatten_asked and not self.store.active(self.mode):
            self.flatten_asked = False                                      # everything is flat: the emergency is over (new entries stay up to the mode / halt)

    @staticmethod
    def _intent_from_rec(rec):
        try:
            from .models import Intent
            return Intent(**{k: v for k, v in json.loads(rec["intent_json"]).items() if k != "raw"}, raw=None)
        except Exception:
            return None

    @staticmethod
    def _frozen(it, rec):
        """With a silent dashboard the trade is treated as still open at the stop we already hold (no exit, no stop move)."""
        import dataclasses
        return dataclasses.replace(it, status="OPEN", stop_now=min(it.stop_now, rec["sl_stop"] or it.stop_now))

    # ------------------------------------------------------------------ admission
    def _admit(self, it, now, day, accept_new, dash_fresh, auth_ok):
        cfg = self.cfg
        if it.status != "OPEN":                                              # a trade that was already over when we first saw it is never entered
            if self.store.claim(it.signal_id, self.mode, day, self._intent_json(it), "SKIPPED"):
                self._rec_update(it.signal_id, note="already over when first seen", symbol=it.symbol)
                self.store.event(self.mode, it.signal_id, "info", "skip", f"{it.symbol}: already {it.status} when first seen, not ordered")
            return
        if not accept_new or not dash_fresh or not auth_ok:
            return                                                           # transient: look again next pass (the age limit still applies)
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
        if self.mode == "LIVE" and self.store.realized(day, self.mode) <= -cfg["max_daily_loss_inr"]:
            reasons.append("the day's loss limit was reached")
        if mins < _hm(cfg["first_order"]) or mins >= _hm(cfg["square_off"]):
            reasons.append("outside the order window")
        lot = None
        if self.instruments is not None:
            lot = self.instruments.lot_size(it.symbol)
        if not lot and self.mode == "PAPER":
            lot = self.lot_fallback
        qty = (lot * cfg["lots"]) if lot else None
        if qty is None:
            reasons.append("the lot size is unknown (the instrument master has no such contract)")
        elif self.mode == "LIVE":
            chk = self.instruments.validate(it.symbol, qty, it.option_side)
            if not chk.ok:
                reasons.append(chk.reason)
        if not self.store.claim(it.signal_id, self.mode, day, self._intent_json(it), "ENTRY_PENDING_SEND"):
            return                                                           # a duplicate: another pass or a restart already has this signal
        self._rec_update(it.signal_id, symbol=it.symbol, qty_req=qty)
        if reasons:
            self._rec_update(it.signal_id, phase="SKIPPED", note="; ".join(reasons))
            self._say(it.signal_id, "info", "skip", f"dashboard trade {it.n} ({it.option_side} {it.symbol}) NOT ordered: {'; '.join(reasons)}.")
            return
        tag = self._tag(it, "E")
        limit = tick_up(it.entry * (1 + cfg["entry_cushion_pct"] / 100), cfg["tick"])
        req = {"symbol": it.symbol, "qty": qty, "side": 1, "type": 1, "limit": limit, "tag": tag}
        res = self.b.place(it.symbol, qty, 1, 1, limit=limit, tag=tag, ref=it.entry)
        self._log_order(it.signal_id, "ENTRY", tag, "place", req, res)
        self._rec_update(it.signal_id, entry_tag=tag, entry_at=time.time())
        if res.auth:
            self._rec_update(it.signal_id, phase="REJECTED", note=f"broker refused the credentials: {res.msg}")
            self._halt(day, f"the broker refused the credentials ({res.msg})")
        elif res.ok:
            self._rec_update(it.signal_id, phase="ENTRY_SENT", entry_order_id=res.id)
            self._say(it.signal_id, "info", "entry_sent", f"trade {it.n}: BUY {qty} {it.symbol} limit {limit:.2f} (dashboard entry {it.entry:.2f}); order {res.id}.")
        elif res.uncertain:
            self._rec_update(it.signal_id, phase="ENTRY_UNCERTAIN", entry_order_id=res.id, note=res.msg)
            self._say(it.signal_id, "warn", "entry_uncertain", f"trade {it.n}: the BUY got no definite answer ({res.msg}). Looking it up at the broker; it will NOT be sent again.")
        else:
            self._rec_update(it.signal_id, phase="REJECTED", note=res.msg)
            self._say(it.signal_id, "warn", "entry_rejected", f"trade {it.n}: the BUY was REJECTED ({res.msg}). Nothing is open.")

    @staticmethod
    def _intent_json(it):
        import dataclasses
        d = dataclasses.asdict(it)
        d.pop("raw", None)
        return json.dumps(d)

    # ------------------------------------------------------------------ the phases
    def _advance(self, rec, it, now, day):
        ph = rec["phase"]
        if ph == "ENTRY_UNCERTAIN":
            self._resolve_entry(rec, it, now, day)
        elif ph == "ENTRY_SENT":
            self._entry_sent(rec, it, now, day)
        elif ph == "PROTECTED":
            self._protected(rec, it, now, day)
        elif ph == "EXIT_SENT":
            self._exit_sent(rec, it, now, day)

    def _resolve_entry(self, rec, it, now, day):
        """An entry whose answer was lost: look for it by tag. Found -> carry on with it. Not found for uncertain_wait_s -> it is declared unconfirmed and orders halt."""
        sid = rec["signal_id"]
        rows = self.b.order_by_tag(rec["entry_tag"])
        if rows:
            o = rows[0]
            self._rec_update(sid, phase="ENTRY_SENT", entry_order_id=o.id, note="found by tag after an unacknowledged send")
            self._say(sid, "info", "entry_found", f"the unacknowledged BUY was found at the broker (order {o.id}, {o.status}).")
            return
        if rows is not None and time.time() - (rec["entry_at"] or 0) > self.cfg["uncertain_wait_s"]:
            self._rec_update(sid, phase="CHECK_MANUALLY", note="entry not found at the broker after an unacknowledged send")
            self._say(sid, "critical", "entry_unconfirmed", f"the BUY for {rec['symbol']} was never acknowledged and is NOT in the orderbook after {self.cfg['uncertain_wait_s']} s. It was not resent. "
                      "CHECK THE BROKER APP: if a position exists, protect or exit it yourself.")
            self._halt(day, "an entry order could not be confirmed")
        elif rows is None and time.time() - (rec["entry_at"] or 0) > self.cfg["uncertain_wait_s"] * 3:
            self._rec_update(sid, phase="CHECK_MANUALLY", note="the orderbook cannot be read")
            self._say(sid, "critical", "entry_unconfirmed", f"the BUY for {rec['symbol']} could not be confirmed and the orderbook cannot be read. CHECK THE BROKER APP NOW.")
            self._halt(day, "the orderbook cannot be read after an unacknowledged entry")

    def _entry_sent(self, rec, it, now, day):
        sid = rec["signal_id"]
        o = self._order(rec["entry_order_id"], rec["entry_tag"])
        waited = time.time() - (rec["entry_at"] or time.time())
        if o is None:
            if waited > self.cfg["uncertain_wait_s"]:
                self._rec_update(sid, phase="CHECK_MANUALLY", note="entry status unreadable")
                self._say(sid, "critical", "entry_unconfirmed", f"the BUY for {rec['symbol']} cannot be read at the broker. CHECK THE BROKER APP NOW: you may hold a position without a stop.")
                self._halt(day, "an entry order status could not be read")
            return
        if o.status == "filled":
            return self._protect(rec, it, o.filled, o.avg, now, day)
        if o.status in ("cancelled", "rejected", "expired"):
            if o.filled > 0:
                return self._protect(rec, it, o.filled, o.avg, now, day)
            self._rec_update(sid, phase="REJECTED" if o.status == "rejected" else "MISSED", note=o.message or o.status)
            self._say(sid, "info", "entry_" + o.status, f"trade {it.n}: the BUY was {o.status}. Nothing is open.")
            return
        over = it.status != "OPEN" or self.flatten_asked or now.hour * 60 + now.minute >= _hm(self.cfg["square_off"])
        if waited > self.cfg["entry_wait_s"] or over:                        # no chasing: cancel what is not filled, keep what is
            self.b.cancel(o.id)
            o2 = self._confirm(o.id, rec["entry_tag"], want=("cancelled", "filled", "rejected", "expired"))
            if o2 is None or not o2.terminal:
                self._rec_update(sid, phase="CHECK_MANUALLY", note="entry cancel not confirmed")
                self._say(sid, "critical", "entry_unconfirmed", f"the BUY for {rec['symbol']} was asked to cancel but its state is not confirmed. CHECK THE BROKER APP NOW.")
                self._halt(day, "an entry cancel could not be confirmed")
                return
            if o2.filled > 0:
                return self._protect(rec, it, o2.filled, o2.avg, now, day)
            self._rec_update(sid, phase="MISSED", note="not filled in time" if not over else "the dashboard trade was already over")
            self._say(sid, "info", "entry_missed", f"trade {it.n}: the BUY was not filled {'within ' + str(self.cfg['entry_wait_s']) + ' s' if not over else '(the dashboard trade is over)'} and was cancelled (not chased).")

    def _protect(self, rec, it, qty, avg, now, day):
        """The entry filled (fully or in part): record what the broker confirmed and place the stop-loss order for exactly that quantity."""
        sid = rec["signal_id"]
        qty, avg = int(qty), float(avg or it.entry)
        self._rec_update(sid, qty_filled=qty, avg_entry=avg)
        rec = self.store.get(sid, self.mode)                                 # work from the saved record, not the copy read before the fill
        stop = tick_down(it.stop_now, self.cfg["tick"])
        lim = tick_down(stop * (1 - self.cfg["sl_limit_gap_pct"] / 100), self.cfg["tick"])
        if avg <= stop:                                                      # filled at or below the stop: a sell stop would be rejected, so leave at once
            self._say(sid, "warn", "no_room", f"trade {it.n}: filled at {avg:.2f}, not above the dashboard stop {stop:.2f}: exiting at once.")
            return self._begin_exit(rec, it, "filled at or below the stop", now, day, no_sl=True)
        tag = self._tag(it, "S")
        req = {"symbol": it.symbol, "qty": qty, "side": -1, "type": 4, "limit": lim, "stop": stop, "tag": tag}
        res = self.b.place(it.symbol, qty, -1, 4, limit=lim, stop=stop, tag=tag)
        self._log_order(sid, "STOP", tag, "place", req, res)
        if res.uncertain:
            for _ in range(3):                                               # the answer was lost: is the stop order there?
                rows = self.b.order_by_tag(tag)
                if rows:
                    res.ok, res.id = True, rows[0].id
                    break
                self.sleep(0.5)
        if res.ok:
            extra = json.loads(rec["extra_json"] or "{}")
            extra["protected_at"] = time.time()
            self._rec_update(sid, phase="PROTECTED", sl_order_id=res.id, sl_tag=tag, sl_stop=stop, sl_limit=lim, extra_json=json.dumps(extra))
            self._say(sid, "info", "protected", f"trade {it.n}: BUY filled {qty} at {avg:.2f} (dashboard entry {it.entry:.2f}). Stop-loss order placed: trigger {stop:.2f}, limit {lim:.2f} "
                      f"(dashboard target {it.target:.2f}).")
        else:
            self._say(sid, "critical", "stop_failed", f"trade {it.n}: the stop-loss order could not be placed ({res.msg}). The position is NOT left unprotected: exiting now.")
            self._begin_exit(rec, it, "the stop-loss order could not be placed", now, day, no_sl=True)

    def _protected(self, rec, it, now, day):
        sid = rec["signal_id"]
        extra = json.loads(rec["extra_json"] or "{}")
        sl = self._order(rec["sl_order_id"], rec["sl_tag"])
        if sl is not None and sl.status in ("filled",):
            return self._close(rec, "stop-loss order filled", sl.avg or rec["sl_stop"], day)
        if sl is not None and sl.status in ("cancelled", "rejected", "expired") and not extra.get("exit_reason"):
            self._say(sid, "critical", "stop_gone", f"trade {it.n}: the stop-loss order is {sl.status} ({sl.message}): the position would be unprotected. Exiting now.")
            return self._begin_exit(rec, it, f"the stop-loss order became {sl.status}", now, day, no_sl=True)
        reason = extra.get("exit_reason")
        if not reason:
            if self.flatten_asked:
                reason = "emergency exit asked"
            elif it.status != "OPEN":
                reason = {"TARGET": "the dashboard target was reached", "DAY END": "the dashboard day end"}.get(it.status, f"the dashboard trade ended ({it.status})")
            elif now.hour * 60 + now.minute >= _hm(self.cfg["square_off"]):
                reason = "square-off time"
        if reason:
            return self._begin_exit(rec, it, reason, now, day)
        new_stop = tick_down(it.stop_now, self.cfg["tick"])
        if sl is not None and new_stop - (rec["sl_stop"] or 0) >= self.cfg["tick"] - 1e-9:      # the dashboard raised the stop (trailing / regime): follow it, never lower it
            lim = tick_down(new_stop * (1 - self.cfg["sl_limit_gap_pct"] / 100), self.cfg["tick"])
            res = self.b.modify(rec["sl_order_id"], limit=lim, stop=new_stop, qty=rec["qty_filled"], otype=4)
            self._log_order(sid, "STOP", rec["sl_tag"], "modify", {"id": rec["sl_order_id"], "stop": new_stop, "limit": lim}, res)
            if res.ok:
                self._rec_update(sid, sl_stop=new_stop, sl_limit=lim)
                self._say(sid, "info", "stop_moved", f"trade {it.n}: stop-loss order moved to trigger {new_stop:.2f}, limit {lim:.2f} (the dashboard's stop).")
            else:
                self._say(sid, "warn", "stop_move_failed", f"trade {it.n}: moving the stop to {new_stop:.2f} FAILED ({res.msg}); the old stop {rec['sl_stop']:.2f} is still in force.")

    def _begin_exit(self, rec, it, reason, now, day, no_sl=False):
        """Cancel the stop-loss order and make sure it is really gone BEFORE selling, so the stop and the exit can never both fill and reverse the position."""
        sid = rec["signal_id"]
        rec = self.store.get(sid, self.mode)
        extra = json.loads(rec["extra_json"] or "{}")
        extra["exit_reason"] = reason
        self._rec_update(sid, exit_reason=reason, extra_json=json.dumps(extra))
        sold = 0
        if not no_sl and rec["sl_order_id"]:
            sl = self._order(rec["sl_order_id"], rec["sl_tag"])
            if sl is not None and not sl.terminal:
                self.b.cancel(sl.id)
                sl = self._confirm(sl.id, rec["sl_tag"])
            if sl is None or not sl.terminal:
                if time.time() - extra.setdefault("exit_asked_at", time.time()) > self.cfg["uncertain_wait_s"] and not extra.get("told"):
                    extra["told"] = True
                    self._say(sid, "critical", "cancel_unconfirmed", f"trade {it.n}: the stop-loss order's cancel is not confirmed, so no exit is sent (it could double-sell). CHECK THE BROKER APP.")
                self._rec_update(sid, extra_json=json.dumps(extra))
                return                                                       # try again next pass: stay PROTECTED with exit_reason set
            if sl.status == "filled":
                return self._close(rec, "stop-loss order filled", sl.avg or rec["sl_stop"], day)
            sold = sl.filled
        qty = int(rec["qty_filled"]) - int(sold)
        pos = self.b.positions()                                             # the broker's position is the truth for how much to sell
        if pos is not None:
            held = sum(p.net_qty for p in pos if p.symbol == it.symbol)
            if held > 0:
                qty = min(qty, held)
            elif self._settled(rec):
                qty = 0
        if qty <= 0:
            return self._close(rec, f"{reason}; position already flat", self._exit_price_from_trades(it.symbol, rec), day)
        ref = it.exit or it.last or rec["avg_entry"] or it.entry
        lim = tick_down(ref * (1 - self.cfg["exit_cushion_pct"] / 100), self.cfg["tick"])
        self._send_exit(rec, it, qty, lim, 1, reason)

    def _send_exit(self, rec, it, qty, lim, attempt, reason):
        sid = rec["signal_id"]
        tag = self._tag(it, "X", str(attempt))
        req = {"symbol": it.symbol, "qty": qty, "side": -1, "type": 1, "limit": lim, "tag": tag}
        res = self.b.place(it.symbol, qty, -1, 1, limit=lim, tag=tag, ref=it.exit or it.last)
        self._log_order(sid, "EXIT", tag, "place", req, res)
        if res.uncertain:
            rows = self.b.order_by_tag(tag)
            if rows:
                res.ok, res.id = True, rows[0].id
        if res.ok:
            self._rec_update(sid, phase="EXIT_SENT", exit_order_id=res.id, exit_tag=tag, exit_limit=lim, tries=attempt, exit_at=time.time())
            self._say(sid, "info", "exit_sent", f"trade {it.n}: {reason}: SELL {qty} {it.symbol} limit {lim:.2f} sent.")
        else:
            self._rec_update(sid, phase="STUCK", note=f"exit order failed: {res.msg}")
            self._say(sid, "critical", "exit_failed", f"trade {it.n}: {reason}, and the EXIT ORDER FAILED ({res.msg}). EXIT THE POSITION YOURSELF NOW.")
            self._halt(it.day, "an exit order failed")

    def _exit_sent(self, rec, it, now, day):
        sid = rec["signal_id"]
        o = self._order(rec["exit_order_id"], rec["exit_tag"])
        if o is None:
            return
        if o.status == "filled":
            return self._close(rec, rec["exit_reason"] or "exit filled", o.avg or rec["exit_limit"], day)
        waited = time.time() - (rec["exit_at"] or time.time())
        if o.status in ("cancelled", "rejected", "expired") or waited > self.cfg["entry_wait_s"]:
            if not o.terminal:
                self.b.cancel(o.id)
                o = self._confirm(o.id, rec["exit_tag"])
                if o is None or not o.terminal:
                    return
                if o.status == "filled":
                    return self._close(rec, rec["exit_reason"] or "exit filled", o.avg or rec["exit_limit"], day)
            remaining = int(rec["qty_filled"]) - int(o.filled)
            if rec["tries"] >= self.cfg["exit_tries"] or remaining <= 0:
                if remaining <= 0:
                    return self._close(rec, rec["exit_reason"] or "exit filled", o.avg or rec["exit_limit"], day)
                self._rec_update(sid, phase="STUCK", note="exit not filling")
                self._say(sid, "critical", "exit_stuck", f"trade {it.n}: the exit order is NOT FILLING after {rec['tries']} tries. EXIT THE POSITION YOURSELF NOW.")
                self._halt(day, "an exit order is not filling")
                return
            lim = tick_down((rec["exit_limit"] or it.last or rec["avg_entry"]) * (1 - self.cfg["exit_cushion_pct"] / 100), self.cfg["tick"])
            self._send_exit(rec, it, remaining, lim, rec["tries"] + 1, (rec["exit_reason"] or "exit") + " (re-priced)")

    def _exit_price_from_trades(self, symbol, rec):
        """Average price of the sells of this symbol in the broker's tradebook (used when the position was found closed); the entry price when nothing can be read."""
        try:
            sells = [t for t in (self.b.trades() or []) if t.get("symbol") == symbol and int(t.get("side", 0)) == -1]
            q = sum(float(t.get("tradedQty", 0)) for t in sells)
            if q > 0:
                return sum(float(t["tradePrice"]) * float(t["tradedQty"]) for t in sells) / q
        except Exception:
            pass
        return rec["avg_entry"]

    def _close(self, rec, reason, price, day):
        sid = rec["signal_id"]
        price = float(price or rec["avg_entry"] or 0)
        qty, entry = int(rec["qty_filled"] or 0), float(rec["avg_entry"] or 0)
        pnl = round((price - entry) * qty, 2)
        self._rec_update(sid, phase="CLOSED", avg_exit=price, exit_at=time.time(), realized_pnl=pnl, exit_reason=reason)
        total = self.store.realized(day, self.mode)
        self._say(sid, "info", "closed", f"{rec['symbol']}: closed ({reason}) at {price:.2f}; entry {entry:.2f} x {qty}: Rs {pnl:+,.0f} on this trade, Rs {total:+,.0f} today (broker-confirmed prices, before charges).")
        pos = self.b.positions()
        if pos is not None:
            left = sum(p.net_qty for p in pos if p.symbol == rec["symbol"])
            self.recon = {"ok": left == 0, "at": time.time(), "text": "flat after exit" if left == 0 else f"AFTER THE EXIT the broker still shows {left} of {rec['symbol']}"}
            if left != 0:
                self._say(sid, "critical", "not_flat", f"after the exit the broker still shows a position of {left} in {rec['symbol']}. CHECK THE BROKER APP.")
                self._halt(day, "the position is not flat after an exit")
        if self.mode == "LIVE" and total <= -self.cfg["max_daily_loss_inr"]:
            self._halt(day, f"the day's loss reached Rs {-total:,.0f}")

    # ------------------------------------------------------------------ reconciliation with the broker's positions
    def reconcile(self, now):
        """The broker's positions are the truth. Called every few seconds and once at start-up (also after a restart)."""
        day = now.date().isoformat()
        pos = self.b.positions()
        if pos is None:
            self.recon = {"ok": None, "at": time.time(), "text": "the broker's positions cannot be read right now"}
            return self.recon
        held = {p.symbol: p.net_qty for p in pos}
        problems = []
        active_symbols = set()
        for rec in self.store.active(self.mode):
            sym, ph = rec["symbol"], rec["phase"]
            active_symbols.add(sym)
            net = held.get(sym, 0)
            exp = int(rec["qty_filled"] or 0)
            sid = rec["signal_id"]
            it = self.last_intents.get(sid) or self._intent_from_rec(rec)
            if ph in ("PROTECTED", "EXIT_SENT") and net == 0 and exp > 0 and self._settled(rec):
                sl = self._order(rec["sl_order_id"], rec["sl_tag"]) if rec["sl_order_id"] else None
                if sl is not None and sl.status == "filled":
                    self._close(rec, "stop-loss order filled", sl.avg or rec["sl_stop"], day)
                else:
                    if sl is not None and not sl.terminal:                  # do not leave a live sell order behind a flat position
                        self.b.cancel(sl.id)
                    self._close(rec, "position found closed at the broker (closed outside the engine)", self._exit_price_from_trades(sym, rec), day)
                    self._say(sid, "warn", "closed_outside", f"{sym}: the broker shows the position closed although the engine had not exited it (manual exit?).")
            elif ph == "PROTECTED" and net > 0 and net != exp:
                problems.append(f"{sym}: broker holds {net}, engine expected {exp}")
                res = self.b.modify(rec["sl_order_id"], qty=net, otype=4, stop=rec["sl_stop"], limit=rec["sl_limit"])
                self._say(sid, "warn", "qty_mismatch", f"{sym}: the broker holds {net} but {exp} was expected; the stop-loss order was resized to {net} ({'ok' if res.ok else 'FAILED: ' + res.msg}).")
                if res.ok:
                    self._rec_update(sid, qty_filled=net)
            elif ph in ("ENTRY_SENT", "ENTRY_UNCERTAIN") and net > 0:
                pass                                                         # an entry fill the order loop has not processed yet; it will place the stop
        for sym, net in held.items():
            if net != 0 and sym not in active_symbols and sym not in self.orphans_told and self.mode == "LIVE":
                self.orphans_told.add(sym)
                problems.append(f"{sym}: an open position of {net} that the engine does not manage")
                self._say(None, "critical", "orphan_position", f"the broker shows a position of {net} in {sym} that this engine did not open. It will NOT touch it. New orders are halted: check the broker app.")
                self._halt(day, f"an unmanaged position in {sym}")
        self.recon = {"ok": not problems, "at": time.time(), "text": "matches the broker" if not problems else "; ".join(problems)}
        return self.recon

    # ------------------------------------------------------------------ controls and reporting
    def request_flatten(self):
        """Emergency: cancel any unfilled entry and exit the open position (stop first, confirmed, then the sell). Takes effect on the next pass."""
        self.flatten_asked = True

    def snapshot(self, day):
        recs = self.store.signals(day, self.mode)
        cur = next((r for r in recs if r["phase"] in ACTIVE), None)
        unreal = None
        if cur and cur["phase"] in ("PROTECTED", "EXIT_SENT"):
            it = self.last_intents.get(cur["signal_id"]) or self._intent_from_rec(cur)
            if it and it.last and cur["avg_entry"]:
                unreal = round((it.last - cur["avg_entry"]) * (cur["qty_filled"] or 0), 2)
        return {"mode": self.mode, "halted": self.store.halted(day, self.mode), "recon": self.recon, "ws": self.ws_state, "dashboard_fresh": self.dash_ok,
                "current": cur, "unrealized": unreal, "realized": self.store.realized(day, self.mode), "entries_today": self.store.executed_entries(day, self.mode),
                "signals": [{k: r[k] for k in ("signal_id", "symbol", "phase", "qty_filled", "avg_entry", "avg_exit", "sl_stop", "exit_reason", "realized_pnl", "note")} for r in recs]}
