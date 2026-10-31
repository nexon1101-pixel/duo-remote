"""仮想のゲームパッド（Xbox 360）。通信の取り決めは docs/protocol.md の「ゲームパッド」。

本物（VGamepadPad）は、vgamepad と ViGEmBus が要るので、Windows でしか動かない。Linux のテストでは確認できない。
テストでは、呼ばれた内容を記録するだけの FakePad を使う。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable

log = logging.getLogger(__name__)

# 取り決めの表にあるボタンのビット（XInput の XINPUT_GAMEPAD_*）。これ以外のビットは捨てる
BUTTON_MASK = (
    0x0001 | 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0080
    | 0x0100 | 0x0200 | 0x0400 | 0x1000 | 0x2000 | 0x4000 | 0x8000
)

# 1秒に受け取る pad の上限（取り決め）
MAX_PAD_PER_SEC = 120

RumbleCallback =Callable[[float, float], None]


class PadUnavailable(Exception):
    """仮想のゲームパッドを作れない。メッセージは、人に見せる理由（日本語）。"""


# --- 値の直し方（純粋な関数）

def _number(value: Any) -> float:
    """数だけを通す。数でない値、型が違う値（真偽を含む）、NaN、inf は 0。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        f = float(value)
    except OverflowError:
        return 0.0
    return f if math.isfinite(f) else 0.0


def clean_buttons(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value & BUTTON_MASK


def clean_stick(value: Any) -> int:
    """-1.0〜1.0 に収めて、-32768〜32767 の整数に。"""
    f = max(-1.0, min(1.0, _number(value)))
    return max(-32768, min(32767, round(f * (32768 if f < 0 else 32767))))


def clean_trigger(value: Any) -> int:
    """0.0〜1.0 に収めて、0〜255 の整数に。"""
    return max(0, min(255, round(max(0.0, min(1.0, _number(value))) * 255)))


def clean_state(msg: dict[str, Any]) -> tuple[int, int, int, int, int, int, int]:
    """`pad` メッセージから、(buttons, lx, ly, rx, ry, lt, rt) を作る。省略した項目は 0。"""
    return (
        clean_buttons(msg.get("b")),
        clean_stick(msg.get("lx")), clean_stick(msg.get("ly")),
        clean_stick(msg.get("rx")), clean_stick(msg.get("ry")),
        clean_trigger(msg.get("lt")), clean_trigger(msg.get("rt")),
    )


def motor_level(value: Any) -> float:
    """モーターの強さ（0〜255）を、0.0〜1.0 にする。"""
    return max(0.0, min(1.0, _number(value) / 255))


# --- 入れ物

class VirtualPad:
    """仮想のゲームパッドの入れ物。update には、直した値（整数）を渡す。"""

    def __init__(self) -> None:
        self._rumble: RumbleCallback | None = None

    def connect(self) -> None:
        """仮想のゲームパッドを作る。作れなければ PadUnavailable。"""
        raise NotImplementedError

    def update(self, buttons: int, lx: int, ly: int, rx: int, ry: int, lt: int, rt: int) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        """全部離して、スティックとトリガーを 0 にする。"""
        raise NotImplementedError

    def close(self) -> None:
        """取り外す。"""
        raise NotImplementedError

    def set_rumble_callback(self, fn: RumbleCallback | None) -> None:
        """ゲームが振動を出したときに、fn(l, r)（どちらも 0.0〜1.0）を呼ぶ。呼ばれるスレッドは決まっていない。"""
        self._rumble = fn

    def _emit_rumble(self, large: float, small: float) -> None:
        fn = self._rumble
        if fn is not None:
            try:
                fn(large, small)
            except Exception:
                log.exception("振動の通知でエラーが出ました")


class FakePad(VirtualPad):
    """テスト用。呼ばれた内容を記録するだけ。fail に理由を入れると、connect が PadUnavailable を出す。"""

    def __init__(self, fail: str | None = None) -> None:
        super().__init__()
        self.fail = fail
        self.calls: list[tuple] = []

    def connect(self) -> None:
        if self.fail:
            raise PadUnavailable(self.fail)
        self.calls.append(("connect",))

    def update(self, buttons: int, lx: int, ly: int, rx: int, ry: int, lt: int, rt: int) -> None:
        self.calls.append(("update", buttons, lx, ly, rx, ry, lt, rt))

    def reset(self) -> None:
        self.calls.append(("reset",))

    def close(self) -> None:
        self.calls.append(("close",))

    def rumble(self, large: float, small: float) -> None:
        """ゲームが振動を出したことにする（テスト用）。"""
        self._emit_rumble(large, small)

    @property
    def updates(self) -> list[tuple]:
        return [c[1:] for c in self.calls if c[0] == "update"]


class VGamepadPad(VirtualPad):
    """本物。vgamepad（VX360Gamepad）を使う。Windows で、ViGEmBus が入っているときだけ動く（Linux では確認できない）。"""

    def __init__(self) -> None:
        super().__init__()
        self._pad: Any = None

    def connect(self) -> None:
        try:
            import vgamepad  # 使うときまで読まない
        except Exception as e:  # 入っていない、読み込めない
            raise PadUnavailable("vgamepad が入っていません") from e
        try:
            pad = vgamepad.VX360Gamepad()
        except Exception as e:  # ドライバーがないと、作るときに例外が出る
            raise PadUnavailable("ViGEmBus が入っていません") from e
        self._pad = pad
        try:
            pad.register_notification(callback_function=self._on_notification)
        except Exception:
            log.exception("振動の受け取りを登録できませんでした")

    def _on_notification(self, client, target, large_motor, small_motor, led_number, user_data) -> None:
        # vgamepad が用意した別のスレッドで呼ばれる
        self._emit_rumble(motor_level(large_motor), motor_level(small_motor))

    def update(self, buttons: int, lx: int, ly: int, rx: int, ry: int, lt: int, rt: int) -> None:
        pad = self._pad
        if pad is None:
            return
        pad.report.wButtons = buttons
        pad.left_joystick(x_value=lx, y_value=ly)
        pad.right_joystick(x_value=rx, y_value=ry)
        pad.left_trigger(value=lt)
        pad.right_trigger(value=rt)
        pad.update()

    def reset(self) -> None:
        if self._pad is not None:
            self._pad.reset()
            self._pad.update()

    def close(self) -> None:
        pad, self._pad = self._pad, None
        if pad is None:
            return
        try:
            pad.unregister_notification()
        except Exception:
            pass
        del pad  # 参照がなくなると、vgamepad が取り外す


def create_pad() -> VirtualPad:
    """本物の入れ物を作る（まだ connect していない）。"""
    return VGamepadPad()


def probe(factory: Callable[[], VirtualPad] = create_pad) -> tuple[bool, str]:
    """入れ物を作れるかを調べる。作って、すぐ取り外す。(使えるか, 使えない理由) を返す。"""
    try:
        pad = factory()
        pad.connect()
    except PadUnavailable as e:
        return False, str(e)
    except Exception as e:
        log.exception("仮想のゲームパッドを調べられませんでした")
        return False, f"調べられませんでした（{type(e).__name__}）"
    try:
        pad.close()
    except Exception:
        log.exception("調べ用の仮想のゲームパッドを取り外せませんでした")
    return True, ""
