"""UDP での映像と音のテスト。UDP のソケットは、ループバック（127.0.0.1）で、実際に動かす。
Windows の画面取得（ffmpeg）は使わず、映像は偽物にしている。"""
import asyncio
import json
import struct

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from duo_server import protocol
from duo_server import udp as udp_mod
from duo_server.capture import build_ffmpeg_cmd
from duo_server.input_win import NullInput
from duo_server.server import Settings, make_handler
from duo_server.udp import KIND_ACK, KIND_AUDIO, KIND_BIND, KIND_KEEPALIVE, KIND_VIDEO, UdpEndpoint

TOKEN = "test-token"


# --- 分けて送る関数

def test_split_payload_sizes():
    assert udp_mod.MAX_CHUNK == 1186
    assert udp_mod.split_payload(b"") == [b""]
    assert udp_mod.split_payload(b"a" * 1186) == [b"a" * 1186]
    parts = udp_mod.split_payload(b"a" * 1187)
    assert [len(p) for p in parts] == [1186, 1]
    parts = udp_mod.split_payload(bytes(range(256)) * 20)
    assert b"".join(parts) == bytes(range(256)) * 20
    assert all(len(p) <= 1186 for p in parts)


def test_pack_datagrams_header_and_count():
    payload = b"x" * 3000
    grams = udp_mod.pack_datagrams(KIND_VIDEO, 7, 1234, True, payload)
    assert len(grams) == 3
    assert all(len(g) <= 1200 for g in grams)
    for i, g in enumerate(grams):
        kind, seq, ts, flags, index, count = struct.unpack(">BIIBHH", g[:14])
        assert (kind, seq, ts, flags, index, count) == (KIND_VIDEO, 7, 1234, 1, i, 3)
    assert b"".join(g[14:] for g in grams) == payload


def test_audio_is_not_split_and_has_no_keyframe_flag():
    grams = udp_mod.pack_datagrams(KIND_AUDIO, 0, 5, True, b"o" * 300)
    assert len(grams) == 1
    kind, _seq, _ts, flags, index, count = struct.unpack(">BIIBHH", grams[0][:14])
    assert (kind, flags, index, count) == (KIND_AUDIO, 0, 0, 1)


def test_sequence_numbers_are_separate_for_video_and_audio():
    peer = udp_mod.UdpPeer()
    assert [peer.next_seq(KIND_VIDEO) for _ in range(3)] == [0, 1, 2]
    assert [peer.next_seq(KIND_AUDIO) for _ in range(2)] == [0, 1]
    assert peer.next_seq(KIND_VIDEO) == 3


def test_key_is_16_hex_chars():
    k = udp_mod.new_key()
    assert len(k) == 16 and bytes.fromhex(k)
    assert udp_mod.new_key() != k


def test_short_gop_in_ffmpeg_command():
    def gop(**kw):
        cmd = build_ffmpeg_cmd(fps=60, **kw)
        return cmd[cmd.index("-g") + 1]
    assert gop() == "120"
    assert gop(short_gop=True) == "30"
    assert "expr:gte(t,n_forced*0.5)" in build_ffmpeg_cmd(fps=60, window=1, short_gop=True)
    assert "expr:gte(t,n_forced*2)" in build_ffmpeg_cmd(fps=60, window=1)


# --- ソケット

class Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.queue.put_nowait(data)

    async def get(self, timeout=5):
        return await asyncio.wait_for(self.queue.get(), timeout)


async def open_client(port):
    loop = asyncio.get_running_loop()
    _, proto = await loop.create_datagram_endpoint(Collector, remote_addr=("127.0.0.1", port))
    return proto


def run(coro):
    return asyncio.run(coro)


def test_bind_with_right_key_gets_ack():
    async def go():
        ep = await UdpEndpoint.open("127.0.0.1", 0)
        peer = ep.new_peer()
        client = await open_client(ep.port)
        try:
            key = bytes.fromhex(peer.key)
            client.transport.sendto(bytes([KIND_BIND]) + key)
            assert await client.get() == bytes([KIND_ACK]) + key
            assert peer.bound
            client.transport.sendto(bytes([KIND_BIND]) + key)  # 何度でも、同じ ACK
            assert await client.get() == bytes([KIND_ACK]) + key
        finally:
            client.transport.close()
            ep.close()
    run(go())


def test_bind_with_wrong_key_is_ignored():
    async def go():
        ep = await UdpEndpoint.open("127.0.0.1", 0)
        peer = ep.new_peer()
        client = await open_client(ep.port)
        try:
            client.transport.sendto(bytes([KIND_BIND]) + b"\x00" * 8)
            client.transport.sendto(bytes([KIND_BIND]) + b"\x01")  # 短い
            await asyncio.sleep(0.2)
            assert client.queue.empty()
            assert not peer.bound
        finally:
            client.transport.close()
            ep.close()
    run(go())


def test_keepalive_updates_only_bound_sender():
    async def go():
        ep = await UdpEndpoint.open("127.0.0.1", 0)
        peer = ep.new_peer()
        a, b = await open_client(ep.port), await open_client(ep.port)
        try:
            a.transport.sendto(bytes([KIND_BIND]) + bytes.fromhex(peer.key))
            await a.get()
            seen = peer.last_seen
            await asyncio.sleep(0.05)
            b.transport.sendto(bytes([KIND_KEEPALIVE]))  # 知らない送り元
            await asyncio.sleep(0.1)
            assert peer.last_seen == seen
            a.transport.sendto(bytes([KIND_KEEPALIVE]))
            await asyncio.sleep(0.1)
            assert peer.last_seen > seen
        finally:
            a.transport.close()
            b.transport.close()
            ep.close()
    run(go())


# --- WebSocket と合わせて

class StreamingStream:
    """決まった間隔で、フレームを出し続ける偽物の映像。5枚に1枚がキーフレーム。"""
    big_key = 0

    async def start(self):
        pass

    async def frames(self):
        n = 0
        while True:
            key = n % 5 == 0
            head = b"K" * self.big_key if key and self.big_key else b"\x00\x00\x00\x01\x09\xf0"
            yield key, head + bytes([n % 256])
            n += 1
            await asyncio.sleep(0.02)

    async def stop(self):
        pass


async def with_server(body, *, udp=True, big_key=0, settings_seen=None):
    ep = await UdpEndpoint.open("127.0.0.1", 0) if udp else None

    def factory(settings, src, region, monitor=None, window=None):
        if settings_seen is not None:
            settings_seen.append(settings)
        s = StreamingStream()
        s.big_key = big_key
        return s

    handler = make_handler(Settings(token=TOKEN), NullInput((1920, 1080)), factory, None, ep)
    try:
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            await body(f"ws://127.0.0.1:{port}", ep)
    finally:
        if ep:
            ep.close()


def hello(**extra):
    return json.dumps({"t": "hello", "v": protocol.VERSION, "token": TOKEN, **extra})


async def recv_json(ws):
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(raw, str):
            return json.loads(raw)


async def recv_type(ws, t):
    """ほかの制御メッセージ（ime_state など）は読み飛ばして、種類 t のメッセージを返す。"""
    while True:
        msg = await recv_json(ws)
        if msg["t"] == t:
            return msg


async def connect_udp(ws_url, ep):
    """hello、BIND、ACK、transport udp まで進める。(ws, welcome, UDP のクライアント) を返す。"""
    ws = await connect(ws_url)
    await ws.send(hello(transport="udp"))
    welcome = await recv_json(ws)
    client = await open_client(welcome["udp_port"])
    key = bytes.fromhex(welcome["udp_key"])
    client.transport.sendto(bytes([KIND_BIND]) + key)
    assert await client.get() == bytes([KIND_ACK]) + key
    await ws.send(json.dumps({"t": "transport", "mode": "udp"}))
    return ws, welcome, client


async def drain(ws, timeout):
    """timeout のあいだ届いたものを、すべて返す。"""
    got = []
    try:
        while True:
            got.append(await asyncio.wait_for(ws.recv(), timeout))
    except asyncio.TimeoutError:
        return got


def test_welcome_has_udp_only_when_asked_and_available():
    async def body(url, ep):
        for transport, expected in (("udp", True), ("auto", True), ("tcp", False), (None, False)):
            async with connect(url) as ws:
                extra = {} if transport is None else {"transport": transport}
                await ws.send(hello(**extra))
                welcome = await recv_json(ws)
                assert ("udp_key" in welcome) == expected, transport
                assert ("udp_port" in welcome) == expected
                if expected:
                    assert welcome["udp_port"] == ep.port
                    assert len(welcome["udp_key"]) == 16
    run(with_server(body))


def test_udp_request_without_udp_is_error_and_closes():
    async def body(url, ep):
        async with connect(url) as ws:
            await ws.send(hello(transport="udp"))
            msg = await recv_json(ws)
            assert msg["t"] == "error"
            await ws.wait_closed()
        async with connect(url) as ws:  # auto なら、tcp で続ける
            await ws.send(hello(transport="auto"))
            welcome = await recv_json(ws)
            assert welcome["t"] == "welcome" and "udp_key" not in welcome
    run(with_server(body, udp=False))


def test_short_gop_only_for_udp_and_auto():
    seen = []

    async def body(url, ep):
        for transport, expected in (("tcp", False), ("udp", True), ("auto", True)):
            async with connect(url) as ws:
                await ws.send(hello(transport=transport))
                await recv_json(ws)
            assert seen[-1].short_gop is expected, transport
    run(with_server(body, settings_seen=seen))


def test_video_goes_over_udp_after_transport_udp():
    async def body(url, ep):
        ws, welcome, client = await connect_udp(url, ep)
        try:
            first = None
            for _ in range(20):
                d = await client.get()
                if d[0] == KIND_VIDEO:
                    first = d
                    break
            assert first is not None and len(first) <= 1200
            await drain(ws, 0.3)  # 切り替え前に送られた分を、読み捨てる
            got = await drain(ws, 0.5)
            assert [m for m in got if isinstance(m, bytes)] == []
            seqs = []
            while len(seqs) < 5:
                d = await client.get()
                if d[0] == KIND_VIDEO:
                    seqs.append(struct.unpack(">I", d[1:5])[0])
            assert seqs == list(range(seqs[0], seqs[0] + 5))
        finally:
            client.transport.close()
            await ws.close()
    run(with_server(body))


def test_big_frame_is_split_over_udp():
    async def body(url, ep):
        ws, welcome, client = await connect_udp(url, ep)
        try:
            for _ in range(200):
                d = await client.get()
                kind, _seq, _ts, flags, index, count = struct.unpack(">BIIBHH", d[:14])
                if kind == KIND_VIDEO and flags & 1:
                    assert count == 3  # 3000 バイトと数バイト
                    assert len(d) <= 1200
                    break
            else:
                raise AssertionError("キーフレームが届かない")
        finally:
            client.transport.close()
            await ws.close()
    run(with_server(body, big_key=3000))


def test_keepalive_timeout_returns_to_websocket(monkeypatch):
    monkeypatch.setattr(udp_mod, "KEEPALIVE_TIMEOUT_SEC", 0.6)

    async def body(url, ep):
        ws, welcome, client = await connect_udp(url, ep)
        try:
            msg = await recv_type(ws, "transport")  # KEEPALIVE を送らない
            assert msg == {"t": "transport", "mode": "tcp"}
            # そのあとの映像は、WebSocket に来て、キーフレームから始まる
            while True:
                raw = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(raw, bytes):
                    assert protocol.unpack_frame(raw)[0]
                    break
        finally:
            client.transport.close()
            await ws.close()
    run(with_server(body))


def test_keepalive_keeps_udp(monkeypatch):
    monkeypatch.setattr(udp_mod, "KEEPALIVE_TIMEOUT_SEC", 0.6)

    async def body(url, ep):
        ws, welcome, client = await connect_udp(url, ep)
        try:
            for _ in range(8):  # 1.6 秒のあいだ、0.2 秒おきに送る
                client.transport.sendto(bytes([KIND_KEEPALIVE]))
                await asyncio.sleep(0.2)
            got = await drain(ws, 0.1)
            assert not any(isinstance(m, str) and '"transport"' in m for m in got)
        finally:
            client.transport.close()
            await ws.close()
    run(with_server(body))


def test_transport_requests():
    async def body(url, ep):
        # BIND していないのに udp を頼むと error
        async with connect(url) as ws:
            await ws.send(hello(transport="udp"))
            await recv_json(ws)
            await ws.send(json.dumps({"t": "transport", "mode": "udp"}))
            await recv_type(ws, "error")
        # tcp で始めた接続では、udp にできない
        async with connect(url) as ws:
            await ws.send(hello())
            await recv_json(ws)
            await ws.send(json.dumps({"t": "transport", "mode": "udp"}))
            await recv_type(ws, "error")
        # udp から tcp に戻すと、映像が WebSocket に、キーフレームから来る
        ws, welcome, client = await connect_udp(url, ep)
        try:
            await asyncio.sleep(0.2)
            await ws.send(json.dumps({"t": "transport", "mode": "tcp"}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(raw, bytes):
                    assert protocol.unpack_frame(raw)[0]
                    break
        finally:
            client.transport.close()
            await ws.close()
    run(with_server(body))


def test_tcp_session_is_unchanged():
    async def body(url, ep):
        async with connect(url) as ws:
            await ws.send(hello())
            welcome = await recv_json(ws)
            assert "udp_key" not in welcome
            frames = 0
            while frames < 3:
                raw = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(raw, bytes):
                    frames += 1
    run(with_server(body))
