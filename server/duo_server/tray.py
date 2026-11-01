"""タスクトレイ（通知エリア）のアイコン。管理画面（panel.py）のプログラムの中で動く。

アイコンは、サーバーの状態の色の丸。左クリック（ダブルクリック）で管理画面を開き、右クリックのメニューで、
サーバーの起動、停止、再起動、仮想ディスプレイを無効にする、管理画面の終了ができる。
pystray と Pillow は Windows でだけ入れるので、アイコンを作る関数の中で読み込む（Linux のテストでは読み込まない）。
状態の文、色、メニューの項目は、Windows がなくても確認できるよう、画面の部品とは分けてある。
実際のアイコンは、Windows の実機でしか確認できない。
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("duo.tray")

# サーバーの状態
STOPPED, STARTING, RUNNING, ERROR = "stopped", "starting", "running", "error"
# 起動してから、この秒数のあいだは「起動中」とする
STARTING_SECONDS = 3.0
# サーバーの状態を調べる間隔（秒）
POLL_SECONDS = 2.0

COLORS = {
    RUNNING: (46, 160, 67),    # 緑
    STOPPED: (140, 140, 140),  # 灰色
    STARTING: (230, 190, 40),  # 黄色
    ERROR: (215, 58, 73),      # 赤
}

# メニューの項目を押したときの動き（名前）。Tray に渡す actions の鍵と同じ
OPEN, START, STOP, RESTART, VIRTUAL_OFF, QUIT = "open", "start", "stop", "restart", "virtual_off", "quit"


@dataclass(frozen=True)
class TrayState:
    kind: str = STOPPED
    iphone: bool | None = None  # iPhone がつながっているか。わからなければ None


@dataclass(frozen=True)
class MenuItem:
    label: str = ""
    enabled: bool = True
    action: str | None = None
    default: bool = False  # アイコンを左クリックしたときの項目
    separator: bool = False


def status_text(state: TrayState) -> str:
    """ツールチップの文。"""
    if state.kind == RUNNING:
        text = "DuoRemote サーバー: 実行中"
        if state.iphone:
            text += "（iPhone 接続中）"
        return text
    if state.kind == STARTING:
        return "DuoRemote サーバー: 起動中"
    if state.kind == ERROR:
        return "DuoRemote サーバー: 停止中（異常で止まりました）"
    return "DuoRemote サーバー: 停止中"


def state_color(state: TrayState) -> tuple[int, int, int]:
    return COLORS.get(state.kind, COLORS[STOPPED])


def menu_items(state: TrayState, windows: bool) -> list[MenuItem]:
    """メニューの項目の一覧。windows が False のとき、仮想ディスプレイの項目は入れない。"""
    running = state.kind in (RUNNING, STARTING)
    items = [
        MenuItem("管理画面を開く", action=OPEN, default=True),
        MenuItem(separator=True),
        MenuItem("サーバーを停止", action=STOP) if running else MenuItem("サーバーを起動", action=START),
        MenuItem("サーバーを再起動", enabled=state.kind == RUNNING, action=RESTART),
        MenuItem(separator=True),
    ]
    if windows:
        items += [MenuItem("仮想ディスプレイを無効にする", action=VIRTUAL_OFF), MenuItem(separator=True)]
    items.append(MenuItem("管理画面を終了", action=QUIT))
    return items


def state_of(process: Any, now: float | None = None) -> TrayState:
    """ServerProcess（または同じ形のもの）から、トレイの状態を作る。
    iPhone がつながっているかは、制御用の入り口の status 命令では取れないので、省略する。"""
    now = time.time() if now is None else now
    if process.running:
        started = process.started_at
        if started is not None and now - started < STARTING_SECONDS:
            return TrayState(STARTING)
        return TrayState(RUNNING)
    if process.last_exit not in (None, 0):
        return TrayState(ERROR)
    return TrayState(STOPPED)


def draw_icon(state: TrayState):
    """64x64 のアイコンを描く。透明な背景に、状態の色の丸（縁は濃い色）、真ん中に白い小さなモニター。"""
    from PIL import Image, ImageDraw  # Windows でだけ入れる

    r, g, b = state_color(state)
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.ellipse((2, 2, 61, 61), fill=(r, g, b, 255), outline=(r // 2, g // 2, b // 2, 255), width=3)
    d.rectangle((18, 20, 45, 37), fill=(255, 255, 255, 255))  # 画面
    d.rectangle((29, 38, 34, 43), fill=(255, 255, 255, 255))  # 首
    d.rectangle((23, 43, 40, 46), fill=(255, 255, 255, 255))  # 足
    return image


class Tray:
    """タスクトレイのアイコン。get_state は今のサーバーの状態を返す。actions は、メニューの動き（名前 -> 呼ぶもの）。
    呼ぶものが文字を返したら、通知で見せる。Windows 以外では、start() は何もしない。"""

    def __init__(self, get_state: Callable[[], TrayState], actions: dict[str, Callable[[], Any]]) -> None:
        self.get_state = get_state
        self.actions = actions
        self._icon: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last: TrayState | None = None

    @staticmethod
    def supported() -> bool:
        return sys.platform == "win32"

    @property
    def active(self) -> bool:
        return self._icon is not None

    def start(self) -> None:
        with self._lock:
            if self._icon is not None:
                return
            if not self.supported():
                log.info("タスクトレイのアイコンは、Windows でだけ出ます")
                return
            try:
                import pystray  # Windows でだけ入れる

                state = self.get_state()
                icon = pystray.Icon("duo-remote", draw_icon(state), status_text(state), self._build_menu(pystray, state))
                icon.run_detached()
            except Exception as e:  # noqa: BLE001 アイコンが出せなくても、管理画面は動かす
                log.warning("タスクトレイのアイコンを出せませんでした（%s）", type(e).__name__)
                return
            self._icon = icon
            self._last = state
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._watch, args=(self._stop,), daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            icon, self._icon = self._icon, None
            self._stop.set()
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def update(self) -> None:
        """状態が変わっていれば、絵とツールチップとメニューを変える。"""
        with self._lock:
            icon = self._icon
            if icon is None:
                return
            state = self.get_state()
            if state == self._last:
                return
            self._last = state
            try:
                import pystray

                icon.icon = draw_icon(state)
                icon.title = status_text(state)
                icon.menu = self._build_menu(pystray, state)
                icon.update_menu()
            except Exception as e:  # noqa: BLE001
                log.warning("タスクトレイのアイコンを更新できませんでした（%s）", type(e).__name__)

    def _watch(self, stop: threading.Event) -> None:
        while not stop.wait(POLL_SECONDS):
            self.update()

    def _build_menu(self, pystray: Any, state: TrayState):
        entries = []
        for item in menu_items(state, self.supported()):
            if item.separator:
                entries.append(pystray.Menu.SEPARATOR)
            else:
                entries.append(pystray.MenuItem(
                    item.label, self._callback(item.action), enabled=item.enabled, default=item.default,
                ))
        return pystray.Menu(*entries)

    def _callback(self, name: str | None):
        def run(*_: Any) -> None:
            # 時間のかかる動き（サーバーを止める、など）で、メニューの動きを止めないよう、別のスレッドで呼ぶ
            threading.Thread(target=self._invoke, args=(name,), daemon=True).start()

        return run

    def _invoke(self, name: str | None) -> None:
        fn = self.actions.get(name or "")
        if fn is None:
            return
        try:
            message = fn()
        except Exception as e:  # noqa: BLE001
            message = f"できませんでした（{type(e).__name__}）"
        if isinstance(message, str) and message and self._icon is not None:
            try:
                self._icon.notify(message, "DuoRemote")
            except Exception:  # noqa: BLE001
                pass
        self.update()
