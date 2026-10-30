import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from duo_server import updater
from duo_server.updater import UpdateError


def test_parse_version():
    assert updater.parse_version('"""doc"""\n__version__ = "0.12.0"\n') == "0.12.0"
    assert updater.parse_version("__version__ = '1.2'") == "1.2"
    with pytest.raises(UpdateError):
        updater.parse_version("x = 1")


def test_version_tuple():
    assert updater.version_tuple("0.12.0") == (0, 12, 0)
    assert updater.version_tuple("0.9.0") < updater.version_tuple("0.12.0")
    for bad in ("", "1.x", "1.0-beta", "a"):
        with pytest.raises(UpdateError):
            updater.version_tuple(bad)


def test_check(monkeypatch):
    cur = updater.__version__
    monkeypatch.setattr(updater, "latest_version", lambda timeout=10: "999.0.0")
    r = updater.check()
    assert r["available"] is True and r["latest"] == "999.0.0" and r["error"] is None
    assert r["current"] == cur and r["method"] in ("git", "zip")

    monkeypatch.setattr(updater, "latest_version", lambda timeout=10: cur)
    r = updater.check()
    assert r["available"] is False and r["error"] is None

    def fail(timeout=10):
        raise UpdateError("新しい版を調べられませんでした: x")

    monkeypatch.setattr(updater, "latest_version", fail)
    r = updater.check()
    assert r["available"] is False and r["latest"] is None and "x" in r["error"]


def test_latest_version_network_error(monkeypatch):
    def boom(url, timeout):
        raise OSError("接続できません")

    monkeypatch.setattr(updater, "_download", boom)
    with pytest.raises(UpdateError, match="新しい版を調べられませんでした"):
        updater.latest_version()


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_apply(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "server/.venv").mkdir(parents=True)
    (root / "server/duo_server").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "server/config.json").write_text("SECRET")
    (root / "server/.venv/x").write_text("venv")
    (root / "server/duo_server.log").write_text("log")
    (root / "server/duo_server.log.1").write_text("log1")
    (root / "server/panel.lock").write_text("lock")
    (root / ".git/HEAD").write_text("head")
    (root / "server/duo_server/__init__.py").write_text('__version__ = "0.1.0"\n')
    (root / "server/duo_server/only_local.py").write_text("local")

    p = "duo-remote-main/"
    data = _make_zip({
        p + "server/duo_server/__init__.py": b'__version__ = "9.9.9"\n',
        p + "server/duo_server/new.py": b"new",
        p + "server/config.json": b"OVERWRITTEN",
        p + "server/.venv/x": b"OVERWRITTEN",
        p + "server/duo_server.log": b"OVERWRITTEN",
        p + "server/duo_server.log.1": b"OVERWRITTEN",
        p + "server/panel.lock": b"OVERWRITTEN",
        p + ".git/HEAD": b"OVERWRITTEN",
        p + "server/duo_server/__pycache__/a.pyc": b"OVERWRITTEN",
        p + "docs/plan.md": b"plan",
    })
    monkeypatch.setattr(updater, "_download", lambda url, timeout: data)
    monkeypatch.setattr(updater.shutil, "which", lambda name: None)  # 強制的に zip
    logs: list[str] = []
    updater.apply(logs.append, root=root)

    assert (root / "server/duo_server/__init__.py").read_text() == '__version__ = "9.9.9"\n'
    assert (root / "server/duo_server/new.py").read_text() == "new"
    assert (root / "docs/plan.md").read_text() == "plan"
    assert (root / "server/config.json").read_text() == "SECRET"
    assert (root / "server/.venv/x").read_text() == "venv"
    assert (root / "server/duo_server.log").read_text() == "log"
    assert (root / "server/duo_server.log.1").read_text() == "log1"
    assert (root / "server/panel.lock").read_text() == "lock"
    assert (root / ".git/HEAD").read_text() == "head"
    assert not (root / "server/duo_server/__pycache__").exists()
    assert (root / "server/duo_server/only_local.py").read_text() == "local"
    assert not list(root.rglob("*.duo-new"))
    assert "更新しました" in logs[-1]


@pytest.mark.parametrize("bad", ["duo-remote-main/../evil", "../evil", "/abs/evil"])
def test_zip_rejects_unsafe_path(tmp_path, monkeypatch, bad):
    root = tmp_path / "repo"
    root.mkdir()
    data = _make_zip({"duo-remote-main/ok.txt": b"ok", bad: b"evil"})
    monkeypatch.setattr(updater, "_download", lambda url, timeout: data)
    monkeypatch.setattr(updater.shutil, "which", lambda name: None)
    with pytest.raises(UpdateError, match="不正なパス"):
        updater.apply(lambda s: None, root=root)
    assert not (tmp_path / "evil").exists()
    assert not (root / "ok.txt").exists()  # 何も書かない


def _git(*args, cwd=None):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git がありません")
def test_git_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "BRANCH", "main")
    origin = tmp_path / "origin.git"
    _git("init", "--bare", str(origin))
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)

    seed = tmp_path / "seed"
    _git("init", str(seed))
    _git("checkout", "-B", "main", cwd=seed)
    (seed / "a.txt").write_text("1")
    _git("add", ".", cwd=seed)
    _git("commit", "-m", "first", cwd=seed)
    _git("push", str(origin), "main", cwd=seed)

    clone = tmp_path / "clone"
    _git("clone", str(origin), str(clone))
    assert updater.install_method(clone) == "git"

    (seed / "a.txt").write_text("2")
    (seed / "b.txt").write_text("new")
    _git("add", ".", cwd=seed)
    _git("commit", "-m", "second", cwd=seed)
    _git("push", str(origin), "main", cwd=seed)

    logs: list[str] = []
    updater.apply(logs.append, root=clone)
    assert (clone / "a.txt").read_text() == "2"
    assert (clone / "b.txt").read_text() == "new"
    assert "更新しました" in logs[-1]


@pytest.mark.skipif(shutil.which("git") is None, reason="git がありません")
def test_git_apply_failure(tmp_path):
    repo = tmp_path / "r"
    _git("init", str(repo))  # origin がないので失敗する
    with pytest.raises(UpdateError, match="git pull に失敗"):
        updater.apply(lambda s: None, root=repo)


def test_install_method_zip_without_git_dir(tmp_path):
    assert updater.install_method(tmp_path) == "zip"
