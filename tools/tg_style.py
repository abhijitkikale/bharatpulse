"""How the Telegram messages look, and who gets them. Pure functions, no network (tools/telegram_notify.py sends them).

Colours by message type (Telegram has no coloured text, so the coloured circle at the start of the first line is the colour code):
  blue   LIVE GEX levels        orange  TRADE TAKEN (entry, target, stop-loss)
  green  TARGET HIT             red     STOP-LOSS HIT          yellow  a trade closed some other way
  white  setup not taken (only for you, never for the channel)

The friends' channel gets the blue, orange, green, red and yellow messages. Everything else (setups not taken, system warnings, the video,
the raw level text for the indicator) stays in your own chat only."""
import html
import re

PUBLIC = {"gex", "trade_open", "trade_target", "trade_stop", "trade_closed"}
FOOTER = "\n<i>Paper trades and live levels for education. Not investment advice.</i>"

_OPEN = re.compile(r"^TRADE (\d+) \(paper\): (CALL|PUT) (\d+) (CE|PE) on (.+)\nentry (\d\d:\d\d) at ([\d.]+)\s+target ([\d.]+) \(\+(\d+)%\)\s+stop ([\d.]+) \(-(\d+)%\)", re.S)
_CLOSE = re.compile(r"^TRADE (\d+) closed: (\w+) ([+-][\d.]+)%\s+\((CALL|PUT) (\d+) (CE|PE), out (\d\d:\d\d) at ([\d.]+)\)")


def classify(text):
    """-> kind: gex | trade_open | trade_target | trade_stop | trade_closed | setup_skip | other"""
    t = str(text)
    if _OPEN.match(t):
        return "trade_open"
    m = _CLOSE.match(t)
    if m:
        return {"TARGET": "trade_target", "STOP": "trade_stop"}.get(m.group(2), "trade_closed")
    if t.startswith("Setup not taken"):
        return "setup_skip"
    if t.startswith("# BharatPulse") and " LIVE " in t.splitlines()[0]:
        return "gex"
    return "other"


def is_public(kind):
    return kind in PUBLIC


def _sign(x):
    return x.replace("-", "\u2212")


def to_html(text):
    """(kind, html text or None). None means: send the text as it is (the plain monospace message)."""
    t = str(text)
    kind = classify(t)
    m = _OPEN.match(t)
    if m:
        n, side, strike, opt, level, hm, entry, target, tp_pct, stop, sl_pct = m.groups()
        body = (f"\U0001F7E0 <b>TRADE {n} TAKEN</b> \u00b7 paper\n<b>{side} {strike} {opt}</b> on {html.escape(level)}\n"
                f"Entry <b>{entry}</b> at {hm}\n\U0001F3AF Target <b>{target}</b> (+{tp_pct}%)\n\U0001F6D1 Stop-loss <b>{stop}</b> (\u2212{sl_pct}%)")
        return kind, body + FOOTER
    m = _CLOSE.match(t)
    if m:
        n, status, pnl, side, strike, opt, hm, out = m.groups()
        if kind == "trade_target":
            head = f"\U0001F7E2 <b>TARGET HIT</b> \u00b7 trade {n}  <b>{_sign(pnl)}%</b>"
        elif kind == "trade_stop":
            head = f"\U0001F534 <b>STOP-LOSS HIT</b> \u00b7 trade {n}  <b>{_sign(pnl)}%</b>"
        elif status == "TRAIL":
            head = f"\U0001F7E2 <b>TRAILING STOP HIT</b> · trade {n}  <b>{_sign(pnl)}%</b>  (profit locked)"
        else:
            head = f"\U0001F7E1 <b>TRADE {n} CLOSED</b> ({html.escape(status)})  <b>{_sign(pnl)}%</b>"
        return kind, head + f"\n{side} {strike} {opt} \u00b7 out {hm} at {out}" + FOOTER
    if kind == "setup_skip":
        return kind, "\u26AA " + html.escape(t)
    return kind, None


def gex_html(name, block, now):
    """The live GEX message for the channel (blue). block: one index block of live_gex.build_gex(); now: IST timestamp."""
    lv = block.get("levels", {})
    rows = [("Gamma flip", lv.get("flip")), ("GEX call wall", lv.get("gex_call")), ("GEX put wall", lv.get("gex_put")),
            ("OI call wall", lv.get("oi_call")), ("OI put wall", lv.get("oi_put")), ("Max pain", lv.get("pain"))]
    lines = [f"{label}: <b>{v:g}</b>" for label, v in rows if v is not None]
    pos = block.get("net_cr", 0) > 0
    regime = ("positive gamma: moves tend to be dampened" if pos else "negative gamma: moves tend to be amplified")
    head = f"\U0001F535 <b>LIVE GEX \u00b7 {html.escape(name)}</b>  {now:%H:%M} IST\nSpot <b>{block['spot']:.2f}</b> \u00b7 expiry {block['expiry']}\n"
    return head + "\n".join(lines) + f"\n<i>{regime}</i>" + FOOTER
