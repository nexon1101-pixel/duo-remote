import shutil
import asyncio
import json

import pytest

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


HEVC_AUD = b"\x00\x00\x00\x01\x46\x01\x50"
HEVC_KEY = (
    HEVC_AUD + b"\x00\x00\x00\x01\x40\x01\x0c" + b"\x00\x00\x00\x01\x42\x01\x01" + b"\x00\x00\x00\x01\x44\x01\xc0"
    + b"\x00\x00\x01\x26\x01" + b"\x11" * 20
)
HEVC_KEY_NO_SETS = HEVC_AUD + b"\x00\x00\x01\x26\x01" + b"\x11" * 20
HEVC_P = HEVC_AUD + b"\x00\x00\x01\x02\x01" + b"\x22" * 15


def hevc_stream(key):
    class HevcStream(LoopStream):
        async def frames(self):
            i = 0
            while True:
                yield i % 30 == 0, key if i % 30 == 0 else HEVC_P
                i += 1
                await asyncio.sleep(0.01)

    return HevcStream


class OpusLikeAudio(SineAudio):
    """Opus の1パケットくらいの大きさ（60バイト）の中身を出し続ける。"""

    async def frames(self):
        while True:
            yield bytes([0xFC]) + b"\x37" * 59
            await asyncio.sleep(0.02)


async def check_against_server(
    token, try_window, try_audio=False, audio_factory=None, virtual=None, bring=False, title=None, ime=False,
    configure=None, settings=None, stream_cls=None, **options
):
    backend = NullInput((1920, 1080))
    backend.virtual_installed = bool(virtual)
    backend.windows = {7: {"title": "メモ帳", "app": "notepad.exe", "rect": (0, 0, 800, 600)}}
    if configure:
        configure(backend)
    handler = make_handler(settings or Settings(token="secret"), backend, lambda *a: (stream_cls or LoopStream)(), audio_factory)
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        return await run_check(
            f"ws://127.0.0.1:{port}/", token, seconds=0.5, try_window=try_window, try_audio=try_audio,
            try_virtual=virtual is not None and not bring, try_bring=bring, title=title, try_ime=ime, **options,
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


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg がない")
def test_save_last_frame(tmp_path):
    """受け取った H.264 の最後のフレームを、画像にできる。"""
    import subprocess

    from duo_server.selfcheck import save_last_frame

    h264 = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
         "-frames:v", "5", "-c:v", "libx264", "-f", "h264", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    out = tmp_path / "frame.png"
    assert save_last_frame([h264], out) is None
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert save_last_frame([], tmp_path / "none.png") == "キーフレームが届いていません"


# --- 新しい機能の確認 --------------------------------------------------------------

def test_selfcheck_hevc_falls_back_to_h264(capsys):
    """H.265 を作れないサーバーでは、[NG] ではなく、知らせる。"""
    assert asyncio.run(check_against_server("secret", False, try_hevc=True))
    out = capsys.readouterr().out
    assert "このサーバーは H.265 を作れません" in out and "[NG]" not in out


def test_selfcheck_hevc_ok(capsys):
    ok = asyncio.run(check_against_server(
        "secret", False, try_hevc=True, settings=Settings(token="secret", hevc_available=True, codec="hevc"),
        stream_cls=hevc_stream(HEVC_KEY),
    ))
    out = capsys.readouterr().out
    assert ok and "映像の形は H.265" in out and "VPS・SPS・PPS が付いています" in out and "[NG]" not in out


def test_selfcheck_hevc_without_parameter_sets(capsys):
    ok = asyncio.run(check_against_server(
        "secret", False, try_hevc=True, settings=Settings(token="secret", hevc_available=True, codec="hevc"),
        stream_cls=hevc_stream(HEVC_KEY_NO_SETS),
    ))
    assert not ok and "VPS・SPS・PPS が付いていません" in capsys.readouterr().out


def test_selfcheck_opus_ok(capsys):
    seen = []

    def factory(rate, channels, codec="pcm"):
        seen.append((rate, channels, codec))
        return OpusLikeAudio()

    assert asyncio.run(check_against_server("secret", False, audio_factory=factory, try_opus=True))
    out = capsys.readouterr().out
    assert seen == [(48000, 2, "opus")]
    assert "Opus の音が届きました" in out and "平均 60バイト" in out and "[NG]" not in out


def test_selfcheck_opus_unavailable(capsys):
    assert not asyncio.run(check_against_server("secret", False, audio_factory=None, try_opus=True))
    assert "Opus の音を始められませんでした" in capsys.readouterr().out


def test_selfcheck_monitors_switches_to_virtual(capsys):
    def virtual_on(backend):
        backend.virtual_installed = True
        backend.virtual_enabled = True

    assert asyncio.run(check_against_server("secret", False, configure=virtual_on, try_monitors=True))
    out = capsys.readouterr().out
    assert "モニターの一覧: 2 個" in out and "1920x1080、主" in out and "1536x1080、主ではない、仮想" in out
    assert "モニター DISPLAY2を映せました" in out and "画面全体の表示に戻せました" in out and "[NG]" not in out


def test_selfcheck_monitors_without_secondary(capsys):
    assert asyncio.run(check_against_server("secret", False, try_monitors=True))
    out = capsys.readouterr().out
    assert "モニターの一覧: 1 個" in out and "主でないモニターがない" in out and "[NG]" not in out


def test_selfcheck_quality_resizes_and_gets_stats(capsys):
    assert asyncio.run(check_against_server("secret", False, try_quality=True))
    out = capsys.readouterr().out
    assert "1280x720 になり" in out and "元の大きさ" in out
    assert "stats が" in out and "sent " in out and "kbps" in out and "[NG]" not in out


def test_selfcheck_pull_text(capsys):
    def setup(backend):
        backend.clipboard = "こんにちは" + "あ" * 100

    assert asyncio.run(check_against_server("secret", False, configure=setup, try_pull=True))
    out = capsys.readouterr().out
    assert "文字を受け取りました（105文字）" in out and "こんにちは" in out and "あ" * 45 + "…" in out and "あ" * 46 not in out


def test_selfcheck_pull_file_saves(tmp_path, capsys):
    src = tmp_path / "src" / "メモ.txt"
    src.parent.mkdir()
    body = b"x" * 300_000  # 256KB を超えるので、2フレームになる
    src.write_bytes(body)
    out_dir = tmp_path / "saved"

    assert asyncio.run(check_against_server(
        "secret", False, configure=lambda b: setattr(b, "clipboard_files", [str(src)]),
        try_pull=True, save_dir=out_dir,
    ))
    out = capsys.readouterr().out
    assert "名前 メモ.txt、大きさ 300000バイト、mime text/plain" in out and "2フレーム" in out and "[NG]" not in out
    assert (out_dir / "メモ.txt").read_bytes() == body


def test_selfcheck_pull_image(capsys):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    assert asyncio.run(check_against_server(
        "secret", False, configure=lambda b: setattr(b, "clipboard_image", ("png", png)), try_pull=True,
    ))
    out = capsys.readouterr().out
    assert "名前 clipboard.png、大きさ 58バイト、mime image/png" in out and "[NG]" not in out


def test_selfcheck_put_saves_file(tmp_path, capsys):
    src = tmp_path / "送る.bin"
    body = bytes(range(256)) * 2000  # 512000バイト。256KB ずつだと 2 フレーム
    src.write_bytes(body)
    dest = tmp_path / "dl"

    assert asyncio.run(check_against_server(
        "secret", False, configure=lambda b: setattr(b, "download_dir", dest), put_file=src,
    ))
    out = capsys.readouterr().out
    assert "ファイルをPCに送れました（512000バイト）" in out and str(dest) in out and "[NG]" not in out
    assert (dest / "送る.bin").read_bytes() == body


def test_selfcheck_put_to_clipboard_and_missing_file(tmp_path, capsys):
    src = tmp_path / "a.txt"
    src.write_text("hi")
    assert asyncio.run(check_against_server(
        "secret", False, configure=lambda b: setattr(b, "download_dir", tmp_path / "dl"), put_file=src, put_clipboard=True,
    ))
    assert "クリップボードにも入れるよう頼みました" in capsys.readouterr().out
    assert not asyncio.run(check_against_server("secret", False, put_file=tmp_path / "ない.txt"))
    assert "ファイルを読めませんでした" in capsys.readouterr().out
