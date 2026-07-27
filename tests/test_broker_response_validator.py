from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
VALIDATOR = ROOT / "tests" / "native" / "validate_broker_response.py"
MISSING = object()


def run_validator(
    tmp_path: Path, response: str, payload: str, *, pid_path: Path | None = None
) -> subprocess.CompletedProcess[str]:
    response_path = tmp_path / "response.json"
    payload_path = tmp_path / "payload.txt"
    response_path.write_text(response, encoding="utf-8")
    payload_path.write_text(payload, encoding="utf-8")
    command = [
        sys.executable,
        str(VALIDATOR),
        "--response",
        str(response_path),
        "--payload",
        str(payload_path),
        "--request-id",
        "7",
    ]
    if pid_path is not None:
        command.extend(("--pid-file", str(pid_path)))
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


def valid_response(payload: str) -> dict[str, Any]:
    return {
        "args": ["app-server", "--stdio"],
        "request": {"id": 7, "method": "account/rateLimits/read", "payload": payload},
        "bundle_in_path": False,
        "preserved_path_present": True,
        "broker_target_matches_expected": True,
        "pid": 123,
    }


def assert_safe_diagnostic(
    completed: subprocess.CompletedProcess[str],
    *,
    code: int,
    reason: str,
    response: str,
    payload: str,
    marker: str,
) -> None:
    assert completed.returncode == code
    assert completed.stdout == ""
    assert marker not in completed.stdout
    assert marker not in completed.stderr
    assert completed.stderr == (
        f"AACC_BROKER_VALIDATOR code={code} reason={reason} pos=none "
        f"response_len={len(response.encode())} "
        f"response_sha256={hashlib.sha256(response.encode()).hexdigest()} "
        f"payload_len={len(payload.encode())} "
        f"payload_sha256={hashlib.sha256(payload.encode()).hexdigest()}\n"
    )


def test_validator_preserves_a_70k_payload(tmp_path: Path) -> None:
    payload = "x" * 70_000
    response = json.dumps(valid_response(payload))

    pid_path = tmp_path / "child.pid"
    completed = run_validator(tmp_path, response, payload, pid_path=pid_path)

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert pid_path.read_text(encoding="ascii") == "123"


@pytest.mark.parametrize(
    ("response", "position", "byte", "previous_byte"),
    [("{invalid", 1, "105", "123"), ("{", 1, "none", "123")],
)
def test_validator_reports_json_position_without_echoing_a_secret(
    tmp_path: Path, response: str, position: int, byte: str, previous_byte: str
) -> None:
    marker = "AACC_SECRET_MARKER_4ce1"
    payload = marker * 10

    completed = run_validator(tmp_path, response, payload)

    assert completed.returncode == 3
    assert completed.stdout == ""
    assert marker not in completed.stdout
    assert marker not in completed.stderr
    assert completed.stderr == (
        "AACC_BROKER_VALIDATOR code=3 reason=response-json "
        f"pos={position} byte={byte} prev={previous_byte} "
        f"response_len={len(response.encode())} "
        f"response_sha256={hashlib.sha256(response.encode()).hexdigest()} "
        f"payload_len={len(payload.encode())} "
        f"payload_sha256={hashlib.sha256(payload.encode()).hexdigest()}\n"
    )


def test_validator_reports_an_embedded_utf8_bom_as_a_json_error(tmp_path: Path) -> None:
    marker = "AACC_SECRET_MARKER_4ce1"
    payload = marker * 10
    response = '{"request":\ufeff{"id":7}}'

    completed = run_validator(tmp_path, response, payload)

    assert completed.returncode == 3
    assert "reason=response-json" in completed.stderr
    assert "byte=239" in completed.stderr
    assert marker not in completed.stdout
    assert marker not in completed.stderr


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("args", ["app-server"], "args"),
        ("bundle_in_path", True, "bundle-in-path"),
        ("preserved_path_present", False, "preserved-path"),
        ("broker_target_matches_expected", False, "broker-target"),
        ("pid", True, "pid-type"),
        ("pid", -1, "pid-range"),
        ("pid", 0, "pid-range"),
        ("request", [], "request-type"),
        ("request.id", True, "request-id-type"),
        ("request.id", 7.0, "request-id-type"),
        ("request.id", MISSING, "request-id-type"),
        ("request.id", 8, "request-id"),
        ("request.method", "wrong", "request-method"),
        ("request.payload", "wrong", "request-payload"),
    ],
)
def test_validator_rejects_each_schema_field_without_secret_or_pid_file(
    tmp_path: Path, field: str, value: Any, reason: str
) -> None:
    marker = "AACC_SECRET_MARKER_4ce1"
    payload = marker * 10
    response_data = valid_response(payload)
    if "." in field:
        group, key = field.split(".", maxsplit=1)
        if value is MISSING:
            del response_data[group][key]
        else:
            response_data[group][key] = value
    else:
        response_data[field] = value
    response = json.dumps(deepcopy(response_data))
    pid_path = tmp_path / "child.pid"

    completed = run_validator(tmp_path, response, payload, pid_path=pid_path)

    assert_safe_diagnostic(
        completed,
        code=4,
        reason=reason,
        response=response,
        payload=payload,
        marker=marker,
    )
    assert not pid_path.exists()
