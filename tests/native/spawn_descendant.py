from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _depth() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--descendant-depth":
        return int(sys.argv[2])
    if sys.argv[1:] != ["app-server", "--stdio"]:
        raise ValueError("unexpected fixed broker arguments")
    return 2


def main() -> int:
    pid_file_value = os.environ.get("AACC_TEST_DESCENDANT_PID_FILE")
    if not pid_file_value:
        return 94

    pid_file = Path(pid_file_value)
    with pid_file.open("a", encoding="ascii") as stream:
        stream.write(f"{os.getpid()}\n")
        stream.flush()

    depth = _depth()
    if depth > 0:
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--descendant-depth",
                str(depth - 1),
            ],
            close_fds=True,
        )

    while True:
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
