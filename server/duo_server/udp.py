"""UDP での映像と音。docs/protocol.md の「UDP での映像と音」と対応する。"""
from __future__ import annotations

import asyncio
import logging
import secrets
import struct
import time

log = logging.getLogger(__name__)

KIND_BIND = 0x01
KIND_ACK = 0x02
KIND_KEEPALIVE = 0x03
KIND_VIDEO = 0x10
KIND_AUDIO = 0x11

KEY_BYTES = 8  # udp_key は、16進で16文字（8バイト）
# データグラム全体は 1200 バイト以下。ヘッダーは 14 バイトなので、中身は 1186 バイト以下
MAX_DATAGRAM = 1200
HEADER = struct.Struct(">BIIBHH")
MAX_CHUNK = MAX_DATAGRAM - HEADER.size  # 1186
# BIND も KEEPALIVE も、この秒数のあいだ来なければ、WebSocket に戻す
KEEPALIVE_TIMEOUT_SEC = 6.0


def new_key() -> str:
    """この接続だけの合言葉（16進の16文字）。"""
    return secrets.token_hex(KEY_BYTES)


def split_payload(payload: bytes, max_chunk: int = MAX_CHUNK) -> list[bytes]:
    """中身を、max_chunk バイト以下に分ける。空の中身も、空の1つとして返す（分けた数は 1 以上）。"""
    if len(payload) <= max_chunk:
        return [payload]
    return [payload[i:i + max_chunk] for i in range(0, len(payload), max_chunk)]


def pack_datagrams(kind: int, seq: int, ts_ms: int, keyframe: bool, payload: bytes) -> list[bytes]:
    """1つのフレームを、送るデータグラムに分ける。音（KIND_AUDIO）は、キーフレームの印を付けない。"""
    chunks = split_payload(payload)
    flags = 1 if keyframe and kind == KIND_VIDEO else 0
    return [
        HEADER.pack(kind, seq & 0xFFFFFFFF, ts_ms & 0xFFFFFFFF, flags, i, len(chunks)) + chunk
        for i, chunk in enumerate(chunks)
    ]


class UdpPeer:
    """1つの接続の、UDP の相手。BIND が合うまで、addr は None。"""

    def __init__(self) -> None:
        self.key = new_key()
        self.addr: tuple | None = None
        self.last_seen = 0.0
        self.video_seq = 0  # 映像と音は、別々に数える
        self.audio_seq = 0

    @property
    def bound(self) -> bool:
        return self.addr is not None

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) - self.last_seen > KEEPALIVE_TIMEOUT_SEC

    def next_seq(self, kind: int) -> int:
        if kind == KIND_VIDEO:
            seq, self.video_seq = self.video_seq, (self.video_seq + 1) & 0xFFFFFFFF
        else:
            seq, self.audio_seq = self.audio_seq, (self.audio_seq + 1) & 0xFFFFFFFF
        return seq


class UdpEndpoint(asyncio.DatagramProtocol):
    """UDP のソケット。接続ごとの UdpPeer を覚えておき、BIND と KEEPALIVE を受ける。"""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.peers: dict[bytes, UdpPeer] = {}

    @classmethod
    async def open(cls, host: str, port: int) -> "UdpEndpoint":
        loop = asyncio.get_running_loop()
        _, endpoint = await loop.create_datagram_endpoint(cls, local_addr=(host, port))
        return endpoint

    @property
    def port(self) -> int:
        assert self.transport is not None
        return self.transport.get_extra_info("sockname")[1]

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None

    def new_peer(self) -> UdpPeer:
        peer = UdpPeer()
        self.peers[bytes.fromhex(peer.key)] = peer
        return peer

    def drop_peer(self, peer: UdpPeer) -> None:
        self.peers.pop(bytes.fromhex(peer.key), None)

    def buffered_bytes(self) -> int:
        try:
            return self.transport.get_write_buffer_size() if self.transport is not None else 0
        except (AttributeError, RuntimeError):
            return 0

    def send_frame(self, peer: UdpPeer, kind: int, ts_ms: int, keyframe: bool, payload: bytes) -> None:
        if self.transport is None or peer.addr is None:
            return
        seq = peer.next_seq(kind)
        for datagram in pack_datagrams(kind, seq, ts_ms, keyframe, payload):
            self.transport.sendto(datagram, peer.addr)

    # --- DatagramProtocol

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if not data or self.transport is None:
            return
        kind = data[0]
        now = time.monotonic()
        if kind == KIND_BIND and len(data) == 1 + KEY_BYTES:
            peer = self.peers.get(data[1:])
            if peer is None:
                return  # 合わない BIND は、無視する
            if peer.addr != addr:
                log.info("UDP の相手を覚えました: %s", addr)
            peer.addr = addr
            peer.last_seen = now
            self.transport.sendto(bytes([KIND_ACK]) + data[1:], addr)
        elif kind == KIND_KEEPALIVE:
            for peer in self.peers.values():
                if peer.addr == addr:
                    peer.last_seen = now

    def error_received(self, exc: Exception) -> None:
        log.debug("UDP のエラー: %s", exc)
