"""起動: python -m duo_server"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import sys
from pathlib import Path

from websockets.asyncio.server import serve

from . import __version__
from .audio import AudioStream
from .capture import (
    FfmpegStream, build_ffmpeg_cmd, capture_target, detect_encoder, detect_hevc, detect_window_capture,
)
from .input_win import create_input
from .server import Settings, make_handler

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def load_config(path: Path) -> tuple[dict, bool]:
    """設定を読む。なければ作る（トークンもこのとき作る）。
    トークンを作ったとき（ファイルがなかった、またはトークンが消されていた）はTrueを返す。"""
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
        token = cfg.get("token")
        if isinstance(token, str) and token:
            return cfg, False
        print("設定ファイルにトークンがないため、作り直しました")
    else:
        cfg = {"port": 8765, "fps": 60, "bitrate_kbps": 8000, "max_height": 0}
    cfg["token"] = secrets.token_urlsafe(16)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return cfg, True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="duo_server", description="iPhoneから操作するための画面配信サーバー")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="設定ファイル（なければ作る）")
    p.add_argument("--host", default="0.0.0.0", help="待ち受けるアドレス")
    p.add_argument("--port", type=int, help="ポート（設定ファイルの値を上書き）")
    p.add_argument("--fps", type=int, help="フレームレート")
    p.add_argument("--bitrate", type=int, help="ビットレート（kbps）")
    p.add_argument("--max-height", type=int, help="映像の高さの上限（0は元の大きさ）")
    p.add_argument("--encoder", choices=["auto", "h264_nvenc", "libx264"], default="auto")
    p.add_argument("--ffmpeg", default="ffmpeg", help="ffmpegのパス")
    p.add_argument(
        "--output-idx", type=int, default=0,
        help="撮るモニターの番号（ddagrab の output_idx）。モニターが2枚以上で、映像と操作がずれるときに変える",
    )
    p.add_argument(
        "--window-capture", choices=["auto", "on", "off"], default="auto",
        help="ウィンドウを表示するとき、ウィンドウだけを撮るか（auto: ffmpeg に gfxcapture があれば撮る。off: 画面を切り抜く）",
    )
    p.add_argument("--show-token", action="store_true", help="トークンを表示する")
    p.add_argument(
        "--control-stdin", action="store_true",
        help="標準入力に stop の行が来るか、標準入力が閉じたら、後片付けをして止まる（管理画面から動かすとき）",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    cfg, created = load_config(args.config)
    settings = Settings(
        token=cfg["token"],
        fps=args.fps or cfg.get("fps", 60),
        bitrate_kbps=args.bitrate or cfg.get("bitrate_kbps", 8000),
        max_height=args.max_height if args.max_height is not None else cfg.get("max_height", 0),
    )
    port = args.port or cfg.get("port", 8765)
    encoder = detect_encoder(args.ffmpeg) if args.encoder == "auto" else args.encoder
    settings.hevc_available = encoder == "h264_nvenc" and detect_hevc(args.ffmpeg)
    if args.window_capture == "auto":
        settings.window_capture = sys.platform == "win32" and detect_window_capture(args.ffmpeg)
    else:
        settings.window_capture = args.window_capture == "on"

    backend = create_input()
    install_console_handler(backend)

    def stream_factory(
        s: Settings, src: tuple[int, int], region: tuple[int, int, int, int] | None, monitor: dict | None,
        window: int | None = None,
    ) -> FfmpegStream:
        if window is not None:
            cmd = build_ffmpeg_cmd(
                ffmpeg=args.ffmpeg, fps=s.fps, bitrate_kbps=s.bitrate_kbps, encoder=encoder,
                max_height=s.max_height, src_height=src[1], window=window, codec=s.codec,
            )
            return FfmpegStream(cmd, s.codec)
        # 主モニターは --output-idx、そのほか（仮想ディスプレイなど）は、調べた DXGI の番号
        if monitor is not None and monitor.get("adapter") not in (None, 0):
            log.warning("このモニターは、別のグラフィックアダプター（%s）にあります", monitor["adapter"])
        output_idx, relative = capture_target(monitor, region, args.output_idx)
        cmd = build_ffmpeg_cmd(
            ffmpeg=args.ffmpeg, fps=s.fps, bitrate_kbps=s.bitrate_kbps, encoder=encoder,
            max_height=s.max_height, src_height=src[1], region=relative, output_idx=output_idx, codec=s.codec,
        )
        return FfmpegStream(cmd, s.codec)

    def audio_factory(rate: int, channels: int, codec: str = "pcm") -> AudioStream:
        return AudioStream(args.ffmpeg, rate, channels, codec=codec)

    handler = make_handler(settings, backend, stream_factory, audio_factory)
    # 仮想ディスプレイのドライバーが入っているかを、先に調べておく（調べるのに1秒ほどかかる）
    virtual = await asyncio.to_thread(backend.virtual_available)
    if virtual and await asyncio.to_thread(backend.virtual_cleanup_leftover):
        # 前回、サーバーが後片付けをせずに止められると、有効のまま残る。使っていないときは無効、が決まり
        print("前回から有効のまま残っていた仮想ディスプレイを、無効に戻しました")
    try:
        # close_timeout: 応答しない相手を閉じるときに、待つ時間（秒）
        async with serve(handler, args.host, port, max_size=1024 * 1024, close_timeout=2):
            print(f"duo_server {__version__} を起動しました: {args.host}:{port}（エンコーダー: {encoder}{'、H.265 も使えます' if settings.hevc_available else ''}）")
            print(f"仮想ディスプレイ: {'使えます' if virtual else 'ドライバーが入っていません'}")
            print(
                "ウィンドウの表示: "
                + ("ウィンドウだけを撮ります（Windows Graphics Capture）" if settings.window_capture
                   else "画面を切り抜きます（ほかのウィンドウが重なると映ります。ffmpeg 8.1 以降で、ウィンドウだけを撮れます）")
            )
            if created or args.show_token:
                print(f"トークン: {settings.token}")
                print(f"（{args.config} に保存してあります。他の人に見せないでください）")
            await wait_for_stop(args.control_stdin)
    finally:
        # 持ってきたウィンドウを元に戻し、仮想ディスプレイを無効に戻す
        backend.shutdown()


async def wait_for_stop(control_stdin: bool) -> None:
    """止める合図まで待つ。control_stdin のときは、標準入力の stop の行か、標準入力が閉じたのを合図にする。
    そうでなければ、Ctrl+C まで待つ。"""
    if not control_stdin:
        await asyncio.Future()
        return
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line or line.strip() == "stop":
            print("止める合図を受け取りました")
            return


LOG_PATH = Path(__file__).resolve().parent.parent / "duo_server.log"
_console_handler = None  # コンソールの閉じる合図を受ける関数。ガベージコレクションされないよう、覚えておく


def install_console_handler(backend) -> None:
    """コンソールの画面を閉じたとき、ログオフ、シャットダウンのときも、後片付け（仮想ディスプレイを無効に戻す、
    ウィンドウを元に戻す）をする。Ctrl+C は、いつもの KeyboardInput の流れで済む（ここでは扱わない）。"""
    global _console_handler
    if sys.platform != "win32":
        return
    import ctypes

    handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

    def on_event(event: int) -> int:
        if event in (2, 5, 6):  # CTRL_CLOSE_EVENT、CTRL_LOGOFF_EVENT、CTRL_SHUTDOWN_EVENT
            try:
                backend.shutdown()
            except Exception:
                log.exception("終了の後片付けでエラーが出ました")
            return 1
        return 0

    _console_handler = handler_type(on_event)
    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, 1):
        log.warning("コンソールを閉じたときの後片付けを、設定できませんでした")


def setup_logging(verbose: bool) -> None:
    """画面と、ファイル（server/duo_server.log。1MBで切り替え、3つまで）に、ログを出す。"""
    from logging.handlers import RotatingFileHandler

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass  # 書き込めない場所でも、サーバーは動かす
    for h in handlers:
        h.setFormatter(formatter)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("終了します")
    except OSError as e:
        print(f"起動できませんでした: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
