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

MAX_TEXT_LENGTH = 4096


def pack_frame(payload: bytes, keyframe: bool, ts_ms: int) -> bytes:
    flags = FLAG_KEYFRAME if keyframe else 0
    return FRAME_HEADER.pack(flags, ts_ms & 0xFFFFFFFF) + payload


def pack_audio(payload: bytes, ts_ms: int) -> bytes:
    return FRAME_HEADER.pack(FLAG_AUDIO, ts_ms & 0xFFFFFFFF) + payload


def is_audio(data: bytes) -> bool:
    return bool(data) and bool(data[0] & FLAG_AUDIO)


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
