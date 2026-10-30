import json

import pytest

from duo_server import virtual_display as vd


def test_matches_driver_names():
    assert vd.matches("Virtual Display Driver")
    assert vd.matches("ROOT\\DISPLAY\\0000 / Root\\MttVDD")
    assert not vd.matches("NVIDIA GeForce RTX 3070 Ti / Generic PnP Monitor")


MODES = [(720, 1080, 60), (1080, 1080, 60), (1440, 1080, 60), (1536, 1080, 60), (1920, 1080, 60), (2560, 1440, 120)]


def test_choose_mode_nearest_aspect():
    assert vd.choose_mode(MODES, 1536 / 1080) == (1536, 1080, 60)
    assert vd.choose_mode(MODES, 4 / 3) == (1440, 1080, 60)
    assert vd.choose_mode(MODES, 0.66) == (720, 1080, 60)  # 縦長
    assert vd.choose_mode(MODES, 16 / 9) == (1920, 1080, 60)  # 2560x1440 も 16:9 だが、高さが 1080 に近いほう


def test_choose_mode_prefers_height_then_refresh_rate():
    modes = [(1920, 1080, 144), (1920, 1080, 60), (3840, 2160, 60)]
    assert vd.choose_mode(modes, 16 / 9) == (1920, 1080, 60)


def test_choose_mode_empty():
    assert vd.choose_mode([], 1.5) is None
    assert vd.choose_mode(MODES, 0) is None


def test_build_vdd_settings():
    xml = vd.build_vdd_settings("NVIDIA GeForce RTX 3070 Ti")
    assert "<friendlyname>NVIDIA GeForce RTX 3070 Ti</friendlyname>" in xml
    assert "<count>1</count>" in xml
    assert xml.count("<resolution>") == len(vd.PRESET_WIDTHS)
    assert "<width>1536</width>" in xml and "<height>1080</height>" in xml
    assert "<friendlyname>default</friendlyname>" in vd.build_vdd_settings()
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml.split("?>", 1)[1])  # XML として読める
    assert root.tag == "vdd_settings"


def test_parse_pnp_json_single_and_list():
    one = json.dumps({"InstanceId": "ROOT\\DISPLAY\\0000", "FriendlyName": "Virtual Display Driver", "Status": "OK"})
    assert vd.parse_pnp_json(one)[0]["Status"] == "OK"
    assert len(vd.parse_pnp_json(json.dumps([{"a": 1}, {"a": 2}]))) == 2
    assert vd.parse_pnp_json("  ") == []


def test_monitor_of_picks_largest_overlap():
    monitors = [
        {"name": "main", "rect": (0, 0, 3840, 1080)},
        {"name": "virtual", "rect": (3840, 0, 1536, 1080)},
    ]
    assert vd.monitor_of((100, 100, 800, 600), monitors)["name"] == "main"
    assert vd.monitor_of((4000, 100, 800, 600), monitors)["name"] == "virtual"
    assert vd.monitor_of((3200, 100, 800, 600), monitors)["name"] == "main"  # 重なりが多いほう
    assert vd.monitor_of((3600, 100, 800, 600), monitors)["name"] == "virtual"
    assert vd.monitor_of((-5000, 0, 100, 100), monitors) is None


@pytest.mark.skipif(vd.sys.platform == "win32", reason="Windows 以外での動きを見る")
def test_not_windows_raises_clear_error():
    with pytest.raises(vd.VirtualDisplayError, match="Windows"):
        vd.list_monitors()
