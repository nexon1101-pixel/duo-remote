"""サーバーの更新。

git clone で取った場合は git pull、ZIP で取った場合は ZIP を取り直して上書きする。
標準ライブラリだけを使う。管理画面から呼ばれる。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from . import __version__

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent

REPO = "nexon1101-pixel/duo-remote"
BRANCH = "main"

# ZIP から上書きしないもの（リポジトリのルートからの相対パス）
_PROTECTED_FILES = {"server/config.json", "server/panel.lock"}
_PROTECTED_DIRS = {".git", "server/.venv"}
_PROTECTED_PREFIXES = ("server/duo_server.log",)


class UpdateError(Exception):
    """利用者に見せる日本語のメッセージを持つ。"""


def parse_version(text: str) -> str:
    m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", text, re.MULTILINE)
    if not m:
        raise UpdateError("版の番号が見つかりませんでした")
    return m.group(1).strip()


def version_tuple(v: str) -> tuple[int, ...]:
    parts = v.strip().split(".")
    if not parts or not all(p.isascii() and p.isdigit() for p in parts):
        raise UpdateError(f"版の番号を読めませんでした: {v}")
    return tuple(int(p) for p in parts)


def install_method(root: Path = REPO_ROOT) -> str:
    if (root / ".git").exists() and shutil.which("git"):
        return "git"
    return "zip"


def _download(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "duo-remote-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def latest_version(timeout: float = 10) -> str:
    url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/server/duo_server/__init__.py"
    try:
        text = _download(url, timeout).decode("utf-8")
        return parse_version(text)
    except UpdateError as e:
        raise UpdateError(f"新しい版を調べられませんでした: {e}") from e
    except (OSError, ValueError) as e:  # URLError、タイムアウト、文字コードの誤りなど
        raise UpdateError(f"新しい版を調べられませんでした: {e}") from e


def check(timeout: float = 10) -> dict:
    result = {
        "current": __version__,
        "latest": None,
        "available": False,
        "method": "zip",
        "error": None,
    }
    try:
        result["method"] = install_method()
        latest = latest_version(timeout)
        result["latest"] = latest
        result["available"] = version_tuple(latest) > version_tuple(__version__)
    except UpdateError as e:
        result["error"] = str(e)
    except Exception as e:  # 管理画面を落とさない
        result["error"] = f"新しい版を調べられませんでした: {e}"
    return result


def _is_protected(rel: str) -> bool:
    parts = rel.split("/")
    if "__pycache__" in parts:
        return True
    if rel in _PROTECTED_FILES or any(rel.startswith(p) for p in _PROTECTED_PREFIXES):
        return True
    for d in _PROTECTED_DIRS:
        if rel == d or rel.startswith(d + "/"):
            return True
    return False


def _unsafe_name(name: str) -> bool:
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return True
    return ".." in re.split(r"[/\\]", name)


def _run_git(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as e:
        raise UpdateError("git の実行が時間内に終わりませんでした") from e
    except OSError as e:
        raise UpdateError(f"git を実行できませんでした: {e}") from e


def _apply_git(log: Callable[[str], None], root: Path, timeout: float) -> None:
    log("git で最新の版を取得しています")
    res = _run_git(["-C", str(root), "pull", "--ff-only", "origin", BRANCH], timeout)
    out = (res.stdout or "") + (res.stderr or "")
    for line in out.strip().splitlines()[-5:]:
        log(line)
    if res.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-5:])
        raise UpdateError(
            "git pull に失敗しました。手元で変えたファイルがあるかもしれません。"
            "戻すか退避してから、もう一度試してください。\n" + tail
        )


def _apply_zip(log: Callable[[str], None], root: Path, timeout: float) -> None:
    url = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
    log("ZIP をダウンロードしています")
    try:
        data = _download(url, timeout)
    except (OSError, ValueError) as e:
        raise UpdateError(f"ダウンロードできませんでした: {e}") from e

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError("ダウンロードしたファイルを ZIP として開けませんでした") from e

    with zf, tempfile.TemporaryDirectory(prefix="duo-update-") as tmp:
        # 先に全部の名前を確かめる。危ないものが1つでもあれば、何も書かない
        entries: list[tuple[zipfile.ZipInfo, str]] = []
        for info in zf.infolist():
            if _unsafe_name(info.filename):
                raise UpdateError(f"ZIP の中に使えないパスがあります: {info.filename}")
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if len(parts) < 2:  # 先頭のフォルダに入っていないファイルは使わない
                continue
            entries.append((info, "/".join(parts[1:])))

        log("ファイルを上書きしています")
        count = 0
        root_resolved = root.resolve()
        for info, rel in entries:
            if _is_protected(rel):
                continue
            dest = (root / rel).resolve()
            if root_resolved != dest and root_resolved not in dest.parents:
                raise UpdateError(f"ZIP の中に使えないパスがあります: {info.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = Path(tmp) / f"{count}.part"
            with zf.open(info) as src, open(tmp_file, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # 同じドライブに一時ファイルを作ってから入れ替える
            staged = dest.with_name(dest.name + ".duo-new")
            shutil.copyfile(tmp_file, staged)
            os.replace(staged, dest)
            count += 1
        log(f"{count} 個のファイルを更新しました")


def apply(log: Callable[[str], None] = print, root: Path = REPO_ROOT, timeout: float = 60) -> None:
    method = install_method(root)
    if method == "git":
        _apply_git(log, root, timeout)
    else:
        _apply_zip(log, root, timeout)
    log("更新しました。管理画面を起動し直します")
