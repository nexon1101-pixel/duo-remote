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
WINDOW_POLL_SEC = 0.5
# 撮る範囲の最小の大きさ（画素）。これより小さいと、エンコーダーが受け付けないことがある
MIN_REGION = 160


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


# 引数: (設定, 撮る大きさ, 撮る範囲（Noneなら画面全体）) -> 映像の取得元
StreamFactory = Callable[[Settings, "tuple[int, int]", "tuple[int, int, int, int] | None"], Stream]


def _num(v: Any, default: float | None = None) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return float(v)


def _unit(v: Any) -> float | None:
    n = _num(v)
    return None if n is None else min(max(n, 0.0), 1.0)


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def clamp_region(rect: tuple[int, int, int, int], screen: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """ウィンドウの範囲を、主モニターの中に収まるよう切り詰める。幅と高さは偶数にそろえる。
    モニターの外にあるときや、小さすぎるときはNone。"""
    x, y, w, h = rect
    sw, sh = screen
    left, top = max(x, 0), max(y, 0)
    right, bottom = min(x + w, sw), min(y + h, sh)
    w, h = right - left, bottom - top
    w, h = w - (w % 2), h - (h % 2)
    if w < MIN_REGION or h < MIN_REGION:
        return None
    return left, top, w, h


def dispatch_input(msg: dict[str, Any], backend, held: set | None = None) -> dict[str, Any] | None:
    """入力のメッセージを処理する。返事が要るときだけ、その内容を返す。
    heldには、押したままのボタンとキーを入れておく（切断のときに離すため）。"""
    held = held if held is not None else set()
    t = msg.get("t")
    if t == "move":
        x, y = _unit(msg.get("x")), _unit(msg.get("y"))
        if x is not None and y is not None:
            backend.move(x, y)
    elif t == "button":
        b, d = msg.get("b"), msg.get("d")
        if b in ("left", "right", "middle") and d in ("down", "up"):
            backend.button(b, d == "down", _unit(msg.get("x")), _unit(msg.get("y")))
            (held.add if d == "down" else held.discard)(("button", b))
    elif t == "scroll":
        dx, dy = _num(msg.get("dx"), 0.0), _num(msg.get("dy"), 0.0)
        if dx or dy:
            backend.scroll(dx, dy)
    elif t == "key":
        vk, d = _int(msg.get("vk")), msg.get("d")
        if vk is not None and 1 <= vk <= 254 and d in ("down", "up"):
            backend.key(vk, d == "down")
            (held.add if d == "down" else held.discard)(("key", vk))
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
    else:
        log.debug("未対応のメッセージを無視します: %r", t)
    return None


def release_held(backend, held: set) -> None:
    """押したままのボタンとキーを、すべて離す。"""
    for kind, value in sorted(held, key=repr):
        try:
            if kind == "button":
                backend.button(value, False)
            else:
                backend.key(value, False)
        except Exception:  # 離せなくても、残りは続ける
            log.exception("離す操作でエラーが出ました: %s %s", kind, value)
    held.clear()


class Session:
    """1つの接続。表示する範囲（画面全体か、ウィンドウ1つ）と、その映像を受け持つ。"""

    def __init__(self, ws: ServerConnection, settings: Settings, backend, stream_factory: StreamFactory) -> None:
        self.ws = ws
        self.settings = settings
        self.backend = backend
        self.stream_factory = stream_factory
        self.held: set = set()
        self.window: int | None = None  # 表示しているウィンドウ。Noneなら画面全体
        self.region: tuple[int, int, int, int] | None = None
        self.size = (0, 0)
        self.failed: asyncio.Future = asyncio.get_running_loop().create_future()
        self._stream: Stream | None = None
        self._video: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._t0 = time.monotonic()

    async def send(self, obj: dict[str, Any]) -> None:
        await self.ws.send(protocol.dumps(obj))

    # --- 映像

    async def open_stream(self) -> None:
        """今の範囲で撮り始める（まだ送らない）。"""
        if self.region is None:
            src = self.backend.screen_size()
        else:
            src = self.region[2], self.region[3]
        self.size = output_size(src[0], src[1], self.settings.max_height)
        self._stream = self.stream_factory(self.settings, src, self.region)
        await self._stream.start()

    def start_video(self) -> None:
        stream = self._stream
        assert stream is not None
        self._video = asyncio.create_task(self._send_video(stream))
        self._video.add_done_callback(self._video_done)

    async def _send_video(self, stream: Stream) -> None:
        async for keyframe, unit in stream.frames():
            ts = int((time.monotonic() - self._t0) * 1000)
            await self.ws.send(protocol.pack_frame(unit, keyframe, ts))

    def _video_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or self.failed.done():
            return
        exc = task.exception()
        if exc is not None:
            self.failed.set_exception(exc)

    async def stop_stream(self) -> None:
        if self._video is not None:
            self._video.cancel()
            await asyncio.gather(self._video, return_exceptions=True)
            self._video = None
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None

    async def _switch(self, window: int | None, region: tuple[int, int, int, int] | None) -> None:
        """表示する範囲を変えて、撮り直す。大きさが変わるときは、先に resize を送る。"""
        await self.stop_stream()
        self.window, self.region = window, region
        self.backend.set_region(region)
        old = self.size
        await self.open_stream()
        if self.size != old:
            await self.send({"t": "resize", "w": self.size[0], "h": self.size[1]})
        self.start_video()

    # --- 制御メッセージ

    async def handle(self, msg: dict[str, Any]) -> None:
        t = msg.get("t")
        if t == "list_windows":
            await self.send({"t": "windows", "list": self.backend.list_windows()})
        elif t == "mode":
            await self.set_mode(msg)
        else:
            reply = dispatch_input(msg, self.backend, self.held)
            if reply:
                await self.send(reply)

    async def set_mode(self, msg: dict[str, Any]) -> None:
        mode = msg.get("mode")
        if mode == "screen":
            async with self._lock:
                await self.send({"t": "mode", "mode": "screen"})
                await self._switch(None, None)
            return
        if mode != "window":
            await self.send({"t": "error", "message": f"知らない表示の範囲です: {mode!r}"})
            return

        wid = _int(msg.get("id"))
        if wid is None or self.backend.window_info(wid) is None:
            await self.send({"t": "error", "message": "そのウィンドウは見つかりません。一覧を読み直してください"})
            return
        fit_w, fit_h = _num(msg.get("fit_w")), _num(msg.get("fit_h"))
        if fit_w and fit_h and fit_w > 0 and fit_h > 0:
            self.backend.fit_window(wid, fit_w, fit_h)
        # 上に他のウィンドウが重なると映像にも映るので、前に出す
        if not self.backend.focus_window(wid):
            log.warning("ウィンドウを前に出せませんでした: %s", wid)
        await asyncio.sleep(0.2)  # 元の大きさに戻すときの動きが終わるのを待つ

        info = self.backend.window_info(wid)
        region = clamp_region(info["rect"], self.backend.screen_size()) if info else None
        if info is None or region is None:
            await self.send({"t": "error", "message": "そのウィンドウは、主モニターに出ていないため表示できません"})
            return
        async with self._lock:
            await self.send({"t": "mode", "mode": "window", "id": wid, "title": info["title"]})
            await self._switch(wid, region)

    async def watch_window(self) -> None:
        """表示しているウィンドウが動いたら撮り直す。閉じたら画面全体に戻す。"""
        pending: tuple[int, int, int, int] | None = None
        while True:
            await asyncio.sleep(WINDOW_POLL_SEC)
            wid = self.window
            if wid is None:
                pending = None
                continue
            info = self.backend.window_info(wid)
            if info is None:
                async with self._lock:
                    if self.window != wid:
                        continue
                    await self.send({"t": "error", "message": "ウィンドウが閉じられたため、画面全体に戻しました"})
                    await self.send({"t": "mode", "mode": "screen"})
                    await self._switch(None, None)
                continue
            if info["minimized"]:
                continue  # 最小化している間は、前の範囲のまま
            region = clamp_region(info["rect"], self.backend.screen_size())
            if region is None or region == self.region:
                pending = None
                continue
            # 動かしている途中で何度も撮り直さないよう、同じ位置が2回続いたら撮り直す
            if region != pending:
                pending = region
                continue
            pending = None
            async with self._lock:
                if self.window == wid:
                    await self._switch(wid, region)


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

    async def receive(session: Session) -> None:
        async for raw in session.ws:
            msg = protocol.loads(raw)
            if msg is not None:
                await session.handle(msg)

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

        session = Session(ws, per_conn, backend, stream_factory)
        backend.set_region(None)
        tasks: list[asyncio.Task] = []
        try:
            await session.open_stream()
            w, h = session.size
            await ws.send(protocol.dumps(
                {"t": "welcome", "v": protocol.VERSION, "w": w, "h": h, "codec": "h264", "mode": "screen"}
            ))
            log.info("接続しました: %s（%dx%d, %dfps）", ws.remote_address, w, h, per_conn.fps)
            session.start_video()
            tasks = [
                asyncio.create_task(receive(session)),
                asyncio.create_task(watch_ime(ws)),
                asyncio.create_task(session.watch_window()),
            ]
            done, _ = await asyncio.wait([*tasks, session.failed], return_when=asyncio.FIRST_COMPLETED)
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
            await session.stop_stream()
            # 押したまま切れると、PCでボタンやキーが押されたままになるので、離す
            release_held(backend, session.held)
            if active["ws"] is ws:
                active["ws"] = None
                backend.set_region(None)
            log.info("切断しました")

    return handler
