"""PCへの入力（マウス・キーボード・文字・IME）。

Windowsでは SendInput を使う。Windows以外（テストなど）では、
呼ばれた内容を記録するだけの NullInput を使う。
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

VK_IME_ON = 0x16
VK_IME_OFF = 0x1A

# 拡張キー（矢印、Insert/Delete、Home/End、PageUp/Down、右Ctrl/Alt、Winキー、テンキーの÷など）
EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
    0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5,
}


class NullInput:
    """Windows以外・テスト用。呼ばれた内容を記録するだけ。"""

    def __init__(self, size: tuple[int, int] = (1920, 1080)) -> None:
        self.calls: list[tuple] = []
        self._size = size
        self._ime = False

    def screen_size(self) -> tuple[int, int]:
        return self._size

    def move(self, x: float, y: float) -> None:
        self.calls.append(("move", x, y))

    def button(self, b: str, down: bool, x: float | None = None, y: float | None = None) -> None:
        self.calls.append(("button", b, down, x, y))

    def scroll(self, dx: float, dy: float) -> None:
        self.calls.append(("scroll", dx, dy))

    def key(self, vk: int, down: bool) -> None:
        self.calls.append(("key", vk, down))

    def text(self, s: str) -> None:
        self.calls.append(("text", s))

    def ime_set(self, on: bool) -> None:
        self.calls.append(("ime", on))
        self._ime = on

    def ime_state(self) -> bool | None:
        return self._ime


if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
            ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
            ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wt.UINT
    user32.GetForegroundWindow.restype = wt.HWND
    user32.SendMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    user32.SendMessageW.restype = wt.LPARAM
    user32.MapVirtualKeyW.argtypes = (wt.UINT, wt.UINT)
    user32.MapVirtualKeyW.restype = wt.UINT
    imm32.ImmGetDefaultIMEWnd.argtypes = (wt.HWND,)
    imm32.ImmGetDefaultIMEWnd.restype = wt.HWND

    INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_HWHEEL = 0x1000
    _BUTTON_FLAGS = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    WM_IME_CONTROL = 0x0283
    IMC_GETOPENSTATUS = 0x0005
    SM_CXSCREEN, SM_CYSCREEN = 0, 1
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

    def _make_dpi_aware() -> None:
        # 画面の大きさを、拡大率に左右されない実際の画素数で取る
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            try:
                user32.SetProcessDPIAware()
            except (OSError, AttributeError):
                pass

    def _send(inputs: list) -> None:
        if not inputs:
            return
        arr = (INPUT * len(inputs))(*inputs)
        sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            log.warning("SendInput が一部しか通りませんでした（%d/%d）", sent, len(inputs))

    def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
        i = INPUT(type=INPUT_MOUSE)
        i.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        return i

    def _key(vk: int, scan: int, flags: int) -> INPUT:
        i = INPUT(type=INPUT_KEYBOARD)
        i.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        return i

    class WinInput:
        """画面全体（主モニター）を対象にした入力。"""

        def __init__(self) -> None:
            _make_dpi_aware()

        def screen_size(self) -> tuple[int, int]:
            return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)

        def _absolute(self, x: float, y: float) -> tuple[int, int]:
            sw, sh = self.screen_size()
            vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            vw = max(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), 2)
            vh = max(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN), 2)
            px = x * (sw - 1)  # 主モニターの左上が(0, 0)
            py = y * (sh - 1)
            return int((px - vx) * 65535 / (vw - 1)), int((py - vy) * 65535 / (vh - 1))

        def _move_input(self, x: float, y: float) -> INPUT:
            ax, ay = self._absolute(x, y)
            flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            return _mouse(flags, ax, ay)

        def move(self, x: float, y: float) -> None:
            _send([self._move_input(x, y)])

        def button(self, b: str, down: bool, x: float | None = None, y: float | None = None) -> None:
            if b not in _BUTTON_FLAGS:
                return
            inputs = []
            if x is not None and y is not None:
                inputs.append(self._move_input(x, y))
            inputs.append(_mouse(_BUTTON_FLAGS[b][0 if down else 1]))
            _send(inputs)

        def scroll(self, dx: float, dy: float) -> None:
            inputs = []
            if dy:
                inputs.append(_mouse(MOUSEEVENTF_WHEEL, data=int(dy * 120)))
            if dx:
                inputs.append(_mouse(MOUSEEVENTF_HWHEEL, data=int(dx * 120)))
            _send(inputs)

        def key(self, vk: int, down: bool) -> None:
            flags = 0 if down else KEYEVENTF_KEYUP
            if vk in EXTENDED_VKS:
                flags |= KEYEVENTF_EXTENDEDKEY
            scan = user32.MapVirtualKeyW(vk, 0)
            _send([_key(vk, scan, flags)])

        def text(self, s: str) -> None:
            units = s.encode("utf-16-le")
            inputs = []
            for i in range(0, len(units), 2):
                code = units[i] | (units[i + 1] << 8)
                inputs.append(_key(0, code, KEYEVENTF_UNICODE))
                inputs.append(_key(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            _send(inputs)

        def ime_set(self, on: bool) -> None:
            vk = VK_IME_ON if on else VK_IME_OFF
            _send([_key(vk, 0, 0), _key(vk, 0, KEYEVENTF_KEYUP)])

        def ime_state(self) -> bool | None:
            """前面のウィンドウのIMEがオンか。取れなければNone（環境によっては不正確）。"""
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            ime = imm32.ImmGetDefaultIMEWnd(hwnd)
            if not ime:
                return None
            return bool(user32.SendMessageW(ime, WM_IME_CONTROL, IMC_GETOPENSTATUS, 0))


def create_input():
    if sys.platform == "win32":
        return WinInput()
    log.warning("Windowsではないため、入力は記録するだけです（NullInput）")
    return NullInput()
