from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
VALIDATOR = ROOT / "tests" / "native" / "validate_broker_response.py"


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


def test_validator_preserves_a_70k_payload(tmp_path: Path) -> None:
    payload = "x" * 70_000
    response = json.dumps(
        {
            "args": ["app-server", "--stdio"],
            "request": {"id": 7, "method": "account/rateLimits/read", "payload": payload},
            "bundle_in_path": False,
            "preserved_path_present": True,
            "broker_target_matches_expected": True,
            "pid": 123,
        }
    )

    pid_path = tmp_path / "child.pid"
    completed = run_validator(tmp_path, response, payload, pid_path=pid_path)

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert pid_path.read_text(encoding="ascii") == "123"


def test_validator_rejects_bad_json_without_echoing_payload(tmp_path: Path) -> None:
    payload = "secret" * 10_000

    invalid = run_validator(tmp_path, "not json", payload)
    assert invalid.returncode == 3
    assert payload not in invalid.stderr

    trailing_data = run_validator(tmp_path, "{} trailing data", payload)
    assert trailing_data.returncode == 3
    assert payload not in trailing_data.stderr

    mismatch_response = json.dumps(
        {
            "args": ["app-server", "--stdio"],
            "request": {"id": 7, "method": "account/rateLimits/read", "payload": "wrong"},
            "bundle_in_path": False,
            "preserved_path_present": True,
            "broker_target_matches_expected": True,
            "pid": 123,
        }
    )
    mismatch = run_validator(tmp_path, mismatch_response, payload)
    assert mismatch.returncode == 4
    assert payload not in mismatch.stderr
