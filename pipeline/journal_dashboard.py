"""The trading journal as one self-contained page (data/journal_dashboard.html): every day in data/journal/ with its paper trades, the setups that were
taken or ignored, the index chart with the levels, and the option price of each trade. Private: it is a local file, nothing is uploaded.

  python pipeline/journal_dashboard.py          write data/journal_dashboard.html
  python pipeline/journal_dashboard.py --open   write it and open it in the browser

The live stream page also serves it at http://127.0.0.1:8766/journal, and every journal save rewrites the file, so it is always current."""
import argparse
import json
import pathlib
import sys
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDER = ROOT / "data" / "journal"
OUT = ROOT / "data" / "journal_dashboard.html"
SETTINGS = ROOT / "data" / "journal_settings.json"     # {"capital": 25000, "lot": 65}: rupee results = (exit - entry) x lot; Nifty lot is 65 (NSE circular FAOP70616, from Jan 2026)
DEFAULTS = {"capital": 25000, "lot": 65, "from": "2026-11-11"}      # "from": the first day the page shows; earlier journal files stay on disk, they are only left out


def load_days(folder=FOLDER, start=None):
    days = []
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
            "setups": d.get("setups", []), "candles": [[r[0], r[2], r[3], r[4]] for r in d.get("index_candles_hm_ohlcv", [])],
            "levels": (d.get("levels_used") or {}).get("levels", {}), "close": (d.get("levels_used") or {}).get("close"),
            "opening": d.get("opening_candles"), "saved": d.get("saved_at"),
        })
    return days


def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return s


def build_html(folder=FOLDER):
    cfg = load_settings()
    data = json.dumps({"days": load_days(folder, cfg.get("from")), "settings": cfg}, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", data)


def write_file(folder=FOLDER, out=OUT):
    out = pathlib.Path(out)
    out.write_text(build_html(folder), encoding="utf-8")
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
#home,#dayview{margin:0;width:100%}
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
.band.small{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
</style></head><body><div class="wrap">
<header><div><h1>Trading Journal</h1><div class="sub">Paper trades from the BharatPulse stream page. +40% target, &minus;10% stop that trails up (+20% &rarr; +5%, +30% &rarr; +15%, +35% &rarr; +30%), no trade in the first 10 minutes (before 9:25), one trade at a time, one lot of 65 on a &#8377;25,000 capital. A record of what the rules did, not advice.</div></div></header>
<div id="home" class="wrap" style="gap:18px">
<section class="panel"><div class="mhead"><div class="nav"><button class="btn" id="prev" aria-label="Previous month">&lsaquo;</button><span class="t" id="mname"></span><button class="btn" id="next" aria-label="Next month">&rsaquo;</button></div><div id="msum" class="num"></div></div><div class="cal" id="cal"></div><div class="legend"><span>Each box is one trading day: result in rupees and number of trades. Click a day for its details.</span></div></section>
<section><h2>Overall</h2><div class="band small" id="stats"></div></section>
<section class="panel"><h2>Result after each trade</h2><div class="chartbox" id="curve"></div></section>
<section class="panel"><h2>All days</h2><div class="tablewrap"><table id="all"></table></div></section>
</div>
<div id="dayview" class="wrap" style="gap:18px" hidden><div><button class="btn" id="back">&lsaquo; Calendar</button></div><section class="panel" id="day"></section></div>
<div class="foot" id="foot"></div>
</div>
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
function status(s){ if(s.startsWith('TRADE')) return ['Taken','tgt']; if(s.startsWith('ignored')) return ['Ignored: a trade was open','mut']; if(s.startsWith('no new trade')) return ['After the 14:45 cutoff','mut']; if(s.startsWith('no trade before')) return ['In the first 10 minutes (no trade)','mut']; return [s,'mut']; }
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
  d.trades.forEach(t=>{ if(!t.entry_time||!t.exit_time) return; const a=x(mins(t.entry_time)), b=Math.max(x(mins(t.exit_time)),a+3);
    s+=`<rect x="${a}" y="${T}" width="${b-a}" height="${ih}" fill="${(t.result==='TARGET'||t.result==='TRAIL')?'var(--up)':'var(--dn)'}" opacity=".13"><title>Trade ${t.n}: ${t.entry_time} to ${t.exit_time}, ${pct(t.pnl_pct)}</title></rect>`; });
  lv.forEach(([k,v])=>{ s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--vio)" stroke-dasharray="5 4" opacity=".7"/><text x="${W-R+6}" y="${y(v)+4}" style="fill:var(--vio)">${k} ${Math.round(v)}</text>`; });
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
function card(t){
  const win=t.result==='TARGET'||t.result==='TRAIL', dur=(t.entry_time&&t.exit_time)?(mins(t.exit_time)-mins(t.entry_time)):null;
  return `<div class="card"><div class="top"><span class="name">Trade ${t.n} &middot; <span class="chip ${t.side==='CALL'?'call':'put'}">${t.side}</span> ${t.strike} ${t.side==='CALL'?'CE':'PE'}</span><span class="chip ${win?'tgt':t.result==='STOP'?'stp':'open'}">${t.result==='TARGET'?'Target':t.result==='TRAIL'?'Trailing stop':t.result==='STOP'?'Stop':esc(t.result||'Open')} ${rs(tradeRs(t))} (${pct(t.pnl_pct)})</span></div>
  <div class="sub" style="margin:0">${esc(t.setup)}</div>${spark(t)}
  <div class="kv num"><span>In</span><span>${t.entry_time} at ${t.entry}</span><span>Out</span><span>${t.exit_time||'&ndash;'}${t.exit!=null?' at '+t.exit:''}${dur!=null?' ('+dur+' min)':''}</span><span>Cost of one lot</span><span>₹${Math.round(t.entry*LOT).toLocaleString('en-IN')}${t.entry*LOT>CAP?' (more than the capital)':''}</span><span>Expiry</span><span>${t.expiry||''}</span><span>Setups skipped meanwhile</span><span>${t.ignored}</span></div>${(t.shifts&&t.shifts.length)?`<div class="sub" style="margin:6px 0 0">${t.shifts.map(esc).join('<br>')}</div>`:''}</div>`;
}
function setups(d){
  if(!d.setups.length) return '<div class="empty">No setups recorded.</div>';
  return `<table><thead><tr><th>Time</th><th>Side</th><th>Strike</th><th>Level</th><th>What happened</th></tr></thead><tbody>${d.setups.map(q=>{const [txt,c]=status(q.status);return `<tr class="${q.status.startsWith('TRADE')?'taken':''}"><td class="num">${q.time}</td><td><span class="chip ${q.side==='CALL'?'call':'put'}">${q.side}</span></td><td class="num">${q.strike}</td><td>${esc(q.level)}</td><td><span class="chip ${c}">${esc(txt)}</span>${q.status.startsWith('TRADE')?' '+esc(q.status):''}</td></tr>`;}).join('')}</tbody></table>`;
}
function levels(d){
  const e=Object.entries(d.levels).sort((a,b)=>b[1]-a[1]); if(!e.length) return '<div class="empty">No levels saved.</div>';
  return `<div class="kv num">${e.map(([k,v])=>`<span>${esc(k)}</span><span>${v}</span>`).join('')}</div>`;
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
  <div class="legend"><span>Line: index close, 1-minute</span><span>Shaded: a trade was open</span><span>&#9650; &#9660; filled: setup taken &middot; hollow: setup not taken</span>${LVSHOWN ? '<span style="color:var(--vio)">Dashed: end-of-day levels near the day range</span>' : '<span>No end-of-day level was near the day range</span>'}</div>
  <h2 style="margin-top:18px">Trades</h2><div class="cards">${d.trades.length?d.trades.map(card).join(''):'<div class="empty">No trade was taken.</div>'}</div>
  <div class="two" style="margin-top:18px"><div><h2>Setups, in time order</h2><div class="tablewrap">${setups(d)}</div></div><div><h2>Levels the page used</h2>${levels(d)}</div></div>`;
}
function alltable(){
  $('all').innerHTML = `<thead><tr><th>Date</th><th>Trades</th><th>Target</th><th>Trail</th><th>Stop</th><th>Result</th><th>% of capital</th><th>Opening candles</th></tr></thead><tbody>${days.map(d=>`<tr class="row" tabindex="0" data-d="${d.date}"><td>${wk(d.date)} ${d.date.slice(0,4)}</td><td class="num">${d.trades.length}</td><td class="num">${d.trades.filter(t=>t.result==='TARGET').length}</td><td class="num">${d.trades.filter(t=>t.result==='TRAIL').length}</td><td class="num">${d.trades.filter(t=>t.result==='STOP').length}</td><td class="num ${cls(dayRs(d))}">${rs(dayRs(d))}</td><td class="num ${cls(dayRs(d))}">${pct(dayRs(d)/CAP*100)}</td><td>${esc(d.opening||'n/a')}</td></tr>`).join('')}</tbody>`;
  $('all').querySelectorAll('tr.row').forEach(r=>{const f=()=>go(r.dataset.d);r.onclick=f;r.onkeydown=e=>{if(e.key==='Enter')f();};});
}
function render(){
  view = location.hash.slice(1); const isDay = !!byDate[view];
  $('home').hidden = isDay; $('dayview').hidden = !isDay;
  if(isDay){ dayview(); window.scrollTo(0,0); } else { if(view) location.hash=''; calendar(); statsBand(); curve(); alltable(); }
  $('foot').textContent = days.length ? 'Last saved ' + (days[0].saved||'').replace('T',' ') + ' (PC clock). Source: data/journal/*.json' : 'No journal days yet. They appear after a trading session.';
}
$('prev').onclick = () => { const [Y,M]=mon.split('-').map(Number); const d=new Date(Y,M-2,1); mon=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; calendar(); };
$('next').onclick = () => { const [Y,M]=mon.split('-').map(Number); const d=new Date(Y,M,1); mon=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; calendar(); };
$('back').onclick = () => go('');
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
