"""サーバーの管理画面。PCの中だけで使うウェブページを出し、サーバー（python -m duo_server）を動かす。

起動: python -m duo_server.panel [--port 8770] [--autostart] [--no-window] [--restart]
127.0.0.1 だけで待ち受ける。起動のたびに新しい鍵を作り、鍵のない要求は断る。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import __version__, control
from .control import SERVER_DIR, SettingsError

DEFAULT_PORT = 8770
LOCK_PATH = SERVER_DIR / "panel.lock"
PANEL_BAT = SERVER_DIR / "panel.bat"
# 前回の更新のログ（更新のあと、起動し直した管理画面で見せる）
UPDATE_LOG_PATH = SERVER_DIR / "last_update.log"
HTML_PATH = Path(__file__).with_name("panel.html")
# 更新のあと、新しい管理画面を起動してから、自分が終わるまで待つ秒数
RELAUNCH_WAIT = 2.0
# --restart のとき、前の管理画面が終わるのを待つ秒数
RESTART_WAIT = 15.0


# --- 起動し直す・ウィンドウを開く

# 起動し直すとき、新しい管理画面に引き継ぐ引数（--browser chrome で開いていたら、起動し直しても Chrome で開く）
CARRY_ARGS: list[str] = []

def relaunch(args: list[str]) -> None:
    """panel.bat を、この管理画面から切り離して起動する（この管理画面が終わっても、続けて動く）。"""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["cmd", "/c", str(PANEL_BAT), *args, *CARRY_ARGS],
        cwd=SERVER_DIR, creationflags=flags, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def run_as_admin(extra: list[str] | None = None) -> bool:
    """管理者として panel.bat を起動し直す。UAC（確認の画面）で断られたら False。extra は、足す引数。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        params = subprocess.list2cmdline(["--restart", *CARRY_ARGS, *(extra or [])])
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(PANEL_BAT), params, str(SERVER_DIR), 1)
        return int(result) > 32
    except Exception:
        return False


def find_edge() -> str | None:
    if sys.platform != "win32":
        return None
    found = shutil.which("msedge")
    if found:
        return found
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.exists():
                return str(candidate)
    return None


def find_chrome() -> str | None:
    if sys.platform != "win32":
        return shutil.which("google-chrome") or shutil.which("chrome")
    found = shutil.which("chrome")
    if found:
        return found
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.exists():
                return str(candidate)
    return None


def open_window(url: str, browser: str = "edge") -> None:
    """管理画面を開く。
    - edge: Edge のアプリ表示で、普通のウィンドウのように開く
    - chrome: Chrome の新しいタブで開く（拡張機能が使えるので、PCを操作する Claude が、Claude in Chrome で触れる）
    - default: いつものブラウザーで開く
    選んだブラウザーが見つからなければ、いつものブラウザーで開く。"""
    if browser == "edge" and (edge := find_edge()):
        subprocess.Popen([edge, f"--app={url}", "--window-size=920,760"])
    elif browser == "chrome" and (chrome := find_chrome()):
        subprocess.Popen([chrome, "--new-tab", url])
    else:
        webbrowser.open(url)


# --- 2重起動の確認

def ping(port: int, key: str, timeout: float = 1.5) -> bool:
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", "/api/ping", headers={"X-Panel-Key": key})
            resp = conn.getresponse()
            return resp.status == 200 and json.loads(resp.read()).get("ok") is True
        finally:
            conn.close()
    except (OSError, ValueError):
        return False


def read_lock(path: Path = LOCK_PATH) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("port"), int) and isinstance(data.get("key"), str):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return None


def running_panel(path: Path = LOCK_PATH) -> dict | None:
    """すでに動いている管理画面があれば、その port と key を返す。"""
    data = read_lock(path)
    if data and ping(data["port"], data["key"]):
        return data
    return None


# --- HTTP

class UpdateState:
    """更新のログと、進み具合。ServerProcess のログとは別に覚える。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.done = False
        self.error: str | None = None
        self.lines: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"running": self.running, "lines": list(self.lines), "done": self.done, "error": self.error}


def make_app(process: Any, key: str, config: Path = control.DEFAULT_CONFIG):
    """リクエストを受けるクラスを作る。process は ServerProcess（または同じ形のもの）。
    クラスの exit_event が立ったら、main が管理画面を終わる。"""
    update = UpdateState()
    exit_event = threading.Event()
    # 起動し直すときに、新しい管理画面に渡す鍵。このページは、新しい管理画面が動き出したら、この鍵のアドレスに移る
    next_key = secrets.token_urlsafe(24)

    def args_for_server() -> list[str]:
        return control.server_args(control.read_settings(config), config)

    def status() -> dict[str, Any]:
        return {
            "running": process.running,
            "started_at": process.started_at,
            "last_exit": process.last_exit,
            "version": __version__,
            "admin": control.is_admin(),
            "addresses": control.local_addresses(),
            "address_details": control.address_details(),
            "port": control.read_settings(config)["port"],
            "udp_port": control.read_settings(config)["port"],  # UDP も、WebSocket と同じ番号
            "autostart": control.autostart_enabled(),
        }

    def update_log() -> dict[str, Any]:
        """今の更新の進み具合と、前回の更新のログ（ファイルに残したもの）。"""
        snap = update.snapshot()
        previous: list[str] = []
        if not snap["running"] and not snap["lines"]:
            try:
                previous = UPDATE_LOG_PATH.read_text(encoding="utf-8").splitlines()
            except OSError:
                pass
        if snap["done"]:
            snap["next_key"] = next_key
        return {**snap, "previous": previous}

    def finish_and_exit(relaunch_args: list[str] | None) -> None:
        """サーバーを止め、必要なら新しい管理画面を起動してから、終わる合図を出す。"""
        try:
            process.stop()
            if relaunch_args is not None:
                relaunch(relaunch_args)
                time.sleep(RELAUNCH_WAIT)
        finally:
            exit_event.set()

    def run_update() -> None:
        error: str | None = None
        try:
            from . import updater

            # 更新のあとは管理画面が起動し直すので、ログはファイルにも残し、新しい管理画面で見せる
            UPDATE_LOG_PATH.write_text("", encoding="utf-8")

            def log(line: str) -> None:
                with update.lock:
                    update.lines.append(str(line))
                with UPDATE_LOG_PATH.open("a", encoding="utf-8") as f:
                    f.write(f"{line}\n")

            try:
                updater.apply(log)
            except updater.UpdateError as e:
                error = str(e) or "更新できませんでした"
        except Exception as e:  # noqa: BLE001 更新の失敗で管理画面を落とさない
            error = str(e) or "更新できませんでした"
        with update.lock:
            update.running = False
            update.error = error
            update.done = error is None
            if error:
                update.lines.append(f"失敗しました: {error}")
                try:
                    with UPDATE_LOG_PATH.open("a", encoding="utf-8") as f:
                        f.write(f"失敗しました: {error}\n")
                except OSError:
                    pass
        if error is None:
            finish_and_exit(["--restart", "--key", next_key])

    class Handler(BaseHTTPRequestHandler):
        server_version = "DuoPanel"
        exit_signal = exit_event
        update_state = update

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 画面に出さない
            pass

        # -- 共通
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _key_ok(self, given: str | None) -> bool:
            return bool(given) and secrets.compare_digest(given.encode(), key.encode())

        def _body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length == 0:
                    return {}
                data = json.loads(self.rfile.read(length))
                return data if isinstance(data, dict) else None
            except (ValueError, OSError):
                return None

        def _handle(self, method: str) -> None:
            after: Callable[[], None] | None = None
            try:
                if not self._host_ok():
                    return self._json(403, {"error": "Host が違います"})
                url = urlparse(self.path)
                if method == "GET" and url.path == "/":
                    if not self._key_ok((parse_qs(url.query).get("k") or [None])[0]):
                        return self._json(403, {"error": "鍵が違います"})
                    return self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
                if not url.path.startswith("/api/"):
                    return self._json(404, {"error": "見つかりません"})
                if not self._key_ok(self.headers.get("X-Panel-Key")):
                    return self._json(403, {"error": "鍵が違います"})
                body = self._body() if method == "POST" else {}
                if body is None:
                    return self._json(400, {"error": "本文はJSONの辞書にしてください"})
                code, obj, after = self._route(method, url.path, parse_qs(url.query), body)
                self._json(code, obj)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:  # noqa: BLE001
                try:
                    self._json(500, {"error": f"{type(e).__name__}: {e}"})
                except OSError:
                    return
            if after:
                after()

        def _route(self, method: str, path: str, query: dict, body: dict):
            """(状態コード, 返す内容, 返したあとにすること) を返す。"""
            routes = {
                ("GET", "/api/ping"): lambda: (200, {"ok": True}, None),
                ("GET", "/api/status"): lambda: (200, status(), None),
                ("GET", "/api/log"): lambda: self._log(query),
                ("POST", "/api/start"): lambda: self._start(),
                ("POST", "/api/stop"): lambda: self._stop(),
                ("POST", "/api/restart"): lambda: self._restart(),
                ("GET", "/api/settings"): lambda: self._settings_get(),
                ("POST", "/api/settings"): lambda: self._settings_post(body),
                ("POST", "/api/token"): lambda: self._token(),
                ("POST", "/api/autostart"): lambda: self._autostart(body),
                ("GET", "/api/update"): lambda: self._update_check(),
                ("POST", "/api/update"): lambda: self._update_start(),
                ("GET", "/api/update/log"): lambda: (200, update_log(), None),
                ("POST", "/api/admin"): lambda: self._admin(),
                ("POST", "/api/quit"): lambda: self._quit(),
            }
            fn = routes.get((method, path))
            if fn is None:
                return 404, {"error": "見つかりません"}, None
            return fn()

        # -- 各API
        def _log(self, query: dict):
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                return 400, {"error": "since は整数にしてください"}, None
            seq, lines = process.log_since(since)
            return 200, {"seq": seq, "lines": lines}, None

        def _start(self):
            process.start(args_for_server())
            return 200, status(), None

        def _stop(self):
            process.stop()
            return 200, status(), None

        def _restart(self):
            process.restart(args_for_server())
            return 200, status(), None

        def _settings_get(self):
            return 200, {
                "settings": control.read_settings(config),
                "limits": control.SETTING_LIMITS,
                "encoders": list(control.ENCODERS),
            }, None

        def _settings_post(self, body: dict):
            changes = body.get("changes")
            if not isinstance(changes, dict):
                return 400, {"error": "changes を辞書で指定してください"}, None
            try:
                saved = control.save_settings(changes, config)
            except SettingsError as e:
                return 400, {"error": str(e)}, None
            return 200, {"settings": saved, "restart_needed": bool(process.running)}, None

        def _token(self):
            token = control.regenerate_token(config)
            if process.running:
                process.restart(args_for_server())
            return 200, {"token": token}, None

        def _autostart(self, body: dict):
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                return 400, {"error": "enabled は true か false にしてください"}, None
            error = control.set_autostart(enabled)
            if error:
                return 400, {"error": error}, None
            return 200, {"autostart": control.autostart_enabled()}, None

        def _update_check(self):
            try:
                from . import updater

                return 200, updater.check(), None
            except Exception as e:  # noqa: BLE001 調べられなくても、画面は動かす
                return 200, {
                    "current": __version__, "latest": None, "available": False,
                    "method": "git", "error": str(e) or "調べられませんでした",
                }, None

        def _update_start(self):
            with update.lock:
                if update.running:
                    return 409, {"error": "更新している途中です"}, None
                update.running = True
                update.done = False
                update.error = None
                update.lines = []
            threading.Thread(target=run_update, daemon=True).start()
            return 200, {"started": True, "next_key": next_key}, None

        def _admin(self):
            if not run_as_admin(["--key", next_key]):
                return 400, {"error": "管理者として起動できませんでした"}, None
            process.stop()
            return 200, {"ok": True, "next_key": next_key}, exit_event.set

        def _quit(self):
            process.stop()
            return 200, {"ok": True}, exit_event.set

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

    return Handler


# --- 起動

def remove_lock(path: Path = LOCK_PATH) -> None:
    data = read_lock(path)
    if data is None or data.get("pid") == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


def browser_of(args: argparse.Namespace) -> str:
    """開くブラウザー。--browser があればそれ、なければ管理画面の設定。"""
    if args.browser:
        return args.browser
    try:
        return str(control.read_settings().get("panel_browser", "edge"))
    except (OSError, ValueError):
        return "edge"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="duo_server.panel", description="サーバーの管理画面")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="管理画面のポート（127.0.0.1だけで待ち受ける）")
    p.add_argument("--autostart", action="store_true", help="ログイン時の自動起動（ウィンドウを開かない）")
    p.add_argument("--no-window", action="store_true", help="ウィンドウを開かない")
    p.add_argument("--restart", action="store_true", help="更新などの起動し直し（前の管理画面が終わるのを待つ）")
    p.add_argument("--key", help="起動し直しのとき、前の管理画面から渡される鍵（ページが、そのまま移れるように）")
    p.add_argument(
        "--browser", choices=control.PANEL_BROWSERS,
        help="管理画面を開くブラウザー（省略すると、管理画面の設定。初めは edge）",
    )
    return p.parse_args(argv)


def wait_until_gone(lock: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and running_panel(lock):
        time.sleep(0.3)


def bind(port: int, handler: type, retry_seconds: float) -> ThreadingHTTPServer:
    deadline = time.monotonic() + retry_seconds
    while True:
        try:
            ThreadingHTTPServer.daemon_threads = True
            return ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.3)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    CARRY_ARGS[:] = ["--browser", args.browser] if args.browser else []
    if args.port != DEFAULT_PORT:
        CARRY_ARGS.extend(["--port", str(args.port)])
    # 鍵を渡されたとき（起動し直し）は、前の管理画面のページが、そのまま新しいアドレスに移るので、ウィンドウを開かない
    show_window = not (args.autostart or args.no_window or args.key)

    if args.restart:
        wait_until_gone(LOCK_PATH, RESTART_WAIT)
    existing = running_panel()
    if existing:
        if show_window:
            open_window(f"http://127.0.0.1:{existing['port']}/?k={existing['key']}", browser_of(args))
        return 0

    key = args.key or secrets.token_urlsafe(24)
    process = control.ServerProcess()
    handler = make_app(process, key, control.DEFAULT_CONFIG)
    try:
        httpd = bind(args.port, handler, 5.0 if args.restart else 0.0)
    except OSError as e:
        print(f"管理画面を起動できませんでした（ポート {args.port}）: {e}", file=sys.stderr)
        return 1
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?k={key}"
    control.refresh_autostart()
    LOCK_PATH.write_text(json.dumps({"port": port, "key": key, "pid": os.getpid()}), encoding="utf-8")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        process.start(control.server_args(control.read_settings(), control.DEFAULT_CONFIG))
        if show_window:
            open_window(url, browser_of(args))
        print(f"管理画面を起動しました: {url}")
        while not handler.exit_signal.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("終了します")
    finally:
        process.stop()
        httpd.shutdown()
        httpd.server_close()
        remove_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
