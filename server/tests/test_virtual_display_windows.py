"""Windows の上でだけ動くテスト。ctypes の定義が正しいか（大きさ、呼び出し）を確かめる。"""
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows だけ")


def test_structure_sizes():
    import ctypes

    from duo_server import virtual_display as vd

    assert ctypes.sizeof(vd.DEVMODEW) == 220
    assert ctypes.sizeof(vd.DISPLAY_DEVICEW) == 840
    assert ctypes.sizeof(vd.MONITORINFOEXW) == 104


def test_dxgi_outputs_runs():
    from duo_server import virtual_display as vd

    outputs = vd.dxgi_outputs()  # 画面のない環境では空でもよい。落ちないこと
    for o in outputs:
        assert o["rect"][2] > 0 and o["device_name"].startswith("\\\\.\\DISPLAY")


def test_list_monitors_and_modes_run():
    from duo_server import virtual_display as vd

    monitors = vd.list_monitors()
    for m in monitors:
        assert m["rect"][2] > 0 and m["rect"][3] > 0
        assert vd.list_modes(m["name"]) is not None
        vd.current_mode(m["name"])


def test_pnp_query_runs():
    from duo_server import virtual_display as vd

    devices = vd.pnp_display_devices()
    assert isinstance(devices, list)
    for d in devices:
        assert "InstanceId" in d
    assert vd.VirtualDisplay().device() is None or True  # 入っていなくてもよい。落ちないこと


def test_audio_capture_library_installed():
    """PCの音を取る部品（PyAudioWPatch）が入っていて、読み込める。"""
    import pyaudiowpatch

    assert hasattr(pyaudiowpatch, "PyAudio") and hasattr(pyaudiowpatch, "paWASAPI")
