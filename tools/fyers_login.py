"""Evening Fyers login for a NON-TRADING (data only) API app.

Run once after the market closes:   python tools/fyers_login.py
  1. opens the Fyers login in your browser - you enter your own PIN / TOTP there (this script never sees them)
  2. catches the one-time code on http://127.0.0.1:5050/ and swaps it for today's access token
  3. saves the token to data/fyers_token.json (git-ignored) and, unless --no-push, stores it as the GitHub secret
     FYERS_TOKEN (with FYERS_APP_ID) and starts the cloud build.  The token stops working at about 6:30 AM next day.

Needs two environment variables on this PC (never commit them):  FYERS_APP_ID  and  FYERS_APP_SECRET.
The app's redirect URL on myapi.fyers.in must be exactly   http://127.0.0.1:5050/
"""
import hashlib
import http.server
import json
import os
import pathlib
import secrets
import subprocess
import sys
import threading
import urllib.parse
import webbrowser

import requests

HOST, PORT = "127.0.0.1", 5050
REDIRECT = f"http://{HOST}:{PORT}/"
BASE = "https://api-t1.fyers.in/api/v3"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "fyers_token.json"


def need(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"Set the environment variable {name} first (see the top of this file).")
    return v


class Catcher(http.server.BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "auth_code" in q or "code" in q and "s" in q:
            Catcher.result = {k: v[0] for k, v in q.items()}
            body = b"<h3>BharatPulse: login received. You can close this tab.</h3>"
        else:
            body = b"<h3>Waiting for the Fyers login...</h3>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                     # keep the one-time code out of the console
        pass


def main():
    app_id, secret = need("FYERS_APP_ID"), need("FYERS_APP_SECRET")
    push = "--no-push" not in sys.argv
    state = secrets.token_urlsafe(16)
    url = f"{BASE}/generate-authcode?" + urllib.parse.urlencode(
        {"client_id": app_id, "redirect_uri": REDIRECT, "response_type": "code", "state": state})
    srv = http.server.HTTPServer((HOST, PORT), Catcher)
    srv.timeout = 1
    print("Opening the Fyers login in your browser... (waiting up to 3 minutes)")
    webbrowser.open(url)
    for _ in range(180):
        srv.handle_request()
        if Catcher.result.get("auth_code"):
            break
    srv.server_close()
    res = Catcher.result
    if not res.get("auth_code"):
        sys.exit("No login received. Run the script again.")
    if res.get("state") != state:
        sys.exit("The login response did not match this request (state mismatch); nothing was saved.")
    r = requests.post(f"{BASE}/validate-authcode", json={
        "grant_type": "authorization_code", "appIdHash": hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest(), "code": res["auth_code"]}, timeout=30)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    token = j.get("access_token")
    if not token:
        sys.exit(f"Fyers did not return a token: {j.get('message') or r.status_code}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"app_id": app_id, "access_token": token}), encoding="utf-8")
    print(f"Token saved to {OUT.name} (valid until about 6:30 AM tomorrow).")
    if push:
        for name, val in (("FYERS_APP_ID", app_id), ("FYERS_TOKEN", token)):
            p = subprocess.run(["gh", "secret", "set", name], input=val, text=True, capture_output=True)
            if p.returncode:
                sys.exit(f"Could not set the GitHub secret {name}: {p.stderr.strip()}")
        print("GitHub secrets updated.")
        p = subprocess.run(["gh", "workflow", "run", "refresh.yml"], capture_output=True, text=True)
        print("Cloud build started." if p.returncode == 0 else f"Could not start the build: {p.stderr.strip()}")


if __name__ == "__main__":
    main()
