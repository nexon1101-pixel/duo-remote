import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from duo_server import gamepad, protocol
from duo_server import server as server_mod
from duo_server.gamepad import FakePad, PadUnavailable
from duo_server.input_win import NullInput
from duo_server.server import Settings, make_handler

from test_session import FakeStream, hello, recv_json, recv_until, TOKEN


# --- 値の直し方

def test_clean_stick():
    assert gamepad.clean_stick(0) == 0
    assert gamepad.clean_stick(1.0) == 32767
    assert gamepad.clean_stick(-1.0) == -32768
    assert gamepad.clean_stick(0.5) == 16384
    assert gamepad.clean_stick(5) == 32767
    assert gamepad.clean_stick(-5.5) == -32768
    assert gamepad.clean_stick(1e300) == 32767


@pytest.mark.parametrize("bad", [None, "0.5", [1], {"a": 1}, True, float("nan"), float("inf"), float("-inf")])
def test_clean_values_reject_bad(bad):
    assert gamepad.clean_stick(bad) == 0
    assert gamepad.clean_trigger(bad) == 0
    assert gamepad.clean_buttons(bad) == 0


def test_clean_trigger():
    assert gamepad.clean_trigger(0.0) == 0
    assert gamepad.clean_trigger(1.0) == 255
    assert gamepad.clean_trigger(0.5) == 128
    assert gamepad.clean_trigger(-0.2) == 0
    assert gamepad.clean_trigger(3) == 255


def test_clean_buttons():
    assert gamepad.clean_buttons(0x1000) == 0x1000
    # 表にないビット（0x0800）と、16ビットより上は捨てる
    assert gamepad.clean_buttons(0x0800) == 0
    assert gamepad.clean_buttons(0x10000) == 0
    assert gamepad.clean_buttons(0xFFFFF) == gamepad.BUTTON_MASK
    assert gamepad.clean_buttons(1.0) == 0


def test_clean_state_defaults_to_zero():
    assert gamepad.clean_state({}) == (0, 0, 0, 0, 0, 0, 0)
    assert gamepad.clean_state({"b": 1, "lx": 1, "ly": -1, "rx": 0.0, "ry": "x", "lt": 1, "rt": 0.5}) == (
        1, 32767, -32768, 0, 0, 255, 128,
    )


def test_motor_level():
    assert gamepad.motor_level(0) == 0.0
    assert gamepad.motor_level(255) == 1.0
    assert gamepad.motor_level(1000) == 1.0
    assert gamepad.motor_level(None) == 0.0


def test_vgamepad_missing_is_unavailable(monkeypatch):
    # Linux のテストでは、vgamepad を読み込めない。読み込めなくても、理由つきの例外になる
    monkeypatch.setitem(__import__("sys").modules, "vgamepad", None)
    pad = gamepad.VGamepadPad()
    with pytest.raises(PadUnavailable) as e:
        pad.connect()
    assert "vgamepad" in str(e.value)
    assert gamepad.probe()[0] is False


# --- サーバー

class Pads:
    """make_handler に渡す入れ物の工場。作った入れ物を覚える。"""

    def __init__(self, fail=None):
        self.fail = fail
        self.made: list[FakePad] = []

    def __call__(self):
        pad = FakePad(fail=self.fail)
        self.made.append(pad)
        return pad

    @property
    def sessions(self):
        """起動時の調べ用（最初の1つ）を除いた、接続で作った入れ物。"""
        return self.made[1:]


async def run(body, pads):
    backend = NullInput((1920, 1080))

    def factory(settings, src, region, monitor=None, window=None):
        return FakeStream()

    handler = make_handler(Settings(token=TOKEN), backend, factory, pad_factory=pads)
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await body(f"ws://127.0.0.1:{port}", pads)


async def open_ws(url):
    ws = await connect(url)
    await ws.send(hello())
    welcome = await recv_json(ws)
    assert welcome["t"] == "welcome"
    return ws, welcome


def padmsg(**kw):
    return json.dumps({"t": "pad", **kw})


async def wait_for(cond, timeout=3):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "待ちきれません"
        await asyncio.sleep(0.01)


def test_welcome_pad_flag():
    async def body(url, pads):
        ws, welcome = await open_ws(url)
        assert welcome["pad"] is True
        await ws.close()

    asyncio.run(run(body, Pads()))

    async def body2(url, pads):
        ws, welcome = await open_ws(url)
        assert welcome["pad"] is False
        await ws.close()

    asyncio.run(run(body2, Pads(fail="ViGEmBus が入っていません")))


def test_welcome_pad_false_without_factory():
    async def go():
        handler = make_handler(Settings(token=TOKEN), NullInput((1920, 1080)), lambda *a, **k: FakeStream())
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ws, welcome = await open_ws(f"ws://127.0.0.1:{port}")
            assert welcome["pad"] is False
            await ws.send(padmsg(b=1))
            state = await recv_until(ws, "pad_state")
            assert state["t"] == "pad_state" and state["available"] is False
            await ws.close()

    asyncio.run(go())


def test_probe_runs_once_at_startup():
    async def body(url, pads):
        assert len(pads.made) == 1  # 起動のときの調べ
        for _ in range(2):
            ws, _w = await open_ws(url)
            await ws.close()
        assert len(pads.made) == 1  # pad を送らない接続では、作らない

    asyncio.run(run(body, Pads()))


def test_pad_creates_and_applies_cleaned_values():
    async def body(url, pads):
        ws, _ = await open_ws(url)
        await ws.send(padmsg(b=0x1000 | 0x0800, lx=2.0, ly=-1.0, rx="x", ry=0.5, lt=1.0, rt=0.5))
        state = await recv_until(ws, "pad_state")
        assert state == {"t": "pad_state", "available": True}
        await wait_for(lambda: pads.sessions and pads.sessions[0].updates)
        pad = pads.sessions[0]
        assert pad.calls[0] == ("connect",)
        assert pad.updates[0] == (0x1000, 32767, -32768, 0, 16384, 255, 128)
        await ws.close()

    asyncio.run(run(body, Pads()))


def test_pad_state_returned_once():
    async def body(url, pads):
        ws, _ = await open_ws(url)
        await ws.send(padmsg(b=1))
        await recv_until(ws, "pad_state")
        await ws.send(padmsg(b=2))
        await ws.send(json.dumps({"t": "list_monitors"}))
        # 2回目の pad では pad_state を返さない（monitors が先に届く）
        while True:
            nxt = await recv_json(ws)
            assert nxt["t"] != "pad_state"
            if nxt["t"] == "monitors":
                break
        assert len(pads.sessions) == 1
        await ws.close()

    asyncio.run(run(body, Pads()))


def test_pad_unavailable_replies_and_drops():
    async def body(url, pads):
        ws, welcome = await open_ws(url)
        assert welcome["pad"] is False
        await ws.send(padmsg(b=1))
        state = await recv_until(ws, "pad_state")
        assert state["t"] == "pad_state" and state["available"] is False
        assert "ViGEmBus" in state["reason"]
        # 以後は、静かに捨てる
        await ws.send(padmsg(b=2))
        await ws.send(json.dumps({"t": "list_monitors"}))
        nxt = await recv_until(ws, "monitors")
        assert nxt["t"] == "monitors"
        assert len(pads.made) == 1  # 起動の調べだけ。接続では作ろうとしない
        await ws.close()

    asyncio.run(run(body, Pads(fail="ViGEmBus が入っていません")))


def test_pad_creation_failure_later_gives_up():
    """起動のときは作れたが、あとで作れなかった場合。"""
    class Flaky(Pads):
        def __call__(self):
            pad = super().__call__()
            if len(self.made) > 1:
                pad.fail = "あとで失敗しました"
            return pad

    async def body(url, pads):
        ws, welcome = await open_ws(url)
        assert welcome["pad"] is True
        await ws.send(padmsg(b=1))
        state = await recv_until(ws, "pad_state")
        assert state == {"t": "pad_state", "available": False, "reason": "あとで失敗しました"}
        await ws.send(padmsg(b=2))
        await ws.send(json.dumps({"t": "list_monitors"}))
        assert (await recv_until(ws, "monitors"))["t"] == "monitors"
        assert len(pads.made) == 2
        await ws.close()

    asyncio.run(run(body, Flaky()))


def test_pad_off_resets_and_closes():
    async def body(url, pads):
        ws, _ = await open_ws(url)
        await ws.send(padmsg(b=1))
        await recv_until(ws, "pad_state")
        await ws.send(json.dumps({"t": "pad_off"}))
        await wait_for(lambda: ("close",) in pads.sessions[0].calls)
        calls = pads.sessions[0].calls
        assert calls.index(("reset",)) < calls.index(("close",))
        # また pad を送ると、作り直す
        await ws.send(padmsg(b=1))
        assert (await recv_until(ws, "pad_state"))["available"] is True
        assert len(pads.sessions) == 2
        await ws.close()

    asyncio.run(run(body, Pads()))


def test_disconnect_resets_and_closes():
    async def body(url, pads):
        ws, _ = await open_ws(url)
        await ws.send(padmsg(b=0x1000))
        await recv_until(ws, "pad_state")
        await wait_for(lambda: pads.sessions[0].updates)
        await ws.close()
        await wait_for(lambda: ("close",) in pads.sessions[0].calls)
        assert ("reset",) in pads.sessions[0].calls

    asyncio.run(run(body, Pads()))


def test_kicked_by_other_device_closes_pad():
    async def body(url, pads):
        ws1, _ = await open_ws(url)
        await ws1.send(padmsg(b=1))
        await recv_until(ws1, "pad_state")
        ws2, _ = await open_ws(url)
        await wait_for(lambda: ("close",) in pads.sessions[0].calls)
        assert ("reset",) in pads.sessions[0].calls
        await ws2.close()
        await ws1.close()

    asyncio.run(run(body, Pads()))


def test_rumble_reaches_client():
    async def body(url, pads):
        ws, _ = await open_ws(url)
        await ws.send(padmsg(b=1))
        await recv_until(ws, "pad_state")
        pad = pads.sessions[0]
        pad.rumble(1.0, 0.25)
        msg = await recv_until(ws, "rumble")
        assert msg == {"t": "rumble", "l": 1.0, "r": 0.25}
        # 別のスレッドからでも届く
        await asyncio.to_thread(pad.rumble, 0.0, 0.0)
        msg = await recv_until(ws, "rumble")
        assert msg == {"t": "rumble", "l": 0.0, "r": 0.0}
        await ws.close()

    asyncio.run(run(body, Pads()))


def test_rate_limit_keeps_last_state(monkeypatch):
    monkeypatch.setattr(server_mod, "PAD_MIN_INTERVAL_SEC", 0.2)

    async def body(url, pads):
        ws, _ = await open_ws(url)
        await ws.send(padmsg(b=1))
        await recv_until(ws, "pad_state")
        for b in (2, 4, 8):
            await ws.send(padmsg(b=b))
        pad = pads.sessions[0]
        await wait_for(lambda: len(pad.updates) >= 2)
        await asyncio.sleep(0.4)
        # 速すぎる分は落ち、最後の状態（8）が反映される
        assert pad.updates[0][0] == 1
        assert pad.updates[-1][0] == 8
        assert len(pad.updates) < 4
        await ws.close()

    asyncio.run(run(body, Pads()))
