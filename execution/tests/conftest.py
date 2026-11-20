import datetime as dt
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from execution import config  # noqa: E402
from execution.engine import IST, Engine  # noqa: E402
from execution.instrument import Instruments  # noqa: E402
from execution.mock_broker import MockBroker  # noqa: E402
from execution.models import parse_intent  # noqa: E402
from execution.store import Store  # noqa: E402

DAY = "2026-11-19"
SYM = "NSE:NIFTY26NOV25300CE"
NOW = dt.datetime(2026, 11, 19, 10, 0, 30, tzinfo=IST)


class Clock:
    """A controllable time.time(): the engine's waits (entry wait, uncertain wait) are tested without sleeping."""
    def __init__(self):
        self.t = time.time()                      # starts at the real time, so files written by the tests are 'fresh'

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(time, "time", c)
    return c


def trade(**kw):
    t = {"n": 1, "side": "CALL", "strike": 25300, "symbol": SYM, "entry": 100.0, "stop": 90.0, "stop_now": 90.0, "target": 140.0, "status": "OPEN", "exit": None, "last": 100.0,
         "entry_time": "10:00", "setup_time": "09:59", "tp_pct": 40, "sl_pct": 10}
    t.update(kw)
    return t


def intent(**kw):
    it, bad = parse_intent(DAY, trade(**kw))
    assert it is not None, bad
    return it


class Env:
    def __init__(self, mode="LIVE", cfg=None, broker=None, store=None):
        self.msgs = []
        self.cfg = config.load(pathlib.Path("/nonexistent"))
        self.cfg.update(cfg or {})
        self.b = broker or MockBroker()
        self.store = store or Store(":memory:")
        far = int(time.time()) + 30 * 86400
        self.inst = Instruments(rows={SYM: {"lot": 65, "tick": 0.05, "expiry": far}, "NSE:NIFTY26NOV25300PE": {"lot": 65, "tick": 0.05, "expiry": far}})
        self.mode = mode
        self.e = self.make_engine()

    def make_engine(self):
        return Engine(self.mode, self.b, self.store, self.cfg, notify=lambda text, level="info": self.msgs.append((level, text)), instruments=self.inst, sleep=lambda s: None)

    def step(self, its, now=NOW, **kw):
        self.e.step(its, now, **kw)

    def rec(self, it):
        return self.store.get(it.signal_id, self.mode)

    def text(self):
        return "\n".join(t for _, t in self.msgs)


@pytest.fixture
def env(clock):
    return Env()
