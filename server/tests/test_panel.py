"""管理画面（panel.py）のテスト。実際のサーバーは動かさず、偽物の ServerProcess を渡す。"""
import json
import sys
import threading
import time
import types
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from duo_server import control, panel

KEY = "test-key"


class FakeProcess:
    def __init__(self):
        self.running = False
        self.started_at = None
        self.last_exit = None
        self.calls = []
        self.lines = ["a", "b", "c"]
        self.endpoint = None  # 制御用の入り口 (ポート, 鍵)

    def start(self, args):
        self.calls.append(("start", args))
        self.running = True

    def stop(self):
        self.calls.append(("stop", None))
        self.running = False

    def restart(self, args):
        self.calls.append(("restart", args))
        self.running = True

    def control_endpoint(self):
        return self.endpoint if self.running else None

    def log_since(self, seq):
        return len(self.lines), self.lines[seq:]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "local_addresses", lambda: ["192.168.0.5", "100.64.0.1"])
    monkeypatch.setattr(control, "is_admin", lambda: False)
    monkeypatch.setattr(control, "autostart_enabled", lambda: False)
    monkeypatch.setattr(panel, "RELAUNCH_WAIT", 0)
    monkeypatch.setattr(panel, "UPDATE_LOG_PATH", tmp_path / "last_update.log")
    monkeypatch.setattr(control, "address_details", lambda: [])
    config = tmp_path / "config.json"
    process = FakeProcess()
    handler = panel.make_app(process, KEY, config)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, path, body=None, key=KEY, host=None):
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        if key is not None:
            conn.putheader("X-Panel-Key", key)
        data = json.dumps(body).encode() if body is not None else b""
        conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, raw

    call.process = process
    call.handler = handler
    call.config = config
    call.port = port
    yield call
    httpd.shutdown()
    httpd.server_close()


def test_page_needs_key(app):
    assert app("GET", "/", key=None)[0] == 403
    assert app("GET", "/?k=wrong", key=None)[0] == 403
    status, html = app("GET", f"/?k={KEY}", key=None)
    assert status == 200 and "DuoRemote" in html


def test_api_needs_key(app):
    assert app("GET", "/api/ping", key=None)[0] == 403
    assert app("GET", "/api/ping", key="wrong")[0] == 403
    assert app("GET", "/api/ping") == (200, {"ok": True})


def test_wrong_host(app):
    assert app("GET", "/api/ping", host="evil.example:80")[0] == 403
    assert app("GET", f"/?k={KEY}", key=None, host="evil.example")[0] == 403
    assert app("GET", "/api/ping", host=f"localhost:{app.port}")[0] == 200


def test_status(app):
    status, data = app("GET", "/api/status")
    assert status == 200
    assert data["running"] is False
    assert data["addresses"] == ["192.168.0.5", "100.64.0.1"]
    assert data["port"] == 8765
    assert data["admin"] is False and data["autostart"] is False
    assert "version" in data and "started_at" in data and "last_exit" in data
    # Linux には vgamepad も ViGEmBus もない。使えない、と理由が返る
    assert data["pad"]["available"] is False and data["pad"]["reason"]


def test_start_stop_restart(app):
    status, data = app("POST", "/api/start")
    assert status == 200 and data["running"] is True
    assert app.process.calls[0][0] == "start" and "--control-stdin" in app.process.calls[0][1]
    assert app("POST", "/api/restart")[1]["running"] is True
    assert app("POST", "/api/stop")[1]["running"] is False
    assert [c[0] for c in app.process.calls] == ["start", "restart", "stop"]


def test_log(app):
    assert app("GET", "/api/log?since=1")[1] == {"seq": 3, "lines": ["b", "c"]}
    assert app("GET", "/api/log?since=x")[0] == 400


def test_settings_get_and_post(app):
    status, data = app("GET", "/api/settings")
    assert status == 200
    assert data["settings"]["fps"] == 60 and data["settings"]["token"]
    assert data["limits"]["fps"] == [5, 120]
    assert "libx264" in data["encoders"]

    status, data = app("POST", "/api/settings", {"changes": {"fps": 30, "encoder": "libx264"}})
    assert status == 200 and data["settings"]["fps"] == 30 and data["restart_needed"] is False
    app.process.running = True
    assert app("POST", "/api/settings", {"changes": {"fps": 45}})[1]["restart_needed"] is True
    assert json.loads(app.config.read_text(encoding="utf-8"))["fps"] == 45

    status, data = app("POST", "/api/settings", {"changes": {"fps": 1000}})
    assert status == 400 and "fps" in data["error"]
    assert app("POST", "/api/settings", {"changes": {"token": "x"}})[0] == 400
    assert app("POST", "/api/settings", {})[0] == 400


def test_token(app):
    old = app("GET", "/api/settings")[1]["settings"]["token"]
    status, data = app("POST", "/api/token")
    assert status == 200 and data["token"] != old
    assert app.process.calls == []  # 止まっているときは起動しない
    app.process.running = True
    app("POST", "/api/token")
    assert app.process.calls[-1][0] == "restart"


def test_autostart(app, monkeypatch):
    monkeypatch.setattr(control, "set_autostart", lambda enabled: None if enabled else "だめです")
    assert app("POST", "/api/autostart", {"enabled": True})[0] == 200
    status, data = app("POST", "/api/autostart", {"enabled": False})
    assert status == 400 and data["error"] == "だめです"
    assert app("POST", "/api/autostart", {"enabled": "yes"})[0] == 400


def fake_updater(monkeypatch, apply):
    mod = types.ModuleType("duo_server.updater")

    class UpdateError(Exception):
        pass

    mod.UpdateError = UpdateError
    mod.check = lambda: {"current": "0.1.0", "latest": "0.2.0", "available": True, "method": "zip", "error": None}
    mod.apply = apply
    monkeypatch.setitem(sys.modules, "duo_server.updater", mod)
    import duo_server

    monkeypatch.setattr(duo_server, "updater", mod, raising=False)
    return mod


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_update_check(app, monkeypatch):
    fake_updater(monkeypatch, lambda log: None)
    status, data = app("GET", "/api/update")
    assert status == 200 and data["latest"] == "0.2.0" and data["method"] == "zip"


def test_update_apply_success(app, monkeypatch):
    launched = []
    monkeypatch.setattr(panel, "relaunch", lambda args: launched.append(args))

    def apply(log):
        log("取ってきます")
        log("入れ替えました")

    fake_updater(monkeypatch, apply)
    status, started = app("POST", "/api/update")
    assert status == 200 and started["started"] is True and started["next_key"]
    assert app.handler.exit_signal.wait(5)
    status, data = app("GET", "/api/update/log")
    assert data == {
        "running": False, "lines": ["取ってきます", "入れ替えました"], "done": True, "error": None, "previous": [],
        "next_key": started["next_key"],
    }
    # 新しい管理画面には、ページが移れるよう、鍵を渡す
    assert launched == [["--restart", "--key", started["next_key"]]]
    # 起動し直した管理画面は、ファイルに残った前回の更新のログを見せる
    assert panel.UPDATE_LOG_PATH.read_text(encoding="utf-8").splitlines() == ["取ってきます", "入れ替えました"]
    assert ("stop", None) in app.process.calls


def test_update_apply_failure(app, monkeypatch):
    launched = []
    monkeypatch.setattr(panel, "relaunch", lambda args: launched.append(args))
    mod = fake_updater(monkeypatch, lambda log: None)

    def apply(log):
        raise mod.UpdateError("つながりません")

    mod.apply = apply
    app("POST", "/api/update")
    assert wait_for(lambda: not app("GET", "/api/update/log")[1]["running"])
    data = app("GET", "/api/update/log")[1]
    assert data["error"] == "つながりません" and data["done"] is False
    assert launched == [] and not app.handler.exit_signal.is_set()


def test_admin_and_quit(app, monkeypatch):
    monkeypatch.setattr(panel, "run_as_admin", lambda extra=None: False)
    status, data = app("POST", "/api/admin")
    assert status == 400 and data["error"] == "管理者として起動できませんでした"
    assert not app.handler.exit_signal.is_set()
    admin_args = []
    monkeypatch.setattr(panel, "run_as_admin", lambda extra=None: admin_args.append(extra) or True)
    status, data = app("POST", "/api/admin")
    assert status == 200 and data["next_key"]
    assert admin_args == [["--key", data["next_key"]]]  # 新しい管理画面に、同じ鍵を渡す
    assert app.handler.exit_signal.wait(5)
    assert ("stop", None) in app.process.calls


def test_quit(app):
    assert app("POST", "/api/quit")[0] == 200
    assert app.handler.exit_signal.wait(5)
    assert app.process.calls[-1][0] == "stop"


def test_lock_and_ping(tmp_path, app):
    lock = tmp_path / "panel.lock"
    assert panel.running_panel(lock) is None
    lock.write_text(json.dumps({"port": app.port, "key": KEY, "pid": 1}))
    assert panel.running_panel(lock)["port"] == app.port
    lock.write_text(json.dumps({"port": app.port, "key": "bad", "pid": 1}))
    assert panel.running_panel(lock) is None


def test_browser_of(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "read_settings", lambda: {"panel_browser": "chrome"})
    assert panel.browser_of(panel.parse_args([])) == "chrome"
    assert panel.browser_of(panel.parse_args(["--browser", "default"])) == "default"


def test_open_window_falls_back(monkeypatch):
    opened = []
    monkeypatch.setattr(panel, "find_chrome", lambda: None)
    monkeypatch.setattr(panel.webbrowser, "open", lambda url: opened.append(("default", url)))
    panel.open_window("http://x/", "chrome")
    assert opened == [("default", "http://x/")]
    monkeypatch.setattr(panel, "find_chrome", lambda: "chrome.exe")
    monkeypatch.setattr(panel.subprocess, "Popen", lambda cmd: opened.append(tuple(cmd)))
    panel.open_window("http://y/", "chrome")
    assert opened[-1] == ("chrome.exe", "--new-tab", "http://y/")


def test_relaunch_carries_browser(monkeypatch):
    """--browser chrome で開いた管理画面は、起動し直しても Chrome で開く。"""
    started = []
    monkeypatch.setattr(panel.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    monkeypatch.setattr(panel, "CARRY_ARGS", ["--browser", "chrome"])
    panel.relaunch(["--restart"])
    assert started[0][-3:] == ["--restart", "--browser", "chrome"]


def test_page_does_not_use_browser_dialogs():
    """confirm() や alert() は、PCを操作する Claude が押せず、タブが止まるので使わない。"""
    html = (panel.Path(panel.__file__).parent / "panel.html").read_text(encoding="utf-8")
    assert "confirm(" not in html and "alert(" not in html and "prompt(" not in html


def test_main_with_handed_over_key(tmp_path, monkeypatch):
    """main を通しで動かす。渡された鍵で待ち受け、ウィンドウは開かず、--browser と --port を引き継ぐ。"""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(panel, "LOCK_PATH", tmp_path / "panel.lock")
    monkeypatch.setattr(panel, "CARRY_ARGS", [])
    monkeypatch.setattr(control, "read_settings", lambda *a: {"panel_browser": "edge", "encoder": "auto", "output_idx": 0})
    monkeypatch.setattr(control, "refresh_autostart", lambda: None)
    opened = []
    monkeypatch.setattr(panel, "open_window", lambda *a: opened.append(a))

    monkeypatch.setattr(control, "ServerProcess", FakeProcess)
    done = threading.Event()
    result = []
    t = threading.Thread(
        target=lambda: (result.append(panel.main(["--port", str(port), "--key", "handed", "--browser", "chrome"])), done.set()),
        daemon=True,
    )
    t.start()
    assert wait_for(lambda: panel.ping(port, "handed"))
    assert opened == []
    assert panel.CARRY_ARGS == ["--browser", "chrome", "--port", str(port)]
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/api/quit", body=b"{}", headers={"X-Panel-Key": "handed", "Host": f"127.0.0.1:{port}"})
    assert conn.getresponse().status == 200
    assert done.wait(10) and result == [0]


# --- 仮想ディスプレイを今すぐ無効にする

CONTROL_KEY = "fake-control-key"


def run_fake_control(reply):
    """偽の制御用の入り口を、別のスレッドで動かす。(ポート, 受け取った要求の一覧, 止める関数) を返す。"""
    import asyncio

    from duo_server import localctl

    received = []
    ready = threading.Event()
    box = {}

    async def cleanup():
        received.append("cleanup")
        return reply

    async def main():
        server = await localctl.start_control_server(0, CONTROL_KEY, cleanup, lambda: {"ok": True})
        box["port"] = server.sockets[0].getsockname()[1]
        box["stop"] = asyncio.Event()
        box["loop"] = asyncio.get_running_loop()
        ready.set()
        await box["stop"].wait()
        server.close()

    thread = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
    thread.start()
    assert ready.wait(5)

    def stop():
        box["loop"].call_soon_threadsafe(box["stop"].set)
        thread.join(5)

    return box["port"], received, stop


def test_virtual_cleanup_needs_key(app):
    status, _ = app("POST", "/api/virtual/cleanup", {}, key=None)
    assert status == 403
    status, _ = app("POST", "/api/virtual/cleanup", {}, key="wrong")
    assert status == 403


def test_virtual_cleanup_via_server(app, monkeypatch):
    port, received, stop = run_fake_control({"ok": True, "restored": 2, "virtual_off": True, "was_on": True})
    try:
        app.process.running = True
        app.process.endpoint = (port, CONTROL_KEY)
        monkeypatch.setattr(panel, "direct_virtual_off", lambda: pytest.fail("直接は呼ばない"))
        status, data = app("POST", "/api/virtual/cleanup", {})
    finally:
        stop()
    assert status == 200 and received == ["cleanup"]
    assert data["via"] == "server" and data["restored"] == 2 and data["virtual_off"] is True and data["was_on"] is True
    assert "2 個" in data["message"] and "無効にしました" in data["message"]
    assert CONTROL_KEY not in json.dumps(data)


def test_virtual_cleanup_via_server_without_admin(app):
    port, _, stop = run_fake_control({
        "ok": True, "restored": 1, "virtual_off": False, "was_on": True,
        "error": "仮想ディスプレイを無効にするには、管理者の権限が要ります",
    })
    try:
        app.process.running = True
        app.process.endpoint = (port, CONTROL_KEY)
        status, data = app("POST", "/api/virtual/cleanup", {})
    finally:
        stop()
    assert status == 200 and data["virtual_off"] is False and data["restored"] == 1
    assert "管理者として起動し直してください" in data["message"] and "1 個" in data["message"]


def test_virtual_cleanup_direct_when_server_stopped(app, monkeypatch):
    calls = []
    monkeypatch.setattr(control, "is_admin", lambda: True)
    monkeypatch.setattr(panel, "direct_virtual_off", lambda: calls.append("off") or True)
    status, data = app("POST", "/api/virtual/cleanup", {})  # サーバーは止まっている
    assert status == 200 and calls == ["off"]
    assert data["via"] == "direct" and data["virtual_off"] is True and data["was_on"] is True
    assert "戻していません" in data["message"]


def test_virtual_cleanup_direct_when_server_unreachable(app, monkeypatch):
    import socket

    with socket.socket() as s:  # 誰も待っていないポート
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    app.process.running = True
    app.process.endpoint = (dead, CONTROL_KEY)
    monkeypatch.setattr(control, "is_admin", lambda: True)
    monkeypatch.setattr(panel, "direct_virtual_off", lambda: False)
    status, data = app("POST", "/api/virtual/cleanup", {})
    assert status == 200 and data["via"] == "direct" and data["was_on"] is False
    assert "すでに無効" in data["message"]


def test_virtual_cleanup_direct_calls_off_with_force(app, monkeypatch):
    from duo_server import virtual_display

    calls = []

    class FakeVD:
        def monitor(self):
            return {"name": "x"}

        def off(self, force=False):
            calls.append(force)

    monkeypatch.setattr(virtual_display, "VirtualDisplay", FakeVD)
    monkeypatch.setattr(control, "is_admin", lambda: True)
    status, data = app("POST", "/api/virtual/cleanup", {})
    assert status == 200 and calls == [True] and data["was_on"] is True


def test_virtual_cleanup_direct_needs_admin(app, monkeypatch):
    monkeypatch.setattr(panel, "direct_virtual_off", lambda: pytest.fail("管理者でないので呼ばない"))
    status, data = app("POST", "/api/virtual/cleanup", {})  # is_admin は False
    assert status == 400 and "管理者として起動し直してください" in data["error"]


def test_server_process_gives_control_port_and_key(tmp_path, monkeypatch):
    seen = {}

    class Fake(control.ServerProcess):
        def command(self, args):
            seen["args"] = args
            return [sys.executable, "-c", "import time; time.sleep(30)"]

    proc = Fake(cwd=tmp_path)
    assert proc.control_endpoint() is None
    proc.start(["--port", "1"])
    try:
        port, key = proc.control_endpoint()
        args = seen["args"]
        assert args[args.index("--control-port") + 1] == str(port) and 0 < port < 65536
        assert args[args.index("--control-key") + 1] == key and len(key) >= 16
        assert key not in "\n".join(proc.log_since(0)[1])  # ログに出ない
    finally:
        proc.stop()
    assert proc.control_endpoint() is None
