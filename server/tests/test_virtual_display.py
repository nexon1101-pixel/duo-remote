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


def test_presets_fit_iphone_duo_screens():
    """iPhone Duo の内側 1878x2670px、外側 1398x2034px の縦横比に、ほぼ合う解像度を選べる。"""
    modes = [(w, vd.TARGET_HEIGHT, 60) for w in vd.PRESET_WIDTHS]
    for width_px, height_px in ((1878, 2670), (1398, 2034)):
        for aspect in (width_px / height_px, height_px / width_px):  # 縦持ちと横持ち
            w, h, _ = vd.choose_mode(modes, aspect)
            assert abs(w / h - aspect) / aspect < 0.01  # 1%以内


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


def _written_modes(xml: str) -> list[tuple[int, int]]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml.split("?>", 1)[1])
    return [(int(r.findtext("width")), int(r.findtext("height"))) for r in root.iter("resolution")]


def test_build_vdd_settings_writes_four_heights():
    modes = _written_modes(vd.build_vdd_settings())
    assert {h for _, h in modes} == {1080, 1350, 1620, 2160}
    assert len(modes) == len(set(modes))  # 重複なし
    assert len(modes) <= 4 * len(vd.PRESET_WIDTHS)
    assert all(w % 2 == 0 for w, _ in modes)
    # 高さ1080の幅は、いままでと同じ
    assert [w for w, h in modes if h == 1080] == list(vd.PRESET_WIDTHS)
    # 高さごとに、1080 の幅 × H / 1080
    assert (950, 1350) in modes and (1140, 1620) in modes
    assert (3840, 2160) in modes and (1440, 2160) in modes


def test_build_vdd_settings_custom_heights():
    modes = _written_modes(vd.build_vdd_settings(heights=(1080,)))
    assert modes == [(w, 1080) for w in vd.PRESET_WIDTHS]


def _all_modes():
    return [(w, h, 60) for w, h in _written_modes(vd.build_vdd_settings())]


def test_choose_mode_picks_height_nearest_target_for_same_aspect():
    modes = [(760, 1080, 60), (950, 1350, 60), (1140, 1620, 60), (1520, 2160, 60)]
    for target, expected in ((1080, 1080), (1350, 1350), (1620, 1620), (2160, 2160), (1500, 1620)):
        assert vd.choose_mode(modes, 760 / 1080, target)[1] == expected


def test_choose_mode_keeps_1080_without_large_heights():
    # 古い設定ファイル（高さ1080だけ）では、大きい高さを望んでも 1080 のまま
    old = [(w, 1080, 60) for w in vd.PRESET_WIDTHS]
    for aspect in (760 / 1080, 1536 / 1080, 16 / 9):
        assert vd.choose_mode(old, aspect, 2160)[1] == 1080


def test_choose_mode_scaled_with_new_settings():
    modes = _all_modes()
    for aspect in (1878 / 2670, 2670 / 1878, 1398 / 2034, 2034 / 1398, 16 / 9):
        for scale in (1.0, 1.25, 1.5, 2.0):
            w, h, _ = vd.choose_mode(modes, aspect, round(vd.TARGET_HEIGHT * scale))
            assert h == round(1080 * scale)
            assert abs(w / h - aspect) / aspect < 0.01


def test_clamp_scale():
    assert vd.clamp_scale(1.5) == 1.5
    assert vd.clamp_scale(5.0) == 2.0
    assert vd.clamp_scale(0.2) == 1.0
    for bad in ("1.5", None, float("nan"), float("inf"), True, [1]):
        assert vd.clamp_scale(bad) == 1.0
