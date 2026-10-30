import asyncio
import json
import math
import struct

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from duo_server import protocol
from duo_server.audio import AudioError, AudioStream, build_audio_ffmpeg_cmd, chunk_bytes
from duo_server.input_win import NullInput
from duo_server.server import Settings, make_handler

TOKEN = "t"


def test_pack_audio_flag():
    data = protocol.pack_audio(b"\x01\x02", 1234)
    assert protocol.is_audio(data) and data[5:] == b"\x01\x02"
    video = protocol.pack_frame(b"x", True, 1)
    assert not protocol.is_audio(video)


def test_chunk_bytes_aligned():
    assert chunk_bytes(48000, 2) == 960 * 4  # 20ms
    assert chunk_bytes(24000, 1) == 480 * 2
    assert chunk_bytes(44100, 2) % 4 == 0


def test_ffmpeg_cmd():
    cmd = build_audio_ffmpeg_cmd(in_rate=44100, in_channels=8, rate=48000, channels=2)
    i = cmd.index("-i")
    before, after = cmd[:i], cmd[i:]
    assert before[before.index("-ar") + 1] == "44100" and before[before.index("-ac") + 1] == "8"
    assert after[after.index("-ar") + 1] == "48000" and after[after.index("-ac") + 1] == "2"
    assert cmd[-1] == "pipe:1" and cmd[cmd.index("-f", i) + 1] == "s16le"
    assert "-probesize" in before and "-nobuffer" not in cmd and "nobuffer" not in cmd


class FakeCapture:
    """44.1kHz モノラルの正弦波を、0.5秒ぶん出す、音を取る部品のかわり。"""

    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.stopped = False

    def start(self, on_data):
        if self.fail:
            raise AudioError(self.fail)
        import threading

        def run():
            n = 44100 // 50  # 20ms
            for k in range(25):
                samples = [int(8000 * math.sin(2 * math.pi * 440 * (k * n + i) / 44100)) for i in range(n)]
                on_data(struct.pack(f"<{n}h", *samples))

        threading.Thread(target=run, daemon=True).start()
        return 44100, 1

    def stop(self):
        self.stopped = True


def test_audio_stream_converts_with_real_ffmpeg():
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

    async def go():
        capture = FakeCapture()
        stream = AudioStream(imageio_ffmpeg.get_ffmpeg_exe(), 48000, 2, capture_factory=lambda: capture)
        await stream.start()
        chunks = []
        try:
            async def collect():
                async for chunk in stream.frames():
                    chunks.append(chunk)

            await asyncio.wait_for(collect(), 2)
        except asyncio.TimeoutError:
            pass
        finally:
            await stream.stop()
        return chunks, capture

    chunks, capture = asyncio.run(go())
    assert capture.stopped
    assert chunks and all(len(c) == chunk_bytes(48000, 2) for c in chunks)
    # 44.1kHz モノラル 0.5秒 → 48kHz ステレオ。20ms のかたまりで、20個前後
    assert 15 <= len(chunks) <= 26
    # 音が入っている（無音ではない）
    peak = max(abs(v) for v in struct.unpack(f"<{len(chunks[5]) // 2}h", chunks[5]))
    assert peak > 1000


def test_audio_stream_reports_capture_failure():
    async def go():
        stream = AudioStream("ffmpeg", 48000, 2, capture_factory=lambda: FakeCapture(fail="音を取れません"))
        with pytest.raises(AudioError, match="音を取れません"):
            await stream.start()

    asyncio.run(go())


# --- サーバーとの通信 ---------------------------------------------------------

AUD = b"\x00\x00\x00\x01\x09\xf0"


class FakeVideo:
    async def start(self):
        pass

    async def frames(self):
        yield True, AUD + b"KEY"
        await asyncio.sleep(3600)

    async def stop(self):
        pass


class FakeAudio:
    def __init__(self, fail=None):
        self.fail, self.stopped, self.started = fail, False, False

    async def start(self):
        if self.fail:
            raise AudioError(self.fail)
        self.started = True

    async def frames(self):
        for i in range(1000):
            yield bytes([i % 256]) * 8
            await asyncio.sleep(0.005)

    async def stop(self):
        self.stopped = True


async def with_server(body, audio_factory):
    handler = make_handler(Settings(token=TOKEN), NullInput(), lambda *a: FakeVideo(), audio_factory)
    async with serve(handler, "127.0.0.1", 0) as server:
        await body(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")


async def recv_json(ws, wanted):
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(raw, str) and json.loads(raw)["t"] == wanted:
            return json.loads(raw)


async def open_session(url):
    ws = await connect(url)
    await ws.send(json.dumps({"t": "hello", "v": protocol.VERSION, "token": TOKEN}))
    await recv_json(ws, "welcome")
    return ws


def test_audio_on_off_over_websocket():
    audios = []

    def factory(rate, channels):
        a = FakeAudio()
        a.rate, a.channels = rate, channels
        audios.append(a)
        return a

    async def body(url):
        ws = await open_session(url)
        async with ws:
            await ws.send(json.dumps({"t": "audio", "on": True, "rate": 24000, "channels": 1}))
            state = await recv_json(ws, "audio_state")
            assert state == {"t": "audio_state", "on": True, "rate": 24000, "channels": 1}
            frames = []
            while len(frames) < 3:
                raw = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(raw, bytes) and protocol.is_audio(raw):
                    frames.append(raw)
            assert all(len(f) == 5 + 8 for f in frames)
            await ws.send(json.dumps({"t": "audio", "on": False}))
            assert (await recv_json(ws, "audio_state"))["on"] is False
            assert audios[0].stopped
        await asyncio.sleep(0.1)

    asyncio.run(with_server(body, factory))
    assert (audios[0].rate, audios[0].channels) == (24000, 1)


def test_audio_stopped_when_client_disconnects():
    audios = []

    def factory(rate, channels):
        audios.append(FakeAudio())
        return audios[-1]

    async def body(url):
        ws = await open_session(url)
        async with ws:
            await ws.send(json.dumps({"t": "audio", "on": True}))
            await recv_json(ws, "audio_state")
        await asyncio.sleep(0.3)

    asyncio.run(with_server(body, factory))
    assert audios[0].stopped


def test_audio_errors_are_reported_and_video_continues():
    async def body(url):
        ws = await open_session(url)
        async with ws:
            await ws.send(json.dumps({"t": "audio", "on": True}))
            state = await recv_json(ws, "audio_state")
            assert state["on"] is False and "取れません" in state["reason"]
            await ws.send(json.dumps({"t": "audio", "on": True, "rate": 12345}))  # 対応していない形
            assert (await recv_json(ws, "audio_state"))["on"] is False
            await ws.send(json.dumps({"t": "ping", "n": 9}))  # 接続は続いている
            assert (await recv_json(ws, "pong"))["n"] == 9

    asyncio.run(with_server(body, lambda r, c: FakeAudio(fail="音を取れません")))


def test_audio_not_available_without_factory():
    async def body(url):
        ws = await open_session(url)
        async with ws:
            await ws.send(json.dumps({"t": "audio", "on": True}))
            state = await recv_json(ws, "audio_state")
            assert state["on"] is False and state["reason"]

    asyncio.run(with_server(body, None))
