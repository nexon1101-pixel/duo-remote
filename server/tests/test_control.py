import json
import sys
import time

import pytest

from duo_server import control


def test_save_settings_checks_ranges(tmp_path):
    cfg = tmp_path / "config.json"
    settings = control.read_settings(cfg)
    assert settings["token"] and settings["port"] == 8765 and settings["encoder"] == "auto"

    saved = control.save_settings({"fps": 30, "encoder": "libx264", "output_idx": 1}, cfg)
    assert saved["fps"] == 30 and saved["encoder"] == "libx264" and saved["output_idx"] == 1
    on_disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert on_disk["token"] == settings["token"]  # トークンは変わらない

    for bad in ({"fps": 500}, {"port": "x"}, {"fps": 1.5}, {"fps": True}, {"encoder": "h265"}, {"token": "x"}):
        with pytest.raises(control.SettingsError):
            control.save_settings(bad, cfg)
    assert json.loads(cfg.read_text(encoding="utf-8"))["fps"] == 30


def test_regenerate_token(tmp_path):
    cfg = tmp_path / "config.json"
    old = control.read_settings(cfg)["token"]
    new = control.regenerate_token(cfg)
    assert new != old and control.read_settings(cfg)["token"] == new


def test_server_args(tmp_path):
    args = control.server_args({"encoder": "h264_nvenc", "output_idx": 2}, tmp_path / "c.json")
    assert args == ["--config", str(tmp_path / "c.json"), "--encoder", "h264_nvenc", "--output-idx", "2", "--control-stdin"]


def test_console_python(tmp_path):
    (tmp_path / "python.exe").write_text("")
    assert control.console_python(str(tmp_path / "pythonw.exe")) == str(tmp_path / "python.exe")
    assert control.console_python("/usr/bin/python3") == "/usr/bin/python3"


def test_autostart_xml():
    """72 時間で止めない、バッテリーでも動かす、最上位の特権、の設定が入る。"""
    import xml.etree.ElementTree as ET

    from pathlib import Path

    xml = control.autostart_xml("PC\\user<&>", Path("C:/duo remote/server/panel.bat"))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(xml.split("?>", 1)[1])  # 先頭の UTF-16 の宣言は、文字列のままでは読めないので外す
    assert root.find("t:Settings/t:ExecutionTimeLimit", ns).text == "PT0S"
    assert root.find("t:Settings/t:DisallowStartIfOnBatteries", ns).text == "false"
    assert root.find("t:Settings/t:StopIfGoingOnBatteries", ns).text == "false"
    assert root.find("t:Principals/t:Principal/t:RunLevel", ns).text == "HighestAvailable"
    assert root.find("t:Principals/t:Principal/t:UserId", ns).text == "PC\\user<&>"
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", ns).text == "PC\\user<&>"
    exe = root.find("t:Actions/t:Exec", ns)
    assert exe.find("t:Command", ns).text.endswith("panel.bat")
    assert exe.find("t:Arguments", ns).text == "--autostart"


def test_address_details(monkeypatch):
    monkeypatch.setattr(control, "_details_cache", None)
    monkeypatch.setattr(control, "local_addresses", lambda: ["10.242.22.4", "192.168.0.200", "100.101.102.103"])
    monkeypatch.setattr(control, "_primary_address", lambda: "192.168.0.200")
    monkeypatch.setattr(control, "_interface_names", lambda: {"10.242.22.4": "ZeroTier One", "192.168.0.200": "Wi-Fi"})
    monkeypatch.setattr(control.sys, "platform", "win32")
    details = control.address_details()
    assert details[0] == {"address": "192.168.0.200", "name": "Wi-Fi", "recommended": True, "tailscale": False}
    assert {"address": "100.101.102.103", "name": "", "recommended": False, "tailscale": True} in details
    assert details[1]["name"] == "ZeroTier One"


def test_server_process_stops_on_stdin(tmp_path):
    """標準入力の stop で、後片付けをしてから止まる（本物の duo_server の代わりに、同じ決まりで動く小さな程序を使う）。"""
    script = tmp_path / "fake.py"
    script.write_text(
        "import sys\n"
        "print('起動しました', flush=True)\n"
        "for line in sys.stdin:\n"
        "    if line.strip() == 'stop':\n"
        "        break\n"
        "print('後片付けしました', flush=True)\n",
        encoding="utf-8",
    )

    class Fake(control.ServerProcess):
        def command(self, args):
            return [sys.executable, str(script)]

    proc = Fake(cwd=tmp_path)
    proc.start([])
    deadline = time.time() + 5
    while time.time() < deadline and "起動しました" not in proc.log_since(0)[1]:
        time.sleep(0.05)
    assert proc.running
    proc.stop()
    deadline = time.time() + 5
    while time.time() < deadline and proc.last_exit is None:
        time.sleep(0.05)
    seq, lines = proc.log_since(0)
    assert "後片付けしました" in lines
    assert not proc.running and proc.last_exit == 0
    assert proc.log_since(seq) == (seq, [])


def test_wait_for_stop_reads_stdin(monkeypatch):
    import asyncio
    import io

    from duo_server import __main__ as main_mod

    monkeypatch.setattr(main_mod.sys, "stdin", io.StringIO("hello\nstop\n"))
    asyncio.run(asyncio.wait_for(main_mod.wait_for_stop(True), 2))
    monkeypatch.setattr(main_mod.sys, "stdin", io.StringIO(""))  # 閉じたときも止まる
    asyncio.run(asyncio.wait_for(main_mod.wait_for_stop(True), 2))
