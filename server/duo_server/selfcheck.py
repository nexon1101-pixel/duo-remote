"""サーバーの自己診断。起動: python -m duo_server.selfcheck

動いているサーバーに、このPCからつないで、次を確認する。iPhone は使わない。
- 接続とトークン
- 映像が届くか（フレーム数、キーフレーム、ビットレート）
- 往復の時間（ping）
- ウィンドウの一覧
- --window を付けたとき: ウィンドウ1つだけの表示に切り替えられるか（そのウィンドウが前に出る）
- --audio を付けたとき: PCの音が届くか（PCで音を鳴らしながら実行する）
- --virtual を付けたとき: 仮想ディスプレイを出して映せるか（サーバーを管理者として起動しておく。終わったら、無効に戻る）
- --ime を付けたとき: IMEのオン・オフを送って、PCのIMEの状態が変わるか（--title で選んだウィンドウを前に出して試す）
- --bring を付けたとき: ウィンドウを仮想ディスプレイに持ってきて、映して、元に戻せるか（--title で、ウィンドウを選ぶ）
- --hevc を付けたとき: H.265 で映るか（キーフレームに VPS・SPS・PPS が付いているか）。作れないサーバーなら、h264 になったことを知らせる
- --opus を付けたとき: PCの音が Opus で届くか（PCで音を鳴らしながら実行する）
- --monitors を付けたとき: モニターの一覧を出し、主でないモニターがあれば、そこに切り替えて映せるか
- --quality を付けたとき: 画質を変える（高さ720）と映像の大きさが変わるか。stats が届くか
- --pull を付けたとき: PCのクリップボードの中身（文字、ファイル、画像）を受け取れるか（--save-dir で、ファイルを保存する）
- --put ファイル を付けたとき: ファイルをPCに送って、保存できるか（--put-clipboard で、クリップボードにも入れる）

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
from websockets.exceptions import ConnectionClosed

from . import protocol
from .capture import iter_nals
from .files import sanitize_name, unique_path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"
TIMEOUT_SEC = 5
STATS_WAIT_SEC = 5.0  # --quality で、stats を待つ秒数（stats は約2秒ごと）
PUT_CHUNK_BYTES = 256 * 1024
MAX_PUT_BYTES = 200 * 1024 * 1024
# 映像の形ごとの、キーフレームに付くパラメーターセットの NAL タイプ
PARAMETER_SETS = {"h264": ({7, 8}, "SPS と PPS"), "hevc": ({32, 33, 34}, "VPS・SPS・PPS")}


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


async def _count_frames(ws, seconds: float, keep: list[bytes] | None = None, codec: str = "h264") -> dict:
    """映像を seconds 秒受け取って数える。keep を渡すと、最初のキーフレームからの H.264 を、そこに入れる。"""
    wanted_sets = PARAMETER_SETS.get(codec, PARAMETER_SETS["h264"])[0]
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
        if keep is not None and (key or keep):
            keep.append(payload)
        if key:
            keyframes += 1
            types = {t for _, t in iter_nals(payload, codec)}
            has_parameter_sets = has_parameter_sets or wanted_sets <= types
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


async def _collect_audio_sizes(ws, seconds: float) -> list[int]:
    """音のフレームを seconds 秒受け取る。1つずつの中身の大きさ（バイト）を返す。"""
    sizes: list[int] = []
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        try:
            raw = await asyncio.wait_for(ws.recv(), left)
        except asyncio.TimeoutError:
            break
        if isinstance(raw, bytes) and protocol.is_audio(raw):
            sizes.append(len(raw) - protocol.FRAME_HEADER.size)
    return sizes


def save_last_frame(h264: list[bytes], path: Path, ffmpeg: str = "ffmpeg") -> str | None:
    """受け取った H.264 の、最後のフレームを画像にする。できなければ、その理由を返す。"""
    import subprocess
    import tempfile

    if not h264:
        return "キーフレームが届いていません"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "frames.h264"
        src.write_bytes(b"".join(h264))
        try:
            # -update 1: 同じファイルに上書きし続けるので、最後のフレームが残る
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "h264", "-i", str(src), "-update", "1", str(path)],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return f"ffmpeg を動かせませんでした（{e}）"
    if result.returncode != 0 or not path.exists():
        return (result.stderr.strip().splitlines() or ["ffmpeg が失敗しました"])[-1]
    return None


async def run_check(
    url: str, token: str, seconds: float = 3.0, try_window: bool = False, try_audio: bool = False,
    try_virtual: bool = False, try_bring: bool = False, title: str | None = None, try_ime: bool = False,
    save_frame: Path | None = None, try_hevc: bool = False, try_opus: bool = False, try_monitors: bool = False,
    try_quality: bool = False, try_pull: bool = False, save_dir: Path | None = None, put_file: Path | None = None,
    put_clipboard: bool = False, try_mouse: bool = False,
) -> bool:
    r = Report()
    print(f"接続先: {url}")

    try:
        ws = await asyncio.wait_for(connect(url, max_size=16 * 1024 * 1024), TIMEOUT_SEC)
    except (OSError, asyncio.TimeoutError) as e:
        r.bad(f"サーバーにつながりません（{e}）。start.bat でサーバーが動いているか確認してください")
        return False

    try:
        await _run_checks(
            ws, r, token, seconds, try_window, try_audio, try_virtual, try_bring, title, try_ime, save_frame,
            try_hevc=try_hevc, try_opus=try_opus, try_monitors=try_monitors, try_quality=try_quality,
            try_pull=try_pull, save_dir=save_dir, put_file=put_file, put_clipboard=put_clipboard,
            try_mouse=try_mouse,
        )
    except ConnectionClosed as e:
        reason = e.rcvd.reason if e.rcvd and e.rcvd.reason else str(e)
        r.bad(f"サーバーが接続を閉じました: {reason}。サーバーの画面と、server\\duo_server.log を確認してください")

    print()
    print("すべて問題ありません" if r.ok else "[NG] の項目があります。上のメッセージと、サーバーの画面、server\\duo_server.log を確認してください")
    return r.ok


def _pick_window(windows: list[dict], title: str | None) -> dict | None:
    """試すウィンドウを選ぶ。title があれば、タイトルにその文字を含む最初のもの。なければ、一覧の先頭。"""
    if title:
        return next((w for w in windows if title.lower() in w["title"].lower()), None)
    return windows[0] if windows else None


async def _wait_ime_state(ws, expected: bool, timeout: float = 4) -> bool:
    """IMEの状態が expected になった知らせ（ime_state）が来るまで待つ。"""
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        try:
            msg = await _recv_json(ws, {"ime_state"}, timeout=left)
        except asyncio.TimeoutError:
            return False
        if msg.get("on") is expected:
            return True
    return False


async def _window_place(ws, wid: int) -> str | None:
    """ウィンドウが出ている場所（main、virtual など）。一覧に見つからなければ None。"""
    await ws.send(json.dumps({"t": "list_windows"}))
    listed = (await _recv_json(ws, {"windows"}))["list"]
    return next((w.get("where") for w in listed if w["id"] == wid), None)


async def _run_checks(
    ws, r: Report, token: str, seconds: float, try_window: bool, try_audio: bool, try_virtual: bool,
    try_bring: bool = False, title: str | None = None, try_ime: bool = False, save_frame: Path | None = None,
    *, try_hevc: bool = False, try_opus: bool = False, try_monitors: bool = False, try_quality: bool = False,
    try_pull: bool = False, save_dir: Path | None = None, put_file: Path | None = None, put_clipboard: bool = False,
    try_mouse: bool = False,
) -> None:
    async with ws:
        # 1. 接続とトークン
        hello = {"t": "hello", "v": protocol.VERSION, "token": token}
        if try_hevc:
            hello["codecs"] = ["hevc", "h264"]
        await ws.send(json.dumps(hello))
        try:
            msg = await _recv_json(ws, {"welcome", "error"})
        except asyncio.TimeoutError:
            r.bad("welcome が届きません。サーバーの画面に出ているエラーを確認してください")
            return
        if msg["t"] == "error":
            r.bad(f"サーバーに断られました: {msg.get('message')}")
            return
        r.good(f"接続できました（映像の大きさ {msg['w']}x{msg['h']}）")
        virtual_installed = bool(msg.get("virtual"))
        video_h = int(msg.get("h") or 0)
        codec = msg.get("codec") or "h264"
        if codec not in PARAMETER_SETS:
            r.bad(f"知らない映像の形が返されました: {codec}")
            codec = "h264"
        if try_hevc:
            if codec == "hevc":
                r.good("映像の形は H.265（hevc）です")
            else:
                r.info("このサーバーは H.265 を作れません（NVENC の hevc_nvenc がない）。映像の形は h264 です")

        # 2. 映像
        stats = await _count_frames(ws, seconds, codec=codec)
        fps = stats["frames"] / seconds
        mbps = stats["bytes"] * 8 / seconds / 1_000_000
        if stats["frames"] == 0:
            r.bad("映像が届きません。サーバーの画面に出ている ffmpeg のエラーを確認してください")
        else:
            r.good(f"映像が届きました: {stats['frames']} フレーム（{fps:.0f}fps、{mbps:.1f}Mbps）、キーフレーム {stats['keyframes']}")
            if not stats["first_is_key"]:
                r.bad("最初のフレームがキーフレームではありません（iPhone では、次のキーフレームまで映りません）")
            sets_name = PARAMETER_SETS[codec][1]
            if stats["keyframes"] and not stats["has_parameter_sets"]:
                r.bad(f"キーフレームに {sets_name} が付いていません（iPhone で映りません）")
            elif stats["keyframes"] and try_hevc and codec == "hevc":
                r.good(f"キーフレームに {sets_name} が付いています")
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
            target = _pick_window(windows, title)
            if target is None:
                r.bad(f"試すウィンドウが見つかりません（{'タイトルに「' + title + '」を含むもの' if title else 'ウィンドウがありません'}）")
            else:
                await ws.send(json.dumps({"t": "mode", "mode": "window", "id": target["id"]}))
                try:
                    msg = await _recv_json(ws, {"mode", "error"}, timeout=10)
                    if msg["t"] == "error":
                        r.bad(f"「{target['title']}」を表示できませんでした: {msg.get('message')}")
                    else:
                        kept: list[bytes] = []
                        stats = await _count_frames(ws, 2, keep=kept if save_frame else None)
                        if stats["frames"] and stats["first_is_key"]:
                            r.good(f"「{target['title']}」だけの表示に切り替えられました（{stats['frames']} フレーム）")
                            if save_frame:
                                error = await asyncio.to_thread(save_last_frame, kept, save_frame)
                                if error:
                                    r.bad(f"映像を画像にできませんでした: {error}")
                                else:
                                    r.good(f"ウィンドウの映像を保存しました: {save_frame}")
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

        # 7. ウィンドウを仮想ディスプレイに持ってきて、映して、元に戻す
        if try_bring:
            target = _pick_window(windows, title)
            if not virtual_installed:
                r.bad("仮想ディスプレイのドライバーが見つかりません")
            elif target is None:
                r.bad(f"試すウィンドウが見つかりません（{'タイトルに「' + title + '」を含むもの' if title else 'ウィンドウがありません'}）")
            else:
                name = f"「{target['title']}」"
                before = await _window_place(ws, target["id"])
                await ws.send(json.dumps({"t": "bring", "id": target["id"]}))
                try:
                    reply = await _recv_json(ws, {"brought", "error"}, timeout=40)
                except asyncio.TimeoutError:
                    reply = None
                if reply is None:
                    r.bad(f"{name}を持ってきた返事がありません")
                elif reply["t"] == "error":
                    r.bad(f"{name}を仮想ディスプレイに持ってこられませんでした: {reply.get('message')}")
                else:
                    after = await _window_place(ws, target["id"])
                    if after == "virtual":
                        r.good(f"{name}を、仮想ディスプレイに持ってこられました（{before} → {after}）")
                    else:
                        r.bad(f"{name}を持ってきたはずですが、一覧では、まだ {after} にあります")
                    # 仮想ディスプレイ上のウィンドウだけを映す
                    await ws.send(json.dumps({"t": "mode", "mode": "window", "id": target["id"]}))
                    try:
                        shown = await _recv_json(ws, {"mode", "error"}, timeout=40)
                        if shown["t"] == "error":
                            r.bad(f"仮想ディスプレイ上の{name}を映せませんでした: {shown.get('message')}")
                        else:
                            stats = await _count_frames(ws, seconds)
                            if stats["frames"] and stats["first_is_key"]:
                                r.good(f"仮想ディスプレイ上の{name}だけを映せました（{stats['frames']} フレーム）")
                            else:
                                r.bad(f"仮想ディスプレイ上の{name}に切り替えたあと、映像が届きません")
                    except asyncio.TimeoutError:
                        r.bad("ウィンドウの表示に切り替えた返事がありません")
                    await ws.send(json.dumps({"t": "restore"}))
                    await _recv_json(ws, {"notice"}, timeout=15)
                    restored = await _window_place(ws, target["id"])
                    if restored == before:
                        r.good(f"{name}を、元の場所（{restored}）に戻せました")
                    else:
                        r.bad(f"{name}を戻したはずですが、一覧では {restored} にあります（元は {before}）")
                await ws.send(json.dumps({"t": "mode", "mode": "screen"}))
                try:
                    await _recv_json(ws, {"mode"}, timeout=10)
                    r.good("画面全体の表示に戻せました")
                except asyncio.TimeoutError:
                    r.bad("画面全体の表示に戻した返事がありません")

        # 8. IME（日本語入力のオン・オフ。iPhone の半角/全角キーと、IMEの切り替えボタンが、これを使う）
        if try_ime:
            target = _pick_window(windows, title)
            if target is None:
                r.bad(f"試すウィンドウが見つかりません（{'タイトルに「' + title + '」を含むもの' if title else 'ウィンドウがありません'}）")
            else:
                # IMEの状態は、前に出ているウィンドウのもの。試すウィンドウを、前に出す
                await ws.send(json.dumps({"t": "mode", "mode": "window", "id": target["id"]}))
                try:
                    await _recv_json(ws, {"mode", "error"}, timeout=10)
                except asyncio.TimeoutError:
                    pass
                await ws.send(json.dumps({"t": "ime", "on": False}))
                await asyncio.sleep(1.2)  # 状態が、すでにオフでも、変わったことが分かるよう、一度オフにして、待つ
                await ws.send(json.dumps({"t": "ime", "on": True}))
                if await _wait_ime_state(ws, True):
                    r.good("IMEをオンにできました（PCのIMEの状態が、オンになった）")
                else:
                    r.bad("IMEをオンにしても、PCのIMEの状態が、オンになりませんでした（IMEが日本語になっているか、確認してください）")
                await ws.send(json.dumps({"t": "ime", "on": False}))
                if await _wait_ime_state(ws, False):
                    r.good("IMEをオフにできました（PCのIMEの状態が、オフになった）")
                else:
                    r.bad("IMEをオフにしても、PCのIMEの状態が、オフになりませんでした")
                await ws.send(json.dumps({"t": "mode", "mode": "screen"}))
                try:
                    await _recv_json(ws, {"mode"}, timeout=10)
                except asyncio.TimeoutError:
                    pass

        # 9. 音
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

        # 10. 音（Opus）
        if try_opus:
            await ws.send(json.dumps({"t": "audio", "on": True, "codec": "opus", "rate": 48000, "channels": 2}))
            try:
                state = await _recv_json(ws, {"audio_state"}, timeout=10)
            except asyncio.TimeoutError:
                state = None
            if state is None:
                r.bad("Opus の音を始めた返事がありません")
            elif not state.get("on"):
                r.bad(f"Opus の音を始められませんでした: {state.get('reason')}")
            elif state.get("codec") != "opus":
                r.bad(f"Opus を頼みましたが、audio_state の codec が {state.get('codec')!r} です（サーバーが古いかもしれません）")
                await ws.send(json.dumps({"t": "audio", "on": False}))
            else:
                sizes = await _collect_audio_sizes(ws, seconds)
                if not sizes:
                    r.bad("Opus の音が届きません。PCで音を鳴らしながら、もう一度実行してください（無音のときは、何も届きません）")
                else:
                    r.good(
                        f"Opus の音が届きました: {len(sizes)} 個（約{len(sizes) * 20 / 1000:.1f}秒ぶん）、"
                        f"1つの大きさ 平均 {sum(sizes) / len(sizes):.0f}バイト（{min(sizes)}〜{max(sizes)}）"
                    )
                    if max(sizes) > 1500:
                        r.bad(f"1つの大きさが大きすぎます（{max(sizes)}バイト）。Opus ではなく PCM が届いているかもしれません")
                await ws.send(json.dumps({"t": "audio", "on": False}))

        # 11. モニター
        if try_monitors:
            await _check_monitors(ws, r, seconds, codec)

        # 12. 画質の変更と stats
        if try_quality:
            await _check_quality(ws, r, seconds, codec, video_h)

        # 13. PCのクリップボードの中身を受け取る
        if try_pull:
            await _check_pull(ws, r, save_dir)

        # 14. ファイルをPCに送る
        if put_file is not None:
            await _check_put(ws, r, put_file, put_clipboard)

        # 15. マウスの入力が、PCのカーソルを動かすか
        if try_mouse:
            await _check_mouse(ws, r)


def _cursor_position() -> tuple[int, int] | None:
    """PCのカーソルの位置（画素）。Windows 以外では None。このプログラムは、サーバーと同じPCで動かす。"""
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as wt

    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # サーバーと同じ、実際の画素で読む
    except (OSError, AttributeError):
        pass
    pos = wt.POINT()
    return (pos.x, pos.y) if ctypes.WinDLL("user32").GetCursorPos(ctypes.byref(pos)) else None


def _screen_pixels() -> tuple[int, int]:
    import ctypes

    user32 = ctypes.WinDLL("user32")
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


async def _check_mouse(ws, r: Report) -> None:
    """アプリから届く操作（move）が、PCのカーソルを動かすかを試す。iPhone がなくても、サーバーと Windows の間を確認できる。
    動かなければ、サーバーの画面（server\\duo_server.log）の WARNING を見る。"""
    if _cursor_position() is None:
        r.info("マウスの確認は、サーバーと同じ Windows のPCでだけできます")
        return
    width, height = _screen_pixels()
    ok = True
    for x, y in ((0.25, 0.25), (0.75, 0.6), (0.5, 0.5)):
        await ws.send(json.dumps({"t": "move", "x": x, "y": y}))
        await asyncio.sleep(0.4)
        got = _cursor_position()
        want = (round(x * (width - 1)), round(y * (height - 1)))
        if got is None or abs(got[0] - want[0]) > 4 or abs(got[1] - want[1]) > 4:
            ok = False
            r.bad(f"カーソルが動きませんでした（送った先 {want[0]},{want[1]}、今の位置 {got[0] if got else '?'},{got[1] if got else '?'}）")
        else:
            r.good(f"カーソルが動きました（{want[0]},{want[1]}）")
    if not ok:
        r.info("サーバーが操作を受け取っているかは、server\\duo_server.log の「最初の操作を受け取りました」で分かる。"
               "受け取っているのに動かないときは、Windows が入力を断っている（サーバーを管理者として動かす）")


async def _back_to_screen(ws, r: Report) -> None:
    await ws.send(json.dumps({"t": "mode", "mode": "screen"}))
    try:
        await _recv_json(ws, {"mode"}, timeout=10)
        r.good("画面全体の表示に戻せました")
    except asyncio.TimeoutError:
        r.bad("画面全体の表示に戻した返事がありません")


async def _check_monitors(ws, r: Report, seconds: float, codec: str) -> None:
    await ws.send(json.dumps({"t": "list_monitors"}))
    try:
        monitors = (await _recv_json(ws, {"monitors"}))["list"]
    except asyncio.TimeoutError:
        r.bad("モニターの一覧が届きません")
        return
    r.good(f"モニターの一覧: {len(monitors)} 個")
    for m in monitors:
        kind = ("主" if m.get("primary") else "主ではない") + ("、仮想" if m.get("virtual") else "")
        r.info(f"- {m.get('name')}（id {m.get('id')}）{m.get('w')}x{m.get('h')}、{kind}")
    target = next((m for m in monitors if not m.get("primary")), None)
    if target is None:
        r.info("主でないモニターがないので、モニターの切り替えは試しません")
        return
    name = f"モニター {target['name']}"
    await ws.send(json.dumps({"t": "mode", "mode": "monitor", "id": target["id"]}))
    try:
        reply = await _recv_json(ws, {"mode", "error"}, timeout=40)
    except asyncio.TimeoutError:
        reply = None
    if reply is None:
        r.bad(f"{name}に切り替えた返事がありません")
    elif reply["t"] == "error":
        r.bad(f"{name}に切り替えられませんでした: {reply.get('message')}")
    elif reply.get("mode") != "monitor" or reply.get("id") != target["id"]:
        r.bad(f"{name}に切り替えましたが、返事が違います: {reply}")
    else:
        stats = await _count_frames(ws, seconds, codec=codec)
        if stats["frames"] and stats["first_is_key"]:
            r.good(f"{name}を映せました（{stats['frames']} フレーム）")
        else:
            r.bad(f"{name}に切り替えたあと、映像が届かないか、キーフレームから始まっていません")
    await _back_to_screen(ws, r)


async def _check_quality(ws, r: Report, seconds: float, codec: str, video_h: int) -> None:
    await ws.send(json.dumps({"t": "quality", "max_height": 720}))
    if 0 < video_h <= 720:
        r.info(f"映像の高さが {video_h} なので、高さ720への変更は確認しません")
    else:
        try:
            size = await _recv_json(ws, {"resize"}, timeout=10)
        except asyncio.TimeoutError:
            size = None
        if size is None:
            r.bad("max_height を 720 にしても、resize が届きません")
        elif size.get("h") != 720:
            r.bad(f"max_height を 720 にしましたが、映像の高さが {size.get('h')} です")
        else:
            stats = await _count_frames(ws, seconds, codec=codec)
            if stats["frames"] and stats["first_is_key"]:
                r.good(f"画質を変えられました: 映像が {size['w']}x{size['h']} になり、{stats['frames']} フレーム届きました")
            else:
                r.bad("画質を変えたあと、映像が届かないか、キーフレームから始まっていません")
    await ws.send(json.dumps({"t": "quality", "max_height": 0}))
    r.info("max_height を 0（元の大きさ）に戻しました")
    got: list[dict] = []
    deadline = time.monotonic() + STATS_WAIT_SEC
    while (left := deadline - time.monotonic()) > 0:
        try:
            got.append(await _recv_json(ws, {"stats"}, timeout=left))
        except asyncio.TimeoutError:
            break
    if len(got) >= 2:
        r.good(f"{STATS_WAIT_SEC:g}秒のあいだに stats が {len(got)} 回届きました")
    else:
        r.bad(f"{STATS_WAIT_SEC:g}秒のあいだに stats が {len(got)} 回しか届きません（2回以上のはずです）")
    for s in got:
        r.info(f"- sent {s.get('sent')}、dropped {s.get('dropped')}、{s.get('kbps')} kbps")


async def _check_pull(ws, r: Report, save_dir: Path | None) -> None:
    await ws.send(json.dumps({"t": "clip_pull"}))
    received = 0
    wait = 10.0
    while True:
        try:
            msg = await _recv_json(ws, {"clip", "file_begin", "error"}, timeout=wait)
        except asyncio.TimeoutError:
            if received == 0:
                r.bad("clip_pull の返事がありません")
            return
        wait = 1.0  # 2つ目のファイルが続くかを待つ
        if msg["t"] == "error":
            r.bad(f"PCのクリップボードを受け取れませんでした: {msg.get('message')}")
            return
        if msg["t"] == "clip":
            text = msg.get("s", "")
            shown = text[:50] + ("…" if len(text) > 50 else "")
            r.good(f"クリップボードの文字を受け取りました（{len(text)}文字）: {shown!r}" if text else "クリップボードの文字を受け取りました（空です）")
            return
        received += 1
        await _receive_file(ws, r, msg, save_dir)


async def _receive_file(ws, r: Report, begin: dict, save_dir: Path | None) -> None:
    fid, name, size = begin.get("id"), str(begin.get("name", "")), begin.get("size")
    r.info(f"ファイル: 名前 {name}、大きさ {size}バイト、mime {begin.get('mime')}")
    chunks: list[bytes] = []
    deadline = time.monotonic() + 60
    while True:
        left = deadline - time.monotonic()
        try:
            if left <= 0:
                raise asyncio.TimeoutError
            raw = await asyncio.wait_for(ws.recv(), left)
        except asyncio.TimeoutError:
            r.bad(f"「{name}」の file_end が届きません（{sum(map(len, chunks))}バイトまで受け取りました）")
            return
        if isinstance(raw, bytes):
            parsed = protocol.unpack_file(raw)
            if parsed is not None and parsed[0] == fid:
                chunks.append(parsed[1])
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if msg.get("t") == "error":
            r.bad(f"「{name}」を受け取る途中でエラー: {msg.get('message')}")
            return
        if msg.get("t") == "file_end" and msg.get("id") == fid:
            break
    data = b"".join(chunks)
    if len(data) != size:
        r.bad(f"「{name}」の受け取った大きさ（{len(data)}バイト）が、知らされた大きさ（{size}バイト）と合いません")
        return
    r.good(f"「{name}」を受け取りました（{len(data)}バイト、{len(chunks)}フレーム）")
    if save_dir is not None:
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            path = unique_path(save_dir, sanitize_name(name))
            path.write_bytes(data)
            r.good(f"保存しました: {path}")
        except OSError as e:
            r.bad(f"保存できませんでした（{e}）")


async def _check_put(ws, r: Report, path: Path, to_clipboard: bool) -> None:
    try:
        size = path.stat().st_size
        if size > MAX_PUT_BYTES:
            r.bad(f"ファイルが大きすぎます（200MBまで）: {path}")
            return
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as e:
        r.bad(f"ファイルを読めませんでした（{e}）: {path}")
        return
    fid = 1
    begin = {"t": "file_put", "id": fid, "name": path.name, "size": len(data)}
    if to_clipboard:
        begin["to_clipboard"] = True
    await ws.send(json.dumps(begin))
    for i in range(0, len(data), PUT_CHUNK_BYTES):
        await ws.send(protocol.pack_file(fid, data[i:i + PUT_CHUNK_BYTES]))
    await ws.send(json.dumps({"t": "file_end", "id": fid}))
    try:
        reply = await _recv_json(ws, {"file_saved", "error"}, timeout=30)
    except asyncio.TimeoutError:
        r.bad("file_saved の返事がありません")
        return
    if reply["t"] == "error":
        r.bad(f"ファイルを保存できませんでした: {reply.get('message')}")
    else:
        r.good(f"ファイルをPCに送れました（{len(data)}バイト）。保存先: {reply.get('path')}" + ("。クリップボードにも入れるよう頼みました" if to_clipboard else ""))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="duo_server.selfcheck", description="動いているサーバーを、このPCから確認する")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="サーバーの設定ファイル（トークンを読む）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, help="ポート（省略すると設定ファイルの値）")
    p.add_argument("--seconds", type=float, default=3.0, help="映像を受け取る秒数")
    p.add_argument("--window", action="store_true", help="ウィンドウ1つだけの表示も試す（一覧の先頭のウィンドウが前に出る）")
    p.add_argument("--audio", action="store_true", help="PCの音が届くかも試す（PCで音を鳴らしながら実行する）")
    p.add_argument("--virtual", action="store_true", help="仮想ディスプレイを出して映せるかも試す（サーバーを管理者として起動しておく）")
    p.add_argument("--bring", action="store_true", help="ウィンドウを仮想ディスプレイに持ってきて、映して、元に戻せるかも試す（--title で選ぶ）")
    p.add_argument("--ime", action="store_true", help="IMEのオン・オフを送って、PCのIMEの状態が変わるかも試す（--title で、試すウィンドウを選ぶ）")
    p.add_argument("--hevc", action="store_true", help="H.265 で映るかも試す（作れないサーバーなら、h264 になったことを知らせる）")
    p.add_argument("--opus", action="store_true", help="PCの音が Opus で届くかも試す（PCで音を鳴らしながら実行する）")
    p.add_argument("--monitors", action="store_true", help="モニターの一覧を出し、主でないモニターがあれば、そこに切り替えて映せるかも試す")
    p.add_argument("--quality", action="store_true", help="画質を変える（高さ720）と映像の大きさが変わるか、stats が届くかも試す（約5秒かかる）")
    p.add_argument("--pull", action="store_true", help="PCのクリップボードの中身（文字、ファイル、画像）を受け取れるかも試す")
    p.add_argument("--save-dir", type=Path, help="--pull で受け取ったファイルを、このフォルダに保存する")
    p.add_argument("--put", type=Path, metavar="ファイル", help="このファイルをPCに送って、保存できるかも試す")
    p.add_argument("--put-clipboard", action="store_true", help="--put のファイルを、PCのクリップボードにも入れる")
    p.add_argument("--mouse", action="store_true", help="マウスの操作（move）が、PCのカーソルを動かすかも試す（このPCで動かす。カーソルが動く）")
    p.add_argument(
        "--save-frame", type=Path,
        help="--window で受け取った映像の最後の1枚を、この画像（.png）に保存する（ほかのウィンドウが映っていないかを見るため）",
    )
    p.add_argument("--title", help="--window と --bring で試すウィンドウ。タイトルに、この文字を含む最初のもの（省略すると、一覧の先頭）")
    args = p.parse_args(argv)

    if not args.config.exists():
        print(f"設定ファイルがありません: {args.config}。先に start.bat でサーバーを起動してください")
        sys.exit(1)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    port = args.port or cfg.get("port", 8765)
    ok = asyncio.run(run_check(
        f"ws://{args.host}:{port}/", cfg.get("token", ""), args.seconds, args.window, args.audio, args.virtual, args.bring, args.title, args.ime, args.save_frame,
        args.hevc, args.opus, args.monitors, args.quality, args.pull, args.save_dir, args.put, args.put_clipboard,
        args.mouse,
    ))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
