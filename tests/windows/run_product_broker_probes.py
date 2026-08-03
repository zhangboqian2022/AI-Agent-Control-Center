from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import psutil

from aacc.codex_app_server import CodexAppServerReader
from aacc.codex_quota import CodexQuotaStatus
from aacc.windows_broker import build_broker_command

logging.basicConfig(
    level=logging.DEBUG,
    format="%(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


def _identity_alive(record: dict[str, object]) -> bool:
    try:
        process = psutil.Process(int(record["pid"]))
        return (
            os.path.normcase(process.exe()) == os.path.normcase(str(record["image_path"]))
            and abs(process.create_time() - float(record["creation_time"])) < 0.01
        )
    except (KeyError, TypeError, ValueError, psutil.Error):
        return False


def _wait_identity_gone(record: dict[str, object], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _identity_alive(record):
            return
        time.sleep(0.05)
    raise RuntimeError("owned target identity remained alive")


def _broker_identities(broker: Path) -> set[tuple[int, str, float]]:
    expected = os.path.normcase(str(broker.resolve()))
    result: set[tuple[int, str, float]] = set()
    for process in psutil.process_iter(("pid", "exe", "create_time")):
        try:
            executable = process.info["exe"]
            if executable and os.path.normcase(executable) == expected:
                result.add(
                    (
                        int(process.info["pid"]),
                        executable,
                        float(process.info["create_time"]),
                    )
                )
        except (TypeError, ValueError, psutil.Error):
            continue
    return result


def _reader(broker: Path, codex: Path, bundle: Path, timeout: float) -> CodexAppServerReader:
    return CodexAppServerReader(
        codex,
        platform="win32",
        timeout_seconds=timeout,
        command_factory=lambda target: build_broker_command(
            broker,
            target,
            parent_pid=os.getpid(),
            bundle_dir=bundle,
        ),
    )


def _read_json_lines(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--timeout-identities", type=Path, required=True)
    arguments = parser.parse_args()

    baseline = _broker_identities(arguments.broker)
    os.environ["AACC_FAKE_CODEX_PYTHON"] = sys.executable
    os.environ["AACC_FAKE_CODEX_MARKER"] = str(arguments.marker)
    os.environ.pop("AACC_FAKE_CODEX_MODE", None)
    for _index in range(20):
        arguments.marker.unlink(missing_ok=True)
        snapshot = _reader(
            arguments.broker,
            arguments.codex,
            arguments.bundle_dir,
            10.0,
        ).read_latest()
        if snapshot.status is not CodexQuotaStatus.OK:
            raise RuntimeError(
                "normal packaged broker probe failed: "
                f"status={snapshot.status} message={snapshot.message!r}"
            )
        marker = json.loads(arguments.marker.read_text(encoding="utf-8"))
        _wait_identity_gone(marker)
        if _broker_identities(arguments.broker) != baseline:
            raise RuntimeError("packaged broker baseline changed after a normal probe")

    arguments.timeout_identities.unlink(missing_ok=True)
    os.environ["AACC_FAKE_CODEX_MODE"] = "timeout"
    os.environ["AACC_TIMEOUT_IDENTITY_FILE"] = str(arguments.timeout_identities)
    timeout_snapshot = _reader(
        arguments.broker,
        arguments.codex,
        arguments.bundle_dir,
        2.0,
    ).read_latest()
    if timeout_snapshot.status is not CodexQuotaStatus.UNKNOWN:
        raise RuntimeError("timeout probe returned a dishonest quota result")

    deadline = time.monotonic() + 10.0
    identities: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        if arguments.timeout_identities.exists():
            identities = _read_json_lines(arguments.timeout_identities)
        if {item.get("role") for item in identities} == {"root", "child", "grandchild"}:
            break
        time.sleep(0.05)
    if {item.get("role") for item in identities} != {"root", "child", "grandchild"}:
        raise RuntimeError("timeout fixture did not record its complete owned tree")
    for identity in identities:
        _wait_identity_gone(identity)
    if _broker_identities(arguments.broker) != baseline:
        raise RuntimeError("packaged broker baseline changed after timeout cleanup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
