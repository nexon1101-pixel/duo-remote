#!/usr/bin/env python3
"""DuoRemote のヘッドレスクライアント。AI エージェントが、画面を見て操作するためのもの。

サーバー（Windows の duo_server）につなぎ、映像を受けて、頼まれたときに PNG の
スナップショットとして書き出す。操作（マウス・キー・文字）も送れる。

標準入出力が JSON の行ごとのやり取りになる（1行送ると、1行返る）。

  起動すると: {"ready": true, "role": "control", "w": 1920, "h": 1080, ...}
  コマンド:   {"cmd": "shot", "path": "/tmp/shot.png"}
              {"cmd": "click", "x": 0.5, "y": 0.5}
              {"cmd": "move", "x": 0.5, "y": 0.5} / {"cmd": "button", "b": "left", "d": "down"}
              {"cmd": "scroll", "dx": 0, "dy": -3}
              {"cmd": "key", "vk": 13}            （d を省略すると押して離す）
              {"cmd": "text", "s": "こんにちは"}
              {"cmd": "windows"} / {"cmd": "clip"} / {"cmd": "ping"} / {"cmd": "info"}
              {"cmd": "quit"}
  返事:       {"ok": true, ...}（"id" を付けると、そのまま返す）

トークンは引数に書かない。設定ファイル（既定 ~/.config/duo-client/config.json、
{"host": ..., "port": 8765, "token": ...}）か、環境変数 DUO_TOKEN から読む。
Tailscale 越しは --tailscale（HTTPS_PROXY のポートを 3130 に変えた CONNECT プロキシを使う）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

# 映像フレームの先頭（server/duo_server/protocol.py と同じ取り決め）
FRAME_HEADER = struct.Struct(">BI")
FLAG_KEYFRAME = 0x01
FLAG_AUDIO = 0x02
FLAG_FILE = 0x04

MAX_BUFFER_BYTES = 16 * 1024 * 1024  # キーフレームからの蓄えの上限


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("ffmpeg が見つかりません（PATH か imageio-ffmpeg が必要です）")


class FrameBuffer:
    """最後のキーフレームからの映像を蓄える。スナップショットは、ここを丸ごとデコードする。"""

    def __init__(self) -> None:
        self._units: list[bytes] = []
        self._bytes = 0
        self.last_frame_at = 0.0
        self.frames = 0

    def add(self, keyframe: bool, payload: bytes) -> None:
        if keyframe:
            self._units = [payload]
            self._bytes = len(payload)
        else:
            if not self._units:
                return  # キーフレームがまだ無い
            self._units.append(payload)
            self._bytes += len(payload)
            if self._bytes > MAX_BUFFER_BYTES:
                self._units = []
                self._bytes = 0
        self.last_frame_at = time.monotonic()
        self.frames += 1

    def data(self) -> bytes:
        return b"".join(self._units)

    def age_ms(self) -> int | None:
        if not self.last_frame_at:
            return None
        return int((time.monotonic() - self.last_frame_at) * 1000)


def decode_snapshot(ffmpeg: str, h264: bytes, out_path: str) -> tuple[int, int]:
    """H.264 の蓄えをデコードして、最後のフレームを PNG にする。(幅, 高さ) を返す。"""
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-f", "h264", "-i", "pipe:", "-update", "1", "-y", out_path],
        input=h264, capture_output=True, timeout=30,
    )
    path = Path(out_path)
    if proc.returncode != 0 or not path.exists():
        raise RuntimeError(f"映像をデコードできませんでした: {proc.stderr.decode(errors='replace')[:300]}")
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("PNG ではない出力が返りました")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


class DuoClient:
    def __init__(self, args, token: str) -> None:
        self.args = args
        self.token = token
        self.ws = None
        self.role: str | None = None
        self.welcome: dict = {}
        self.buffer = FrameBuffer()
        self.ffmpeg = find_ffmpeg()
        self._waiters: dict[str, list[asyncio.Future]] = {}
        self.last_notice: str | None = None
        self.last_error: str | None = None

    # --- 接続

    async def connect(self) -> None:
        from websockets.asyncio.client import connect

        proxy = self.args.proxy
        if self.args.tailscale:
            base = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
            if not base:
                raise RuntimeError("--tailscale には HTTPS_PROXY の設定が必要です")
            proxy = base.rsplit(":", 1)[0] + ":3130"
        uri = f"ws://{self.args.host}:{self.args.port}/"
        self.ws = await connect(
            uri, proxy=proxy, max_size=1024 * 1024, open_timeout=180,
        )
        hello = {
            "t": "hello", "v": 1, "token": self.token,
            "fps": self.args.fps, "max_height": self.args.max_height,
            "bitrate": self.args.bitrate, "codecs": ["h264"],
            "transport": "tcp", "role": self.args.role, "client": "agent",
        }
        await self.ws.send(json.dumps(hello))
        self._recv_task = asyncio.create_task(self._receive_loop())
        welcome = await self._wait_for("welcome", timeout=30)
        self.welcome = welcome
        self.role = welcome.get("role")

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if getattr(self, "_recv_task", None) is not None:
            self._recv_task.cancel()

    # --- 受け取り

    async def _receive_loop(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    if len(raw) < FRAME_HEADER.size:
                        continue
                    flags, _ts = FRAME_HEADER.unpack_from(raw)
                    if flags & (FLAG_AUDIO | FLAG_FILE):
                        continue  # 音とファイルは、今は使わない
                    self.buffer.add(bool(flags & FLAG_KEYFRAME), raw[FRAME_HEADER.size:])
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                t = msg.get("t")
                if t == "notice":
                    self.last_notice = msg.get("message")
                elif t == "error":
                    self.last_error = msg.get("message")
                elif t == "role":
                    self.role = msg.get("role")  # takeover で役割が変わったとき
                for fut in self._waiters.pop(t, []):
                    if not fut.done():
                        fut.set_result(msg)
        except Exception:
            pass  # 切れたことは、コマンド側の失敗として現れる

    async def _wait_for(self, t: str, timeout: float) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(t, []).append(fut)
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            waiters = self._waiters.get(t, [])
            if fut in waiters:
                waiters.remove(fut)

    async def _send(self, msg: dict) -> None:
        await self.ws.send(json.dumps(msg, ensure_ascii=False))

    # --- コマンド

    async def cmd_shot(self, cmd: dict) -> dict:
        path = cmd.get("path") or "/tmp/duo-shot.png"
        deadline = time.monotonic() + float(cmd.get("wait", 5))
        while not self.buffer.data():
            if time.monotonic() > deadline:
                raise RuntimeError("映像がまだ届いていません")
            await asyncio.sleep(0.05)
        w, h = await asyncio.to_thread(decode_snapshot, self.ffmpeg, self.buffer.data(), path)
        return {"path": path, "w": w, "h": h, "age_ms": self.buffer.age_ms(), "frames": self.buffer.frames}

    async def run_command(self, cmd: dict) -> dict:
        name = cmd.get("cmd")
        if name == "shot":
            return await self.cmd_shot(cmd)
        if name == "move":
            await self._send({"t": "move", "x": cmd["x"], "y": cmd["y"]})
            return {}
        if name == "click":
            x, y = cmd["x"], cmd["y"]
            b = cmd.get("b", "left")
            await self._send({"t": "button", "b": b, "d": "down", "x": x, "y": y})
            await self._send({"t": "button", "b": b, "d": "up"})
            return {}
        if name == "button":
            msg = {"t": "button", "b": cmd.get("b", "left"), "d": cmd["d"]}
            if "x" in cmd and "y" in cmd:
                msg["x"], msg["y"] = cmd["x"], cmd["y"]
            await self._send(msg)
            return {}
        if name == "scroll":
            await self._send({"t": "scroll", "dx": cmd.get("dx", 0), "dy": cmd.get("dy", 0)})
            return {}
        if name == "key":
            key = {k: cmd[k] for k in ("vk", "sc") if k in cmd}
            if "d" in cmd:
                await self._send({"t": "key", **key, "d": cmd["d"]})
            else:
                await self._send({"t": "key", **key, "d": "down"})
                await self._send({"t": "key", **key, "d": "up"})
            return {}
        if name == "text":
            await self._send({"t": "text", "s": cmd["s"]})
            return {}
        if name == "windows":
            await self._send({"t": "list_windows"})
            msg = await self._wait_for("windows", timeout=10)
            return {"list": msg.get("list", [])}
        if name == "clip":
            await self._send({"t": "clip_get"})
            msg = await self._wait_for("clip", timeout=10)
            return {"s": msg.get("s", "")}
        if name == "ping":
            n = int(time.monotonic() * 1000) % 100000
            await self._send({"t": "ping", "n": n})
            msg = await self._wait_for("pong", timeout=10)
            return {"n": msg.get("n")}
        if name == "info":
            return {
                "role": self.role, "welcome": self.welcome,
                "last_notice": self.last_notice, "last_error": self.last_error,
                "frames": self.buffer.frames, "age_ms": self.buffer.age_ms(),
            }
        raise RuntimeError(f"知らないコマンドです: {name!r}")


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


async def amain(args) -> int:
    config: dict = {}
    config_path = Path(args.config).expanduser()
    if config_path.exists():
        config = json.loads(config_path.read_text())
    args.host = args.host or config.get("host") or "localhost"
    args.port = args.port or int(config.get("port") or 8765)
    token = os.environ.get("DUO_TOKEN") or config.get("token")
    if not token:
        emit({"ready": False, "error": "トークンがありません（設定ファイルか DUO_TOKEN で渡してください）"})
        return 2

    client = DuoClient(args, token)
    try:
        await client.connect()
    except Exception as e:
        emit({"ready": False, "error": f"接続できませんでした: {e}"})
        return 1
    emit({
        "ready": True, "role": client.role,
        "w": client.welcome.get("w"), "h": client.welcome.get("h"),
        "codec": client.welcome.get("codec"),
        "demoted": args.role == "control" and client.role == "view",
    })

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def read_stdin() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=read_stdin, daemon=True).start()
    try:
        while True:
            line = await queue.get()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except ValueError:
                emit({"ok": False, "error": "JSON として読めませんでした"})
                continue
            if cmd.get("cmd") == "quit":
                emit({"ok": True, "bye": True})
                break
            reply: dict = {"ok": True}
            if "id" in cmd:
                reply["id"] = cmd["id"]
            try:
                reply.update(await client.run_command(cmd))
            except Exception as e:
                reply = {"ok": False, "error": str(e)}
                if "id" in cmd:
                    reply["id"] = cmd["id"]
            emit(reply)
    finally:
        await client.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="DuoRemote ヘッドレスクライアント")
    parser.add_argument("--config", default="~/.config/duo-client/config.json")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--role", choices=("control", "view"), default="control")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--max-height", type=int, default=720)
    parser.add_argument("--bitrate", type=int, default=2500)
    parser.add_argument("--proxy", default=None, help="HTTP CONNECT プロキシの URL")
    parser.add_argument("--tailscale", action="store_true", help="HTTPS_PROXY のポートを 3130 に変えて使う")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
