import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from duo_server import __main__ as app
from duo_server import protocol
from duo_server.capture import FfmpegStream, StreamError


def test_load_config_creates_token_once(tmp_path):
    path = tmp_path / "config.json"
    cfg, created = app.load_config(path)
    assert created and len(cfg["token"]) >= 16
    cfg2, created2 = app.load_config(path)
    assert not created2 and cfg2["token"] == cfg["token"]
    assert json.loads(path.read_text(encoding="utf-8"))["port"] == 8765


def test_load_config_recreates_missing_token(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"port": 9000, "token": ""}), encoding="utf-8")
    cfg, created = app.load_config(path)
    assert created and len(cfg["token"]) >= 16 and cfg["port"] == 9000
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == cfg["token"]


def test_missing_ffmpeg_gives_clear_error():
    async def go():
        stream = FfmpegStream(["ffmpeg-that-does-not-exist-xyz"])
        with pytest.raises(StreamError, match="ffmpeg を起動できません"):
            await stream.start()

    asyncio.run(go())


def test_server_reports_missing_ffmpeg_to_client(tmp_path):
    """ffmpegがない環境でつないだとき、クライアントにエラーが返って切れる。"""
    from websockets.asyncio.server import serve

    from duo_server.input_win import NullInput
    from duo_server.server import Settings, make_handler

    def factory(settings, src, region, monitor=None, window=None):
        return FfmpegStream(["ffmpeg-that-does-not-exist-xyz"])

    async def go():
        handler = make_handler(Settings(token="t"), NullInput(), factory)
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"t": "hello", "v": protocol.VERSION, "token": "t"}))
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert msg["t"] == "error" and "映像" in msg["message"]

    asyncio.run(go())
