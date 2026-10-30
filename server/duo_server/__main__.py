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
from .capture import FfmpegStream, build_ffmpeg_cmd, detect_encoder
from .input_win import create_input
from .server import Settings, make_handler

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
    p.add_argument("--show-token", action="store_true", help="トークンを表示する")
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

    backend = create_input()

    def stream_factory(s: Settings, src: tuple[int, int], region: tuple[int, int, int, int] | None) -> FfmpegStream:
        cmd = build_ffmpeg_cmd(
            ffmpeg=args.ffmpeg, fps=s.fps, bitrate_kbps=s.bitrate_kbps, encoder=encoder,
            max_height=s.max_height, src_height=src[1], region=region, output_idx=args.output_idx,
        )
        return FfmpegStream(cmd)

    handler = make_handler(settings, backend, stream_factory)
    # close_timeout: 応答しない相手を閉じるときに、待つ時間（秒）
    async with serve(handler, args.host, port, max_size=1024 * 1024, close_timeout=2):
        print(f"duo_server {__version__} を起動しました: {args.host}:{port}（エンコーダー: {encoder}）")
        if created or args.show_token:
            print(f"トークン: {settings.token}")
            print(f"（{args.config} に保存してあります。他の人に見せないでください）")
        await asyncio.Future()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("終了します")
    except OSError as e:
        print(f"起動できませんでした: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
