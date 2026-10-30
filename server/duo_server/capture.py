"""画面の取得と動画への圧縮。

ffmpegを子プロセスとして動かし、標準出力に出るH.264（Annex B形式）を
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


def iter_nals(data: bytes):
    """(NALヘッダーの位置, NALタイプ) を順に返す。"""
    i = 0
    while True:
        j = data.find(_START3, i)
        if j == -1 or j + 3 >= len(data):
            return
        yield j + 3, data[j + 3] & 0x1F
        i = j + 3


def is_keyframe(access_unit: bytes) -> bool:
    return any(t == NAL_IDR for _, t in iter_nals(access_unit))


class AccessUnitSplitter:
    """H.264のバイト列を、AUD（NALタイプ9）を目印に1フレームずつに切り分ける。"""

    def __init__(self, max_buffer: int = 16 * 1024 * 1024) -> None:
        self._buf = b""
        self._max = max_buffer

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        buf = self._buf
        starts: list[int] = []
        for off, nal_type in iter_nals(buf):
            if nal_type == NAL_AUD:
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
) -> list[str]:
    """画面全体を撮ってH.264にするffmpegのコマンドを作る。"""
    gop = max(fps * 2, 1)
    grab = f"ddagrab=output_idx={output_idx}:framerate={fps}:draw_mouse=1"
    scale = bool(max_height and src_height and src_height > max_height)

    if encoder == "h264_nvenc":
        # 拡大縮小がなければ、GPUのまま圧縮に渡す
        graph = grab
        if scale:
            h = max_height - (max_height % 2)
            graph += f",hwdownload,format=bgra,scale=-2:{h}:flags=fast_bilinear,format=nv12"
        enc = [
            "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll",
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
        "-bsf:v", "dump_extra=freq=keyframe",
        "-f", "h264", "pipe:1",
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


class StreamError(RuntimeError):
    pass


class FfmpegStream:
    """ffmpegを動かし、フレームを (キーフレームか, データ) の形で順に返す。"""

    def __init__(self, cmd: list[str]) -> None:
        self._cmd = cmd
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
        splitter = AccessUnitSplitter()
        while True:
            chunk = await self._proc.stdout.read(65536)
            if not chunk:
                break
            for unit in splitter.feed(chunk):
                yield is_keyframe(unit), unit
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
