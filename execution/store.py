"""Execution records in SQLite (standard library): one row per dashboard signal and mode, every order sent, and an event log. PAPER and LIVE are separate rows
(mode column) and nothing here touches the dashboard's own paper journal. The signal row is claimed BEFORE any order is sent (primary key signal_id + mode), which is
what stops a duplicate entry after a restart, a reconnection or a second pass over the same dashboard trade."""
import json
import pathlib
import sqlite3
import threading
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  signal_id TEXT NOT NULL, mode TEXT NOT NULL, day TEXT NOT NULL, phase TEXT NOT NULL, created_at REAL, updated_at REAL,
  intent_json TEXT, symbol TEXT, qty_req INTEGER, qty_filled INTEGER DEFAULT 0, avg_entry REAL, avg_exit REAL,
  entry_order_id TEXT, entry_tag TEXT, sl_order_id TEXT, sl_tag TEXT, sl_stop REAL, sl_limit REAL, exit_order_id TEXT, exit_tag TEXT, exit_limit REAL,
  entry_at REAL, exit_at REAL, exit_reason TEXT, realized_pnl REAL, note TEXT, tries INTEGER DEFAULT 0, extra_json TEXT,
  PRIMARY KEY (signal_id, mode));
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, mode TEXT, signal_id TEXT, role TEXT, tag TEXT, fyers_id TEXT, action TEXT,
  request_json TEXT, response_json TEXT, ok INTEGER, uncertain INTEGER, message TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, mode TEXT, signal_id TEXT, level TEXT, kind TEXT, text TEXT, data_json TEXT);
CREATE TABLE IF NOT EXISTS day_state (day TEXT NOT NULL, mode TEXT NOT NULL, halted TEXT, PRIMARY KEY (day, mode));
"""
ACTIVE_PHASES = ("ENTRY_UNCERTAIN", "ENTRY_SENT", "PROTECTED", "EXIT_SENT", "STUCK", "CHECK_MANUALLY")
SIGNAL_COLS = ("phase", "updated_at", "symbol", "qty_req", "qty_filled", "avg_entry", "avg_exit", "entry_order_id", "entry_tag", "sl_order_id", "sl_tag", "sl_stop", "sl_limit",
               "exit_order_id", "exit_tag", "exit_limit", "entry_at", "exit_at", "exit_reason", "realized_pnl", "note", "tries", "extra_json")


class Store:
    def __init__(self, path=None):
        path = pathlib.Path(path or (config.DATA / "execution.db"))
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # ---- signals ----
    def claim(self, signal_id, mode, day, intent_json, phase="NEW"):
        """True if this (signal, mode) was not known and is now recorded; False when it already exists (a duplicate)."""
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO signals (signal_id, mode, day, phase, created_at, updated_at, intent_json) VALUES (?,?,?,?,?,?,?)",
                                  (signal_id, mode, day, phase, time.time(), time.time(), intent_json))
            return cur.rowcount == 1

    def get(self, signal_id, mode):
        with self.lock:
            r = self.db.execute("SELECT * FROM signals WHERE signal_id=? AND mode=?", (signal_id, mode)).fetchone()
            return dict(r) if r else None

    def update(self, signal_id, mode, **fields):
        bad = [k for k in fields if k not in SIGNAL_COLS]
        if bad:
            raise KeyError(bad)
        fields["updated_at"] = time.time()
        with self.lock:
            self.db.execute(f"UPDATE signals SET {', '.join(k + '=?' for k in fields)} WHERE signal_id=? AND mode=?", (*fields.values(), signal_id, mode))

    def signals(self, day=None, mode=None):
        q, a = "SELECT * FROM signals WHERE 1=1", []
        if day:
            q += " AND day=?"
            a.append(day)
        if mode:
            q += " AND mode=?"
            a.append(mode)
        with self.lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY created_at", a)]

    def active(self, mode):
        with self.lock:
            return [dict(r) for r in self.db.execute(f"SELECT * FROM signals WHERE mode=? AND phase IN ({','.join('?' * len(ACTIVE_PHASES))}) ORDER BY created_at", (mode, *ACTIVE_PHASES))]

    def executed_entries(self, day, mode):
        """Entries that really filled today (the daily cap counts these, not attempts)."""
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM signals WHERE day=? AND mode=? AND qty_filled>0", (day, mode)).fetchone()[0]

    def realized(self, day, mode):
        with self.lock:
            return float(self.db.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM signals WHERE day=? AND mode=?", (day, mode)).fetchone()[0])

    # ---- orders and events ----
    def order(self, mode, signal_id, role, tag, action, request, res):
        with self.lock:
            self.db.execute("INSERT INTO orders (ts, mode, signal_id, role, tag, fyers_id, action, request_json, response_json, ok, uncertain, message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (time.time(), mode, signal_id, role, tag, getattr(res, "id", None), action, json.dumps(request, default=str), json.dumps(getattr(res, "raw", None), default=str),
                             int(bool(res.ok)), int(bool(res.uncertain)), (res.msg or "")[:300]))

    def orders(self, signal_id=None, mode=None):
        q, a = "SELECT * FROM orders WHERE 1=1", []
        if signal_id:
            q += " AND signal_id=?"
            a.append(signal_id)
        if mode:
            q += " AND mode=?"
            a.append(mode)
        with self.lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY id", a)]

    def event(self, mode, signal_id, level, kind, text, data=None):
        with self.lock:
            self.db.execute("INSERT INTO events (ts, mode, signal_id, level, kind, text, data_json) VALUES (?,?,?,?,?,?,?)",
                            (time.time(), mode, signal_id, level, kind, text, json.dumps(data, default=str) if data is not None else None))

    def events(self, mode=None, limit=60):
        with self.lock:
            q = "SELECT * FROM events" + (" WHERE mode=?" if mode else "") + " ORDER BY id DESC LIMIT ?"
            return [dict(r) for r in self.db.execute(q, ((mode, limit) if mode else (limit,)))]

    # ---- halts (per day and mode, survive a restart) ----
    def halted(self, day, mode):
        with self.lock:
            r = self.db.execute("SELECT halted FROM day_state WHERE day=? AND mode=?", (day, mode)).fetchone()
            return r["halted"] if r else None

    def halt(self, day, mode, why):
        with self.lock:
            self.db.execute("INSERT INTO day_state (day, mode, halted) VALUES (?,?,?) ON CONFLICT(day, mode) DO UPDATE SET halted=COALESCE(halted, excluded.halted)", (day, mode, why))

    def clear_halt(self, day, mode):
        with self.lock:
            self.db.execute("DELETE FROM day_state WHERE day=? AND mode=?", (day, mode))
