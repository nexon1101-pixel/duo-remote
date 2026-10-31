import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from duo_server import protocol
from duo_server import server as server_mod
from duo_server.capture import StreamError
from duo_server.input_win import NullInput
from duo_server.server import Session, Settings, make_handler

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


class WindowFailsStream(FakeStream):
    """ウィンドウの範囲を撮ろうとすると、起動に失敗する映像。"""

    async def start(self):
        if self.region is not None:
            raise StreamError("ffmpeg が起動できませんでした（テスト）")


async def run_with_server(body, backend=None, streams=None, stream_cls=FakeStream, window_capture=False):
    backend = backend or NullInput((1920, 1080))
    streams = streams if streams is not None else []

    def factory(settings, src, region, monitor=None, window=None):
        s = stream_cls()
        s.settings, s.src, s.region, s.window = settings, src, region, window
        streams.append(s)
        return s

    handler = make_handler(Settings(token=TOKEN, window_capture=window_capture), backend, factory)
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await body(f"ws://127.0.0.1:{port}", backend)


async def recv_json(ws):
    """映像（バイナリ）は読み飛ばして、次のJSONメッセージを返す。"""
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(raw, str):
            return json.loads(raw)


async def recv_frame(ws):
    """制御メッセージ（JSON）は読み飛ばして、次の映像のフレームを返す。"""
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(raw, bytes):
            return protocol.unpack_frame(raw)


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
                "t": "welcome", "v": 1, "w": 960, "h": 540, "codec": "h264", "mode": "screen", "virtual": False, "pad": False,
            }
            key, ts, payload = await recv_frame(ws)
            assert key and payload.endswith(b"KEY")
            key, ts2, payload = await recv_frame(ws)
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
            await send(t="key", sc=0x1E0, d="down")
            await send(t="key", sc=0, d="down")
            await send(t="text", s=123)
            await send(t="unknown")
            await ws.send("これはJSONではありません")
            await send(t="ping", n=1)
            while (await recv_json(ws))["t"] != "pong":
                pass
        assert backend.calls == []

    asyncio.run(run_with_server(body))


async def recv_until(ws, t):
    """種類が t のメッセージが来るまで読み、それを返す。"""
    while True:
        msg = await recv_json(ws)
        if msg["t"] == t:
            return msg


def make_backend():
    backend = NullInput((1920, 1080))
    backend.windows = {
        101: {"title": "メモ帳", "app": "notepad.exe", "rect": (100, 50, 801, 601)},
        202: {"title": "外のモニター", "app": "a.exe", "rect": (2000, 0, 800, 600)},
    }
    return backend


def test_list_windows():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "list_windows"}))
            msg = await recv_until(ws, "windows")
            assert msg["list"][0] == {"id": 101, "title": "メモ帳", "app": "notepad.exe", "where": "main"}
            assert len(msg["list"]) == 2

    asyncio.run(run_with_server(body, backend=make_backend()))


def test_window_mode_crops_and_maps_input():
    streams = []

    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "mode", "mode": "window", "id": 101, "fit_w": 669, "fit_h": 951}))
            mode = await recv_until(ws, "mode")
            assert mode == {"t": "mode", "mode": "window", "id": 101, "title": "メモ帳"}
            assert await recv_until(ws, "resize") == {"t": "resize", "w": 800, "h": 600}
            # 撮り直した映像が、resizeの後に届く
            key, _, payload = await recv_frame(ws)
            assert key and payload.endswith(b"KEY")

            await ws.send(json.dumps({"t": "mode", "mode": "screen"}))
            assert (await recv_until(ws, "mode"))["mode"] == "screen"
            assert await recv_until(ws, "resize") == {"t": "resize", "w": 1920, "h": 1080}
        await asyncio.sleep(0.2)

        assert ("fit", 101, 669, 951) in backend.calls and ("focus", 101) in backend.calls
        assert [s.region for s in streams] == [None, (100, 50, 800, 600), None]
        assert all(s.stopped for s in streams)

    asyncio.run(run_with_server(body, backend=make_backend(), streams=streams))


def test_window_region_is_used_for_input():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "mode", "mode": "window", "id": 101}))
            await recv_until(ws, "resize")
            assert backend.region == (100, 50, 800, 600)
        await asyncio.sleep(0.1)
        assert backend.region is None  # 切断したら画面全体に戻す

    asyncio.run(run_with_server(body, backend=make_backend()))


def test_window_errors():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            for msg in (
                {"t": "mode", "mode": "window", "id": 999},  # ない
                {"t": "mode", "mode": "window", "id": "101"},  # 形が違う
                {"t": "mode", "mode": "window", "id": 202},  # 主モニターの外
                {"t": "mode", "mode": "other"},
            ):
                await ws.send(json.dumps(msg))
                assert (await recv_until(ws, "error"))["message"]

    asyncio.run(run_with_server(body, backend=make_backend()))


def test_window_moved_and_closed(monkeypatch):
    monkeypatch.setattr(server_mod, "WINDOW_POLL_SEC", 0.05)
    streams = []

    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "mode", "mode": "window", "id": 101}))
            await recv_until(ws, "resize")

            backend.windows[101]["rect"] = (0, 0, 1000, 700)
            assert await recv_until(ws, "resize") == {"t": "resize", "w": 1000, "h": 700}

            backend.windows[101]["minimized"] = True  # 最小化の間は撮り直さない
            await asyncio.sleep(0.3)
            backend.windows[101]["minimized"] = False

            del backend.windows[101]
            assert "閉じられた" in (await recv_until(ws, "error"))["message"]
            assert (await recv_until(ws, "mode"))["mode"] == "screen"
            assert await recv_until(ws, "resize") == {"t": "resize", "w": 1920, "h": 1080}

        assert [s.region for s in streams] == [None, (100, 50, 800, 600), (0, 0, 1000, 700), None]

    asyncio.run(run_with_server(body, backend=make_backend(), streams=streams))


def test_held_buttons_and_keys_are_released_on_disconnect():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            send = lambda **m: ws.send(json.dumps(m))
            await send(t="button", b="left", d="down", x=0.5, y=0.5)
            await send(t="key", vk=0x10, d="down")
            await send(t="key", vk=0x41, d="down")
            await send(t="key", vk=0x41, d="up")
            await send(t="key", sc=0xE04B, d="down")  # 左矢印（拡張キー）
            await send(t="key", sc=0x1E, d="down")
            await send(t="key", sc=0x1E, d="up")
            await send(t="ping", n=1)
            await recv_until(ws, "pong")
        await asyncio.sleep(0.2)
        released = backend.calls[7:]
        assert sorted(released, key=repr) == [
            ("button", "left", False, None, None), ("key", 0x10, False), ("scan", 0xE04B, False),
        ]

    asyncio.run(run_with_server(body))


def test_clamp_region():
    from duo_server.server import clamp_region

    assert clamp_region((100, 50, 801, 601), (1920, 1080)) == (100, 50, 800, 600)
    assert clamp_region((-8, -8, 1936, 1096), (1920, 1080)) == (0, 0, 1920, 1080)  # 最大化
    assert clamp_region((2000, 0, 800, 600), (1920, 1080)) is None
    assert clamp_region((1900, 0, 800, 600), (1920, 1080)) is None  # はみ出して、小さすぎる


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


def test_text_newlines_become_enter():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "text", "s": "a\nb\r\nc"}))
            await ws.send(json.dumps({"t": "text", "s": "\n"}))
            await ws.send(json.dumps({"t": "ping", "n": 1}))
            await recv_until(ws, "pong")
        enter = [("key", 0x0D, True), ("key", 0x0D, False)]
        assert backend.calls == [("text", "a"), *enter, ("text", "b"), *enter, ("text", "c"), *enter]

    asyncio.run(run_with_server(body))


def test_move_rel():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "move_rel", "dx": 0.1, "dy": -2}))  # 範囲外は -1〜1 にそろえる
            await ws.send(json.dumps({"t": "move_rel", "dx": 0, "dy": 0}))  # 動かないものは送らない
            await ws.send(json.dumps({"t": "ping", "n": 1}))
            await recv_until(ws, "pong")
        assert backend.calls == [("move_rel", 0.1, -1.0)]

    asyncio.run(run_with_server(body))


def test_hello_quality_settings():
    streams = []

    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello(bitrate=1500, fps=30, max_height=720))
            assert (await recv_until(ws, "welcome"))["h"] == 720
        async with connect(url) as ws:
            await ws.send(hello(bitrate=999999, fps=1000))  # 上限と下限にそろえる
            await recv_until(ws, "welcome")

    asyncio.run(run_with_server(body, streams=streams))
    first, second = streams[0].settings, streams[1].settings
    assert (first.bitrate_kbps, first.fps, first.max_height) == (1500, 30, 720)
    assert (second.bitrate_kbps, second.fps) == (20000, 120)


def test_window_that_cannot_be_captured_falls_back_to_screen():
    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "mode", "mode": "window", "id": 101}))
            assert (await recv_until(ws, "mode"))["mode"] == "window"
            assert "メイン画面に戻します" in (await recv_until(ws, "error"))["message"]
            assert (await recv_until(ws, "mode"))["mode"] == "screen"
            # 接続は続いている
            await ws.send(json.dumps({"t": "ping", "n": 5}))
            assert (await recv_until(ws, "pong"))["n"] == 5
            assert backend.region is None

    asyncio.run(run_with_server(body, backend=make_backend(), stream_cls=WindowFailsStream))


def test_unresponsive_previous_client_does_not_delay_new_one():
    streams = []

    async def body(url, backend):
        async with connect(url) as first:
            await first.send(hello())
            await recv_until(first, "welcome")
            await first.send(json.dumps({"t": "key", "vk": 0x41, "d": "down"}))
            await first.send(json.dumps({"t": "ping", "n": 1}))
            await recv_until(first, "pong")
            first.transport.pause_reading()  # 電波が切れて、応答しなくなった端末のかわり

            loop = asyncio.get_running_loop()
            started = loop.time()
            async with connect(url) as second:
                await second.send(hello())
                await recv_until(second, "welcome")
                assert loop.time() - started < 1.0
            # 前の接続の後片付けが、新しい映像の前に終わっている
            assert streams[0].stopped
            assert ("key", 0x41, False) in backend.calls
            first.transport.resume_reading()

    asyncio.run(run_with_server(body, streams=streams))


class SlowSocket:
    """送るのが遅い接続のかわり。届いた映像を記録する。"""

    def __init__(self):
        self.frames = []

    async def send(self, data):
        if isinstance(data, bytes):
            await asyncio.sleep(0.02)
            self.frames.append(protocol.unpack_frame(data))


class BurstStream:
    """速く次々とフレームを出す映像。10フレームごとにキーフレーム。"""

    async def start(self):
        pass

    async def frames(self):
        for i in range(80):
            yield i % 10 == 0, f"{i:03d}".encode()
            await asyncio.sleep(0.002)
        raise StreamError("おわり")

    async def stop(self):
        pass


def test_frames_are_dropped_until_keyframe_when_sending_falls_behind(monkeypatch):
    monkeypatch.setattr(server_mod, "RESTART_MAX", 0)  # 止まったときは、撮り直さずに、すぐ失敗にする

    async def go():
        ws = SlowSocket()
        session = Session(ws, Settings(token="t"), NullInput(), lambda *a: BurstStream())
        await session.open_stream()
        session.start_video()
        with pytest.raises(StreamError):
            await asyncio.wait_for(session.failed, 10)
        await session.stop_stream()
        return ws.frames

    frames = asyncio.run(go())
    numbers = [int(payload) for _, _, payload in frames]
    assert numbers[0] == 0 and len(numbers) < 80  # 捨てたフレームがある
    for prev, cur, (key, _, _) in zip(numbers, numbers[1:], frames[1:]):
        if cur != prev + 1:
            # 捨てたあとは、キーフレームから始まる
            assert key and cur % 10 == 0


async def recv_reply(ws):
    """ime_state（0.5秒ごとに来ることがある）を読み飛ばして、次のJSONメッセージを返す。"""
    while True:
        msg = await recv_json(ws)
        if msg["t"] != "ime_state":
            return msg


def test_clipboard():
    async def body(url, backend):
        backend.clipboard = "PCの\r\n文字"
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_reply(ws)  # welcome
            await ws.send(json.dumps({"t": "clip_get"}))
            msg = await recv_reply(ws)
            assert msg == {"t": "clip", "s": "PCの\n文字"}  # 改行は \n にそろえる

            await ws.send(json.dumps({"t": "clip_set", "s": "a\nb\r\nc"}))
            msg = await recv_reply(ws)
            assert msg["t"] == "notice"
            assert backend.clipboard == "a\r\nb\r\nc"  # Windowsの決まり（CRLF）にする

            await ws.send(json.dumps({"t": "clip_set", "s": "x" * (protocol.MAX_CLIP_LENGTH + 1)}))
            assert (await recv_reply(ws))["t"] == "error"
            assert backend.clipboard == "a\r\nb\r\nc"  # 長すぎるものは入れない

    asyncio.run(run_with_server(body))


def test_clipboard_error_keeps_connection():
    class Busy(NullInput):
        def clip_get(self):
            from duo_server.input_win import ClipboardError
            raise ClipboardError("PCのクリップボードを、他のアプリが使っています")

    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_reply(ws)
            await ws.send(json.dumps({"t": "clip_get"}))
            assert (await recv_reply(ws))["t"] == "error"
            await ws.send(json.dumps({"t": "ping", "n": 1}))
            while (await recv_reply(ws))["t"] != "pong":
                pass

    asyncio.run(run_with_server(body, backend=Busy((1920, 1080))))


def test_window_capture_takes_whole_window_and_does_not_restart_on_move(monkeypatch):
    """ウィンドウだけを撮るときは、ウィンドウの番号を渡し、はみ出した部分も切り詰めない。
    動かしただけなら撮り直さず、操作の座標だけを合わせる。大きさが変わったら撮り直す。"""
    monkeypatch.setattr(server_mod, "WINDOW_POLL_SEC", 0.05)
    streams = []

    async def body(url, backend):
        # 右に 200 はみ出したウィンドウ（主モニターは 1920x1080）
        backend.windows[101]["rect"] = (1320, 50, 801, 600)
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "mode", "mode": "window", "id": 101}))
            assert await recv_until(ws, "resize") == {"t": "resize", "w": 800, "h": 600}
            assert backend.region == (1320, 50, 800, 600)

            backend.windows[101]["rect"] = (10, 20, 801, 600)  # 動かしただけ
            for _ in range(40):
                if backend.region == (10, 20, 800, 600):
                    break
                await asyncio.sleep(0.05)
            assert backend.region == (10, 20, 800, 600)
            assert len(streams) == 2  # 撮り直していない

            backend.windows[101]["rect"] = (10, 20, 1000, 700)  # 大きさが変わった
            assert await recv_until(ws, "resize") == {"t": "resize", "w": 1000, "h": 700}

        assert [(s.region, s.window) for s in streams] == [
            (None, None), ((1320, 50, 800, 600), 101), ((10, 20, 1000, 700), 101),
        ]

    asyncio.run(run_with_server(body, backend=make_backend(), streams=streams, window_capture=True))


class SilentStream(FakeStream):
    """ウィンドウの中身が変わるまで、1枚も出さない映像（ffmpeg 8.1 の gfxcapture と同じ）。
    ウィンドウに描き直しを頼まれたら、出し始める。"""

    repainted: asyncio.Event | None = None

    async def frames(self):
        if self.window is not None:
            await SilentStream.repainted.wait()
        yield True, b"\x00\x00\x00\x01\x09\xf0KEY"
        await asyncio.sleep(3600)


def test_window_capture_repaints_until_first_frame(monkeypatch):
    """ウィンドウだけを撮るとき、最初の1枚が届くまで、ウィンドウに描き直しを頼む。届いたら、やめる。"""
    monkeypatch.setattr(server_mod, "NUDGE_INTERVAL_SEC", 0.05)

    async def body(url, backend):
        SilentStream.repainted = asyncio.Event()
        repaint = backend.repaint_window

        def on_repaint(wid):
            repaint(wid)
            SilentStream.repainted.set()

        backend.repaint_window = on_repaint
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await recv_frame(ws)  # 画面全体の最初の1枚
            await ws.send(json.dumps({"t": "mode", "mode": "window", "id": 101}))
            await recv_until(ws, "resize")
            key, _, payload = await recv_frame(ws)
            assert key and payload.endswith(b"KEY")
            count = backend.calls.count(("repaint", 101))
            await asyncio.sleep(0.3)
            assert count >= 1 and backend.calls.count(("repaint", 101)) == count  # 届いたあとは頼まない

    asyncio.run(run_with_server(body, backend=make_backend(), stream_cls=SilentStream, window_capture=True))


def test_codec_is_chosen_from_hello():
    from duo_server.server import choose_codec

    assert choose_codec(["hevc", "h264"], True) == "hevc"
    assert choose_codec(["hevc", "h264"], False) == "h264"
    assert choose_codec(None, True) == "h264"
    assert choose_codec(["av1"], True) == "h264"

    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello(codecs=["hevc", "h264"]))
            welcome = json.loads(await ws.recv())
            assert welcome["codec"] == "h264"  # このサーバー（テスト）は H.265 を作れない

    asyncio.run(run_with_server(body))


def test_window_thumb_sends_jpeg_or_none():
    """ウィンドウの小さい絵は、取れたものは JPEG（base64）で、取れなかったものは none で、頼んだ順に届く。"""
    import base64
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg がない")

    backend = make_backend()
    # 幅16 x 高さ8 の、明るい赤（BGRA）。202 は絵がない
    backend.windows[101]["thumb"] = (16, 8, bytes([40, 40, 220, 255]) * (16 * 8))

    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            await ws.send(json.dumps({"t": "window_thumb", "ids": [101, 202, 999, "x"], "w": 100}))
            first = await recv_until(ws, "window_thumb")
            assert first["id"] == 101 and (first["w"], first["h"]) == (16, 8)
            assert base64.b64decode(first["jpeg"]).startswith(b"\xff\xd8")
            assert await recv_until(ws, "window_thumb") == {"t": "window_thumb", "id": 202, "none": True}
            assert await recv_until(ws, "window_thumb") == {"t": "window_thumb", "id": 999, "none": True}
            assert ("thumb", 101, 100) in backend.calls

    asyncio.run(run_with_server(body, backend=backend))


def test_window_thumb_width_is_clamped_and_ids_are_limited():
    backend = make_backend()

    async def body(url, backend):
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_until(ws, "welcome")
            ids = list(range(1000, 1000 + protocol.THUMB_MAX_IDS + 5))
            await ws.send(json.dumps({"t": "window_thumb", "ids": ids, "w": 99999}))
            for wid in ids[: protocol.THUMB_MAX_IDS]:
                assert (await recv_until(ws, "window_thumb"))["id"] == wid
            thumbs = [c for c in backend.calls if c[0] == "thumb"]
            assert len(thumbs) == protocol.THUMB_MAX_IDS
            assert all(c[2] == protocol.THUMB_MAX_WIDTH for c in thumbs)

    asyncio.run(run_with_server(body, backend=backend))


def test_is_blank_bgra():
    from duo_server.input_win import is_blank_bgra

    assert is_blank_bgra(bytes(4 * 100))
    assert not is_blank_bgra(bytes([30, 30, 30, 255]) * 100)
