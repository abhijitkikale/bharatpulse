"""Live (intraday) snapshot for the dashboard: today's developing volume profile from Fyers 1-minute index candles.

build_live() is pure (candles in, small JSON-able dict out) so it can be tested without the network. tools/live_loop.py calls it
every 30 minutes and publishes the result; the page reads it and overlays it on the end-of-day numbers."""
import compute_options

BIN = {"NIFTY": 5, "BANKNIFTY": 10}          # volume-profile row size in index points, as in the end-of-day build


def build_live(frames, now):
    """frames: {sym: DataFrame(o, h, l, c, v) on an IST index}; now: tz-aware Timestamp (IST). Returns None if no symbol has candles
    for today's session (holiday, before the open, or a stale token)."""
    today = now.date()
    out = {}
    for sym, df in frames.items():
        if df.empty or df.index.max().date() != today:
            continue
        vp, sessions = compute_options.intraday_block(df, BIN.get(sym, 5))
        if not vp.get("1") or not sessions or sessions[-1]["date"] != str(today):
            continue
        out[sym] = {"spot": round(float(df["c"].iloc[-1]), 2), "last": df.index.max().strftime("%H:%M"), "vp": vp, "session": sessions[-1]}
    if not out:
        return None
    return {"v": 1, "asof": now.isoformat(timespec="seconds"), "date": str(today), "symbols": out}
