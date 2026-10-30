import subprocess

import pytest

from duo_server.capture import (
    AccessUnitSplitter,
    build_ffmpeg_cmd,
    capture_target,
    is_keyframe,
    output_size,
)

AUD = b"\x00\x00\x00\x01\x09\xf0"
IDR = b"\x00\x00\x00\x01\x65" + b"\x11" * 20
SPS_PPS = b"\x00\x00\x00\x01\x67\x42\x00\x1f" + b"\x00\x00\x00\x01\x68\xce\x3c\x80"
P = b"\x00\x00\x01\x41" + b"\x22" * 15  # 3バイトの開始コード

AU_KEY = AUD + SPS_PPS + IDR
AU_P = AUD + P


def test_is_keyframe():
    assert is_keyframe(AU_KEY)
    assert not is_keyframe(AU_P)


@pytest.mark.parametrize("chunk", [1, 3, 7, 64, 10_000])
def test_splitter_any_chunk_size(chunk):
    stream = AU_KEY + AU_P + AU_P + AU_KEY + AU_P
    sp = AccessUnitSplitter()
    got = []
    for i in range(0, len(stream), chunk):
        got += sp.feed(stream[i : i + chunk])
    # 最後の1つは、次のAUDが来るまで確定しない
    assert got == [AU_KEY, AU_P, AU_P, AU_KEY]
    assert sp.feed(AUD) == [AU_P]


def test_splitter_drops_leading_garbage():
    sp = AccessUnitSplitter()
    got = sp.feed(b"\xff\xfe\x00\x01" + AU_KEY + AU_P)
    assert got == [AU_KEY]


def test_output_size():
    assert output_size(1920, 1080, 0) == (1920, 1080)
    assert output_size(2560, 1440, 720) == (1280, 720)
    assert output_size(1920, 1080, 1440) == (1920, 1080)  # 拡大はしない
    assert output_size(1921, 1081, 0) == (1920, 1080)  # 偶数にそろえる


def test_ffmpeg_cmd_nvenc_no_scale_stays_on_gpu():
    cmd = build_ffmpeg_cmd(encoder="h264_nvenc", fps=60, src_height=1080)
    graph = cmd[cmd.index("-i") + 1]
    assert "ddagrab" in graph and "hwdownload" not in graph
    assert "h264_nvenc" in cmd and "-aud" in cmd
    assert cmd[cmd.index("-zerolatency") + 1] == "1" and cmd[cmd.index("-delay") + 1] == "0"


def test_ffmpeg_cmd_output_idx():
    cmd = build_ffmpeg_cmd(encoder="h264_nvenc", output_idx=1)
    assert "ddagrab=output_idx=1:" in cmd[cmd.index("-i") + 1]


def test_ffmpeg_cmd_region():
    cmd = build_ffmpeg_cmd(encoder="h264_nvenc", region=(100, 50, 800, 600), src_height=600)
    graph = cmd[cmd.index("-i") + 1]
    assert ":offset_x=100:offset_y=50:video_size=800x600" in graph
    full = build_ffmpeg_cmd(encoder="h264_nvenc")
    assert "offset_x" not in full[full.index("-i") + 1]


def test_ffmpeg_cmd_scale_and_x264():
    cmd = build_ffmpeg_cmd(encoder="libx264", max_height=720, src_height=1440)
    graph = cmd[cmd.index("-i") + 1]
    assert "scale=-2:720" in graph and graph.endswith("format=yuv420p")
    assert "aud=1" in cmd[cmd.index("-x264-params") + 1]


def _real_x264_stream():
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run(
        [
            exe, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30", "-t", "2",
            "-c:v", "libx264", "-preset", "ultrafast", "-bf", "0", "-g", "15",
            "-x264-params", "aud=1:repeat-headers=1",
            "-bsf:v", "dump_extra=freq=keyframe", "-f", "h264", "pipe:1",
        ],
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode()
    return r.stdout


def test_splitter_with_real_x264_output():
    data = _real_x264_stream()
    sp = AccessUnitSplitter()
    units = []
    for i in range(0, len(data), 4096):
        units += sp.feed(data[i : i + 4096])
    units += sp.feed(b"\x00\x00\x00\x01\x09\xf0")  # 最後のフレームを確定させる
    assert len(units) == 60  # 30fps x 2秒
    keys = [i for i, u in enumerate(units) if is_keyframe(u)]
    assert keys == [0, 15, 30, 45]
    # キーフレームの先頭にSPS(7)とPPS(8)が付く
    from duo_server.capture import iter_nals

    types = [t for _, t in iter_nals(units[0])]
    assert types[:3] == [9, 7, 8] and 5 in types


def test_capture_target():
    primary_window = (100, 50, 800, 600)
    assert capture_target(None, None) == (0, None)
    assert capture_target(None, primary_window, default_output=2) == (2, primary_window)
    virtual = {"output": 1, "rect": (3840, 0, 1536, 1080)}
    assert capture_target(virtual, None) == (1, None)  # モニター全体
    # 仮想モニター上のウィンドウ: そのモニターの左上が基準になる
    assert capture_target(virtual, (3940, 40, 800, 600)) == (1, (100, 40, 800, 600))
    # 番号が分からないモニターは、主モニターの番号で撮る
    assert capture_target({"output": None, "rect": (0, 0, 1, 1)}, None, default_output=3) == (3, None)
