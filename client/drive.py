# -*- coding: utf-8 -*-
"""One-shot driver for duo_client.py: spawn it, send JSON commands from a
file (one per line), print each reply as one JSON line, then quit it.
Usage: drive.py commands.jsonl [per_command_timeout_sec]
"""
import json
import select
import subprocess
import sys
import time

def main() -> int:
    cmds_path = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0
    cmds = []
    with open(cmds_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cmds.append(line)

    proc = subprocess.Popen(
        [sys.executable, "duo_client.py", "--tailscale"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1, cwd="/home/hatch/workspace/duo-remote/client",
    )
    buf = ""

    def read_line(deadline: float):
        nonlocal buf
        while "\n" not in buf:
            remain = deadline - time.monotonic()
            if remain <= 0:
                return None
            r, _, _ = select.select([proc.stdout], [], [], remain)
            if not r:
                return None
            chunk = proc.stdout.read(1)
            if chunk == "":
                return None
            buf += chunk
        line, buf = buf.split("\n", 1)
        return line

    try:
        ready = read_line(time.monotonic() + 75)
        print(json.dumps({"driver": "ready", "line": ready}, ensure_ascii=False), flush=True)
        if ready is None:
            return 2
        for c in cmds:
            proc.stdin.write(c + "\n")
            proc.stdin.flush()
            reply = read_line(time.monotonic() + timeout)
            print(json.dumps({"driver": "reply", "cmd": json.loads(c).get("cmd"),
                              "line": reply}, ensure_ascii=False), flush=True)
            if reply is None:
                return 3
        proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
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
