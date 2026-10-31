"""PCが鳴らしている音を取って、iPhoneに送る形（PCM、16ビット、インターリーブ）にする。

- 音は、Windows の WASAPI のループバック（スピーカーに出る音を、そのまま取る仕組み）で取る
- 取った音は、ffmpeg で、送る形（周波数とチャンネル数）にそろえる。pcm のときは圧縮しない
- opus のときは、ffmpeg が Ogg に包んだ Opus を出す。その Ogg を読んで、Opus のパケットを1つずつ取り出して送る
- PCが無音のときは、ループバックからデータが来ない。そのときは、何も送らない
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque
from typing import AsyncIterator, Callable

log = logging.getLogger(__name__)

CHUNK_MS = 20
# 取った音の、ffmpeg に渡す前の待ち行列の上限（20msごと）。これを超えたら古いほうを捨てる
MAX_PENDING_CHUNKS = 50
SAMPLE_BYTES = 2  # 16ビット

# クライアントが選べる音の形
ALLOWED_RATES = (48000, 44100, 24000, 16000)
ALLOWED_CHANNELS = (1, 2)
ALLOWED_CODECS = ("pcm", "opus")
OPUS_RATE = 48000  # opus のときの周波数は、これだけ
OPUS_BITRATE = {1: "48k", 2: "96k"}


class AudioError(RuntimeError):
    """音を取れないときの理由（利用者に見せる文）。"""


def build_audio_ffmpeg_cmd(
    *, ffmpeg: str = "ffmpeg", in_rate: int, in_channels: int, rate: int, channels: int, codec: str = "pcm"
) -> list[str]:
    """取った音（in_*）を、送る形（rate、channels、codec）にする ffmpeg のコマンド。"""
    if codec == "opus":
        return [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-probesize", "32", "-analyzeduration", "0",
            "-f", "s16le", "-ar", str(in_rate), "-ac", str(in_channels), "-i", "pipe:0",
            "-ar", str(OPUS_RATE), "-ac", str(channels),
            "-c:a", "libopus", "-b:a", OPUS_BITRATE.get(channels, "96k"),
            "-application", "lowdelay", "-frame_duration", "20",
            # 1ページを小さく（20ミリ秒 = 1パケット）して、ffmpeg の中で音をためないようにする
            "-f", "ogg", "-page_duration", "20000", "-flush_packets", "1", "pipe:1",
        ]
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
        # 入力を調べる時間をなくす。これがないと、音を流し続けても、ffmpeg が出力を始めない。
        # （-fflags nobuffer -flags low_delay は、付けると出力が空になるので、付けない）
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "s16le", "-ar", str(in_rate), "-ac", str(in_channels), "-i", "pipe:0",
        "-ar", str(rate), "-ac", str(channels),
        "-f", "s16le", "-flush_packets", "1", "pipe:1",
    ]


def chunk_bytes(rate: int, channels: int, ms: int = CHUNK_MS) -> int:
    """ms ミリ秒ぶんの音のバイト数（1サンプルぶんの境目で切れるようにする）。"""
    return (rate * ms // 1000) * channels * SAMPLE_BYTES


class OggPacketReader:
    """Ogg のバイト列を少しずつ受け取り、中のパケットを1つずつ取り出す。

    Ogg のページは、"OggS"、版、種類、granule(8)、serial(4)、順番(4)、CRC(4)、segment 数(1)、
    segment table（segment ごとの長さ。255 なら次の segment に続く）、の順。
    1つのパケットは、255 未満の長さで終わる。ページの最後が 255 のときは、パケットが次のページに続く。
    "OggS" で始まらないデータは、次の "OggS" まで読み飛ばす。
    Opus は、最初の2つのパケット（OpusHead、OpusTags）が見出しなので、返さない。
    """

    HEADER_SIZE = 27

    def __init__(self, skip_opus_headers: bool = True) -> None:
        self._buf = bytearray()
        self._partial = bytearray()  # ページをまたいで続いているパケットの、ここまで
        self._skip = skip_opus_headers

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        packets: list[bytes] = []
        while True:
            start = self._buf.find(b"OggS")
            if start < 0:
                # "OggS" の途中で切れているかもしれないので、後ろ3バイトは残す
                del self._buf[: max(len(self._buf) - 3, 0)]
                break
            if start:
                del self._buf[:start]
            if len(self._buf) < self.HEADER_SIZE:
                break
            nseg = self._buf[26]
            head = self.HEADER_SIZE + nseg
            if len(self._buf) < head:
                break
            table = self._buf[self.HEADER_SIZE:head]
            total = head + sum(table)
            if len(self._buf) < total:
                break
            pos = head
            for lacing in table:
                self._partial += self._buf[pos:pos + lacing]
                pos += lacing
                if lacing < 255:
                    packet = bytes(self._partial)
                    self._partial.clear()
                    if self._skip and (packet.startswith(b"OpusHead") or packet.startswith(b"OpusTags")):
                        continue
                    packets.append(packet)
            del self._buf[:total]
        return packets


class LoopbackCapture:
    """WASAPI のループバックで、PCが鳴らしている音を取る（Windows だけ）。"""

    def __init__(self) -> None:
        self._pa = None
        self._stream = None

    def start(self, on_data: Callable[[bytes], None]) -> tuple[int, int]:
        """取り始める。on_data は、音のかたまりが取れるたびに、別のスレッドから呼ばれる。
        取っている音の (周波数, チャンネル数) を返す。"""
        if sys.platform != "win32":
            raise AudioError("PCの音を取る機能は、Windows でだけ使えます")
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as e:
            raise AudioError(
                "音を取る部品（PyAudioWPatch）が入っていません。start.bat をもう一度実行してください"
            ) from e

        pa = pyaudio.PyAudio()
        try:
            try:
                wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            except OSError as e:
                raise AudioError("この PC では、音を取る仕組み（WASAPI）が使えません") from e
            speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not speakers.get("isLoopbackDevice"):
                for loopback in pa.get_loopback_device_info_generator():
                    if speakers["name"] in loopback["name"]:
                        speakers = loopback
                        break
                else:
                    raise AudioError(f"スピーカー「{speakers['name']}」の音を取る入り口が見つかりません")
            rate = int(speakers["defaultSampleRate"])
            channels = int(speakers["maxInputChannels"])

            def callback(data, _frames, _time, _status):
                on_data(bytes(data))
                return None, pyaudio.paContinue

            self._stream = pa.open(
                format=pyaudio.paInt16, channels=channels, rate=rate,
                frames_per_buffer=rate * CHUNK_MS // 1000,
                input=True, input_device_index=speakers["index"], stream_callback=callback,
            )
        except AudioError:
            pa.terminate()
            raise
        except Exception as e:  # PortAudio のエラーは、種類が多い
            pa.terminate()
            raise AudioError(f"音を取り始められませんでした（{e}）") from e
        self._pa = pa
        log.info("音を取り始めました: %s（%dHz、%dチャンネル）", speakers["name"], rate, channels)
        return rate, channels

    def stop(self) -> None:
        stream, pa = self._stream, self._pa
        self._stream = self._pa = None
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        finally:
            if pa is not None:
                pa.terminate()


class AudioStream:
    """PCの音を、(rate, channels) の PCM にして、20msごとに返す。"""

    def __init__(
        self, ffmpeg: str, rate: int, channels: int,
        capture_factory: Callable[[], LoopbackCapture] = LoopbackCapture,
        codec: str = "pcm",
    ) -> None:
        self.codec = codec
        self.ffmpeg = ffmpeg
        self.rate = rate
        self.channels = channels
        self._capture = capture_factory()
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: deque[bytes] = deque()
        self._wake = asyncio.Event()
        self._writer: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._closed = False

    async def start(self) -> None:
        loop = asyncio.get_running_loop()

        def on_data(data: bytes) -> None:  # 取るスレッドから呼ばれる
            loop.call_soon_threadsafe(self._push, data)

        in_rate, in_channels = await asyncio.to_thread(self._capture.start, on_data)
        cmd = build_audio_ffmpeg_cmd(
            ffmpeg=self.ffmpeg, in_rate=in_rate, in_channels=in_channels,
            rate=self.rate, channels=self.channels, codec=self.codec,
        )
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            self._capture.stop()
            raise AudioError(f"ffmpeg を起動できません（{e}）") from e
        self._writer = asyncio.create_task(self._write())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    def _push(self, data: bytes) -> None:
        if self._closed:
            return
        self._pending.append(data)
        while len(self._pending) > MAX_PENDING_CHUNKS:
            self._pending.popleft()  # 追いつけないときは、古い音を捨てる
        self._wake.set()

    async def _write(self) -> None:
        assert self._proc and self._proc.stdin
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while self._pending:
                    self._proc.stdin.write(self._pending.popleft())
                await self._proc.stdin.drain()
        except (ConnectionError, BrokenPipeError):
            pass  # ffmpeg が止まった。読む側が気づく

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while line := await self._proc.stderr.readline():
            log.warning("ffmpeg（音）: %s", line.decode(errors="replace").rstrip())

    async def frames(self) -> AsyncIterator[bytes]:
        assert self._proc and self._proc.stdout
        if self.codec == "opus":
            reader = OggPacketReader()
            while True:
                data = await self._proc.stdout.read(65536)
                if not data:
                    raise AudioError("音の変換（ffmpeg）が止まりました")
                for packet in reader.feed(data):
                    yield packet
        size = chunk_bytes(self.rate, self.channels)
        buf = b""
        while True:
            data = await self._proc.stdout.read(65536)
            if not data:
                raise AudioError("音の変換（ffmpeg）が止まりました")
            buf += data
            while len(buf) >= size:
                yield buf[:size]
                buf = buf[size:]

    async def stop(self) -> None:
        self._closed = True
        await asyncio.to_thread(self._capture.stop)
        for task in (self._writer, self._stderr_task):
            if task:
                task.cancel()
        proc = self._proc
        if proc and proc.returncode is None:
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except Exception:
                pass
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
