"""画面の取得と動画への圧縮。

ffmpegを子プロセスとして動かし、標準出力に出るH.264かH.265（HEVC）（Annex B形式）を
フレーム単位に切り分けて取り出す。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from collections import deque
from typing import AsyncIterator

log = logging.getLogger(__name__)

_START3 = b"\x00\x00\x01"
NAL_IDR = 5
NAL_AUD = 9
# H.265（HEVC）の NAL タイプ
HEVC_AUD = 35
HEVC_KEY = (19, 20, 21)  # IDR_W_RADL、IDR_N_LP、CRA
HEVC_PARAMETER_SETS = (32, 33, 34)  # VPS、SPS、PPS

CODECS = ("h264", "hevc")


def iter_nals(data: bytes, codec: str = "h264"):
    """(NALヘッダーの位置, NALタイプ) を順に返す。HEVC のタイプは、ヘッダーの1バイト目の 1〜6 ビット目。"""
    hevc = codec == "hevc"
    i = 0
    while True:
        j = data.find(_START3, i)
        if j == -1 or j + 3 >= len(data):
            return
        header = data[j + 3]
        yield j + 3, (header >> 1) & 0x3F if hevc else header & 0x1F
        i = j + 3


def is_keyframe(access_unit: bytes, codec: str = "h264") -> bool:
    keys = HEVC_KEY if codec == "hevc" else (NAL_IDR,)
    return any(t in keys for _, t in iter_nals(access_unit, codec))


class AccessUnitSplitter:
    """H.264 か H.265 のバイト列を、AUD（H.264 は NALタイプ9、H.265 は35）を目印に1フレームずつに切り分ける。"""

    def __init__(self, max_buffer: int = 16 * 1024 * 1024, codec: str = "h264") -> None:
        self._buf = b""
        self._max = max_buffer
        self._codec = codec
        self._aud = HEVC_AUD if codec == "hevc" else NAL_AUD

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        buf = self._buf
        starts: list[int] = []
        for off, nal_type in iter_nals(buf, self._codec):
            if nal_type == self._aud:
                s = off - 3
                if s > 0 and buf[s - 1] == 0:  # 4バイトの開始コード
                    s -= 1
                starts.append(s)
        if not starts:
            if len(buf) > self._max:
                self._buf = b""
            return []
        units = [buf[a:b] for a, b in zip(starts, starts[1:])]
        self._buf = buf[starts[-1]:]
        return units


def capture_target(
    monitor: dict | None, region: tuple[int, int, int, int] | None, default_output: int = 0
) -> tuple[int, tuple[int, int, int, int] | None]:
    """ddagrab に渡す (モニターの番号, 撮る範囲) を決める。
    monitor が None なら主モニター（番号は default_output）。それ以外は、調べた DXGI の番号を使う。
    region は、デスクトップ上の位置。ddagrab の範囲は、そのモニターの左上が基準なので、引いて直す。"""
    if monitor is not None and monitor.get("output") is not None:
        output_idx, (ox, oy) = monitor["output"], monitor["rect"][:2]
    else:
        output_idx, (ox, oy) = default_output, (0, 0)
    if region is None:
        return output_idx, None
    return output_idx, (region[0] - ox, region[1] - oy, region[2], region[3])


def output_size(src_w: int, src_h: int, max_height: int) -> tuple[int, int]:
    """送る映像の大きさ。max_heightが0なら元の大きさのまま（偶数にそろえる）。"""
    if max_height and src_h > max_height:
        h = max_height - (max_height % 2)
        w = round(src_w * h / src_h)
    else:
        w, h = src_w, src_h
    return w - (w % 2), h - (h % 2)


def build_ffmpeg_cmd(
    *,
    ffmpeg: str = "ffmpeg",
    fps: int = 60,
    bitrate_kbps: int = 8000,
    encoder: str = "h264_nvenc",
    output_idx: int = 0,
    max_height: int = 0,
    src_height: int = 0,
    region: tuple[int, int, int, int] | None = None,
    window: int | None = None,
    codec: str = "h264",
    short_gop: bool = False,
) -> list[str]:
    """画面を撮ってH.264にするffmpegのコマンドを作る。
    short_gop が True なら、キーフレームの間隔を短くする（約0.5秒。UDP で送るとき。docs/protocol.md の「UDP での映像と音」）。
    regionは撮る範囲（モニターの左上からの x, y, 幅, 高さ。画素）。Noneなら画面全体。
    windowを渡すと、画面を切り抜くのではなく、そのウィンドウだけを撮る（Windows Graphics Capture。
    ほかのウィンドウが上に重なっても映らない）。このとき output_idx と region は使わない。
    codecが "hevc" なら H.265 にする（NVENC のときだけ。同じ画質で、通信量が H.264 の半分くらい）。"""
    hevc = codec == "hevc" and encoder == "h264_nvenc"
    gop = max(fps // 2, 1) if short_gop else max(fps * 2, 1)
    gop_sec = "0.5" if short_gop else "2"
    if window is not None:
        # width=-2、height=-2: 幅と高さを偶数に切り下げる（圧縮に偶数が要る）
        grab = f"gfxcapture=hwnd={window}:max_framerate={fps}:capture_cursor=1:width=-2:height=-2"
    else:
        grab = f"ddagrab=output_idx={output_idx}:framerate={fps}:draw_mouse=1"
        if region is not None:
            x, y, w, h = region
            grab += f":offset_x={x}:offset_y={y}:video_size={w}x{h}"
    scale = bool(max_height and src_height and src_height > max_height)

    # ウィンドウだけを撮るときは、中身が変わったときにしか映像が来ない（ffmpeg 8.1）。
    # 枚数でキーフレームを入れると、止まったウィンドウでは何分も来ないので、時間で入れる（前から2秒たった次の1枚）
    keyframes = ["-force_key_frames", f"expr:gte(t,n_forced*{gop_sec})"] if window is not None else []

    if encoder == "h264_nvenc":
        # 拡大縮小がなければ、GPUのまま圧縮に渡す
        graph = grab
        if scale:
            h = max_height - (max_height % 2)
            graph += f",hwdownload,format=bgra,scale=-2:{h}:flags=fast_bilinear,format=nv12"
        enc = [
            "-c:v", "hevc_nvenc" if hevc else "h264_nvenc", "-preset", "p1", "-tune", "ll",
            # フレームを溜めずに、すぐ出す
            "-delay", "0", "-zerolatency", "1",
            "-rc", "cbr", "-b:v", f"{bitrate_kbps}k",
            "-bf", "0", "-g", str(gop), "-forced-idr", "1", "-aud", "1",
        ]
    else:
        graph = f"{grab},hwdownload,format=bgra"
        if scale:
            h = max_height - (max_height % 2)
            graph += f",scale=-2:{h}:flags=fast_bilinear"
        graph += ",format=yuv420p"
        enc = [
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", f"{bitrate_kbps}k", "-bf", "0", "-g", str(gop),
            "-x264-params", "aud=1:repeat-headers=1",
        ]

    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-f", "lavfi", "-i", graph,
        *enc,
        *keyframes,
        "-bsf:v", "dump_extra=freq=keyframe",
        "-f", "hevc" if hevc else "h264", "pipe:1",
    ]


def detect_encoder(ffmpeg: str = "ffmpeg") -> str:
    """ffmpegがNVENCを使えるか調べる。使えなければlibx264にする。"""
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "libx264"
    return "h264_nvenc" if "h264_nvenc" in out else "libx264"


def detect_hevc(ffmpeg: str = "ffmpeg") -> bool:
    """ffmpeg に、NVENC の H.265（hevc_nvenc）があるか調べる。CPU の H.265（libx265）は、その場で圧縮するには重いので使わない。"""
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "hevc_nvenc" in out


def detect_window_capture(ffmpeg: str = "ffmpeg") -> bool:
    """ffmpeg に、ウィンドウだけを撮る gfxcapture（Windows Graphics Capture。ffmpeg 8.1 以降）があるか調べる。"""
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == ["gfxcapture"] for line in out.splitlines())


class StreamError(RuntimeError):
    pass


class FfmpegStream:
    """ffmpegを動かし、フレームを (キーフレームか, データ) の形で順に返す。"""

    def __init__(self, cmd: list[str], codec: str = "h264") -> None:
        self._cmd = cmd
        self._codec = codec
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        log.info("ffmpeg を起動します: %s", " ".join(self._cmd))
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            raise StreamError(
                f"ffmpeg を起動できません（{e}）。ffmpegを入れて、PATHに通すか --ffmpeg でパスを指定してください"
            ) from e
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            self._stderr_tail.append(text)
            log.warning("ffmpeg: %s", text)

    async def frames(self) -> AsyncIterator[tuple[bool, bytes]]:
        assert self._proc and self._proc.stdout
        splitter = AccessUnitSplitter(codec=self._codec)
        while True:
            chunk = await self._proc.stdout.read(65536)
            if not chunk:
                break
            for unit in splitter.feed(chunk):
                yield is_keyframe(unit, self._codec), unit
        code = await self._proc.wait()
        tail = "\n".join(self._stderr_tail)
        raise StreamError(f"ffmpeg が終了しました（コード {code}）\n{tail}")

    async def stop(self) -> None:
        proc = self._proc
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if self._stderr_task:
            self._stderr_task.cancel()
