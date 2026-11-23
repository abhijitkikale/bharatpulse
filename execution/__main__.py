"""Command line.
  python -m execution run        start the engine (PAPER) and the status page http://127.0.0.1:8780/ ; LIVE is activated from the page
  python -m execution check      Stage 3: read-only connectivity check against FYERS (token, profile, funds, orderbook, tradebook, positions, order WebSocket, instrument
                                 master). It places NO order.
  python -m execution unkill     remove the KILL file after an emergency stop
  python -m execution status     print today's execution records
  python -m execution unhalt     clear today's LIVE halt after you fixed its cause (direct-API and Automate)
  python -m execution executor [api|automate]   show / switch how live orders are sent (api = straight to the FYERS API, automate = FYERS Automate webhooks); restart the
                                 engine after switching. Switching back to api is the revert.
  python -m execution automate-check   validate execution/automate.json (slots, addresses, one entry and one exit address each); prints no address
  python -m execution automate-test [--calls N] [--gap S] [--which entry|exit]   fire the TEST workflow's webhook(s) from automate.json ("test" block). The test workflow must
                                 only send a notification, never an order; live slot addresses are refused by this command.
"""
import argparse
import datetime as dt
import sys
import threading
import time

from . import config, mode as modemod
from .engine import IST, now_ist


def lock_or_exit():
    config.STATE.mkdir(parents=True, exist_ok=True)
    f = open(config.STATE / "engine.lock", "w")
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another execution engine is already running")
    return f


def cmd_run(args):
    from .run import Runner
    from .ui_server import serve
    _lock = lock_or_exit()
    r = Runner()
    serve(r, r.cfg["ui_port"])
    print(f"status page: http://127.0.0.1:{r.cfg['ui_port']}/   (PAPER mode; activate LIVE on that page)")
    stop = threading.Event()
    try:
        r.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()


def cmd_check(args):
    """Stage 3. Read-only: nothing here can place, modify or cancel an order."""
    from .brokers import FyersBroker
    from .instrument import Instruments
    cfg = config.load()
    ok_all = True

    def line(ok, text):
        nonlocal ok_all
        ok_all = ok_all and ok
        print(("PASS  " if ok else "FAIL  ") + text)
    tok = modemod.load_token()
    line(tok is not None, "trading-app token for today" + ("" if tok else " (run tools/fyers_order_login.py after 06:30 IST; the app must be the activated Algo Trading app)"))
    line(not modemod.old_router_live(), "the dashboard's own order router is not live")
    if tok is None:
        return print("\nStopping: the remaining checks need the token.")
    b = FyersBroker(tok[0], tok[1], cfg)
    prof = b.profile()
    line(prof is not None, "profile" + (f" (keys: {sorted(prof)[:6]})" if prof else ""))
    funds = b.funds()
    line(funds is not None, "funds" + (f" (keys: {sorted(funds)[:6]})" if funds else ""))
    orders = b.orders()
    line(orders is not None, f"orderbook ({0 if orders is None else len(orders)} orders today)")
    trades = b.trades()
    line(trades is not None, f"tradebook ({0 if trades is None else len(trades)} trades today)")
    pos = b.positions()
    line(pos is not None, f"positions ({0 if pos is None else len([p for p in pos if p.net_qty])} open)")
    state = {"connected": False, "last": 0.0, "error": ""}
    from .order_feed import OrderFeed
    feed = OrderFeed(tok[0], tok[1], lambda m: None, state)
    started = feed.start()
    t0 = time.time()
    while started and time.time() - t0 < 10 and not state["connected"]:
        time.sleep(0.5)
    line(state["connected"], "order WebSocket connect + subscribe" + (f" ({state['error']})" if state["error"] else ""))
    feed.stop()
    sym = args.symbol
    if not sym:
        from . import trade_source
        its, _, _ = trade_source.read(now_ist().date().isoformat())
        sym = its[-1].symbol if its else None
    if sym:
        chk = Instruments().validate(sym, 0, "CALL" if sym.endswith("CE") else "PUT")
        row = Instruments().lookup(sym)
        line(row is not None, f"instrument master: {sym} -> {row}")
    else:
        print("INFO  no symbol to look up (pass --symbol NSE:NIFTY26NOV25300CE)")
    print("\nALL READ-ONLY CHECKS PASSED. No order was placed." if ok_all else "\nSome checks FAILED: fix them before activating LIVE.")


def cmd_unkill(args):
    try:
        modemod.KILL.unlink()
        print("KILL file removed: LIVE can be activated again.")
    except FileNotFoundError:
        print("no KILL file")


def cmd_unhalt(args):
    """Clear today's LIVE halt (it survives a restart on purpose). Use it only after you have fixed what caused it."""
    from .store import Store
    day = now_ist().date().isoformat()
    s = Store()
    why = s.halted(day, "LIVE")
    s.clear_halt(day, "LIVE")
    why2 = s.halted(day, "AUTOMATE")
    s.clear_halt(day, "AUTOMATE")
    why = why or why2
    print(f"LIVE halt cleared for {day} (it was: {why})" if why else "there was no LIVE halt today")


def cmd_executor(args):
    import json
    cfg = config.load()
    if not args.which:
        return print(f"executor: {cfg['executor']}  ({'FYERS Automate webhooks' if cfg['executor'] == 'automate' else 'direct FYERS API, needs the registered static IP'})")
    try:
        raw = json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw["executor"] = args.which
    config.CONFIG_FILE.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    print(f"executor set to {args.which}. Restart the execution engine for it to take effect (a restart is PAPER; activate LIVE again).")


def cmd_automate_check(args):
    from . import automate
    ac = automate.load_config()
    raw = {}
    try:
        import json
        raw = json.loads(automate.AUTOMATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    print(f"executor: {config.load()['executor']}")
    for s in ac["slots"]:
        print(f"slot {s['name']:<8} {s['side']:<5} entry address OK, exit address OK")
    bad = len(raw.get("slots") or []) - len(ac["slots"])
    if bad:
        print(f"WARNING: {bad} slot(s) in automate.json are not valid (name, side CALL/PUT, two different https://....fyers.in/ addresses) and are ignored")
    print(f"test workflow: {'entry' if 'entry_url' in ac['test'] else '-'} {'exit' if 'exit_url' in ac['test'] else '-'}")
    print(f"{len(ac['slots'])} usable slot(s): CALL {sum(s['side'] == 'CALL' for s in ac['slots'])}, PUT {sum(s['side'] == 'PUT' for s in ac['slots'])} = that many trades a day at most")


def cmd_automate_test(args):
    from . import automate
    ac = automate.load_config()
    url = ac["test"].get(f"{args.which}_url")
    if not url:
        return print(f"no valid test {args.which} address in execution/automate.json (a \"test\" block with entry_url / exit_url on fyers.in; live slot addresses are refused)")
    for i in range(args.calls):
        t0 = time.time()
        res = automate.fire(url, {"source": "bharatpulse", "event": "test", "n": i + 1})
        print(f"call {i + 1}: {'OK' if res.ok else 'FAILED'}  http={res.code}  {time.time() - t0:.2f}s  {res.msg[:100]}", flush=True)
        if i + 1 < args.calls:
            time.sleep(args.gap)


def cmd_status(args):
    from .store import Store
    s = Store()
    day = now_ist().date().isoformat()
    for r in s.signals(day):
        print(r["mode"], r["phase"], r["symbol"], r["qty_filled"], r["avg_entry"], r["avg_exit"], r["realized_pnl"], r["note"] or r["exit_reason"] or "")
    for e in s.events(None, 15)[::-1]:
        print(dt.datetime.fromtimestamp(e["ts"], IST).strftime("%H:%M:%S"), e["mode"], e["level"], e["text"])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m execution", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    c = sub.add_parser("check")
    c.add_argument("--symbol", default=None)
    c.set_defaults(fn=cmd_check)
    sub.add_parser("unkill").set_defaults(fn=cmd_unkill)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("unhalt").set_defaults(fn=cmd_unhalt)
    e = sub.add_parser("executor")
    e.add_argument("which", nargs="?", choices=["api", "automate"])
    e.set_defaults(fn=cmd_executor)
    sub.add_parser("automate-check").set_defaults(fn=cmd_automate_check)
    t = sub.add_parser("automate-test")
    t.add_argument("--calls", type=int, default=1)
    t.add_argument("--gap", type=float, default=5.0)
    t.add_argument("--which", choices=["entry", "exit"], default="entry")
    t.set_defaults(fn=cmd_automate_test)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
