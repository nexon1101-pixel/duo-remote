"""ViGEmBus（仮想のゲームパッドのドライバー）を入れる。管理画面の「ドライバーを入れる」から呼ばれる。

取る場所は、GitHub の nefarius/ViGEmBus のリリースだけ。取ったあと、実行する前に、大きさ、SHA-256（あれば）、
署名を確認する。実行は、管理者の確認（UAC）つきで、インストーラーの画面をそのまま出す。
ネットワークと PowerShell の呼び出しは、引数で差し替えられる（テストでは偽物を渡す）。
本物の動き（GitHub からの取得、PowerShell、インストーラー）は、Linux では確認できない。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

RELEASE_API = "https://api.github.com/repos/nefarius/ViGEmBus/releases/latest"
ALLOWED_HOSTS = {
    "github.com", "api.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
}
SIGNER_NAME = "Nefarius"
# 取るファイルの大きさの上限（想定より大きいものは取らない）
MAX_DOWNLOAD = 100 * 1024 * 1024
TIMEOUT = 30

Progress = Callable[[str, str], None]
Fetch = Callable[[str], bytes]
# 署名の確認: パスを渡すと (状態, 署名者の名前) を返す
SignatureChecker = Callable[[Path], "tuple[str, str]"]
Runner = Callable[[Path], None]


class DriverError(Exception):
    """人に見せる理由（日本語）つきの失敗。"""


# --- 純粋な関数

def check_url(url: str) -> bool:
    """HTTPS で、github.com か githubusercontent.com の中のホストだけ通す。"""
    try:
        parts = urlparse(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    return host in ALLOWED_HOSTS or host.endswith(".githubusercontent.com")


def find_asset(release: Any) -> dict | None:
    """リリースの JSON から、.exe の資産（なければ .msi）を選ぶ。{name, url, size, digest} を返す。"""
    if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
        return None
    for suffix in (".exe", ".msi"):
        for asset in release["assets"]:
            if not isinstance(asset, dict):
                continue
            name, url = asset.get("name"), asset.get("browser_download_url")
            if isinstance(name, str) and isinstance(url, str) and name.lower().endswith(suffix):
                size, digest = asset.get("size"), asset.get("digest")
                return {
                    "name": name, "url": url,
                    "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                    "digest": digest if isinstance(digest, str) else None,
                }
    return None


def verify(path: Path, expected_size: int | None, digest: str | None, signature_checker: SignatureChecker) -> None:
    """実行する前の確認。合わなければ DriverError（理由つき）。"""
    actual = path.stat().st_size
    if expected_size is not None and actual != expected_size:
        raise DriverError(f"ファイルの大きさが合いません（{actual} バイト、想定は {expected_size} バイト）")
    if digest:
        algo, _, want = digest.partition(":")
        if algo.lower() != "sha256" or not want:
            raise DriverError("SHA-256 以外の確認用の値は扱えません")
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest().lower() != want.strip().lower():
            raise DriverError("SHA-256 が合いません")
    status, signer = signature_checker(path)
    if status != "Valid":
        raise DriverError(f"署名が正しくありません（{status or '不明'}）")
    if SIGNER_NAME.lower() not in (signer or "").lower():
        raise DriverError(f"署名者が {SIGNER_NAME} ではありません")


# --- ネットワーク

class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not check_url(newurl):
            raise DriverError("GitHub 以外の場所への転送は受けません")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_fetch(url: str) -> bytes:
    """url の中身を取る。HTTPS で GitHub の中だけ（転送先も同じ）。"""
    if not check_url(url):
        raise DriverError("GitHub 以外の場所からは取りません")
    opener = urllib.request.build_opener(_CheckedRedirect)
    req = urllib.request.Request(
        url, headers={"User-Agent": "duo-remote", "Accept": "application/octet-stream, application/json"},
    )
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            data = resp.read(MAX_DOWNLOAD + 1)
    except DriverError:
        raise
    except (urllib.error.URLError, OSError) as e:
        raise DriverError(f"GitHub につながりません（{e}）") from e
    if len(data) > MAX_DOWNLOAD:
        raise DriverError("ファイルが大きすぎます")
    return data


def fetch_release(fetch: Fetch = default_fetch) -> dict:
    """ViGEmBus の最新のリリースの情報を取る。"""
    try:
        data = json.loads(fetch(RELEASE_API))
    except ValueError as e:
        raise DriverError("リリースの情報を読めませんでした") from e
    if not isinstance(data, dict):
        raise DriverError("リリースの情報を読めませんでした")
    return data


def download(url: str, dest: Path, fetch: Fetch = default_fetch) -> None:
    if not check_url(url):
        raise DriverError("GitHub 以外の場所からは取りません")
    dest.write_bytes(fetch(url))


# --- Windows の本物

def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def powershell_signature(path: Path) -> tuple[str, str]:
    """Get-AuthenticodeSignature で、署名の状態と署名者の名前を調べる。Windows でだけ動く。"""
    script = (
        f"$s = Get-AuthenticodeSignature -LiteralPath {_ps_quote(str(path))}; "
        "$s.Status; if ($s.SignerCertificate) { $s.SignerCertificate.Subject }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise DriverError(f"署名を確認できませんでした（{type(e).__name__}）") from e
    lines = out.stdout.splitlines()
    return (lines[0].strip() if lines else ""), (lines[1].strip() if len(lines) > 1 else "")


def powershell_run(path: Path) -> None:
    """管理者の確認（UAC）つきで、インストーラーを実行し、終わるまで待つ。静かなオプションは付けない。"""
    if path.suffix.lower() == ".msi":
        command = (
            "Start-Process -FilePath msiexec -ArgumentList "
            f"'/i', {_ps_quote(chr(34) + str(path) + chr(34))} -Verb RunAs -Wait"
        )
    else:
        command = f"Start-Process -FilePath {_ps_quote(str(path))} -Verb RunAs -Wait"
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise DriverError(f"インストーラーを実行できませんでした（{type(e).__name__}）") from e
    if out.returncode != 0:
        raise DriverError("インストーラーを実行できませんでした（管理者の確認で断られた可能性があります）")


# --- 全体の流れ

def install_vigem(
    progress: Progress,
    fetch: Fetch = default_fetch,
    signature_checker: SignatureChecker = powershell_signature,
    runner: Runner = powershell_run,
    platform: str | None = None,
    tmp_root: str | None = None,
) -> bool:
    """調べる→取る→検証→実行→片付け。段階ごとに progress(state, message) を呼ぶ。成功なら True。"""
    if (platform or sys.platform) != "win32":
        progress("error", "Windows でだけ使えます")
        return False
    tmpdir: tempfile.TemporaryDirectory | None = None
    try:
        progress("downloading", "ダウンロード中…")
        asset = find_asset(fetch_release(fetch))
        if asset is None:
            raise DriverError("リリースにインストーラーが見つかりません")
        if not check_url(asset["url"]):
            raise DriverError("GitHub 以外の場所からは取りません")
        tmpdir = tempfile.TemporaryDirectory(prefix="duo-vigem-", dir=tmp_root)
        # ファイル名は GitHub が返したもの。パスの区切りは捨てる
        dest = Path(tmpdir.name) / (os.path.basename(asset["name"].replace("\\", "/")) or "ViGEmBus.exe")
        download(asset["url"], dest, fetch)
        progress("verifying", "確認中…")
        verify(dest, asset["size"], asset["digest"], signature_checker)
        progress("waiting", "インストーラーの画面で、操作してください")
        runner(dest)
    except DriverError as e:
        progress("error", str(e) or "入れられませんでした")
        return False
    except Exception as e:  # noqa: BLE001 失敗は画面に伝える
        progress("error", f"入れられませんでした（{type(e).__name__}）")
        return False
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()
    progress("done", "入れました。サーバーを再起動してください")
    return True
