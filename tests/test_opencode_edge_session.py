from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

from aacc.kimi_web_login_state import KimiWebLoginStateStore
from aacc.opencode_edge_cdp import (
    OpenCodeEdgeCancelledError,
    OpenCodeEdgeQuotaError,
    OpenCodeEdgeUnauthorizedError,
)
from aacc.opencode_web_error import OpenCodeQuotaErrorCategory

WORKSPACE_URL = "https://opencode.ai/workspace/wrk_123/go"


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
    from aacc.opencode_edge_session import OpenCodeEdgeSession

    session = OpenCodeEdgeSession(
        tmp_path,
        operation=operation,
        local_app_data=tmp_path / "local",
        login_state=KimiWebLoginStateStore(
            tmp_path, state_file_name="opencode-web-session-state.json"
        ),
        thread_factory=kwargs.pop("thread_factory", ImmediateThread),
        profile_cleaner=kwargs.pop("profile_cleaner", lambda *_args: None),
    )
    assert not kwargs
    session.set_workspace_url(WORKSPACE_URL)
    return session


def test_open_login_uses_visible_edge_and_persists_site_specific_permission(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"subscription": {"rollingUsage": {"usagePercent": 5}}})
    session = make_session(tmp_path, operation)
    states: list[bool] = []
    quotas: list[object] = []
    session.login_state_changed.connect(states.append)
    session.quota_received.connect(quotas.append)

    session.open_login()

    assert operation.calls == [True]
    assert KimiWebLoginStateStore(
        tmp_path, state_file_name="opencode-web-session-state.json"
    ).may_reuse()
    assert states == [True]
    assert quotas == [{"subscription": {"rollingUsage": {"usagePercent": 5}}}]


def test_refresh_requires_saved_permission_and_runs_headless(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"subscription": {}})
    session = make_session(tmp_path, operation)

    session.refresh()
    assert operation.calls == []

    session.login_state.set_may_reuse(True)
    session.refresh()
    assert operation.calls == [False]


def test_unauthorized_refresh_revokes_permission(qapp, tmp_path):
    del qapp
    operation = FakeOperation(OpenCodeEdgeUnauthorizedError())
    session = make_session(tmp_path, operation)
    session.login_state.set_may_reuse(True)
    states: list[bool] = []
    session.login_state_changed.connect(states.append)

    session.refresh()

    assert session.login_state.may_reuse() is False
    assert states == [False]


def test_transient_refresh_error_preserves_permission_and_emits_category(qapp, tmp_path):
    del qapp
    operation = FakeOperation(OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_TIMEOUT))
    session = make_session(tmp_path, operation)
    session.login_state.set_may_reuse(True)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)

    session.refresh()

    assert session.login_state.may_reuse() is True
    assert errors == [OpenCodeQuotaErrorCategory.REFRESH_TIMEOUT.value]


def test_logout_revokes_permission_before_profile_cleanup(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"subscription": {}})
    cleaned: list[tuple[Path, Path]] = []
    session = make_session(
        tmp_path,
        operation,
        profile_cleaner=lambda profile, root: cleaned.append((profile, root)),
    )
    session.login_state.set_may_reuse(True)

    assert session.logout() is True
    assert session.login_state.may_reuse() is False
    assert cleaned == [(tmp_path / "local" / "AACC" / "opencode-edge-profile", tmp_path / "local")]


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
        FakeOperation({"subscription": {}}),
        thread_factory=make_thread,
        profile_cleaner=lambda profile, _root: cleaned.append(profile),
    )
    session.open_login()
    session.logout()
    assert cleaned == []

    threads[0].finish()
    assert cleaned == [tmp_path / "local" / "AACC" / "opencode-edge-profile"]


def test_cancelled_worker_does_not_emit_error(qapp, tmp_path):
    del qapp
    operation = FakeOperation(OpenCodeEdgeCancelledError())
    session = make_session(tmp_path, operation)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.login_state.set_may_reuse(True)

    session.refresh()

    assert errors == []


def test_session_guards_busy_closed_and_missing_workspace(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"subscription": {}})
    session = make_session(tmp_path, operation)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.workspace_url = ""
    session.open_login()
    assert errors == [OpenCodeQuotaErrorCategory.REFRESH_FAILED.value]

    session.set_workspace_url(WORKSPACE_URL)
    session.close()
    session.open_login()
    assert errors == [OpenCodeQuotaErrorCategory.REFRESH_FAILED.value]

    busy_session = make_session(
        tmp_path / "busy",
        FakeOperation({"subscription": {}}),
        thread_factory=NeverStopsThread,
    )
    busy_errors: list[str] = []
    busy_session.error_occurred.connect(busy_errors.append)
    busy_session.open_login()
    busy_session.open_login()
    assert busy_errors == [OpenCodeQuotaErrorCategory.REFRESH_FAILED.value]
    busy_session._cancel_active(wait=True)


def test_session_handles_operation_creation_and_state_persistence_failures(
    qapp, tmp_path, monkeypatch
):
    del qapp
    import aacc.opencode_edge_session as module

    session = make_session(tmp_path, FakeOperation({"subscription": {}}))
    session._operation = None
    monkeypatch.setattr(
        module,
        "ManagedOpenCodeEdgeOperation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid config")),
    )
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.open_login()
    assert errors == [OpenCodeQuotaErrorCategory.REFRESH_FAILED.value]

    class FailingState:
        def may_reuse(self) -> bool:
            return False

        def set_may_reuse(self, _value: bool) -> None:
            raise OSError("state unavailable")

    state_session = make_session(tmp_path / "state", FakeOperation({"subscription": {}}))
    state_session.login_state = FailingState()  # type: ignore[assignment]
    state_errors: list[str] = []
    state_session.error_occurred.connect(state_errors.append)
    state_session.open_login()
    assert "state_save_failed" in state_errors


def test_session_logout_reports_profile_cleanup_failure(qapp, tmp_path):
    del qapp
    session = make_session(
        tmp_path,
        FakeOperation({"subscription": {}}),
        profile_cleaner=lambda *_args: (_ for _ in ()).throw(OSError("locked")),
    )
    errors: list[str] = []
    session.error_occurred.connect(errors.append)

    assert session.logout() is False
    assert errors == [OpenCodeQuotaErrorCategory.REFRESH_FAILED.value]


def test_session_ignores_late_or_closed_operation_results(qapp, tmp_path):
    del qapp
    from aacc.opencode_edge_session import OpenCodeEdgeSession

    session = make_session(tmp_path, FakeOperation({"subscription": {}}))
    quotas: list[object] = []
    session.quota_received.connect(quotas.append)
    session._on_operation_finished(999, {"subscription": {}})
    assert quotas == []
    session.close()
    session._on_operation_finished(0, {"subscription": {}})
    assert quotas == []
    assert isinstance(session, OpenCodeEdgeSession)
