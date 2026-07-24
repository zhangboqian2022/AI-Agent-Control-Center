from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from PySide6.QtWidgets import QApplication

from aacc.codex_quota import (
    CodexQuotaSnapshot,
    CodexQuotaStatus,
    CodexQuotaWindow,
)
from aacc.codex_quota_service import CodexQuotaService

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)
SNAPSHOT = CodexQuotaSnapshot(
    weekly=CodexQuotaWindow(
        used_percent=9,
        window_minutes=10080,
        resets_at=NOW + timedelta(days=2),
    ),
    observed_at=NOW,
    status=CodexQuotaStatus.OK,
    plan_type="prolite",
)


class FakeReader:
    def __init__(self, values: list[object]) -> None:
        self._values = values
        self.calls = 0

    def read_latest(self) -> CodexQuotaSnapshot:
        value = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        assert isinstance(value, CodexQuotaSnapshot)
        return value


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_refresh_before_start_emits_snapshot(qapp):
    reader = FakeReader([SNAPSHOT])
    service = CodexQuotaService(reader, interval_seconds=60)
    received: list[CodexQuotaSnapshot] = []
    service.quota_updated.connect(received.append)

    service.refresh_now()

    assert wait_for(lambda: received == [SNAPSHOT])
    assert reader.calls == 1


def test_reader_error_does_not_kill_poll_thread(qapp):
    reader = FakeReader([OSError("token=private-token-sentinel"), SNAPSHOT])
    service = CodexQuotaService(reader, interval_seconds=0.2)
    errors: list[str] = []
    received: list[CodexQuotaSnapshot] = []
    service.error_occurred.connect(errors.append)
    service.quota_updated.connect(received.append)

    service.start()
    try:
        assert wait_for(lambda: received == [SNAPSHOT])
    finally:
        service.stop()

    assert len(errors) == 1
    assert "private-token-sentinel" not in errors[0]
    assert reader.calls >= 2


def test_concurrent_refreshes_are_single_flight(qapp):
    entered = threading.Event()
    release = threading.Event()

    class BlockingReader:
        def __init__(self) -> None:
            self.calls = 0

        def read_latest(self) -> CodexQuotaSnapshot:
            self.calls += 1
            entered.set()
            assert release.wait(5)
            return SNAPSHOT

    reader = BlockingReader()
    service = CodexQuotaService(reader, interval_seconds=60)

    service.refresh_now()
    assert entered.wait(5)
    for _ in range(9):
        service.refresh_now()
    time.sleep(0.1)
    release.set()

    assert wait_for(lambda: not service._poll_lock.locked())
    assert reader.calls == 1
