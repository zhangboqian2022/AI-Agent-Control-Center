from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import psutil


def _read_message() -> dict[str, Any]:
    line = sys.stdin.buffer.readline()
    if not line:
        raise RuntimeError("unexpected end of app-server input")
    message = json.loads(line.decode("utf-8"))
    if not isinstance(message, dict):
        raise TypeError("app-server message must be an object")
    return message


def _reply(request_id: object, result: dict[str, Any]) -> None:
    response = json.dumps(
        {"id": request_id, "result": result},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stdout.buffer.write(response.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def main() -> int:
    if sys.argv[1:] != ["app-server", "--stdio"]:
        return 90

    initialize = _read_message()
    if initialize.get("method") != "initialize" or not isinstance(initialize.get("id"), int):
        return 91
    _reply(initialize["id"], {})

    initialized = _read_message()
    if initialized.get("method") != "initialized":
        return 92

    request = _read_message()
    if request.get("method") != "account/rateLimits/read":
        return 93
    marker = os.environ.get("AACC_FAKE_CODEX_MARKER")
    if not marker:
        return 94
    marker_path = Path(marker)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    process = psutil.Process()
    marker_temporary_path = marker_path.with_name(marker_path.name + ".tmp")
    marker_temporary_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "image_path": process.exe(),
                "creation_time": process.create_time(),
                "initialize": initialize["method"],
                "request": request["method"],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.replace(marker_temporary_path, marker_path)

    # The reader reaps the broker immediately after receiving this response.
    # Publish complete process evidence before making the response observable.
    _reply(
        request.get("id"),
        {
            "rateLimitsByLimitId": {
                "codex": {
                    "planType": "aacc-smoke",
                    "primary": {
                        "usedPercent": 17,
                        "windowDurationMins": 10080,
                        "resetsAt": 1785747600,
                    },
                }
            }
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
