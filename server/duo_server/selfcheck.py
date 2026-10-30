"""サーバーの自己診断。起動: python -m duo_server.selfcheck

動いているサーバーに、このPCからつないで、次を確かめる。iPhone は使わない。
- 接続とトークン
- 映像が届くか（フレーム数、キーフレーム、ビットレート）
- 往復の時間（ping）
- ウィンドウの一覧
- --window を付けたとき: ウィンドウ1つだけの表示に切り替えられるか（そのウィンドウが前に出る）
- --audio を付けたとき: PCの音が届くか（PCで音を鳴らしながら実行する）
- --virtual を付けたとき: 仮想ディスプレイを出して映せるか（サーバーを管理者として起動しておく。終わったら、無効に戻る）

マウスとキーボードは動かさない。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import struct
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
        if not isinstance(raw, bytes) or protocol.is_audio(raw):
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


async def _collect_audio(ws, seconds: float) -> tuple[int, int]:
    """音のフレームを seconds 秒受け取る。(フレーム数, いちばん大きい音の値 0〜32767) を返す。"""
    count = peak = 0
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        try:
            raw = await asyncio.wait_for(ws.recv(), left)
        except asyncio.TimeoutError:
            break
        if isinstance(raw, bytes) and protocol.is_audio(raw):
            _, _, payload = protocol.unpack_frame(raw)
            samples = struct.unpack(f"<{len(payload) // 2}h", payload[: len(payload) // 2 * 2])
            peak = max(peak, max((abs(v) for v in samples), default=0))
            count += 1
    return count, peak


async def run_check(
    url: str, token: str, seconds: float = 3.0, try_window: bool = False, try_audio: bool = False,
    try_virtual: bool = False,
) -> bool:
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
        virtual_installed = bool(msg.get("virtual"))

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

        # 6. 仮想ディスプレイ
        if try_virtual:
            if not virtual_installed:
                r.bad("仮想ディスプレイのドライバーが見つかりません（docs/virtual-display.md の手順で入れてください）")
            else:
                started = time.monotonic()
                await ws.send(json.dumps({"t": "mode", "mode": "desktop", "fit_w": 951, "fit_h": 669}))
                try:
                    reply = await _recv_json(ws, {"mode", "error"}, timeout=40)
                except asyncio.TimeoutError:
                    reply = None
                if reply is None:
                    r.bad("仮想ディスプレイを出した返事がありません")
                elif reply["t"] == "error":
                    r.bad(f"仮想ディスプレイを出せませんでした: {reply.get('message')}")
                else:
                    elapsed = time.monotonic() - started
                    try:
                        size = await _recv_json(ws, {"resize"}, timeout=5)
                        shown = f"{size['w']}x{size['h']}"
                    except asyncio.TimeoutError:
                        shown = "大きさ不明"
                    stats = await _count_frames(ws, seconds)
                    if stats["frames"] and stats["first_is_key"]:
                        r.good(
                            f"仮想ディスプレイ（デスクトップ）を映せました: {shown}、"
                            f"出すまで {elapsed:.1f}秒、{stats['frames']} フレーム"
                        )
                    else:
                        r.bad(f"仮想ディスプレイに切り替えたあと、映像が届きません（{shown}）。サーバーの画面の ffmpeg のエラーを見てください")
                await ws.send(json.dumps({"t": "mode", "mode": "screen"}))
                try:
                    await _recv_json(ws, {"mode"}, timeout=10)
                    r.good("画面全体の表示に戻せました")
                except asyncio.TimeoutError:
                    r.bad("画面全体の表示に戻した返事がありません")

        # 7. 音
        if try_audio:
            await ws.send(json.dumps({"t": "audio", "on": True, "rate": 48000, "channels": 2}))
            try:
                state = await _recv_json(ws, {"audio_state"}, timeout=10)
            except asyncio.TimeoutError:
                state = None
            if state is None:
                r.bad("音を始めた返事がありません")
            elif not state.get("on"):
                r.bad(f"音を始められませんでした: {state.get('reason')}")
            else:
                count, peak = await _collect_audio(ws, seconds)
                if count == 0:
                    r.bad("音が届きません。PCで音を鳴らしながら、もう一度実行してください（無音のときは、何も届きません）")
                elif peak < 100:
                    r.bad(f"音は届きましたが、ほぼ無音です（{count} 個、最大 {peak}）。PCで音を鳴らしながら、もう一度実行してください")
                else:
                    r.good(f"音が届きました: {count} 個（約{count * 20 / 1000:.1f}秒ぶん）、最大の音の大きさ {peak}/32767")
                await ws.send(json.dumps({"t": "audio", "on": False}))

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
    p.add_argument("--audio", action="store_true", help="PCの音が届くかも試す（PCで音を鳴らしながら実行する）")
    p.add_argument("--virtual", action="store_true", help="仮想ディスプレイを出して映せるかも試す（サーバーを管理者として起動しておく）")
    args = p.parse_args(argv)

    if not args.config.exists():
        print(f"設定ファイルがありません: {args.config}。先に start.bat でサーバーを起動してください")
        sys.exit(1)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    port = args.port or cfg.get("port", 8765)
    ok = asyncio.run(run_check(f"ws://{args.host}:{port}/", cfg.get("token", ""), args.seconds, args.window, args.audio, args.virtual))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
