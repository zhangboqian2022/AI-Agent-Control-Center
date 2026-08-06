from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

from aacc.kimi_web_login_state import KimiWebLoginStateStore
from aacc.qwen_chrome_cdp import (
    QwenChromeCancelledError,
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
