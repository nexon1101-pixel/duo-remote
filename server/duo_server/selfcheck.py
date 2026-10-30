"""サーバーの自己診断。起動: python -m duo_server.selfcheck

動いているサーバーに、このPCからつないで、次を確かめる。iPhone は使わない。
- 接続とトークン
- 映像が届くか（フレーム数、キーフレーム、ビットレート）
- 往復の時間（ping）
- ウィンドウの一覧
- --window を付けたとき: ウィンドウ1つだけの表示に切り替えられるか（そのウィンドウが前に出る）

マウスとキーボードは動かさない。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from websockets.asyncio.client import connect

from . import protocol
from .capture import iter_nals

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"
TIMEOUT_SEC = 5


class Report:
    def __init__(self) -> None:
        self.ok = True

    def good(self, message: str) -> None:
        print(f"[OK] {message}")

    def bad(self, message: str) -> None:
        self.ok = False
        print(f"[NG] {message}")

    def info(self, message: str) -> None:
        print(f"     {message}")


async def _recv_json(ws, wanted: set[str], timeout: float = TIMEOUT_SEC) -> dict:
    """種類が wanted のどれかの制御メッセージが来るまで読む（映像と、ほかのメッセージは読み飛ばす）。"""
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise asyncio.TimeoutError
        raw = await asyncio.wait_for(ws.recv(), left)
        if isinstance(raw, str):
            msg = json.loads(raw)
            if msg.get("t") in wanted:
                return msg


async def _count_frames(ws, seconds: float) -> dict:
    frames = keyframes = size = 0
    first_is_key = None
    has_parameter_sets = False
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        try:
            raw = await asyncio.wait_for(ws.recv(), left)
        except asyncio.TimeoutError:
            break
        if not isinstance(raw, bytes):
            continue
        key, _, payload = protocol.unpack_frame(raw)
        frames += 1
        size += len(raw)
        if first_is_key is None:
            first_is_key = key
        if key:
            keyframes += 1
            types = {t for _, t in iter_nals(payload)}
            has_parameter_sets = has_parameter_sets or {7, 8} <= types
    return {
        "frames": frames, "keyframes": keyframes, "bytes": size,
        "first_is_key": bool(first_is_key), "has_parameter_sets": has_parameter_sets,
    }


async def run_check(url: str, token: str, seconds: float = 3.0, try_window: bool = False) -> bool:
    r = Report()
    print(f"接続先: {url}")

    try:
        ws = await asyncio.wait_for(connect(url, max_size=16 * 1024 * 1024), TIMEOUT_SEC)
    except (OSError, asyncio.TimeoutError) as e:
        r.bad(f"サーバーにつながりません（{e}）。start.bat でサーバーが動いているか確認してください")
        return False

    async with ws:
        # 1. 接続とトークン
        await ws.send(json.dumps({"t": "hello", "v": protocol.VERSION, "token": token}))
        try:
            msg = await _recv_json(ws, {"welcome", "error"})
        except asyncio.TimeoutError:
            r.bad("welcome が届きません。サーバーの画面に出ているエラーを確認してください")
            return False
        if msg["t"] == "error":
            r.bad(f"サーバーに断られました: {msg.get('message')}")
            return False
        r.good(f"接続できました（映像の大きさ {msg['w']}x{msg['h']}）")

        # 2. 映像
        stats = await _count_frames(ws, seconds)
        fps = stats["frames"] / seconds
        mbps = stats["bytes"] * 8 / seconds / 1_000_000
        if stats["frames"] == 0:
            r.bad("映像が届きません。サーバーの画面に出ている ffmpeg のエラーを確認してください")
        else:
            r.good(f"映像が届きました: {stats['frames']} フレーム（{fps:.0f}fps、{mbps:.1f}Mbps）、キーフレーム {stats['keyframes']}")
            if not stats["first_is_key"]:
                r.bad("最初のフレームがキーフレームではありません（iPhone では、次のキーフレームまで映りません）")
            if stats["keyframes"] and not stats["has_parameter_sets"]:
                r.bad("キーフレームに SPS と PPS が付いていません（iPhone で映りません）")
            if fps < 20:
                r.info("フレームレートが低めです。画面に動きがないと、少なくなることがあります。動画などを流して、もう一度試してください")

        # 3. 往復の時間
        started = time.monotonic()
        await ws.send(json.dumps({"t": "ping", "n": 1}))
        try:
            await _recv_json(ws, {"pong"})
            r.good(f"往復の時間: {(time.monotonic() - started) * 1000:.0f}ms")
        except asyncio.TimeoutError:
            r.bad("ping の返事がありません")

        # 4. ウィンドウの一覧
        await ws.send(json.dumps({"t": "list_windows"}))
        windows: list[dict] = []
        try:
            windows = (await _recv_json(ws, {"windows"}))["list"]
            r.good(f"ウィンドウの一覧: {len(windows)} 個")
            for w in windows[:10]:
                r.info(f"- {w['title']}（{w['app'] or '不明'}）")
        except asyncio.TimeoutError:
            r.bad("ウィンドウの一覧が届きません")

        # 5. ウィンドウ1つだけの表示
        if try_window:
            if not windows:
                r.bad("ウィンドウがないため、ウィンドウの表示は試せません")
            else:
                target = windows[0]
                await ws.send(json.dumps({"t": "mode", "mode": "window", "id": target["id"]}))
                try:
                    msg = await _recv_json(ws, {"mode", "error"}, timeout=10)
                    if msg["t"] == "error":
                        r.bad(f"「{target['title']}」を表示できませんでした: {msg.get('message')}")
                    else:
                        stats = await _count_frames(ws, 2)
                        if stats["frames"] and stats["first_is_key"]:
                            r.good(f"「{target['title']}」だけの表示に切り替えられました（{stats['frames']} フレーム）")
                        else:
                            r.bad(f"「{target['title']}」に切り替えたあと、映像が届きません")
                except asyncio.TimeoutError:
                    r.bad("ウィンドウの表示に切り替えた返事がありません")
                await ws.send(json.dumps({"t": "mode", "mode": "screen"}))
                try:
                    await _recv_json(ws, {"mode"}, timeout=10)
                    r.good("画面全体の表示に戻せました")
                except asyncio.TimeoutError:
                    r.bad("画面全体の表示に戻した返事がありません")

    print()
    print("すべて問題ありません" if r.ok else "[NG] の項目があります。上のメッセージと、サーバーの画面を確認してください")
    return r.ok


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="duo_server.selfcheck", description="動いているサーバーを、このPCから確かめる")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="サーバーの設定ファイル（トークンを読む）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, help="ポート（省略すると設定ファイルの値）")
    p.add_argument("--seconds", type=float, default=3.0, help="映像を受け取る秒数")
    p.add_argument("--window", action="store_true", help="ウィンドウ1つだけの表示も試す（一覧の先頭のウィンドウが前に出る）")
    args = p.parse_args(argv)

    if not args.config.exists():
        print(f"設定ファイルがありません: {args.config}。先に start.bat でサーバーを起動してください")
        sys.exit(1)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    port = args.port or cfg.get("port", 8765)
    ok = asyncio.run(run_check(f"ws://{args.host}:{port}/", cfg.get("token", ""), args.seconds, args.window))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
