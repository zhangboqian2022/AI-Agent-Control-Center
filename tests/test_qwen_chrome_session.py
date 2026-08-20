from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

from aacc.kimi_web_login_state import KimiWebLoginStateStore
from aacc.qwen_chrome_cdp import (
    QwenChromeCancelledError,
    QwenChromeLoginCancelledError,
    QwenChromeQuotaError,
    QwenChromeUnauthorizedError,
)
from aacc.qwen_web_error import QwenQuotaErrorCategory

WORKSPACE_URL = (
    "https://bailian.console.aliyun.com/cn-beijing?tab=plan#/efm/subscription/token-plan/personal"
)


class ImmediateThread:
    def __init__(self, target: Callable[[], None]) -> None:
        self._target = target
        self._alive = False

    def start(self) -> None:
        self._alive = True
        try:
            self._target()
        finally:
            self._alive = False

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def is_alive(self) -> bool:
        return self._alive


class NeverStopsThread:
    def __init__(self, target: Callable[[], None]) -> None:
        del target

    def start(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def is_alive(self) -> bool:
        return True


class ManualThread:
    def __init__(self, target: Callable[[], None]) -> None:
        self._target = target
        self._alive = False

    def start(self) -> None:
        self._alive = True

    def finish(self) -> None:
        try:
            self._target()
        finally:
            self._alive = False

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def is_alive(self) -> bool:
        return self._alive


class FakeOperation:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[bool] = []

    def run(self, *, visible: bool, cancel: Event) -> dict[str, object]:
        assert not cancel.is_set()
        self.calls.append(visible)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        assert isinstance(self.outcome, dict)
        return self.outcome


def make_session(tmp_path: Path, operation: FakeOperation, **kwargs: object):
    from aacc.qwen_chrome_session import QwenChromeSession

    session = QwenChromeSession(
        tmp_path,
        operation=operation,
        login_state=KimiWebLoginStateStore(tmp_path, state_file_name="qwen-web-session-state.json"),
        thread_factory=kwargs.pop("thread_factory", ImmediateThread),
        profile_cleaner=kwargs.pop("profile_cleaner", lambda *_args: None),
        orphan_cleaner=kwargs.pop("orphan_cleaner", lambda _profile: None),
        auto_session_recopy=kwargs.pop("auto_session_recopy", False),
    )
    assert not kwargs
    session.set_workspace_url(WORKSPACE_URL)
    return session


def test_open_login_uses_visible_chrome_and_persists_permission(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n0.04%", "weeklyText": "7 天\n65%"})
    session = make_session(tmp_path, operation)
    states: list[bool] = []
    quotas: list[object] = []
    session.login_state_changed.connect(states.append)
    session.quota_received.connect(quotas.append)

    session.open_login()

    assert operation.calls == [True]
    assert KimiWebLoginStateStore(
        tmp_path, state_file_name="qwen-web-session-state.json"
    ).may_reuse()
    assert states == [True]
    assert quotas == [{"fiveHourText": "5 小时\n0.04%", "weeklyText": "7 天\n65%"}]


def test_refresh_requires_saved_permission_and_runs_headless(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})
    session = make_session(tmp_path, operation)

    session.refresh()
    assert operation.calls == []

    session.login_state.set_may_reuse(True)
    session.refresh()
    assert operation.calls == [False]


def test_unauthorized_refresh_revokes_permission(qapp, tmp_path):
    del qapp
    operation = FakeOperation(QwenChromeUnauthorizedError())
    session = make_session(tmp_path, operation)
    session.login_state.set_may_reuse(True)
    states: list[bool] = []
    session.login_state_changed.connect(states.append)

    session.refresh()

    assert session.login_state.may_reuse() is False
    assert states == [False]


def test_unauthorized_refresh_logs_visible_warning(qapp, tmp_path, caplog):
    import logging

    del qapp
    operation = FakeOperation(QwenChromeUnauthorizedError())
    session = make_session(tmp_path, operation)
    session.login_state.set_may_reuse(True)

    with caplog.at_level(logging.WARNING, logger="aacc.qwen_chrome_session"):
        session.refresh()

    assert any("logged out" in record.message for record in caplog.records)


def test_transient_refresh_error_preserves_permission_and_emits_category(qapp, tmp_path):
    del qapp
    operation = FakeOperation(QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_TIMEOUT))
    session = make_session(tmp_path, operation)
    session.login_state.set_may_reuse(True)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)

    session.refresh()

    assert session.login_state.may_reuse() is True
    assert errors == [QwenQuotaErrorCategory.REFRESH_TIMEOUT.value]


def test_logout_revokes_permission_before_profile_cleanup(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})
    cleaned: list[tuple[Path, Path]] = []
    session = make_session(
        tmp_path,
        operation,
        profile_cleaner=lambda profile, root: cleaned.append((profile, root)),
    )
    session.login_state.set_may_reuse(True)

    assert session.logout() is True
    assert session.login_state.may_reuse() is False
    assert cleaned == [(tmp_path / "qwen-chrome-profile", tmp_path)]


def test_logout_waits_for_running_worker_before_cleanup(qapp, tmp_path):
    del qapp
    threads: list[ManualThread] = []
    cleaned: list[Path] = []

    def make_thread(target: Callable[[], None]) -> ManualThread:
        thread = ManualThread(target)
        threads.append(thread)
        return thread

    session = make_session(
        tmp_path,
        FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None}),
        thread_factory=make_thread,
        profile_cleaner=lambda profile, _root: cleaned.append(profile),
    )
    session.open_login()
    session.logout()
    assert cleaned == []

    threads[0].finish()
    assert cleaned == [tmp_path / "qwen-chrome-profile"]


def test_cancelled_worker_does_not_emit_error(qapp, tmp_path):
    del qapp
    operation = FakeOperation(QwenChromeCancelledError())
    session = make_session(tmp_path, operation)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.login_state.set_may_reuse(True)

    session.refresh()

    assert errors == []


def test_session_guards_busy_closed_and_missing_workspace(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})
    session = make_session(tmp_path, operation)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.workspace_url = ""
    session.open_login()
    assert errors == [QwenQuotaErrorCategory.REFRESH_FAILED.value]

    session.set_workspace_url(WORKSPACE_URL)
    session.close()
    session.open_login()
    assert errors == [QwenQuotaErrorCategory.REFRESH_FAILED.value]

    busy_session = make_session(
        tmp_path / "busy",
        FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None}),
        thread_factory=NeverStopsThread,
    )
    busy_errors: list[str] = []
    busy_session.error_occurred.connect(busy_errors.append)
    busy_session.open_login()
    busy_session.open_login()
    assert busy_errors == [QwenQuotaErrorCategory.REFRESH_FAILED.value]
    busy_session._cancel_active(wait=True)


def test_close_reaps_owned_chrome_profile(qapp, tmp_path):
    del qapp
    cleaned: list[Path] = []
    session = make_session(
        tmp_path,
        FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None}),
        orphan_cleaner=lambda profile: cleaned.append(profile),
    )

    session.close()

    assert cleaned == [tmp_path / "qwen-chrome-profile"]


def test_close_drains_pending_launch_before_profile_scan(qapp, tmp_path, monkeypatch):
    del qapp
    import aacc.qwen_chrome_session as module

    events: list[str] = []
    monkeypatch.setattr(
        module,
        "cancel_pending_qwen_chrome_launches",
        lambda _profile: events.append("pending-launch"),
    )
    session = make_session(
        tmp_path,
        FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None}),
        orphan_cleaner=lambda _profile: events.append("profile-scan"),
    )

    session.close()

    assert events == ["pending-launch", "profile-scan"]


def test_session_handles_operation_creation_and_state_persistence_failures(
    qapp, tmp_path, monkeypatch
):
    del qapp
    import aacc.qwen_chrome_session as module

    session = make_session(
        tmp_path, FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})
    )
    session._operation = None
    monkeypatch.setattr(
        module,
        "ManagedQwenChromeOperation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid config")),
    )
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.open_login()
    assert errors == [QwenQuotaErrorCategory.REFRESH_FAILED.value]

    class FailingState:
        def may_reuse(self) -> bool:
            return False

        def set_may_reuse(self, _value: bool) -> None:
            raise OSError("state unavailable")

    state_session = make_session(
        tmp_path / "state",
        FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None}),
    )
    state_session.login_state = FailingState()  # type: ignore[assignment]
    state_errors: list[str] = []
    state_session.error_occurred.connect(state_errors.append)
    state_session.open_login()
    assert "state_save_failed" in state_errors


def test_session_logout_reports_profile_cleanup_failure(qapp, tmp_path):
    del qapp
    session = make_session(
        tmp_path,
        FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None}),
        profile_cleaner=lambda *_args: (_ for _ in ()).throw(OSError("locked")),
    )
    errors: list[str] = []
    session.error_occurred.connect(errors.append)

    assert session.logout() is False
    assert errors == [QwenQuotaErrorCategory.REFRESH_FAILED.value]


def test_session_ignores_late_or_closed_operation_results(qapp, tmp_path):
    del qapp
    from aacc.qwen_chrome_session import QwenChromeSession

    session = make_session(
        tmp_path, FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})
    )
    quotas: list[object] = []
    session.quota_received.connect(quotas.append)
    session._on_operation_finished(999, {"fiveHourText": "5 小时\n1%", "weeklyText": None})
    assert quotas == []
    session.close()
    session._on_operation_finished(0, {"fiveHourText": "5 小时\n1%", "weeklyText": None})
    assert quotas == []
    assert isinstance(session, QwenChromeSession)


def test_auto_session_recopy_flag_wires_operation(qapp, tmp_path, monkeypatch):
    del qapp
    import aacc.qwen_chrome_session as module
    from aacc.qwen_chrome_cdp import recopy_qwen_daily_chrome_session
    from aacc.qwen_chrome_session import QwenChromeSession

    constructed: list[object] = []

    class RecorderOperation:
        def __init__(self, workspace_url, *, config_dir, session_recopy=None):
            del workspace_url, config_dir
            constructed.append(session_recopy)

        def run(self, *, visible, cancel):
            del visible, cancel
            return {}

    monkeypatch.setattr(module, "ManagedQwenChromeOperation", RecorderOperation)

    enabled = QwenChromeSession(tmp_path, thread_factory=ManualThread, auto_session_recopy=True)
    enabled.set_workspace_url(WORKSPACE_URL)
    enabled.open_login()
    assert constructed == [recopy_qwen_daily_chrome_session]

    constructed.clear()
    disabled = QwenChromeSession(tmp_path, thread_factory=ManualThread)
    disabled.set_workspace_url(WORKSPACE_URL)
    disabled.open_login()
    assert constructed == [None]


def test_refresh_recovers_from_expiry_when_auto_recopy_enabled(qapp, tmp_path):
    del qapp
    operation = FakeOperation(QwenChromeUnauthorizedError())
    session = make_session(tmp_path, operation, auto_session_recopy=True)
    assert session.login_state.may_reuse() is False
    assert session.login_state.logged_out_by_user() is False

    session.refresh()

    assert operation.calls == [False]


def test_refresh_respects_explicit_logout_with_auto_recopy(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})
    session = make_session(tmp_path, operation, auto_session_recopy=True)
    session.login_state.set_may_reuse(True)

    assert session.logout() is True
    assert session.login_state.logged_out_by_user() is True
    session.refresh()

    assert operation.calls == []


def test_unauthorized_outcome_marked_as_expiry_not_user_logout(qapp, tmp_path):
    del qapp
    operation = FakeOperation(QwenChromeUnauthorizedError())
    session = make_session(tmp_path, operation, auto_session_recopy=True)
    session.login_state.set_may_reuse(True)

    session.refresh()

    assert session.login_state.may_reuse() is False
    assert session.login_state.logged_out_by_user() is False


def test_login_window_closed_outcome_is_silent_cancel(qapp, tmp_path, caplog):
    import logging

    del qapp
    operation = FakeOperation(QwenChromeLoginCancelledError())
    session = make_session(tmp_path, operation)
    errors: list[str] = []
    states: list[bool] = []
    session.error_occurred.connect(errors.append)
    session.login_state_changed.connect(states.append)

    with caplog.at_level(logging.INFO, logger="aacc.qwen_chrome_session"):
        session.open_login()

    # A user closing the login window abandons the login: no error, no login
    # state change, and never the unauthorized outcome that triggers recopy.
    assert errors == []
    assert states == []
    assert session.login_state.may_reuse() is False
    assert session.login_state.logged_out_by_user() is False
    assert any("closed" in record.message for record in caplog.records)


def test_logged_out_refresh_cleans_orphaned_chrome_once(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})
    cleaned: list[Path] = []
    session = make_session(
        tmp_path,
        operation,
        orphan_cleaner=lambda profile: cleaned.append(profile),
    )

    # Refresh is skipped while logged out, so the per-launch cleanup inside
    # the operation never runs; the session itself must reap orphaned Chrome
    # instances left behind by a killed or crashed AACC — exactly once.
    session.refresh()
    session.refresh()

    assert cleaned == [tmp_path / "qwen-chrome-profile"]
    assert operation.calls == []


def test_orphan_cleanup_failure_does_not_break_refresh(qapp, tmp_path, caplog):
    import logging

    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})

    def broken_cleaner(_profile: Path) -> None:
        raise OSError("psutil unavailable")

    session = make_session(tmp_path, operation, orphan_cleaner=broken_cleaner)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)

    with caplog.at_level(logging.WARNING, logger="aacc.qwen_chrome_session"):
        session.refresh()
        session.refresh()

    assert errors == []
    assert any("orphan" in record.message for record in caplog.records)


def test_orphan_cleanup_aborted_when_operation_started_before_worker_runs(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n1%", "weeklyText": None})
    threads: list[ManualThread] = []

    def manual_factory(target: Callable[[], None]) -> ManualThread:
        thread = ManualThread(target)
        threads.append(thread)
        return thread

    cleaned: list[Path] = []
    session = make_session(
        tmp_path,
        operation,
        thread_factory=manual_factory,
        orphan_cleaner=lambda profile: cleaned.append(profile),
    )

    # The cleanup worker runs off-thread; a login started before its psutil
    # scan launches a new Chrome on the same --user-data-dir, and the scan
    # would terminate that live instance. The worker must re-check and abort.
    session.refresh()
    session.open_login()
    assert len(threads) == 2
    cleanup_thread, operation_thread = threads
    cleanup_thread.finish()

    assert cleaned == []
    operation_thread.finish()
