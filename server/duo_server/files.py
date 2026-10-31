"""ファイルの受け渡し（iPhone と PC の間）と、クリップボードの画像・ファイル。

- iPhone→PC: `file_put` → 中身のバイナリフレーム → `file_end` で、ダウンロードフォルダの DuoRemote に保存する
- PC→iPhone: `clip_pull` で、クリップボードのファイルか画像を、`file_begin` → 中身 → `file_end` で送る
- 取り決めは docs/protocol.md の「ファイルの受け渡し」
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
import struct
import sys
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from websockets.exceptions import ConnectionClosed

from . import protocol
from .input_win import ClipboardError, from_windows_newlines

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 200 * 1024 * 1024  # 1回に送れる大きさ（クリップボードのファイルは、合計）
CHUNK_BYTES = 256 * 1024  # 中身のフレーム1つの大きさ
MAX_INCOMING = 8  # 同時に受け取れるファイルの数
MAX_NAME_LENGTH = 150
MESSAGES = frozenset({"file_put", "file_end", "clip_pull"})
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")  # to_clipboard で、画像として入れるもの（heic は除く）

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
}


def default_download_dir() -> Path:
    """保存先。Windows では `%USERPROFILE%\\Downloads\\DuoRemote`。"""
    home = os.environ.get("USERPROFILE") if sys.platform == "win32" else None
    return Path(home or Path.home()) / "Downloads" / "DuoRemote"


def sanitize_name(name: str) -> str:
    """送られてきた名前から、フォルダの部分と、Windows で使えない文字を取り除く。"""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _INVALID_CHARS.sub("", name).strip().rstrip(". ")
    if not name.strip(". "):
        return "file"
    stem, ext = os.path.splitext(name)
    if len(name) > MAX_NAME_LENGTH:
        ext = ext[:20]
        name = stem[: MAX_NAME_LENGTH - len(ext)] + ext
        stem = os.path.splitext(name)[0]
    if stem.upper() in _RESERVED:
        name = "_" + name
    return name


def unique_path(directory: Path, name: str) -> Path:
    """同じ名前があれば、「名前 (2).拡張子」のようにする。"""
    path = directory / name
    if not path.exists():
        return path
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        path = directory / f"{stem} ({n}){ext}"
        if not path.exists():
            return path
        n += 1


# --- クリップボードの形式（Windows の部分は input_win.py。ここは、バイト列を作る・読むだけ）

def parse_hdrop(raw: bytes) -> list[str]:
    """CF_HDROP（DROPFILES の後ろに、ファイル名が並ぶ）から、パスの並びを取り出す。"""
    if len(raw) < 20:
        return []
    offset, _x, _y, _nc, wide = struct.unpack_from("<IiiII", raw, 0)
    if offset < 20 or offset > len(raw):
        return []
    body = raw[offset:]
    if wide:
        text = body.decode("utf-16-le", errors="replace")
    else:
        text = body.decode("mbcs" if sys.platform == "win32" else "utf-8", errors="replace")
    paths = []
    for item in text.split("\0"):
        if not item:
            break
        paths.append(item)
    return paths


def build_hdrop(paths: list[str]) -> bytes:
    """パスの並びを、CF_HDROP のデータにする（ワイド文字）。"""
    head = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    body = "".join(p + "\0" for p in paths) + "\0"
    return head + body.encode("utf-16-le")


def _dib_layout(dib: bytes) -> tuple[int, int, int, int, int] | None:
    """DIB の (幅, 高さ（上から下なら負）, ビット数, 圧縮, 画素の始まり) を返す。読めなければ None。"""
    if len(dib) < 40:
        return None
    size, width, height, _planes, bits, comp, _img, _xp, _yp, used, _imp = struct.unpack_from("<IiiHHIIiiII", dib, 0)
    if size < 40 or size > len(dib) or width <= 0 or height == 0:
        return None
    colors = used if used else ((1 << bits) if bits <= 8 else 0)
    offset = size + colors * 4 + (12 if comp == 3 and size == 40 else 0)
    return width, height, bits, comp, offset


def dib_to_bmp(dib: bytes) -> bytes | None:
    """DIB に BMP のファイルヘッダー（14バイト）を付ける。"""
    layout = _dib_layout(dib)
    if layout is None:
        return None
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, 14 + layout[4]) + dib


# クリップボードの画像を JPEG にしてみる大きさ（これより小さい PNG は、そのまま送る）
JPEG_TRY_BYTES = 1024 * 1024
# JPEG に使う ffmpeg（__main__ が --ffmpeg の値に置き換える）
FFMPEG = "ffmpeg"


def to_jpeg_if_smaller(image: bytes, ffmpeg: str | None = None) -> bytes | None:
    """大きな画像（写真など）を JPEG にする。元の半分以下になったときだけ返す（そうでなければ None）。
    PNG のままで小さいスクリーンショットなどは、文字がにじまないよう、JPEG にしない。"""
    import subprocess

    if len(image) < JPEG_TRY_BYTES:
        return None
    try:
        result = subprocess.run(
            [ffmpeg or FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-i", "pipe:0",
             "-frames:v", "1", "-pix_fmt", "yuvj420p", "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
            input=image, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("画像を JPEG にできませんでした: %s", e)
        return None
    jpeg = result.stdout
    if result.returncode != 0 or not jpeg.startswith(b"\xff\xd8"):
        return None
    return jpeg if len(jpeg) * 2 <= len(image) else None


def bgra_to_jpeg(width: int, height: int, data: bytes, ffmpeg: str | None = None) -> bytes | None:
    """上から順の BGRA（幅 x 高さ）を JPEG にする。ウィンドウの小さい絵に使う。できなければ None。"""
    import subprocess

    if width <= 0 or height <= 0 or len(data) != width * height * 4:
        return None
    try:
        result = subprocess.run(
            [ffmpeg or FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra",
             "-s", f"{width}x{height}", "-i", "pipe:0", "-frames:v", "1", "-pix_fmt", "yuvj420p",
             "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
            input=data, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("ウィンドウの絵を JPEG にできませんでした: %s", e)
        return None
    jpeg = result.stdout
    if result.returncode != 0 or not jpeg.startswith(b"\xff\xd8"):
        return None
    return jpeg


def dib_to_png(dib: bytes) -> bytes | None:
    """DIB（24ビットか32ビット）を PNG にする。透明度は使わない（クリップボードの32ビットは、当てにならない）。
    ほかの形のときは None。"""
    layout = _dib_layout(dib)
    if layout is None:
        return None
    width, height, bits, comp, offset = layout
    if bits not in (24, 32) or comp not in (0, 3):
        return None
    if comp == 3 and (
        bits != 32 or len(dib) < 52 or struct.unpack_from("<III", dib, 40) != (0xFF0000, 0xFF00, 0xFF)
    ):
        return None
    rows = abs(height)
    stride = ((width * bits + 31) // 32) * 4
    if offset + stride * rows > len(dib) or width * rows > 100_000_000:
        return None
    step = bits // 8
    raw = bytearray()
    for i in range(rows):
        y = rows - 1 - i if height > 0 else i  # 高さが正なら、下の行から並んでいる
        row = dib[offset + y * stride: offset + y * stride + width * step]
        out = bytearray(width * 3)
        out[0::3], out[1::3], out[2::3] = row[2::step], row[1::step], row[0::step]
        raw += b"\0" + out

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, rows, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


@dataclass
class _Incoming:
    name: str
    size: int
    to_clipboard: bool
    tmp: Path
    fh: Any
    received: int = 0


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class FileTransfer:
    """1つの接続の、ファイルの受け渡し。"""

    def __init__(
        self,
        send_json: Callable[[dict[str, Any]], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
        backend,
    ) -> None:
        self._send = send_json
        self._send_bytes = send_bytes
        self.backend = backend
        self._incoming: dict[int, _Incoming] = {}
        self._pull: asyncio.Task | None = None
        self._next_id = 1

    @property
    def directory(self) -> Path:
        return Path(getattr(self.backend, "download_dir", None) or default_download_dir())

    async def _error(self, message: str) -> None:
        await self._send({"t": "error", "message": message})

    async def handle(self, msg: dict[str, Any]) -> None:
        t = msg.get("t")
        if t == "file_put":
            await self._put(msg)
        elif t == "file_end":
            await self._end(msg)
        elif t == "clip_pull":
            await self._start_pull()

    # --- iPhone → PC

    async def _put(self, msg: dict[str, Any]) -> None:
        fid, name, size = msg.get("id"), msg.get("name"), msg.get("size")
        if not _is_int(fid) or not isinstance(name, str) or not _is_int(size) or size < 0:
            await self._error("ファイルの指定が正しくありません")
            return
        if size > MAX_FILE_BYTES:
            await self._error(f"ファイルが大きすぎます（{MAX_FILE_BYTES // (1024 * 1024)}MBまで）")
            return
        if fid in self._incoming or len(self._incoming) >= MAX_INCOMING:
            await self._error("同時に受け取れるファイルの数を超えました")
            return
        directory = self.directory
        try:
            directory.mkdir(parents=True, exist_ok=True)
            tmp = directory / f".duo-{uuid.uuid4().hex}.part"
            fh = open(tmp, "wb")
        except OSError as e:
            await self._error(f"保存先を用意できませんでした（{e}）")
            return
        self._incoming[fid] = _Incoming(name, size, msg.get("to_clipboard") is True, tmp, fh)

    async def handle_binary(self, raw: bytes) -> None:
        """クライアントからのバイナリフレーム。ファイルの中身だけを受け付ける。"""
        parsed = protocol.unpack_file(raw)
        if parsed is None:
            return
        fid, data = parsed
        item = self._incoming.get(fid)
        if item is None:
            return
        item.received += len(data)
        if item.received > item.size:
            self._discard(fid)
            await self._error(f"ファイルの大きさが、知らされた大きさ（{item.size}バイト）より大きくなりました")
            return
        try:
            item.fh.write(data)
        except OSError as e:
            self._discard(fid)
            await self._error(f"ファイルを書き込めませんでした（{e}）")

    def _discard(self, fid: int) -> None:
        item = self._incoming.pop(fid, None)
        if item is None:
            return
        try:
            item.fh.close()
        except OSError:
            pass
        try:
            item.tmp.unlink()
        except OSError:
            pass

    async def _end(self, msg: dict[str, Any]) -> None:
        fid = msg.get("id")
        item = self._incoming.get(fid) if _is_int(fid) else None
        if item is None:
            return
        if item.received != item.size:
            self._discard(fid)
            await self._error(f"ファイルの大きさが合いません（{item.size}バイトのはずが、{item.received}バイト）")
            return
        del self._incoming[fid]
        try:
            item.fh.close()
            final = unique_path(item.tmp.parent, sanitize_name(item.name))
            os.replace(item.tmp, final)
        except OSError as e:
            try:
                item.tmp.unlink()
            except OSError:
                pass
            await self._error(f"ファイルを保存できませんでした（{e}）")
            return
        log.info("ファイルを保存しました: %s", final)
        await self._send({"t": "file_saved", "id": fid, "path": str(final)})
        if item.to_clipboard:
            await self._to_clipboard(final)

    async def _to_clipboard(self, path: Path) -> None:
        suffix = path.suffix.lower()
        try:
            if suffix in IMAGE_EXTENSIONS:
                try:
                    await asyncio.to_thread(self.backend.clip_set_image, str(path))
                except ClipboardError:
                    if suffix == ".png":
                        raise
                    # 画像にできなかったときは、ファイルとして入れる
                    await asyncio.to_thread(self.backend.clip_set_files, [str(path)])
            else:
                await asyncio.to_thread(self.backend.clip_set_files, [str(path)])
        except (ClipboardError, OSError) as e:
            await self._error(f"保存はできましたが、クリップボードには入れられませんでした（{e}）")
            return
        await self._send({"t": "notice", "message": "PCのクリップボードに入れました"})

    # --- PC → iPhone

    async def _start_pull(self) -> None:
        if self._pull is not None and not self._pull.done():
            await self._error("PCのクリップボードの中身を送っている途中です")
            return
        self._pull = asyncio.create_task(self._run_pull())

    async def _run_pull(self) -> None:
        try:
            await self._pull_clipboard()
        except (ConnectionClosed, asyncio.CancelledError):
            raise
        except Exception as e:
            log.exception("クリップボードの中身を送れませんでした")
            try:
                await self._error(f"クリップボードの中身を送れませんでした（{e}）")
            except ConnectionClosed:
                pass

    async def _pull_clipboard(self) -> None:
        try:
            paths = await asyncio.to_thread(self.backend.clip_files)
            if paths:
                await self._send_clip_files(paths)
                return
            image = await asyncio.to_thread(self.backend.clip_image)
            if image:
                await self._send_clip_image(*image)
                return
            text = from_windows_newlines(await asyncio.to_thread(self.backend.clip_get))
        except ClipboardError as e:
            await self._error(str(e))
            return
        if len(text) > protocol.MAX_CLIP_LENGTH:
            await self._error(f"PCのクリップボードの文字が長すぎます（{protocol.MAX_CLIP_LENGTH}文字まで）")
            return
        await self._send({"t": "clip", "s": text})

    async def _send_clip_files(self, paths: list[str]) -> None:
        files = [Path(p) for p in paths if os.path.isfile(p)]  # フォルダは送らない
        if not files:
            await self._error("クリップボードにあるのはフォルダだけです。フォルダは送れません")
            return
        try:
            sizes = [f.stat().st_size for f in files]
        except OSError as e:
            await self._error(f"ファイルを読めませんでした（{e}）")
            return
        if sum(sizes) > MAX_FILE_BYTES:
            await self._error(f"ファイルの合計が大きすぎます（{MAX_FILE_BYTES // (1024 * 1024)}MBまで）")
            return
        for path, size in zip(files, sizes):
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                fh = open(path, "rb")
            except OSError as e:
                await self._error(f"ファイル「{path.name}」を読めませんでした（{e}）")
                return
            with fh:
                await self._send_stream(path.name, size, mime, fh.read)

    async def _send_clip_image(self, kind: str, data: bytes) -> None:
        if kind == "png":
            name, mime = "clipboard.png", "image/png"
        else:  # DIB。PNG にできれば PNG、できなければ BMP
            png = await asyncio.to_thread(dib_to_png, data)
            if png is not None:
                data, name, mime = png, "clipboard.png", "image/png"
            else:
                bmp = dib_to_bmp(data)
                if bmp is None:
                    await self._error("クリップボードの画像を読めませんでした")
                    return
                data, name, mime = bmp, "clipboard.bmp", "image/bmp"
        # 写真のような大きな画像は、JPEG の方がずっと小さい（モバイル回線で時間がかからない）
        jpeg = await asyncio.to_thread(to_jpeg_if_smaller, data)
        if jpeg is not None:
            data, name, mime = jpeg, "clipboard.jpg", "image/jpeg"
        if len(data) > MAX_FILE_BYTES:
            await self._error(f"画像が大きすぎます（{MAX_FILE_BYTES // (1024 * 1024)}MBまで）")
            return
        pos = 0

        def read(n: int) -> bytes:
            nonlocal pos
            chunk = data[pos:pos + n]
            pos += len(chunk)
            return chunk

        await self._send_stream(name, len(data), mime, read)

    async def _send_stream(self, name: str, size: int, mime: str, read: Callable[[int], bytes]) -> None:
        fid = self._next_id
        self._next_id += 1
        await self._send({"t": "file_begin", "id": fid, "name": name, "size": size, "mime": mime})
        try:
            while True:
                chunk = await asyncio.to_thread(read, CHUNK_BYTES)
                if not chunk:
                    break
                await self._send_bytes(protocol.pack_file(fid, chunk))
        except OSError as e:
            await self._error(f"ファイル「{name}」を読んでいる途中で失敗しました（{e}）")
            return
        await self._send({"t": "file_end", "id": fid})

    async def close(self) -> None:
        """切断したとき。受け取りかけのファイルは捨て、送っている途中のものは止める。"""
        for fid in list(self._incoming):
            self._discard(fid)
        task, self._pull = self._pull, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
