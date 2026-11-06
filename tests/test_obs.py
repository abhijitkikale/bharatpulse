import asyncio
import base64
import hashlib
import json
import pathlib
import sys
import threading

import pandas as pd
import websockets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import obs_control  # noqa: E402

PASSWORD, SALT, CHALLENGE = "s3cret-pw", "c2FsdHNhbHQ=", "Y2hhbGxlbmdl"


class FakeObs:
    """An OBS WebSocket v5 server that checks the password the way OBS does and answers the requests the client makes."""

    def __init__(self):
        self.scenes, self.inputs, self.streaming, self.calls = [], [], False, []

    async def handler(self, ws):
        await ws.send(json.dumps({"op": 0, "d": {"obsWebSocketVersion": "5.1.0", "rpcVersion": 1, "authentication": {"challenge": CHALLENGE, "salt": SALT}}}))
        ident = json.loads(await ws.recv())["d"]
        secret = base64.b64encode(hashlib.sha256((PASSWORD + SALT).encode()).digest()).decode()
        want = base64.b64encode(hashlib.sha256((secret + CHALLENGE).encode()).digest()).decode()
        if ident.get("authentication") != want:
            await ws.close(code=4009)
            return
        await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        async for raw in ws:
            m = json.loads(raw)["d"]
            r, data, resp = m["requestType"], m.get("requestData", {}), {}
            self.calls.append(r)
            if r == "GetSceneList":
                resp = {"scenes": [{"sceneName": s} for s in self.scenes]}
            elif r == "CreateScene":
                self.scenes.append(data["sceneName"])
            elif r == "GetInputList":
                resp = {"inputs": [{"inputName": i} for i in self.inputs]}
            elif r == "CreateInput":
                self.inputs.append(data["inputName"])
                self.url = data["inputSettings"]["url"]
            elif r == "GetStreamStatus":
                resp = {"outputActive": self.streaming}
            elif r == "StartStream":
                self.streaming = True
            elif r == "StopStream":
                self.streaming = False
            await ws.send(json.dumps({"op": 7, "d": {"requestType": r, "requestId": m["requestId"], "requestStatus": {"result": True, "code": 100}, "responseData": resp}}))


def _serve(fake):
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def main():
        async with websockets.serve(fake.handler, "127.0.0.1", 0) as srv:
            fake.port = srv.sockets[0].getsockname()[1]
            ready.set()
            await asyncio.Future()

    threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True).start()
    ready.wait(5)


def test_client_authenticates_creates_the_scene_and_starts_and_stops():
    fake = FakeObs()
    _serve(fake)
    obs = obs_control.Obs("127.0.0.1", fake.port, PASSWORD).connect()
    obs.ensure_scene("BharatPulse", "http://127.0.0.1:8766/?clean=1")
    assert fake.scenes == ["BharatPulse"] and fake.inputs == ["BharatPulse page"] and fake.url.endswith("?clean=1")
    obs.ensure_scene("BharatPulse", "http://127.0.0.1:8766/?clean=1")                      # a second run does not duplicate anything
    assert fake.scenes == ["BharatPulse"] and fake.inputs == ["BharatPulse page"]
    assert obs.streaming() is False
    obs.call("StartStream")
    assert obs.streaming() is True
    obs.call("StopStream")
    assert obs.streaming() is False
    obs.close()
    try:
        obs_control.Obs("127.0.0.1", fake.port, "wrong").connect()
        assert False, "a wrong password must be refused"
    except Exception as e:
        assert not isinstance(e, AssertionError)


def test_when_to_start_and_stop_the_broadcast():
    t = lambda s: pd.Timestamp(s, tz="Asia/Kolkata")
    cfg = {"mode": "direct", "stop_at": "15:35"}
    d = lambda c, when, streaming=False, ready=True, healthy=True, done=False, bad=0: obs_control.decide(c, t(when), streaming, ready, healthy, done, bad)
    assert d(cfg, "2026-11-09 06:50") == "start"                                    # direct: right after the sign-in, before the open
    assert d(cfg, "2026-11-09 06:50", ready=False) is None                          # but only once the stream page has data
    assert d(cfg, "2026-11-09 06:50", done=True) is None                            # one start per day: a broadcast you stopped stays stopped
    assert d(cfg, "2026-11-09 15:36") is None and d(cfg, "2026-11-14 10:00") is None   # not after the stop time, not at the weekend
    assert d(cfg, "2026-11-09 15:36", streaming=True) == "stop"
    assert d(cfg, "2026-11-09 11:00", streaming=True) is None
    assert d(cfg, "2026-11-09 11:00", streaming=True, bad=10) == "stale-stop"        # feed bad for 10 minutes in market hours
    assert d(cfg, "2026-11-09 07:30", streaming=True, bad=60) is None               # the stale rule only applies in market hours
    later = {"mode": "at_open", "stop_at": "15:35"}
    assert d(later, "2026-11-09 07:00") is None and d(later, "2026-11-09 09:30") is None
    assert d(later, "2026-11-09 09:31") == "start" and d(later, "2026-11-09 09:31", healthy=False) is None
    assert d({"mode": "off", "stop_at": "15:35"}, "2026-11-09 09:40") is None


def test_feed_health():
    t = pd.Timestamp("2026-11-09 10:00", tz="Asia/Kolkata")
    good = {"candles": [1], "status": "live", "last_candle": "09:58"}
    assert obs_control.feed_health(good, t) == (True, True)
    assert obs_control.feed_health({**good, "last_candle": "09:50"}, t) == (True, False)       # last candle 10 minutes old
    assert obs_control.feed_health({**good, "status": "Fyers sign-in needed"}, t) == (True, False)
    assert obs_control.feed_health(None, t) == (False, False)
