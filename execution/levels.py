"""Stop, target and trailing measured from the REAL fill price, and the check against a live price. Pure functions, no network, no broker.

Why: the dashboard's paper trade measures its 10% stop from ITS entry price (the trigger or the setup candle's high). The order Automate places is a market order that fills a few
seconds later, often higher or lower. A stop copied from the dashboard then sits at a different percentage from the price you actually paid (26 Nov: a 13% stop instead of 10%).
Here the same percentages (sl_pct / tp_pct of the dashboard's trade) are applied to the real fill, and the stop trails up the same way (TRAIL below, a copy of
pipeline/stream_trades.TRAIL: a test keeps the two equal). Anything the dashboard raises on top (the gamma-regime tightening) is mirrored as a percentage of the real fill."""

TRAIL = ((0.35, 0.30), (0.30, 0.15), (0.20, 0.05))     # once the premium has reached +35% / +30% / +20% of the fill the stop moves up to +30% / +15% / +5% (never down)
DEFAULT_SL, DEFAULT_TP = 10.0, 40.0                     # percent: used only when the dashboard's trade does not carry its own


def initial(fill, sl_pct=None, tp_pct=None):
    """{"fill", "stop", "target", "sl_pct", "tp_pct"} for a fill price; the percentages are the dashboard trade's own."""
    sl = float(sl_pct) if sl_pct not in (None, "") else DEFAULT_SL
    tp = float(tp_pct) if tp_pct not in (None, "") else DEFAULT_TP
    fill = float(fill)
    return {"fill": round(fill, 2), "stop": round(fill * (1 - sl / 100), 2), "target": round(fill * (1 + tp / 100), 2), "sl_pct": sl, "tp_pct": tp}


def dash_lock(it):
    """The dashboard's raised stop as a fraction of ITS entry (e.g. 0.05 for 'stop at +5%'), or None while its stop is still the initial one."""
    try:
        if it.stop_now > it.stop + 1e-9 and it.entry > 0:
            return it.stop_now / it.entry - 1.0
    except (AttributeError, TypeError, ZeroDivisionError):
        pass
    return None


def ratchet(lv, price=None, lock=None):
    """Returns (new levels, why or None). The stop only moves up: to the trailing level the price has reached, and to the dashboard's own raised stop (lock) applied to the real fill."""
    fill, stop = lv["fill"], lv["stop"]
    new, why = stop, None
    if price is not None:
        for reach, lk in TRAIL:                                # the first (highest) level reached counts
            if price >= fill * (1 + reach):
                cand = round(fill * (1 + lk), 2)
                if cand > new:
                    new, why = cand, f"the option reached {reach * 100:+.0f}%, stop moved to {lk * 100:+.0f}% of your fill"
                break
    if lock is not None:
        cand = round(fill * (1 + lock), 2)
        if cand > new:
            new, why = cand, f"the dashboard raised its stop, mirrored on your fill: {lock * 100:+.0f}%"
    if new > stop:
        return {**lv, "stop": new}, why
    return lv, None


def check(lv, price):
    """None while the price is between the stop and the target, else (kind, text): kind STOP (below the initial stop level), TRAIL (a raised stop, a profit locked in) or TARGET."""
    fill, stop, target = lv["fill"], lv["stop"], lv["target"]
    if price <= stop:
        kind = "STOP" if stop < fill else "TRAIL"
        return kind, f"{kind}: the option fell to {price:.2f}, at or below your stop {stop:.2f} ({(stop / fill - 1) * 100:+.1f}% of your fill {fill:.2f})"
    if price >= target:
        return "TARGET", f"TARGET: the option rose to {price:.2f}, at or above your target {target:.2f} ({(target / fill - 1) * 100:+.1f}% of your fill {fill:.2f})"
    return None
