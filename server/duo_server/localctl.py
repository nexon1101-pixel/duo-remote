"""PCの中だけの制御用の入り口。管理画面が、動いているサーバーに「今すぐ片付けて」と頼むために使う。
iPhone との通信ではない（docs/protocol.md の対象外）。127.0.0.1 だけで待ち受け、鍵が合う相手だけを受け付ける。

1行ごとの JSON。クライアントは {"key": "...", "cmd": "cleanup"} のような1行を送り、サーバーは1行で返す。
cmd は cleanup と status だけ。"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

HOST = "127.0.0.1"  # 外から届かないよう、0.0.0.0 にはしない
MAX_LINE = 1024  # 1行の長さの上限（バイト）
READ_TIMEOUT = 5.0  # 1行が届くまで待つ秒数


async def _reply(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
    try:
        writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
    except (ConnectionError, OSError):
        pass


async def start_control_server(
    port: int, key: str,
    cleanup: Callable[[], Awaitable[dict[str, Any]]],
    status: Callable[[], dict[str, Any]],
    read_timeout: float = READ_TIMEOUT,
) -> asyncio.Server:
    """制御用の入り口を始める。port が 0 なら空いている番号を使う（テスト用）。"""
    key_bytes = key.encode("utf-8")

    async def serve_one(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), read_timeout)
            except asyncio.TimeoutError:
                await _reply(writer, {"ok": False, "error": "時間内に要求が届きませんでした"})
                return
            except (ValueError, asyncio.LimitOverrunError):
                await _reply(writer, {"ok": False, "error": "要求が長すぎます"})
                return
            except (ConnectionError, OSError):
                return
            try:
                msg = json.loads(line)
            except ValueError:
                await _reply(writer, {"ok": False, "error": "JSON として読めません"})
                return
            if not isinstance(msg, dict):
                await _reply(writer, {"ok": False, "error": "JSON の辞書を送ってください"})
                return
            given = msg.get("key")
            if not isinstance(given, str) or not hmac.compare_digest(given.encode("utf-8"), key_bytes):
                log.warning("制御用の入り口: 鍵が合わない要求を断りました")
                await _reply(writer, {"ok": False, "error": "鍵が違います"})
                return
            cmd = msg.get("cmd")
            try:
                if cmd == "cleanup":
                    result = await cleanup()
                elif cmd == "status":
                    result = await asyncio.to_thread(status)
                else:
                    result = {"ok": False, "error": f"知らない cmd です: {str(cmd)[:40]!r}"}
            except Exception as e:  # noqa: BLE001 片付けの失敗で、サーバーを落とさない
                log.exception("制御用の入り口: %s でエラーが出ました", cmd)
                result = {"ok": False, "error": f"サーバーの中でエラーが出ました（{type(e).__name__}）"}
            await _reply(writer, result)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    return await asyncio.start_server(serve_one, HOST, port, limit=MAX_LINE)
