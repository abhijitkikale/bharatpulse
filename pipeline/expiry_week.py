"""Numbers for the journal's "Expiry week" tab: one connected chart per weekly-expiry cycle (Wednesday to Tuesday). Pure functions, no network.

  expiry_of(day)            the Tuesday that ends the cycle a trading day belongs to (Nifty weekly expiry is Tuesday)
  vwap_series(rows)         per 1-minute candle (VWAP, upper, lower): daily VWAP and +/- 1 standard deviation on hlc3, restarting each day,
                            the same maths as the stream page (stream_levels.vwap_bands)
  node_series(readings)     per node reading [HH:MM, King size, King sign, net gamma of the whole map]: what the strength strip draws"""
import datetime as dt

import nodes


def expiry_of(day):
    """'2026-11-27' -> '2026-12-01'. A Wednesday starts a new cycle; Tuesday is the last day of its own."""
    d = dt.date.fromisoformat(day)
    return (d + dt.timedelta(days=(1 - d.weekday()) % 7)).isoformat()


def vwap_series(rows):
    """rows: the journal's index candles [HH:MM, o, h, l, c, v]. Returns [[vwap, upper, lower] or None, ...] aligned to the rows, 2 decimals.
    Candles with no volume add nothing; before the first candle with volume the entry is None."""
    out, cv, cpv, cpv2 = [], 0.0, 0.0, 0.0
    for r in rows:
        try:
            v = float(r[5] or 0)
            tp = (float(r[2]) + float(r[3]) + float(r[4])) / 3
        except (TypeError, ValueError, IndexError):
            v, tp = 0.0, 0.0
        if v > 0:
            cv += v
            cpv += tp * v
            cpv2 += tp * tp * v
        if cv <= 0:
            out.append(None)
            continue
        vw = cpv / cv
        sd = max(cpv2 / cv - vw * vw, 0.0) ** 0.5
        out.append([round(vw, 2), round(vw + sd, 2), round(vw - sd, 2)])
    return out


def weekly_vwap(days):
    """days: [(date 'YYYY-MM-DD', index candles [HH:MM, o, h, l, c, v]), ...] oldest first. Returns {date: [weekly VWAP or None per candle]}: the VWAP on hlc3 that restarts
    each ISO week (Monday), the yellow line of the live dashboard (stream_levels.vwaps)."""
    out, cur, cv, cpv = {}, None, 0.0, 0.0
    for day, rows in days:
        wk = dt.date.fromisoformat(day).isocalendar()[:2]
        if wk != cur:
            cur, cv, cpv = wk, 0.0, 0.0
        res = []
        for r in rows:
            try:
                v = float(r[5] or 0)
                tp = (float(r[2]) + float(r[3]) + float(r[4])) / 3
            except (TypeError, ValueError, IndexError):
                v, tp = 0.0, 0.0
            if v > 0:
                cv += v
                cpv += tp * v
            res.append(round(cpv / cv, 2) if cv > 0 else None)
        out[day] = res
    return out


def node_history(readings, shift=19800):
    """The live chart's bubble rows, one entry per saved node reading: [[time, [[strike, % of King, sign +1/-1, King 0/1, role letter K/G/C/F or ""], ...]], ...]
    (only the nodes the chart shows). The same rows as stream_server.nodes_history_json; time is the chart axis (IST written as UTC)."""
    out = []
    for r in readings:
        cl = nodes.classify({"k": r["k"], "net": r["n"]}, r["spot"])
        rows = [[n["k"], round(n["pct"]), 1 if n["sign"] == "+" else -1, 1 if "king" in n["roles"] else 0,
                 next((c for r_, c in (("king", "K"), ("gatekeeper", "G"), ("ceiling", "C"), ("floor", "F")) if r_ in n["roles"]), "")]
                for n in cl["nodes"] if n["pct"] >= 40 or any(x in n["roles"] for x in ("king", "gatekeeper", "floor", "ceiling"))]
        out.append([int(r["t"].timestamp()) + shift, rows])
    return out


def node_series(readings):
    """readings: nodes_ledger.load_readings(). Returns [[HH:MM, |King net gamma| (crore), '+'|'-', total net gamma (crore)], ...]."""
    out = []
    for r in readings:
        cl = nodes.classify({"k": r["k"], "net": r["n"]}, r["spot"])
        k = cl["structure"].get("king")
        if k is None:
            continue
        kn = [n for n in cl["nodes"] if "king" in n["roles"]]
        if not kn:
            continue
        out.append([r["t"].strftime("%H:%M"), round(abs(kn[0]["net"]), 1), kn[0]["sign"], round(float(r.get("total_cr") or 0.0), 1)])
    return out
