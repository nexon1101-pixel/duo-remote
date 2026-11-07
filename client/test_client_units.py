"""ヘッドレスクライアントの新コマンド（chord / drag / sscroll / bring など）の単体テスト。

接続はせず、_send を捕まえて、サーバーへ送るメッセージの並びだけを確かめる。
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from duo_client import DuoClient, FrameBuffer, resolve_key  # noqa: E402


def make_client(wait_replies: dict | None = None) -> tuple[DuoClient, list[dict]]:
    client = DuoClient.__new__(DuoClient)
    client.buffer = FrameBuffer()
    client.welcome = {}
    client.role = "control"
    client.last_notice = None
    client.last_error = None
    sent: list[dict] = []

    async def fake_send(msg: dict) -> None:
        sent.append(msg)

    async def fake_wait(t: str, timeout: float) -> dict:
        if wait_replies and t in wait_replies:
            return wait_replies[t]
        raise asyncio.TimeoutError

    client._send = fake_send  # type: ignore[method-assign]
    client._wait_for = fake_wait  # type: ignore[method-assign]
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
