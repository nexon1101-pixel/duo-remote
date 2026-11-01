"""制御用の入り口（localctl.py）と、今すぐの後片付け（server.py の cleanup_now）のテスト。
ネットワークは 127.0.0.1 の中だけ。"""
import asyncio
import json

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from duo_server import localctl, protocol
from duo_server import server as server_mod
from duo_server.input_win import NullInput
from duo_server.server import Settings, make_handler
from duo_server.virtual_display import VirtualDisplayError

KEY = "control-key-for-test"
TOKEN = "t"
AUD = b"\x00\x00\x00\x01\x09\xf0"


# --- 制御用の入り口

async def ask_line(port, raw: bytes, wait=True):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), 5) if wait else b""
    writer.close()
    return json.loads(line) if line else None


async def with_control(body, cleanup=None, status=None, **kw):
    calls = []

    async def default_cleanup():
        calls.append("cleanup")
        return {"ok": True, "restored": 2, "virtual_off": True, "was_on": True}

    def default_status():
        calls.append("status")
        return {"ok": True, "virtual_on": False, "brought": 0}

    server = await localctl.start_control_server(
        0, KEY, cleanup or default_cleanup, status or default_status, **kw
    )
    try:
        await body(server.sockets[0].getsockname()[1], calls)
    finally:
        server.close()
        await server.wait_closed()


def request(cmd, key=KEY):
    return (json.dumps({"key": key, "cmd": cmd}) + "\n").encode()


def test_listens_on_loopback_only():
    async def body(port, calls):
        pass

    async def go():
        server = await localctl.start_control_server(0, KEY, None, None)
        try:
            hosts = {s.getsockname()[0] for s in server.sockets}
        finally:
            server.close()
        return hosts

    assert asyncio.run(go()) == {"127.0.0.1"}


def test_cleanup_and_status_with_right_key():
    async def body(port, calls):
        assert await ask_line(port, request("cleanup")) == {
            "ok": True, "restored": 2, "virtual_off": True, "was_on": True,
        }
        assert await ask_line(port, request("status")) == {"ok": True, "virtual_on": False, "brought": 0}
        assert calls == ["cleanup", "status"]

    asyncio.run(with_control(body))


def test_wrong_key_is_refused_without_running_anything():
    async def body(port, calls):
        for key in ("wrong", "", None, 123):
            r = await ask_line(port, request("cleanup", key))
            assert r == {"ok": False, "error": "鍵が違います"}
        r = await ask_line(port, b'{"cmd": "cleanup"}\n')  # 鍵なし
        assert r == {"ok": False, "error": "鍵が違います"}
        assert calls == []

    asyncio.run(with_control(body))


def test_unknown_cmd_bad_json_and_long_line_are_errors():
    async def body(port, calls):
        r = await ask_line(port, request("shutdown"))
        assert r["ok"] is False and "cmd" in r["error"]
        r = await ask_line(port, b"this is not json\n")
        assert r["ok"] is False and "JSON" in r["error"]
        r = await ask_line(port, b"[1, 2]\n")
        assert r["ok"] is False
        r = await ask_line(port, b'{"key": "' + b"a" * 5000 + b'"}\n')
        assert r["ok"] is False and "長すぎ" in r["error"]
        assert calls == []

    asyncio.run(with_control(body))


def test_slow_client_is_cut_after_timeout():
    async def body(port, calls):
        r = await ask_line(port, b'{"key"')  # 改行が来ない
        assert r["ok"] is False

    asyncio.run(with_control(body, read_timeout=0.3))


def test_error_inside_cleanup_is_returned_not_raised():
    async def broken():
        raise RuntimeError("こわれた")

    async def body(port, calls):
        r = await ask_line(port, request("cleanup"))
        assert r["ok"] is False and "RuntimeError" in r["error"]
        assert KEY not in json.dumps(r)

    asyncio.run(with_control(body, cleanup=broken))


# --- cleanup_now

class FakeVideo:
    async def start(self):
        pass

    async def frames(self):
        yield True, AUD + b"KEY"
        await asyncio.sleep(3600)

    async def stop(self):
        pass


class NoAdminInput(NullInput):
    """管理者の権限がなくて、仮想ディスプレイを無効にできない。"""

    def virtual_off(self):
        raise VirtualDisplayError("仮想ディスプレイを無効にするには、管理者の権限が要ります")


def make_backend(cls=NullInput):
    backend = cls((1920, 1080))
    backend.virtual_installed = True
    backend.windows = {
        101: {"title": "メモ帳", "app": "notepad.exe", "rect": (100, 50, 800, 600)},
        102: {"title": "ブラウザー", "app": "chrome.exe", "rect": (300, 100, 1000, 700)},
    }
    return backend


async def with_handler(body, backend):
    handler = make_handler(Settings(token=TOKEN), backend, lambda *a, **k: FakeVideo())
    async with serve(handler, "127.0.0.1", 0) as server:
        await body(handler, f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")


async def recv_until(ws, wanted, timeout=5):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen = []
    while True:
        raw = await asyncio.wait_for(ws.recv(), max(deadline - loop.time(), 0.01))
        if isinstance(raw, str):
            msg = json.loads(raw)
            seen.append(msg)
            if msg["t"] == wanted:
                return msg, seen


async def open_session(url):
    ws = await connect(url)
    await ws.send(json.dumps({"t": "hello", "v": protocol.VERSION, "token": TOKEN}))
    await recv_until(ws, "welcome")
    return ws


def prepare_virtual(backend):
    backend.virtual_on()
    backend.bring_window(101, (1920, 0, 800, 600))


def test_cleanup_now_restores_windows_and_turns_virtual_off():
    backend = make_backend()
    prepare_virtual(backend)
    assert backend.virtual_enabled and backend.brought_windows() == [101]

    async def body(handler, url):
        r = await handler.cleanup_now()
        assert r == {"ok": True, "restored": 1, "virtual_off": True, "was_on": True}
        assert handler.status_now() == {"ok": True, "virtual_on": False, "brought": 0}

    asyncio.run(with_handler(body, backend))
    assert not backend.virtual_enabled and backend.brought_windows() == []
    assert backend.windows[101]["rect"] == (100, 50, 800, 600)


def test_cleanup_now_when_nothing_to_do():
    async def body(handler, url):
        assert handler.status_now() == {"ok": True, "virtual_on": False, "brought": 0}
        r = await handler.cleanup_now()
        assert r == {"ok": True, "restored": 0, "virtual_off": True, "was_on": False}

    asyncio.run(with_handler(body, make_backend()))


def test_status_now_reports_virtual_and_brought():
    backend = make_backend()
    prepare_virtual(backend)

    async def body(handler, url):
        assert handler.status_now() == {"ok": True, "virtual_on": True, "brought": 1}

    asyncio.run(with_handler(body, backend))


def test_cleanup_now_cancels_scheduled_cleanup(monkeypatch):
    monkeypatch.setattr(server_mod, "RESTORE_GRACE_SEC", 30)
    backend = make_backend()

    async def body(handler, url):
        ws = await open_session(url)
        await ws.send(json.dumps({"t": "mode", "mode": "desktop"}))
        await recv_until(ws, "mode")
        await asyncio.sleep(0.1)
        await ws.close(code=4000)  # きれいでない切れ方（1000 以外）。後片付けが予定される
        await asyncio.sleep(0.5)
        assert backend.virtual_enabled  # まだ待っている
        r = await handler.cleanup_now()
        assert r["ok"] and r["was_on"] and r["virtual_off"]
        assert not backend.virtual_enabled
        # 予定が取り消されたので、あとで二度目の後片付けは走らない
        backend.calls.clear()
        monkeypatch.setattr(server_mod, "RESTORE_GRACE_SEC", 0)
        await asyncio.sleep(0.3)
        assert ("virtual_off",) not in backend.calls

    asyncio.run(with_handler(body, backend))


def test_cleanup_now_moves_phone_back_to_screen_and_sends_notice():
    backend = make_backend()

    async def body(handler, url):
        ws = await open_session(url)
        await ws.send(json.dumps({"t": "mode", "mode": "desktop"}))
        await recv_until(ws, "mode")
        r = await handler.cleanup_now()
        assert r["ok"] and r["virtual_off"] and r["was_on"]
        mode, seen = await recv_until(ws, "mode")
        assert mode["mode"] == "screen"
        notice, _ = await recv_until(ws, "notice")
        assert notice["message"] == "仮想ディスプレイを無効にしました"
        await ws.close()

    asyncio.run(with_handler(body, backend))
    assert not backend.virtual_enabled


def test_cleanup_now_leaves_window_on_virtual_to_screen():
    backend = make_backend()

    async def body(handler, url):
        ws = await open_session(url)
        await ws.send(json.dumps({"t": "mode", "mode": "window", "id": 102, "to_virtual": True}))
        await recv_until(ws, "mode")
        r = await handler.cleanup_now()
        assert r["restored"] == 1 and r["virtual_off"]
        mode, _ = await recv_until(ws, "mode")
        assert mode["mode"] == "screen"
        await recv_until(ws, "notice")
        await ws.close()

    asyncio.run(with_handler(body, backend))
    assert backend.windows[102]["rect"] == (300, 100, 1000, 700)


def test_cleanup_now_does_not_touch_phone_showing_real_screen():
    backend = make_backend()
    prepare_virtual(backend)

    async def body(handler, url):
        ws = await open_session(url)
        r = await handler.cleanup_now()
        assert r["ok"]
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({"t": "ping"}))
        # mode や notice は届かない
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), 0.5)
                if isinstance(raw, str):
                    assert json.loads(raw)["t"] not in ("notice", "mode")
        except asyncio.TimeoutError:
            pass
        await ws.close()

    asyncio.run(with_handler(body, backend))


def test_cleanup_now_without_admin_restores_windows_and_returns_error():
    backend = make_backend(NoAdminInput)
    backend.virtual_enabled = True
    backend.bring_window(101, (1920, 0, 800, 600))

    async def body(handler, url):
        r = await handler.cleanup_now()
        assert r["ok"] is True and r["restored"] == 1 and r["virtual_off"] is False
        assert "管理者" in r["error"] and r["was_on"] is True

    asyncio.run(with_handler(body, backend))
    assert backend.windows[101]["rect"] == (100, 50, 800, 600)
