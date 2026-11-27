"""Plain data types: the dashboard's trade object (Intent), the broker's order / position (BOrder, BPos) and a call result (Res), plus tick helpers."""
import dataclasses
import hashlib
import math
from typing import Optional

TICK = 0.05


def tick_up(x, tick=TICK):
    return round(math.ceil(x / tick - 1e-9) * tick, 2)


def tick_down(x, tick=TICK):
    return round(math.floor(x / tick + 1e-9) * tick, 2)


@dataclasses.dataclass
class Res:
    """Result of one broker call. ok: accepted. uncertain: no definite answer (timeout, 201, 5xx): the order may or may not exist, look it up, never just resend.
    auth: the broker refused the credentials / IP (stop everything that is new)."""
    ok: bool
    id: Optional[str] = None
    code: Optional[int] = None
    msg: str = ""
    uncertain: bool = False
    auth: bool = False
    raw: Optional[dict] = None


@dataclasses.dataclass
class BOrder:
    id: str
    tag: str
    symbol: str
    side: int                       # 1 buy, -1 sell
    type: int                       # 1 limit, 2 market, 3 SL-M, 4 SL-L
    status: str                     # transit, pending, partial, filled, cancelled, rejected, expired, unknown
    qty: int
    filled: int
    limit: float = 0.0
    stop: float = 0.0
    avg: float = 0.0
    message: str = ""
    time: str = ""

    @property
    def terminal(self):
        return self.status in ("filled", "cancelled", "rejected", "expired")


@dataclasses.dataclass
class BPos:
    symbol: str
    net_qty: int
    avg: float = 0.0
    ltp: float = 0.0
    realized: float = 0.0
    unrealized: float = 0.0
    product: str = ""
    buy_qty: int = 0                # everything bought in this contract today, and the average price of those buys (FYERS keeps these across trades of the same contract)
    buy_avg: float = 0.0


@dataclasses.dataclass
class Intent:
    """The dashboard's trade object, as the dashboard produced it. Nothing here is computed by the engine."""
    signal_id: str
    day: str
    n: int
    symbol: str
    option_side: str                # CALL / PUT: the dashboard buys the option (CE for CALL, PE for PUT)
    strike: float
    entry: float                    # the dashboard's entry price (the entry trigger / fill it assumed)
    stop: float                     # initial stop
    stop_now: float                 # stop in force now (trailing and regime moves included)
    target: float
    status: str                     # OPEN, TARGET, STOP, TRAIL, DAY END
    exit: Optional[float]
    last: Optional[float]
    entry_time: str                 # HH:MM IST
    setup_time: str
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None
    raw: Optional[dict] = None

    @property
    def tag_base(self):
        """12 hex characters of the signal id: the base of the order tags (FYERS tags are 1 to 30 alphanumeric characters)."""
        return hashlib.sha1(self.signal_id.encode()).hexdigest()[:12]


def make_signal_id(day, trade):
    """A stable id for one dashboard trade: the day, the contract, when the setup appeared and when it entered, and the entry price. The trade number is NOT part of it
    (the dashboard may renumber a day's trades when it recomputes), so a restart or a recomputation never turns one signal into two."""
    return f"{day}|{trade.get('symbol')}|{trade.get('setup_time')}|{trade.get('entry_time')}|{float(trade.get('entry', 0)):.2f}"


REQUIRED = ("symbol", "side", "entry", "stop_now", "target", "status", "entry_time")


def parse_intent(day, trade):
    """(Intent, None) or (None, [what is missing or wrong]). Missing strategy values are never invented."""
    bad = [k for k in REQUIRED if trade.get(k) in (None, "")]
    if bad:
        return None, [f"the dashboard trade has no '{k}'" for k in bad]
    try:
        entry, stop_now, target = float(trade["entry"]), float(trade["stop_now"]), float(trade["target"])
    except (TypeError, ValueError):
        return None, ["entry, stop_now or target is not a number"]
    problems = []
    if trade["side"] not in ("CALL", "PUT"):
        problems.append(f"unknown side {trade['side']!r}")
    if not (entry > 0 and stop_now > 0 and target > entry):                    # a trailed stop may sit above the entry, so only these are checked here
        problems.append(f"the levels do not make sense (entry {entry}, stop {stop_now}, target {target})")
    if problems:
        return None, problems
    return Intent(signal_id=make_signal_id(day, trade), day=str(day), n=int(trade.get("n", 0)), symbol=str(trade["symbol"]), option_side=trade["side"], strike=float(trade.get("strike", 0)),
                  entry=entry, stop=float(trade.get("stop", stop_now)), stop_now=stop_now, target=target, status=str(trade["status"]),
                  exit=None if trade.get("exit") is None else float(trade["exit"]), last=None if trade.get("last") is None else float(trade["last"]),
                  entry_time=str(trade["entry_time"]), setup_time=str(trade.get("setup_time") or ""), tp_pct=trade.get("tp_pct"), sl_pct=trade.get("sl_pct"), raw=trade), None
