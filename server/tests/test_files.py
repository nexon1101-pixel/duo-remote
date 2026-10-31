import asyncio
import json
import shutil
import struct
import zlib

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from duo_server import files, protocol
from duo_server.input_win import ClipboardError, NullInput
from duo_server.server import Settings, make_handler

TOKEN = "t"


# --- 名前と形式 ---------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("photo.jpg", "photo.jpg"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\x\\メモ.txt", "メモ.txt"),
    ('a<b>:"c"|d?*.txt', "abcd.txt"),
    ("tab\tname.txt", "tabname.txt"),
    ("名前 .txt. ", "名前 .txt"),
    ("", "file"),
    ("...", "file"),
    ("a/", "file"),
    ("CON.txt", "_CON.txt"),
    (".hidden", ".hidden"),
])
def test_sanitize_name(raw, expected):
    assert files.sanitize_name(raw) == expected


def test_sanitize_name_long():
    name = files.sanitize_name("x" * 400 + ".png")
    assert len(name) <= files.MAX_NAME_LENGTH and name.endswith(".png")


def test_unique_path(tmp_path):
    assert files.unique_path(tmp_path, "a.txt") == tmp_path / "a.txt"
    (tmp_path / "a.txt").write_text("1")
    assert files.unique_path(tmp_path, "a.txt") == tmp_path / "a (2).txt"
    (tmp_path / "a (2).txt").write_text("2")
    assert files.unique_path(tmp_path, "a.txt") == tmp_path / "a (3).txt"
    (tmp_path / "noext").write_text("")
    assert files.unique_path(tmp_path, "noext") == tmp_path / "noext (2)"


def test_hdrop_round_trip():
    paths = [r"C:\Users\x\a.txt", r"D:\メモ 1.png"]
    assert files.parse_hdrop(files.build_hdrop(paths)) == paths
    assert files.parse_hdrop(b"") == []


def make_dib(width, height, bits, pixels_bgr, *, top_down=False):
    """pixels_bgr: 行ごとの [(b, g, r), ...]（見た目の上の行から）。"""
    step = bits // 8
    stride = ((width * bits + 31) // 32) * 4
    rows = []
    for row in pixels_bgr:
        data = b"".join(bytes(px) + (b"\xff" if step == 4 else b"") for px in row)
        rows.append(data + bytes(stride - len(data)))
    if not top_down:
        rows.reverse()
    head = struct.pack("<IiiHHIIiiII", 40, width, -height if top_down else height, 1, bits, 0, 0, 0, 0, 0, 0)
    return head + b"".join(rows)


def decode_png(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        assert zlib.crc32(kind + body) == struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h, depth, ctype, *_ = ihdr
    assert (depth, ctype) == (8, 2)
    raw = zlib.decompress(idat)
    rows = []
    for y in range(h):
        row = raw[y * (1 + w * 3):(y + 1) * (1 + w * 3)]
        assert row[0] == 0
        rows.append([tuple(row[1 + 3 * x:4 + 3 * x]) for x in range(w)])
    return w, h, rows


PALETTE_DIB = struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 8, 0, 0, 0, 0, 0, 0) + bytes(4 * 256 + 8)


@pytest.mark.parametrize("bits", [24, 32])
@pytest.mark.parametrize("top_down", [False, True])
def test_dib_to_png(bits, top_down):
    # 3x2。BGR で指定し、PNG では RGB になる
    px = [[(1, 2, 3), (4, 5, 6), (7, 8, 9)], [(10, 11, 12), (13, 14, 15), (16, 17, 18)]]
    png = files.dib_to_png(make_dib(3, 2, bits, px, top_down=top_down))
    w, h, rows = decode_png(png)
    assert (w, h) == (3, 2)
    assert rows == [[(3, 2, 1), (6, 5, 4), (9, 8, 7)], [(12, 11, 10), (15, 14, 13), (18, 17, 16)]]


def test_dib_to_png_unsupported_and_bmp():
    dib = make_dib(2, 2, 24, [[(0, 0, 0)] * 2] * 2)
    assert files.dib_to_png(PALETTE_DIB) is None
    assert files.dib_to_png(b"short") is None
    bmp = files.dib_to_bmp(PALETTE_DIB)
    assert bmp[:2] == b"BM" and struct.unpack_from("<I", bmp, 2)[0] == len(bmp)
    assert struct.unpack_from("<I", bmp, 10)[0] == 14 + 40 + 256 * 4
    assert files.dib_to_bmp(dib)[14:] == dib


# --- サーバーとの通信 ---------------------------------------------------------

class FakeVideo:
    async def start(self):
        pass

    async def frames(self):
        yield True, b"\x00\x00\x00\x01\x09\xf0KEY"
        await asyncio.sleep(3600)

    async def stop(self):
        pass


def run_server(body, backend):
    """body(url) を、サーバーを立てて実行し、その戻り値を返す。"""
    async def go():
        handler = make_handler(Settings(token=TOKEN), backend, lambda *a, **k: FakeVideo())
        async with serve(handler, "127.0.0.1", 0) as server:
            return await body(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")

    return asyncio.run(go())


async def open_session(url):
    ws = await connect(url)
    await ws.send(json.dumps({"t": "hello", "v": protocol.VERSION, "token": TOKEN}))
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(raw, str) and json.loads(raw)["t"] == "welcome":
            return ws


async def recv_until(ws, wanted):
    """wanted のJSONが来るまで読み、それまでに来た、JSONとファイルのフレーム（id, 中身）を返す（最後が wanted）。"""
    got = []
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(raw, str):
            msg = json.loads(raw)
            got.append(msg)
            if msg["t"] == wanted:
                return got
        elif protocol.is_file(raw):
            got.append(protocol.unpack_file(raw))


def put(ws, fid, name, size, **extra):
    return ws.send(json.dumps({"t": "file_put", "id": fid, "name": name, "size": size, **extra}))


def frame(fid, data):
    return protocol.pack_file(fid, data)


def end(ws, fid):
    return ws.send(json.dumps({"t": "file_end", "id": fid}))


@pytest.fixture
def backend(tmp_path):
    b = NullInput()
    b.download_dir = tmp_path / "DuoRemote"
    return b


def saved_files(backend):
    d = backend.download_dir
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_receive_file(backend):
    async def body(url):
        async with await open_session(url) as ws:
            await put(ws, 1, "hello.txt", 11)
            await ws.send(frame(1, b"hello "))
            await ws.send(frame(1, b"world"))
            await end(ws, 1)
            return (await recv_until(ws, "file_saved"))[-1]

    saved = run_server(body, backend)
    assert saved == {"t": "file_saved", "id": 1, "path": str(backend.download_dir / "hello.txt")}
    assert (backend.download_dir / "hello.txt").read_bytes() == b"hello world"
    assert saved_files(backend) == ["hello.txt"]  # 一時ファイルは残らない


def test_receive_empty_file_and_ignores_unknown_frames(backend):
    async def body(url):
        async with await open_session(url) as ws:
            await ws.send(frame(99, b"stray"))  # 知らない id は無視
            await ws.send(b"\x00\x00\x00\x00\x00junk")  # ファイルのフレームではない
            await put(ws, 2, "empty.bin", 0)
            await end(ws, 2)
            await recv_until(ws, "file_saved")

    run_server(body, backend)
    assert (backend.download_dir / "empty.bin").read_bytes() == b""


@pytest.mark.parametrize("sent", [[b"abc"], [b"abcdefgh", b"ijk"]])
def test_receive_size_mismatch_is_not_saved(backend, sent):
    async def body(url):
        async with await open_session(url) as ws:
            await put(ws, 1, "x.bin", 5)
            for chunk in sent:
                await ws.send(frame(1, chunk))
            await end(ws, 1)
            assert (await recv_until(ws, "error"))[-1]["t"] == "error"
            await ws.send(json.dumps({"t": "ping", "n": 1}))  # 接続は続く
            await recv_until(ws, "pong")

    run_server(body, backend)
    assert saved_files(backend) == []


def test_receive_too_big_and_bad_requests(backend):
    async def body(url):
        async with await open_session(url) as ws:
            for msg in (
                {"t": "file_put", "id": 1, "name": "big.bin", "size": files.MAX_FILE_BYTES + 1},
                {"t": "file_put", "id": 2, "name": "neg.bin", "size": -1},
                {"t": "file_put", "id": "x", "name": "a", "size": 1},
                {"t": "file_put", "id": 3, "name": 5, "size": 1},
            ):
                await ws.send(json.dumps(msg))
                assert (await recv_until(ws, "error"))[-1]["t"] == "error"
            await ws.send(frame(1, b"data"))  # 断ったファイルの中身は、捨てられる
            await end(ws, 1)
            await ws.send(json.dumps({"t": "ping", "n": 1}))
            await recv_until(ws, "pong")

    run_server(body, backend)
    assert saved_files(backend) == []


def test_receive_tidies_names_and_avoids_duplicates(backend):
    async def body(url):
        paths = []
        async with await open_session(url) as ws:
            for fid, name in enumerate(["../../a:b.txt", "ab.txt", "C:\\x\\ab.txt"], start=1):
                await put(ws, fid, name, 1)
                await ws.send(frame(fid, str(fid).encode()))
                await end(ws, fid)
                paths.append((await recv_until(ws, "file_saved"))[-1]["path"])
        return paths

    result = run_server(body, backend)
    d = backend.download_dir
    assert result == [str(d / "ab.txt"), str(d / "ab (2).txt"), str(d / "ab (3).txt")]
    assert saved_files(backend) == ["ab (2).txt", "ab (3).txt", "ab.txt"]
    assert (d / "ab (3).txt").read_bytes() == b"3"


def test_disconnect_midway_discards_partial_file(backend):
    async def body(url):
        ws = await open_session(url)
        await put(ws, 1, "big.bin", 1000)
        await ws.send(frame(1, b"x" * 100))
        await asyncio.sleep(0.2)
        assert any(p.name.endswith(".part") for p in backend.download_dir.iterdir())  # 受け取り中
        await ws.close()
        for _ in range(50):
            await asyncio.sleep(0.1)
            if saved_files(backend) == []:
                break

    run_server(body, backend)
    assert saved_files(backend) == []


def test_to_clipboard_image_and_other_files(backend):
    async def body(url):
        async with await open_session(url) as ws:
            for fid, name in ((1, "pic.png"), (2, "doc.pdf"), (3, "Photo.JPG")):
                await put(ws, fid, name, 3, to_clipboard=True)
                await ws.send(frame(fid, b"abc"))
                await end(ws, fid)
                await recv_until(ws, "notice")

    run_server(body, backend)
    d = backend.download_dir
    assert backend.calls == [
        ("clip_set_image", str(d / "pic.png")),
        ("clip_set_files", [str(d / "doc.pdf")]),
        ("clip_set_image", str(d / "Photo.JPG")),
    ]


def test_to_clipboard_failure_keeps_the_file(backend):
    def broken(path):
        raise ClipboardError("使えません")

    backend.clip_set_image = broken
    calls = []
    backend.clip_set_files = lambda paths: calls.append(paths)

    async def body(url):
        async with await open_session(url) as ws:
            # jpg は、画像にできなければ、ファイルとして入れる
            await put(ws, 1, "a.jpg", 1, to_clipboard=True)
            await ws.send(frame(1, b"x"))
            await end(ws, 1)
            await recv_until(ws, "notice")
            # png は、そのまま失敗を知らせる（保存はされている）
            await put(ws, 2, "b.png", 1, to_clipboard=True)
            await ws.send(frame(2, b"x"))
            await end(ws, 2)
            msgs = await recv_until(ws, "error")
            assert [m["t"] for m in msgs if isinstance(m, dict)] == ["file_saved", "error"]

    run_server(body, backend)
    assert calls == [[str(backend.download_dir / "a.jpg")]]
    assert saved_files(backend) == ["a.jpg", "b.png"]


# --- clip_pull ----------------------------------------------------------------

def collect_transfers(items):
    """recv_until の結果から、送られたファイルごとの {begin, data, frames} を作る。"""
    out, cur = [], None
    for it in items:
        if isinstance(it, dict) and it["t"] == "file_begin":
            cur = {"begin": it, "data": b"", "frames": 0}
        elif isinstance(it, tuple):
            assert cur is not None and it[0] == cur["begin"]["id"]
            cur["data"] += it[1]
            cur["frames"] += 1
            assert len(it[1]) <= files.CHUNK_BYTES
        elif isinstance(it, dict) and it["t"] == "file_end":
            assert it["id"] == cur["begin"]["id"]
            out.append(cur)
            cur = None
    return out


async def pull_files(ws, count=1):
    await ws.send(json.dumps({"t": "clip_pull"}))
    items = []
    for _ in range(count):
        items += await recv_until(ws, "file_end")
    return collect_transfers(items)


def test_clip_pull_files_skips_folders(backend, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_bytes(b"hello")
    big = bytes(range(256)) * 2048 + b"tail"  # 256KB を超える
    (src / "big.png").write_bytes(big)
    (src / "dir").mkdir()
    backend.clipboard_files = [str(src / "a.txt"), str(src / "dir"), str(src / "big.png")]
    backend.clipboard = "ignored text"

    async def body(url):
        async with await open_session(url) as ws:
            return await pull_files(ws, 2)

    a, b = run_server(body, backend)
    assert a["begin"] == {"t": "file_begin", "id": a["begin"]["id"], "name": "a.txt", "size": 5, "mime": "text/plain"}
    assert a["data"] == b"hello" and a["frames"] == 1
    assert b["begin"]["name"] == "big.png" and b["begin"]["size"] == len(big) and b["begin"]["mime"] == "image/png"
    assert b["data"] == big and b["frames"] == 3
    assert a["begin"]["id"] != b["begin"]["id"]


def test_clip_pull_folder_only_and_too_large(backend, tmp_path, monkeypatch):
    (tmp_path / "d").mkdir()
    (tmp_path / "f.bin").write_bytes(b"x" * 10)
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 15)

    async def body(url):
        async with await open_session(url) as ws:
            backend.clipboard_files = [str(tmp_path / "d")]
            await ws.send(json.dumps({"t": "clip_pull"}))
            assert "フォルダ" in (await recv_until(ws, "error"))[-1]["message"]
            backend.clipboard_files = [str(tmp_path / "f.bin"), str(tmp_path / "f.bin")]  # 合計 20 > 15
            await ws.send(json.dumps({"t": "clip_pull"}))
            assert "大きすぎ" in (await recv_until(ws, "error"))[-1]["message"]

    run_server(body, backend)


def test_clip_pull_png_image(backend):
    png = b"\x89PNG\r\n\x1a\nfakepng"
    backend.clipboard_image = ("png", png)

    async def body(url):
        async with await open_session(url) as ws:
            return await pull_files(ws)

    (t,) = run_server(body, backend)
    assert t["begin"]["name"] == "clipboard.png" and t["begin"]["mime"] == "image/png"
    assert t["data"] == png and t["begin"]["size"] == len(png)


def test_clip_pull_dib_image_becomes_png_or_bmp(backend):
    px = [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9), (10, 11, 12)]]
    backend.clipboard_image = ("dib", make_dib(2, 2, 32, px))

    async def body(url):
        async with await open_session(url) as ws:
            (png,) = await pull_files(ws)
            backend.clipboard_image = ("dib", PALETTE_DIB)  # PNG にできない DIB（256色）は BMP のまま送る
            (bmp,) = await pull_files(ws)
            return png, bmp

    png, bmp = run_server(body, backend)
    assert png["begin"]["mime"] == "image/png" and png["begin"]["name"] == "clipboard.png"
    assert decode_png(png["data"])[2] == [[(3, 2, 1), (6, 5, 4)], [(9, 8, 7), (12, 11, 10)]]
    assert bmp["begin"]["mime"] == "image/bmp" and bmp["begin"]["name"] == "clipboard.bmp"
    assert bmp["data"][:2] == b"BM" and bmp["begin"]["size"] == len(bmp["data"])


def test_clip_pull_text_when_nothing_else(backend):
    backend.clipboard = "one\r\ntwo"

    async def body(url):
        async with await open_session(url) as ws:
            await ws.send(json.dumps({"t": "clip_pull"}))
            assert (await recv_until(ws, "clip"))[-1] == {"t": "clip", "s": "one\ntwo"}
            backend.clipboard = ""
            await ws.send(json.dumps({"t": "clip_pull"}))
            assert (await recv_until(ws, "clip"))[-1] == {"t": "clip", "s": ""}

    run_server(body, backend)



@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg がない")
def test_big_clipboard_image_becomes_jpeg():
    """写真のような大きな画像は JPEG にする。小さい画像は、そのまま（None）。"""
    import subprocess

    from duo_server.files import to_jpeg_if_smaller

    def png(size: str, source: str) -> bytes:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"{source}=size={size}",
             "-frames:v", "1", "-f", "image2", "-c:v", "png", "pipe:1"],
            capture_output=True, check=True,
        ).stdout

    photo = png("1600x1200", "mandelbrot")  # 色の変化が細かく、PNG では大きくなる
    assert len(photo) > 1024 * 1024
    jpeg = to_jpeg_if_smaller(photo)
    assert jpeg is not None and jpeg[:2] == b"\xff\xd8" and len(jpeg) * 2 <= len(photo)
    assert to_jpeg_if_smaller(png("320x240", "testsrc")) is None  # 小さい画像は PNG のまま
