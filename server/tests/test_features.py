"""複数のモニター、スリープを防ぐ、画質の変更、stats のテスト。"""
import asyncio
import json
import threading

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from duo_server import protocol
from duo_server import server as server_mod
from duo_server.input_win import NullInput
from duo_server.server import Session, Settings, make_handler
from test_session import BurstStream, SlowSocket

TOKEN = "t"
AUD = b"\x00\x00\x00\x01\x09\xf0"
DISPLAY1 = "\\\\.\\DISPLAY1"
DISPLAY2 = "\\\\.\\DISPLAY2"


class FakeVideo:
    def __init__(self):
        self.stopped = False

    async def start(self):
        pass

    async def frames(self):
        yield True, AUD + b"KEY"
        await asyncio.sleep(3600)

    async def stop(self):
        self.stopped = True


class ThreadRecordingInput(NullInput):
    """keep_awake を、どのスレッドから呼んだかも記録する。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.awake_threads: list[int] = []

    def keep_awake(self, on):
        super().keep_awake(on)
        self.awake_threads.append(threading.get_ident())


async def with_server(body, backend, streams=None):
    streams = streams if streams is not None else []

    def factory(settings, src, region, monitor=None, window=None):
        s = FakeVideo()
        s.src, s.region, s.monitor = src, region, monitor
        # 設定は後から変わるので、撮り始めの値を控えておく
        s.started_with = (settings.fps, settings.max_height, settings.bitrate_kbps)
        streams.append(s)
        return s

    handler = make_handler(Settings(token=TOKEN), backend, factory)
    async with serve(handler, "127.0.0.1", 0) as server:
        await body(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")


async def recv_json(ws, wanted, timeout=5):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        raw = await asyncio.wait_for(ws.recv(), max(deadline - asyncio.get_running_loop().time(), 0.01))
        if isinstance(raw, str):
            msg = json.loads(raw)
            if msg["t"] == wanted:
                return msg


async def open_session(url, **extra):
    ws = await connect(url)
    await ws.send(json.dumps({"t": "hello", "v": protocol.VERSION, "token": TOKEN, **extra}))
    welcome = await recv_json(ws, "welcome")
    return ws, welcome


def send(ws, **msg):
    return ws.send(json.dumps(msg))


async def eventually(cond, timeout=5):
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < deadline, "条件が満たされませんでした"
        await asyncio.sleep(0.02)


def make_backend(cls=NullInput):
    backend = cls((1920, 1080))
    backend.virtual_installed = True
    backend.virtual_enabled = True  # 主モニターの右に、仮想モニターが出ている
    return backend


# --- 複数のモニター

def test_list_monitors():
    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="list_monitors")
        msg = await recv_json(ws, "monitors")
        assert msg["list"] == [
            {"id": DISPLAY1, "name": "DISPLAY1", "w": 1920, "h": 1080, "primary": True, "virtual": False},
            {"id": DISPLAY2, "name": "DISPLAY2", "w": 1536, "h": 1080, "primary": False, "virtual": True},
        ]
        await ws.close()

    asyncio.run(with_server(body, make_backend()))


def test_mode_monitor_shows_whole_monitor_and_primary_is_like_screen():
    backend = make_backend()
    streams = []

    async def body(url):
        ws, welcome = await open_session(url)
        assert (welcome["w"], welcome["h"]) == (1920, 1080)

        await send(ws, t="mode", mode="monitor", id=DISPLAY2)
        assert await recv_json(ws, "mode") == {"t": "mode", "mode": "monitor", "id": DISPLAY2, "name": "DISPLAY2"}
        assert await recv_json(ws, "resize") == {"t": "resize", "w": 1536, "h": 1080}
        await eventually(lambda: len(streams) == 2)
        assert streams[-1].monitor["name"] == DISPLAY2 and streams[-1].region is None
        assert streams[-1].src == (1536, 1080)
        assert backend.region == (1920, 0, 1536, 1080)  # 入力の座標は、そのモニターが基準

        await send(ws, t="mode", mode="monitor", id=DISPLAY1)  # 主モニター
        assert (await recv_json(ws, "mode"))["id"] == DISPLAY1
        assert await recv_json(ws, "resize") == {"t": "resize", "w": 1920, "h": 1080}
        await eventually(lambda: len(streams) == 3)
        assert streams[-1].monitor is None and backend.region is None  # screen と同じ

        await send(ws, t="mode", mode="monitor", id="\\\\.\\DISPLAY9")
        assert "見つかりません" in (await recv_json(ws, "error"))["message"]
        assert len(streams) == 3
        await ws.close()

    asyncio.run(with_server(body, backend, streams))


# --- スリープを防ぐ

def test_keep_awake_on_while_connected_and_off_after_disconnect():
    backend = make_backend(ThreadRecordingInput)

    async def body(url):
        loop_thread = threading.get_ident()
        assert backend.keep_awake_calls == []
        ws, _ = await open_session(url)
        assert backend.keep_awake_calls == [True]

        # 別の端末がつないで、前の接続が切られても、入れたまま（切れ目に戻さない）
        ws2, _ = await open_session(url)
        await asyncio.wait_for(ws.wait_closed(), 5)
        await asyncio.sleep(0.2)
        assert backend.keep_awake_calls == [True]

        await ws2.close()
        await eventually(lambda: backend.keep_awake_calls == [True, False])
        # 入れるときと戻すときは、同じスレッド（イベントループ）から呼ぶ
        assert backend.awake_threads == [loop_thread, loop_thread]

    asyncio.run(with_server(body, backend))


def test_keep_awake_not_called_for_rejected_connection():
    backend = make_backend()

    async def body(url):
        async with connect(url) as ws:
            await ws.send(json.dumps({"t": "hello", "v": protocol.VERSION, "token": "wrong"}))
            await recv_json(ws, "error")
        await asyncio.sleep(0.1)
        assert backend.keep_awake_calls == []

    asyncio.run(with_server(body, backend))


# --- 画質

def test_quality_changes_settings_and_recaptures():
    backend = make_backend()
    streams = []

    async def body(url):
        ws, _ = await open_session(url, fps=60, max_height=0, bitrate=8000)
        await eventually(lambda: len(streams) == 1)
        assert streams[0].started_with == (60, 0, 8000)

        # 省略したものは今のまま。範囲の外は、端にそろえる
        await send(ws, t="quality", max_height=540, bitrate=999999)
        assert await recv_json(ws, "resize") == {"t": "resize", "w": 960, "h": 540}
        await eventually(lambda: len(streams) == 2)
        assert streams[0].stopped
        assert streams[1].started_with == (60, 540, 20000)

        await send(ws, t="quality", fps=1, bitrate=1)
        await eventually(lambda: len(streams) == 3)
        assert streams[2].started_with == (5, 540, 500)

        await send(ws, t="quality", fps=500)
        await eventually(lambda: len(streams) == 4)
        assert streams[3].started_with == (120, 540, 500)

        # 変わらないときは、撮り直さない
        await send(ws, t="quality", fps=120, bitrate=500)
        await send(ws, t="ping", n=1)
        await recv_json(ws, "pong")
        assert len(streams) == 4
        await ws.close()

    asyncio.run(with_server(body, backend, streams))


def test_quality_keeps_current_monitor():
    backend = make_backend()
    streams = []

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="mode", mode="monitor", id=DISPLAY2)
        await eventually(lambda: len(streams) == 2)
        await send(ws, t="quality", max_height=540)
        await eventually(lambda: len(streams) == 3)
        assert streams[-1].monitor["name"] == DISPLAY2  # 本物の画面に戻らない
        assert streams[-1].src == (1536, 1080) and streams[-1].started_with[1] == 540
        await ws.close()

    asyncio.run(with_server(body, backend, streams))


# --- stats

def test_stats_are_sent_periodically(monkeypatch):
    monkeypatch.setattr(server_mod, "STATS_INTERVAL_SEC", 0.1)

    async def body(url):
        ws, _ = await open_session(url)
        first = await recv_json(ws, "stats")
        assert set(first) == {"t", "sent", "dropped", "kbps"}
        assert first["sent"] == 1 and first["dropped"] == 0 and first["kbps"] > 0
        second = await recv_json(ws, "stats")  # 前の stats からの数なので、新しい映像がなければ 0
        assert second == {"t": "stats", "sent": 0, "dropped": 0, "kbps": 0}
        await ws.close()

    asyncio.run(with_server(body, make_backend()))


def test_stats_count_sent_and_dropped_frames(monkeypatch):
    monkeypatch.setattr(server_mod, "RESTART_MAX", 0)  # 止まったときは、撮り直さずに、すぐ失敗にする

    async def go():
        ws = SlowSocket()
        session = Session(ws, Settings(token="t"), NullInput(), lambda *a: BurstStream())
        await session.open_stream()
        session.start_video()
        with pytest.raises(Exception):  # BurstStream は、最後に止まる
            await asyncio.wait_for(session.failed, 10)
        await session.stop_stream()
        return len(ws.frames), session.take_stats(), session.take_stats()

    frames, stats, again = asyncio.run(go())
    assert stats["sent"] == frames
    assert stats["dropped"] > 0 and stats["sent"] + stats["dropped"] == 80
    assert stats["kbps"] > 0
    assert again["sent"] == 0 and again["dropped"] == 0  # 前に取った分は引く
