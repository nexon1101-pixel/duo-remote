import asyncio

from websockets.asyncio.server import serve

from duo_server.input_win import NullInput
from duo_server.selfcheck import run_check
from duo_server.server import Settings, make_handler

AUD = b"\x00\x00\x00\x01\x09\xf0"
KEY = AUD + b"\x00\x00\x00\x01\x67\x42\x00\x1f" + b"\x00\x00\x00\x01\x68\xce\x3c\x80" + b"\x00\x00\x00\x01\x65" + b"\x11" * 20
P = AUD + b"\x00\x00\x01\x41" + b"\x22" * 15


class LoopStream:
    """キーフレームのあと、Pフレームを出し続ける映像。"""

    async def start(self):
        pass

    async def frames(self):
        i = 0
        while True:
            yield i % 30 == 0, KEY if i % 30 == 0 else P
            i += 1
            await asyncio.sleep(0.01)

    async def stop(self):
        pass


async def check_against_server(token, try_window):
    backend = NullInput((1920, 1080))
    backend.windows = {7: {"title": "メモ帳", "app": "notepad.exe", "rect": (0, 0, 800, 600)}}
    handler = make_handler(Settings(token="secret"), backend, lambda *a: LoopStream())
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        return await run_check(f"ws://127.0.0.1:{port}/", token, seconds=0.5, try_window=try_window)


def test_selfcheck_passes(capsys):
    assert asyncio.run(check_against_server("secret", try_window=True))
    out = capsys.readouterr().out
    assert "[NG]" not in out and "メモ帳" in out and "すべて問題ありません" in out


def test_selfcheck_reports_wrong_token(capsys):
    assert not asyncio.run(check_against_server("wrong", try_window=False))
    assert "トークンが違います" in capsys.readouterr().out


def test_selfcheck_reports_no_server(capsys):
    assert not asyncio.run(run_check("ws://127.0.0.1:9/", "x", seconds=0.1))
    assert "つながりません" in capsys.readouterr().out
