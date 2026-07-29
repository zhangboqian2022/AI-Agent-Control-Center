"""Qt-facing persistent Kimi session backed by an AACC-owned Edge profile."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from aacc.i18n import ZH_CN, LanguageManager
from aacc.kimi_edge_cdp import (
    EdgeCancelledError,
    EdgeQuotaResult,
    EdgeSessionError,
    EdgeUnauthorizedError,
    ManagedEdgeOperation,
    clear_owned_profile,
    edge_profile_path,
)
from aacc.kimi_web_error import KimiWebErrorCategory
from aacc.kimi_web_login_state import KimiWebLoginStateStore

_logger = logging.getLogger("aacc.kimi_edge_session")


class _OperationLike(Protocol):
    def run(self, *, visible: bool, cancel: Event) -> EdgeQuotaResult: ...


class _ThreadLike(Protocol):
    def start(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


def _make_thread(target: Callable[[], None]) -> _ThreadLike:
    return Thread(target=target, name="aacc-kimi-edge", daemon=True)


@dataclass(frozen=True)
class _Succeeded:
    result: EdgeQuotaResult


@dataclass(frozen=True)
class _Failed:
    kind: str
    category: KimiWebErrorCategory | None = None


class KimiEdgeSession(QObject):
    """Run Edge work off the Qt thread and expose the native session protocol."""

    login_state_changed = Signal(bool)
    quota_received = Signal(object, object)
    error_occurred = Signal(str)
    _operation_finished = Signal(int, object)

    def __init__(
        self,
        config_dir: Path,
        parent: QObject | None = None,
        login_state: KimiWebLoginStateStore | None = None,
        *,
        language_manager: LanguageManager | None = None,
        operation: _OperationLike | None = None,
        local_app_data: Path | None = None,
        thread_factory: Callable[[Callable[[], None]], _ThreadLike] = _make_thread,
        profile_cleaner: Callable[[Path, Path], None] = clear_owned_profile,
    ) -> None:
        super().__init__(parent)
        raw_local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data is None:
            if not raw_local_app_data:
                raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
            local_app_data = Path(raw_local_app_data)
        self.local_app_data = local_app_data
        self.profile = edge_profile_path(local_app_data)
        self.login_state = login_state or KimiWebLoginStateStore(config_dir)
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self._operation = operation or ManagedEdgeOperation(local_app_data=local_app_data)
        self._thread_factory = thread_factory
        self._profile_cleaner = profile_cleaner
        self._thread: _ThreadLike | None = None
        self._cancel: Event | None = None
        self._generation = 0
        self._busy = False
        self._closed = False
        self._cleanup_after_worker = False
        self._operation_finished.connect(self._on_operation_finished)

    def open_login(self, parent: QWidget | None = None) -> None:
        del parent
        if self._closed or self._busy:
            if self._busy:
                self.error_occurred.emit(KimiWebErrorCategory.REFRESH_FAILED.value)
            return
        self._start(visible=True)

    def refresh(self) -> None:
        if self._closed or self._busy or not self.login_state.may_reuse():
            return
        self._start(visible=False)

    def logout(self) -> bool:
        succeeded = self._persist_reuse(False)
        self.login_state_changed.emit(False)
        if not self._cancel_active(wait=False):
            self._cleanup_after_worker = True
            return succeeded
        return self._finish_logout_cleanup(succeeded)

    def _finish_logout_cleanup(self, succeeded: bool) -> bool:
        try:
            self._profile_cleaner(self.profile, self.local_app_data)
        except Exception:
            _logger.error("Kimi Edge logout failed category=profile-cleanup")
            self.error_occurred.emit(KimiWebErrorCategory.LOGOUT_PARTIAL.value)
            succeeded = False
        return succeeded

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_active(wait=False)

    def retranslate_ui(self) -> None:
        """The browser owns the visible login UI; no Qt widget needs translation."""

    def _start(self, *, visible: bool) -> None:
        self._generation += 1
        generation = self._generation
        cancel = Event()
        self._cancel = cancel
        self._busy = True
        mode = "login" if visible else "refresh"
        _logger.info("Kimi Edge operation started mode=%s", mode)

        def run() -> None:
            outcome: _Succeeded | _Failed
            try:
                outcome = _Succeeded(self._operation.run(visible=visible, cancel=cancel))
            except EdgeCancelledError:
                outcome = _Failed("cancelled")
            except EdgeUnauthorizedError:
                outcome = _Failed("unauthorized")
            except EdgeSessionError as error:
                outcome = _Failed("error", error.category)
            except Exception:
                outcome = _Failed("error", KimiWebErrorCategory.REFRESH_FAILED)
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
        if isinstance(outcome, _Succeeded):
            persisted = self._persist_reuse(True)
            if not persisted:
                self.error_occurred.emit(KimiWebErrorCategory.STATE_SAVE_FAILED.value)
            _logger.info("Kimi Edge operation completed category=quota")
            self.login_state_changed.emit(True)
            self.quota_received.emit(outcome.result.stats, outcome.result.subscription)
            return
        if not isinstance(outcome, _Failed) or outcome.kind == "cancelled":
            return
        if outcome.kind == "unauthorized":
            self._persist_reuse(False)
            _logger.info("Kimi Edge operation completed category=unauthorized")
            self.login_state_changed.emit(False)
            return
        category = outcome.category or KimiWebErrorCategory.REFRESH_FAILED
        if category is KimiWebErrorCategory.PROFILE_UNSAFE:
            self._persist_reuse(False)
            self.login_state_changed.emit(False)
        _logger.warning("Kimi Edge operation completed category=%s", category.value)
        self.error_occurred.emit(category.value)

    def _cancel_active(self, *, wait: bool) -> bool:
        self._generation += 1
        cancel = self._cancel
        thread = self._thread
        if cancel is not None:
            cancel.set()
        if wait and thread is not None and thread.is_alive():
            thread.join(timeout=0)
        if thread is not None and thread.is_alive():
            self._busy = True
            return False
        self._cancel = None
        self._thread = None
        self._busy = False
        return True

    def _persist_reuse(self, value: bool) -> bool:
        try:
            self.login_state.set_may_reuse(value)
        except Exception:
            _logger.error("Kimi Edge session state update failed")
            return False
        return True
