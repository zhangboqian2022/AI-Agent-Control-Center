"""Qt-facing Qwen quota session backed by an AACC-owned Chrome profile."""

from __future__ import annotations

import logging
import time
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
    QwenChromeLoginCancelledError,
    QwenChromeQuotaError,
    QwenChromeUnauthorizedError,
    cancel_pending_qwen_chrome_launches,
    clear_owned_qwen_chrome_profile,
    daily_chrome_session_source,
    qwen_chrome_profile_path,
    recopy_qwen_daily_chrome_session,
    terminate_qwen_chrome_profile_processes,
)
from aacc.qwen_web_error import QwenQuotaErrorCategory

_logger = logging.getLogger("aacc.qwen_chrome_session")

QWEN_AUTO_LOGIN_MIN_INTERVAL_SECONDS = 1800.0


class _OperationLike(Protocol):
    def run(self, *, visible: bool, cancel: Event) -> dict[str, object]: ...


class _ThreadLike(Protocol):
    def start(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


def _make_thread(target: Callable[[], None]) -> _ThreadLike:
    return Thread(target=target, name="aacc-qwen-chrome", daemon=True)


def _default_visible_window_bounds() -> tuple[int, int, int, int] | None:
    """Center a 1100x700 login window on the primary screen, Qt-side.

    Computed here (Qt thread) instead of inside the CDP module so the
    operation stays platform-neutral and unit-testable.
    """

    try:
        from PySide6.QtGui import QGuiApplication

        from aacc.qwen_chrome_cdp import centered_window_bounds

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        geometry = screen.availableGeometry()
        return centered_window_bounds(geometry.width(), geometry.height())
    except Exception:
        return None


class QwenChromeSession(QObject):
    """Run Qwen Chrome work off the Qt thread and expose a stable session API."""

    login_state_changed = Signal(bool)
    quota_received = Signal(object)
    error_occurred = Signal(str)
    sync_started = Signal()
    login_window_opening = Signal()
    auto_login_requested = Signal()
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
        orphan_cleaner: Callable[[Path], None] = terminate_qwen_chrome_profile_processes,
        auto_session_recopy: bool = False,
        daily_source_probe: Callable[[], Path | None] = daily_chrome_session_source,
        visible_bounds_provider: Callable[[], tuple[int, int, int, int] | None] = (
            _default_visible_window_bounds
        ),
        success_clock: Callable[[], int] = lambda: int(time.time()),
        auto_login_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        del language_manager
        self.config_dir = config_dir
        self.profile = qwen_chrome_profile_path(config_dir)
        self.auto_session_recopy = auto_session_recopy
        self._daily_source_probe = daily_source_probe
        self._visible_bounds_provider = visible_bounds_provider
        self._success_clock = success_clock
        self._auto_login_clock = auto_login_clock
        self._last_auto_login_at: float | None = None
        self._auto_login_in_flight = False
        self._auto_login_disabled = False
        self._login_phase: str | None = None
        self._active_operation: object | None = None
        self.login_state = login_state or KimiWebLoginStateStore(
            config_dir,
            state_file_name="qwen-web-session-state.json",
        )
        self.workspace_url = ""
        self._operation = operation
        self._thread_factory = thread_factory
        self._profile_cleaner = profile_cleaner
        self._orphan_cleaner = orphan_cleaner
        self._orphan_cleanup_done = False
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
        if self.auto_session_recopy and self._daily_source_probe() is not None:
            # Sync-first: fetch the quota hidden, reusing whatever is already in
            # the owned profile. The origin-aware recovery inside the operation
            # recopies the daily session only when that cache really is dead, so
            # a possibly healthy manual-login cache is never overwritten blind.
            # Only a terminal failure opens the visible login.
            self._login_phase = "sync"
            self.sync_started.emit()
            _logger.info("Qwen login attempting silent daily-session sync first")
            self._start(visible=False)
            return
        self._login_phase = "visible"
        self._start(visible=True)

    def refresh(self) -> None:
        if self._closed or self._busy or not self.workspace_url:
            _logger.debug("Qwen Chrome refresh skipped (closed/busy/unconfigured)")
            return
        if self.login_state.may_reuse() or (
            self.auto_session_recopy and not self.login_state.logged_out_by_user()
        ):
            # With recopy enabled a logged-out refresh still launches: the
            # operation rebuilds the profile from the daily Chrome session
            # and retries, so the bar self-heals once that session is live
            # again. An explicit user logout is never auto-recovered.
            self._start(visible=False)
            return
        # No operation launches on this path, so the per-launch cleanup
        # inside the operation never runs; reap orphaned Chrome instances
        # (left behind by a killed or crashed AACC) here instead.
        self._cleanup_orphaned_processes()
        _logger.debug("Qwen Chrome refresh skipped (logged out)")

    def _cleanup_orphaned_processes(self) -> None:
        """Reap leftover owned-profile Chrome instances once, off the Qt thread.

        A killed or crashed AACC strands the windowless hidden-refresh Chrome,
        which never exits by itself and pins a Dock icon. The operation's
        pre-launch cleanup only runs when an operation launches, so a session
        whose refreshes stay skipped (logged out, recopy off) still needs one
        cleanup pass. The psutil scan plus terminate/wait can block for
        seconds, so it runs in a worker thread; the worker aborts when an
        operation has become active in the meantime, since terminating by
        profile path would kill that operation's just-launched Chrome.
        """

        if self._closed or self._orphan_cleanup_done:
            return
        self._orphan_cleanup_done = True

        def run() -> None:
            # Re-check right before the psutil scan: a login/refresh started
            # after this cleanup was scheduled launches a new Chrome on the
            # same --user-data-dir, and the scan would terminate that live
            # instance. The unsynchronized read still races, but it shrinks
            # the window from seconds (scan + terminate) to instructions.
            if self._busy or self._thread is not None:
                _logger.debug("Qwen Chrome orphan cleanup skipped (operation active)")
                return
            try:
                self._orphan_cleaner(self.profile)
            except Exception:
                _logger.warning("Qwen Chrome orphan cleanup failed", exc_info=True)

        self._thread_factory(run).start()

    def logout(self) -> bool:
        # Drop any in-flight login phase so a worker that is cancelled (or a
        # success that still arrives) can never persist a sync/visible origin
        # for a session the user has just ended.
        self._login_phase = None
        self._auto_login_in_flight = False
        succeeded = self._persist_reuse(False, logged_out_by_user=True)
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
        # LaunchServices completion is asynchronous and cannot be cancelled
        # by AppKit. Drain this session's pending request before the final
        # profile scan so a late callback cannot create a new orphan Chrome
        # after AACC has already closed.
        try:
            cancel_pending_qwen_chrome_launches(self.profile)
        except Exception:
            _logger.warning("Qwen Chrome pending-launch cleanup failed", exc_info=True)
        # A page evaluation can outlive the bounded worker join during app
        # shutdown. Reap only this session's exact profile synchronously so
        # the Qt process cannot exit while a hidden Chrome keeps running.
        try:
            self._orphan_cleaner(self.profile)
        except Exception:
            _logger.warning("Qwen Chrome close cleanup failed", exc_info=True)

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
                    session_origin=self.login_state.session_origin(),
                    visible_window_bounds=(self._visible_bounds_provider() if visible else None),
                )
            except Exception:
                self._busy = False
                self._cancel = None
                self._login_phase = None
                self.error_occurred.emit(QwenQuotaErrorCategory.REFRESH_FAILED.value)
                return
        mode = "login" if visible else "refresh"
        _logger.info("Qwen Chrome operation started mode=%s", mode)

        def run() -> None:
            outcome: object
            try:
                outcome = operation.run(visible=visible, cancel=cancel)
            except QwenChromeLoginCancelledError:
                outcome = QwenChromeLoginCancelledError()
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
        self._active_operation = operation
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
            self._persist_success_origin()
            self._login_phase = None
            self._auto_login_in_flight = False
            self.login_state_changed.emit(True)
            self.quota_received.emit(outcome)
            return
        if self._login_phase == "sync" and not isinstance(
            outcome, (QwenChromeCancelledError, QwenChromeLoginCancelledError)
        ):
            # The silent sync attempt failed (dead daily session, missing
            # source files, or a failed hidden fetch): open the visible login.
            _logger.info("Qwen silent sync failed; falling back to the visible login window")
            self.login_window_opening.emit()
            self._login_phase = "visible"
            self._start(visible=True)
            return
        dismissal = self._auto_login_in_flight and isinstance(
            outcome, QwenChromeLoginCancelledError
        )
        self._login_phase = None
        self._auto_login_in_flight = False
        if isinstance(outcome, QwenChromeUnauthorizedError):
            _logger.warning(
                "Qwen Chrome session is logged out; quota refresh paused until re-login"
            )
            self._persist_reuse(False, logged_out_by_user=False)
            self.login_state_changed.emit(False)
            self._maybe_request_auto_login()
            return
        if isinstance(outcome, QwenChromeLoginCancelledError):
            _logger.info("Qwen Chrome login window closed by the user; login abandoned")
            if dismissal:
                # The popup itself was dismissed: stop stealing focus for the
                # rest of this run, the user is back in control of re-logging in.
                self._auto_login_disabled = True
                _logger.warning("Qwen auto-login disabled after the user dismissed the popup")
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

    def _maybe_request_auto_login(self) -> None:
        """Ask the GUI to open the login automatically, rate-limited.

        Recovery is exhausted when this fires: the refresh hit the login
        banner past every recheck and recopy chance. An explicit user logout
        must never resurrect a popup, and neither must a popup the user has
        already dismissed once during this run.
        """

        if self._auto_login_disabled:
            _logger.debug("Qwen auto-login suppressed (dismissed by the user this run)")
            return
        if self.login_state.logged_out_by_user():
            return
        now = self._auto_login_clock()
        if (
            self._last_auto_login_at is not None
            and now - self._last_auto_login_at < QWEN_AUTO_LOGIN_MIN_INTERVAL_SECONDS
        ):
            _logger.debug("Qwen auto-login popup suppressed by the rate limit")
            return
        self._last_auto_login_at = now
        self._auto_login_in_flight = True
        _logger.warning("Qwen quota session expired; requesting an automatic login window")
        self.auto_login_requested.emit()

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

    def _persist_success_origin(self) -> None:
        if self._login_phase == "visible":
            origin = KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN
        else:
            # Hidden work (periodic refresh or the sync-first login attempt):
            # the origin only changes when the operation actually recopied the
            # daily session. A plain success means the pre-existing cache is
            # still live, so its provenance — and its protected recheck grace —
            # must be kept rather than relabeled as a daily-session copy.
            recopy_performed = bool(getattr(self._active_operation, "recopy_performed", False))
            origin = (
                KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY
                if recopy_performed
                else self.login_state.session_origin()
            )
        if not self._persist_reuse(
            True,
            logged_out_by_user=False,
            session_origin=origin,
            last_success_epoch=self._success_clock(),
        ):
            self.error_occurred.emit("state_save_failed")

    def _persist_reuse(
        self,
        value: bool,
        *,
        logged_out_by_user: bool | None = None,
        session_origin: str | None = None,
        last_success_epoch: int | None = None,
    ) -> bool:
        try:
            self.login_state.set_may_reuse(
                value,
                logged_out_by_user=logged_out_by_user,
                session_origin=session_origin,
                last_success_epoch=last_success_epoch,
            )
        except Exception:
            _logger.error("Qwen Chrome session state update failed")
            return False
        return True
