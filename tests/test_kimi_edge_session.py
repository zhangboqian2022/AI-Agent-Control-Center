from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

from aacc.i18n import EN_US, LanguageManager
from aacc.kimi_edge_cdp import (
    EdgeCancelledError,
    EdgeQuotaResult,
    EdgeSessionError,
    EdgeUnauthorizedError,
)
from aacc.kimi_web_error import KimiWebErrorCategory
from aacc.kimi_web_login_state import KimiWebLoginStateStore


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

    def run(self, *, visible: bool, cancel: Event) -> EdgeQuotaResult:
        assert not cancel.is_set()
        self.calls.append(visible)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        assert isinstance(self.outcome, EdgeQuotaResult)
        return self.outcome


class CancelAwareOperation:
    def run(self, *, visible: bool, cancel: Event) -> EdgeQuotaResult:
        del visible
        if cancel.is_set():
            raise EdgeCancelledError
        return EdgeQuotaResult({}, {})


def immediate_thread(target: Callable[[], None]) -> ImmediateThread:
    return ImmediateThread(target)


def make_session(tmp_path: Path, operation: FakeOperation):
    from aacc.kimi_edge_session import KimiEdgeSession

    login_state = KimiWebLoginStateStore(tmp_path)
    cleared: list[tuple[Path, Path]] = []
    session = KimiEdgeSession(
        tmp_path,
        login_state=login_state,
        language_manager=LanguageManager(EN_US),
        operation=operation,
        local_app_data=tmp_path / "local",
        thread_factory=immediate_thread,
        profile_cleaner=lambda profile, root: cleared.append((profile, root)),
    )
    return session, login_state, cleared


def test_open_login_uses_visible_edge_and_persists_success(qapp, tmp_path: Path) -> None:
    del qapp
    stats = {"ratelimitCode5h": 0.2}
    subscription = {"subscriptionBalance": {"amountUsedRatio": 0.3}}
    operation = FakeOperation(EdgeQuotaResult(stats, subscription))
    session, login_state, _cleared = make_session(tmp_path, operation)
    quotas: list[tuple[object, object]] = []
    login_states: list[bool] = []
    session.quota_received.connect(lambda left, right: quotas.append((left, right)))
    session.login_state_changed.connect(login_states.append)

    session.open_login()

    assert operation.calls == [True]
    assert login_state.may_reuse() is True
    assert login_states == [True]
    assert quotas == [(stats, subscription)]


def test_refresh_reuses_profile_without_visible_window(qapp, tmp_path: Path) -> None:
    del qapp
    operation = FakeOperation(EdgeQuotaResult({}, {}))
    session, login_state, _cleared = make_session(tmp_path, operation)
    login_state.set_may_reuse(True)

    session.refresh()

    assert operation.calls == [False]


def test_refresh_without_saved_permission_does_not_launch_edge(qapp, tmp_path: Path) -> None:
    del qapp
    operation = FakeOperation(EdgeQuotaResult({}, {}))
    session, _login_state, _cleared = make_session(tmp_path, operation)

    session.refresh()

    assert operation.calls == []


def test_expired_session_revokes_reuse_and_emits_signed_out(qapp, tmp_path: Path) -> None:
    del qapp
    operation = FakeOperation(EdgeUnauthorizedError())
    session, login_state, _cleared = make_session(tmp_path, operation)
    login_state.set_may_reuse(True)
    login_states: list[bool] = []
    session.login_state_changed.connect(login_states.append)

    session.refresh()

    assert login_state.may_reuse() is False
    assert login_states == [False]


def test_transient_error_keeps_reuse_and_emits_only_category(qapp, tmp_path: Path) -> None:
    del qapp
    operation = FakeOperation(EdgeSessionError(KimiWebErrorCategory.REFRESH_TIMEOUT))
    session, login_state, _cleared = make_session(tmp_path, operation)
    login_state.set_may_reuse(True)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)

    session.refresh()

    assert login_state.may_reuse() is True
    assert errors == ["refresh_timeout"]


def test_unsafe_profile_revokes_reuse_and_requires_login(qapp, tmp_path: Path) -> None:
    del qapp
    operation = FakeOperation(EdgeSessionError(KimiWebErrorCategory.PROFILE_UNSAFE))
    session, login_state, _cleared = make_session(tmp_path, operation)
    login_state.set_may_reuse(True)
    login_states: list[bool] = []
    errors: list[str] = []
    session.login_state_changed.connect(login_states.append)
    session.error_occurred.connect(errors.append)

    session.refresh()

    assert login_state.may_reuse() is False
    assert login_states == [False]
    assert errors == [KimiWebErrorCategory.PROFILE_UNSAFE.value]


def test_logout_revokes_reuse_before_owned_profile_cleanup(qapp, tmp_path: Path) -> None:
    del qapp
    operation = FakeOperation(EdgeQuotaResult({}, {}))
    session, login_state, cleared = make_session(tmp_path, operation)
    login_state.set_may_reuse(True)

    result = session.logout()

    assert result is True
    assert login_state.may_reuse() is False
    assert cleared == [
        (
            tmp_path / "local" / "AACC" / "kimi-edge-profile",
            tmp_path / "local",
        )
    ]


def test_logout_does_not_delete_profile_while_old_worker_is_alive(qapp, tmp_path: Path) -> None:
    del qapp
    from aacc.kimi_edge_session import KimiEdgeSession

    login_state = KimiWebLoginStateStore(tmp_path)
    login_state.set_may_reuse(True)
    cleaned: list[Path] = []
    errors: list[str] = []
    session = KimiEdgeSession(
        tmp_path,
        login_state=login_state,
        operation=FakeOperation(EdgeQuotaResult({}, {})),
        local_app_data=tmp_path / "local",
        thread_factory=NeverStopsThread,
        profile_cleaner=lambda profile, _root: cleaned.append(profile),
    )
    session.error_occurred.connect(errors.append)
    session.open_login()

    result = session.logout()

    assert result is True
    assert login_state.may_reuse() is False
    assert cleaned == []
    assert errors == []


def test_logout_cleans_profile_after_cancelled_worker_finishes(qapp, tmp_path: Path) -> None:
    del qapp
    from aacc.kimi_edge_session import KimiEdgeSession

    threads: list[ManualThread] = []
    cleaned: list[Path] = []

    def create_thread(target: Callable[[], None]) -> ManualThread:
        thread = ManualThread(target)
        threads.append(thread)
        return thread

    session = KimiEdgeSession(
        tmp_path,
        operation=CancelAwareOperation(),
        local_app_data=tmp_path / "local",
        thread_factory=create_thread,
        profile_cleaner=lambda profile, _root: cleaned.append(profile),
    )
    session.open_login()
    session.logout()

    assert cleaned == []
    threads[0].finish()

    assert cleaned == [tmp_path / "local" / "AACC" / "kimi-edge-profile"]
