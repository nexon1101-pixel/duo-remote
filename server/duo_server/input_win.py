"""PCへの入力（マウス・キーボード・文字・IME）と、ウィンドウの操作。

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
        self.region: tuple[int, int, int, int] | None = None
        # テストで使うウィンドウ。id -> {"title", "app", "rect": (x, y, 幅, 高さ), "minimized"}
        self.windows: dict[int, dict] = {}

    def screen_size(self) -> tuple[int, int]:
        return self._size

    def set_region(self, region: tuple[int, int, int, int] | None) -> None:
        self.region = region

    def list_windows(self) -> list[dict]:
        return [{"id": i, "title": w["title"], "app": w.get("app", "")} for i, w in self.windows.items()]

    def window_info(self, wid: int) -> dict | None:
        w = self.windows.get(wid)
        if w is None:
            return None
        return {"title": w["title"], "rect": w["rect"], "minimized": w.get("minimized", False)}

    def focus_window(self, wid: int) -> bool:
        self.calls.append(("focus", wid))
        w = self.windows.get(wid)
        if w is not None:
            w["minimized"] = False
        return w is not None

    def fit_window(self, wid: int, w: float, h: float) -> bool:
        self.calls.append(("fit", wid, w, h))
        return wid in self.windows

    def move(self, x: float, y: float) -> None:
        self.calls.append(("move", x, y))

    def button(self, b: str, down: bool, x: float | None = None, y: float | None = None) -> None:
        self.calls.append(("button", b, down, x, y))

    def scroll(self, dx: float, dy: float) -> None:
        self.calls.append(("scroll", dx, dy))

    def key(self, vk: int, down: bool) -> None:
        self.calls.append(("key", vk, down))

    def key_scan(self, sc: int, down: bool) -> None:
        self.calls.append(("scan", sc, down))

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

    dwmapi = ctypes.WinDLL("dwmapi")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.EnumWindows.argtypes = (WNDENUMPROC, wt.LPARAM)
    user32.IsWindow.argtypes = (wt.HWND,)
    user32.IsWindowVisible.argtypes = (wt.HWND,)
    user32.IsIconic.argtypes = (wt.HWND,)
    user32.IsZoomed.argtypes = (wt.HWND,)
    user32.GetWindow.argtypes = (wt.HWND, wt.UINT)
    user32.GetWindow.restype = wt.HWND
    user32.GetWindowLongW.argtypes = (wt.HWND, ctypes.c_int)
    user32.GetWindowLongW.restype = wt.LONG
    user32.GetWindowTextLengthW.argtypes = (wt.HWND,)
    user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
    user32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
    user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
    user32.ShowWindow.argtypes = (wt.HWND, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (wt.HWND,)
    user32.SetWindowPos.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
    user32.SystemParametersInfoW.argtypes = (wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT)
    dwmapi.DwmGetWindowAttribute.argtypes = (wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
    kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    kernel32.CloseHandle.argtypes = (wt.HANDLE,)

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
    KEYEVENTF_SCANCODE = 0x0008
    WM_IME_CONTROL = 0x0283
    IMC_GETOPENSTATUS = 0x0005
    SM_CXSCREEN, SM_CYSCREEN = 0, 1
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    VK_MENU = 0x12
    GW_OWNER = 4
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    DWMWA_CLOAKED = 14
    SW_RESTORE = 9
    SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
    SPI_GETWORKAREA = 0x0030
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

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

    def _visible_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        """ウィンドウの見えている範囲（x, y, 幅, 高さ）。影や見えない枠は含めない。"""
        r = wt.RECT()
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return None
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def _window_title(hwnd: int) -> str:
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _app_name(hwnd: int) -> str:
        """ウィンドウを出しているアプリの実行ファイル名（notepad.exe など）。"""
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not proc:
            return ""
        try:
            size = wt.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
                return ""
            return buf.value.rsplit("\\", 1)[-1]
        finally:
            kernel32.CloseHandle(proc)

    def _is_listable(hwnd: int) -> bool:
        """タスクバーに出るような、普通のウィンドウか。"""
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return False
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
            return False
        cloaked = wt.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:  # 別の仮想デスクトップや、裏で止まっているストアアプリ
            return False
        return user32.GetWindowTextLengthW(hwnd) > 0

    class WinInput:
        """主モニターを対象にした入力。set_region で範囲を決めると、座標はその範囲が基準になる。"""

        def __init__(self) -> None:
            _make_dpi_aware()
            self.region: tuple[int, int, int, int] | None = None

        def screen_size(self) -> tuple[int, int]:
            return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)

        def set_region(self, region: tuple[int, int, int, int] | None) -> None:
            self.region = region

        def _absolute(self, x: float, y: float) -> tuple[int, int]:
            if self.region is not None:
                rx, ry, rw, rh = self.region
            else:
                rx, ry = 0, 0  # 主モニターの左上が(0, 0)
                rw, rh = self.screen_size()
            vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            vw = max(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), 2)
            vh = max(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN), 2)
            px = rx + x * (rw - 1)
            py = ry + y * (rh - 1)
            return int((px - vx) * 65535 / (vw - 1)), int((py - vy) * 65535 / (vh - 1))

        # --- ウィンドウ

        def list_windows(self) -> list[dict]:
            found: list[int] = []

            @WNDENUMPROC
            def collect(hwnd, _):
                if hwnd and _is_listable(hwnd):
                    found.append(hwnd)
                return True

            user32.EnumWindows(collect, 0)
            # EnumWindowsは手前のウィンドウから順に返すので、その順のまま並べる
            return [{"id": h, "title": _window_title(h), "app": _app_name(h)} for h in found]

        def window_info(self, wid: int) -> dict | None:
            if not user32.IsWindow(wid):
                return None
            rect = _visible_rect(wid)
            if rect is None:
                return None
            return {"title": _window_title(wid), "rect": rect, "minimized": bool(user32.IsIconic(wid))}

        def focus_window(self, wid: int) -> bool:
            if not user32.IsWindow(wid):
                return False
            if user32.IsIconic(wid):
                user32.ShowWindow(wid, SW_RESTORE)
            if user32.SetForegroundWindow(wid):
                return True
            # Windowsは、他のアプリが勝手に前に出るのを止めることがある。
            # 直前にキー入力があったことにすると通るため、Altを押して離してから、もう一度試す
            _send([_key(VK_MENU, 0, 0), _key(VK_MENU, 0, KEYEVENTF_KEYUP)])
            return bool(user32.SetForegroundWindow(wid))

        def fit_window(self, wid: int, w: float, h: float) -> bool:
            """ウィンドウの大きさを、w x h（iPhoneの画面のポイント数）に合わせる。
            1ポイントをWindowsの1論理画素（拡大率をかけた画素）にするので、文字はPCで見るのと同じくらいの大きさになる。
            作業領域に入らないときは、縦横比を保って縮める。"""
            if not user32.IsWindow(wid) or w <= 0 or h <= 0:
                return False
            if user32.IsIconic(wid) or user32.IsZoomed(wid):
                user32.ShowWindow(wid, SW_RESTORE)
            get_dpi = getattr(user32, "GetDpiForWindow", None)  # Windows 10 1607 から
            scale = 1.0
            if get_dpi is not None:
                get_dpi.argtypes = (wt.HWND,)
                scale = (get_dpi(wid) or 96) / 96
            work = wt.RECT()
            user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
            ww, wh = work.right - work.left, work.bottom - work.top
            tw, th = w * scale, h * scale
            shrink = min(1.0, ww / tw, wh / th)
            tw, th = int(tw * shrink), int(th * shrink)

            # SetWindowPosの大きさには、見えない枠も含まれる。その分を足す
            outer = wt.RECT()
            visible = _visible_rect(wid)
            if visible is None or not user32.GetWindowRect(wid, ctypes.byref(outer)):
                return False
            vx, vy, vw, vh = visible
            pad_l, pad_t = vx - outer.left, vy - outer.top
            pad_r, pad_b = outer.right - (vx + vw), outer.bottom - (vy + vh)
            # 作業領域の中央に置く
            x = work.left + (ww - tw) // 2 - pad_l
            y = work.top + (wh - th) // 2 - pad_t
            return bool(user32.SetWindowPos(
                wid, None, x, y, tw + pad_l + pad_r, th + pad_t + pad_b, SWP_NOZORDER | SWP_NOACTIVATE,
            ))

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

        def key_scan(self, sc: int, down: bool) -> None:
            """キーの位置（スキャンコード）で押す。どの文字になるかは、PCのキー配列で決まる。"""
            flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
            if sc >> 8 == 0xE0:
                flags |= KEYEVENTF_EXTENDEDKEY
            _send([_key(0, sc & 0xFF, flags)])

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
