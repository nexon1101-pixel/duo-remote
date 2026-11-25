"""ヘッドレスクライアントの新コマンド（chord / drag / sscroll / bring など）の単体テスト。

接続はせず、_send を捕まえて、サーバーへ送るメッセージの並びだけを確かめる。
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from duo_client import (  # noqa: E402
    DuoClient,
    FrameBuffer,
    describe_error,
    profile_summaries,
    resolve_key,
    resolve_target,
    run_oneshot,
)


def make_client(wait_replies: dict | None = None) -> tuple[DuoClient, list[dict]]:
    client = DuoClient.__new__(DuoClient)
    client.buffer = FrameBuffer()
    client.welcome = {}
    client.role = "control"
    client.last_notice = None
    client.last_error = None
    client._file_seq = 0
    client._pulls = {}
    client._waiters = {}
    sent: list[dict] = []
    sent_bytes: list[bytes] = []

    async def fake_send(msg: dict) -> None:
        sent.append(msg)

    async def fake_send_bytes(data: bytes) -> None:
        sent_bytes.append(data)

    async def fake_wait(t: str, timeout: float) -> dict:
        if wait_replies and t in wait_replies:
            return wait_replies[t]
        raise asyncio.TimeoutError

    client._send = fake_send  # type: ignore[method-assign]
    client._send_bytes = fake_send_bytes  # type: ignore[method-assign]
    client._wait_for = fake_wait  # type: ignore[method-assign]
    client.sent_bytes = sent_bytes  # type: ignore[attr-defined]
    return client, sent


def run(client: DuoClient, cmd: dict) -> dict:
    return asyncio.run(client.run_command(cmd))


def test_resolve_key():
    assert resolve_key(17) == {"vk": 17}
    assert resolve_key("ctrl") == {"vk": 17}
    assert resolve_key("V") == {"vk": 86}
    assert resolve_key({"sc": 30}) == {"sc": 30}
    with pytest.raises(RuntimeError):
        resolve_key("そんなキー")


def test_chord_ctrl_v_sequence():
    client, sent = make_client()
    run(client, {"cmd": "chord", "keys": ["ctrl", "v"]})
    assert sent == [
        {"t": "key", "vk": 17, "d": "down"},
        {"t": "key", "vk": 86, "d": "down"},
        {"t": "key", "vk": 86, "d": "up"},
        {"t": "key", "vk": 17, "d": "up"},
    ]


def test_chord_win_r_sequence():
    client, sent = make_client()
    run(client, {"cmd": "chord", "keys": ["lwin", "r"]})
    assert [m["vk"] for m in sent] == [91, 82, 82, 91]


def test_drag_moves_and_releases():
    client, sent = make_client()
    run(client, {"cmd": "drag", "x1": 0.1, "y1": 0.2, "x2": 0.5, "y2": 0.2, "steps": 4})
    assert sent[0] == {"t": "move", "x": 0.1, "y": 0.2}
    assert sent[1]["t"] == "button" and sent[1]["d"] == "down"
    assert sent[-1]["t"] == "button" and sent[-1]["d"] == "up"
    moves = [m for m in sent if m["t"] == "move"]
    assert moves[-1] == {"t": "move", "x": 0.5, "y": 0.2}


def test_sscroll_splits_delta():
    client, sent = make_client()
    run(client, {"cmd": "sscroll", "dy": -3, "steps": 6})
    assert len(sent) == 6
    assert all(m == {"t": "scroll", "dx": 0.0, "dy": -0.5} for m in sent)


def test_button_validates_d():
    client, _ = make_client()
    with pytest.raises(RuntimeError):
        run(client, {"cmd": "button", "which": "left", "down": True})


def test_quality_requires_a_field_and_resets_buffer():
    client, sent = make_client()
    with pytest.raises(RuntimeError):
        run(client, {"cmd": "quality"})
    run(client, {"cmd": "quality", "max_height": 0})
    assert sent == [{"t": "quality", "max_height": 0}]


def test_bring_waits_for_brought():
    client, sent = make_client({"brought": {"t": "brought", "id": 7, "title": "メモ帳"}})
    out = run(client, {"cmd": "bring", "id": 7})
    assert sent == [{"t": "bring", "id": 7}]
    assert out == {"id": 7, "title": "メモ帳"}


def test_clipset_sends_clip_set():
    client, sent = make_client()
    run(client, {"cmd": "clipset", "s": "貼る文字"})
    assert sent == [{"t": "clip_set", "s": "貼る文字"}]


def test_peek_reads_via_clipboard_and_restores():
    client, sent = make_client({"clip": {"t": "clip", "s": "欄の中身"}})
    out = run(client, {"cmd": "peek"})
    assert out == {"s": "欄の中身"}
    types = [m["t"] for m in sent]
    # 退避の clip_get → 全選択＋コピー（key の並び）→ clip_get → 復帰の clip_set → 選択解除の key
    assert types[0] == "clip_get"
    clip_gets = [i for i, t in enumerate(types) if t == "clip_get"]
    assert len(clip_gets) == 2
    assert "clip_set" in types[clip_gets[1]:]


def test_send_file_frames(tmp_path):
    src = tmp_path / "hello.txt"
    src.write_bytes(b"hello" * 1000)
    client, sent = make_client({"file_saved": {"t": "file_saved", "id": 1, "path": "C:/x/hello.txt"}})
    out = run(client, {"cmd": "send", "path": str(src)})
    assert sent[0] == {"t": "file_put", "id": 1, "name": "hello.txt", "size": 5000}
    assert sent[-1] == {"t": "file_end", "id": 1}
    frames = client.sent_bytes  # type: ignore[attr-defined]
    assert all(f[0] & 0x04 for f in frames)  # FLAG_FILE
    assert b"".join(f[5:] for f in frames) == b"hello" * 1000
    assert out == {"path": "C:/x/hello.txt", "size": 5000}


def fake_args(**kw):
    import types

    base = {"profile": None, "host": None, "port": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


PROFILES_CONFIG = {
    "profiles": {
        "main-pc": {"host": "100.0.0.1", "port": 8765, "token": "tok-main"},
        "ui-pc": {"port": 8765, "token": "tok-ui"},
    },
    "default": "main-pc",
}


def test_describe_error_never_empty():
    assert "時間切れ" in describe_error(asyncio.TimeoutError())
    assert "断られました" in describe_error(ConnectionRefusedError(111, "Connection refused"))
    assert "x" in describe_error(RuntimeError("x"))
    assert describe_error(RuntimeError(""))  # 空でも何か返る
    assert describe_error(ValueError())  # 空でも何か返る


def test_resolve_target_profiles():
    host, port, token, name = resolve_target(fake_args(), PROFILES_CONFIG)
    assert (host, port, token, name) == ("100.0.0.1", 8765, "tok-main", "main-pc")
    # --host はプロファイルより優先
    host, _, _, _ = resolve_target(fake_args(host="100.0.0.9"), PROFILES_CONFIG)
    assert host == "100.0.0.9"
    # 知らないプロファイルは、あるものを挙げて断る
    with pytest.raises(RuntimeError, match="ありません"):
        resolve_target(fake_args(profile="nope"), PROFILES_CONFIG)
    # host 未設定のプロファイルは、理由が分かる形で断る
    with pytest.raises(RuntimeError, match="host が設定されていません"):
        resolve_target(fake_args(profile="ui-pc"), PROFILES_CONFIG)


def test_resolve_target_flat_config_compat():
    host, port, token, name = resolve_target(fake_args(), {"host": "h1", "port": 9000, "token": "t"})
    assert (host, port, token, name) == ("h1", 9000, "t", "default")


def test_profile_summaries_hide_token():
    out = profile_summaries(PROFILES_CONFIG)
    assert [p["name"] for p in out] == ["main-pc", "ui-pc"]
    assert out[0]["default"] is True and out[0]["has_token"] is True
    assert "tok-main" not in str(out) and "tok-ui" not in str(out)


def test_fail_waiters_gives_reason():
    client = DuoClient.__new__(DuoClient)
    client._waiters = {}
    client.close_detail = "コード 1008／サーバーの返事: トークンが違います"
    client.last_error = None

    async def scenario():
        task = asyncio.create_task(client._wait_for("welcome", 30))
        await asyncio.sleep(0.05)
        client._fail_waiters()
        return await task

    with pytest.raises(ConnectionError, match="1008"):
        asyncio.run(scenario())


def test_run_oneshot_collects_replies_and_errors():
    client, _sent = make_client({"pong": {"t": "pong", "n": 42}})
    replies, all_ok = asyncio.run(run_oneshot(client, [
        '{"cmd": "ping"}',
        "not json",
        '{"cmd": "bogus"}',
    ]))
    assert all_ok is False
    assert replies[0]["ok"] is True and replies[0]["n"] == 42
    assert replies[1]["ok"] is False and replies[1]["error"]
    assert replies[2]["ok"] is False and replies[2]["error"]


def test_pull_file_roundtrip(tmp_path):
    import struct as _struct

    from duo_client import FRAME_HEADER

    client, sent = make_client()

    async def scenario():
        task = asyncio.create_task(client.run_command({"cmd": "pull", "dir": str(tmp_path)}))
        await asyncio.sleep(0.05)
        client._handle_text('{"t":"file_begin","id":3,"name":"pic.png","size":4,"mime":"image/png"}')
        client._handle_bytes(FRAME_HEADER.pack(0x04, 3) + b"PNG!")
        client._handle_text('{"t":"file_end","id":3}')
        return await task

    out = asyncio.run(scenario())
    assert sent == [{"t": "clip_pull"}]
    assert out["kind"] == "file" and out["size"] == 4
    assert (tmp_path / "pic.png").read_bytes() == b"PNG!"
    assert _struct.calcsize(">BI") == 5
