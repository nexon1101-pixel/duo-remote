"""管理画面から使う部品。サーバーの起動と停止、設定の読み書き、PCのアドレス、ログイン時の自動起動。

画面（panel.py）とは分けてあり、Windows以外でもテストできる。
"""
from __future__ import annotations

import collections
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__
from .__main__ import DEFAULT_CONFIG, load_config

SERVER_DIR = Path(__file__).resolve().parent.parent

# 画面で変えられる設定と、その範囲。token は別に扱う（作り直しだけ）
SETTING_LIMITS: dict[str, tuple[int, int]] = {
    "port": (1024, 65535),
    "fps": (5, 120),
    "bitrate_kbps": (500, 20000),
    "max_height": (0, 4320),
    "output_idx": (0, 15),
}
ENCODERS = ("auto", "h264_nvenc", "libx264")
DEFAULTS: dict[str, Any] = {
    "port": 8765, "fps": 60, "bitrate_kbps": 8000, "max_height": 0, "encoder": "auto", "output_idx": 0,
}


class SettingsError(ValueError):
    """設定の値が範囲の外、などで、保存できなかった。"""


def read_settings(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """設定を読む。なければ作る。足りない項目は、初期値で埋めて返す（ファイルは書き換えない）。"""
    cfg, _ = load_config(path)
    return {**DEFAULTS, **cfg}


def save_settings(changes: dict[str, Any], path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """変えた項目だけを、確かめてから保存する。トークンは、ここでは変えない。保存した後の設定を返す。"""
    cfg, _ = load_config(path)
    for key, value in changes.items():
        if key in SETTING_LIMITS:
            low, high = SETTING_LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
                raise SettingsError(f"{key} は整数にしてください")
            if not low <= int(value) <= high:
                raise SettingsError(f"{key} は {low}〜{high} にしてください")
            cfg[key] = int(value)
        elif key == "encoder":
            if value not in ENCODERS:
                raise SettingsError(f"encoder は {'、'.join(ENCODERS)} のどれかにしてください")
            cfg[key] = value
        else:
            raise SettingsError(f"{key} は変えられません")
    _write(path, cfg)
    return {**DEFAULTS, **cfg}


def regenerate_token(path: Path = DEFAULT_CONFIG) -> str:
    """トークンを作り直して保存する。iPhoneのアプリにも、新しいトークンを入れ直す必要がある。"""
    import secrets

    cfg, _ = load_config(path)
    cfg["token"] = secrets.token_urlsafe(16)
    _write(path, cfg)
    return cfg["token"]


def _write(path: Path, cfg: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def server_args(settings: dict[str, Any], config: Path = DEFAULT_CONFIG) -> list[str]:
    """設定から、サーバーを起動するときの引数を作る。"""
    return [
        "--config", str(config),
        "--encoder", str(settings.get("encoder", "auto")),
        "--output-idx", str(settings.get("output_idx", 0)),
        "--control-stdin",
    ]


def local_addresses() -> list[str]:
    """このPCのIPv4アドレス（127.0.0.1 を除く）。iPhoneのアプリのホストに入れる候補。
    Tailscale のアドレス（100.64.0.0/10）も含む。"""
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if not addr.startswith("127.") and addr not in found:
                found.append(addr)
    except OSError:
        pass
    # 外への経路で使うアドレス（実際には何も送らない）
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            addr = s.getsockname()[0]
            if not addr.startswith("127.") and addr not in found:
                found.insert(0, addr)
    except OSError:
        pass
    return found


def is_admin() -> bool:
    """管理者として動いているか（仮想ディスプレイを使うのに要る）。Windows以外では False。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def console_python(executable: str = sys.executable) -> str:
    """サーバーを動かす python。管理画面は pythonw.exe（黒い画面を出さない）で動くので、隣の python.exe を使う。"""
    path = Path(executable)
    if path.name.lower() == "pythonw.exe":
        candidate = path.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return executable


class ServerProcess:
    """サーバー（python -m duo_server）を、子のプロセスとして動かす。
    出力は、最後の数百行を覚えておき、画面に出す。止めるときは、標準入力に stop を送り、後片付けを待つ。"""

    STOP_TIMEOUT = 10.0
    LOG_LINES = 500

    def __init__(self, python: str | None = None, cwd: Path = SERVER_DIR) -> None:
        self.python = python or console_python()
        self.cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._lock = threading.RLock()  # start の中から _add を呼ぶので、同じスレッドで重ねてかけられるものにする
        self._log: collections.deque[str] = collections.deque(maxlen=self.LOG_LINES)
        # 出力の行の通し番号。画面は、前に読んだ番号より後の行だけをもらう
        self._seq = 0
        self.started_at: float | None = None
        self.last_exit: int | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, args: list[str]) -> None:
        with self._lock:
            if self.running:
                return
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.CREATE_NO_WINDOW  # 黒い画面を出さない
            self._proc = subprocess.Popen(
                self.command(args),
                cwd=self.cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags,
            )
            self.started_at = time.time()
            self.last_exit = None
            self._add(f"--- サーバーを起動しました（duo_server {__version__}）---")
            threading.Thread(target=self._pump, args=(self._proc,), daemon=True).start()

    def command(self, args: list[str]) -> list[str]:
        return [self.python, "-m", "duo_server", *args]

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self._add(line.rstrip("\n"))
        code = proc.wait()
        with self._lock:
            if self._proc is proc:
                self.last_exit = code
                self.started_at = None
        self._add(f"--- サーバーが止まりました（コード {code}）---")

    def _add(self, line: str) -> None:
        with self._lock:
            self._seq += 1
            self._log.append(line)

    def stop(self) -> None:
        """後片付け（仮想ディスプレイを無効に戻す、など）をさせてから止める。止まらなければ、強制的に止める。"""
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            assert proc.stdin is not None
            proc.stdin.write("stop\n")
            proc.stdin.flush()
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(self.STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._add("--- 止まらないため、強制的に止めます ---")
            proc.kill()
            proc.wait()

    def restart(self, args: list[str]) -> None:
        self.stop()
        self.start(args)

    def log_since(self, seq: int) -> tuple[int, list[str]]:
        """seq より後の行と、今の通し番号を返す。古すぎて消えた行は、返せる分だけ返す。"""
        with self._lock:
            lines = list(self._log)
            current = self._seq
        new = current - seq
        if new <= 0:
            return current, []
        return current, lines[-min(new, len(lines)):]


# --- ログインしたときの自動起動（タスク スケジューラ）

TASK_NAME = "DuoRemote サーバー"


def autostart_command(panel_bat: Path = SERVER_DIR / "panel.bat") -> list[str]:
    """ログイン時に管理画面を、サーバーを動かした状態で、画面を出さずに起動するタスクを作るコマンド。
    仮想ディスプレイのために、最上位の特権で動かす。"""
    return [
        "schtasks", "/Create", "/F", "/TN", TASK_NAME,
        "/TR", f'"{panel_bat}" --autostart',
        "/SC", "ONLOGON", "/RL", "HIGHEST",
    ]


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def set_autostart(enabled: bool) -> str | None:
    """自動起動を設定する・外す。失敗したら、理由を返す（管理者でないと、最上位の特権のタスクは作れない）。"""
    if sys.platform != "win32":
        return "Windowsでしか使えません"
    cmd = autostart_command() if enabled else ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]
    result = subprocess.run(
        cmd, capture_output=True, text=True, errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or "設定できませんでした"
    return None
