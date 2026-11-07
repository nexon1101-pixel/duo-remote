"""ヘッドレスクライアント（client/duo_client.py）の結合テスト。

本物のサーバー（duo_server）を疑似バックエンドで立て、クライアントを別プロセスで
起動して、映像のスナップショットと操作の往復を確かめる。映像は、ffmpeg で作った
本物の H.264 を疑似ストリームとして流す。
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from duo_server.input_win import NullInput  # noqa: E402
from duo_server.server import Settings, make_handler  # noqa: E402

TOKEN = "<MASKED_SECRET_001>"
CLIENT = Path(__file__).resolve().parent / "duo_client.py"


def make_units(tmp: Path) -> list[tuple[bool, bytes]]:
    """testsrc2 を 30 フレームだけ H.264 にして、アクセスユニット（AU）に割る。"""
    raw = tmp / "stream.h264"
    ffmpeg = shutil.which("ffmpeg")
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10",
         "-frames:v", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-x264-params", "aud=1:keyint=250", "-f", "h264", str(raw)],
        check=True,
    )
    data = raw.read_bytes()
    # AUD（NAL 種別 9）ごとに AU に割る。キーフレームは IDR（種別 5）を含む AU
    starts = [m.start() for m in re.finditer(rb"\x00\x00\x00\x01", data)]
    nals = [(pos, data[pos + 4] & 0x1F) for pos in starts]
    units: list[tuple[bool, bytes]] = []
    bounds = [pos for pos, nal_type in nals if nal_type == 9]
    for i, begin in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else len(data)
        au = data[begin:end]
        units.append((b"\x00\x00\x00\x01\x65" in au or b"\x00\x00\x01\x65" in au, au))
    assert len(units) == 30 and units[0][0]
    return units


class RealStream:
    """本物の H.264 の AU を順に流す疑似ストリーム。"""

    def __init__(self, units):
        self.units = units
        self.stopped = False

    async def start(self):
        pass

    async def frames(self):
        for key, au in self.units:
            yield key, au
            await asyncio.sleep(0.01)
        await asyncio.sleep(3600)

    async def stop(self):
        self.stopped = True


class Client:
    """クライアントのプロセス。JSON の行でやり取りする。"""

    def __init__(self, config: Path):
        self.proc = None
        self.config = config

    async def start(self) -> dict:
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, str(CLIENT), "--config", str(self.config),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return await self.read_reply()

    async def read_reply(self) -> dict:
        line = await asyncio.wait_for(self.proc.stdout.readline(), 15)
        assert line, "クライアントから返事がありません"
        return json.loads(line)

    async def cmd(self, **cmd) -> dict:
        self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
        await self.proc.stdin.drain()
        return await self.read_reply()

    async def quit(self) -> int:
        await self.cmd(cmd="quit")
        return await asyncio.wait_for(self.proc.wait(), 10)


async def run_scenario(tmp: Path) -> None:
    units = make_units(tmp)
    backend = NullInput((1920, 1080))

    def factory(settings, src, region, monitor=None, window=None):
        return RealStream(units)

    handler = make_handler(Settings(token=TOKEN), backend, factory)
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        config = tmp / "config.json"
        config.write_text(json.dumps({"host": "127.0.0.1", "port": port, "token": TOKEN}))

        # 操作側
        client = Client(config)
        ready = await client.start()
        assert ready["ready"] and ready["role"] == "control" and not ready["demoted"]

        assert (await client.cmd(cmd="ping"))["ok"]
        assert (await client.cmd(cmd="windows"))["list"] == []

        shot = await client.cmd(cmd="shot", path=str(tmp / "shot.png"))
        assert shot["ok"] and (shot["w"], shot["h"]) == (320, 180)
        assert Path(shot["path"]).stat().st_size > 1000

        await client.cmd(cmd="click", x=0.25, y=0.5)
        await client.cmd(cmd="key", vk=0x0D)
        await client.cmd(cmd="text", s="ab")
        await client.cmd(cmd="ping")  # ここまでの操作が届いたことの区切りに使う
        assert backend.calls == [
            ("button", "left", True, 0.25, 0.5),
            ("button", "left", False, None, None),
            ("key", 0x0D, True),
            ("key", 0x0D, False),
            ("text", "ab"),
        ]

        # 2つ目は視聴に降格され、操作は届かない
        viewer = Client(config)
        ready2 = await viewer.start()
        assert ready2["ready"] and ready2["role"] == "view" and ready2["demoted"]
        shot2 = await viewer.cmd(cmd="shot", path=str(tmp / "shot2.png"))
        assert shot2["ok"] and (shot2["w"], shot2["h"]) == (320, 180)
        calls_before = list(backend.calls)
        await viewer.cmd(cmd="click", x=0.1, y=0.1)
        await viewer.cmd(cmd="ping")
        assert backend.calls == calls_before
        assert await viewer.quit() == 0

        assert await client.quit() == 0


def test_client_end_to_end(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg がありません")
    asyncio.run(run_scenario(tmp_path))
