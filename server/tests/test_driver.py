"""ViGEmBus を入れる処理（driver.py）のテスト。ネットワークにはつながず、偽物の fetch、署名確認、実行を渡す。
Windows の本物の動き（PowerShell、インストーラー）は、ここでは確認できない。"""
import hashlib
import json
import threading
import time

import pytest

from duo_server import control, driver, panel

DATA = b"MZ-fake-installer"
RELEASE = {
    "assets": [
        {"name": "ViGEmBus_1.22.0_x64_x86_arm64.exe", "browser_download_url": "https://github.com/nefarius/ViGEmBus/releases/download/v1/a.exe",
         "size": len(DATA), "digest": "sha256:" + hashlib.sha256(DATA).hexdigest()},
        {"name": "ViGEmBus.msi", "browser_download_url": "https://github.com/x/b.msi", "size": 5},
    ],
}


def good_sig(path):
    return "Valid", "CN=Nefarius Software Solutions e.U., O=Nefarius"


def make_fetch(release=RELEASE, data=DATA):
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps(release).encode() if url == driver.RELEASE_API else data

    fetch.calls = calls
    return fetch


# --- find_asset

def test_find_asset_prefers_exe():
    a = driver.find_asset(RELEASE)
    assert a["name"].endswith(".exe") and a["size"] == len(DATA) and a["digest"].startswith("sha256:")
    assert a["url"].startswith("https://github.com/")


def test_find_asset_msi_only():
    a = driver.find_asset({"assets": [{"name": "x.msi", "browser_download_url": "https://github.com/x.msi", "size": 3}]})
    assert a["name"] == "x.msi" and a["digest"] is None


def test_find_asset_none():
    assert driver.find_asset({"assets": [{"name": "x.zip", "browser_download_url": "https://github.com/x.zip"}]}) is None
    assert driver.find_asset({"assets": []}) is None
    assert driver.find_asset({}) is None
    assert driver.find_asset([]) is None


# --- check_url

@pytest.mark.parametrize("url", [
    "https://github.com/nefarius/ViGEmBus/releases/download/v1/a.exe",
    "https://api.github.com/repos/nefarius/ViGEmBus/releases/latest",
    "https://objects.githubusercontent.com/x",
    "https://release-assets.githubusercontent.com/x",
    "https://foo.githubusercontent.com/x",
])
def test_check_url_ok(url):
    assert driver.check_url(url)


@pytest.mark.parametrize("url", [
    "http://github.com/x", "https://example.com/x", "https://github.com.evil.example/x",
    "https://evilgithubusercontent.com/x", "https://user@github.com/x", "ftp://github.com/x", "", "github.com/x",
])
def test_check_url_ng(url):
    assert not driver.check_url(url)


# --- verify

def make_file(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(DATA)
    return p


def test_verify_all_ok(tmp_path):
    driver.verify(make_file(tmp_path), len(DATA), "sha256:" + hashlib.sha256(DATA).hexdigest(), good_sig)
    driver.verify(make_file(tmp_path), None, None, good_sig)  # 大きさも digest も無ければ、署名だけ


def test_verify_wrong_size(tmp_path):
    with pytest.raises(driver.DriverError, match="大きさ"):
        driver.verify(make_file(tmp_path), len(DATA) + 1, None, good_sig)


def test_verify_wrong_sha(tmp_path):
    with pytest.raises(driver.DriverError, match="SHA-256"):
        driver.verify(make_file(tmp_path), len(DATA), "sha256:" + "0" * 64, good_sig)


def test_verify_signature_not_valid(tmp_path):
    with pytest.raises(driver.DriverError, match="署名"):
        driver.verify(make_file(tmp_path), None, None, lambda p: ("NotSigned", ""))


def test_verify_signer_not_nefarius(tmp_path):
    with pytest.raises(driver.DriverError, match="署名者"):
        driver.verify(make_file(tmp_path), None, None, lambda p: ("Valid", "CN=Someone Else"))


# --- install_vigem

def run_install(tmp_path, **kw):
    events, ran = [], []
    kw.setdefault("fetch", make_fetch())
    kw.setdefault("signature_checker", good_sig)
    kw.setdefault("runner", lambda p: ran.append((p, p.exists())))
    ok = driver.install_vigem(lambda s, m: events.append((s, m)), platform="win32", tmp_root=str(tmp_path), **kw)
    return ok, events, ran


def test_install_flow(tmp_path):
    ok, events, ran = run_install(tmp_path)
    assert ok
    assert [s for s, _ in events] == ["downloading", "verifying", "waiting", "done"]
    assert len(ran) == 1 and ran[0][1] is True  # 実行のときは、ファイルがある
    assert not ran[0][0].exists() and list(tmp_path.iterdir()) == []  # 終わったら消える


def test_install_verify_fails_no_run(tmp_path):
    ok, events, ran = run_install(tmp_path, signature_checker=lambda p: ("HashMismatch", "CN=Nefarius"))
    assert not ok and ran == []
    assert [s for s, _ in events] == ["downloading", "verifying", "error"]
    assert "署名" in events[-1][1]
    assert list(tmp_path.iterdir()) == []


def test_install_wrong_sha_no_run(tmp_path):
    ok, events, ran = run_install(tmp_path, fetch=make_fetch(data=b"x" * len(DATA)))
    assert not ok and ran == [] and events[-1][0] == "error" and "SHA-256" in events[-1][1]
    assert list(tmp_path.iterdir()) == []


def test_install_no_asset(tmp_path):
    ok, events, ran = run_install(tmp_path, fetch=make_fetch(release={"assets": []}))
    assert not ok and ran == [] and events[-1][0] == "error"


def test_install_rejects_foreign_url(tmp_path):
    rel = {"assets": [{"name": "a.exe", "browser_download_url": "https://example.com/a.exe", "size": 1}]}
    fetch = make_fetch(release=rel)
    ok, events, ran = run_install(tmp_path, fetch=fetch)
    assert not ok and ran == [] and fetch.calls == [driver.RELEASE_API]


def test_install_runner_error_cleans_up(tmp_path):
    def boom(p):
        raise driver.DriverError("断られました")

    ok, events, ran = run_install(tmp_path, runner=boom)
    assert not ok and events[-1] == ("error", "断られました")
    assert list(tmp_path.iterdir()) == []


def test_install_fetch_error(tmp_path):
    def bad(url):
        raise driver.DriverError("つながりません")

    ok, events, _ = run_install(tmp_path, fetch=bad)
    assert not ok and events[-1] == ("error", "つながりません")


def test_install_not_windows(tmp_path):
    events = []
    fetch = make_fetch()
    assert not driver.install_vigem(lambda s, m: events.append((s, m)), fetch=fetch, platform="linux")
    assert events == [("error", "Windows でだけ使えます")] and fetch.calls == []


# --- panel の API（test_panel.py と同じ作り）

from test_panel import KEY, app  # noqa: E402,F401


def test_status_has_pad_install(app):
    status, data = app("GET", "/api/status")
    assert status == 200 and data["pad_install"] == {"state": "idle", "message": ""}


def test_driver_api_needs_key(app):
    status, _ = app("POST", "/api/driver/vigem", {}, key=None)
    assert status == 403
    status, _ = app("POST", "/api/driver/vigem", {}, key="wrong")
    assert status == 403


def test_driver_api_starts_and_conflicts(app, monkeypatch):
    gate = threading.Event()
    seen = []

    def fake_install(progress):
        progress("downloading", "ダウンロード中…")
        seen.append("started")
        gate.wait(5)
        progress("done", "入れました。サーバーを再起動してください")
        return True

    monkeypatch.setattr(panel.driver, "install_vigem", fake_install)
    status, data = app("POST", "/api/driver/vigem", {})
    assert status == 200 and data == {"started": True}
    for _ in range(100):
        if seen:
            break
        time.sleep(0.02)
    status, data = app("POST", "/api/driver/vigem", {})
    assert status == 409 and data["error"]
    assert app("GET", "/api/status")[1]["pad_install"]["state"] == "downloading"
    gate.set()
    for _ in range(100):
        if app("GET", "/api/status")[1]["pad_install"]["state"] == "done":
            break
        time.sleep(0.02)
    assert app("GET", "/api/status")[1]["pad_install"]["state"] == "done"
    # 終わったあとは、また始められる
    for _ in range(100):
        status, _ = app("POST", "/api/driver/vigem", {})
        if status == 200:
            break
        time.sleep(0.02)
    assert status == 200
    gate.set()


def test_driver_api_reprobes_after_done(app, monkeypatch):
    calls = []
    monkeypatch.setattr(panel.gamepad, "probe", lambda: (calls.append(1), (len(calls) > 1, ""))[1])
    assert app("GET", "/api/status")[1]["pad"]["available"] is False
    assert app("GET", "/api/status")[1]["pad"]["available"] is False  # 1回だけ調べる
    assert len(calls) == 1
    monkeypatch.setattr(panel.driver, "install_vigem", lambda progress: progress("done", "ok") or True)
    app("POST", "/api/driver/vigem", {})
    for _ in range(100):
        data = app("GET", "/api/status")[1]
        if data["pad_install"]["state"] == "done":
            break
        time.sleep(0.02)
    assert data["pad"]["available"] is True and len(calls) == 2


def test_driver_api_non_windows_error(app):
    app("POST", "/api/driver/vigem", {})
    for _ in range(100):
        data = app("GET", "/api/status")[1]
        if data["pad_install"]["state"] == "error":
            break
        time.sleep(0.02)
    import sys

    if sys.platform != "win32":
        assert data["pad_install"] == {"state": "error", "message": "Windows でだけ使えます"}
