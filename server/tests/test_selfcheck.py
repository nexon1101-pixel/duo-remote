import asyncio
import json

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


class SineAudio:
    """音の入った、20msごとのかたまりを出し続ける、音の取得元のかわり。"""

    def __init__(self, loud=True):
        self.loud = loud

    async def start(self):
        pass

    async def frames(self):
        import struct

        while True:
            yield struct.pack("<1920h", *[(8000 if self.loud else 0) * (1 if i % 2 else -1) for i in range(1920)])
            await asyncio.sleep(0.02)

    async def stop(self):
        pass


async def check_against_server(
    token, try_window, try_audio=False, audio_factory=None, virtual=None, bring=False, title=None, ime=False
):
    backend = NullInput((1920, 1080))
    backend.virtual_installed = bool(virtual)
    backend.windows = {7: {"title": "メモ帳", "app": "notepad.exe", "rect": (0, 0, 800, 600)}}
    handler = make_handler(Settings(token="secret"), backend, lambda *a: LoopStream(), audio_factory)
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        return await run_check(
            f"ws://127.0.0.1:{port}/", token, seconds=0.5, try_window=try_window, try_audio=try_audio,
            try_virtual=virtual is not None and not bring, try_bring=bring, title=title, try_ime=ime,
        )


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


def test_selfcheck_audio_ok(capsys):
    assert asyncio.run(check_against_server("secret", False, True, lambda r, c: SineAudio()))
    out = capsys.readouterr().out
    assert "音が届きました" in out and "[NG]" not in out


def test_selfcheck_audio_silent(capsys):
    assert not asyncio.run(check_against_server("secret", False, True, lambda r, c: SineAudio(loud=False)))
    assert "ほぼ無音" in capsys.readouterr().out


def test_selfcheck_audio_unavailable(capsys):
    assert not asyncio.run(check_against_server("secret", False, True, None))
    assert "音を始められませんでした" in capsys.readouterr().out


def test_selfcheck_virtual_ok(capsys):
    assert asyncio.run(check_against_server("secret", False, virtual=True))
    out = capsys.readouterr().out
    assert "仮想ディスプレイ（デスクトップ）を映せました" in out and "1536x1080" in out and "[NG]" not in out


def test_selfcheck_virtual_not_installed(capsys):
    assert not asyncio.run(check_against_server("secret", False, virtual=False))
    assert "ドライバーが見つかりません" in capsys.readouterr().out


def test_selfcheck_reports_server_closing_the_connection(capsys):
    """サーバーが、途中で接続を閉じても、落ちずに [NG] で理由を出す。"""

    async def closing_handler(ws):
        await ws.recv()  # hello
        await ws.send(json.dumps({"t": "welcome", "v": 1, "w": 1920, "h": 1080, "codec": "h264", "mode": "screen"}))
        await ws.close(1008, "映像の取得が止まりました。サーバーのログを確認してください")

    async def go():
        async with serve(closing_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await run_check(f"ws://127.0.0.1:{port}/", "x", seconds=0.3)

    assert not asyncio.run(go())
    out = capsys.readouterr().out
    assert "サーバーが接続を閉じました" in out and "映像の取得が止まりました" in out and "duo_server.log" in out


def test_selfcheck_bring_window_to_virtual_and_back(capsys):
    assert asyncio.run(check_against_server("secret", False, virtual=True, bring=True))
    out = capsys.readouterr().out
    assert "仮想ディスプレイに持ってこられました（main → virtual）" in out
    assert "だけを映せました" in out and "元の場所（main）に戻せました" in out and "[NG]" not in out


def test_selfcheck_picks_window_by_title(capsys):
    assert asyncio.run(check_against_server("secret", True, title="メモ"))
    assert "「メモ帳」だけの表示に切り替えられました" in capsys.readouterr().out
    assert not asyncio.run(check_against_server("secret", True, title="存在しない窓"))
    assert "試すウィンドウが見つかりません" in capsys.readouterr().out


def test_selfcheck_bring_without_driver(capsys):
    assert not asyncio.run(check_against_server("secret", False, virtual=False, bring=True))
    assert "ドライバーが見つかりません" in capsys.readouterr().out


def test_selfcheck_ime_toggle(capsys):
    assert asyncio.run(check_against_server("secret", False, title="メモ", ime=True))
    out = capsys.readouterr().out
    assert "IMEをオンにできました" in out and "IMEをオフにできました" in out and "[NG]" not in out
