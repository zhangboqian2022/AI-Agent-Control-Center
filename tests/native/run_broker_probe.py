from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from validate_broker_response import emit_failure, validate_response

PopenFactory = Callable[..., Any]


def emit_probe_failure(code: int, reason: str) -> int:
    print(f"AACC_BROKER_PROBE code={code} reason={reason}", file=sys.stderr)
    return code


def main(
    argv: Sequence[str] | None = None,
    *,
    popen_factory: PopenFactory = subprocess.Popen,
) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--broker", required=True)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--request-id", required=True, type=int)
    parser.add_argument("--expected-exit-code", required=True, type=int)
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args(argv)

    payload_bytes: bytes | None = None
    try:
        payload_bytes = Path(args.payload).read_bytes()
        payload = payload_bytes.decode("utf-8")
    except OSError:
        return emit_failure(2, "payload-read", None, payload_bytes)
    except UnicodeDecodeError:
        return emit_failure(2, "payload-encoding", None, payload_bytes)

    request_bytes = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": args.request_id,
                "method": "account/rateLimits/read",
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    command = [
        args.broker,
        "--protocol",
        "1",
        "--parent-pid",
        str(os.getpid()),
        "--bundle-dir",
        args.bundle_dir,
        "--codex",
        args.codex,
    ]
    environment = os.environ.copy()
    environment["AACC_BROKER_CODEX_TARGET"] = r"C:\malicious inherited target\not-codex.cmd"
    environment["AACC_UNSET"] = "SHOULD_NOT_EXPAND"
    environment["AACC_TEST_EXPECTED_CODEX_TARGET"] = args.codex

    try:
        process = popen_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=environment,
        )
    except OSError:
        return emit_probe_failure(5, "launch")

    try:
        response_bytes, error_bytes = process.communicate(
            input=request_bytes, timeout=args.timeout_seconds
        )
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        try:
            process.communicate(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            return emit_probe_failure(5, "timeout-reap")
        return emit_probe_failure(5, "timeout")
    except OSError:
        return emit_probe_failure(5, "communicate")

    if process.returncode != args.expected_exit_code:
        return emit_probe_failure(6, "target-exit")
    if error_bytes:
        return emit_probe_failure(6, "target-stderr")

    exit_code, child_pid = validate_response(response_bytes, payload_bytes, args.request_id)
    if exit_code != 0:
        return exit_code
    assert child_pid is not None
    try:
        Path(args.pid_file).write_text(str(child_pid), encoding="ascii")
    except OSError:
        return emit_failure(2, "pid-write", response_bytes, payload_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
