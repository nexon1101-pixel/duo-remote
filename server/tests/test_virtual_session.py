"""仮想ディスプレイ（デスクトップの表示、ウィンドウを持ってくる、元に戻す）のテスト。"""
import asyncio
import json

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from duo_server import protocol
from duo_server import server as server_mod
from duo_server.capture import StreamError
from duo_server.input_win import NullInput
from duo_server.server import Settings, make_handler

TOKEN = "t"
AUD = b"\x00\x00\x00\x01\x09\xf0"


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


def make_backend(installed=True):
    backend = NullInput((1920, 1080))
    backend.virtual_installed = installed
    backend.windows = {
        101: {"title": "メモ帳", "app": "notepad.exe", "rect": (100, 50, 800, 600)},
        102: {"title": "ブラウザー", "app": "chrome.exe", "rect": (300, 100, 1000, 700)},
    }
    return backend


class DyingVideo(FakeVideo):
    """撮り始めに、ffmpeg が止まる映像（モニターの番号が合わないときなど）。"""

    async def frames(self):
        raise StreamError("ffmpeg が終了しました（コード 4294967274）\nFailed to configure output pad on Parsed_ddagrab_0")
        yield  # 非同期ジェネレーターにするため


class DiesLaterVideo(FakeVideo):
    """最初のフレームは出して、そのあと止まる映像。"""

    async def frames(self):
        yield True, AUD + b"KEY"
        await asyncio.sleep(0.2)
        raise StreamError("ffmpeg が終了しました（コード 1）\nDXGI_ERROR_ACCESS_LOST")


async def with_server(body, backend, streams=None, stream_for=None):
    """stream_for: (モニター, 何個目の映像か) -> 映像。省略すると、いつも正常な映像。"""
    streams = streams if streams is not None else []

    def factory(settings, src, region, monitor=None, window=None):
        s = stream_for(monitor, len(streams)) if stream_for else FakeVideo()
        s.src, s.region, s.monitor = src, region, monitor
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


async def open_session(url):
    ws = await connect(url)
    await ws.send(json.dumps({"t": "hello", "v": protocol.VERSION, "token": TOKEN}))
    welcome = await recv_json(ws, "welcome")
    return ws, welcome


def send(ws, **msg):
    return ws.send(json.dumps(msg))


def test_welcome_tells_whether_virtual_display_is_installed():
    async def check(installed):
        out = {}

        async def body(url):
            ws, welcome = await open_session(url)
            out["virtual"] = welcome["virtual"]
            await ws.close()

        await with_server(body, make_backend(installed))
        return out["virtual"]

    assert asyncio.run(check(True)) is True
    assert asyncio.run(check(False)) is False


def test_desktop_mode_shows_virtual_monitor_and_turns_it_off_on_disconnect():
    backend = make_backend()
    streams = []

    async def body(url):
        ws, _ = await open_session(url)
        assert not backend.virtual_enabled  # つないだだけでは、出さない
        await send(ws, t="mode", mode="desktop", fit_w=669, fit_h=951)  # 縦持ちの内側画面
        assert (await recv_json(ws, "notice"))["message"]
        assert (await recv_json(ws, "mode"))["mode"] == "desktop"
        # 高さ1080のまま、縦横比に合わせた幅（669/951 * 1080 = 760）
        assert await recv_json(ws, "resize") == {"t": "resize", "w": 760, "h": 1080}
        assert backend.virtual_enabled
        monitor = streams[-1].monitor
        assert monitor["virtual"] and streams[-1].region is None
        assert backend.region == monitor["rect"]  # 入力の座標は、仮想モニターが基準
        await ws.close()  # 正常な切断（切断ボタン）
        await asyncio.sleep(0.3)

    asyncio.run(with_server(body, backend, streams))
    assert not backend.virtual_enabled and ("virtual_off",) in backend.calls
    assert backend.region is None


def test_desktop_mode_errors():
    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="mode", mode="desktop")
        assert "入っていません" in (await recv_json(ws, "error"))["message"]
        await send(ws, t="ping", n=1)  # 接続は続いている
        assert (await recv_json(ws, "pong"))["n"] == 1
        await ws.close()

    asyncio.run(with_server(body, make_backend(installed=False)))

    backend = make_backend()
    backend.virtual_error = "仮想ディスプレイを出すには、サーバーを管理者として起動してください"

    async def body2(url):
        ws, _ = await open_session(url)
        await send(ws, t="mode", mode="desktop")
        assert "管理者" in (await recv_json(ws, "error"))["message"]
        await ws.close()

    asyncio.run(with_server(body2, backend))
    assert not backend.virtual_enabled


def test_bring_window_to_virtual_monitor_and_restore():
    backend = make_backend()

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="list_windows")
        assert {w["where"] for w in (await recv_json(ws, "windows"))["list"]} == {"main"}

        await send(ws, t="bring", id=101)
        brought = await recv_json(ws, "brought")
        assert brought == {"t": "brought", "id": 101, "title": "メモ帳"}
        vx = backend.monitors()[-1]["work"]
        assert backend.windows[101]["rect"] == vx  # 仮想モニターの作業領域いっぱいに移った
        await send(ws, t="list_windows")
        wheres = {w["id"]: w["where"] for w in (await recv_json(ws, "windows"))["list"]}
        assert wheres == {101: "virtual", 102: "main"}

        await send(ws, t="restore")
        assert "1個" in (await recv_json(ws, "notice"))["message"]
        assert backend.windows[101]["rect"] == (100, 50, 800, 600)  # 元の位置に戻った
        await ws.close()

    asyncio.run(with_server(body, backend))


def test_window_on_virtual_monitor_is_captured_there_and_restored_on_disconnect():
    backend = make_backend()
    streams = []

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="mode", mode="window", id=102, to_virtual=True, fit_w=951, fit_h=669)  # 横持ち
        mode = await recv_json(ws, "mode")
        assert mode["mode"] == "window" and mode["id"] == 102
        await recv_json(ws, "resize")  # 撮り直しのあと
        stream = streams[-1]
        assert stream.monitor["virtual"]
        assert stream.region == backend.windows[102]["rect"]  # ウィンドウの範囲（デスクトップ上の位置）
        assert ("virtual_aspect", round(951 / 669, 3)) in backend.calls
        await ws.close()
        await asyncio.sleep(0.3)

    asyncio.run(with_server(body, backend, streams))
    assert backend.windows[102]["rect"] == (300, 100, 1000, 700)  # 切断で、元の位置に戻った
    assert not backend.virtual_enabled


def test_connection_drop_waits_before_restoring(monkeypatch):
    monkeypatch.setattr(server_mod, "RESTORE_GRACE_SEC", 0.6)
    backend = make_backend()

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="bring", id=101)
        await recv_json(ws, "brought")
        ws.transport.abort()  # 通信が切れた（切断ボタンではない）
        await asyncio.sleep(0.3)
        assert backend.virtual_enabled and backend.windows[101]["rect"] != (100, 50, 800, 600)  # まだ戻さない
        await asyncio.sleep(0.8)
        assert not backend.virtual_enabled and backend.windows[101]["rect"] == (100, 50, 800, 600)

    asyncio.run(with_server(body, backend))


def test_reconnecting_within_grace_keeps_virtual_display(monkeypatch):
    monkeypatch.setattr(server_mod, "RESTORE_GRACE_SEC", 0.6)
    backend = make_backend()

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="bring", id=101)
        await recv_json(ws, "brought")
        ws.transport.abort()
        await asyncio.sleep(0.2)
        ws2, welcome = await open_session(url)  # 待っている間につなぎ直した
        assert welcome["virtual"]
        await asyncio.sleep(1.0)  # 待ち時間を過ぎても、戻さない
        assert backend.virtual_enabled and backend.windows[101]["rect"] != (100, 50, 800, 600)
        await ws2.close()  # 切断ボタンで、戻る
        await asyncio.sleep(0.3)
        assert not backend.virtual_enabled and backend.windows[101]["rect"] == (100, 50, 800, 600)

    asyncio.run(with_server(body, backend))


def test_server_shutdown_restores_everything():
    backend = make_backend()
    backend.virtual_on()
    backend.bring_window(101, backend.monitors()[-1]["work"])
    backend.shutdown()
    assert not backend.virtual_enabled
    assert backend.windows[101]["rect"] == (100, 50, 800, 600)
    backend.shutdown()  # 2回呼んでも、困らない


def test_bring_remembers_only_the_first_position():
    backend = make_backend()
    backend.virtual_on()
    work = backend.monitors()[-1]["work"]
    backend.bring_window(101, work)
    backend.bring_window(101, (work[0] + 10, work[1], work[2] - 10, work[3]))  # もう一度持ってきても
    backend.restore_brought()
    assert backend.windows[101]["rect"] == (100, 50, 800, 600)  # 最初の位置に戻る


def test_virtual_capture_is_retried_when_it_fails_at_first(monkeypatch):
    monkeypatch.setattr(server_mod, "VIRTUAL_RETRY_SEC", 0.01)
    backend = make_backend()
    streams = []

    def stream_for(monitor, n):
        # 仮想モニターの、最初の2回は失敗、3回目から正常
        return DyingVideo() if monitor and monitor["virtual"] and n < 3 else FakeVideo()

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="mode", mode="desktop")
        assert (await recv_json(ws, "mode"))["mode"] == "desktop"
        await asyncio.sleep(0.3)
        await send(ws, t="ping", n=3)
        assert (await recv_json(ws, "pong"))["n"] == 3
        await ws.close()

    asyncio.run(with_server(body, backend, streams, stream_for))
    virtual_streams = [s for s in streams if s.monitor and s.monitor["virtual"]]
    assert len(virtual_streams) == 3  # 2回失敗して、3回目で撮れた
    assert streams[-1] is virtual_streams[-1] and not isinstance(streams[-1], DyingVideo)


def test_virtual_capture_falls_back_to_screen_with_reason(monkeypatch):
    monkeypatch.setattr(server_mod, "VIRTUAL_RETRY_SEC", 0.01)
    backend = make_backend()

    def stream_for(monitor, n):
        return DyingVideo() if monitor and monitor["virtual"] else FakeVideo()

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="mode", mode="desktop")
        error = await recv_json(ws, "error")
        # 理由（ffmpeg のエラーの、最後の1行）が、利用者に見える
        assert "仮想画面を映せませんでした" in error["message"] and "Failed to configure output pad" in error["message"]
        assert "メイン画面に戻します" in error["message"]
        assert (await recv_json(ws, "mode"))["mode"] == "screen"
        await send(ws, t="ping", n=4)  # 接続は続いている
        assert (await recv_json(ws, "pong"))["n"] == 4
        assert backend.region is None
        await ws.close()

    asyncio.run(with_server(body, backend, stream_for=stream_for))


def test_stream_that_dies_later_returns_to_screen_without_closing(monkeypatch):
    monkeypatch.setattr(server_mod, "RESTART_DELAY_SEC", 0.01)
    backend = make_backend()
    streams = []

    def stream_for(monitor, n):
        return DiesLaterVideo() if monitor and monitor["virtual"] else FakeVideo()

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="mode", mode="desktop")
        await recv_json(ws, "mode")
        error = await recv_json(ws, "error")  # 0.2秒後に、映像が止まった
        assert "メイン画面に戻します" in error["message"] and "DXGI_ERROR_ACCESS_LOST" in error["message"]
        await send(ws, t="ping", n=5)
        assert (await recv_json(ws, "pong"))["n"] == 5
        await ws.close()

    asyncio.run(with_server(body, backend, streams, stream_for))
    assert streams[-1].monitor is None  # 本物の画面を撮り直している


def test_leftover_virtual_display_is_turned_off_at_startup():
    backend = make_backend()
    backend.virtual_on()  # 前回、後片付けをせずに止められて、有効のまま残った
    assert backend.virtual_cleanup_leftover() is True
    assert not backend.virtual_enabled
    assert backend.virtual_cleanup_leftover() is False  # 残っていなければ、何もしない


class AccessLostOnce(FakeVideo):
    """画面の設定が変わって、撮影が一度無効になった映像（DXGI_ERROR_ACCESS_LOST）。"""

    async def frames(self):
        yield True, AUD + b"KEY"
        await asyncio.sleep(0.1)
        raise StreamError("ffmpeg が終了しました（コード 0）\nAcquireNextFrame failed: 887a0026")


def test_capture_is_restarted_after_access_lost_without_closing(monkeypatch):
    monkeypatch.setattr(server_mod, "RESTART_DELAY_SEC", 0.01)
    streams = []

    def stream_for(monitor, n):
        return AccessLostOnce() if n == 0 else FakeVideo()  # 最初の1回だけ、止まる

    async def body(url):
        ws, _ = await open_session(url)  # 本物の画面を撮っている
        await asyncio.sleep(0.5)  # 止まって、撮り直された
        await send(ws, t="ping", n=1)
        assert (await recv_json(ws, "pong"))["n"] == 1  # 接続は切れていない
        await ws.close()

    asyncio.run(with_server(body, make_backend(), streams, stream_for))
    assert len(streams) == 2  # 止まった映像と、撮り直した映像
    assert all(s.monitor is None for s in streams)  # どちらも、本物の画面
    assert all(s.stopped for s in streams)  # 切断のあと、どちらも後片付けされた


def test_connection_is_closed_when_the_screen_keeps_failing(monkeypatch):
    from websockets.exceptions import ConnectionClosed

    monkeypatch.setattr(server_mod, "RESTART_DELAY_SEC", 0.01)
    monkeypatch.setattr(server_mod, "RESTART_MAX", 3)

    async def body(url):
        ws, _ = await open_session(url)
        try:
            while True:  # 何度撮り直しても止まるので、最後は、接続が閉じられる
                await asyncio.wait_for(ws.recv(), 5)
        except ConnectionClosed as e:
            assert e.rcvd and "映像" in e.rcvd.reason

    asyncio.run(with_server(body, make_backend(), stream_for=lambda monitor, n: DyingVideo()))


def test_capture_is_paused_while_virtual_display_changes_and_resumed_after():
    """仮想ディスプレイを出す間（画面の設定が変わる間）は、動いている撮影を、先に止める。"""
    streams = []
    seen = []

    class SpyBackend(NullInput):
        def virtual_on(self):
            seen.append(streams[0].stopped)  # 画面の設定を変える時点で、本物の画面の撮影は、止まっているはず
            return super().virtual_on()

    backend = SpyBackend((1920, 1080))
    backend.virtual_installed = True
    backend.windows = {101: {"title": "メモ帳", "app": "notepad.exe", "rect": (100, 50, 800, 600)}}

    async def body(url):
        ws, _ = await open_session(url)
        await send(ws, t="bring", id=101)  # 表示は、本物の画面のまま。仮想ディスプレイが出るので、一度止めて、撮り直す
        await recv_json(ws, "brought")
        await asyncio.sleep(0.3)
        await ws.close()

    asyncio.run(with_server(body, backend, streams))
    assert seen == [True]
    assert len(streams) == 2 and streams[1].monitor is None  # 本物の画面を、撮り直した
