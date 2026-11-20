"""A SIMPLE, shareable version of the trading journal: one static HTML file (no scripts, no network), small enough to upload to GitHub.

It shows only results: the overall figures, one row per day, the results by level and signal type (winners total vs losers total, in rupees on one lot), and the list of
trades. It leaves out everything private or heavy: your notes, the charts, the setups, the King and node analysis, the option candles, and your capital.

  python pipeline/journal_simple.py                      write data/journal_simple.html (local, not published)
  python pipeline/journal_simple.py --out docs/journal.html     write it where you want it (for example a folder you upload)

Rupees = (exit - entry) x lot (65), no brokerage or slippage. Paper trades only, a record of what the rules did, not advice."""
import argparse
import html
import pathlib
import re
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import journal_dashboard as J  # noqa: E402

OUT = J.ROOT / "data" / "journal_simple.html"


def _rs(x):
    if x is None:
        return "&ndash;"
    r = round(x)
    return ("+" if r > 0 else "&minus;" if r < 0 else "") + "&#8377;" + f"{abs(r):,}"


def _pct(x):
    return "&ndash;" if x is None else ("+" if x > 0 else "&minus;" if x < 0 else "") + f"{abs(x):.1f}%"


def _cls(x):
    return "" if x is None or x == 0 else "up" if x > 0 else "dn"


def _trade_rows(days, lot):
    out = []
    for d in days:
        for t in d["trades"]:
            setup = t.get("setup") or "?"
            m = re.search(r"\(([^)]*)\)", setup)
            level = re.sub(r"\s*\([^)]*\)", "", re.sub(r"\s[\d.]+", "", setup)).strip() or "?"
            rs = (t["exit"] - t["entry"]) * lot if t.get("exit") is not None and t.get("entry") is not None else None
            out.append({"day": d["date"], "n": t["n"], "side": t["side"], "level": level, "how": m.group(1) if m else "level", "in": t.get("entry_time"), "out": t.get("exit_time"),
                        "result": t.get("result") or "OPEN", "pct": t.get("pnl_pct"), "rs": rs})
    return out


def _stat(rows):
    w = [r for r in rows if (r["pct"] or 0) > 0]
    lo = [r for r in rows if (r["pct"] or 0) <= 0]
    return {"n": len(rows), "w": len(w), "wrs": sum(r["rs"] or 0 for r in w), "lrs": sum(r["rs"] or 0 for r in lo), "net": sum(r["rs"] or 0 for r in rows), "pct": sum(r["pct"] or 0 for r in rows)}


def _grp_rows(rows, key):
    g = {}
    for r in rows:
        g.setdefault(r[key], []).append(r)
    return sorted(g.items(), key=lambda kv: -_stat(kv[1])["net"])


def _line(label, rows, bold=False):
    s = _stat(rows)
    win = round(s["w"] / s["n"] * 100) if s["n"] else 0
    b = ' style="font-weight:700"' if bold else ""
    return (f'<tr{b}><td>{html.escape(str(label))}</td><td class="num">{s["n"]}</td><td class="num">{win}%</td><td class="num up">{_rs(s["wrs"])}</td><td class="num dn">{_rs(s["lrs"])}</td>'
            f'<td class="num {_cls(s["net"])}">{_rs(s["net"])}</td><td class="num {_cls(s["pct"])}">{_pct(s["pct"])}</td></tr>')


CSS = """:root{--bg:#f4f6fa;--card:#fff;--line:#d5dbe4;--txt:#111827;--mut:#5b6678;--up:#0f9d6c;--dn:#d93f3f}
@media (prefers-color-scheme:dark){:root{--bg:#0b0f16;--card:#121a26;--line:#243044;--txt:#e6ebf3;--mut:#8b97a8;--up:#2ecc9a;--dn:#ef5b5b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,"Segoe UI",Arial,sans-serif}.wrap{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:14px;margin:22px 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}.sub{color:var(--mut);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:14px}.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.tile .k{color:var(--mut);font-size:12px}.tile .v{font-size:22px;font-weight:700}.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 10px;overflow-x:auto}
table{width:100%;border-collapse:collapse}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:13.5px;white-space:nowrap}th{color:var(--mut);font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}.up{color:var(--up)}.dn{color:var(--dn)}.foot{color:var(--mut);font-size:12.5px;margin:8px 2px}
td.sec{color:var(--mut);padding-top:12px}"""


def build_html(folder=None):
    cfg = J.load_settings()
    lot = cfg["lot"]
    days = J.load_days(folder or J.FOLDER, cfg.get("from"))
    rows = _trade_rows(days, lot)
    tot = _stat(rows)
    first, last = (days[0]["date"], days[-1]["date"]) if days else ("", "")
    tiles = [("Net result (one lot)", _rs(tot["net"]), _cls(tot["net"])), ("Winners total", _rs(tot["wrs"]), "up"), ("Losers total", _rs(tot["lrs"]), "dn"),
             ("Trades", str(tot["n"]), ""), ("Win rate", f'{round(tot["w"] / tot["n"] * 100) if tot["n"] else 0}%', "")]
    h = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Trading Journal</title><style>{CSS}</style></head><body><div class="wrap">']
    h.append(f'<h1>Trading Journal</h1><div class="sub">Paper trades of the BharatPulse rules, {html.escape(first)} to {html.escape(last)}. One lot of {lot}; rupees are (exit &minus; entry) &times; lot, '
             f'before brokerage and slippage. A record of what the rules did, not advice. Updated {date.today().isoformat()}.</div>')
    h.append('<div class="tiles">' + "".join(f'<div class="tile"><div class="k">{k}</div><div class="v num {c}">{v}</div></div>' for k, v, c in tiles) + "</div>")
    h.append('<h2>Results by level</h2><div class="panel"><table><thead><tr><th>Group</th><th class="num">Trades</th><th class="num">Win</th><th class="num">Winners total</th><th class="num">Losers total</th>'
             '<th class="num">Net</th><th class="num">Sum of %</th></tr></thead><tbody>' + _line("All trades", rows, True))
    h.append('<tr><td class="sec" colspan="7">By level</td></tr>' + "".join(_line(k, v) for k, v in _grp_rows(rows, "level")))
    h.append('<tr><td class="sec" colspan="7">By signal type</td></tr>' + "".join(_line(k, v) for k, v in _grp_rows(rows, "how")) + "</tbody></table></div>")
    h.append('<h2>By day</h2><div class="panel"><table><thead><tr><th>Date</th><th class="num">Trades</th><th class="num">Winners</th><th class="num">Result</th><th class="num">Sum of %</th></tr></thead><tbody>')
    for d in reversed(days):
        r = [x for x in rows if x["day"] == d["date"]]
        s = _stat(r)
        h.append(f'<tr><td>{d["date"]}</td><td class="num">{s["n"]}</td><td class="num">{s["w"]}</td><td class="num {_cls(s["net"])}">{_rs(s["net"])}</td><td class="num {_cls(s["pct"])}">{_pct(s["pct"])}</td></tr>')
    h.append("</tbody></table></div>")
    h.append('<h2>Every trade</h2><div class="panel"><table><thead><tr><th>Date</th><th>#</th><th>Side</th><th>Level</th><th>Signal</th><th>In</th><th>Out</th><th>Ended</th><th class="num">Option</th><th class="num">One lot</th></tr></thead><tbody>')
    for x in reversed(rows):
        h.append(f'<tr><td>{x["day"]}</td><td class="num">{x["n"]}</td><td>{html.escape(x["side"])}</td><td>{html.escape(x["level"])}</td><td>{html.escape(x["how"])}</td><td>{html.escape(str(x["in"] or ""))}</td>'
                 f'<td>{html.escape(str(x["out"] or ""))}</td><td>{html.escape(x["result"])}</td><td class="num {_cls(x["pct"])}">{_pct(x["pct"])}</td><td class="num {_cls(x["rs"])}">{_rs(x["rs"])}</td></tr>')
    h.append('</tbody></table></div><div class="foot">Rules: one rule set all day, target +40%, stop &minus;10% that trails up (+20% &rarr; +5%, +30% &rarr; +15%, +35% &rarr; +30%); a trade against the session trend in negative gamma is skipped; '
             'no trade before 09:25 or after 14:45; at most 5 trades a day; one trade at a time. Educational use only; not investment advice.</div></div></body></html>')
    return "".join(h)


def write_file(out=None, folder=None):
    f = pathlib.Path(out) if out else OUT
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(build_html(folder), encoding="utf-8")
    return f


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="where to write the file (default data/journal_simple.html)")
    a = ap.parse_args()
    print("written:", write_file(a.out))


if __name__ == "__main__":
    main()
