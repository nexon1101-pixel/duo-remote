# -*- coding: utf-8 -*-
"""Driver for duo_client.py: send JSON commands from a file (one per line),
print each reply as one JSON line.

Usage: drive.py commands.jsonl [per_command_timeout_sec] [--profile NAME]
       [--host HOST] [--port N] [--one-shot]

Default mode spawns the resident client and feeds it the commands.
--one-shot instead runs duo_client.py --run ... once (connect, run all
commands, disconnect), which survives client restarts and needs no
resident process to babysit.
"""
import argparse
import json
import os
import select
import subprocess
import sys
import time

CLIENT_DIR = "/home/hatch/workspace/duo-remote/client"


def target_args(a) -> list[str]:
    out: list[str] = []
    if a.profile:
        out += ["--profile", a.profile]
    if a.host:
        out += ["--host", a.host]
    if a.port:
        out += ["--port", str(a.port)]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="duo_client.py のドライバ")
    parser.add_argument("cmds")
    parser.add_argument("timeout", nargs="?", type=float, default=40.0)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--one-shot", action="store_true")
    a = parser.parse_args()

    cmds = []
    with open(a.cmds, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cmds.append(line)

    if a.one_shot:
        run_args: list[str] = []
        for c in cmds:
            run_args += ["--run", c]
        proc = subprocess.run(
            [sys.executable, "duo_client.py", "--tailscale", *target_args(a), *run_args],
            cwd=CLIENT_DIR, text=True, capture_output=True, timeout=a.timeout * max(1, len(cmds)) + 90,
        )
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            kind = "ready" if i == 0 else "reply"
            print(json.dumps({"driver": kind, "line": ln}, ensure_ascii=False), flush=True)
        if proc.stderr.strip():
            print(json.dumps({"driver": "stderr", "line": proc.stderr.strip()[:500]}, ensure_ascii=False), flush=True)
        return proc.returncode

    # バイナリで読み、自分でバッファする。TextIOWrapper + select は、ラッパの
    # 内側バッファに行が残ったまま select が待ち続けて、ready が永遠に来ない
    # ことがある（実際に空振りした）ので、os.read で生に読む。
    proc = subprocess.Popen(
        [sys.executable, "duo_client.py", "--tailscale", *target_args(a)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        cwd=CLIENT_DIR,
    )
    buf = b""

    def read_line(deadline: float):
        nonlocal buf
        while b"\n" not in buf:
            remain = deadline - time.monotonic()
            if remain <= 0:
                return None
            r, _, _ = select.select([proc.stdout], [], [], remain)
            if not r:
                return None
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                return None
            buf += chunk
        line, buf = buf.split(b"\n", 1)
        return line.decode("utf-8", errors="replace")

    try:
        ready = read_line(time.monotonic() + 75)
        print(json.dumps({"driver": "ready", "line": ready}, ensure_ascii=False), flush=True)
        if ready is None:
            return 2
        for c in cmds:
            proc.stdin.write((c + "\n").encode())
            proc.stdin.flush()
            reply = read_line(time.monotonic() + a.timeout)
            print(json.dumps({"driver": "reply", "cmd": json.loads(c).get("cmd"),
                              "line": reply}, ensure_ascii=False), flush=True)
            if reply is None:
                return 3
        proc.stdin.write((json.dumps({"cmd": "quit"}) + "\n").encode())
        proc.stdin.flush()
        time.sleep(0.5)
        return 0
    finally:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
