import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from duo_server import protocol
from duo_server.input_win import NullInput
from duo_server.server import Settings, make_handler

TOKEN = "test-token"


class FakeStream:
    """ffmpegの代わりに、決まったフレームを返す。"""

    def __init__(self):
        self.stopped = False

    async def start(self):
        pass

    async def frames(self):
        yield True, b"\x00\x00\x00\x01\x09\xf0KEY"
        yield False, b"\x00\x00\x00\x01\x09\xf0P1"
        await asyncio.sleep(3600)  # 以後は止まったまま

    async def stop(self):
        self.stopped = True


async def run_with_server(body, backend=None, streams=None):
    backend = backend or NullInput((1920, 1080))
    streams = streams if streams is not None else []

    def factory(settings, src):
        s = FakeStream()
        streams.append(s)
        return s

    handler = make_handler(Settings(token=TOKEN), backend, factory)
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await body(f"ws://127.0.0.1:{port}", backend)


async def recv_json(ws):
    """映像（バイナリ）は読み飛ばして、次のJSONメッセージを返す。"""
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(raw, str):
            return json.loads(raw)


def hello(token=TOKEN, **extra):
    return json.dumps({"t": "hello", "v": protocol.VERSION, "token": token, **extra})


def test_wrong_token_is_rejected():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello(token="wrong"))
            msg = json.loads(await ws.recv())
            assert msg["t"] == "error"
            with pytest.raises(ConnectionClosed):
                await ws.recv()

    asyncio.run(run_with_server(body))


def test_first_message_must_be_hello():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(json.dumps({"t": "move", "x": 0.5, "y": 0.5}))
            assert json.loads(await ws.recv())["t"] == "error"
        assert backend.calls == []  # 認証前の入力は通らない

    asyncio.run(run_with_server(body))


def test_welcome_video_and_input():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello(max_height=540))
            welcome = json.loads(await ws.recv())
            assert welcome == {
                "t": "welcome", "v": 1, "w": 960, "h": 540, "codec": "h264", "mode": "screen",
            }
            key, ts, payload = protocol.unpack_frame(await ws.recv())
            assert key and payload.endswith(b"KEY")
            key, ts2, payload = protocol.unpack_frame(await ws.recv())
            assert not key and payload.endswith(b"P1") and ts2 >= ts

            send = lambda **m: ws.send(json.dumps(m))
            await send(t="move", x=0.25, y=2.0)  # 範囲外は0〜1にそろえる
            await send(t="button", b="left", d="down", x=0.5, y=0.5)
            await send(t="button", b="left", d="up")
            await send(t="scroll", dx=0, dy=-1.5)
            await send(t="key", vk=0x41, d="down")
            await send(t="key", vk=0x41, d="up")
            await send(t="text", s="こんにちは")
            await send(t="ime", on=True)
            await send(t="ping", n=7)
            while True:  # ime_state などが先に来ることがある
                msg = json.loads(await ws.recv())
                if msg["t"] == "pong":
                    assert msg["n"] == 7
                    break

        assert backend.calls == [
            ("move", 0.25, 1.0),
            ("button", "left", True, 0.5, 0.5),
            ("button", "left", False, None, None),
            ("scroll", 0.0, -1.5),
            ("key", 0x41, True),
            ("key", 0x41, False),
            ("text", "こんにちは"),
            ("ime", True),
        ]

    asyncio.run(run_with_server(body))


def test_invalid_input_is_ignored():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            assert (await recv_json(ws))["t"] == "welcome"
            send = lambda **m: ws.send(json.dumps(m))
            await send(t="move", x="a", y=None)
            await send(t="button", b="side", d="down")
            await send(t="key", vk=0, d="down")
            await send(t="key", vk=True, d="down")
            await send(t="text", s=123)
            await send(t="unknown")
            await ws.send("これはJSONではありません")
            await send(t="ping", n=1)
            while (await recv_json(ws))["t"] != "pong":
                pass
        assert backend.calls == []

    asyncio.run(run_with_server(body))


def test_window_mode_is_not_supported_yet():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            assert (await recv_json(ws))["t"] == "welcome"
            await ws.send(json.dumps({"t": "mode", "mode": "window", "window": "メモ帳"}))
            while (await recv_json(ws))["t"] != "error":
                pass

    asyncio.run(run_with_server(body))


def test_new_client_replaces_old_and_stream_is_stopped():
    streams = []

    async def body(url, backend):
        async with connect(url) as first:
            await first.send(hello())
            await first.recv()
            async with connect(url) as second:
                await second.send(hello())
                assert json.loads(await second.recv())["t"] == "welcome"
                with pytest.raises(ConnectionClosed):
                    while True:
                        await asyncio.wait_for(first.recv(), 3)
        await asyncio.sleep(0.2)
        assert len(streams) == 2 and all(s.stopped for s in streams)

    asyncio.run(run_with_server(body, streams=streams))
