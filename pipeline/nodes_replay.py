"""End-of-day node replay as one self-contained page (data/nodes_replay.html): pick a saved day, drag the slider (or press play) to see the gamma nodes as they
stood at each saved reading, the day's price with every node approach marked (green held, red passed, grey chop), and the reaction ledger tables with the
dealer-sign check. Private: a local file built from data/nodes/*.jsonl, data/journal/*.json; nothing is uploaded.

  python pipeline/nodes_replay.py          write data/nodes_replay.html
  python pipeline/nodes_replay.py --open   write it and open it in the browser

The live stream page also serves it at http://127.0.0.1:8766/nodes-replay (built fresh at each visit). Days exist from the day the nodes agent first ran in a session."""
import argparse
import json
import pathlib
import sys
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import nodes_ledger  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
NODES_DIR = ROOT / "data" / "nodes"
JOURNAL_DIR = ROOT / "data" / "journal"
OUT = ROOT / "data" / "nodes_replay.html"


def build_data(nodes_dir=NODES_DIR, journal_dir=JOURNAL_DIR):
    days, all_events = [], []
    for f in sorted(pathlib.Path(nodes_dir).glob("????-??-??.jsonl")):
        readings = nodes_ledger.load_readings(f)
        if not readings:
            continue
        candles = nodes_ledger.load_candles(f.stem, journal_dir)
        ev = nodes_ledger.events_for_day(f.stem, readings, candles)
        all_events += ev
        days.append({
            "date": f.stem,
            "readings": [{"t": r["t"].strftime("%H:%M"), "spot": r["spot"], "flip": r["flip"], "tot": r["total_cr"], "k": r["k"], "n": r["n"]} for r in readings],
            "candles": [] if candles is None else [[t.strftime("%H:%M"), float(r.h), float(r.l), float(r.c)] for t, r in candles.iterrows()],
            "events": [{"time": e["time"], "k": e["k"], "outcome": e["outcome"], "role": e["role"], "pct": e["pct"], "sign": e["sign"]} for e in ev],
        })
    return {"days": days, "summary": nodes_ledger.summarize(all_events), "events": len(all_events)}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Node replay</title>
<style>
:root{--bg:#0b0f16;--card:#121a26;--line:#243044;--txt:#e6ebf3;--mut:#8b97a8;--up:#2ecc9a;--dn:#ef5b5b;--pos:#ffd54f;--neg:#b388ff}
@media (prefers-color-scheme:light){:root{--bg:#f4f6fa;--card:#fff;--line:#d5dbe4;--txt:#111827;--mut:#5b6678}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui,"Segoe UI",Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px}h1{margin:0 0 4px;font-size:22px}.sub{color:var(--mut);margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:14px}
.row{display:flex;gap:14px;flex-wrap:wrap}.col{flex:1 1 520px;min-width:0}canvas{width:100%;display:block}
.ctl{display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap}.ctl input[type=range]{flex:1;min-width:200px}
button,select{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:5px 12px;font:inherit;cursor:pointer}
.kv{display:flex;gap:18px;flex-wrap:wrap;color:var(--mut);margin-top:6px}.kv b{color:var(--txt)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--line)}th:first-child,td:first-child{text-align:left}th{color:var(--mut);font-weight:600}
h2{font-size:15px;margin:0 0 6px}.note{color:var(--mut);font-size:12.5px;margin-top:6px}.empty{padding:30px;text-align:center;color:var(--mut)}
.pill{display:inline-block;border-radius:6px;padding:0 7px;font-size:12px;color:#0b0f16}
</style></head><body><div class="wrap">
<h1>Node replay</h1><div class="sub">The gamma nodes as the agent saw them through the day, with every approach of price to a significant node and what came next. For review, not advice.</div>
<div id="app"></div>
</div>
<script>
const D=__DATA__;
const $=s=>document.querySelector(s),app=$('#app');
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function rows(list){return list.map(r=>`<tr><td>${r.label}</td><td>${r.n}</td><td>${r.held==null?'':r.held+'%'}</td><td>${r.passed==null?'':r.passed+'%'}</td><td>${r.chop==null?'':r.chop+'%'}</td></tr>`).join('')}
function summaryHtml(){
  const S=D.summary;if(!D.events)return '<div class="empty">No node approaches yet. They are logged from the first live session of the nodes agent.</div>';
  let h=`<div class="card"><h2>Reaction ledger: ${D.events} approaches to a significant node</h2><div class="note">held = price moved 25 points back the way it came within 30 minutes; passed = 25 points through the node; chop = neither. Small groups are noise; the picture firms up over weeks.</div>
  <table style="margin-top:8px"><tr><th>All</th><th>n</th><th>held</th><th>passed</th><th>chop</th></tr>${rows([S.overall])}</table>
  <div class="note"><b>Dealer-sign check:</b> ${S.sign_check}</div></div><div class="row">`;
  Object.entries(S.groups).forEach(([t,rs])=>{h+=`<div class="col card"><h2>${t}</h2><table><tr><th></th><th>n</th><th>held</th><th>passed</th><th>chop</th></tr>${rows(rs)}</table></div>`});
  return h+'</div>';
}
function draw(){
  if(!D.days.length){app.innerHTML='<div class="card empty">No saved days yet. The nodes agent saves a reading every 2 minutes in market hours from the first live session; the replay and the ledger appear after that day.</div>';return}
  const sel=window.__day||D.days.length-1,day=D.days[sel];
  app.innerHTML=`<div class="card"><div class="ctl"><label>Day <select id="dsel">${D.days.map((d,i)=>`<option value="${i}" ${i===sel?'selected':''}>${d.date}</option>`).join('')}</select></label>
   <button id="play">▶ Play</button><input id="sl" type="range" min="0" max="${day.readings.length-1}" value="0"><span id="tm" style="min-width:5em;font-weight:700"></span></div>
   <div class="kv" id="kv"></div></div>
   <div class="row"><div class="col card"><h2>Price and approaches</h2><canvas id="cv" height="380"></canvas><div class="note"><span class="pill" style="background:var(--up)">held</span> <span class="pill" style="background:var(--dn)">passed</span> <span class="pill" style="background:#8b97a8">chop</span> dots sit at the node's strike when price approached it.</div></div>
   <div class="col card" style="flex:0 1 420px"><h2>Nodes at this reading</h2><canvas id="bars" height="380"></canvas></div></div>`+summaryHtml();
  $('#dsel').onchange=e=>{window.__day=+e.target.value;stop();draw()};
  const sl=$('#sl');let timer=null;
  function stop(){if(timer){clearInterval(timer);timer=null;const b=$('#play');if(b)b.textContent='▶ Play'}}
  $('#play').onclick=()=>{if(timer){stop();return}$('#play').textContent='⏸ Pause';timer=setInterval(()=>{let v=+sl.value+1;if(v>+sl.max){stop();return}sl.value=v;show()},350)};
  sl.value=Math.min(window.__i||0,+sl.max);sl.oninput=show;
  function hm2x(hm,W,x0,x1){const m=(+hm.slice(0,2))*60+(+hm.slice(3,5));return x0+(m-555)/(930-555)*(x1-x0)}
  function show(){
    window.__i=+sl.value;const r=day.readings[+sl.value];$('#tm').textContent=r.t;
    const king=r.k[r.n.reduce((b,v,i)=>Math.abs(v)>Math.abs(r.n[b])?i:b,0)];
    $('#kv').innerHTML=`<span>Spot <b>${r.spot.toFixed(2)}</b></span><span>Gamma flip <b>${r.flip==null?'–':r.flip}</b></span><span>Regime <b>${r.tot>0?'positive':'negative'}</b> (${r.tot} cr)</span><span>King <b>${king}</b></span>`;
    // price chart
    const cv=$('#cv'),W=cv.clientWidth,H=380,dpr=devicePixelRatio||1;cv.width=W*dpr;cv.height=H*dpr;const g=cv.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,W,H);
    const cs=day.candles,x0=44,x1=W-8,y0=10,y1=H-22;
    let lo=Infinity,hi=-Infinity;(cs.length?cs:day.readings.map(q=>[q.t,q.spot,q.spot,q.spot])).forEach(c=>{lo=Math.min(lo,c[2]);hi=Math.max(hi,c[1])});
    day.events.forEach(e=>{lo=Math.min(lo,e.k-5);hi=Math.max(hi,e.k+5)});const pad=(hi-lo)*.05||5;lo-=pad;hi+=pad;
    const Y=p=>y1-(p-lo)/(hi-lo)*(y1-y0);g.font='11px system-ui';g.fillStyle=css('--mut');g.strokeStyle=css('--line');g.lineWidth=1;
    for(let i=0;i<=4;i++){const p=lo+(hi-lo)*i/4,y=Y(p);g.beginPath();g.moveTo(x0,y);g.lineTo(x1,y);g.stroke();g.fillText(p.toFixed(0),2,y+4)}
    ['09:15','10:30','12:00','13:30','15:30'].forEach(t=>{const x=hm2x(t,W,x0,x1);g.fillText(t,x-14,H-6)});
    g.strokeStyle=css('--txt');g.lineWidth=1.4;g.beginPath();(cs.length?cs.map(c=>[c[0],c[3]]):day.readings.map(q=>[q.t,q.spot])).forEach((c,j)=>{const x=hm2x(c[0],W,x0,x1),y=Y(c[1]);j?g.lineTo(x,y):g.moveTo(x,y)});g.stroke();
    day.events.forEach(e=>{const x=hm2x(e.time,W,x0,x1),y=Y(e.k);g.beginPath();g.arc(x,y,5,0,6.283);g.fillStyle=e.outcome==='held'?css('--up'):e.outcome==='passed'?css('--dn'):'#8b97a8';g.fill()});
    const cx=hm2x(r.t,W,x0,x1);g.strokeStyle='#7aa2ff';g.setLineDash([4,4]);g.beginPath();g.moveTo(cx,y0);g.lineTo(cx,y1);g.stroke();g.setLineDash([]);
    // bars at this reading
    const bv=$('#bars'),BW=bv.clientWidth,bh=380;bv.width=BW*dpr;bv.height=bh*dpr;const b=bv.getContext('2d');b.setTransform(dpr,0,0,dpr,0,0);b.clearRect(0,0,BW,bh);
    const near=r.k.map((k,i)=>[k,r.n[i]]).filter(([k])=>Math.abs(k-r.spot)<=600).sort((a,c)=>c[0]-a[0]);
    const mx=Math.max(...r.n.map(Math.abs))||1,rh=Math.min(16,(bh-10)/Math.max(near.length,1));b.font='11px system-ui';b.textBaseline='middle';
    near.forEach(([k,v],i)=>{const y=5+i*rh+rh/2,w=Math.abs(v)/mx*(BW-110);if(Math.abs(k-r.spot)<=25){b.fillStyle='rgba(122,162,255,.18)';b.fillRect(0,y-rh/2,BW,rh)}
      b.fillStyle=css('--mut');b.textAlign='right';b.fillText(k,44,y);const fr=Math.abs(v)/mx;b.globalAlpha=fr>=.6?1:fr>=.25?.6:.25;b.fillStyle=v>=0?css('--pos'):css('--neg');b.fillRect(50,y-rh/2+2,Math.max(w,1),rh-4);b.globalAlpha=1;if(k===king){b.strokeStyle=css('--txt');b.lineWidth=2;b.strokeRect(50,y-rh/2+2,Math.max(w,1),rh-4)}
      b.fillStyle=css('--txt');b.textAlign='left';if(Math.abs(v)/mx>=.25)b.fillText(Math.round(Math.abs(v)/mx*100)+'%'+(k===king?' ★':''),54+w,y)});
  }
  show();
}
draw();addEventListener('resize',()=>{if(D.days.length)draw()});
</script></body></html>"""


def build_html(nodes_dir=NODES_DIR, journal_dir=JOURNAL_DIR):
    data = json.dumps(build_data(nodes_dir, journal_dir), separators=(",", ":")).replace("</", "<\\/")
    return PAGE.replace("__DATA__", data)


def write_file(nodes_dir=NODES_DIR, journal_dir=JOURNAL_DIR, out=OUT):
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(nodes_dir, journal_dir), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    p = write_file()
    print("written:", p)
    if a.open:
        webbrowser.open(p.as_uri())
