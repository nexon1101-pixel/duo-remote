"""通信の取り決め。docs/protocol.md と対応する。"""
from __future__ import annotations

import json
import struct
from typing import Any

VERSION = 1

# 映像フレームの先頭: フラグ(1バイト) + タイムスタンプ(4バイト, ミリ秒)
FRAME_HEADER = struct.Struct(">BI")
FLAG_KEYFRAME = 0x01
FLAG_AUDIO = 0x02  # 音のフレーム（立っていなければ、映像）
FLAG_FILE = 0x04  # ファイルの中身のフレーム（先頭4バイトの後ろは、タイムスタンプではなく、ファイルの id）

MAX_TEXT_LENGTH = 4096
MAX_CLIP_LENGTH = 100_000  # クリップボードの文字の上限（文字で数える）


def pack_frame(payload: bytes, keyframe: bool, ts_ms: int) -> bytes:
    flags = FLAG_KEYFRAME if keyframe else 0
    return FRAME_HEADER.pack(flags, ts_ms & 0xFFFFFFFF) + payload


def pack_audio(payload: bytes, ts_ms: int) -> bytes:
    return FRAME_HEADER.pack(FLAG_AUDIO, ts_ms & 0xFFFFFFFF) + payload


def is_audio(data: bytes) -> bool:
    return bool(data) and bool(data[0] & FLAG_AUDIO)


def pack_file(file_id: int, payload: bytes) -> bytes:
    return FRAME_HEADER.pack(FLAG_FILE, file_id & 0xFFFFFFFF) + payload


def is_file(data: bytes) -> bool:
    return bool(data) and bool(data[0] & FLAG_FILE)


def unpack_file(data: bytes) -> tuple[int, bytes] | None:
    """ファイルの中身のフレームを (id, 中身) にする。形が違えばNone。"""
    if len(data) < FRAME_HEADER.size or not data[0] & FLAG_FILE:
        return None
    _flags, file_id = FRAME_HEADER.unpack_from(data)
    return file_id, data[FRAME_HEADER.size:]


def unpack_frame(data: bytes) -> tuple[bool, int, bytes]:
    flags, ts_ms = FRAME_HEADER.unpack_from(data)
    return bool(flags & FLAG_KEYFRAME), ts_ms, data[FRAME_HEADER.size:]


def dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | bytes) -> dict[str, Any] | None:
    """JSONの制御メッセージを読む。形式が違う場合はNoneを返す。"""
    if isinstance(raw, bytes):
        return None
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None
