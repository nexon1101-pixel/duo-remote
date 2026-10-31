"""PCへの入力（マウス・キーボード・文字・IME）と、ウィンドウの操作。

Windowsでは SendInput を使う。Windows以外（テストなど）では、
呼ばれた内容を記録するだけの NullInput を使う。
"""
from __future__ import annotations

import logging
import sys
import time

from .virtual_display import VirtualDisplay, VirtualDisplayError, list_monitors

log = logging.getLogger(__name__)

VK_IME_ON = 0x16
VK_IME_OFF = 0x1A

# 拡張キー（矢印、Insert/Delete、Home/End、PageUp/Down、右Ctrl/Alt、Winキー、テンキーの÷など）
EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
    0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5,
}


class ClipboardError(Exception):
    """PCのクリップボードを読み書きできなかった。"""


def to_windows_newlines(s: str) -> str:
    """改行を、Windowsのクリップボードの決まり（CRLF）にそろえる。"""
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def from_windows_newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


class _BringMixin:
    """ウィンドウを仮想モニターに持ってきて、あとで元の位置に戻す。
    持ってくる前の位置は、持ってくるたびに上書きせず、最初の1回だけ覚える。"""

    _brought: dict

    def bring_window(self, wid: int, rect: tuple[int, int, int, int]) -> bool:
        """ウィンドウを rect（x, y, 幅, 高さ。デスクトップ上の位置）に移す。"""
        if wid not in self._brought:
            placement = self.window_placement(wid)
            if placement is None:
                return False
            self._brought[wid] = placement
        return self.place_window(wid, rect)

    def restore_brought(self) -> list[int]:
        """持ってきたウィンドウを、すべて元の位置に戻す。戻したウィンドウの番号を返す。"""
        restored = []
        for wid, placement in list(self._brought.items()):
            try:
                self.restore_window(wid, placement)
                restored.append(wid)
            except Exception:  # 閉じられたウィンドウなど。ほかは続ける
                log.exception("ウィンドウを元に戻せませんでした: %s", wid)
        self._brought.clear()
        return restored

    def brought_windows(self) -> list[int]:
        return list(self._brought)

    def shutdown(self) -> None:
        """サーバーを止めるとき。持ってきたウィンドウを戻し、仮想ディスプレイを無効に戻す。"""
        self.restore_brought()
        try:
            self.virtual_off()
        except VirtualDisplayError as e:
            log.warning("仮想ディスプレイを無効に戻せませんでした: %s", e)


class NullInput(_BringMixin):
    """Windows以外・テスト用。呼ばれた内容を記録するだけ。"""

    def __init__(self, size: tuple[int, int] = (1920, 1080)) -> None:
        self.calls: list[tuple] = []
        self._size = size
        self._ime = False
        self.region: tuple[int, int, int, int] | None = None
        # テストで使うウィンドウ。id -> {"title", "app", "rect": (x, y, 幅, 高さ), "minimized"}
        self.windows: dict[int, dict] = {}
        self._brought: dict = {}
        # 仮想ディスプレイ。installed が True のとき、virtual_on で、主モニターの右に仮想モニターが出る
        self.virtual_installed = False
        self.virtual_enabled = False
        self.virtual_error: str | None = None  # 設定すると、virtual_on が失敗する
        self._virtual_width = 1536
        self.clipboard = ""
        self.keep_awake_calls: list[bool] = []  # keep_awake に渡された値の記録
        # クリップボードの、ファイルと画像（テスト用）。画像は ("png", PNGのバイト列) か ("dib", DIBのバイト列)
        self.clipboard_files: list[str] = []
        self.clipboard_image: tuple[str, bytes] | None = None
        # ファイルの保存先。Noneなら、files.default_download_dir()
        self.download_dir = None

    def screen_size(self) -> tuple[int, int]:
        return self._size

    def set_region(self, region: tuple[int, int, int, int] | None) -> None:
        self.region = region

    def monitors(self) -> list[dict]:
        w, h = self._size
        found = [{
            "name": "\\\\.\\DISPLAY1", "rect": (0, 0, w, h), "work": (0, 0, w, h - 40),
            "primary": True, "virtual": False, "adapter": 0, "output": 0, "description": "",
        }]
        if self.virtual_enabled:
            vw = self._virtual_width
            found.append({
                "name": "\\\\.\\DISPLAY2", "rect": (w, 0, vw, 1080), "work": (w, 0, vw, 1040),
                "primary": False, "virtual": True, "adapter": 0, "output": 1, "description": "Virtual Display Driver",
            })
        return found

    def virtual_available(self) -> bool:
        return self.virtual_installed

    def virtual_on(self) -> dict:
        self.calls.append(("virtual_on",))
        if not self.virtual_installed:
            raise VirtualDisplayError("仮想ディスプレイのドライバーが見つかりません")
        if self.virtual_error:
            raise VirtualDisplayError(self.virtual_error)
        self.virtual_enabled = True
        return self.monitors()[-1]

    def virtual_off(self) -> None:
        if self.virtual_enabled:
            self.calls.append(("virtual_off",))
        self.virtual_enabled = False

    def virtual_cleanup_leftover(self) -> bool:
        """有効のまま残っている仮想ディスプレイを、無効に戻す。戻したときは True。"""
        was = self.virtual_enabled
        self.virtual_off()
        return was

    def virtual_set_aspect(self, aspect: float) -> dict:
        self.calls.append(("virtual_aspect", round(aspect, 3)))
        self._virtual_width = round(1080 * aspect / 2) * 2
        return self.monitors()[-1]

    def window_placement(self, wid: int) -> dict | None:
        w = self.windows.get(wid)
        return None if w is None else {"rect": w["rect"], "minimized": w.get("minimized", False)}

    def place_window(self, wid: int, rect: tuple[int, int, int, int]) -> bool:
        self.calls.append(("place", wid, rect))
        if wid not in self.windows:
            return False
        self.windows[wid]["rect"] = rect
        return True

    def restore_window(self, wid: int, placement: dict) -> None:
        self.calls.append(("restore", wid))
        if wid in self.windows:
            self.windows[wid]["rect"] = placement["rect"]

    def list_windows(self) -> list[dict]:
        return [
            {"id": i, "title": w["title"], "app": w.get("app", ""), "rect": w["rect"]}
            for i, w in self.windows.items()
        ]

    def window_info(self, wid: int) -> dict | None:
        w = self.windows.get(wid)
        if w is None:
            return None
        return {"title": w["title"], "rect": w["rect"], "minimized": w.get("minimized", False)}

    def window_thumb_raw(self, wid: int, max_w: int) -> tuple[int, int, bytes] | None:
        """テスト用。ウィンドウの "thumb" に、(幅, 高さ, BGRA) を入れておくと、それを返す。なければ None"""
        self.calls.append(("thumb", wid, max_w))
        w = self.windows.get(wid)
        return None if w is None else w.get("thumb")

    def focus_window(self, wid: int) -> bool:
        self.calls.append(("focus", wid))
        w = self.windows.get(wid)
        if w is not None:
            w["minimized"] = False
        return w is not None

    def repaint_window(self, wid: int) -> None:
        self.calls.append(("repaint", wid))

    def fit_window(self, wid: int, w: float, h: float, area: tuple[int, int, int, int] | None = None) -> bool:
        self.calls.append(("fit", wid, w, h))
        return wid in self.windows

    def move(self, x: float, y: float) -> None:
        self.calls.append(("move", x, y))

    def move_rel(self, dx: float, dy: float) -> None:
        self.calls.append(("move_rel", dx, dy))

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

    def clip_get(self) -> str:
        return self.clipboard

    def clip_set(self, s: str) -> None:
        self.calls.append(("clip_set", s))
        self.clipboard = s
        self.clipboard_files, self.clipboard_image = [], None

    def clip_files(self) -> list[str]:
        return list(self.clipboard_files)

    def clip_image(self) -> tuple[str, bytes] | None:
        return self.clipboard_image

    def clip_set_files(self, paths: list[str]) -> None:
        self.calls.append(("clip_set_files", list(paths)))
        self.clipboard, self.clipboard_image = "", None
        self.clipboard_files = list(paths)

    def clip_set_image(self, path: str) -> None:
        self.calls.append(("clip_set_image", path))
        with open(path, "rb") as f:
            data = f.read()
        self.clipboard, self.clipboard_files = "", []
        self.clipboard_image = ("png", data)

    def keep_awake(self, on: bool) -> None:
        self.keep_awake_calls.append(on)  # calls には入れない（入力の記録と分けておく）


def is_blank_bgra(data: bytes) -> bool:
    """画素が、全部ほぼ真っ黒か（ウィンドウの中身が取れなかったときの目印）。間引いて調べる"""
    step = max(4, (len(data) // 4096) // 4 * 4)
    return not any(data[i] > 8 or data[i + 1] > 8 or data[i + 2] > 8 for i in range(0, len(data) - 3, step))


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
    user32.SendMessageTimeoutW.argtypes = (
        wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t),
    )
    user32.SendMessageTimeoutW.restype = wt.LPARAM
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wt.POINT),)
    user32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
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
    user32.RedrawWindow.argtypes = (wt.HWND, ctypes.c_void_p, wt.HRGN, wt.UINT)
    user32.SetWindowPos.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
    user32.SystemParametersInfoW.argtypes = (wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT)
    dwmapi.DwmGetWindowAttribute.argtypes = (wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
    kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    kernel32.CloseHandle.argtypes = (wt.HANDLE,)
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
    kernel32.SetThreadExecutionState.argtypes = (wt.DWORD,)
    kernel32.SetThreadExecutionState.restype = wt.DWORD
    user32.OpenClipboard.argtypes = (wt.HWND,)
    user32.GetClipboardData.argtypes = (wt.UINT,)
    user32.GetClipboardData.restype = wt.HANDLE
    user32.SetClipboardData.argtypes = (wt.UINT, wt.HANDLE)
    user32.SetClipboardData.restype = wt.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = (wt.UINT,)
    user32.CreateWindowExW.argtypes = (
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
    )
    user32.CreateWindowExW.restype = wt.HWND
    user32.DestroyWindow.argtypes = (wt.HWND,)
    kernel32.GlobalAlloc.argtypes = (wt.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalLock.argtypes = (wt.HGLOBAL,)
    kernel32.GlobalLock.restype = wt.LPVOID
    kernel32.GlobalUnlock.argtypes = (wt.HGLOBAL,)
    kernel32.GlobalFree.argtypes = (wt.HGLOBAL,)
    kernel32.GlobalFree.restype = wt.HGLOBAL

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
    SMTO_BLOCK, SMTO_ABORTIFHUNG = 0x0001, 0x0002
    IME_QUERY_TIMEOUT_MS = 200
    # デスクトップそのもの（壁紙とアイコンの層）。一覧には出さない
    DESKTOP_CLASSES = {"Progman", "WorkerW"}
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
    RDW_INVALIDATE, RDW_ERASE, RDW_FRAME, RDW_ALLCHILDREN, RDW_UPDATENOW = 0x0001, 0x0004, 0x0400, 0x0080, 0x0100
    SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
    SWP_NOSIZE, SWP_NOMOVE, SWP_FRAMECHANGED = 0x0001, 0x0002, 0x0020
    SPI_GETWORKAREA = 0x0030
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    HWND_MESSAGE = wt.HWND(-3)
    CLIPBOARD_TRIES = 10  # 他のアプリが開いていることがあるので、少し待って何度か試す

    def _open_clipboard(hwnd) -> None:
        for _ in range(CLIPBOARD_TRIES):
            if user32.OpenClipboard(hwnd):
                return
            time.sleep(0.05)
        raise ClipboardError("PCのクリップボードを、他のアプリが使っています")

    def _clip_get() -> str:
        _open_clipboard(None)
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def _clip_set(s: str) -> None:
        data = (s.replace("\0", "") + "\0").encode("utf-16-le")
        # 持ち主のウィンドウがないと、SetClipboardData が失敗することがあるので、見えないウィンドウを作る
        owner = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
        try:
            _open_clipboard(owner)
            try:
                user32.EmptyClipboard()
                mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not mem:
                    raise ClipboardError("PCのクリップボードに入れられませんでした（メモリ）")
                ptr = kernel32.GlobalLock(mem)
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(mem)
                if not user32.SetClipboardData(CF_UNICODETEXT, mem):
                    kernel32.GlobalFree(mem)  # 渡せなかったときだけ、自分で消す
                    raise ClipboardError("PCのクリップボードに入れられませんでした")
            finally:
                user32.CloseClipboard()
        finally:
            if owner:
                user32.DestroyWindow(owner)


    CF_DIB = 8
    CF_HDROP = 15
    user32.RegisterClipboardFormatW.argtypes = (wt.LPCWSTR,)
    user32.RegisterClipboardFormatW.restype = wt.UINT
    user32.GetDC.argtypes = (wt.HWND,)
    user32.GetDC.restype = wt.HDC
    user32.ReleaseDC.argtypes = (wt.HWND, wt.HDC)
    kernel32.GlobalSize.argtypes = (wt.HGLOBAL,)
    kernel32.GlobalSize.restype = ctypes.c_size_t

    def _clip_read_format(fmt: int) -> bytes | None:
        """開いているクリップボードから、形式 fmt のデータをバイト列で読む。"""
        if not user32.IsClipboardFormatAvailable(fmt):
            return None
        handle = user32.GetClipboardData(fmt)
        if not handle:
            return None
        size = kernel32.GlobalSize(handle)
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(handle)

    def _clip_files() -> list[str]:
        _open_clipboard(None)
        try:
            raw = _clip_read_format(CF_HDROP)
        finally:
            user32.CloseClipboard()
        from .files import parse_hdrop

        return parse_hdrop(raw) if raw else []

    def _clip_image() -> tuple[str, bytes] | None:
        png_format = user32.RegisterClipboardFormatW("PNG")
        _open_clipboard(None)
        try:
            png = _clip_read_format(png_format) if png_format else None
            if png:
                return "png", png
            dib = _clip_read_format(CF_DIB)
            return ("dib", dib) if dib else None
        finally:
            user32.CloseClipboard()

    def _clip_put(items: list[tuple[int, bytes]]) -> None:
        """クリップボードを空にして、(形式, データ) を入れる。"""
        owner = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
        try:
            _open_clipboard(owner)
            try:
                user32.EmptyClipboard()
                for fmt, data in items:
                    mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                    if not mem:
                        raise ClipboardError("PCのクリップボードに入れられませんでした（メモリ）")
                    ptr = kernel32.GlobalLock(mem)
                    ctypes.memmove(ptr, data, len(data))
                    kernel32.GlobalUnlock(mem)
                    if not user32.SetClipboardData(fmt, mem):
                        kernel32.GlobalFree(mem)
                        raise ClipboardError("PCのクリップボードに入れられませんでした")
            finally:
                user32.CloseClipboard()
        finally:
            if owner:
                user32.DestroyWindow(owner)

    def _clip_set_files(paths: list[str]) -> None:
        from .files import build_hdrop

        _clip_put([(CF_HDROP, build_hdrop(paths))])

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    class _GdiplusStartupInput(ctypes.Structure):
        _fields_ = [
            ("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
            ("SuppressBackgroundThread", wt.BOOL), ("SuppressExternalCodecs", wt.BOOL),
        ]

    def _window_thumb_raw(hwnd: int, max_w: int) -> tuple[int, int, bytes] | None:
        """ウィンドウの中身を、横 max_w ピクセル以内に縮めて、(幅, 高さ, 上から順の BGRA) で返す。
        最小化している、大きさがない、中身が真っ黒（GPU で描くウィンドウで、取れなかった）ときは None。"""
        c_vp = ctypes.c_void_p
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi32.CreateCompatibleDC.argtypes = (c_vp,)
        gdi32.CreateCompatibleDC.restype = c_vp
        gdi32.CreateCompatibleBitmap.argtypes = (c_vp, ctypes.c_int, ctypes.c_int)
        gdi32.CreateCompatibleBitmap.restype = c_vp
        gdi32.SelectObject.argtypes = (c_vp, c_vp)
        gdi32.SelectObject.restype = c_vp
        gdi32.DeleteObject.argtypes = (c_vp,)
        gdi32.DeleteDC.argtypes = (c_vp,)
        gdi32.SetStretchBltMode.argtypes = (c_vp, ctypes.c_int)
        gdi32.SetBrushOrgEx.argtypes = (c_vp, ctypes.c_int, ctypes.c_int, c_vp)
        gdi32.StretchBlt.argtypes = (
            c_vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            c_vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD,
        )
        gdi32.GetDIBits.argtypes = (c_vp, c_vp, wt.UINT, wt.UINT, c_vp, c_vp, wt.UINT)
        user32.GetDC.argtypes = (c_vp,)
        user32.GetDC.restype = c_vp
        user32.ReleaseDC.argtypes = (c_vp, c_vp)
        user32.PrintWindow.argtypes = (wt.HWND, c_vp, wt.UINT)

        if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
            return None
        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0 or w * h > 8192 * 8192:
            return None
        tw = max(1, min(max_w, w))
        th = max(1, round(h * tw / w))
        screen = user32.GetDC(None)
        src_dc = dst_dc = None
        src_bmp = dst_bmp = None
        try:
            src_dc = gdi32.CreateCompatibleDC(screen)
            src_bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
            dst_dc = gdi32.CreateCompatibleDC(screen)
            dst_bmp = gdi32.CreateCompatibleBitmap(screen, tw, th)
            if not (src_dc and src_bmp and dst_dc and dst_bmp):
                return None
            old_src = gdi32.SelectObject(src_dc, src_bmp)
            old_dst = gdi32.SelectObject(dst_dc, dst_bmp)
            try:
                # 2 = PW_RENDERFULLCONTENT。DWM で描くウィンドウ（ブラウザなど）も、中身を取る
                if not user32.PrintWindow(hwnd, src_dc, 2):
                    return None
                gdi32.SetStretchBltMode(dst_dc, 4)  # HALFTONE
                gdi32.SetBrushOrgEx(dst_dc, 0, 0, None)
                if not gdi32.StretchBlt(dst_dc, 0, 0, tw, th, src_dc, 0, 0, w, h, 0x00CC0020):  # SRCCOPY
                    return None
            finally:
                gdi32.SelectObject(src_dc, old_src)
                gdi32.SelectObject(dst_dc, old_dst)
            info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), tw, -th, 1, 32, 0, tw * th * 4, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(tw * th * 4)
            if not gdi32.GetDIBits(dst_dc, dst_bmp, 0, th, pixels, ctypes.byref(info), 0):
                return None
            data = pixels.raw
        finally:
            for bmp in (src_bmp, dst_bmp):
                if bmp:
                    gdi32.DeleteObject(bmp)
            for dc in (src_dc, dst_dc):
                if dc:
                    gdi32.DeleteDC(dc)
            user32.ReleaseDC(None, screen)
        if is_blank_bgra(data):
            return None
        return tw, th, data

    def _image_to_dib(path: str) -> bytes | None:
        """画像ファイル（PNG、JPEG など）を、GDI+ で読んで、CF_DIB の形（32ビット）にする。できなければ None。"""
        gdiplus = ctypes.WinDLL("gdiplus")
        gdi32 = ctypes.WinDLL("gdi32")
        c_vp = ctypes.c_void_p
        gdiplus.GdiplusStartup.argtypes = (ctypes.POINTER(c_vp), ctypes.POINTER(_GdiplusStartupInput), c_vp)
        gdiplus.GdiplusShutdown.argtypes = (c_vp,)
        gdiplus.GdipCreateBitmapFromFile.argtypes = (wt.LPCWSTR, ctypes.POINTER(c_vp))
        gdiplus.GdipGetImageWidth.argtypes = (c_vp, ctypes.POINTER(wt.UINT))
        gdiplus.GdipGetImageHeight.argtypes = (c_vp, ctypes.POINTER(wt.UINT))
        gdiplus.GdipCreateHBITMAPFromBitmap.argtypes = (c_vp, ctypes.POINTER(c_vp), wt.DWORD)
        gdiplus.GdipDisposeImage.argtypes = (c_vp,)
        gdi32.GetDIBits.argtypes = (wt.HDC, c_vp, wt.UINT, wt.UINT, c_vp, c_vp, wt.UINT)
        gdi32.DeleteObject.argtypes = (c_vp,)
        token = c_vp()
        startup = _GdiplusStartupInput(1, None, False, False)
        if gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(startup), None) != 0:
            return None
        bitmap, hbitmap = c_vp(), c_vp()
        try:
            if gdiplus.GdipCreateBitmapFromFile(path, ctypes.byref(bitmap)) != 0:
                return None
            width, height = wt.UINT(), wt.UINT()
            gdiplus.GdipGetImageWidth(bitmap, ctypes.byref(width))
            gdiplus.GdipGetImageHeight(bitmap, ctypes.byref(height))
            w, h = width.value, height.value
            if not w or not h or w * h * 4 > 400 * 1024 * 1024:
                return None
            # 透明な部分は、白にする
            if gdiplus.GdipCreateHBITMAPFromBitmap(bitmap, ctypes.byref(hbitmap), 0xFFFFFFFF) != 0:
                return None
            info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(w * h * 4)
            hdc = user32.GetDC(None)
            try:
                got = gdi32.GetDIBits(hdc, hbitmap, 0, h, pixels, ctypes.byref(info), 0)
            finally:
                user32.ReleaseDC(None, hdc)
            if not got:
                return None
            return bytes(info) + pixels.raw
        finally:
            if hbitmap:
                gdi32.DeleteObject(hbitmap)
            if bitmap:
                gdiplus.GdipDisposeImage(bitmap)
            gdiplus.GdiplusShutdown(token)

    def _clip_set_image(path: str) -> None:
        """画像を、画像としてクリップボードに入れる。PNG は "PNG" 形式でも入れる。DIB にできず PNG でもなければ失敗。"""
        items: list[tuple[int, bytes]] = []
        dib = None
        try:
            dib = _image_to_dib(path)
        except OSError:
            pass
        if dib:
            items.append((CF_DIB, dib))
        with open(path, "rb") as f:
            head = f.read(8)
            if head == b"\x89PNG\r\n\x1a\n":
                f.seek(0)
                items.append((user32.RegisterClipboardFormatW("PNG"), f.read()))
        if not items:
            raise ClipboardError("画像として、PCのクリップボードに入れられませんでした")
        _clip_put(items)

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
            # 0 のときは、権限が足りない（相手のウィンドウが管理者で、サーバーはそうでない）ことが多い
            log.warning(
                "SendInput が一部しか通りませんでした（%d/%d、エラー %d）",
                sent, len(inputs), ctypes.get_last_error(),
            )

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
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return False
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        return cls.value not in DESKTOP_CLASSES

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
            ("ptMinPosition", wt.POINT), ("ptMaxPosition", wt.POINT), ("rcNormalPosition", wt.RECT),
        ]

    user32.GetWindowPlacement.argtypes = (wt.HWND, ctypes.POINTER(WINDOWPLACEMENT))
    user32.SetWindowPlacement.argtypes = (wt.HWND, ctypes.POINTER(WINDOWPLACEMENT))

    def _set_visible_rect(wid: int, x: int, y: int, w: int, h: int) -> bool:
        """ウィンドウの、見えている範囲を (x, y, 幅, 高さ) にする。
        SetWindowPos の大きさには、見えない枠も含まれるので、その分を足す。"""
        outer = wt.RECT()
        visible = _visible_rect(wid)
        if visible is None or not user32.GetWindowRect(wid, ctypes.byref(outer)):
            return False
        vx, vy, vw, vh = visible
        pad_l, pad_t = vx - outer.left, vy - outer.top
        pad_r, pad_b = outer.right - (vx + vw), outer.bottom - (vy + vh)
        return bool(user32.SetWindowPos(
            wid, None, x - pad_l, y - pad_t, w + pad_l + pad_r, h + pad_t + pad_b, SWP_NOZORDER | SWP_NOACTIVATE,
        ))

    class WinInput(_BringMixin):
        """主モニターを対象にした入力。set_region で範囲を決めると、座標はその範囲が基準になる。"""

        def __init__(self) -> None:
            _make_dpi_aware()
            self.region: tuple[int, int, int, int] | None = None
            self._brought: dict = {}
            self._vd = VirtualDisplay()
            self._virtual_installed: bool | None = None  # 調べた結果を覚えておく（調べるのに1秒ほどかかる）
            self._cursor_checks = 0

        # --- モニターと、仮想ディスプレイ

        def monitors(self) -> list[dict]:
            return list_monitors(self._vd.patterns)

        def virtual_available(self) -> bool:
            if self._virtual_installed is None:
                try:
                    self._virtual_installed = self._vd.device() is not None
                except Exception as e:  # PowerShell が使えないなど。仮想ディスプレイなしとして続ける
                    log.warning("仮想ディスプレイのドライバーを調べられませんでした: %s", e)
                    self._virtual_installed = False
            return self._virtual_installed

        def virtual_on(self) -> dict:
            return self._vd.on()

        def virtual_off(self) -> None:
            self._vd.off()

        def virtual_cleanup_leftover(self) -> bool:
            """有効のまま残っている仮想ディスプレイを、無効に戻す（サーバーが、後片付けをせずに止められたあとに残る）。
            戻したときは True。管理者でなくて戻せないときは、理由をログに出して False。"""
            try:
                if self._vd.monitor() is None:
                    return False
                self._vd.off(force=True)
                return True
            except VirtualDisplayError as e:
                log.warning("有効のまま残っている仮想ディスプレイを、無効に戻せませんでした: %s", e)
                return False

        def virtual_set_aspect(self, aspect: float) -> dict:
            self._vd.set_resolution(aspect)
            monitor = self._vd.monitor()
            if monitor is None:
                raise VirtualDisplayError("仮想モニターが出ていません")
            return monitor

        # --- ウィンドウの位置（仮想モニターに持ってきて、戻す）

        def window_placement(self, wid: int) -> dict | None:
            if not user32.IsWindow(wid):
                return None
            wp = WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(WINDOWPLACEMENT)
            if not user32.GetWindowPlacement(wid, ctypes.byref(wp)):
                return None
            return {"wp": wp}

        def place_window(self, wid: int, rect: tuple[int, int, int, int]) -> bool:
            if not user32.IsWindow(wid):
                return False
            if user32.IsIconic(wid) or user32.IsZoomed(wid):
                user32.ShowWindow(wid, SW_RESTORE)
            x, y, w, h = rect
            return _set_visible_rect(wid, x, y, w, h)

        def restore_window(self, wid: int, placement: dict) -> None:
            if user32.IsWindow(wid):
                user32.SetWindowPlacement(wid, ctypes.byref(placement["wp"]))

        def screen_size(self) -> tuple[int, int]:
            return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)

        def set_region(self, region: tuple[int, int, int, int] | None) -> None:
            self.region = region

        def _range(self) -> tuple[int, int, int, int]:
            """座標の基準にする範囲（x, y, 幅, 高さ。画素）。"""
            if self.region is not None:
                return self.region
            w, h = self.screen_size()
            return 0, 0, w, h  # 主モニターの左上が(0, 0)

        def _absolute(self, x: float, y: float) -> tuple[int, int]:
            rx, ry, rw, rh = self._range()
            return self._absolute_px(rx + x * (rw - 1), ry + y * (rh - 1))

        def _absolute_px(self, px: float, py: float) -> tuple[int, int]:
            """画面上の画素の位置を、SendInput の絶対座標（仮想画面全体で0〜65535）に変える。"""
            vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            vw = max(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), 2)
            vh = max(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN), 2)
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
            result = []
            for h in found:
                rect = _visible_rect(h)
                if rect is not None:
                    result.append({"id": h, "title": _window_title(h), "app": _app_name(h), "rect": rect})
            return result

        def window_thumb_raw(self, wid: int, max_w: int) -> tuple[int, int, bytes] | None:
            return _window_thumb_raw(wid, max_w)

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

        def repaint_window(self, wid: int) -> None:
            """ウィンドウに、描き直しを頼む。ウィンドウだけを撮るとき（gfxcapture）、中身が変わらないと、
            最初の1枚が出ないため（ffmpeg 8.1）。枠も含めて、すぐ描き直させる。"""
            if user32.IsWindow(wid):
                user32.RedrawWindow(
                    wid, None, None, RDW_INVALIDATE | RDW_ERASE | RDW_FRAME | RDW_ALLCHILDREN | RDW_UPDATENOW,
                )
                # 中身を自分で描くアプリ（ストアアプリなど）は、上だけでは描き直さないことがあるので、枠も描き直させる
                user32.SetWindowPos(
                    wid, None, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                )

        def fit_window(self, wid: int, w: float, h: float, area: tuple[int, int, int, int] | None = None) -> bool:
            """ウィンドウの大きさを、w x h（iPhoneの画面のポイント数）に合わせる。
            1ポイントをWindowsの1論理画素（拡大率をかけた画素）にするので、文字はPCで見るのと同じくらいの大きさになる。
            area（x, y, 幅, 高さ）の中に収める。省略すると、主モニターの作業領域。入らないときは、縦横比を保って縮める。"""
            if not user32.IsWindow(wid) or w <= 0 or h <= 0:
                return False
            if user32.IsIconic(wid) or user32.IsZoomed(wid):
                user32.ShowWindow(wid, SW_RESTORE)
            get_dpi = getattr(user32, "GetDpiForWindow", None)  # Windows 10 1607 から
            scale = 1.0
            if get_dpi is not None:
                get_dpi.argtypes = (wt.HWND,)
                scale = (get_dpi(wid) or 96) / 96
            if area is None:
                work = wt.RECT()
                user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
                area = (work.left, work.top, work.right - work.left, work.bottom - work.top)
            ax, ay, ww, wh = area
            tw, th = w * scale, h * scale
            shrink = min(1.0, ww / tw, wh / th)
            tw, th = int(tw * shrink), int(th * shrink)
            # 作業領域の中央に置く
            return _set_visible_rect(wid, ax + (ww - tw) // 2, ay + (wh - th) // 2, tw, th)

        def _move_input(self, x: float, y: float) -> INPUT:
            ax, ay = self._absolute(x, y)
            flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            return _mouse(flags, ax, ay)

        def move(self, x: float, y: float) -> None:
            _send([self._move_input(x, y)])
            self._verify_cursor(x, y)

        def _verify_cursor(self, x: float, y: float) -> None:
            """SendInput が通ったのに、カーソルが動かないことがある。最初の数回と、そのあと 200 回に 1 回、
            カーソルの位置を読んで、ずれていたら警告を出す（どこで操作が止まっているかを、ログで見分けるため）。"""
            self._cursor_checks += 1
            if self._cursor_checks > 5 and self._cursor_checks % 200:
                return
            rx, ry, rw, rh = self._range()
            expected = rx + x * (rw - 1), ry + y * (rh - 1)
            pos = wt.POINT()
            if not user32.GetCursorPos(ctypes.byref(pos)):
                return
            if abs(pos.x - expected[0]) > 4 or abs(pos.y - expected[1]) > 4:
                log.warning(
                    "カーソルが、送った位置に動いていません（送った先 %d,%d、今の位置 %d,%d）",
                    expected[0], expected[1], pos.x, pos.y,
                )
            elif self._cursor_checks == 1:
                log.info("カーソルが動くことを確かめました")

        def move_rel(self, dx: float, dy: float) -> None:
            """今のカーソルの位置から動かす。dx, dy は、表示している範囲の幅・高さに対する割合。
            相対移動の SendInput はマウスの加速の設定で量が変わるので、今の位置を読んで絶対座標で送る。"""
            pos = wt.POINT()
            if not user32.GetCursorPos(ctypes.byref(pos)):
                return
            rx, ry, rw, rh = self._range()
            # 表示している範囲の外には出さない
            px = min(max(pos.x + dx * rw, rx), rx + rw - 1)
            py = min(max(pos.y + dy * rh, ry), ry + rh - 1)
            ax, ay = self._absolute_px(px, py)
            _send([_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)])

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
            # 相手のアプリが固まっていると返事が来ないので、時間を区切って聞く
            result = ctypes.c_size_t()
            ok = user32.SendMessageTimeoutW(
                ime, WM_IME_CONTROL, IMC_GETOPENSTATUS, 0,
                SMTO_ABORTIFHUNG | SMTO_BLOCK, IME_QUERY_TIMEOUT_MS, ctypes.byref(result),
            )
            if not ok:
                return None
            return bool(result.value)

        def clip_get(self) -> str:
            return _clip_get()

        def clip_set(self, s: str) -> None:
            _clip_set(s)

        def keep_awake(self, on: bool) -> None:
            """接続している間、スリープと画面の消灯（とロック）を防ぐ。呼んだスレッドに結びつくので、
            入れるときと戻すときは、同じスレッドで呼ぶこと。"""
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else ES_CONTINUOUS
            if not kernel32.SetThreadExecutionState(flags):
                log.warning("SetThreadExecutionState が失敗しました")

        def clip_files(self) -> list[str]:
            return _clip_files()

        def clip_image(self) -> tuple[str, bytes] | None:
            return _clip_image()

        def clip_set_files(self, paths: list[str]) -> None:
            _clip_set_files(paths)

        def clip_set_image(self, path: str) -> None:
            _clip_set_image(path)


def create_input():
    if sys.platform == "win32":
        return WinInput()
    log.warning("Windowsではないため、入力は記録するだけです（NullInput）")
    return NullInput()
