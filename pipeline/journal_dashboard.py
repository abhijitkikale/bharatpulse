"""The trading journal as one self-contained page (data/journal_dashboard.html): every day in data/journal/ with its paper trades, the setups that were
taken or ignored, the index chart with the levels, and the option price of each trade. Private: it is a local file, nothing is uploaded.

  python pipeline/journal_dashboard.py          write data/journal_dashboard.html
  python pipeline/journal_dashboard.py --open   write it and open it in the browser

The live stream page also serves it at http://127.0.0.1:8766/journal, and every journal save rewrites the file, so it is always current."""
import argparse
import json
import pathlib
import sys
import time
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import expiry_week  # noqa: E402
ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDER = ROOT / "data" / "journal"
NODES = ROOT / "data" / "nodes"
REGIME_DIR = ROOT / "data" / "stream_regime"          # the live gamma-flip readings the trade rules used, one file per day
NOTES = ROOT / "data" / "journal_notes.json"          # {"2026-11-17": ["a paragraph", ...]}: your own notes, shown under that day's King analysis
OUT = ROOT / "data" / "journal_dashboard.html"
SETTINGS = ROOT / "data" / "journal_settings.json"     # {"capital": 25000, "lot": 65}: rupee results = (exit - entry) x lot; Nifty lot is 65 (NSE circular FAOP70616, from Jan 2026)
DEFAULTS = {"capital": 25000, "lot": 65, "from": "2026-11-11"}      # "from": the first day the page shows; earlier journal files stay on disk, they are only left out


def regime_bands(day, candles, reg_dir=None):
    """[[from HH:MM, to HH:MM, 'N'|'P'], ...]: the gamma regime the dashboard showed through the day (the live flip reading in force and which side of it the index close was on),
    merged into bands. Empty when that day has no readings."""
    try:
        rd = json.loads((pathlib.Path(reg_dir or REGIME_DIR) / f"{day}.json").read_text(encoding="utf-8"))
        rd = sorted(([r["time"][11:16], r] for r in rd), key=lambda x: x[0])
    except (OSError, ValueError, KeyError):
        return []
    out, i, cur = [], -1, None
    for c in candles:
        while i + 1 < len(rd) and rd[i + 1][0] <= c[0]:
            i += 1
        if i < 0:
            continue
        r = rd[i][1]
        pos = bool(r.get("pos")) if r.get("flip") is None or r.get("pos_above") is None else ((c[3] > r["flip"]) == r["pos_above"])
        ch = "P" if pos else "N"
        if cur and cur[2] == ch:
            cur[1] = c[0]
        else:
            cur = [c[0], c[0], ch]
            out.append(cur)
    return out


def load_days(folder=FOLDER, start=None, reg_dir=None):
    days, raw = [], []
    for f in sorted(pathlib.Path(folder).glob("????-??-??.json")):
        if start and f.stem < start:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        trades = []
        for t in d.get("trades", []):
            trades.append({k: t.get(k) for k in ("n", "side", "strike", "expiry", "setup", "entry_time", "entry", "target", "stop", "exit_time", "exit", "result", "pnl_pct")}
                          | {"shifts": t.get("stop_shifts") or [], "ignored": len(t.get("setups_ignored_while_open") or []), "opt": [[r[0], r[4]] for r in (t.get("option_candles_hm_ohlcv") or [])]})
        days.append({
            "date": d["date"], "symbol": d.get("symbol", "NIFTY"), "total": d.get("total_pct"), "trades": trades,
            "setups": d.get("setups", []), "candles": [[r[0], r[2], r[3], r[4], r[1]] for r in d.get("index_candles_hm_ohlcv", [])],
            "levels": (d.get("levels_used") or {}).get("levels", {}), "close": (d.get("levels_used") or {}).get("close"),
            "opening": d.get("opening_candles"), "saved": d.get("saved_at"),
        })
        days[-1]["regime"] = regime_bands(d["date"], days[-1]["candles"], reg_dir)
        days[-1]["exp"] = expiry_week.expiry_of(d["date"])
        days[-1]["vw"] = expiry_week.vwap_series(d.get("index_candles_hm_ohlcv", []))        # [VWAP, +1 SD, -1 SD] per candle, for the Expiry week tab
        raw.append((d["date"], d.get("index_candles_hm_ohlcv", [])))
    wk = expiry_week.weekly_vwap(raw)
    for dd in days:
        dd["vw"] = [[q[0], q[1], q[2], w] if q else ([None, None, None, w] if w is not None else None) for q, w in zip(dd["vw"], wk.get(dd["date"], []))]       # + the weekly VWAP
    return days


def load_notes(path=NOTES):
    try:
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return {k: ([v] if isinstance(v, str) else list(v)) for k, v in d.items()}
    except (OSError, ValueError, AttributeError):
        return {}


_KING_CACHE = {}


def king_data(nodes_dir, folder, start=None):
    """{"days": {date: analysis}, "pull": summary of the King pull scoreboard} from the nodes agent's saved days; empty when there are none (or on any problem)."""
    key = (str(nodes_dir), str(folder), start, tuple((f.name, f.stat().st_size, f.stat().st_mtime_ns) for d_ in (nodes_dir, folder) if pathlib.Path(d_).exists() for f in sorted(pathlib.Path(d_).glob("????-??-??.json*"))))
    c = _KING_CACHE
    if c.get("key") == key or (c.get("key") and c["key"][:3] == key[:3] and time.time() - c["at"] < 120):
        return c["val"]                                             # nothing changed (or the last analysis is under 2 minutes old): do not redo it at every journal save
    try:
        import king_journal
        import nodes_pull
        days = {d: a for d, a in king_journal.all_days(nodes_dir, folder).items() if not start or d >= start}
        pull = nodes_pull.build_summary(nodes_dir, folder) if days else None
        if pull:
            pull = {"verdict": pull["verdict"], "days": pull["days"], "horizons": {h: {"n": v["n"], "independent": v["independent"], "rows": v["rows"]} for h, v in pull["horizons"].items()}}
        _KING_CACHE.update(key=key, at=time.time(), val={"days": days, "pull": pull})
        return _KING_CACHE["val"]
    except Exception:           # the journal must always build, with or without the King analysis
        return {"days": {}, "pull": None}


def _node_strip(nodes_dir, day):
    """(King size / net gamma per reading, the live chart's bubble rows) for one day, for the Expiry week tab; ([], []) when that day has no readings or on any problem."""
    try:
        import nodes_ledger
        f = pathlib.Path(nodes_dir) / f"{day}.jsonl"
        if not f.exists():
            return [], []
        rd = nodes_ledger.load_readings(f)
        return expiry_week.node_series(rd), expiry_week.node_history(rd)
    except Exception:
        return [], []


def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return s


def build_html(folder=FOLDER, nodes_dir=NODES, notes=NOTES, king_test_dir=None, regime_dir=None):
    cfg = load_settings()
    days = load_days(folder, cfg.get("from"), regime_dir)
    kd, nt = king_data(nodes_dir, folder, cfg.get("from")), load_notes(notes)
    kt, ktsum = {}, None
    try:                                                   # the King trade TEST (pipeline/king_test.py): its own files, never part of the paper-trade figures
        import king_test
        kt = king_test.load_all(king_test_dir or king_test.DIR)
        ktsum = king_test.summary(kt)
    except Exception:
        kt = {}
    for d in days:
        d["king"] = kd["days"].get(d["date"])
        d["notes"] = nt.get(d["date"], [])
        d["ktest"] = kt.get(d["date"])
        d["kx"], d["nh"] = _node_strip(nodes_dir, d["date"])
    data = json.dumps({"days": days, "settings": cfg, "pull": kd["pull"], "ktest": ktsum}, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", data)


def write_file(folder=FOLDER, out=OUT, nodes_dir=NODES, notes=NOTES):
    out = pathlib.Path(out)
    out.write_text(build_html(folder, nodes_dir, notes), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Journal</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>
/* layout: a day strip on top, a summary band, then the chosen day: chart, trade cards, setups; all days at the bottom */
:root{--bg:#f4f6f9;--panel:#ffffff;--line:#dde3ec;--fg:#16202e;--mut:#667489;--up:#12a36b;--dn:#e0394d;--amb:#c98512;--vio:#7a55d6;--acc:#0e8f9a;--soft:#eef2f7;--ff:'Inter',system-ui,sans-serif;--fh:'Space Grotesk','Inter',system-ui,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0a0e15;--panel:#111826;--line:#1f2a3d;--fg:#e6edf3;--mut:#8b97a8;--up:#2ccf94;--dn:#ff6174;--amb:#f5b13d;--vio:#b79cff;--acc:#38d0dd;--soft:#162033;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0a0e15;--panel:#111826;--line:#1f2a3d;--fg:#e6edf3;--mut:#8b97a8;--up:#2ccf94;--dn:#ff6174;--amb:#f5b13d;--vio:#b79cff;--acc:#38d0dd;--soft:#162033;color-scheme:dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
.wrap>*{min-width:0}
#home,#dayview,#weekview{margin:0;width:100%}
body.wide .wrap{max-width:none}
body.wide header .sub{max-width:1180px}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 var(--ff);padding:20px max(16px,env(safe-area-inset-right,0px)) 48px max(16px,env(safe-area-inset-left,0px))}
.wrap{max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:18px}
h1{font:700 26px/1.1 var(--fh);margin:0;letter-spacing:-.01em}
h2{font:700 15px/1.2 var(--fh);margin:0 0 10px;letter-spacing:.02em}
.sub{color:var(--mut);margin-top:4px}
.num{font-variant-numeric:tabular-nums}
.up{color:var(--up)}.dn{color:var(--dn)}
header{display:flex;flex-wrap:wrap;gap:12px 24px;align-items:flex-end;justify-content:space-between}
.tabs{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;max-width:100%}
.tab{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:10px;padding:8px 12px;cursor:pointer;font:inherit;min-width:104px;text-align:left}
.tab small{display:block;color:var(--mut);font-size:11px}
.tab b{font:600 14px var(--fh)}
.tab[aria-selected="true"]{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc) inset}
.tab:focus-visible,.row:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.band{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.tile .v{font:700 24px/1.2 var(--fh);margin-top:2px}
.tile .n{color:var(--mut);font-size:12px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;min-width:0}
.dayhead{display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;justify-content:space-between}
.dayhead .t{font:700 22px var(--fh)}
.pills{display:flex;flex-wrap:wrap;gap:6px}
.pill{border:1px solid var(--line);background:var(--soft);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--mut)}
.pill.warn{border-color:var(--amb);color:var(--amb)}
.chartbox{overflow-x:auto}
svg text{font-family:var(--ff);fill:var(--mut);font-size:11px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.card{border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:8px;min-width:0}
.card .top{display:flex;justify-content:space-between;align-items:center;gap:8px}
.card .name{font:600 15px var(--fh)}
.chip{display:inline-block;border-radius:6px;padding:1px 8px;font:600 12px var(--ff);border:1px solid currentColor}
.chip.call{color:var(--up)}.chip.put{color:var(--dn)}.chip.tgt{color:var(--up)}.chip.stp{color:var(--dn)}.chip.open{color:var(--amb)}.chip.mut{color:var(--mut)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:13px}
.kv span:nth-child(odd){color:var(--mut)}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:520px}
th{text-align:left;color:var(--mut);font:500 12px var(--ff);text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--line);padding:6px 8px}
td{padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
tr.taken td{background:var(--soft)}
.row{cursor:pointer}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--mut);font-size:12px;margin-top:6px}
.empty{color:var(--mut);padding:24px;text-align:center}
.two{display:grid;grid-template-columns:2fr 1fr;gap:12px}
.two>div,header>div{min-width:0}
.chartbox svg{min-width:680px}
@media (max-width:820px){.two{grid-template-columns:1fr}}
.foot{color:var(--mut);font-size:12px}

.nav{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:10px;padding:6px 12px;cursor:pointer;font:inherit}
.btn:disabled{opacity:.4;cursor:default}
.btn:focus-visible,.cell:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.mhead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px 20px;margin-bottom:12px}
.mhead .t{font:700 20px var(--fh)}
.cal{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px}
.dow{color:var(--mut);font:500 12px var(--ff);text-transform:uppercase;letter-spacing:.05em;padding:2px 4px}
.cell{min-height:86px;border:1px solid var(--line);border-radius:10px;padding:6px 8px;display:flex;flex-direction:column;gap:2px;background:var(--panel);text-align:left;font:inherit;color:var(--fg);min-width:0}
.cell.blank{border-color:transparent;background:transparent}
.cell .d{color:var(--mut);font-size:12px}
.cell .r{font:700 15px var(--fh)}
.cell .n{color:var(--mut);font-size:11px}
.cell.win{background:color-mix(in srgb,var(--up) 14%,var(--panel));border-color:color-mix(in srgb,var(--up) 45%,var(--line));cursor:pointer}
.cell.loss{background:color-mix(in srgb,var(--dn) 14%,var(--panel));border-color:color-mix(in srgb,var(--dn) 45%,var(--line));cursor:pointer}
.cell.flat{cursor:pointer}
.cell:disabled{cursor:default}
@media (max-width:640px){.cell{min-height:64px;padding:4px 5px}.cell .r{font-size:12px}.cell .n{display:none}}
.topnav{display:flex;gap:8px;flex-wrap:wrap}
.wtool{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin:10px 0}
.wtool select{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:10px;padding:6px 10px;font:inherit}
.lchip{border:1px solid var(--line);background:var(--panel);color:var(--mut);border-radius:999px;padding:2px 10px;cursor:pointer;font:inherit;font-size:12px}
.lchip[aria-pressed="true"]{color:var(--fg);border-color:var(--acc);box-shadow:0 0 0 1px var(--acc) inset}
.wrow{display:flex;align-items:stretch;min-width:0}
.wrow>svg.ax{flex:0 0 auto}
.wscroll{overflow-x:auto;flex:1 1 auto;min-width:0;position:relative}
.wscroll svg{display:block}
.wread{min-height:22px;font-size:12px;color:var(--fg);margin:6px 0}
.band.small{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
</style></head><body><div class="wrap">
<header><div><h1>Trading Journal</h1><div class="sub">Paper trades from the BharatPulse stream page. +40% target, &minus;10% stop that trails up (+20% &rarr; +5%, +30% &rarr; +15%, +35% &rarr; +30%); the same rules all day from 18 Nov (until 17 Nov, trades entered from 13:00 used a +20% target, &minus;7% stop and trailing +10% &rarr; +5%, +15% &rarr; +10%); in negative gamma a trade against the session trend is skipped; no trade in the first 10 minutes (before 9:25), one trade at a time, one lot of 65 on a &#8377;25,000 capital. A record of what the rules did, not advice. <a href="/nodes-replay" style="color:inherit">Node replay and reaction ledger</a> (opens when the stream page is running).</div></div><div class="topnav" id="topnav"><button class="tab" id="navhome" aria-selected="true"><b>Calendar &amp; days</b></button><button class="tab" id="navweek" aria-selected="false"><b>Expiry week</b><small>one connected chart, Wed to Tue</small></button></div></header>
<div id="home" class="wrap" style="gap:18px">
<section class="panel"><div class="mhead"><div class="nav"><button class="btn" id="prev" aria-label="Previous month">&lsaquo;</button><span class="t" id="mname"></span><button class="btn" id="next" aria-label="Next month">&rsaquo;</button></div><div id="msum" class="num"></div></div><div class="cal" id="cal"></div><div class="legend"><span>Each box is one trading day: result in rupees and number of trades. Click a day for its details.</span></div></section>
<section><h2>Overall</h2><div class="band small" id="stats"></div></section>
<section class="panel"><h2>Rules scoreboard <span class="sub" style="font-weight:400">testing: what each rule kept out, and how the taken trades did by grade</span></h2><div class="tablewrap" id="score"></div></section>
<section class="panel"><h2>Results by level <span class="sub" style="font-weight:400">which setup level and signal type earned and lost, in rupees (one lot) and %</span></h2><div class="tablewrap" id="bylevel"></div></section>
<section class="panel"><h2>King pull <span class="sub" style="font-weight:400">does price drift toward the King node? all journaled days together</span></h2><div id="pull"></div></section>
<section class="panel"><h2>King trade <span class="chip open">test</span> <span class="sub" style="font-weight:400">from the 19 Nov session, separate from the paper trades</span></h2><div id="ktest"></div></section>
<section class="panel"><h2>Result after each trade</h2><div class="chartbox" id="curve"></div></section>
<section class="panel"><h2>All days</h2><div class="tablewrap"><table id="all"></table></div></section>
</div>
<div id="weekview" class="wrap" style="gap:18px" hidden><section class="panel" id="week"></section></div>
<div id="dayview" class="wrap" style="gap:18px" hidden><div><button class="btn" id="back">&lsaquo; Calendar</button></div><section class="panel" id="day"></section></div>
<div class="foot" id="foot"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
const DATA = __DATA__;
const CAP = DATA.settings.capital, LOT = DATA.settings.lot;
const rs = x => x==null ? '&ndash;' : (x>0?'+':x<0?'&minus;':'') + '₹' + Math.abs(Math.round(x)).toLocaleString('en-IN');
const tradeRs = t => (t.exit!=null && t.entry!=null) ? (t.exit-t.entry)*LOT : null;
const dayRs = d => d.trades.reduce((s,t)=>s+(tradeRs(t)||0),0);
const days = DATA.days.slice().sort((a,b)=>a.date<b.date?1:-1);
const $ = id => document.getElementById(id);
const pct = x => x==null ? '&ndash;' : (x>0?'+':x<0?'&minus;':'') + Math.abs(x).toFixed(1) + '%';
const cls = x => x>0 ? 'up' : x<0 ? 'dn' : '';
const wk = d => new Date(d+'T12:00:00').toLocaleDateString('en-GB',{weekday:'short',day:'2-digit',month:'short'});
const mins = hm => { const [h,m]=hm.split(':').map(Number); return h*60+m-(9*60+15); };
const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function status(s){ if(s.startsWith('TRADE')) return ['Taken','tgt']; if(s.startsWith('ignored')) return ['Ignored: a trade was open','mut']; if(s.startsWith('no new trade')) return ['After the 14:45 cutoff','mut']; if(s.startsWith('not taken')) return [(s.includes('against the move')||s.includes('against the session trend'))?'Against the session trend in negative gamma'+(s.includes('trending up')?' (session up)':s.includes('trending down')?' (session down)':''):s.replace('not taken: ','Not taken: '),'mut']; if(s.startsWith('no trade before')) return ['In the first 10 minutes (no trade)','mut']; return [s,'mut']; }
function chart(d){
  const W=1000,H=320,L=56,R=84,T=14,B=26, n=375, iw=W-L-R, ih=H-T-B;
  const cs = d.candles; if(!cs.length) return '<div class="empty">No index candles were saved for this day.</div>';
  let lo=Math.min(...cs.map(c=>c[2])), hi=Math.max(...cs.map(c=>c[1]));
  const lv = Object.entries(d.levels).filter(([k,v])=>['GEX call wall','GEX put wall','POC','VAH','VAL','Gamma flip'].includes(k)&&v>lo-60&&v<hi+60);
  LVSHOWN = lv.length > 0;
  lv.forEach(([k,v])=>{lo=Math.min(lo,v);hi=Math.max(hi,v);});
  const pad=(hi-lo)*.05; lo-=pad; hi+=pad;
  const x=m=>L+m/n*iw, y=p=>T+(hi-p)/(hi-lo)*ih;
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Index price with trades and setups">`;
  const step = (hi-lo)>400?100:50;
  for(let p=Math.ceil(lo/step)*step;p<hi;p+=step) s+=`<line x1="${L}" x2="${W-R}" y1="${y(p)}" y2="${y(p)}" stroke="var(--line)"/><text x="${L-8}" y="${y(p)+4}" text-anchor="end">${p}</text>`;
  [0,60,120,180,240,300,360].forEach(m=>{const h=9*60+15+m; s+=`<text x="${x(m)}" y="${H-8}" text-anchor="middle">${String(Math.floor(h/60)).padStart(2,'0')}:${String(h%60).padStart(2,'0')}</text>`;});
  (d.regime||[]).forEach(([a,b,r])=>{ const xa=x(Math.max(mins(a),0)), xb=x(Math.min(mins(b)+1,n)); if(xb<=xa) return; const c=r==='N'?'var(--dn)':'var(--up)';
    s+=`<rect x="${xa}" y="${T}" width="${xb-xa}" height="${ih}" fill="${c}" opacity=".07"/><rect x="${xa}" y="${T}" width="${xb-xa}" height="5" fill="${c}" opacity=".85"><title>${r==='N'?'Negative':'Positive'} gamma ${a} to ${b}</title></rect>`; });
  d.trades.forEach(t=>{ if(!t.entry_time||!t.exit_time) return; const a=x(mins(t.entry_time)), b=Math.max(x(mins(t.exit_time)),a+3);
    s+=`<rect x="${a}" y="${T}" width="${b-a}" height="${ih}" fill="${(t.result==='TARGET'||t.result==='TRAIL')?'var(--up)':'var(--dn)'}" opacity=".13"><title>Trade ${t.n}: ${t.entry_time} to ${t.exit_time}, ${pct(t.pnl_pct)}</title></rect>`; });
  lv.forEach(([k,v])=>{ s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--vio)" stroke-dasharray="5 4" opacity=".7"/><text x="${W-R+6}" y="${y(v)+4}" style="fill:var(--vio)">${k} ${Math.round(v)}</text>`; });
  if(d.king) d.king.king_steps.forEach(([a,b,k,sg])=>{ if(k<lo||k>hi) return; const xa=x(Math.max(mins(a),0)), xb=x(Math.min(mins(b),n)); if(xb<=xa) return;
    s+=`<line x1="${xa}" x2="${xb}" y1="${y(k)}" y2="${y(k)}" stroke="${sg==='+'?'var(--amb)':'var(--vio)'}" stroke-width="5" opacity=".75"><title>King ${k} ${sg==='+'?'(positive)':'(negative)'} ${a} to ${b}</title></line>`; });
  s+=`<polyline fill="none" stroke="var(--acc)" stroke-width="1.6" points="${cs.map((c,i)=>x(i).toFixed(1)+','+y(c[3]).toFixed(1)).join(' ')}"/>`;
  d.setups.forEach(q=>{ const m=mins(q.time); if(m<0||m>=n) return; const taken=q.status.startsWith('TRADE'); const c=cs[Math.min(m,cs.length-1)]; const px=x(m), py=y(c[3]); const up=q.side==='CALL', col=up?'var(--up)':'var(--dn)';
    const tri = up ? `${px},${py+4} ${px-6},${py+15} ${px+6},${py+15}` : `${px},${py-4} ${px-6},${py-15} ${px+6},${py-15}`;
    s+=`<polygon points="${tri}" fill="${taken?col:'var(--panel)'}" stroke="${col}" stroke-width="1.6"><title>${q.time} ${q.side} ${q.strike}: ${esc(q.level)} (${esc(status(q.status)[0])})</title></polygon>`; });
  return s+'</svg>';
}
function spark(t){
  const W=280,H=92,L=6,R=6,T=18,B=14, o=t.opt; if(!o||o.length<2) return '';
  const vals=o.map(r=>r[1]).concat([t.target,t.stop]); const lo=Math.min(...vals)*.995, hi=Math.max(...vals)*1.005;
  const x=i=>L+i/(o.length-1)*(W-L-R), y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Option price during the trade">`;
  s+=`<line x1="${L}" x2="${W-R}" y1="${y(t.target)}" y2="${y(t.target)}" stroke="var(--up)" stroke-dasharray="4 3"/><line x1="${L}" x2="${W-R}" y1="${y(t.stop)}" y2="${y(t.stop)}" stroke="var(--dn)" stroke-dasharray="4 3"/>`;
  s+=`<text x="${W-R}" y="${y(t.target)-3}" text-anchor="end" style="fill:var(--up)">target ${t.target.toFixed(0)}</text><text x="${W-R}" y="${y(t.stop)+11}" text-anchor="end" style="fill:var(--dn)">stop ${t.stop.toFixed(0)}</text>`;
  s+=`<polyline fill="none" stroke="var(--fg)" stroke-width="1.5" points="${o.map((r,i)=>x(i).toFixed(1)+','+y(r[1]).toFixed(1)).join(' ')}"/>`;
  s+=`<circle cx="${x(0)}" cy="${y(o[0][1])}" r="3.5" fill="var(--acc)"/>`;
  return s+'</svg>';
}
function regAt(d,hm){ const b=(d.regime||[]).find(([a,e])=>a<=hm&&hm<=e); return b?b[2]:null; }
function card(t,d){
  const win=t.result==='TARGET'||t.result==='TRAIL', dur=(t.entry_time&&t.exit_time)?(mins(t.exit_time)-mins(t.entry_time)):null;
  return `<div class="card"><div class="top"><span class="name">Trade ${t.n} &middot; <span class="chip ${t.side==='CALL'?'call':'put'}">${t.side}</span> ${t.strike} ${t.side==='CALL'?'CE':'PE'}</span><span class="chip ${win?'tgt':t.result==='STOP'?'stp':'open'}">${t.result==='TARGET'?'Target':t.result==='TRAIL'?'Trailing stop':t.result==='STOP'?'Stop':esc(t.result||'Open')} ${rs(tradeRs(t))} (${pct(t.pnl_pct)})</span></div>
  <div class="sub" style="margin:0">${esc(t.setup)}</div>${spark(t)}
  <div class="kv num"><span>In</span><span>${t.entry_time} at ${t.entry}</span><span>Out</span><span>${t.exit_time||'&ndash;'}${t.exit!=null?' at '+t.exit:''}${dur!=null?' ('+dur+' min)':''}</span><span>Gamma regime at entry</span><span>${(()=>{const r=d&&regAt(d,t.entry_time);return r==='N'?'<b class="dn">negative</b>':r==='P'?'<b class="up">positive</b>':'&ndash;'})()}</span><span>Cost of one lot</span><span>₹${Math.round(t.entry*LOT).toLocaleString('en-IN')}${t.entry*LOT>CAP?' (more than the capital)':''}</span><span>Expiry</span><span>${t.expiry||''}</span><span>Setups skipped meanwhile</span><span>${t.ignored}</span></div>${(t.shifts&&t.shifts.length)?`<div class="sub" style="margin:6px 0 0">${t.shifts.map(esc).join('<br>')}</div>`:''}</div>`;
}
// Rules scoreboard: every setup a rule kept out is run through the same stop / target / trailing rules (would_pct), so each rule can be judged in numbers
function scoreHtml(days){
  const st=(a)=>{const v=a.filter(x=>x!=null&&isFinite(x));if(!v.length)return null;const w=v.filter(x=>x>0).length,sum=v.reduce((p,c)=>p+c,0);return {n:v.length,w,avg:sum/v.length,sum}};
  const cell=(x)=>x?`<td class="num">${x.n}</td><td class="num">${Math.round(x.w/x.n*100)}%</td><td class="num ${x.avg>=0?'up':'dn'}">${x.avg>=0?'+':''}${x.avg.toFixed(1)}%</td><td class="num ${x.sum>=0?'up':'dn'}">${x.sum>=0?'+':''}${x.sum.toFixed(1)}%</td>`:'<td class="num" colspan="4" style="color:var(--mut)">no results yet</td>';
  const taken=[],byGrade={A:[],B:[],C:[]},kept={'Before 09:25 (first 10 minutes)':[],'Against the session trend in negative gamma':[],'Node rules (min grade, blocked, midpoint)':[],'After the daily limit (5 trades or -30%)':[],'Entry trigger: option never crossed the setup high':[]};
  days.forEach(d=>{
    d.trades.forEach(t=>taken.push(t.pnl_pct));
    (d.setups||[]).forEach(q=>{
      const s=q.status||'';
      if(s.startsWith('TRADE')){const t=d.trades.find(x=>x.n===+s.slice(6));if(t&&q.grade&&byGrade[q.grade])byGrade[q.grade].push(t.pnl_pct)}
      else if(q.would_pct!=null){
        if(s.startsWith('no trade before'))kept['Before 09:25 (first 10 minutes)'].push(q.would_pct);
        else if(s.includes('against the move')||s.includes('against the session trend'))kept['Against the session trend in negative gamma'].push(q.would_pct);
        else if(s.includes('never crossed'))kept['Entry trigger: option never crossed the setup high'].push(q.would_pct);
        else if(s.startsWith('daily'))kept['After the daily limit (5 trades or -30%)'].push(q.would_pct);
        else if(s.startsWith('not taken'))kept['Node rules (min grade, blocked, midpoint)'].push(q.would_pct);
      }
    });
  });
  const row=(l,arr,cls)=>`<tr class="${cls||''}"><td>${l}</td>${cell(st(arr))}</tr>`;
  return `<table><thead><tr><th>Group</th><th>Setups</th><th>Win</th><th>Average</th><th>Total</th></tr></thead><tbody>
   <tr><td colspan="5" style="color:var(--mut);padding-top:8px">Taken trades (actual paper trades)</td></tr>
   ${row('All taken trades',taken)}${['A','B','C'].map(g=>row('&nbsp;&nbsp;Grade '+g,byGrade[g])).join('')}
   <tr><td colspan="5" style="color:var(--mut);padding-top:10px">Kept out by a rule: result if they had been taken (testing)</td></tr>
   ${Object.entries(kept).map(([l,a])=>row(l,a)).join('')}</tbody></table>
   <div class="foot" style="margin-top:6px">A rule is helping when the setups it keeps out do worse than the trades taken. Win = result above zero; each setup is tested alone with the same stop, target and trailing rules, so the totals are not a day's profit. Small samples move a lot; give it weeks.</div>`;
}
function levelHtml(days){
  const all=[];
  days.forEach(d=>d.trades.forEach(t=>{const m=(t.setup||'').match(/\(([^)]*)\)/);all.push({lvl:(t.setup||'?').replace(/\s[\d.]+/g,'').replace(/\s*\([^)]*\)/g,'').trim()||'?',how:m?m[1]:'level',pct:t.pnl_pct,rs:tradeRs(t)}); }));
  const grp=(key)=>{const g={};all.forEach(x=>{(g[x[key]]=g[x[key]]||[]).push(x)});return g};
  const sum=a=>a.reduce((p,c)=>p+(c||0),0);
  const stat=(a)=>{const w=a.filter(x=>x.pct>0),l=a.filter(x=>x.pct<=0);return {n:a.length,w:w.length,wrs:sum(w.map(x=>x.rs)),lrs:sum(l.map(x=>x.rs)),net:sum(a.map(x=>x.rs)),pct:sum(a.map(x=>x.pct))}};
  const row=(label,a,bold)=>{const s=stat(a);return `<tr${bold?' style="font-weight:700"':''}><td>${esc(label)}</td><td class="num">${s.n}</td><td class="num">${Math.round(s.w/s.n*100)}%</td><td class="num up">${rs(s.wrs)}</td><td class="num dn">${rs(s.lrs)}</td><td class="num ${s.net>=0?'up':'dn'}">${rs(s.net)}</td><td class="num ${s.pct>=0?'up':'dn'}">${s.pct>=0?'+':''}${s.pct.toFixed(1)}%</td></tr>`};
  const table=(title,g)=>`<tr><td colspan="7" style="color:var(--mut);padding-top:10px">${title}</td></tr>`+Object.entries(g).sort((a,b)=>stat(b[1]).net-stat(a[1]).net).map(([k,a])=>row(k,a)).join('');
  if(!all.length) return '<div class="empty">No trades yet.</div>';
  return `<table><thead><tr><th>Group</th><th>Trades</th><th>Win</th><th>Winners total</th><th>Losers total</th><th>Net</th><th>Sum of %</th></tr></thead><tbody>${row('All trades',all,true)}${table('By level',grp('lvl'))}${table('By signal type',grp('how'))}</tbody></table>
   <div class="foot" style="margin-top:6px">Rupees = (exit &minus; entry) &times; lot of ${LOT} per trade, no brokerage or slippage; a winner is a trade that closed above its entry. Counts the days shown on this page. Small samples move a lot.</div>`;
}
function setups(d){
  if(!d.setups.length) return '<div class="empty">No setups recorded.</div>';
  return `<table><thead><tr><th>Time</th><th>Side</th><th>Strike</th><th>Level</th><th>What happened</th><th title="Testing: what a setup that was not taken would have returned with the same stop, target and trailing rules">If taken (testing)</th></tr></thead><tbody>${d.setups.map(q=>{const [txt,c]=status(q.status);return `<tr class="${q.status.startsWith('TRADE')?'taken':''}"><td class="num">${q.time}</td><td><span class="chip ${q.side==='CALL'?'call':'put'}">${q.side}</span></td><td class="num">${q.strike}</td><td>${esc(q.level)}</td><td><span class="chip ${c}">${esc(txt)}</span>${q.status.startsWith('TRADE')?' '+esc(q.status):''}</td><td class="num" style="font-size:.78em;color:var(--mut)">${q.would_pct==null?'':`${q.would_pct>0?'+':''}${q.would_pct.toFixed(1)}% ${esc(q.would_status||'')}${q.would_exit?' '+q.would_exit:''}`}</td></tr>`;}).join('')}</tbody></table>`;
}
function levels(d){
  const e=Object.entries(d.levels).sort((a,b)=>b[1]-a[1]); if(!e.length) return '<div class="empty">No levels saved.</div>';
  return `<div class="kv num">${e.map(([k,v])=>`<span>${esc(k)}</span><span>${v}</span>`).join('')}</div>`;
}

function kingChart(d){
  const K=d.king, cs=d.candles; if(!K||!K.map||!K.map.length||!cs.length) return '';
  const W=900,H=380,L=58,R=16,T=34,B=28,n=375,iw=W-L-R,ih=H-T-B;
  let lo=Math.min(...cs.map(c=>c[2])), hi=Math.max(...cs.map(c=>c[1])); const pad=Math.max((hi-lo)*.08,15); lo-=pad; hi+=pad;
  const x=m=>L+Math.min(Math.max(m,0),n)/n*iw, y=p=>T+(hi-p)/(hi-lo)*ih, cy=p=>y(Math.min(Math.max(p,lo+3),hi-3));
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Price with the King, floor and ceiling nodes through the day and the node reactions">`;
  const step=(hi-lo)>400?100:50;
  for(let p=Math.ceil(lo/step)*step;p<hi;p+=step) s+=`<line x1="${L}" x2="${W-R}" y1="${y(p)}" y2="${y(p)}" stroke="var(--line)"/><text x="${L-8}" y="${y(p)+4}" text-anchor="end">${p}</text>`;
  [0,60,120,180,240,300,360].forEach(m=>{const h=9*60+15+m; s+=`<line x1="${x(m)}" x2="${x(m)}" y1="${T}" y2="${H-B}" stroke="var(--line)"/><text x="${x(m)}" y="${H-8}" text-anchor="middle">${String(Math.floor(h/60)).padStart(2,'0')}:${String(h%60).padStart(2,'0')}</text>`;});
  const rows=K.map.map(r=>({a:mins(r[0]),b:mins(r[1]),k:r[2],sg:r[3],fl:r[4],ce:r[5],ng:r[6]}));
  for(let i=0;i<rows.length;){ if(!rows[i].ng){i++;continue} let j=i; while(j+1<rows.length&&rows[j+1].ng) j++; s+=`<rect x="${x(rows[i].a)}" y="${T}" width="${Math.max(x(rows[j].b)-x(rows[i].a),2)}" height="${ih}" fill="var(--vio)" opacity=".10"/>`; i=j+1; }
  const stepPath=(key,cls)=>{ let d2='',seg=[]; const flush=()=>{ if(!seg.length) return; d2+=`M${x(seg[0].a)},${cy(seg[0][key])}`; seg.forEach((r,i)=>{ if(i) d2+=`V${cy(r[key])}`; d2+=`H${x(r.b)}`; }); seg=[]; };
    rows.forEach(r=>{ if(r[key]==null) flush(); else seg.push(r); }); flush(); return `<path d="${d2}" fill="none" stroke="${cls==='ce'?'var(--mut)':'var(--acc)'}" stroke-width="1.7" stroke-dasharray="6 4"/>`; };
  s+=stepPath('ce','ce')+stepPath('fl','fl');
  const runs=[]; rows.forEach(r=>{ const c=runs[runs.length-1]; if(c&&c.k===r.k&&c.sg===r.sg) c.b=r.b; else runs.push({k:r.k,sg:r.sg,a:r.a,b:r.b}); });
  runs.forEach((r,i)=>{ const off=r.k<lo||r.k>hi, col=r.sg==='+'?'var(--amb)':'var(--vio)';
    s+=`<line x1="${x(r.a)}" x2="${x(r.b)}" y1="${cy(r.k)}" y2="${cy(r.k)}" stroke="${col}" stroke-width="6" opacity="${off?.4:.9}"><title>King ${r.k} ${r.sg==='+'?'(positive)':'(negative)'}</title></line>`;
    if(i+1<runs.length) s+=`<line x1="${x(r.b)}" x2="${x(r.b)}" y1="${cy(r.k)}" y2="${cy(runs[i+1].k)}" stroke="var(--mut)" stroke-dasharray="2 3"/>`;
    if(r.b-r.a>=12) s+=`<text x="${x(r.a)+4}" y="${cy(r.k)+(r.k>hi?16:-8)}" style="fill:var(--fg);font-weight:600">King ${r.k}${r.k>hi?' ▲':r.k<lo?' ▼':''}</text>`; });
  s+=`<polyline fill="none" stroke="var(--fg)" stroke-width="1.7" points="${cs.map((c,i)=>x(i).toFixed(1)+','+y(c[3]).toFixed(1)).join(' ')}"/>`;
  (K.events||[]).forEach((e,i)=>{ const col=e[2]==='held'?'var(--up)':e[2]==='passed'?'var(--dn)':'var(--mut)';
    s+=`<circle cx="${x(mins(e[0]))}" cy="${cy(e[1])}" r="9" fill="${col}" stroke="var(--panel)" stroke-width="2"><title>${i+1}: ${e[0]} node ${e[1]} (${esc(e[3])}, ${e[4]==='+'?'positive':'negative'}): ${e[2]}</title></circle><text x="${x(mins(e[0]))}" y="${cy(e[1])+4}" text-anchor="middle" style="fill:#fff;font-weight:700;font-size:11px">${i+1}</text>`; });
  const kt=d.ktest&&d.ktest.trades||[];
  if(kt.length){ s+='<defs><marker id="arG" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="var(--up)"/></marker><marker id="arR" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="var(--dn)"/></marker></defs>';
    kt.forEach(t=>{ const x1=x(mins(t.entry_time)), x2=x(mins(t.exit_time||'15:15')), y1=y(t.spot_in), y2=y(t.spot_out==null?t.spot_in:t.spot_out), c=t.side==='CALL'?'var(--up)':'var(--dn)', mk=t.side==='CALL'?'arG':'arR';
      s+=`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c}" stroke-width="3" marker-end="url(#${mk})"><title>King trade ${t.n}: ${t.side} ${t.strike} ${t.entry_time} to ${t.exit_time||'open'}, ${t.pts==null?'':t.pts+' index points'}${t.pct==null?'':', option '+t.pct+'%'}</title></line>`;
      if(t.pct!=null) s+=`<text x="${(x1+x2)/2}" y="${(y1+y2)/2-8}" text-anchor="middle" style="fill:${c};font-weight:700">${t.pct>0?'+':''}${t.pct}%</text>`; }); }
  const lg=`<g transform="translate(${L},14)"><line x1="0" x2="24" y1="0" y2="0" stroke="var(--amb)" stroke-width="6"/><text x="30" y="4">King +</text><line x1="84" x2="108" y1="0" y2="0" stroke="var(--vio)" stroke-width="6"/><text x="114" y="4">King &minus;</text><line x1="176" x2="200" y1="0" y2="0" stroke="var(--mut)" stroke-width="1.7" stroke-dasharray="6 4"/><text x="206" y="4">ceiling</text><line x1="262" x2="286" y1="0" y2="0" stroke="var(--acc)" stroke-width="1.7" stroke-dasharray="6 4"/><text x="292" y="4">floor</text><rect x="338" y="-7" width="20" height="14" fill="var(--vio)" opacity=".18"/><text x="364" y="4">negative gamma</text><circle cx="470" cy="0" r="7" fill="var(--up)"/><text x="482" y="4">held</text><circle cx="526" cy="0" r="7" fill="var(--dn)"/><text x="538" y="4">passed</text><circle cx="600" cy="0" r="7" fill="var(--mut)"/><text x="612" y="4">chop</text><line x1="660" x2="684" y1="0" y2="0" stroke="var(--fg)" stroke-width="1.7"/><text x="690" y="4">Nifty</text></g>`;
  let ev='';
  if((K.events||[]).length) ev=`<div class="tablewrap" style="margin-top:8px"><table><thead><tr><th>#</th><th>Time</th><th>Node</th><th>Role</th><th>Sign</th><th>What price did in the next 30 minutes</th></tr></thead><tbody>${K.events.map((e,i)=>`<tr><td class="num">${i+1}</td><td class="num">${e[0]}</td><td class="num">${e[1]}</td><td>${esc(e[3])}</td><td>${e[4]==='+'?'positive':'negative'}</td><td>${e[2]==='held'?'<span class="chip tgt">held: turned back 25 points</span>':e[2]==='passed'?'<span class="chip stp">passed: went 25 points through</span>':'<span class="chip mut">chop: neither</span>'}</td></tr>`).join('')}</tbody></table></div>`;
  return `<div class="chartbox" style="margin:6px 0 4px">${s+lg}</svg></div><div class="foot">How the nodes moved and how price reacted. The thick line is the King (it jumps between strikes as the map is rebuilt every 2 minutes; off the chart it sits at the edge with an arrow). Dashed lines: the nearest ceiling and floor. Shading: the minutes when the whole map was negative gamma. Numbered circles: approaches to a significant node.</div>${ev}`;
}
function kingHtml(d){
  const K=d.king; if(!K&&!(d.notes&&d.notes.length)) return '';
  let h='<h2 style="margin-top:18px">King analysis <span class="sub" style="font-weight:400">the largest gamma node: where it was, what price did at it, and whether price drifted toward it</span></h2>';
  if(K){
    h+=`<ul style="margin:0 0 10px;padding-left:20px">${K.lines.map(l=>`<li>${esc(l)}</li>`).join('')}</ul>`;
    h+=kingChart(d);
    const cell=c=>!c?'':c.reached?`${c.time} &middot; ${c.o.toFixed(0)}/${c.h.toFixed(0)}/${c.l.toFixed(0)}/${c.c.toFixed(0)} &middot; from ${c.from}, closed ${c.closed}`:c.time?`closest ${c.closest} pts at ${c.time} (${c.o.toFixed(0)}/${c.h.toFixed(0)}/${c.l.toFixed(0)}/${c.c.toFixed(0)})`:'no candle';
    const res=c=>!c||!c.reached?'<span class="chip mut">not reached</span>':c.outcome==='held'?'<span class="chip tgt">held, turned back</span>':c.outcome==='passed'?'<span class="chip stp">went through</span>':'<span class="chip mut">chop</span>';
    h+=`<div class="tablewrap"><table><thead><tr><th>King</th><th>From</th><th>To</th><th>Min</th><th>Price at start &rarr; end</th><th title="Positive: price ended closer to the King than it started">Toward King</th><th title="The candle that first came within 15 points of the King (open/high/low/close), or the closest one">King's candle</th><th>Result</th></tr></thead><tbody>${K.runs.map(r=>`<tr><td class="num"><b>${r.king}</b> <span class="chip ${r.sign==='+'?'open':'mut'}">${r.sign==='+'?'+':'&minus;'}</span></td><td class="num">${r.from}</td><td class="num">${r.to}</td><td class="num">${r.minutes}</td><td class="num">${r.spot_start.toFixed(0)} &rarr; ${r.spot_end.toFixed(0)}</td><td class="num ${r.toward>0?'up':r.toward<0?'dn':''}">${r.toward>0?'+':''}${r.toward}</td><td class="num" style="white-space:normal">${cell(r.candle)}</td><td>${res(r.candle)}</td></tr>`).join('')}</tbody></table></div>`;
    const P=K.pull; const hs=['15','30','60'].filter(x=>P[x]);
    const pc=(r)=>r?r.closer+'% &middot; '+(r.gain>0?'+':'')+r.gain:'';
    if(hs.length) h+=`<div class="tablewrap" style="margin-top:10px"><table><thead><tr><th>Drift toward the King</th>${hs.map(x=>`<th>${x} min later</th>`).join('')}</tr></thead><tbody>
      <tr><td>King: closer, average points</td>${hs.map(x=>`<td class="num"><b>${pc(P[x].king)}</b></td>`).join('')}</tr>
      <tr><td>Control: King moved 50 points either way</td>${hs.map(x=>`<td class="num">${pc(P[x].shift50)}</td>`).join('')}</tr>
      <tr><td>Control: day's VWAP</td>${hs.map(x=>`<td class="num">${pc(P[x].vwap)}</td>`).join('')}</tr>
      <tr><td>Independent windows</td>${hs.map(x=>`<td class="num">${P[x].independent} (${P[x].n} readings)</td>`).join('')}</tr></tbody></table></div>`;
    h+=`<div class="foot" style="margin-top:6px">${K.flickers} short flicker${K.flickers===1?'':'s'} (under about 6 minutes) are left out of the table but drawn on the chart. Thick chart line: amber = positive King, violet = negative King. Readings come from the nodes agent every 2 minutes.</div>`;
  }
  if(d.notes&&d.notes.length) h+=`<div style="margin-top:10px">${d.notes.map(n=>`<p style="margin:6px 0">${esc(n)}</p>`).join('')}</div>`;
  return h;
}
function ktestHtml(d){
  const K=d.ktest; if(!K||!K.trades||!K.trades.length) return '';
  const tr=K.trades, priced=tr.filter(t=>t.pct!=null), tot=priced.reduce((a,t)=>a+t.rs,0), pts=tr.reduce((a,t)=>a+(t.pts||0),0);
  let h=`<h2 style="margin-top:18px">King trade <span class="chip open">test</span> <span class="sub" style="font-weight:400">toward the King from 10:15; separate from the paper trades above, not in any total on this page</span></h2>`;
  h+=`<div class="tablewrap"><table><thead><tr><th>#</th><th>Side</th><th>Strike</th><th>King</th><th>In</th><th>Out</th><th>Why it ended</th><th>Index points</th><th>Option in &rarr; out</th><th>Option</th><th>One lot</th></tr></thead><tbody>${tr.map(t=>`<tr><td class="num">${t.n}</td><td><span class="chip ${t.side==='CALL'?'call':'put'}">${t.side}</span></td><td class="num">${t.strike}</td><td class="num">${t.king}</td><td class="num">${t.entry_time} &middot; ${t.spot_in.toFixed(0)}</td><td class="num">${t.exit_time?t.exit_time+' &middot; '+t.spot_out.toFixed(0):'open'}</td><td>${esc(t.why)}</td><td class="num ${t.pts>0?'up':t.pts<0?'dn':''}">${t.pts==null?'&ndash;':(t.pts>0?'+':'')+t.pts}</td><td class="num">${t.opt_in==null?'&ndash;':t.opt_in+' &rarr; '+t.opt_out}</td><td class="num ${t.pct>0?'up':t.pct<0?'dn':''}">${t.pct==null?'&ndash;':pct(t.pct)}</td><td class="num ${t.rs>0?'up':t.rs<0?'dn':''}">${t.rs==null?'&ndash;':rs(t.rs)}</td></tr>`).join('')}</tbody></table></div>`;
  h+=`<div class="foot" style="margin-top:6px">${tr.length} trade${tr.length===1?'':'s'}, ${priced.filter(t=>t.pct>0).length} winners, ${pts>0?'+':''}${pts.toFixed(0)} index points${priced.length?`, ${rs(tot)} on one lot of 65 (option candles of the same contract the paper trades use; no brokerage or slippage)`:''}. Rules: first trade from 10:15 toward the King (CALL if it is above price, PUT if below); held until price comes within 15 points of the King; closed if the King's direction turns against it for 5 minutes; the next trade starts 10 minutes after such a turn (5 minutes after reaching the King); no trade when price is within 25 points of the King; all closed at 15:15. Arrows on the King graph show each trade.</div>`;
  return h;
}
function ktestPanel(){
  const S=DATA.ktest; if(!S) return '<div class="empty">No King trades yet. The test starts with the 19 Nov session and fills in during the day.</div>';
  return `<div class="band small"><div class="tile"><div class="k">Trades</div><div class="v num">${S.n}</div><div class="n">${S.wins} winners over ${S.days} day${S.days===1?'':'s'}</div></div><div class="tile"><div class="k">Average per trade</div><div class="v num ${cls(S.avg_pct)}">${pct(S.avg_pct)}</div><div class="n">on the option price</div></div><div class="tile"><div class="k">One lot of 65</div><div class="v num ${cls(S.rs)}">${rs(S.rs)}</div><div class="n">${S.pts>0?'+':''}${S.pts} index points</div></div></div><div class="foot" style="margin-top:6px">Test only: it is not in the net result, the calendar, the curve or the rules scoreboard of this page. Details and arrows are on each day's page.</div>`;
}
function pullPanel(){
  const P=DATA.pull; if(!P||!P.days) return '<div class="empty">No King data yet.</div>';
  const row=(h)=>{const H=P.horizons[h]; if(!H||!H.rows.length) return ''; const k=H.rows.find(r=>r.level==='king'), c=H.rows.filter(r=>r.level!=='king'), best=c.length?c.reduce((a,b)=>b.gain>a.gain?b:a):null;
    return `<tr><td>${h} minutes later</td><td class="num"><b>${k.closer}%</b></td><td class="num ${k.gain>=0?'up':'dn'}">${k.gain>0?'+':''}${k.gain}</td><td class="num">${k.touched}%</td><td>${best?esc(best.label):''}</td><td class="num">${best?best.closer+'%':''}</td><td class="num">${best?(best.gain>0?'+':'')+best.gain:''}</td><td class="num">${H.independent}</td></tr>`};
  const tr=days.flatMap(d=>(d.king&&d.king.trades)||[]), g=a=>{const v=a.filter(t=>t.pnl_pct!=null);return v.length?`${v.length} trades, average ${(v.reduce((s,t)=>s+t.pnl_pct,0)/v.length)>0?'+':''}${(v.reduce((s,t)=>s+t.pnl_pct,0)/v.length).toFixed(1)}%, ${v.filter(t=>t.pnl_pct>0).length} winners`:'none'};
  const dirLine=tr.length?`<div class="foot" style="margin-top:6px">Taken trades pointing toward the King: ${g(tr.filter(t=>t.with_king))}. Pointing away from it: ${g(tr.filter(t=>!t.with_king))}.</div>`:'';
  return `<div class="tablewrap"><table><thead><tr><th>All journaled days</th><th>King: closer</th><th>avg pts</th><th>touched</th><th>Best control</th><th>closer</th><th>avg pts</th><th>Independent windows</th></tr></thead><tbody>${row('15')}${row('30')}${row('60')}</tbody></table></div><div class="foot" style="margin-top:6px">${esc(P.verdict)} Full tables with the other controls and splits: Node replay.</div>${dirLine}`;
}
let LVSHOWN = false, view = '', mon = '';
const MN = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const byDate = {}; days.forEach(d=>byDate[d.date]=d);
const oldest = days.length ? days[days.length-1].date.slice(0,7) : '', newest = days.length ? days[0].date.slice(0,7) : '';
function stats(list){
  const asc = list.slice().sort((a,b)=>a.date<b.date?-1:1);
  const trades = asc.flatMap(d=>d.trades.map(t=>({...t, rs: tradeRs(t), date:d.date})));
  const closed = trades.filter(t=>t.rs!=null), wins = closed.filter(t=>t.rs>0), loss = closed.filter(t=>t.rs<0);
  const net = closed.reduce((s,t)=>s+t.rs,0), avg = a => a.length ? a.reduce((s,t)=>s+t.rs,0)/a.length : null;
  let cum=0, peak=0, dd=0; closed.forEach(t=>{cum+=t.rs; peak=Math.max(peak,cum); dd=Math.min(dd,cum-peak);});
  const dr = asc.map(dayRs);
  return {net, n:closed.length, wins:wins.length, loss:loss.length, rate: closed.length ? wins.length/closed.length*100 : null, avgWin:avg(wins), avgLoss:avg(loss), per: closed.length?net/closed.length:null,
          best: closed.length?closed.reduce((a,b)=>b.rs>a.rs?b:a):null, worst: closed.length?closed.reduce((a,b)=>b.rs<a.rs?b:a):null,
          gd: dr.filter(x=>x>0).length, ld: dr.filter(x=>x<0).length, nd: asc.length, dd, trades: closed};
}
function statsBand(){
  const S = stats(days); if(!S.n){ $('stats').innerHTML = '<div class="empty">No closed trades yet.</div>'; return; }
  const tile = (k,v,n,c='') => `<div class="tile"><div class="k">${k}</div><div class="v num ${c}">${v}</div><div class="n">${n}</div></div>`;
  const rc = S.net/CAP*100;
  $('stats').innerHTML = [
    tile('Net result', rs(S.net), `${days.length} day${days.length===1?'':'s'} journaled`, cls(S.net)),
    tile('Return on capital', pct(rc), `on ₹${CAP.toLocaleString('en-IN')}`, cls(rc)),
    tile('Trades', S.n, `${S.wins} reached the target, ${S.loss} hit the stop`),
    tile('Target rate', S.rate==null?'&ndash;':S.rate.toFixed(0)+'%', 'trades that reached the target'),
    tile('Average target trade', rs(S.avgWin), S.wins+(S.wins===1?' trade':' trades'), 'up'),
    tile('Average stopped trade', rs(S.avgLoss), S.loss+(S.loss===1?' trade':' trades'), 'dn'),
    tile('Average per trade', rs(S.per), 'target and stop trades together', cls(S.per)),
    tile('Best trade', S.best?rs(S.best.rs):'&ndash;', S.best?`${wk(S.best.date)}, trade ${S.best.n}`:'', 'up'),
    tile('Worst trade', S.worst?rs(S.worst.rs):'&ndash;', S.worst?`${wk(S.worst.date)}, trade ${S.worst.n}`:'', 'dn'),
    tile('Winning days', `${S.gd} of ${S.nd}`, `${S.ld} losing, ${S.nd-S.gd-S.ld} flat or without a trade`),
    tile('Deepest dip', rs(S.dd), `${(S.dd/CAP*100).toFixed(1)}% of capital, peak to low`, S.dd<0?'dn':'')
  ].join('');
}
function curve(){
  const S = stats(days), t = S.trades; if(!t.length){ $('curve').innerHTML='<div class="empty">No closed trades yet.</div>'; return; }
  const W=1000,H=220,L=64,R=20,T=14,B=30; let cum=0; const pts=[[0,0]]; t.forEach((x,i)=>{cum+=x.rs; pts.push([i+1,cum]);});
  const lo=Math.min(0,...pts.map(p=>p[1])), hi=Math.max(0,...pts.map(p=>p[1])), pad=(hi-lo||1)*.12, a=lo-pad, b=hi+pad;
  const x=i=>L+(t.length?i/t.length:0)*(W-L-R), y=v=>T+(b-v)/(b-a)*(H-T-B);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Cumulative result after each trade">`;
  const step=Math.max(500,Math.round((b-a)/4/500)*500);
  for(let v=Math.ceil(a/step)*step;v<b;v+=step) s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end">${rs(v).replace('+','')}</text>`;
  s+=`<line x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--mut)" stroke-dasharray="4 3"/>`;
  s+=`<polyline fill="none" stroke="var(--acc)" stroke-width="2" points="${pts.map(p=>x(p[0]).toFixed(1)+','+y(p[1]).toFixed(1)).join(' ')}"/>`;
  t.forEach((q,i)=>{ const px=x(i+1), py=y(pts[i+1][1]); s+=`<circle cx="${px}" cy="${py}" r="4.5" fill="${q.rs>0?'var(--up)':'var(--dn)'}"><title>${wk(q.date)} trade ${q.n}: ${rs(q.rs)}, total ${rs(pts[i+1][1])}</title></circle>`; });
  const seen={}; t.forEach((q,i)=>{ if(!seen[q.date]){seen[q.date]=1; s+=`<text x="${x(i+1)}" y="${H-8}" text-anchor="middle">${wk(q.date)}</text>`;} });
  $('curve').innerHTML = s+'</svg>';
}
function calendar(){
  if(!mon) mon = newest || new Date().toISOString().slice(0,7);
  const [Y,M] = mon.split('-').map(Number), first = new Date(Y,M-1,1), nd = new Date(Y,M,0).getDate(), off = (first.getDay()+6)%7;
  $('mname').textContent = MN[M-1]+' '+Y; $('prev').disabled = !oldest || mon<=oldest; $('next').disabled = !newest || mon>=newest;
  const inMonth = days.filter(d=>d.date.startsWith(mon)), S = stats(inMonth);
  $('msum').innerHTML = inMonth.length ? `<b class="${cls(S.net)}">${rs(S.net)}</b> this month &middot; ${S.n} trade${S.n===1?'':'s'}` : '<span class="sub">No journal days this month</span>';
  let h = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(x=>`<div class="dow">${x}</div>`).join('');
  for(let i=0;i<off;i++) h += '<div class="cell blank"></div>';
  for(let k=1;k<=nd;k++){
    const ds = `${mon}-${String(k).padStart(2,'0')}`, d = byDate[ds];
    if(!d){ h += `<button class="cell" disabled aria-label="${ds}: no journal"><span class="d">${k}</span></button>`; continue; }
    const r = dayRs(d), c = r>0?'win':r<0?'loss':'flat';
    h += `<button class="cell ${c}" data-d="${ds}" aria-label="${wk(ds)}: ${rs(r)}, ${d.trades.length} trades"><span class="d">${k}</span><span class="r num ${r>0?'up':r<0?'dn':''}">${d.trades.length?rs(r):'No trade'}</span><span class="n num">${d.trades.length} trade${d.trades.length===1?'':'s'}${d.trades.length?' &middot; '+pct(r/CAP*100):''}</span></button>`;
  }
  $('cal').innerHTML = h;
  $('cal').querySelectorAll('button[data-d]').forEach(b=>b.onclick=()=>go(b.dataset.d));
}
function go(d){ location.hash = d || ''; }
function dayview(){
  const d = byDate[view]; if(!d){ go(''); return; }
  const op=d.opening||'', opWarn=!/TradingView \(1[5-9]|TradingView \(\d{2}/.test(op);
  const ch = chart(d);
  $('day').innerHTML = `<div class="dayhead"><div><div class="t">${wk(d.date)} ${d.date.slice(0,4)} &middot; ${esc(d.symbol)}</div><div class="pills"><span class="pill">Levels from the ${esc(d.close||'?')} close</span><span class="pill ${opWarn?'warn':''}">Opening candles: ${esc(op||'n/a')}</span><span class="pill">${d.trades.length} trade${d.trades.length===1?'':'s'} &middot; ${d.setups.length} setups</span></div></div><div style="text-align:right"><div class="t ${cls(dayRs(d))} num">${rs(dayRs(d))}</div><div class="sub num">${(dayRs(d)/CAP*100>0?'+':'')}${(dayRs(d)/CAP*100).toFixed(1)}% of capital &middot; ${pct(d.total)} on premiums</div></div></div>
  <div class="chartbox" style="margin-top:12px">${ch}</div>
  <div class="legend"><span>Line: index close, 1-minute</span>${(d.regime&&d.regime.length)?`<span><b class="dn">&#9644;</b> background red: negative gamma</span><span><b class="up">&#9644;</b> green: positive gamma</span><span>Trades entered: ${d.trades.filter(t=>regAt(d,t.entry_time)==='N').length} in negative gamma, ${d.trades.filter(t=>regAt(d,t.entry_time)==='P').length} in positive gamma</span>`:''}<span>Shaded: a trade was open</span><span>&#9650; &#9660; filled: setup taken &middot; hollow: setup not taken</span>${LVSHOWN ? '<span style="color:var(--vio)">Dashed: end-of-day levels near the day range</span>' : '<span>No end-of-day level was near the day range</span>'}</div>
  <div class="legend">${d.king?'<span><b style="color:var(--amb)">&#9473;</b> King, positive</span><span><b style="color:var(--vio)">&#9473;</b> King, negative</span>':''}</div>
  <h2 style="margin-top:18px">Trades</h2><div class="cards">${d.trades.length?d.trades.map(t=>card(t,d)).join(''):'<div class="empty">No trade was taken.</div>'}</div>
  <div class="two" style="margin-top:18px"><div><h2>Setups, in time order</h2><div class="tablewrap">${setups(d)}</div></div><div><h2>Levels the page used</h2>${levels(d)}</div></div>${ktestHtml(d)}${kingHtml(d)}`;
}
function alltable(){
  $('score').innerHTML = scoreHtml(days); $('bylevel').innerHTML = levelHtml(days); $('pull').innerHTML = pullPanel(); $('ktest').innerHTML = ktestPanel();
  $('all').innerHTML = `<thead><tr><th>Date</th><th>Trades</th><th>Target</th><th>Trail</th><th>Stop</th><th>Result</th><th>% of capital</th><th>Opening candles</th></tr></thead><tbody>${days.map(d=>`<tr class="row" tabindex="0" data-d="${d.date}"><td>${wk(d.date)} ${d.date.slice(0,4)}</td><td class="num">${d.trades.length}</td><td class="num">${d.trades.filter(t=>t.result==='TARGET').length}</td><td class="num">${d.trades.filter(t=>t.result==='TRAIL').length}</td><td class="num">${d.trades.filter(t=>t.result==='STOP').length}</td><td class="num ${cls(dayRs(d))}">${rs(dayRs(d))}</td><td class="num ${cls(dayRs(d))}">${pct(dayRs(d)/CAP*100)}</td><td>${esc(d.opening||'n/a')}</td></tr>`).join('')}</tbody>`;
  $('all').querySelectorAll('tr.row').forEach(r=>{const f=()=>go(r.dataset.d);r.onclick=f;r.onkeydown=e=>{if(e.key==='Enter')f();};});
}
/* ---- Expiry week: one connected chart per Wednesday-to-Tuesday cycle. Drawn with the SAME chart library, colours, lines and bubbles as the live dashboard (tools/stream_page/index.html) ---- */
const WS = {exp:null, theme:(()=>{ try{ return localStorage.getItem('bp_theme')==='dark'?'dark':'light'; }catch(e){ return 'light'; } })(), on:{vwap:true,wvwap:true,sd:true,lv:true,bub:true,mk:true,gam:true}, range:null};
const WLAYERS = [['vwap','Daily VWAP'],['wvwap','Weekly VWAP'],['sd','VWAP ± 1 SD'],['lv','Levels'],['bub','Node bubbles'],['mk','Setups / trades'],['gam','Gamma regime']];
const weeks = (()=>{ const m={}; days.forEach(d=>{ (m[d.exp||d.date]=m[d.exp||d.date]||[]).push(d); }); Object.values(m).forEach(a=>a.sort((p,q)=>p.date<q.date?-1:1)); return m; })();
const weekKeys = Object.keys(weeks).sort().reverse();
const dshort = d => new Date(d+'T12:00:00').toLocaleDateString('en-GB',{day:'2-digit',month:'short'});
const dlong = d => new Date(d+'T12:00:00').toLocaleDateString('en-GB',{weekday:'short',day:'2-digit',month:'short'});
const hmOf = m => { const h=9*60+15+m; return String(Math.floor(h/60)).padStart(2,'0')+':'+String(h%60).padStart(2,'0'); };
const WK = (()=>{
  let charts=[], cv=null, S=null;
  const LIGHT={'#ffeb3b':'#c79100','#18ffff':'#0097a7','#ff80ab':'#d81b60','#80cbc4':'#00796b','#d4e157':'#827717','#f5a623':'#e65100','#ef5b5b':'#c62828','#2ecc9a':'#2e7d32','#ff9f6b':'#e64a19','#66c2ff':'#0277bd','#e8c547':'#b8860b','#b388ff':'#6a1b9a'};
  const isL=()=>WS.theme==='light', G=()=>isL()?'#0f8a5f':'#2ecc9a', R=()=>isL()?'#d33f3f':'#ef5b5b';
  const tsOf=(date,hm)=>{ const [Y,M,D]=date.split('-').map(Number), [h,m]=hm.split(':').map(Number); return Date.UTC(Y,M-1,D,h,m)/1000; };
  /* level colours and line styles by name: copied from the live dashboard */
  const chartColor=n=>{ n=n.toLowerCase(); const w=n.includes('weekly');
    if(n.includes('flip')) return 'rgba(217,162,74,.8)'; if(n.includes('gex call')) return 'rgba(217,140,106,.75)'; if(n.includes('gex put')) return 'rgba(106,166,201,.75)';
    if(n.includes('poc')) return w?'rgba(201,180,88,.45)':'rgba(201,180,88,.75)'; return w?'rgba(155,143,196,.4)':'rgba(155,143,196,.7)'; };
  const lstyle=n=>{ const s=n.toLowerCase(); if(s.includes('poc')) return 0; if(s.includes('weekly')) return 1; if(s.includes('max pain')||s.includes('flip')) return 1; return 2; };
  const reacts=n=>{ n=n.toLowerCase(); return !(n==='spot'||n.includes('oi ')||n.includes('max pain')); };
  const nodeCol=(sign,a)=>sign==='+'?`rgba(255,213,79,${a})`:`rgba(179,136,255,${a})`;
  const axisFont=()=>13;
  function build(list){
    const candles=[], gap={}, dv=[], wv=[], up=[], dn=[], lvs=[], hist=[], regs=[], marks=[], idx=new Map(), starts=[], kS=[], gS=[];
    list.forEach(d=>{
      const first=d.candles.length?tsOf(d.date,d.candles[0][0]):null; if(first==null) return;
      starts.push({t:first,date:d.date,last:tsOf(d.date,d.candles[d.candles.length-1][0])});
      const ts=d.candles.map(c=>tsOf(d.date,c[0]));
      gap[first-60]=1;
      d.candles.forEach((c,i)=>{ const t=ts[i]; candles.push({time:t,open:c[4]==null?c[3]:c[4],high:c[1],low:c[2],close:c[3]});
        const v=(d.vw||[])[i]; if(v){ if(v[0]!=null){ dv.push({time:t,value:v[0]}); up.push({time:t,value:v[1]}); dn.push({time:t,value:v[2]}); } if(v[3]!=null) wv.push({time:t,value:v[3]}); }
        idx.set(t,{date:d.date,hm:c[0],c:c,v:v}); });
      Object.entries(d.levels||{}).forEach(([k,v])=>{ if(reacts(k)&&v>0) lvs.push({name:k,value:v,a:ts[0],b:ts[ts.length-1],date:d.date}); });
      (d.nh||[]).forEach(r=>{ hist.push(r); });
      (d.regime||[]).forEach(([a,b,r])=>{ const ia=d.candles.findIndex(c=>c[0]>=a), jb=(()=>{ let j=-1; d.candles.forEach((c,i)=>{ if(c[0]<=b) j=i; }); return j; })(); if(ia>=0&&jb>=ia) regs.push({a:ts[ia],b:ts[jb],r:r}); });
      /* setups not taken: grey arrows; trades: coloured entry arrow and exit circle (the live dashboard's marker scheme) */
      d.setups.filter(q=>!q.status.startsWith('TRADE')).forEach(q=>{ const t=tsOf(d.date,q.time); if(idx.has(t)) marks.push({time:t,position:q.side==='CALL'?'belowBar':'aboveBar',color:'#8b97a8',shape:q.side==='CALL'?'arrowUp':'arrowDown',text:(q.grade||'')}); });
      d.trades.forEach(t=>{ if(!t.entry_time) return; const te=tsOf(d.date,t.entry_time); const call=t.side==='CALL';
        marks.push({time:te,position:call?'belowBar':'aboveBar',color:call?'CALL':'PUT',shape:call?'arrowUp':'arrowDown',text:'T'+t.n,side:t.side,entry:true});
        if(t.exit_time&&t.pnl_pct!=null){ const tx=tsOf(d.date,t.exit_time); marks.push({time:tx,position:call?'aboveBar':'belowBar',color:t.pnl_pct>=0?'+':'-',shape:'circle',text:(t.pnl_pct>0?'+':'')+t.pnl_pct.toFixed(1)+'%',exit:true}); } });
      /* King size and net gamma: the latest reading carried forward to each candle */
      const kx=d.kx||[]; let j=-1;
      d.candles.forEach((c,i)=>{ while(j+1<kx.length&&kx[j+1][0]<=c[0]) j++; if(j<0) return; const r=kx[j]; kS.push({time:ts[i],value:r[1],sign:r[2]}); gS.push({time:ts[i],value:r[3]}); });
    });
    hist.sort((a,b)=>a[0]-b[0]);
    return {candles,gap,dv,wv,up,dn,lvs,hist,regs,marks,idx,starts,kS,gS};
  }
  const withGaps=(arr,gap)=>{ const out=[]; const times=new Set(arr.map(x=>x.time)); arr.forEach(x=>out.push(x)); Object.keys(gap).forEach(t=>{ if(!times.has(+t)) out.push({time:+t}); }); return out.sort((a,b)=>a.time-b.time); };
  function make(el,h,opts){ const L=isL();
    return LightweightCharts.createChart(el,Object.assign({layout:{background:{color:L?'#ffffff':'#0b0f16'},textColor:L?'#1f2937':'#c9d1d9',fontSize:axisFont()},grid:{vertLines:{color:L?'#eef1f5':'#141c28'},horzLines:{color:L?'#eef1f5':'#141c28'}},
      rightPriceScale:{borderColor:L?'#c9d1dc':'#263040',scaleMargins:{top:0.03,bottom:0.03},minimumWidth:78},timeScale:{borderColor:L?'#c9d1dc':'#263040',timeVisible:true,secondsVisible:false,rightOffset:6},crosshair:{mode:0},autoSize:true,handleScale:{axisPressedMouseMove:{time:true,price:false},mouseWheel:true,pinch:true},handleScroll:{pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false,mouseWheel:false}},opts||{})); }
  const LS={priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,autoscaleInfoProvider:()=>null};
  function render(list){
    destroy();
    if(typeof LightweightCharts==='undefined'){ $('wcwrap').innerHTML='<div class="empty">The chart library could not be loaded (it comes from the internet, the same as the live dashboard page). Check the connection and reload.</div>'; return; }
    const D=build(list); S=D; if(!D.candles.length) return;
    const L=isL(), on=WS.on;
    const main=make($('wchart')), cs=main.addCandlestickSeries({upColor:G(),downColor:R(),borderVisible:false,wickUpColor:G(),wickDownColor:R()});
    cs.setData(withGaps(D.candles,D.gap));
    const vd=main.addLineSeries(Object.assign({color:L?'#2a8a8a':'rgba(95,179,179,.9)',lineWidth:2,visible:on.vwap},LS)), vw=main.addLineSeries(Object.assign({color:L?'#a08a1e':'rgba(201,180,88,.75)',lineWidth:1,visible:on.wvwap},LS));
    const SD=Object.assign({lineWidth:2,lineStyle:2,color:L?'#1f8f8f':'#4dd0e1',visible:on.sd},LS), vu=main.addLineSeries(SD), vl=main.addLineSeries(SD);
    [[vd,D.dv],[vw,D.wv],[vu,D.up],[vl,D.dn]].forEach(([s,a])=>s.setData(withGaps(a,D.gap)));
    if(on.lv) D.lvs.forEach(l=>{ const s=main.addLineSeries(Object.assign({color:chartColor(l.name),lineWidth:1,lineStyle:lstyle(l.name)},LS)); s.setData([{time:l.a,value:l.value},{time:l.b,value:l.value}]); });
    const mk=D.marks.map(m=>({time:m.time,position:m.position,shape:m.shape,text:'',color:m.color==='CALL'?G():m.color==='PUT'?R():m.color==='+'?G():m.color==='-'?R():m.color})).sort((a,b)=>a.time-b.time);
    cs.setMarkers(on.mk?mk:[]);
    /* the strips: King size and net gamma, on the same time axis */
    const kc=make($('wkchart'),0,{timeScale:{visible:false,rightOffset:6,borderColor:L?'#c9d1dc':'#263040'}}), ks=kc.addHistogramSeries({priceLineVisible:false,lastValueVisible:false,priceFormat:{type:'price',precision:0,minMove:1}});
    ks.setData(withGaps(D.kS.map(x=>({time:x.time,value:x.value,color:nodeCol(x.sign,.85)})),D.gap));
    const gc=make($('wgchart'),0,{timeScale:{visible:false,rightOffset:6,borderColor:L?'#c9d1dc':'#263040'}}), gs=gc.addHistogramSeries({priceLineVisible:false,lastValueVisible:false,priceFormat:{type:'price',precision:0,minMove:1}});
    gs.setData(withGaps(D.gS.map(x=>({time:x.time,value:x.value,color:x.value>=0?G():R()})),D.gap));
    charts=[main,kc,gc];
    let busy=false; const sync=(src)=>src.timeScale().subscribeVisibleLogicalRangeChange(r=>{ if(!r||busy) return; busy=true; charts.forEach(c=>{ if(c!==src) c.timeScale().setVisibleLogicalRange(r); c.priceScale('right').applyOptions({autoScale:true}); }); busy=false; WS.range=r; draw(); });
    charts.forEach(sync);
    const first=D.starts[D.starts.length-1], last=D.candles[D.candles.length-1].time;
    main.timeScale().setVisibleRange({from:first.t-600,to:last+900});
    cv=$('wcv');
    S.main=main; S.cs=cs; S.sdraw=draw;
    main.subscribeCrosshairMove(p=>{ const o=$('wread'); if(!p||!p.time){ return; } const r=D.idx.get(p.time); if(!r){ return; }
      const v=r.v, kx=(list.find(x=>x.date===r.date).kx||[]).filter(q=>q[0]<=r.hm).slice(-1)[0], sg=(list.find(x=>x.date===r.date).setups||[]).filter(q=>q.time===r.hm);
      o.innerHTML=`<b>${dlong(r.date)} ${r.hm}</b> &middot; O ${(r.c[4]==null?r.c[3]:r.c[4]).toFixed(2)} H ${r.c[1].toFixed(2)} L ${r.c[2].toFixed(2)} C ${r.c[3].toFixed(2)}`+(v&&v[0]!=null?` &middot; VWAP ${v[0].toFixed(1)} (&plusmn;1 SD ${v[2].toFixed(1)} to ${v[1].toFixed(1)})`:'')+(v&&v[3]!=null?` &middot; weekly VWAP ${v[3].toFixed(1)}`:'')+(kx?` &middot; King size ${kx[1]} (${kx[2]==='+'?'positive':'negative'}), net gamma ${kx[3]}`:'')+sg.map(q=>` &middot; <b style="color:${q.side==='CALL'?G():R()}">${q.time} ${q.side} ${q.strike}</b> ${esc(q.level)} (${esc(status(q.status)[0])})`).join(''); });
    draw();
  }
  function destroy(){ charts.forEach(c=>{ try{ c.remove(); }catch(e){} }); charts=[]; S=null; }
  /* overlay: gamma regime wash, day dividers, node bubbles (the dashboard's own bubble code) and marker labels */
  function draw(){
    if(!S||!S.main||!cv) return; const main=S.main, cs=S.cs, el=$('wchart'); const w=el.clientWidth, h=el.clientHeight, dpr=window.devicePixelRatio||1;
    if(cv.width!==Math.round(w*dpr)||cv.height!==Math.round(h*dpr)){ cv.width=Math.round(w*dpr); cv.height=Math.round(h*dpr); cv.style.width=w+'px'; cv.style.height=h+'px'; }
    const g=cv.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0); g.clearRect(0,0,w,h);
    const ts=main.timeScale(), Wp=w-main.priceScale('right').width(), ch=ts.height?ts.height():0, sc=Math.max(.7,Math.min(1.4,w/1300));
    const C=[]; S.idx.forEach((v,t)=>C.push({time:t,high:v.c[1],low:v.c[2]})); C.sort((a,b)=>a.time-b.time);
    const X=t=>ts.timeToCoordinate(t);
    const xa=C.length>1?X(C[C.length-1].time):null, xb=C.length>1?X(C[C.length-2].time):null, bs=(xa!=null&&xb!=null&&xa!==xb)?Math.abs(xa-xb):6;
    g.save(); g.beginPath(); g.rect(0,0,Wp,h); g.clip();
    if(WS.on.gam) S.regs.forEach(q=>{ const x1=X(q.a), x2=X(q.b); if(x1==null||x2==null) return; const neg=q.r==='N', xl=Math.min(x1,x2)-bs/2, wd=Math.abs(x2-x1)+bs;
      g.fillStyle=neg?'rgba(239,91,91,.045)':'rgba(46,204,154,.04)'; g.fillRect(xl,0,wd,h); g.fillStyle=neg?'rgba(239,91,91,.8)':'rgba(46,204,154,.8)'; g.fillRect(xl,0,wd,4);
      const lab=neg?'NEGATIVE GAMMA':'POSITIVE GAMMA'; g.font=`700 ${Math.round(axisFont()*.75)}px sans-serif`; if(g.measureText(lab).width+16<wd){ g.textAlign='center'; g.textBaseline='top'; g.lineWidth=3; g.strokeStyle=isL()?'rgba(255,255,255,.85)':'rgba(11,15,22,.8)'; g.strokeText(lab,xl+wd/2,8); g.fillStyle=neg?(isL()?'#c62828':'#ff9d9d'):(isL()?'#2e7d32':'#9df0cf'); g.fillText(lab,xl+wd/2,8); } });
    S.starts.forEach(s=>{ const x=X(s.t); if(x==null) return; const xx=x-bs/2; g.strokeStyle=isL()?'rgba(100,116,139,.55)':'rgba(139,151,168,.55)'; g.lineWidth=1; g.beginPath(); g.moveTo(xx,0); g.lineTo(xx,h); g.stroke();
      g.font=`700 ${Math.round(axisFont()*.8)}px sans-serif`; g.textAlign='left'; g.textBaseline='bottom'; g.fillStyle=isL()?'#334155':'#c9d1d9'; g.fillText(dlong(s.date),xx+6,h-ch-6); });
    g.restore();
    const obst=[];
    if(WS.on.bub&&S.hist.length&&C.length>2){
      g.save(); g.beginPath(); g.rect(0,0,Wp,h); g.clip();
      const LET=true, every=Math.max(1,Math.round(Math.max(16,19*sc)/bs));
      const bub=(x,y,pct,king,sign,lt,showLt)=>{ const f=Math.min(1,pct/100), r0=(1+6.8*Math.pow(f,1.2))*Math.max(sc,.95), r=(lt&&LET)?Math.max(r0,6.8*Math.max(sc,.95)):r0, a=Math.min(.8,.05+.65*Math.pow(f,1.4)+(king?.08:0)), col=sign>0?'255,213,79':'179,136,255';
        g.fillStyle=`rgba(${col},${a})`; g.strokeStyle=`rgba(${col},${Math.min(.75,a+.25)})`; g.lineWidth=.8; g.beginPath(); g.arc(x,y,r,0,6.283); g.fill(); g.stroke();
        if(lt&&LET&&showLt){ g.save(); g.font=`800 ${Math.round(r*1.15)}px sans-serif`; g.textAlign='center'; g.textBaseline='middle'; g.fillStyle='rgba(255,255,255,.95)'; g.fillText(lt,x,y+.5); g.restore(); } };
      const HIST=S.hist, rd=t=>{ let lo=0,hi=HIST.length-1,ans=-1; while(lo<=hi){ const m=(lo+hi)>>1; if(HIST[m][0]<=t){ ans=m; lo=m+1; } else hi=m-1; } return ans; };
      for(let i=C.length-1;i>=0;i--){ if(i%every) continue; const x=X(C[i].time); if(x==null) continue; if(x<-10) break; if(x>Wp+10) continue;
        const k=rd(C[i].time); if(k<0) continue; if(Math.floor(HIST[k][0]/86400)!==Math.floor(C[i].time/86400)) continue;
        HIST[k][1].forEach(([lv,pct,sg,kg,lt])=>{ const y=cs.priceToCoordinate(lv); if(y!=null&&y>-8&&y<h+8) bub(x,y,pct,kg,sg,lt,Math.round(i/every)%3===0); }); }
      g.restore();
    }
    /* marker labels, pushed apart so they never sit on each other (the live dashboard's drawMarks) */
    if(WS.on.mk&&bs>=1.2){
      const byT=new Map(C.map(c=>[c.time,c])), fs=Math.round(axisFont()*.72), step=fs+5; g.font=`700 ${fs}px sans-serif`; g.textBaseline='middle'; g.textAlign='left';
      const items=[], grp={};
      S.marks.forEach(m=>{ if(!m.text) return; const pos=m.position==='aboveBar'?'above':'below', k=m.time+'|'+pos; (grp[k]=grp[k]||{time:m.time,position:pos,items:[]}).items.push(m); });
      Object.values(grp).forEach(q=>{ const c=byT.get(q.time); if(!c) return; const x=X(q.time); if(x==null||x<0||x>Wp) return; const yb=cs.priceToCoordinate(q.position==='above'?c.high:c.low); if(yb==null) return;
        const m0=q.items[0], col=m0.color==='CALL'?G():m0.color==='PUT'?R():m0.color==='+'?G():m0.color==='-'?R():m0.color;
        items.push({x,yb,above:q.position==='above',text:q.items.map(i=>i.text).join(' · '),color:col}); });
      items.sort((a,b)=>a.x-b.x);
      const placed=[], hit=r=>placed.some(o=>o[0]<r[2]&&o[2]>r[0]&&o[1]<r[3]&&o[3]>r[1]);
      items.forEach(it=>{ const tw=g.measureText(it.text).width, x0=it.x+9; let y=it.yb+(it.above?-16:16), r, tries=0;
        do{ r=[x0-2,y-fs/2-2,x0+tw+2,y+fs/2+2]; if(!hit(r)) break; y+=it.above?-step:step; tries++; }while(tries<8);
        placed.push(r); obst.push(r);
        if(Math.abs(y-it.yb)>22){ g.beginPath(); g.moveTo(it.x,it.yb); g.lineTo(x0-2,y); g.lineWidth=1; g.strokeStyle=it.color; g.globalAlpha=.55; g.stroke(); g.globalAlpha=1; }
        g.lineWidth=3.5; g.strokeStyle=isL()?'rgba(255,255,255,.9)':'rgba(11,15,22,.9)'; g.strokeText(it.text,x0,y); g.fillStyle=it.color; g.fillText(it.text,x0,y); });
    }
  }
  function zoom(f){ if(!S||!S.main) return; const ts=S.main.timeScale(), r=ts.getVisibleLogicalRange(); if(!r) return; const c=(r.from+r.to)/2, half=(r.to-r.from)/2*f; ts.setVisibleLogicalRange({from:c-half,to:c+half}); }
  function showDay(i){ if(!S||!S.main) return; const s=S.starts[i]; if(s) S.main.timeScale().setVisibleRange({from:s.t-600,to:s.last+900}); }
  function showAll(){ if(!S||!S.main) return; S.main.timeScale().setVisibleRange({from:S.starts[0].t-600,to:S.starts[S.starts.length-1].last+900}); }
  window.addEventListener('resize',()=>setTimeout(draw,200));
  return {render,destroy,zoom,showDay,showAll,draw};
})();
function weekView(){
  const exp = WS.exp && weeks[WS.exp] ? WS.exp : weekKeys[0];
  if(!exp){ $('week').innerHTML='<div class="empty">No journal days yet.</div>'; return; }
  WS.exp=exp; const list=weeks[exp];
  const tot=list.reduce((s,d)=>s+dayRs(d),0), nt=list.reduce((s,d)=>s+d.trades.length,0), wins=list.reduce((s,d)=>s+d.trades.filter(t=>t.result==='TARGET'||t.result==='TRAIL').length,0);
  const opts=weekKeys.map(k=>{ const a=weeks[k]; return `<option value="${k}"${k===exp?' selected':''}>Expiry ${dlong(k)} &middot; ${dshort(a[0].date)} to ${dshort(a[a.length-1].date)} (${a.length} day${a.length===1?'':'s'})</option>`; }).join('');
  const L=WS.theme==='light', bg=L?'#ffffff':'#0b0f16';
  let h=`<div class="dayhead"><div><div class="t">Expiry week &middot; ${dlong(exp)} ${exp.slice(0,4)}</div><div class="pills"><span class="pill">${list.length} trading day${list.length===1?'':'s'} so far</span><span class="pill">${nt} trade${nt===1?'':'s'}, ${wins} won</span></div></div><div style="text-align:right"><div class="t ${cls(tot)} num">${rs(tot)}</div><div class="sub num">one lot of ${LOT}, all trades of the week</div></div></div>`;
  h+=`<div class="wtool"><select id="wsel" aria-label="Expiry week">${opts}</select><span class="nav"><button class="btn" id="zout" aria-label="Zoom out">&minus;</button><button class="btn" id="zin" aria-label="Zoom in">+</button><button class="btn" id="zall">Whole week</button></span><button class="btn" id="wtheme" title="Light / dark background, the same switch as the live dashboard">${L?'☀ Light':'☾ Dark'}</button>${WLAYERS.map(([k,l])=>`<button class="lchip" data-l="${k}" aria-pressed="${WS.on[k]}">${l}</button>`).join('')}</div>`;
  h+=`<div class="wtool" style="margin-top:0">Jump to: ${list.map((d,i)=>`<button class="lchip" data-j="${i}">${dlong(d.date)}</button>`).join('')}</div><div class="wread num" id="wread">Point at a candle for its readings. Drag to move along the week, scroll the wheel to zoom.</div>`;
  h+=`<div id="wcwrap" style="position:relative;height:clamp(520px,74vh,980px);background:${bg};border:1px solid var(--line);border-radius:10px;overflow:hidden"><div id="wchart" style="position:absolute;left:0;top:0;right:0;bottom:0"></div><canvas id="wcv" style="position:absolute;left:0;top:0;pointer-events:none;z-index:5"></canvas></div>`;
  h+=`<div class="sub" style="margin:10px 0 2px"><b>King size</b> (the largest node's gamma, crore; yellow positive, purple negative)</div><div id="wkchart" style="height:96px;background:${bg};border:1px solid var(--line);border-radius:10px;overflow:hidden"></div>`;
  h+=`<div class="sub" style="margin:10px 0 2px"><b>Net gamma</b> of the whole map (crore; green above zero, red below)</div><div id="wgchart" style="height:96px;background:${bg};border:1px solid var(--line);border-radius:10px;overflow:hidden"></div>`;
  h+=`<div class="foot" style="margin-top:10px">Drawn like the live dashboard: candles, cyan daily VWAP with dashed &plusmn;1 SD, yellow weekly VWAP, level lines in the same colours and styles (each day's own end-of-day levels, across that day only), the gamma-node bubbles (yellow positive, purple negative; K King, G gatekeeper, C ceiling, F floor), setups (grey arrows: not taken) and trades (T1, T2 &hellip; with the result). Overnight gaps are removed. The King and gamma strips need the nodes agent's readings (from 16 Nov).</div>`;
  $('week').innerHTML=h; bindWeek(list); WK.render(list);
}
function bindWeek(list){
  $('wsel').onchange=e=>{ WS.exp=e.target.value; weekView(); };
  $('zin').onclick=()=>WK.zoom(1/1.5); $('zout').onclick=()=>WK.zoom(1.5); $('zall').onclick=()=>WK.showAll();
  $('wtheme').onclick=()=>{ WS.theme=WS.theme==='light'?'dark':'light'; try{ localStorage.setItem('bp_theme',WS.theme); }catch(e){} weekView(); };
  document.querySelectorAll('#week [data-l]').forEach(b=>b.onclick=()=>{ WS.on[b.dataset.l]=!WS.on[b.dataset.l]; weekView(); });
  document.querySelectorAll('#week [data-j]').forEach(b=>b.onclick=()=>WK.showDay(+b.dataset.j));
}
function render(){
  view = location.hash.slice(1); document.body.classList.toggle('wide', view==='week' || view.startsWith('week/')); const isDay = !!byDate[view], isWeek = view==='week' || view.startsWith('week/');
  $('home').hidden = isDay || isWeek; $('dayview').hidden = !isDay; $('weekview').hidden = !isWeek;
  $('navhome').setAttribute('aria-selected', String(!isWeek)); $('navweek').setAttribute('aria-selected', String(isWeek));
  if(isWeek){ if(view.startsWith('week/')) WS.exp = view.slice(5); weekView(); }
  else if(isDay){ dayview(); window.scrollTo(0,0); } else { if(view) location.hash=''; calendar(); statsBand(); curve(); alltable(); }
  $('foot').textContent = days.length ? 'Last saved ' + (days[0].saved||'').replace('T',' ') + ' (PC clock). Source: data/journal/*.json' : 'No journal days yet. They appear after a trading session.';
}
$('prev').onclick = () => { const [Y,M]=mon.split('-').map(Number); const d=new Date(Y,M-2,1); mon=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; calendar(); };
$('next').onclick = () => { const [Y,M]=mon.split('-').map(Number); const d=new Date(Y,M,1); mon=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; calendar(); };
$('back').onclick = () => go('');
$('navhome').onclick = () => go('');
$('navweek').onclick = () => go('week');
window.addEventListener('hashchange', render);
render();
</script></body></html>
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    f = write_file()
    print("written:", f)
    if a.open:
        webbrowser.open(f.as_uri())


if __name__ == "__main__":
    main()
