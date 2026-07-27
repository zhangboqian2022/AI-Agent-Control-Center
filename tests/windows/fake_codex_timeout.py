from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil


def _record_identity(role: str) -> None:
    identity_path = os.environ.get("AACC_TIMEOUT_IDENTITY_FILE")
    if not identity_path:
        raise RuntimeError("identity file is required")
    process = psutil.Process()
    record = {
        "role": role,
        "pid": process.pid,
        "image_path": process.exe(),
        "creation_time": process.create_time(),
    }
    with Path(identity_path).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()


def _spawn(role: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--role", role],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def main() -> int:
    if sys.argv[1:2] == ["--role"]:
        role = sys.argv[2]
        _record_identity(role)
        if role == "child":
            _spawn("grandchild")
        while True:
            time.sleep(1)

    if sys.argv[1:] != ["app-server", "--stdio"]:
        return 90
    _record_identity("root")
    _spawn("child")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
