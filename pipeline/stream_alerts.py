"""Telegram texts for the live stream page: one message when a setup is taken as a paper trade, one when it closes, one line for setups that are ignored.
Pure function (no network): new_messages() compares the day's run_trades() result with what was already sent and returns only what is new.
Facts only (side, strike, level, prices, percentages); a paper-trade tracker, not advice."""
import re

import pandas as pd


def _hm(t):
    return None if t is None else t.strftime("%H:%M")


def _contract(contracts, tr):
    """The contract name for the ticket: the Fyers symbol when the page priced it, else a plain description."""
    m = (contracts or {}).get((float(tr["strike"]), _opt(tr["side"])))
    return m["symbol"] if m and m.get("symbol") else f"NIFTY {tr['strike']} {_opt(tr['side'])} (nearest weekly)"


def _opt(side):
    return "CE" if side == "CALL" else "PE"


LOT = 65     # Nifty lot size (the journal uses the same)


def new_messages(res, sent, now, fresh_minutes=3, contracts=None, lot=LOT):
    """res: stream_trades.run_trades() result. sent: set of keys already sent (updated here). now: tz-aware pandas Timestamp (IST).
    Events older than `fresh_minutes` are marked as sent without a message (a restart in mid-session or a replayed day must not flood the chat).
    contracts: {(strike, "CE" / "PE"): {"symbol", "expiry"}} (the Fyers contract the page priced), used for the order ticket line.
    Returns [text, ...] in time order."""
    out = []

    def fresh(t):
        return t is not None and (now - t) <= pd.Timedelta(minutes=fresh_minutes)

    def add(key, t, text):
        if key in sent:
            return
        sent.add(key)
        if fresh(t):
            out.append((t, text))

    for tr in res.get("trades", []):
        n = tr["n"]
        add(f"T{n}open", tr["entry_time"],
            f"TRADE {n} (paper): {tr['side']} {tr['strike']} {_opt(tr['side'])} on {tr['level']}\nentry {_hm(tr['entry_time'])} at {tr['entry']:.2f}  "
            f"target {tr['target']:.2f} (+{tr.get('tp_pct', 40)}%)  stop {tr['stop']:.2f} (-{tr.get('sl_pct', 10)}%)\n"
            f"ticket: BUY 1 lot ({lot}) {_contract(contracts, tr)} | reference price {tr['entry']:.2f} (about Rs {tr['entry'] * lot:,.0f} for the lot) | "
            f"stop-loss order {tr['stop']:.2f} | target {tr['target']:.2f}")
        for x in tr.get("shifts", []):                      # the stop moved up: change your stop-loss order at the broker
            why = x["why"] if x.get("why") else f"the premium reached +{round(x['reach'] * 100)}%"
            add(f"T{n}sl{_hm(x['time'])}{round(x['lock'] * 100)}", x["time"],
                f"STOP MOVED, trade {n} ({tr['side']} {tr['strike']} {_opt(tr['side'])}): change your stop-loss order to {x['stop']:.2f} "
                f"({round(x['lock'] * 100):+d}% of the entry price) because {why}. Effective from the next candle.")
        if tr.get("exit_time") is not None:
            add(f"T{n}close", tr["exit_time"],
                f"TRADE {n} closed: {tr['status']} {tr['pnl_pct']:+.1f}%  ({tr['side']} {tr['strike']} {_opt(tr['side'])}, out {_hm(tr['exit_time'])} at {tr['exit']:.2f})\n"
                f"EXIT: if you are still in this position, exit it now (the {tr['status']} level was touched; the page reads 1-minute candles, so your own stop-loss order may already have filled).")
    for s in res.get("signals", []):
        if s["status"].startswith("TRADE"):
            continue
        # the key leaves out every number of the level text: the VWAP value drifts a few paise each minute and would send the same setup again and again
        waiting = s["status"].startswith("waiting")
        key = f"{'W' if waiting else 'S'}{_hm(s['time'])}{s['side']}{re.sub(r'[0-9.]+', '', s['level'])}"
        if waiting:                                         # a setup that has not triggered yet: you need the price to watch for
            add(key, s["time"], f"SETUP WAITING, {s['side']} {s['strike']} {_opt(s['side'])} on {s['level']} at {_hm(s['time'])}: {s['status'][len('waiting: '):]} "
                                f"({_contract(contracts, s)}), within 5 minutes. Not a trade until it crosses.")
        else:
            add(key, s["time"], f"Setup not taken ({s['status']}): {s['side']} {s['strike']} {_opt(s['side'])} on {s['level']} at {_hm(s['time'])}")
    out.sort(key=lambda x: x[0])
    return [t for _, t in out]
