"""WebSocketサーバー。認証、映像の送信、入力の受け付けを行う。"""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from . import files, protocol
from .audio import ALLOWED_CHANNELS, ALLOWED_CODECS, ALLOWED_RATES, OPUS_RATE, AudioError
from .capture import StreamError, output_size
from .input_win import ClipboardError, from_windows_newlines, to_windows_newlines
from .virtual_display import VirtualDisplayError, monitor_of

log = logging.getLogger(__name__)

HELLO_TIMEOUT_SEC = 5
IME_POLL_SEC = 0.5
WINDOW_POLL_SEC = 0.5
# 撮る範囲の最小の大きさ（画素）。これより小さいと、エンコーダーが受け付けないことがある
MIN_REGION = 160
# 通信が切れたとき、仮想ディスプレイを無効に戻し、持ってきたウィンドウを元に戻すまでの待ち時間（秒）。
# この間につなぎ直せば、そのまま続きができる。iPhone の「切断」ボタンのときは、待たずに戻す
RESTORE_GRACE_SEC = 120
# 仮想ディスプレイを撮り始めるときの、やり直しの回数、最初のフレームを待つ時間、やり直す前の待ち時間（秒）。
# 解像度を変えた直後などは、モニターの番号や位置が落ち着くまで、撮るのに失敗することがある
VIRTUAL_START_ATTEMPTS = 3
VIRTUAL_FIRST_FRAME_SEC = 8
VIRTUAL_RETRY_SEC = 1.5
# 撮影が途中で止まったとき（画面の設定が変わる、UAC の画面に切り替わる、など。DXGI_ERROR_ACCESS_LOST）の撮り直し。
# 待ち時間は、止まった回数 × RESTART_DELAY_SEC。RESTART_WINDOW_SEC のうちに RESTART_MAX 回を超えて止まったら、あきらめる
RESTART_DELAY_SEC = 1.0
RESTART_MAX = 5
RESTART_WINDOW_SEC = 30
# 前の接続の後片付けを待つ時間の上限
PREVIOUS_CLEANUP_SEC = 3
# 送り待ちの映像がこれを超えたら、次のキーフレームまで映像を捨てる（遅れをためないため）
MAX_QUEUED_FRAMES = 4
MAX_BUFFERED_BYTES = 256 * 1024
# 音の送信バッファがこれを超えたら、音を捨てる（遅れをためないため）
MAX_AUDIO_BUFFERED_BYTES = 128 * 1024
# クライアントが選べるビットレートの範囲（kbps）
MIN_BITRATE_KBPS = 500
MAX_BITRATE_KBPS = 20000

# ウィンドウだけを撮るとき、最初の1枚が届くまで、描き直しを頼む間隔と回数
NUDGE_INTERVAL_SEC = 0.3
NUDGE_ATTEMPTS = 20

VK_RETURN = 0x0D
_NEWLINE = re.compile(r"\r\n|\r|\n")


@dataclass
class Settings:
    token: str
    fps: int = 60
    bitrate_kbps: int = 8000
    max_height: int = 0
    # ウィンドウを表示するとき、画面を切り抜かず、ウィンドウだけを撮る（ffmpeg に gfxcapture があるとき）
    window_capture: bool = False


class Stream(Protocol):
    async def start(self) -> None: ...
    def frames(self): ...
    async def stop(self) -> None: ...


# 引数: (周波数, チャンネル数) -> 音の取得元（start、frames、stop を持つ）
AudioFactory = Callable[[int, int], Any]

# 引数: (設定, 撮る大きさ, 撮る範囲（Noneなら、モニター全体。デスクトップ上の位置）, モニター（Noneなら主モニター）,
#        ウィンドウ（window_capture のときだけ。ウィンドウだけを撮る）) -> 映像の取得元
StreamFactory = Callable[
    [Settings, "tuple[int, int]", "tuple[int, int, int, int] | None", "dict | None", "int | None"], Stream
]


def _num(v: Any, default: float | None = None) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return float(v)


def _unit(v: Any) -> float | None:
    n = _num(v)
    return None if n is None else min(max(n, 0.0), 1.0)


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def clamp_region(
    rect: tuple[int, int, int, int], bounds: tuple[int, ...]
) -> tuple[int, int, int, int] | None:
    """ウィンドウの範囲を、モニターの中に収まるよう切り詰める。幅と高さは偶数にそろえる。
    bounds は、モニターの (x, y, 幅, 高さ)。(幅, 高さ) だけのときは、左上が (0, 0) のモニター。
    モニターの外にあるときや、小さすぎるときはNone。"""
    x, y, w, h = rect
    bx, by, bw, bh = bounds if len(bounds) == 4 else (0, 0, *bounds)
    left, top = max(x, bx), max(y, by)
    right, bottom = min(x + w, bx + bw), min(y + h, by + bh)
    w, h = right - left, bottom - top
    w, h = w - (w % 2), h - (h % 2)
    if w < MIN_REGION or h < MIN_REGION:
        return None
    return left, top, w, h


def _reason(exc: BaseException) -> str:
    """エラーの文のうち、利用者に見せる1行（いちばん最後の、空でない行）。"""
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    text = lines[-1] if lines else type(exc).__name__
    return text if len(text) <= 160 else text[:157] + "…"


def dispatch_input(msg: dict[str, Any], backend, held: set | None = None) -> dict[str, Any] | None:
    """入力のメッセージを処理する。返事が要るときだけ、その内容を返す。
    heldには、押したままのボタンとキーを入れておく（切断のときに離すため）。"""
    held = held if held is not None else set()
    t = msg.get("t")
    if t == "move":
        x, y = _unit(msg.get("x")), _unit(msg.get("y"))
        if x is not None and y is not None:
            backend.move(x, y)
    elif t == "move_rel":
        dx, dy = _num(msg.get("dx"), 0.0), _num(msg.get("dy"), 0.0)
        dx, dy = min(max(dx, -1.0), 1.0), min(max(dy, -1.0), 1.0)
        if dx or dy:
            backend.move_rel(dx, dy)
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
        vk, sc, d = _int(msg.get("vk")), _int(msg.get("sc")), msg.get("d")
        if d not in ("down", "up"):
            return None
        mark = held.add if d == "down" else held.discard
        if sc is not None:
            # 1バイトのスキャンコードか、拡張キー（0xE0 が付くもの）
            if 1 <= sc <= 0xFF or 0xE001 <= sc <= 0xE0FF:
                backend.key_scan(sc, d == "down")
                mark(("scan", sc))
        elif vk is not None and 1 <= vk <= 254:
            backend.key(vk, d == "down")
            mark(("key", vk))
    elif t == "text":
        s = msg.get("s")
        if isinstance(s, str) and s:
            # 改行は、文字のままだと多くのアプリで改行にならないので、Enter として押す
            lines = _NEWLINE.split(s[: protocol.MAX_TEXT_LENGTH])
            for i, line in enumerate(lines):
                if i > 0:
                    backend.key(VK_RETURN, True)
                    backend.key(VK_RETURN, False)
                if line:
                    backend.text(line)
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
            elif kind == "scan":
                backend.key_scan(value, False)
            else:
                backend.key(value, False)
        except Exception:  # 離せなくても、残りは続ける
            log.exception("離す操作でエラーが出ました: %s %s", kind, value)
    held.clear()


class Session:
    """1つの接続。表示する範囲（画面全体か、ウィンドウ1つ）と、その映像を受け持つ。"""

    def __init__(
        self, ws: ServerConnection, settings: Settings, backend, stream_factory: StreamFactory,
        audio_factory: AudioFactory | None = None,
    ) -> None:
        self.ws = ws
        self.audio_factory = audio_factory
        self._audio = None
        self._audio_task: asyncio.Task | None = None
        self.files = files.FileTransfer(self.send, ws.send, backend)
        self.settings = settings
        self.backend = backend
        self.stream_factory = stream_factory
        self.held: set = set()
        self.window: int | None = None  # 表示しているウィンドウ。Noneなら、ウィンドウ1つではない
        self.region: tuple[int, int, int, int] | None = None
        # 撮っているモニター。Noneなら主モニター。仮想ディスプレイのデスクトップを映すときは、そのモニター
        self.monitor: dict | None = None
        self.size = (0, 0)
        loop = asyncio.get_running_loop()
        self.failed: asyncio.Future = loop.create_future()
        # 別の端末がつながったときに、この接続を終わらせる合図
        self.kicked: asyncio.Future = loop.create_future()
        # この接続の後片付け（映像を止める、押したままのキーを離す）が終わった
        self.finished = asyncio.Event()
        self._stream: Stream | None = None
        self._video: asyncio.Task | None = None
        self._first_frame = asyncio.Event()  # 今の映像の、最初のフレームが届いた
        self._verifying = False  # 撮り始めを確かめている間は、失敗しても、接続を切らない（やり直す）
        self._paused = False  # 画面の設定を変える間、撮影を止めている
        self._restarts: list[float] = []  # 撮影が止まって、撮り直した時刻
        self._recovery: asyncio.Task | None = None
        self._nudge: asyncio.Task | None = None  # ウィンドウに描き直しを頼む（最初の1枚が届くまで）
        self._lock = asyncio.Lock()
        self._t0 = time.monotonic()

    async def send(self, obj: dict[str, Any]) -> None:
        await self.ws.send(protocol.dumps(obj))

    # --- 映像

    async def open_stream(self) -> None:
        """今の範囲で撮り始める（まだ送らない）。"""
        if self.region is not None:
            src = self.region[2], self.region[3]
        elif self.monitor is not None:
            src = self.monitor["rect"][2], self.monitor["rect"][3]
        else:
            src = self.backend.screen_size()
        self.size = output_size(src[0], src[1], self.settings.max_height)
        window = self.window if self.settings.window_capture else None
        self._stream = self.stream_factory(self.settings, src, self.region, self.monitor, window)
        await self._stream.start()

    def start_video(self) -> None:
        stream = self._stream
        assert stream is not None
        self._first_frame = asyncio.Event()
        self._video = asyncio.create_task(self._send_video(stream, self._first_frame))
        self._video.add_done_callback(self._video_done)
        if self.settings.window_capture and self.window is not None:
            self._nudge = asyncio.create_task(self._nudge_window(self.window, self._first_frame, self._video))

    async def _nudge_window(self, wid: int, first_frame: asyncio.Event, video: asyncio.Task) -> None:
        """ウィンドウだけを撮るとき、最初の1枚が届くまで、ウィンドウに描き直しを頼む。
        ffmpeg 8.1 の gfxcapture は、ウィンドウの中身が変わるまで、1枚も出さないため。"""
        for _ in range(NUDGE_ATTEMPTS):
            await asyncio.sleep(NUDGE_INTERVAL_SEC)
            if first_frame.is_set() or video.done() or self.window != wid:
                return
            await asyncio.to_thread(self.backend.repaint_window, wid)

    async def _send_video(self, stream: Stream, first_frame: asyncio.Event) -> None:
        """映像を送る。ffmpegからの読み取りは別に動かし、送るのが追いつかないときは古いフレームを捨てる。"""
        queue: asyncio.Queue = asyncio.Queue()
        reader = asyncio.create_task(self._read_frames(stream, queue, first_frame))
        try:
            while True:
                item = await queue.get()
                if isinstance(item, BaseException):
                    raise item
                keyframe, unit, ts = item
                await self.ws.send(protocol.pack_frame(unit, keyframe, ts))
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def _read_frames(self, stream: Stream, queue: asyncio.Queue, first_frame: asyncio.Event) -> None:
        dropping = False
        dropped = 0
        try:
            async for keyframe, unit in stream.frames():
                first_frame.set()
                ts = int((time.monotonic() - self._t0) * 1000)
                if queue.qsize() >= MAX_QUEUED_FRAMES or self._buffered_bytes() > MAX_BUFFERED_BYTES:
                    if not dropping:
                        log.info("通信が追いつかないため、次のキーフレームまで映像を捨てます")
                    dropping = True
                    while not queue.empty():
                        queue.get_nowait()
                        dropped += 1
                if dropping:
                    # キーフレームの途中からは表示できないので、次のキーフレームまで待つ
                    if not keyframe:
                        dropped += 1
                        continue
                    dropping = False
                    log.info("映像を %d フレーム捨てました", dropped)
                    dropped = 0
                queue.put_nowait((keyframe, unit, ts))
        except Exception as e:  # ffmpegが止まったときなど。送る側で投げ直す
            queue.put_nowait(e)

    def _buffered_bytes(self) -> int:
        transport = getattr(self.ws, "transport", None)
        try:
            return transport.get_write_buffer_size() if transport is not None else 0
        except (AttributeError, RuntimeError):
            return 0

    # --- 音

    async def set_audio(self, msg: dict[str, Any]) -> None:
        """音を送る・止める。取り始めたら、先に audio_state で形を知らせてから、音を送る。"""
        await self.stop_audio()
        if msg.get("on") is not True:
            await self.send({"t": "audio_state", "on": False})
            return
        rate, channels = _int(msg.get("rate")) or 48000, _int(msg.get("channels")) or 2
        codec = msg.get("codec") or "pcm"
        if codec not in ALLOWED_CODECS or (codec == "opus" and rate != OPUS_RATE):
            await self.send({"t": "audio_state", "on": False, "reason": "その音の形には対応していません"})
            return
        if rate not in ALLOWED_RATES or channels not in ALLOWED_CHANNELS:
            await self.send({"t": "audio_state", "on": False, "reason": "その音の形には対応していません"})
            return
        if self.audio_factory is None:
            await self.send({"t": "audio_state", "on": False, "reason": "このサーバーでは、音を送れません"})
            return
        stream = self.audio_factory(rate, channels, "opus") if codec == "opus" else self.audio_factory(rate, channels)
        try:
            await stream.start()
        except AudioError as e:
            log.warning("音を始められませんでした: %s", e)
            await self.send({"t": "audio_state", "on": False, "reason": str(e)})
            return
        self._audio = stream
        await self.send({"t": "audio_state", "on": True, "rate": rate, "channels": channels, "codec": codec})
        self._audio_task = asyncio.create_task(self._send_audio(stream))

    async def _send_audio(self, stream) -> None:
        try:
            async for chunk in stream.frames():
                if self._buffered_bytes() > MAX_AUDIO_BUFFERED_BYTES:
                    continue  # 通信が追いつかないので、この音は捨てる
                ts = int((time.monotonic() - self._t0) * 1000)
                await self.ws.send(protocol.pack_audio(chunk, ts))
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            pass
        except Exception as e:  # ffmpeg が止まったときなど。映像は続ける
            log.warning("音が止まりました: %s", e)
            try:
                await self.send({"t": "audio_state", "on": False, "reason": "音が止まりました"})
            except ConnectionClosed:
                pass

    async def stop_audio(self) -> None:
        task, stream = self._audio_task, self._audio
        self._audio_task = self._audio = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if stream is not None:
            await stream.stop()

    def kick(self) -> None:
        if not self.kicked.done():
            self.kicked.set_result(None)

    def _video_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or self.failed.done() or self._verifying:
            return
        exc = task.exception()
        if exc is None:
            return
        if isinstance(exc, StreamError):
            # 撮影が止まった。画面の設定が変わると、動いている撮影は、すべて一度無効になる（DXGI_ERROR_ACCESS_LOST）。
            # 接続を切らずに、撮り直す
            self._recovery = asyncio.create_task(self._recover(task, exc))
            return
        self.failed.set_exception(exc)

    async def _recover(self, task: asyncio.Task, exc: BaseException) -> None:
        async with self._lock:
            if self._video is not task or self.failed.done():
                return  # もう別の映像に替わっている
            now = time.monotonic()
            self._restarts = [t for t in self._restarts if now - t < RESTART_WINDOW_SEC] + [now]
            try:
                if len(self._restarts) > RESTART_MAX:
                    raise exc
                log.warning("映像が止まりました。撮り直します（%d/%d回目）: %s", len(self._restarts), RESTART_MAX, _reason(exc))
                await asyncio.sleep(RESTART_DELAY_SEC * len(self._restarts))
                await self._switch(*self._current_target())
            except ConnectionClosed:
                pass
            except StreamError as e:
                if self.window is None and self.monitor is None:
                    # 本物の画面も撮れない。接続を閉じる
                    if not self.failed.done():
                        self.failed.set_exception(e)
                    return
                log.error("映像が止まり続けるため、本物の画面に戻します: %s", e)
                try:
                    await self.send({
                        "t": "error", "message": f"映像の取得が止まったため、本物の画面に戻します（{_reason(e)}）",
                    })
                    await self.send({"t": "mode", "mode": "screen"})
                    self._restarts.clear()
                    await self._switch(None, None)
                except ConnectionClosed:
                    pass
                except Exception as e2:
                    if not self.failed.done():
                        self.failed.set_exception(e2)
            except Exception as e:
                if not self.failed.done():
                    self.failed.set_exception(e)

    def _current_target(self) -> tuple[int | None, tuple[int, int, int, int] | None, dict | None]:
        """今の表示を、撮り直すための (ウィンドウ, 範囲, モニター)。ウィンドウの位置や、仮想モニターの番号は、調べ直す。
        ウィンドウや仮想モニターが、なくなっていたら、本物の画面。"""
        if self.window is not None:
            info = self.backend.window_info(self.window)
            if info is not None:
                monitor, region = self._locate(info["rect"])
                if region is not None:
                    return self.window, region, monitor
            return None, None, None
        if self.monitor is not None:
            found = self._find_virtual()
            return (None, None, found) if found is not None else (None, None, None)
        return None, None, None

    async def _pause_video(self) -> None:
        """画面の設定を変える間、撮影を止める（変わった瞬間に、撮影が無効になって、止まるため）。"""
        await self.stop_stream()
        self._paused = True

    async def _resume_video(self) -> None:
        """止めていた撮影を、今の表示で撮り直す（切り替えのほうが先に済んでいれば、何もしない）。"""
        if not self._paused:
            return
        async with self._lock:
            if self._paused:
                await self._switch_with_fallback(*self._current_target(), "映像")

    async def stop_stream(self) -> None:
        if self._nudge is not None:
            self._nudge.cancel()
            self._nudge = None
        if self._video is not None:
            self._video.cancel()
            await asyncio.gather(self._video, return_exceptions=True)
            self._video = None
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None

    async def _switch(
        self, window: int | None, region: tuple[int, int, int, int] | None, monitor: dict | None = None
    ) -> None:
        """表示する範囲を変えて、撮り直す。大きさが変わるときは、先に resize を送る。
        仮想ディスプレイを撮るときは、最初のフレームが届くまで確かめ、失敗したら、モニターの情報を取り直して、やり直す。"""
        self._paused = False
        verify = monitor is not None and bool(monitor.get("virtual"))
        attempts = VIRTUAL_START_ATTEMPTS if verify else 1
        failure: BaseException | None = None
        for attempt in range(attempts):
            await self.stop_stream()
            self.window, self.region, self.monitor = window, region, monitor
            # 入力の座標は、撮っている範囲が基準。範囲がなければ、モニター全体（主モニターなら、Noneのまま）
            self.backend.set_region(region or (monitor["rect"] if monitor else None))
            old = self.size
            try:
                await self.open_stream()
            except StreamError as e:
                if not verify:
                    raise
                failure = e
            else:
                if self.size != old:
                    await self.send({"t": "resize", "w": self.size[0], "h": self.size[1]})
                self._verifying = verify
                self.start_video()
                if not verify:
                    return
                try:
                    failure = await self._wait_first_frame()
                finally:
                    self._verifying = False
                if failure is None:
                    return
            log.warning("仮想ディスプレイを撮れませんでした（%d/%d回目）: %s", attempt + 1, attempts, _reason(failure))
            await self.stop_stream()
            if attempt < attempts - 1:
                await asyncio.sleep(VIRTUAL_RETRY_SEC)
                monitor = self._refresh_virtual(monitor)  # 番号や位置が変わっていることがある
        raise StreamError(str(failure))

    async def _wait_first_frame(self) -> BaseException | None:
        """今の映像の最初のフレームが届くまで待つ。届いたら None、止まった・間に合わなかったときは、その理由。"""
        video, first = self._video, self._first_frame
        assert video is not None
        waiter = asyncio.create_task(first.wait())
        try:
            await asyncio.wait({video, waiter}, timeout=VIRTUAL_FIRST_FRAME_SEC, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
        if first.is_set():
            return None
        if video.done() and not video.cancelled() and video.exception() is not None:
            return video.exception()
        return StreamError(f"映像が{VIRTUAL_FIRST_FRAME_SEC}秒たっても始まりませんでした")

    def _find_virtual(self) -> dict | None:
        for m in self.backend.monitors():
            if m.get("virtual"):
                return m
        return None

    def _refresh_virtual(self, monitor: dict | None) -> dict | None:
        return self._find_virtual() or monitor

    async def _switch_with_fallback(
        self, window: int | None, region: tuple[int, int, int, int] | None, monitor: dict | None, what: str
    ) -> None:
        """切り替える。撮れなかったときは、理由を知らせて、本物の画面に戻す（それも撮れなければ、接続を閉じる）。"""
        try:
            await self._switch(window, region, monitor)
        except StreamError as e:
            log.error("%sを映せませんでした: %s", what, e)
            await self.send({"t": "error", "message": f"{what}を映せませんでした（{_reason(e)}）。本物の画面に戻します"})
            await self.send({"t": "mode", "mode": "screen"})
            await self._switch(None, None)

    async def _switch_to_window(
        self, wid: int, region: tuple[int, int, int, int], monitor: dict | None
    ) -> None:
        await self._switch_with_fallback(wid, region, monitor, "このウィンドウ")

    # --- モニターとウィンドウの位置

    def _locate(self, rect: tuple[int, int, int, int]) -> tuple[dict | None, tuple[int, int, int, int] | None]:
        """ウィンドウの範囲が出ているモニター（主モニターなら None）と、撮る範囲を返す。
        どのモニターにも出ていないときや、小さすぎるときは、撮る範囲が None。"""
        found = monitor_of(rect, self.backend.monitors())
        if found is None:
            return None, None
        monitor = None if found["primary"] else found
        if self.settings.window_capture:
            # ウィンドウだけを撮るときは、モニターからはみ出した部分も撮れるので、切り詰めない
            x, y, w, h = rect
            w, h = w - (w % 2), h - (h % 2)
            return monitor, ((x, y, w, h) if w >= MIN_REGION and h >= MIN_REGION else None)
        return monitor, clamp_region(rect, found["rect"])

    def _where(self, rect: tuple[int, int, int, int], monitors: list[dict]) -> str:
        found = monitor_of(rect, monitors)
        if found is None:
            return "none"
        return "virtual" if found["virtual"] else "main" if found["primary"] else "other"

    async def _ensure_virtual(self, fit_w: float | None = None, fit_h: float | None = None) -> dict | None:
        """仮想ディスプレイを出して、そのモニターを返す。出せないときは、理由を知らせて None。
        fit_w、fit_h があれば、その縦横比に近い解像度にする。"""
        if not self.backend.virtual_available():
            await self.send({"t": "error", "message": "仮想ディスプレイのドライバーが入っていません"})
            return None
        present = any(m["virtual"] for m in self.backend.monitors())
        if not present:
            await self.send({"t": "notice", "message": "仮想ディスプレイを出しています…"})
        if (not present or (fit_w and fit_h)) and self._stream is not None:
            await self._pause_video()  # モニターを足す・解像度を変えると、動いている撮影が止まるので、先に止める
        try:
            monitor = await asyncio.to_thread(self.backend.virtual_on)
            if fit_w and fit_h and fit_w > 0 and fit_h > 0:
                monitor = await asyncio.to_thread(self.backend.virtual_set_aspect, fit_w / fit_h)
        except VirtualDisplayError as e:
            log.warning("仮想ディスプレイを出せませんでした: %s", e)
            await self.send({"t": "error", "message": str(e)})
            return None
        return monitor

    # --- 制御メッセージ

    async def handle(self, msg: dict[str, Any]) -> None:
        t = msg.get("t")
        if t == "list_windows":
            await self.send({"t": "windows", "list": await self._window_list()})
        elif t == "mode":
            try:
                await self.set_mode(msg)
            finally:
                await self._resume_video()
        elif t == "bring":
            try:
                await self.bring(msg)
            finally:
                await self._resume_video()
        elif t == "restore":
            restored = await asyncio.to_thread(self.backend.restore_brought)
            await self.send({"t": "notice", "message": f"ウィンドウを元の場所に戻しました（{len(restored)}個）"})
        elif t == "audio":
            await self.set_audio(msg)
        elif t in files.MESSAGES:
            await self.files.handle(msg)
        elif t == "clip_get":
            await self.clip_get()
        elif t == "clip_set":
            await self.clip_set(msg)
        else:
            reply = dispatch_input(msg, self.backend, self.held)
            if reply:
                await self.send(reply)

    async def clip_get(self) -> None:
        """PCのクリップボードの文字を、iPhoneに送る。"""
        try:
            text = from_windows_newlines(await asyncio.to_thread(self.backend.clip_get))
        except ClipboardError as e:
            await self.send({"t": "error", "message": str(e)})
            return
        if len(text) > protocol.MAX_CLIP_LENGTH:
            await self.send({"t": "error", "message": f"PCのクリップボードの文字が長すぎます（{protocol.MAX_CLIP_LENGTH}文字まで）"})
            return
        await self.send({"t": "clip", "s": text})

    async def clip_set(self, msg: dict[str, Any]) -> None:
        """iPhoneから届いた文字を、PCのクリップボードに入れる。"""
        s = msg.get("s")
        if not isinstance(s, str):
            return
        if len(s) > protocol.MAX_CLIP_LENGTH:
            await self.send({"t": "error", "message": f"文字が長すぎます（{protocol.MAX_CLIP_LENGTH}文字まで）"})
            return
        try:
            await asyncio.to_thread(self.backend.clip_set, to_windows_newlines(s))
        except ClipboardError as e:
            await self.send({"t": "error", "message": str(e)})
            return
        await self.send({"t": "notice", "message": "PCのクリップボードに入れました"})

    async def _window_list(self) -> list[dict]:
        monitors = await asyncio.to_thread(self.backend.monitors)
        windows = await asyncio.to_thread(self.backend.list_windows)
        return [
            {"id": w["id"], "title": w["title"], "app": w["app"], "where": self._where(w["rect"], monitors)}
            for w in windows
        ]

    async def bring(self, msg: dict[str, Any]) -> None:
        """ウィンドウを、仮想モニターに持ってくる（表示は、今のまま）。切断したら、元に戻す。"""
        wid = _int(msg.get("id"))
        info = self.backend.window_info(wid) if wid is not None else None
        if info is None:
            await self.send({"t": "error", "message": "そのウィンドウは見つかりません。一覧を読み直してください"})
            return
        monitor = await self._ensure_virtual()
        if monitor is None:
            return
        if not await asyncio.to_thread(self.backend.bring_window, wid, monitor["work"]):
            await self.send({"t": "error", "message": "そのウィンドウは、仮想ディスプレイに移せませんでした"})
            return
        await asyncio.to_thread(self.backend.focus_window, wid)
        await self.send({"t": "brought", "id": wid, "title": info["title"]})

    async def set_mode(self, msg: dict[str, Any]) -> None:
        mode = msg.get("mode")
        if mode == "screen":
            async with self._lock:
                await self.send({"t": "mode", "mode": "screen"})
                await self._switch(None, None)
            return
        if mode == "desktop":
            monitor = await self._ensure_virtual(_num(msg.get("fit_w")), _num(msg.get("fit_h")))
            if monitor is None:
                return
            async with self._lock:
                await self.send({"t": "mode", "mode": "desktop"})
                await self._switch_with_fallback(None, None, monitor, "仮想画面")
            return
        if mode != "window":
            await self.send({"t": "error", "message": f"知らない表示の範囲です: {mode!r}"})
            return

        wid = _int(msg.get("id"))
        if wid is None or self.backend.window_info(wid) is None:
            await self.send({"t": "error", "message": "そのウィンドウは見つかりません。一覧を読み直してください"})
            return
        fit_w, fit_h = _num(msg.get("fit_w")), _num(msg.get("fit_h"))
        # 相手のアプリが固まっていると戻ってこないことがあるので、ウィンドウの操作は、別のスレッドで呼ぶ
        if msg.get("to_virtual") is True:
            # 仮想モニターを、iPhoneの画面の縦横比にして、そこにウィンドウを移す（作業領域いっぱいに広げる）
            monitor = await self._ensure_virtual(fit_w, fit_h)
            if monitor is None:
                return
            if not await asyncio.to_thread(self.backend.bring_window, wid, monitor["work"]):
                await self.send({"t": "error", "message": "そのウィンドウは、仮想ディスプレイに移せませんでした"})
                return
        elif fit_w and fit_h and fit_w > 0 and fit_h > 0:
            # ウィンドウが出ているモニターの作業領域に、大きさを合わせる
            found = monitor_of(self.backend.window_info(wid)["rect"], self.backend.monitors())
            await asyncio.to_thread(self.backend.fit_window, wid, fit_w, fit_h, found["work"] if found else None)
        # 上に他のウィンドウが重なると映像にも映るので、前に出す
        if not await asyncio.to_thread(self.backend.focus_window, wid):
            log.warning("ウィンドウを前に出せませんでした: %s", wid)
        await asyncio.sleep(0.2)  # 元の大きさに戻すときの動きが終わるのを待つ

        info = self.backend.window_info(wid)
        monitor, region = self._locate(info["rect"]) if info else (None, None)
        if info is None or region is None:
            await self.send({"t": "error", "message": "そのウィンドウは、モニターに出ていないため表示できません"})
            return
        async with self._lock:
            await self.send({"t": "mode", "mode": "window", "id": wid, "title": info["title"]})
            await self._switch_to_window(wid, region, monitor)

    async def watch_window(self) -> None:
        """表示しているウィンドウが動いたら撮り直す。閉じたら画面全体に戻す。"""
        pending: tuple | None = None
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
            monitor, region = self._locate(info["rect"])
            target = (monitor["name"] if monitor else None, region)
            current = (self.monitor["name"] if self.monitor else None, self.region)
            if region is None or target == current:
                pending = None
                continue
            if self.settings.window_capture and self.region is not None and region[2:] == self.region[2:]:
                # ウィンドウだけを撮っているときは、動かしただけなら撮り直さず、操作の座標だけを合わせる
                async with self._lock:
                    if self.window == wid:
                        self.region, self.monitor = region, monitor
                        self.backend.set_region(region)
                pending = None
                continue
            # 動かしている途中で何度も撮り直さないよう、同じ位置が2回続いたら撮り直す
            if target != pending:
                pending = target
                continue
            pending = None
            async with self._lock:
                if self.window == wid:
                    await self._switch_to_window(wid, region, monitor)


async def _close_quietly(ws: ServerConnection, code: int, reason: str) -> None:
    try:
        await ws.close(code, reason)
    except ConnectionClosed:
        pass


def make_handler(
    settings: Settings, backend, stream_factory: StreamFactory, audio_factory: AudioFactory | None = None
):
    active: dict[str, Session | None] = {"session": None}
    background: set[asyncio.Task] = set()
    cleanup: dict[str, asyncio.Task | None] = {"task": None}

    async def run_cleanup() -> None:
        """持ってきたウィンドウを元に戻し、仮想ディスプレイを無効に戻す。"""
        log.info("後片付けをします（ウィンドウを戻し、仮想ディスプレイを無効にします）")
        try:
            await asyncio.to_thread(backend.restore_brought)
        except Exception:
            log.exception("ウィンドウを元に戻せませんでした")
        try:
            await asyncio.to_thread(backend.virtual_off)
        except Exception:
            log.exception("仮想ディスプレイを無効に戻せませんでした")

    def cancel_cleanup() -> None:
        task = cleanup["task"]
        cleanup["task"] = None
        if task is not None and not task.done():
            task.cancel()

    def schedule_cleanup(delay: float) -> None:
        cancel_cleanup()

        async def later() -> None:
            await asyncio.sleep(delay)
            await run_cleanup()

        task = asyncio.create_task(later())
        cleanup["task"] = task
        background.add(task)
        task.add_done_callback(background.discard)

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
            if isinstance(raw, bytes):
                await session.files.handle_binary(raw)
                continue
            msg = protocol.loads(raw)
            if msg is not None:
                await session.handle(msg)

    async def watch_ime(ws: ServerConnection) -> None:
        last: bool | None = None
        while True:
            # 前面のアプリが固まっていても止まらないよう、別のスレッドで問い合わせる
            state = await asyncio.to_thread(backend.ime_state)
            if state is not None and state != last:
                last = state
                await ws.send(protocol.dumps({"t": "ime_state", "on": state}))
            await asyncio.sleep(IME_POLL_SEC)

    async def handler(ws: ServerConnection) -> None:
        hello = await authenticate(ws)
        if hello is None:
            return

        fps = int(_num(hello.get("fps"), settings.fps) or settings.fps)
        max_height = int(_num(hello.get("max_height"), settings.max_height) or 0)
        bitrate = int(_num(hello.get("bitrate"), settings.bitrate_kbps) or settings.bitrate_kbps)
        per_conn = Settings(
            settings.token,
            min(max(fps, 5), 120),
            min(max(bitrate, MIN_BITRATE_KBPS), MAX_BITRATE_KBPS),
            max(max_height, 0),
            settings.window_capture,
        )
        session = Session(ws, per_conn, backend, stream_factory, audio_factory)
        cancel_cleanup()  # 切れていた間に、戻す予定だったものは、続きから使う

        # 同時に1台だけ。前の接続は切る。
        # 前の接続が応答しなくても待たされないよう、閉じるのは裏で行い、後片付けだけを待つ
        # （ffmpegが2つ同時に動かないようにするため）
        previous = active["session"]
        active["session"] = session
        if previous is not None:
            previous.kick()
            task = asyncio.create_task(_close_quietly(previous.ws, 1000, "別の端末がつながりました"))
            background.add(task)
            task.add_done_callback(background.discard)
            try:
                await asyncio.wait_for(previous.finished.wait(), PREVIOUS_CLEANUP_SEC)
            except asyncio.TimeoutError:
                log.warning("前の接続の後片付けが終わらないまま、新しい接続を始めます")

        backend.set_region(None)
        tasks: list[asyncio.Task] = []
        try:
            await session.open_stream()
            w, h = session.size
            await ws.send(protocol.dumps(
                {
                    "t": "welcome", "v": protocol.VERSION, "w": w, "h": h, "codec": "h264", "mode": "screen",
                    "virtual": bool(backend.virtual_available()),
                }
            ))
            log.info("接続しました: %s（%dx%d, %dfps）", ws.remote_address, w, h, per_conn.fps)
            session.start_video()
            tasks = [
                asyncio.create_task(receive(session)),
                asyncio.create_task(watch_ime(ws)),
                asyncio.create_task(session.watch_window()),
            ]
            done, _ = await asyncio.wait(
                [*tasks, session.failed, session.kicked], return_when=asyncio.FIRST_COMPLETED
            )
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
            await session.stop_audio()
            await session.files.close()
            await session.stop_stream()
            # 押したまま切れると、PCでボタンやキーが押されたままになるので、離す
            release_held(backend, session.held)
            if active["session"] is session:
                active["session"] = None
                backend.set_region(None)
            if session.kicked.done():
                # 別の端末に代わったときは、その端末が、仮想ディスプレイとウィンドウを引き継ぐ。
                # この接続を閉じるのも待たない（裏で閉じている）
                log.info("別の端末に切り替えました")
            elif ws.close_code == 1000:
                # iPhone の「切断」ボタン。すぐ元に戻す
                await run_cleanup()
                log.info("切断しました")
            else:
                # 通信が切れた、アプリが裏に回った、など。少し待ってから戻す
                schedule_cleanup(RESTORE_GRACE_SEC)
                log.info("切れました（%d秒以内につなぎ直せば、続きから使えます）", RESTORE_GRACE_SEC)
            session.finished.set()

    return handler
