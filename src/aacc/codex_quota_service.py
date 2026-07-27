from __future__ import annotations

import logging
import threading
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from aacc.codex_quota import CodexQuotaSnapshot
from aacc.security import redact


class CodexQuotaReaderLike(Protocol):
    def read_latest(self) -> CodexQuotaSnapshot: ...


class CodexQuotaService(QObject):
    quota_updated = Signal(object)
    error_occurred = Signal(str)

    def __init__(
        self,
        reader: CodexQuotaReaderLike,
        *,
        interval_seconds: float = 60.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._reader = reader
        self._interval = max(0.2, interval_seconds)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._poll_lock = threading.Lock()
        self._logger = logging.getLogger("aacc.codex_quota")
        self._thread = threading.Thread(
            target=self._run,
            name="aacc-codex-quota",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self._interval + 1)

    def refresh_now(self) -> None:
        self._wake.set()
        if not self._thread.is_alive():
            threading.Thread(
                target=self._poll_guarded,
                name="aacc-codex-quota-refresh",
                daemon=True,
            ).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._poll_guarded()
            self._wake.wait(self._interval)
            self._wake.clear()

    def _poll_guarded(self) -> None:
        if not self._poll_lock.acquire(blocking=False):
            return
        try:
            snapshot = self._reader.read_latest()
            self.quota_updated.emit(snapshot)
        except Exception as error:
            message = redact(str(error) or type(error).__name__)[:160]
            self._logger.warning("Codex quota read failed: %s", message)
            try:
                self.error_occurred.emit(message)
            except RuntimeError:
                return
        finally:
            self._poll_lock.release()
