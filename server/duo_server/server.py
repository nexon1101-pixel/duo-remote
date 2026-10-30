"""WebSocketサーバー。認証、映像の送信、入力の受け付けを行う。"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from . import protocol
from .capture import StreamError, output_size

log = logging.getLogger(__name__)

HELLO_TIMEOUT_SEC = 5
IME_POLL_SEC = 0.5


@dataclass
class Settings:
    token: str
    fps: int = 60
    bitrate_kbps: int = 8000
    max_height: int = 0


class Stream(Protocol):
    async def start(self) -> None: ...
    def frames(self): ...
    async def stop(self) -> None: ...


# 引数: (設定, 画面の大きさ) -> 映像の取得元
StreamFactory = Callable[[Settings, "tuple[int, int]"], Stream]


def _num(v: Any, default: float | None = None) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return float(v)


def _unit(v: Any) -> float | None:
    n = _num(v)
    return None if n is None else min(max(n, 0.0), 1.0)


def dispatch_input(msg: dict[str, Any], backend) -> dict[str, Any] | None:
    """入力のメッセージを処理する。返事が要るときだけ、その内容を返す。"""
    t = msg.get("t")
    if t == "move":
        x, y = _unit(msg.get("x")), _unit(msg.get("y"))
        if x is not None and y is not None:
            backend.move(x, y)
    elif t == "button":
        b, d = msg.get("b"), msg.get("d")
        if b in ("left", "right", "middle") and d in ("down", "up"):
            backend.button(b, d == "down", _unit(msg.get("x")), _unit(msg.get("y")))
    elif t == "scroll":
        dx, dy = _num(msg.get("dx"), 0.0), _num(msg.get("dy"), 0.0)
        if dx or dy:
            backend.scroll(dx, dy)
    elif t == "key":
        vk, d = msg.get("vk"), msg.get("d")
        if isinstance(vk, int) and not isinstance(vk, bool) and 1 <= vk <= 254 and d in ("down", "up"):
            backend.key(vk, d == "down")
    elif t == "text":
        s = msg.get("s")
        if isinstance(s, str) and s:
            backend.text(s[: protocol.MAX_TEXT_LENGTH])
    elif t == "ime":
        on = msg.get("on")
        if isinstance(on, bool):
            backend.ime_set(on)
    elif t == "ping":
        return {"t": "pong", "n": msg.get("n")}
    elif t in ("mode", "list_windows"):
        return {"t": "error", "message": "この版では、画面全体の表示だけに対応しています"}
    else:
        log.debug("未対応のメッセージを無視します: %r", t)
    return None


def make_handler(settings: Settings, backend, stream_factory: StreamFactory):
    active: dict[str, ServerConnection | None] = {"ws": None}

    async def reject(ws: ServerConnection, message: str) -> None:
        try:
            await ws.send(protocol.dumps({"t": "error", "message": message}))
            await ws.close(1008, message)
        except ConnectionClosed:
            pass

    async def authenticate(ws: ServerConnection) -> dict[str, Any] | None:
        try:
            raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT_SEC)
        except (asyncio.TimeoutError, ConnectionClosed):
            return None
        hello = protocol.loads(raw)
        if not hello or hello.get("t") != "hello":
            await reject(ws, "最初のメッセージは hello にしてください")
            return None
        if hello.get("v") != protocol.VERSION:
            await reject(ws, f"通信の版が合いません（サーバーは {protocol.VERSION}）")
            return None
        token = hello.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(
            token.encode(), settings.token.encode()
        ):
            log.warning("トークンが違う接続を断りました: %s", ws.remote_address)
            await reject(ws, "トークンが違います")
            return None
        return hello

    async def send_video(ws: ServerConnection, stream: Stream, t0: float) -> None:
        async for keyframe, unit in stream.frames():
            ts = int((time.monotonic() - t0) * 1000)
            await ws.send(protocol.pack_frame(unit, keyframe, ts))

    async def receive(ws: ServerConnection) -> None:
        async for raw in ws:
            msg = protocol.loads(raw)
            if msg is None:
                continue
            reply = dispatch_input(msg, backend)
            if reply:
                await ws.send(protocol.dumps(reply))

    async def watch_ime(ws: ServerConnection) -> None:
        last: bool | None = None
        while True:
            state = backend.ime_state()
            if state is not None and state != last:
                last = state
                await ws.send(protocol.dumps({"t": "ime_state", "on": state}))
            await asyncio.sleep(IME_POLL_SEC)

    async def handler(ws: ServerConnection) -> None:
        hello = await authenticate(ws)
        if hello is None:
            return

        # 同時に1台だけ。前の接続は切る
        previous = active["ws"]
        if previous is not None:
            try:
                await previous.close(1000, "別の端末がつながりました")
            except ConnectionClosed:
                pass
        active["ws"] = ws

        fps = int(_num(hello.get("fps"), settings.fps) or settings.fps)
        max_height = int(_num(hello.get("max_height"), settings.max_height) or 0)
        per_conn = Settings(settings.token, min(max(fps, 5), 120), settings.bitrate_kbps, max_height)

        src = backend.screen_size()
        w, h = output_size(src[0], src[1], per_conn.max_height)
        stream = stream_factory(per_conn, src)
        tasks: list[asyncio.Task] = []
        try:
            await stream.start()
            await ws.send(protocol.dumps(
                {"t": "welcome", "v": protocol.VERSION, "w": w, "h": h, "codec": "h264", "mode": "screen"}
            ))
            log.info("接続しました: %s（%dx%d, %dfps）", ws.remote_address, w, h, per_conn.fps)
            t0 = time.monotonic()
            tasks = [
                asyncio.create_task(send_video(ws, stream, t0)),
                asyncio.create_task(receive(ws)),
                asyncio.create_task(watch_ime(ws)),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if isinstance(exc, StreamError):
                    log.error("%s", exc)
                    await reject(ws, "映像の取得が止まりました。サーバーのログを確認してください")
                elif exc and not isinstance(exc, ConnectionClosed):
                    log.exception("接続の処理でエラーが出ました", exc_info=exc)
        except StreamError as e:
            log.error("%s", e)
            await reject(ws, "映像を始められませんでした。サーバーのログを確認してください")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await stream.stop()
            if active["ws"] is ws:
                active["ws"] = None
            log.info("切断しました")

    return handler
