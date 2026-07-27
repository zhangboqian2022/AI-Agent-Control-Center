from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def emit_failure(
    code: int,
    reason: str,
    response_bytes: bytes | None,
    payload_bytes: bytes | None,
    *,
    position: int | None = None,
    byte: int | None = None,
    previous_byte: int | None = None,
) -> int:
    def metadata(value: bytes | None) -> tuple[str, str]:
        if value is None:
            return "unavailable", "unavailable"
        return str(len(value)), hashlib.sha256(value).hexdigest()

    response_length, response_digest = metadata(response_bytes)
    payload_length, payload_digest = metadata(payload_bytes)
    position_token = "none" if position is None else str(position)
    byte_tokens = ""
    if reason == "response-json":
        byte_token = "none" if byte is None else str(byte)
        previous_byte_token = "none" if previous_byte is None else str(previous_byte)
        byte_tokens = f" byte={byte_token} prev={previous_byte_token}"
    print(
        f"AACC_BROKER_VALIDATOR code={code} reason={reason} pos={position_token}{byte_tokens} "
        f"response_len={response_length} response_sha256={response_digest} "
        f"payload_len={payload_length} payload_sha256={payload_digest}",
        file=sys.stderr,
    )
    return code


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--response", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--request-id", required=True, type=int)
    parser.add_argument("--pid-file")
    args = parser.parse_args()

    response_bytes: bytes | None = None
    payload_bytes: bytes | None = None
    try:
        response_bytes = Path(args.response).read_bytes()
    except OSError:
        return emit_failure(2, "response-read", response_bytes, payload_bytes)
    try:
        payload_bytes = Path(args.payload).read_bytes()
    except OSError:
        return emit_failure(2, "payload-read", response_bytes, payload_bytes)
    try:
        response = json.loads(response_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        return emit_failure(3, "response-encoding", response_bytes, payload_bytes)
    except json.JSONDecodeError as error:
        byte = response_bytes[error.pos] if error.pos < len(response_bytes) else None
        previous_byte = response_bytes[error.pos - 1] if error.pos > 0 else None
        return emit_failure(
            3,
            "response-json",
            response_bytes,
            payload_bytes,
            position=error.pos,
            byte=byte,
            previous_byte=previous_byte,
        )
    try:
        payload = payload_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return emit_failure(2, "payload-encoding", response_bytes, payload_bytes)

    if not isinstance(response, dict):
        return emit_failure(4, "response-type", response_bytes, payload_bytes)
    if response.get("args") != ["app-server", "--stdio"]:
        return emit_failure(4, "args", response_bytes, payload_bytes)
    if response.get("bundle_in_path") is not False:
        return emit_failure(4, "bundle-in-path", response_bytes, payload_bytes)
    if response.get("preserved_path_present") is not True:
        return emit_failure(4, "preserved-path", response_bytes, payload_bytes)
    if response.get("broker_target_matches_expected") is not True:
        return emit_failure(4, "broker-target", response_bytes, payload_bytes)
    if type(response.get("pid")) is not int:
        return emit_failure(4, "pid-type", response_bytes, payload_bytes)
    if response["pid"] <= 0:
        return emit_failure(4, "pid-range", response_bytes, payload_bytes)

    request = response.get("request")
    if not isinstance(request, dict):
        return emit_failure(4, "request-type", response_bytes, payload_bytes)
    if type(request.get("id")) is not int:
        return emit_failure(4, "request-id-type", response_bytes, payload_bytes)
    if request["id"] != args.request_id:
        return emit_failure(4, "request-id", response_bytes, payload_bytes)
    if request.get("method") != "account/rateLimits/read":
        return emit_failure(4, "request-method", response_bytes, payload_bytes)
    if request.get("payload") != payload:
        return emit_failure(4, "request-payload", response_bytes, payload_bytes)
    if args.pid_file:
        try:
            Path(args.pid_file).write_text(str(response["pid"]), encoding="ascii")
        except OSError:
            return emit_failure(2, "pid-write", response_bytes, payload_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
