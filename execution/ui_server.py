"""The execution status page (its own page: the dashboard itself is not touched). http://127.0.0.1:8780/ , this PC only.

Shows the mode (a red LIVE banner whenever live execution is on), the FYERS connection and authentication, the current order / position, actual entry price and quantity,
the stop-loss and target (the dashboard's values and what is at the broker), realised and unrealised P&L, the reconciliation result and the latest events.
Controls: Activate LIVE (you type the phrase), Switch LIVE off, EMERGENCY EXIT (cancels unfilled entries, exits the position, writes the KILL file)."""
import http.server
import json
import threading

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Execution</title>
<style>
:root{--bg:#0b0f16;--card:#121a26;--line:#243044;--txt:#e6ebf3;--mut:#8b97a8;--ok:#2ecc9a;--bad:#ef5b5b;--warn:#f5b13d}
@media (prefers-color-scheme:light){:root{--bg:#f4f6fa;--card:#fff;--line:#d5dbe4;--txt:#111827;--mut:#5b6678}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui,"Segoe UI",Arial,sans-serif}.wrap{max-width:1100px;margin:0 auto;padding:14px}
.banner{padding:12px 16px;border-radius:10px;font-weight:800;font-size:18px;letter-spacing:.04em;margin-bottom:12px}.paper{background:var(--card);border:1px solid var(--line)}.live{background:#b3202a;color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
h2{font-size:13px;margin:0 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 12px}.kv span:nth-child(odd){color:var(--mut)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}button{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px 14px;font:inherit;cursor:pointer;margin:4px 6px 4px 0}
button.danger{background:#b3202a;color:#fff;border:0}table{width:100%;border-collapse:collapse}td,th{padding:3px 6px;border-bottom:1px solid var(--line);text-align:left;font-size:12.5px}.mut{color:var(--mut)}
</style></head><body><div class="wrap"><div id="banner" class="banner paper">PAPER</div><div id="msg" class="warn" style="min-height:20px"></div>
<div class="grid" id="g"></div><div class="card" style="margin-top:12px"><h2>Events</h2><table id="ev"></table></div>
<p class="mut">Educational / personal use. The dashboard decides every trade; this page only shows and controls the execution.</p></div>
<script>
const $=s=>document.querySelector(s);const f=(x,d=2)=>x==null?'–':(+x).toFixed(d);const inr=x=>x==null?'–':(x<0?'−':'+')+'₹'+Math.abs(Math.round(x)).toLocaleString('en-IN');
const tick=b=>`<span class="${b?'ok':'bad'}">${b?'✓':'✗'}</span>`;
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'X-Requested-With':'execution-ui','Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json()}
async function load(){let s;try{s=await api('/api/status')}catch(e){$('#msg').textContent='cannot reach the engine';return}
 const live=s.mode==='LIVE';$('#banner').className='banner '+(live?'live':'paper');$('#banner').textContent=live?'● LIVE — real orders are being sent to FYERS':'PAPER — no real orders ('+s.note+')';
 const c=s.current,d=c&&c.dashboard;
 $('#g').innerHTML=`
 <div class="card"><h2>Connection</h2><div class="kv"><span>Authentication</span><span>${tick(s.auth.ok)} ${s.auth.text}</span><span>FYERS</span><span>${tick(s.connection.ok)} ${s.connection.text}</span>
  <span>Order WebSocket</span><span>${tick(s.ws.connected)} ${s.ws.connected?'connected':'down (REST polling is used)'} ${s.ws.error||''}</span>
  <span>Dashboard feed</span><span>${tick(s.dashboard.fresh)} ${s.dashboard.fresh?'fresh':'stale or missing: no new entries'}</span><span>Reconciliation</span><span>${s.recon.ok==null?'<span class="warn">?</span>':tick(s.recon.ok)} ${s.recon.text}</span>
  <span>Halted</span><span class="${s.halted?'bad':''}">${s.halted||'no'}</span></div></div>
 <div class="card"><h2>Current trade</h2>${c?`<div class="kv"><span>Contract</span><span>${c.symbol}</span><span>Phase</span><span>${c.phase}</span><span>Quantity filled</span><span>${c.qty||'–'}</span>
  <span>Actual entry price</span><span>${f(c.avg_entry)}</span><span>Stop-loss at broker</span><span>${c.sl_stop?f(c.sl_stop)+' (limit '+f(c.sl_limit)+', order '+c.sl_order+')':'–'}</span>
  <span>Dashboard stop now</span><span>${d?f(d.stop_now):'–'}</span><span>Dashboard target</span><span>${d?f(d.target)+' (managed: the dashboard sends the exit)':'–'}</span>
  <span>Dashboard last price</span><span>${d?f(d.last):'–'}</span><span>Unrealised P&L</span><span>${inr(s.unrealized)}</span><span>Exit reason</span><span>${c.exit_reason||'–'}</span></div>`:'<span class="mut">no open trade</span>'}</div>
 <div class="card"><h2>Today (${s.engine})</h2><div class="kv"><span>Realised P&L (broker-confirmed)</span><span>${inr(s.realized)}</span><span>Executed entries</span><span>${s.entries_today} of ${s.config.max_entries_per_day}</span>
  <span>Lots per trade</span><span>${s.config.lots}</span><span>Loss limit</span><span>₹${s.config.max_daily_loss_inr}</span><span>Square-off</span><span>${s.config.square_off} IST</span></div></div>
 <div class="card"><h2>Controls</h2><div class="kv"><span>LIVE needs</span><span>${s.live_checks.map(x=>tick(x.ok)+' '+x.text).join('<br>')}</span></div>
  <button onclick="arm()">Activate LIVE…</button><button onclick="off()">Switch LIVE off</button><button class="danger" onclick="emerg()">EMERGENCY EXIT</button></div>
 <div class="card"><h2>Signals today</h2><table>${s.signals.map(x=>`<tr><td>${x.symbol||''}</td><td>${x.phase}</td><td>${x.qty_filled||''}</td><td>${f(x.avg_entry)}→${f(x.avg_exit)}</td><td>${x.realized_pnl==null?'':inr(x.realized_pnl)}</td><td class="mut">${x.exit_reason||x.note||''}</td></tr>`).join('')||'<tr><td class="mut">none yet</td></tr>'}</table></div>`;
 $('#ev').innerHTML=s.events.map(e=>`<tr class="${e.level==='critical'||e.level==='warn'?'bad':''}"><td>${e.t}</td><td>${e.mode}</td><td>${e.text}</td></tr>`).join('')}
async function arm(){const p=prompt('Type exactly  ACTIVATE LIVE  to send real orders to FYERS (1 lot per dashboard trade). A restart returns to PAPER.');if(p===null)return;const r=await api('/api/arm',{phrase:p});$('#msg').textContent=r.msg;load()}
async function off(){const r=await api('/api/disarm',{});$('#msg').textContent=r.msg;load()}
async function emerg(){const p=prompt('Type  EXIT NOW  to cancel unfilled entries, exit the open position and switch LIVE off (KILL file).');if(p!=='EXIT NOW')return;const r=await api('/api/emergency',{phrase:p});$('#msg').textContent=r.msg;load()}
load();setInterval(load,3000);
</script></body></html>"""


def make_handler(runner):
    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _local_only(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def do_GET(self):
            if not self._local_only():
                return self._send(403, {"error": "local only"})
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE.encode(), "text/html")
            if self.path == "/api/status":
                try:
                    return self._send(200, runner.status())
                except Exception as e:
                    return self._send(500, {"error": str(e)[:200]})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_only() or self.headers.get("X-Requested-With") != "execution-ui":
                return self._send(403, {"ok": False, "msg": "refused"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            if self.path == "/api/arm":
                ok, msg = runner.arm(str(body.get("phrase", "")))
            elif self.path == "/api/disarm":
                ok, msg = runner.disarm()
            elif self.path == "/api/emergency":
                if body.get("phrase") != "EXIT NOW":
                    return self._send(200, {"ok": False, "msg": "type EXIT NOW to confirm"})
                ok, msg = runner.emergency()
            else:
                return self._send(404, {"ok": False, "msg": "not found"})
            self._send(200, {"ok": ok, "msg": msg})

        def log_message(self, *a):
            pass
    return H


def serve(runner, port, host="127.0.0.1"):
    srv = http.server.ThreadingHTTPServer((host, port), make_handler(runner))
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="execution-ui")
    t.start()
    return srv
