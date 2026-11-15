"""Morning Fyers login for your TRADING (order) API app: a separate app, a separate token, never sent to GitHub.

Run once each morning after 06:30 IST, only when you want the order router in "live" mode:   python tools/fyers_order_login.py
  1. opens the Fyers login in your browser: you enter your own PIN / TOTP there (this script never sees them)
  2. catches the one-time code on http://127.0.0.1:5051/ and swaps it for today's access token
  3. saves it to data/fyers_order_token.json (git-ignored). It is NOT stored as a GitHub secret and the cloud build never sees it.
     The token stops working at about 06:30 IST next day (Fyers rule: one sign-in per trading day).

Needs two environment variables on this PC (never commit them):  FYERS_ORDER_APP_ID  and  FYERS_ORDER_APP_SECRET  (the app you activate at
myapi.fyers.in; its redirect URL must be exactly   http://127.0.0.1:5051/   and its registered IP the fixed address this PC reaches Fyers from)."""
import hashlib
import http.server
import json
import os
import pathlib
import secrets
import sys
import urllib.parse
import webbrowser

import pandas as pd
import requests

HOST, PORT = "127.0.0.1", 5051
REDIRECT = f"http://{HOST}:{PORT}/"
BASE = "https://api-t1.fyers.in/api/v3"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "fyers_order_token.json"


def need(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"Set the environment variable {name} first (see the top of this file).")
    return v


def next_expiry():
    """06:30 IST tomorrow (or today when it is still before 06:30)."""
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    t = now.normalize() + pd.Timedelta(hours=6, minutes=30)
    return (t if now < t else t + pd.Timedelta(days=1)).isoformat()


class Catcher(http.server.BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "auth_code" in q:
            Catcher.result = {k: v[0] for k, v in q.items()}
            body = b"<h3>BharatPulse orders: login received. You can close this tab.</h3>"
        else:
            body = b"<h3>Waiting for the Fyers login...</h3>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    app_id, secret = need("FYERS_ORDER_APP_ID"), need("FYERS_ORDER_APP_SECRET")
    state = secrets.token_urlsafe(16)
    url = f"{BASE}/generate-authcode?" + urllib.parse.urlencode({"client_id": app_id, "redirect_uri": REDIRECT, "response_type": "code", "state": state})
    srv = http.server.HTTPServer((HOST, PORT), Catcher)
    srv.timeout = 1
    print("Opening the Fyers login for the ORDER app in your browser... (waiting up to 3 minutes)")
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
    r = requests.post(f"{BASE}/validate-authcode", json={"grant_type": "authorization_code", "appIdHash": hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest(),
                                                         "code": res["auth_code"]}, timeout=30)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    token = j.get("access_token")
    if not token:
        sys.exit(f"Fyers did not return a token: {j.get('message') or r.status_code}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"app_id": app_id, "access_token": token, "expires": next_expiry()}), encoding="utf-8")
    print(f"Order token saved to {OUT.name}; valid until {next_expiry()}. It stays on this PC.")


if __name__ == "__main__":
    main()
