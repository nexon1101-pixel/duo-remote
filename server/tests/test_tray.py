"""タスクトレイ（tray.py）のテスト。Linux で動く範囲だけ（本物の pystray は Windows の実機で確認する）。"""
import sys
import threading
import time

import pytest

from duo_server import control, panel, tray
from duo_server.tray import ERROR, RUNNING, STARTING, STOPPED, TrayState


class FakeProcess:
    def __init__(self, running=False, started_at=None, last_exit=None):
        self.running = running
        self.started_at = started_at
        self.last_exit = last_exit
        self.calls = []

    def start(self, args):
        self.calls.append("start")
        self.running = True

    def stop(self):
        self.calls.append("stop")
        self.running = False

    def restart(self, args):
        self.calls.append("restart")

    def control_endpoint(self):
        return None


def test_status_text():
    assert status(RUNNING) == "DuoRemote サーバー: 実行中"
    assert status(STOPPED) == "DuoRemote サーバー: 停止中"
    assert status(STARTING) == "DuoRemote サーバー: 起動中"
    assert tray.status_text(TrayState(RUNNING, iphone=True)) == "DuoRemote サーバー: 実行中（iPhone 接続中）"
    assert tray.status_text(TrayState(RUNNING, iphone=False)) == "DuoRemote サーバー: 実行中"
    assert "停止中" in status(ERROR)


def status(kind):
    return tray.status_text(TrayState(kind))


def test_state_color():
    colors = {k: tray.state_color(TrayState(k)) for k in (RUNNING, STOPPED, STARTING, ERROR)}
    assert len(set(colors.values())) == 4
    g, gray, y, r = colors[RUNNING], colors[STOPPED], colors[STARTING], colors[ERROR]
    assert g[1] > g[0] and g[1] > g[2]  # 緑
    assert gray[0] == gray[1] == gray[2]  # 灰色
    assert y[0] > y[2] and y[1] > y[2]  # 黄色
    assert r[0] > r[1] and r[0] > r[2]  # 赤


def labels(kind, windows=True):
    return [(i.label, i.enabled, i.action) for i in tray.menu_items(TrayState(kind), windows) if not i.separator]


def test_menu_running():
    items = labels(RUNNING)
    assert items == [
        ("管理画面を開く", True, tray.OPEN),
        ("サーバーを停止", True, tray.STOP),
        ("サーバーを再起動", True, tray.RESTART),
        ("仮想ディスプレイを無効にする", True, tray.VIRTUAL_OFF),
        ("管理画面を終了", True, tray.QUIT),
    ]
    assert [i.default for i in tray.menu_items(TrayState(RUNNING), True)][0] is True


def test_menu_stopped_and_starting():
    stopped = labels(STOPPED)
    assert ("サーバーを起動", True, tray.START) in stopped
    assert ("サーバーを再起動", False, tray.RESTART) in stopped
    assert not any(a == tray.STOP for _, _, a in stopped)
    starting = labels(STARTING)
    assert ("サーバーを停止", True, tray.STOP) in starting
    assert ("サーバーを再起動", False, tray.RESTART) in starting
    assert labels(ERROR) == stopped


def test_menu_virtual_only_on_windows():
    assert not any(a == tray.VIRTUAL_OFF for _, _, a in labels(RUNNING, windows=False))
    items = tray.menu_items(TrayState(RUNNING), False)
    assert not (items[-2].separator and items[-3].separator)  # 区切りが続かない


def test_menu_separators():
    items = tray.menu_items(TrayState(RUNNING), True)
    assert [i.separator for i in items] == [False, True, False, False, True, False, True, False]


def test_state_of():
    now = 1000.0
    assert tray.state_of(FakeProcess(), now) == TrayState(STOPPED)
    assert tray.state_of(FakeProcess(last_exit=0), now) == TrayState(STOPPED)
    assert tray.state_of(FakeProcess(last_exit=1), now) == TrayState(ERROR)
    assert tray.state_of(FakeProcess(True, started_at=now - 1), now) == TrayState(STARTING)
    assert tray.state_of(FakeProcess(True, started_at=now - 10), now) == TrayState(RUNNING)


def test_tray_does_nothing_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []
    t = tray.Tray(lambda: calls.append("state") or TrayState(), {})
    t.start()
    assert not t.active and calls == []
    t.update()
    t.stop()  # 何も起きない


def test_module_does_not_import_pystray_or_pillow():
    import subprocess

    code = "import sys; import duo_server.tray; print('pystray' in sys.modules, 'PIL' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False"]


def test_tray_setting_validation(tmp_path):
    cfg = tmp_path / "config.json"
    assert control.read_settings(cfg)["tray"] is True
    assert control.save_settings({"tray": False}, cfg)["tray"] is False
    assert control.read_settings(cfg)["tray"] is False
    for bad in ("yes", 1, 0, None, "true"):
        with pytest.raises(control.SettingsError):
            control.save_settings({"tray": bad}, cfg)
    assert control.read_settings(cfg)["tray"] is False


class FakeTray:
    instances = []

    def __init__(self, get_state, actions):
        self.actions = actions
        self.started = 0
        self.stopped = 0
        FakeTray.instances.append(self)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def run_main(monkeypatch, tmp_path, argv, settings_tray=True):
    """main を動かし、/api/quit で終わらせる。作った FakeTray を返す。"""
    import socket
    from http.client import HTTPConnection

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    FakeTray.instances = []
    monkeypatch.setattr(panel, "LOCK_PATH", tmp_path / "panel.lock")
    monkeypatch.setattr(panel, "CARRY_ARGS", [])
    monkeypatch.setattr(control, "read_settings", lambda *a: {
        "panel_browser": "edge", "encoder": "auto", "output_idx": 0, "port": 8765, "tray": settings_tray,
    })
    monkeypatch.setattr(control, "refresh_autostart", lambda: None)
    monkeypatch.setattr(control, "ServerProcess", FakeProcess)
    monkeypatch.setattr(panel.tray, "Tray", FakeTray)
    opened = []
    monkeypatch.setattr(panel, "open_window", lambda *a: opened.append(a))
    done = threading.Event()
    threading.Thread(
        target=lambda: (panel.main(["--port", str(port), "--key", "k", *argv]), done.set()), daemon=True,
    ).start()
    end = time.time() + 5
    while time.time() < end and not panel.ping(port, "k"):
        time.sleep(0.02)
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/api/quit", body=b"{}", headers={"X-Panel-Key": "k", "Host": f"127.0.0.1:{port}"})
    assert conn.getresponse().status == 200
    assert done.wait(10)
    return FakeTray.instances[0], opened


def test_main_starts_tray_by_default(monkeypatch, tmp_path):
    t, _ = run_main(monkeypatch, tmp_path, [])
    assert t.started == 1 and t.stopped >= 1  # 終わるときに stop を呼ぶ


def test_main_no_tray_option(monkeypatch, tmp_path):
    t, _ = run_main(monkeypatch, tmp_path, ["--no-tray", "--autostart"])
    assert t.started == 0


def test_main_tray_setting_false(monkeypatch, tmp_path):
    t, _ = run_main(monkeypatch, tmp_path, [], settings_tray=False)
    assert t.started == 0


def test_main_autostart_with_tray_has_no_window(monkeypatch, tmp_path):
    t, opened = run_main(monkeypatch, tmp_path, ["--autostart"])
    assert t.started == 1 and opened == []


def test_menu_actions_call_panel_functions(monkeypatch, tmp_path):
    """メニューの動きは、ページの操作と同じ処理を呼ぶ。"""
    monkeypatch.setattr(control, "is_admin", lambda: False)
    process = FakeProcess(running=True)
    handler = panel.make_app(process, "k", tmp_path / "config.json")
    actions = handler.tray_actions
    actions[tray.RESTART]()
    actions[tray.STOP]()
    actions[tray.START]()
    assert process.calls == ["restart", "stop", "start"]

    seen = []
    monkeypatch.setattr(panel, "direct_virtual_off", lambda: seen.append("off") or True)
    monkeypatch.setattr(control, "is_admin", lambda: True)
    message = actions[tray.VIRTUAL_OFF]()
    assert seen == ["off"] and "無効にしました" in message

    process.calls.clear()
    actions[tray.QUIT]()
    assert process.calls == ["stop"]


def test_tray_invoke_calls_action_and_notifies():
    notes = []
    t = tray.Tray(lambda: TrayState(), {tray.OPEN: lambda: "ひらきました"})
    t._icon = type("I", (), {"notify": lambda self, m, title: notes.append(m)})()
    t._invoke(tray.OPEN)
    assert notes == ["ひらきました"]
    t._invoke("unknown")  # 知らない名前は無視
    t._invoke(tray.OPEN)


def test_settings_change_calls_tray_hook(monkeypatch, tmp_path):
    handler = panel.make_app(FakeProcess(), "k", tmp_path / "config.json")
    got = []
    handler.hooks["tray"] = got.append
    # Handler のメソッドを、通信なしで呼ぶ
    result = handler._settings_post(handler, {"changes": {"tray": False}})
    assert result[0] == 200 and got == [False]
    result = handler._settings_post(handler, {"changes": {"tray": "x"}})
    assert result[0] == 400 and got == [False]
