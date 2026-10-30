"""仮想ディスプレイ（デスクトップの表示、ウィンドウを持ってくる、元に戻す）のテスト。"""
import asyncio
import json

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from duo_server import protocol
from duo_server import server as server_mod
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


async def with_server(body, backend, streams=None):
    streams = streams if streams is not None else []

    def factory(settings, src, region, monitor=None):
        s = FakeVideo()
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
