"""BharatPulse refresh page - runs on YOUR PC only (127.0.0.1), started by 'Refresh BharatPulse.bat'.

Open page > press "Refresh dashboard" > sign in to Fyers (your PIN / TOTP are typed on Fyers' own page; this program never sees them)
> the page swaps the one-time code for today's data-only token, stores it as an encrypted GitHub secret, starts the cloud build and
shows its progress. The token is never displayed or written anywhere except data/fyers_token.json (git-ignored) and the GitHub secret.

Needs the environment variables FYERS_APP_ID and FYERS_APP_SECRET and the GitHub CLI (gh) signed in. The Fyers app's redirect URL
must be exactly http://127.0.0.1:5050/
"""
import base64
import datetime as dt
import hashlib
import http.server
import json
import os
import pathlib
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import requests

HOST, PORT = "127.0.0.1", 5050
REDIRECT = f"http://{HOST}:{PORT}/"
BASE = "https://api-t1.fyers.in/api/v3"
ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "data" / "fyers_token.json"
LIVE = "https://abhijitkikale.github.io/bharatpulse/"
STEPS = [("login", "Sign in to Fyers"), ("token", "Get today's data token"), ("github", "Send it to GitHub (encrypted)"),
         ("start", "Start the cloud build"), ("build", "Build and publish the dashboard")]
S = {"state": None, "stage": "idle", "error": None, "run_url": None}
LOCK = threading.Lock()


class Fail(Exception):
    pass


def env(name):
    return os.environ.get(name, "").strip()


def run(cmd, stdin=None, timeout=90):
    return subprocess.run(cmd, input=stdin, text=True, capture_output=True, timeout=timeout)


def gh_ok():
    try:
        return run(["gh", "auth", "status"], timeout=20).returncode == 0
    except Exception:
        return False


def set_stage(stage, error=None):
    with LOCK:
        if stage == "error":
            S["failed_at"] = S["stage"]                                 # which step went wrong
        S["stage"], S["error"] = stage, error


def token_info():
    if not TOKEN_FILE.exists():
        return {"have": False}
    saved = dt.datetime.fromtimestamp(TOKEN_FILE.stat().st_mtime)
    expires = saved.replace(hour=6, minute=30, second=0, microsecond=0)
    if expires <= saved:
        expires += dt.timedelta(days=1)
    return {"have": True, "saved": saved.strftime("%d %b %H:%M"), "valid": dt.datetime.now() < expires, "expires": expires.strftime("%d %b %H:%M")}


def watch_build(t0):
    """Follow the cloud build that was started after t0 until it finishes (max 15 minutes)."""
    end = time.time() + 900
    while time.time() < end:
        time.sleep(5)
        try:
            p = run(["gh", "run", "list", "--workflow", "refresh.yml", "--limit", "4", "--json", "databaseId,status,conclusion,url,createdAt"], timeout=40)
            runs = json.loads(p.stdout or "[]")
        except Exception:
            continue
        for r in runs:
            created = dt.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")).timestamp()
            if created >= t0 - 10:
                with LOCK:
                    S["run_url"] = r["url"]
                if r["status"] == "completed":
                    if r["conclusion"] == "success":
                        return
                    raise Fail("The cloud build did not finish successfully. Open the build page to see why.")
                break
    raise Fail("The cloud build is taking longer than 15 minutes. Check the build page.")


def finish_login(code):
    try:
        app_id, secret = env("FYERS_APP_ID"), env("FYERS_APP_SECRET")
        set_stage("token")
        r = requests.post(f"{BASE}/validate-authcode", json={"grant_type": "authorization_code", "code": code,
                          "appIdHash": hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()}, timeout=30)
        try:
            j = r.json()
        except ValueError:
            j = {}
        token = j.get("access_token")
        if not token:
            raise Fail("Fyers did not give a token: " + str(j.get("message") or f"HTTP {r.status_code}")[:120])
        TOKEN_FILE.parent.mkdir(exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({"app_id": app_id, "access_token": token}), encoding="utf-8")
        set_stage("github")
        for name, val in (("FYERS_APP_ID", app_id), ("FYERS_TOKEN", token)):
            p = run(["gh", "secret", "set", name], stdin=val)
            if p.returncode:
                raise Fail(f"Could not store {name} on GitHub: {p.stderr.strip()[:150]}")
        set_stage("start")
        t0 = time.time()
        p = run(["gh", "workflow", "run", "refresh.yml"])
        if p.returncode:
            raise Fail("Could not start the cloud build: " + p.stderr.strip()[:150])
        set_stage("build")
        watch_build(t0)
        set_stage("done")
    except Fail as e:
        set_stage("error", str(e))
    except Exception as e:
        set_stage("error", f"Unexpected problem ({type(e).__name__}). Try again.")


def status():
    with LOCK:
        stage, err, url = S["stage"], S["error"], S["run_url"]
    keys = [k for k, _ in STEPS]
    failed = S.get("failed_at") if stage == "error" else None
    cur = len(keys) if stage == "done" else keys.index(failed) if failed in keys else keys.index(stage) if stage in keys else -1
    steps = []
    for i, (k, label) in enumerate(STEPS):
        st = "done" if i < cur else ("error" if failed and i == cur else "active" if i == cur else "todo")
        steps.append({"label": label, "state": st})
    return {"stage": stage, "error": err, "run_url": url, "steps": steps, "live": LIVE,
            "env_ok": bool(env("FYERS_APP_ID") and env("FYERS_APP_SECRET")), "gh_ok": gh_ok(), "token": token_info()}


def logo():
    f = ROOT / "pipeline" / "assets" / "logo.png"
    return "data:image/png;base64," + base64.b64encode(f.read_bytes()).decode() if f.exists() else ""


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BharatPulse Refresh</title><meta name="referrer" content="no-referrer">
<style>
:root{--bg:#0b0f16;--card:#141a24;--line:#263040;--txt:#e6edf3;--mut:#8b97a8;--pos:#2ecc9a;--neg:#ef5b5b;--acc:#4cc9f0;--warn:#f5a623}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif;display:flex;justify-content:center;padding:28px 16px}
.wrap{width:100%;max-width:640px}.logo{height:56px;margin-bottom:6px}h1{font-size:22px;margin:4px 0 2px}.sub{color:var(--mut);margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:14px}
.btn{display:block;width:100%;background:var(--acc);color:#04202a;border:0;border-radius:10px;padding:14px;font:600 16px inherit;cursor:pointer}
.btn:disabled{opacity:.45;cursor:not-allowed}.btn.ghost{background:transparent;color:var(--txt);border:1px solid var(--line);font-weight:500;padding:9px;font-size:14px;width:auto;display:inline-block;text-decoration:none}
.row{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid var(--line)}.row:last-child{border:0}
.k{color:var(--mut)}.ok{color:var(--pos)}.bad{color:var(--neg)}.warn{color:var(--warn)}
ul{list-style:none;margin:0;padding:0}li{display:flex;align-items:center;gap:12px;padding:9px 0}
.dot{width:24px;height:24px;border-radius:50%;border:2px solid var(--line);display:flex;align-items:center;justify-content:center;font-size:13px;flex:none}
.error .dot{background:var(--neg);border-color:var(--neg);color:#2b0507}.done .dot{background:var(--pos);border-color:var(--pos);color:#04210f}.active .dot{border-color:var(--acc);animation:p 1s infinite}.todo{color:var(--mut)}
@keyframes p{50%{box-shadow:0 0 0 5px rgba(76,201,240,.25)}}.msg{margin-top:10px;padding:10px 12px;border-radius:8px;background:#1d1417;border:1px solid #4a2a2f;color:#ffb4b4}
.fin{background:#102019;border:1px solid #1f4a38;color:#a7f0d2;border-radius:8px;padding:10px 12px;margin-top:10px}.small{font-size:13px;color:var(--mut)}a{color:var(--acc)}
</style></head><body><div class="wrap">
<img class="logo" id="logo" alt="BharatPulse"><h1>Refresh BharatPulse</h1><div class="sub">Sign in to Fyers once a day and the dashboard rebuilds with fresh data.</div>
<div class="card"><button class="btn" id="go">Refresh dashboard</button><div id="note" class="small" style="margin-top:10px"></div></div>
<div class="card" id="progress" style="display:none"><ul id="steps"></ul><div id="err" class="msg" style="display:none"></div><div id="fin" class="fin" style="display:none"></div></div>
<div class="card"><div class="row"><span class="k">Fyers app details on this PC</span><span id="s-env"></span></div>
<div class="row"><span class="k">GitHub sign-in</span><span id="s-gh"></span></div>
<div class="row"><span class="k">Today's data token</span><span id="s-tok"></span></div></div>
<div style="display:flex;gap:10px;flex-wrap:wrap"><a class="btn ghost" id="live" target="_blank" rel="noopener noreferrer">Open the live dashboard</a>
<a class="btn ghost" id="run" target="_blank" rel="noopener noreferrer" style="display:none">Open the build page</a><button class="btn ghost" id="quit">Close this helper</button></div>
<p class="small">Your PIN and one-time code are typed on Fyers' own page. This helper runs only on this PC and never shows or sends your login details anywhere except GitHub's encrypted secret store (a one-day, data-only token).</p>
</div><script>
const $=s=>document.querySelector(s);$('#logo').src="__LOGO__";
function pill(el,ok,yes,no){el.textContent=ok?yes:no;el.className=ok?'ok':'bad'}
async function tick(){let d;try{d=await (await fetch('/status',{cache:'no-store'})).json()}catch(e){return}
 pill($('#s-env'),d.env_ok,'ready','missing (see setup note)');pill($('#s-gh'),d.gh_ok,'signed in','not signed in');
 const t=d.token;$('#s-tok').textContent=!t.have?'none yet':(t.valid?'saved '+t.saved+', works until '+t.expires:'expired ('+t.saved+')');$('#s-tok').className=t.have&&t.valid?'ok':'warn';
 const busy=!['idle','done','error'].includes(d.stage);$('#go').disabled=busy||!d.env_ok||!d.gh_ok;
 $('#go').textContent=busy?'Working...':(d.stage==='done'?'Refresh again':'Refresh dashboard');
 let note='';if(!d.env_ok)note='Setup needed: set FYERS_APP_ID and FYERS_APP_SECRET on this PC (see the setup steps), then open this helper again.';else if(!d.gh_ok)note='Setup needed: sign in to GitHub with the GitHub CLI (gh auth login).';
 $('#note').textContent=note;
 $('#progress').style.display=d.stage==='idle'?'none':'block';
 $('#steps').innerHTML='';d.steps.forEach(s=>{const li=document.createElement('li');li.className=s.state;const dot=document.createElement('span');dot.className='dot';dot.textContent=s.state==='done'?'\\u2713':s.state==='error'?'!':'';li.appendChild(dot);li.appendChild(document.createTextNode(s.label));$('#steps').appendChild(li)});
 const e=$('#err');e.style.display=d.error?'block':'none';e.textContent=d.error||'';
 const f=$('#fin');f.style.display=d.stage==='done'?'block':'none';f.textContent='Done. The dashboard has been rebuilt. Reload the live page in a minute to see the new data.';
 $('#live').href=d.live;const r=$('#run');if(d.run_url){r.href=d.run_url;r.style.display='inline-block'}else r.style.display='none';}
$('#go').onclick=()=>{location.href='/login'};
$('#quit').onclick=async()=>{try{await fetch('/quit',{method:'POST'})}catch(e){};document.body.innerHTML='<p style="padding:30px">You can close this tab.</p>'};
tick();setInterval(tick,2000);
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, where):
        self.send_response(302)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/status":
            return self._send(200, json.dumps(status()), "application/json")
        if u.path == "/login":
            if not (env("FYERS_APP_ID") and env("FYERS_APP_SECRET")):
                return self._redirect("/")
            with LOCK:
                S["state"] = secrets.token_urlsafe(16)
                S["run_url"] = None
            set_stage("login")
            return self._redirect(f"{BASE}/generate-authcode?" + urllib.parse.urlencode(
                {"client_id": env("FYERS_APP_ID"), "redirect_uri": REDIRECT, "response_type": "code", "state": S["state"]}))
        if u.path == "/" and ("auth_code" in q or q.get("s") == "error"):
            with LOCK:
                ok_state = S["state"] is not None and q.get("state") == S["state"]
            if not ok_state:
                set_stage("error", "That login did not come from this page (safety check failed). Press Refresh again.")
            elif "auth_code" not in q:
                set_stage("error", "The Fyers login was not completed. Press Refresh again.")
            else:
                with LOCK:
                    S["state"] = None                                   # one use only
                threading.Thread(target=finish_login, args=(q["auth_code"],), daemon=True).start()
            return self._redirect("/")                                   # drops the one-time code from the address bar
        if u.path == "/":
            return self._send(200, PAGE.replace("__LOGO__", logo()))
        self._send(404, "Not found", "text/plain")

    def do_POST(self):
        if self.path == "/quit":
            self._send(200, "bye", "text/plain")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, "Not found", "text/plain")

    def log_message(self, *a):                  # keep the one-time code out of the console
        pass


def main():
    try:
        srv = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        sys.exit(f"Port {PORT} is already in use. Close the other BharatPulse helper window and try again.")
    print(f"BharatPulse refresh page: {REDIRECT}   (close this window to stop it)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(REDIRECT)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
