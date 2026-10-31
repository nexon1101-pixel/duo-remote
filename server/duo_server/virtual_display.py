"""仮想ディスプレイ（Virtual Display Driver）の操作と、モニターの調べ方。

起動: python -m duo_server.virtual_display status | monitors | modes | on | off | write-config

- 仮想モニターの出し入れは、ドライバーの有効・無効の切り替えで行う（管理者の権限が要る）
- 解像度は、ドライバーの設定ファイル（vdd_settings.xml）にある一覧から選ぶ
- Windows 以外では、調べる処理は使えない（文字列の処理だけ、テストできる）
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

# ドライバーのモニターを見分けるための文字（小文字で比べる）。
# MttVDD は、Virtual Display Driver のハードウェア ID
DEFAULT_PATTERNS = ("virtual display", "mttvdd", "iddsample", "parsec virtual")
CONFIG_PATH = Path(r"C:\VirtualDisplayDriver\vdd_settings.xml")

# 仮想モニターの高さ。本物のモニターの高さ（1080）にそろえると、拡大率が同じになり、
# ウィンドウを行き来させても大きさが崩れない
TARGET_HEIGHT = 1080
# 設定ファイルに書く幅。高さ1080のまま、縦長から横長まで。
# iPhone Duo の画面の縦横比に合う幅を含める（高さ1080のとき）:
#   内側 1878x2670px: 縦持ち 760、横持ち 1536 / 外側 1398x2034px: 縦持ち 742、横持ち 1572
PRESET_WIDTHS = (720, 742, 760, 810, 900, 1080, 1200, 1296, 1440, 1536, 1572, 1620, 1728, 1800, 1920, 2160, 2400, 2560)
REFRESH_RATES = (60,)


class VirtualDisplayError(RuntimeError):
    """仮想ディスプレイを使えないときの理由（利用者に見せる文）。"""


# --- 文字列の処理（Windows 以外でもテストできる）-------------------------------

def matches(text: str, patterns: tuple[str, ...] = DEFAULT_PATTERNS) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in patterns)


def choose_mode(
    modes: list[tuple[int, int, int]], aspect: float, target_height: int = TARGET_HEIGHT
) -> tuple[int, int, int] | None:
    """(幅, 高さ, Hz) の一覧から、縦横比が aspect にいちばん近いものを選ぶ。
    同じくらいなら、高さが target_height に近いもの、さらに 60Hz に近いもの。"""
    candidates = [m for m in modes if m[0] > 0 and m[1] > 0]
    if not candidates or aspect <= 0:
        return None

    def key(m: tuple[int, int, int]):
        w, h, hz = m
        return (round(abs(math.log((w / h) / aspect)), 2), abs(h - target_height), abs(hz - 60))

    return min(candidates, key=key)


def build_vdd_settings(
    gpu_name: str | None = None,
    count: int = 1,
    widths: tuple[int, ...] = PRESET_WIDTHS,
    height: int = TARGET_HEIGHT,
    rates: tuple[int, ...] = REFRESH_RATES,
) -> str:
    """Virtual Display Driver の設定ファイル（vdd_settings.xml）の中身を作る。"""
    lines = [
        "<?xml version='1.0' encoding='utf-8'?>",
        "<vdd_settings>",
        "    <monitors>",
        f"        <count>{count}</count>",
        "    </monitors>",
        "    <gpu>",
        f"        <friendlyname>{gpu_name or 'default'}</friendlyname>",
        "    </gpu>",
        "    <global>",
        *[f"        <g_refresh_rate>{r}</g_refresh_rate>" for r in rates],
        "    </global>",
        "    <resolutions>",
    ]
    for w in widths:
        for r in rates:
            lines += [
                "        <resolution>",
                f"            <width>{w}</width>",
                f"            <height>{height}</height>",
                f"            <refresh_rate>{r}</refresh_rate>",
                "        </resolution>",
            ]
    lines += [
        "    </resolutions>",
        "    <options>",
        "        <CustomEdid>false</CustomEdid>",
        "        <PreventSpoof>false</PreventSpoof>",
        "        <EdidCeaOverride>false</EdidCeaOverride>",
        "        <HardwareCursor>true</HardwareCursor>",
        "        <SDR10bit>false</SDR10bit>",
        "        <HDRPlus>false</HDRPlus>",
        "        <logging>false</logging>",
        "        <debuglogging>false</debuglogging>",
        "    </options>",
        "</vdd_settings>",
        "",
    ]
    return "\n".join(lines)


def parse_pnp_json(text: str) -> list[dict]:
    """PowerShell の ConvertTo-Json の出力を読む。1件のときは、配列ではなく1つの辞書で出てくる。"""
    text = text.strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def rect_contains(rect: tuple[int, int, int, int], x: float, y: float) -> bool:
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def monitor_of(rect: tuple[int, int, int, int], monitors: list[dict]) -> dict | None:
    """ウィンドウの範囲が、いちばん多く重なっているモニター。どこにも重ならなければ None。"""
    x, y, w, h = rect
    best, best_area = None, 0
    for m in monitors:
        mx, my, mw, mh = m["rect"]
        overlap_w = min(x + w, mx + mw) - max(x, mx)
        overlap_h = min(y + h, my + mh) - max(y, my)
        area = max(overlap_w, 0) * max(overlap_h, 0)
        if area > best_area:
            best, best_area = m, area
    return best


# --- Windows の処理 -----------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    ENUM_CURRENT_SETTINGS = 0xFFFFFFFF  # (DWORD)-1
    DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x80000, 0x100000, 0x400000
    DISP_CHANGE_SUCCESSFUL = 0
    MONITORINFOF_PRIMARY = 1
    CREATE_NO_WINDOW = 0x08000000

    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wt.WCHAR * 32),
            ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
            ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD),
            ("dmFields", wt.DWORD),
            ("dmPositionX", wt.LONG), ("dmPositionY", wt.LONG),
            ("dmDisplayOrientation", wt.DWORD), ("dmDisplayFixedOutput", wt.DWORD),
            ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short),
            ("dmFormName", wt.WCHAR * 32),
            ("dmLogPixels", wt.WORD),
            ("dmBitsPerPel", wt.DWORD), ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD),
            ("dmDisplayFlags", wt.DWORD), ("dmDisplayFrequency", wt.DWORD),
            ("dmICMMethod", wt.DWORD), ("dmICMIntent", wt.DWORD),
            ("dmMediaType", wt.DWORD), ("dmDitherType", wt.DWORD),
            ("dmReserved1", wt.DWORD), ("dmReserved2", wt.DWORD),
            ("dmPanningWidth", wt.DWORD), ("dmPanningHeight", wt.DWORD),
        ]

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32), ("DeviceString", wt.WCHAR * 128),
            ("StateFlags", wt.DWORD), ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128),
        ]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
            ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32),
        ]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
    user32.EnumDisplayMonitors.argtypes = (wt.HDC, ctypes.POINTER(wt.RECT), MONITORENUMPROC, wt.LPARAM)
    user32.GetMonitorInfoW.argtypes = (wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW))
    user32.EnumDisplayDevicesW.argtypes = (wt.LPCWSTR, wt.DWORD, ctypes.POINTER(DISPLAY_DEVICEW), wt.DWORD)
    user32.EnumDisplaySettingsW.argtypes = (wt.LPCWSTR, wt.DWORD, ctypes.POINTER(DEVMODEW))
    user32.ChangeDisplaySettingsExW.argtypes = (
        wt.LPCWSTR, ctypes.POINTER(DEVMODEW), wt.HWND, wt.DWORD, ctypes.c_void_p,
    )
    user32.ChangeDisplaySettingsExW.restype = ctypes.c_long

    def is_admin() -> bool:
        try:
            return bool(shell32.IsUserAnAdmin())
        except OSError:
            return False

    # --- DXGI（ffmpeg の ddagrab が使う、アダプターとモニターの番号）

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    _IID_IDXGIFactory1 = _GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    _DXGI_ERROR_NOT_FOUND = 0x887A0002

    class _DXGI_ADAPTER_DESC(ctypes.Structure):
        _fields_ = [
            ("Description", wt.WCHAR * 128), ("VendorId", wt.UINT), ("DeviceId", wt.UINT),
            ("SubSysId", wt.UINT), ("Revision", wt.UINT),
            ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
            ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", ctypes.c_longlong),
        ]

    class _DXGI_OUTPUT_DESC(ctypes.Structure):
        _fields_ = [
            ("DeviceName", wt.WCHAR * 32), ("DesktopCoordinates", wt.RECT),
            ("AttachedToDesktop", wt.BOOL), ("Rotation", wt.UINT), ("Monitor", wt.HMONITOR),
        ]

    def _vcall(obj: ctypes.c_void_p, index: int, argtypes: tuple, *args) -> int:
        """COM のインターフェースの、index 番目の関数を呼ぶ（戻り値は HRESULT）。"""
        vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
        return fn(obj, *args) & 0xFFFFFFFF

    def _release(obj: ctypes.c_void_p) -> None:
        _vcall(obj, 2, ())

    def dxgi_outputs() -> list[dict]:
        """DXGI のアダプターとモニターの一覧。{adapter, output, adapter_name, device_name, rect}"""
        try:
            dxgi = ctypes.WinDLL("dxgi")
        except OSError:
            return []
        factory = ctypes.c_void_p()
        dxgi.CreateDXGIFactory1.argtypes = (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
        if dxgi.CreateDXGIFactory1(ctypes.byref(_IID_IDXGIFactory1), ctypes.byref(factory)) != 0:
            return []
        found: list[dict] = []
        try:
            a = 0
            while True:
                adapter = ctypes.c_void_p()
                # IDXGIFactory1::EnumAdapters1（12番目）
                if _vcall(factory, 12, (wt.UINT, ctypes.POINTER(ctypes.c_void_p)), a, ctypes.byref(adapter)) != 0:
                    break
                try:
                    desc = _DXGI_ADAPTER_DESC()
                    _vcall(adapter, 8, (ctypes.POINTER(_DXGI_ADAPTER_DESC),), ctypes.byref(desc))  # GetDesc
                    o = 0
                    while True:
                        output = ctypes.c_void_p()
                        # IDXGIAdapter::EnumOutputs（7番目）
                        if _vcall(adapter, 7, (wt.UINT, ctypes.POINTER(ctypes.c_void_p)), o, ctypes.byref(output)) != 0:
                            break
                        try:
                            od = _DXGI_OUTPUT_DESC()
                            _vcall(output, 7, (ctypes.POINTER(_DXGI_OUTPUT_DESC),), ctypes.byref(od))  # GetDesc
                            r = od.DesktopCoordinates
                            found.append({
                                "adapter": a, "output": o, "adapter_name": desc.Description,
                                "device_name": od.DeviceName,
                                "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
                            })
                        finally:
                            _release(output)
                        o += 1
                finally:
                    _release(adapter)
                a += 1
        finally:
            _release(factory)
        return found

    def list_monitors(patterns: tuple[str, ...] = DEFAULT_PATTERNS) -> list[dict]:
        """つながっているモニターの一覧。
        {name, rect, work, primary, description, virtual, adapter, output}（rect は、デスクトップ全体での位置）"""
        monitors: list[dict] = []

        @MONITORENUMPROC
        def collect(hmon, _hdc, _rect, _lparam):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r, w = info.rcMonitor, info.rcWork
                monitors.append({
                    "name": info.szDevice,
                    "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
                    "work": (w.left, w.top, w.right - w.left, w.bottom - w.top),
                    "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                })
            return True

        user32.EnumDisplayMonitors(None, None, collect, 0)

        outputs = {o["device_name"]: o for o in dxgi_outputs()}
        for m in monitors:
            # アダプターの名前と、モニターの名前（どちらかに、ドライバーの名前が出る）
            strings: list[str] = []
            dev = DISPLAY_DEVICEW()
            dev.cb = ctypes.sizeof(DISPLAY_DEVICEW)
            i = 0
            while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
                if dev.DeviceName == m["name"]:
                    strings += [dev.DeviceString, dev.DeviceID]
                    mon = DISPLAY_DEVICEW()
                    mon.cb = ctypes.sizeof(DISPLAY_DEVICEW)
                    if user32.EnumDisplayDevicesW(m["name"], 0, ctypes.byref(mon), 0):
                        strings += [mon.DeviceString, mon.DeviceID]
                    break
                i += 1
            m["description"] = " / ".join(s for s in strings if s)
            m["virtual"] = matches(m["description"], patterns)
            o = outputs.get(m["name"])
            m["adapter"] = o["adapter"] if o else None
            m["output"] = o["output"] if o else None
        return monitors

    def list_modes(device_name: str) -> list[tuple[int, int, int]]:
        """モニターが出せる解像度の一覧 (幅, 高さ, Hz)。"""
        modes: set[tuple[int, int, int]] = set()
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        i = 0
        while user32.EnumDisplaySettingsW(device_name, i, ctypes.byref(dm)):
            if dm.dmBitsPerPel >= 24:
                modes.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
            i += 1
        return sorted(modes)

    def current_mode(device_name: str) -> tuple[int, int, int] | None:
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
            return None
        return dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency

    def apply_mode(device_name: str, width: int, height: int, hz: int) -> None:
        """モニターの解像度を変える（設定には保存しない）。"""
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
            raise VirtualDisplayError(f"{device_name} の今の解像度を読めませんでした")
        dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency = width, height, hz
        dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
        code = user32.ChangeDisplaySettingsExW(device_name, ctypes.byref(dm), None, 0, None)
        if code != DISP_CHANGE_SUCCESSFUL:
            raise VirtualDisplayError(f"解像度を {width}x{height} に変えられませんでした（コード {code}）")

    def _powershell(script: str, timeout: float = 30) -> str:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
        out = r.stdout.decode("utf-8", errors="replace")
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace").strip()
            raise VirtualDisplayError(f"PowerShell が失敗しました: {err or out.strip()}")
        return out

    _PNP_QUERY = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "$d = @(Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | ForEach-Object { "
        "$h = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName DEVPKEY_Device_HardwareIds "
        "-ErrorAction SilentlyContinue).Data; "
        "[pscustomobject]@{InstanceId=$_.InstanceId; FriendlyName=$_.FriendlyName; Status=[string]$_.Status; "
        "HardwareIds=($h -join ',')} }); ConvertTo-Json -InputObject $d -Compress"
    )

    def pnp_display_devices() -> list[dict]:
        """画面表示のデバイス（グラフィックボードや、仮想ディスプレイ）の一覧。"""
        return parse_pnp_json(_powershell(_PNP_QUERY))

    def set_pnp_enabled(instance_id: str, enabled: bool) -> None:
        verb = "Enable-PnpDevice" if enabled else "Disable-PnpDevice"
        quoted = instance_id.replace("'", "''")
        _powershell(f"{verb} -InstanceId '{quoted}' -Confirm:$false -ErrorAction Stop", timeout=60)

else:  # Windows 以外
    def _unsupported(*_a, **_k):
        raise VirtualDisplayError("この機能は Windows でだけ使えます")

    is_admin = lambda: False  # noqa: E731
    dxgi_outputs = list_monitors = list_modes = current_mode = apply_mode = _unsupported
    pnp_display_devices = set_pnp_enabled = _unsupported


class VirtualDisplay:
    """仮想ディスプレイの出し入れと、解像度の変更。"""

    def __init__(self, patterns: tuple[str, ...] = DEFAULT_PATTERNS) -> None:
        self.patterns = patterns
        self._we_enabled = False  # このサーバーが有効にしたか（無効に戻すのは、そのときだけ）

    def device(self) -> dict | None:
        """ドライバーのデバイス（入っていなければ None）。"""
        for d in pnp_display_devices():
            if matches(f"{d.get('FriendlyName') or ''} {d.get('HardwareIds') or ''}", self.patterns):
                return d
        return None

    def monitor(self) -> dict | None:
        """今つながっている、仮想モニター。"""
        for m in list_monitors(self.patterns):
            if m["virtual"]:
                return m
        return None

    def on(self, timeout: float = 15) -> dict:
        """仮想モニターを出して、そのモニターを返す。すでに出ていれば、そのまま返す。"""
        found = self.monitor()
        if found:
            return found
        device = self.device()
        if device is None:
            raise VirtualDisplayError(
                "仮想ディスプレイのドライバーが見つかりません。docs/virtual-display.md の手順で入れてください"
            )
        if device.get("Status") != "OK":
            if not is_admin():
                raise VirtualDisplayError(
                    "仮想ディスプレイを出すには、サーバーを管理者として起動してください（start-admin.bat）"
                )
            set_pnp_enabled(device["InstanceId"], True)
            self._we_enabled = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.monitor()
            if found:
                return found
            time.sleep(0.5)
        raise VirtualDisplayError(
            "仮想モニターが出ませんでした。vdd_settings.xml の count が 1 以上か、確認してください"
        )

    def off(self, force: bool = False) -> None:
        """このサーバーが出した仮想モニターを消す。force のときは、出したのが誰でも消す。"""
        if not (self._we_enabled or force):
            return
        device = self.device()
        if device is not None and device.get("Status") == "OK":
            if not is_admin():
                raise VirtualDisplayError("仮想ディスプレイを消すには、管理者の権限が要ります")
            set_pnp_enabled(device["InstanceId"], False)
        self._we_enabled = False

    def set_resolution(self, aspect: float) -> tuple[int, int, int]:
        """縦横比が aspect に近い解像度に変える。変えたあとの (幅, 高さ, Hz) を返す。"""
        monitor = self.monitor()
        if monitor is None:
            raise VirtualDisplayError("仮想モニターが出ていません")
        mode = choose_mode(list_modes(monitor["name"]), aspect)
        if mode is None:
            raise VirtualDisplayError("仮想モニターが出せる解像度が見つかりません")
        if current_mode(monitor["name"]) != mode:
            apply_mode(monitor["name"], *mode)
            self._wait_settled(mode)
        return mode

    def _wait_settled(self, mode: tuple[int, int, int], timeout: float = 10) -> None:
        """解像度を変えると、仮想モニターが作り直される。大きさと番号が、2回続けて同じになるまで待つ。"""
        deadline = time.monotonic() + timeout
        stable = 0
        while time.monotonic() < deadline:
            found = self.monitor()
            if found and tuple(found["rect"][2:]) == (mode[0], mode[1]) and found.get("output") is not None:
                stable += 1
                if stable >= 2:
                    return
            else:
                stable = 0
            time.sleep(0.5)
        log.warning("解像度を変えたあと、仮想モニターが落ち着きませんでした（%dx%d）", mode[0], mode[1])


# --- 診断用のコマンド ----------------------------------------------------------

def _print_monitors() -> None:
    for m in list_monitors():
        x, y, w, h = m["rect"]
        print(
            f"- {m['name']}: {w}x{h} 位置({x},{y}) {'主モニター ' if m['primary'] else ''}"
            f"{'【仮想】 ' if m['virtual'] else ''}アダプター{m['adapter']} 出力{m['output']}"
        )
        print(f"    {m['description']}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="duo_server.virtual_display", description="仮想ディスプレイの状態を調べる・操作する")
    p.add_argument("command", choices=["status", "monitors", "modes", "on", "off", "write-config"])
    p.add_argument("--gpu", help="write-config: 使うグラフィックボードの名前（省略すると default）")
    p.add_argument("--apply", action="store_true", help="write-config: 書いたあと、ドライバーを入れ直して反映する（管理者）")
    args = p.parse_args(argv)
    vd = VirtualDisplay()

    try:
        if args.command == "status":
            print(f"管理者: {'はい' if is_admin() else 'いいえ'}")
            print(f"設定ファイル: {CONFIG_PATH}（{'ある' if CONFIG_PATH.exists() else 'ない'}）")
            device = vd.device()
            print(f"ドライバー: {json.dumps(device, ensure_ascii=False) if device else '見つかりません'}")
            print("モニター:")
            _print_monitors()
            print("DXGI（ffmpeg の ddagrab が使う番号）:")
            for o in dxgi_outputs():
                print(f"- アダプター{o['adapter']}（{o['adapter_name']}）出力{o['output']}: {o['device_name']} {o['rect']}")
        elif args.command == "monitors":
            _print_monitors()
        elif args.command == "modes":
            monitor = vd.monitor()
            if monitor is None:
                print("仮想モニターが出ていません")
            else:
                print(f"{monitor['name']} の解像度:")
                for w, h, hz in list_modes(monitor["name"]):
                    print(f"- {w}x{h} {hz}Hz")
        elif args.command == "on":
            m = vd.on()
            print(f"仮想モニターが出ました: {m['name']} {m['rect']}")
        elif args.command == "off":
            vd.off(force=True)
            print("仮想モニターを消しました")
        elif args.command == "write-config":
            xml = build_vdd_settings(args.gpu)
            if CONFIG_PATH.exists():
                backup = CONFIG_PATH.with_suffix(".xml.bak")
                if not backup.exists():
                    backup.write_bytes(CONFIG_PATH.read_bytes())
                    print(f"元の設定を {backup} に控えました")
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(xml, encoding="utf-8")
            print(f"設定を書きました: {CONFIG_PATH}（高さ {TARGET_HEIGHT}、幅 {PRESET_WIDTHS[0]}〜{PRESET_WIDTHS[-1]}）")
            if args.apply:
                device = vd.device()
                if device is None:
                    raise VirtualDisplayError("ドライバーが見つかりません")
                set_pnp_enabled(device["InstanceId"], False)
                time.sleep(1)
                set_pnp_enabled(device["InstanceId"], True)
                print("ドライバーを入れ直しました")
            else:
                print("反映するには、--apply を付けるか、デバイス マネージャーでドライバーを無効にして有効に戻してください")
    except VirtualDisplayError as e:
        print(f"エラー: {e}")
        sys.exit(1)
    except PermissionError as e:
        print(f"書き込めません: {e}。管理者として実行してください")
        sys.exit(1)


if __name__ == "__main__":
    main()
