"""Qt-facing Qwen quota session backed by an AACC-owned Chrome profile."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from aacc.kimi_edge_cdp import EDGE_SHUTDOWN_TIMEOUT_SECONDS
from aacc.kimi_web_login_state import KimiWebLoginStateStore
from aacc.qwen_chrome_cdp import (
    ManagedQwenChromeOperation,
    QwenChromeCancelledError,
    QwenChromeQuotaError,
    QwenChromeUnauthorizedError,
    clear_owned_qwen_chrome_profile,
    qwen_chrome_profile_path,
    recopy_qwen_daily_chrome_session,
)
from aacc.qwen_web_error import QwenQuotaErrorCategory

_logger = logging.getLogger("aacc.qwen_chrome_session")


class _OperationLike(Protocol):
    def run(self, *, visible: bool, cancel: Event) -> dict[str, object]: ...


class _ThreadLike(Protocol):
    def start(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


def _make_thread(target: Callable[[], None]) -> _ThreadLike:
    return Thread(target=target, name="aacc-qwen-chrome", daemon=True)


class QwenChromeSession(QObject):
    """Run Qwen Chrome work off the Qt thread and expose a stable session API."""

    login_state_changed = Signal(bool)
    quota_received = Signal(object)
    error_occurred = Signal(str)
    _operation_finished = Signal(int, object)

    def __init__(
        self,
        config_dir: Path,
        parent: QObject | None = None,
        *,
        language_manager: object | None = None,
        operation: _OperationLike | None = None,
        login_state: KimiWebLoginStateStore | None = None,
        thread_factory: Callable[[Callable[[], None]], _ThreadLike] = _make_thread,
        profile_cleaner: Callable[[Path, Path], None] = clear_owned_qwen_chrome_profile,
        auto_session_recopy: bool = False,
    ) -> None:
        super().__init__(parent)
        del language_manager
        self.config_dir = config_dir
        self.profile = qwen_chrome_profile_path(config_dir)
        self.auto_session_recopy = auto_session_recopy
        self.login_state = login_state or KimiWebLoginStateStore(
            config_dir,
            state_file_name="qwen-web-session-state.json",
        )
        self.workspace_url = ""
        self._operation = operation
        self._thread_factory = thread_factory
        self._profile_cleaner = profile_cleaner
        self._thread: _ThreadLike | None = None
        self._cancel: Event | None = None
        self._generation = 0
        self._busy = False
        self._closed = False
        self._cleanup_after_worker = False
        self._operation_finished.connect(self._on_operation_finished)

    def set_workspace_url(self, url: str) -> None:
        self.workspace_url = url.strip()

    def open_login(self, parent: QWidget | None = None) -> None:
        del parent
        if self._closed or self._busy:
            if self._busy:
                self.error_occurred.emit(QwenQuotaErrorCategory.REFRESH_FAILED.value)
            return
        if not self.workspace_url:
            self.error_occurred.emit(QwenQuotaErrorCategory.REFRESH_FAILED.value)
            return
        self._start(visible=True)

    def refresh(self) -> None:
        if self._closed or self._busy or not self.workspace_url or not self.login_state.may_reuse():
            _logger.debug("Qwen Chrome refresh skipped (closed/busy/unconfigured/logged out)")
            return
        self._start(visible=False)

    def logout(self) -> bool:
        succeeded = self._persist_reuse(False)
        self.login_state_changed.emit(False)
        if not self._cancel_active(wait=True):
            self._cleanup_after_worker = True
            return succeeded
        return self._finish_logout_cleanup(succeeded)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_active(wait=True)

    def retranslate_ui(self) -> None:
        """Chrome owns the visible login UI; no Qt widget needs translation."""

    def _start(self, *, visible: bool) -> None:
        self._generation += 1
        generation = self._generation
        cancel = Event()
        self._cancel = cancel
        self._busy = True
        operation = self._operation
        if operation is None:
            try:
                operation = ManagedQwenChromeOperation(
                    self.workspace_url,
                    config_dir=self.config_dir,
                    session_recopy=(
                        recopy_qwen_daily_chrome_session if self.auto_session_recopy else None
                    ),
                )
            except Exception:
                self._busy = False
                self._cancel = None
                self.error_occurred.emit(QwenQuotaErrorCategory.REFRESH_FAILED.value)
                return
        mode = "login" if visible else "refresh"
        _logger.info("Qwen Chrome operation started mode=%s", mode)

        def run() -> None:
            outcome: object
            try:
                outcome = operation.run(visible=visible, cancel=cancel)
            except QwenChromeCancelledError:
                outcome = QwenChromeCancelledError()
            except QwenChromeUnauthorizedError:
                outcome = QwenChromeUnauthorizedError()
            except QwenChromeQuotaError as error:
                outcome = error
            except Exception:
                outcome = QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
            self._operation_finished.emit(generation, outcome)

        thread = self._thread_factory(run)
        self._thread = thread
        thread.start()

    def _on_operation_finished(self, generation: int, outcome: object) -> None:
        if self._cleanup_after_worker:
            self._cleanup_after_worker = False
            self._busy = False
            self._thread = None
            self._cancel = None
            self._finish_logout_cleanup(True)
            return
        if generation != self._generation or self._closed:
            return
        self._busy = False
        self._thread = None
        self._cancel = None
        if isinstance(outcome, dict):
            persisted = self._persist_reuse(True)
            if not persisted:
                self.error_occurred.emit("state_save_failed")
            self.login_state_changed.emit(True)
            self.quota_received.emit(outcome)
            return
        if isinstance(outcome, QwenChromeUnauthorizedError):
            _logger.warning(
                "Qwen Chrome session is logged out; quota refresh paused until re-login"
            )
            self._persist_reuse(False)
            self.login_state_changed.emit(False)
            return
        if isinstance(outcome, QwenChromeCancelledError):
            return
        category = (
            outcome.category
            if isinstance(outcome, QwenChromeQuotaError)
            else QwenQuotaErrorCategory.REFRESH_FAILED
        )
        _logger.warning("Qwen Chrome operation completed category=%s", category.value)
        self.error_occurred.emit(category.value)

    def _cancel_active(self, *, wait: bool) -> bool:
        self._generation += 1
        cancel = self._cancel
        thread = self._thread
        if cancel is not None:
            cancel.set()
        if wait and thread is not None and thread.is_alive():
            thread.join(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS + 1.0)
        if thread is not None and thread.is_alive():
            self._busy = True
            return False
        self._cancel = None
        self._thread = None
        self._busy = False
        return True

    def _finish_logout_cleanup(self, succeeded: bool) -> bool:
        try:
            self._profile_cleaner(self.profile, self.config_dir)
        except Exception:
            _logger.error("Qwen Chrome logout failed category=profile_cleanup")
            self.error_occurred.emit(QwenQuotaErrorCategory.REFRESH_FAILED.value)
            return False
        return succeeded

    def _persist_reuse(self, value: bool) -> bool:
        try:
            self.login_state.set_may_reuse(value)
        except Exception:
            _logger.error("Qwen Chrome session state update failed")
            return False
        return True
