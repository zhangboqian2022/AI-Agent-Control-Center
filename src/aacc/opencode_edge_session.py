"""Qt-facing OpenCode quota session backed by an AACC-owned Edge profile."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from aacc.kimi_edge_cdp import EDGE_SHUTDOWN_TIMEOUT_SECONDS
from aacc.kimi_web_login_state import KimiWebLoginStateStore
from aacc.opencode_edge_cdp import (
    ManagedOpenCodeEdgeOperation,
    OpenCodeEdgeCancelledError,
    OpenCodeEdgeQuotaError,
    OpenCodeEdgeUnauthorizedError,
    clear_owned_opencode_profile,
    opencode_edge_profile_path,
)
from aacc.opencode_web_error import OpenCodeQuotaErrorCategory

_logger = logging.getLogger("aacc.opencode_edge_session")


class _OperationLike(Protocol):
    def run(self, *, visible: bool, cancel: Event) -> dict[str, object]: ...


class _ThreadLike(Protocol):
    def start(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


def _make_thread(target: Callable[[], None]) -> _ThreadLike:
    return Thread(target=target, name="aacc-opencode-edge", daemon=True)


class OpenCodeEdgeSession(QObject):
    """Run OpenCode Edge work off the Qt thread and expose a stable session API."""

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
        local_app_data: Path | None = None,
        login_state: KimiWebLoginStateStore | None = None,
        thread_factory: Callable[[Callable[[], None]], _ThreadLike] = _make_thread,
        profile_cleaner: Callable[[Path, Path], None] = clear_owned_opencode_profile,
    ) -> None:
        super().__init__(parent)
        del language_manager
        raw_local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data is None:
            if not raw_local_app_data:
                raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
            local_app_data = Path(raw_local_app_data)
        self.local_app_data = local_app_data
        self.profile = opencode_edge_profile_path(local_app_data)
        self.login_state = login_state or KimiWebLoginStateStore(
            config_dir,
            state_file_name="opencode-web-session-state.json",
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
                self.error_occurred.emit(OpenCodeQuotaErrorCategory.REFRESH_FAILED.value)
            return
        if not self.workspace_url:
            self.error_occurred.emit(OpenCodeQuotaErrorCategory.REFRESH_FAILED.value)
            return
        self._start(visible=True)

    def refresh(self) -> None:
        if self._closed or self._busy or not self.workspace_url or not self.login_state.may_reuse():
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
        """Edge owns the visible login UI; no Qt widget needs translation."""

    def _start(self, *, visible: bool) -> None:
        self._generation += 1
        generation = self._generation
        cancel = Event()
        self._cancel = cancel
        self._busy = True
        operation = self._operation
        if operation is None:
            try:
                operation = ManagedOpenCodeEdgeOperation(
                    self.workspace_url,
                    local_app_data=self.local_app_data,
                )
            except Exception:
                self._busy = False
                self._cancel = None
                self.error_occurred.emit(OpenCodeQuotaErrorCategory.REFRESH_FAILED.value)
                return
        mode = "login" if visible else "refresh"
        _logger.info("OpenCode Edge operation started mode=%s", mode)

        def run() -> None:
            outcome: object
            try:
                outcome = operation.run(visible=visible, cancel=cancel)
            except OpenCodeEdgeCancelledError:
                outcome = OpenCodeEdgeCancelledError()
            except OpenCodeEdgeUnauthorizedError:
                outcome = OpenCodeEdgeUnauthorizedError()
            except OpenCodeEdgeQuotaError as error:
                outcome = error
            except Exception:
                outcome = OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
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
        if isinstance(outcome, OpenCodeEdgeUnauthorizedError):
            self._persist_reuse(False)
            self.login_state_changed.emit(False)
            return
        if isinstance(outcome, OpenCodeEdgeCancelledError):
            return
        category = (
            outcome.category
            if isinstance(outcome, OpenCodeEdgeQuotaError)
            else OpenCodeQuotaErrorCategory.REFRESH_FAILED
        )
        _logger.warning("OpenCode Edge operation completed category=%s", category.value)
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
            self._profile_cleaner(self.profile, self.local_app_data)
        except Exception:
            _logger.error("OpenCode Edge logout failed category=profile_cleanup")
            self.error_occurred.emit(OpenCodeQuotaErrorCategory.REFRESH_FAILED.value)
            return False
        return succeeded

    def _persist_reuse(self, value: bool) -> bool:
        try:
            self.login_state.set_may_reuse(value)
        except Exception:
            _logger.error("OpenCode Edge session state update failed")
            return False
        return True
