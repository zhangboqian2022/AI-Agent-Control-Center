from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from aacc.kimi_quota import QuotaStatus
from aacc.qwen_web_quota_service import (
    QWEN_WEB_QUOTA_INTERVAL_MS,
    QwenWebQuotaService,
)


class FakeSession(QObject):
    login_state_changed = Signal(bool)
    quota_received = Signal(object)
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.refreshes = 0
        self.logins = 0
        self.logouts = 0
        self.closed = 0
        self.logout_result: bool | None = None

    def refresh(self) -> None:
        self.refreshes += 1

    def open_login(self, parent=None) -> None:
        del parent
        self.logins += 1

    def logout(self) -> bool | None:
        self.logouts += 1
        return self.logout_result

    def close(self) -> None:
        self.closed += 1

    def retranslate_ui(self) -> None:
        pass

    def set_workspace_url(self, url: str) -> None:
        del url


def _default_url() -> str:
    return (
        "https://bailian.console.aliyun.com/cn-beijing?tab=plan"
        "#/efm/subscription/token-plan/personal"
    )


def test_service_starts_five_minute_timer(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = QwenWebQuotaService(tmp_path, session=session)
    service.set_workspace_url(_default_url())
    service.start()
    assert QWEN_WEB_QUOTA_INTERVAL_MS == 300_000
    assert service.timer.interval() == 300_000
    assert service.timer.isActive()
    assert session.refreshes == 1
    service.timer.timeout.emit()
    assert session.refreshes == 2
    service.stop()


def test_service_noops_without_workspace_url(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = QwenWebQuotaService(tmp_path, session=session)
    service.start()
    assert session.refreshes == 0
    service.stop()


def test_service_parses_quota_and_preserves_it_on_error(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = QwenWebQuotaService(
        tmp_path,
        session=session,
        now=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
    )
    updates: list[object] = []
    errors: list[str] = []
    service.quota_updated.connect(updates.append)
    service.error_occurred.connect(errors.append)

    session.quota_received.emit(
        {
            "fiveHourText": "5 小时\n30%\n5 小时后重置",
            "weeklyText": "7 天\n65%\n7 天后重置",
        }
    )
    session.error_occurred.emit("refresh_timeout")

    assert len(updates) == 1
    assert updates[0].status is QuotaStatus.OK
    assert updates[0].five_hour.percentage == 30
    assert service.last_quota is updates[0]
    assert errors == ["refresh_timeout"]


def test_service_emits_parse_failed_on_unknown(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = QwenWebQuotaService(tmp_path, session=session)
    errors: list[str] = []
    service.error_occurred.connect(errors.append)
    session.quota_received.emit({"unrelated": {}})
    assert errors == ["parse_failed"]


def test_service_clears_snapshot_on_unauthorized(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = QwenWebQuotaService(tmp_path, session=session)
    service.last_quota = object()  # type: ignore[assignment]
    login_states: list[bool] = []
    service.login_state_changed.connect(login_states.append)
    session.login_state_changed.emit(False)
    assert service.last_quota is None
    assert login_states == [False]


def test_service_logout_clears_snapshot(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = QwenWebQuotaService(tmp_path, session=session)
    service.last_quota = object()  # type: ignore[assignment]
    service.logout()
    assert session.logouts == 1
    assert service.last_quota is None


def test_service_logout_creates_session_to_clean_persisted_state(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    del qapp
    import aacc.qwen_web_quota_service as module

    session = FakeSession()
    monkeypatch.setattr(module, "_create_platform_web_session", lambda *_args, **_kwargs: session)
    service = QwenWebQuotaService(tmp_path)
    assert service.logout() is True
    assert session.logouts == 1
    service.stop()


def test_service_open_login_delegates_to_session(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = QwenWebQuotaService(tmp_path, session=session)
    service.open_login()
    assert session.logins == 1


def test_service_stop_is_idempotent(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = QwenWebQuotaService(tmp_path, session=session)
    service.start()
    service.stop()
    service.stop()
    assert session.closed == 1


def test_service_creates_platform_session_on_demand(qapp, tmp_path: Path, monkeypatch) -> None:
    import aacc.qwen_web_quota_service as module

    session = FakeSession()
    session.storage_path = tmp_path / "qwen-web-session"  # type: ignore[attr-defined]
    monkeypatch.setattr(module, "_create_platform_web_session", lambda *_args, **_kwargs: session)
    service = QwenWebQuotaService(tmp_path)
    assert service._session is None
    ensured = service._ensure_session()
    assert service._session is ensured
    service.stop()


def _record_factories(monkeypatch, module, created: list[str]) -> None:
    def chrome(*_args: object, **_kwargs: object) -> FakeSession:
        created.append("chrome")
        return FakeSession()

    def native(*_args: object, **_kwargs: object) -> FakeSession:
        created.append("native")
        return FakeSession()

    monkeypatch.setattr(module, "_create_chrome_web_session", chrome)
    monkeypatch.setattr(module, "_create_native_web_session", native)


def test_platform_dispatch_prefers_chrome_on_darwin(qapp, tmp_path: Path, monkeypatch) -> None:
    del qapp
    import aacc.qwen_web_quota_service as module

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "find_qwen_chrome_executable", lambda: tmp_path / "chrome")
    created: list[str] = []
    _record_factories(monkeypatch, module, created)
    session = module._create_platform_web_session(
        tmp_path, None, language_manager=module.LanguageManager(module.ZH_CN)
    )
    assert created == ["chrome"]
    assert isinstance(session, FakeSession)


def test_platform_dispatch_falls_back_without_chrome(qapp, tmp_path: Path, monkeypatch) -> None:
    del qapp
    import aacc.qwen_web_quota_service as module

    def missing() -> object:
        raise module.QwenChromeMissingError

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "find_qwen_chrome_executable", missing)
    created: list[str] = []
    _record_factories(monkeypatch, module, created)
    module._create_platform_web_session(
        tmp_path, None, language_manager=module.LanguageManager(module.ZH_CN)
    )
    assert created == ["native"]


def test_platform_dispatch_uses_native_on_windows(qapp, tmp_path: Path, monkeypatch) -> None:
    del qapp
    import aacc.qwen_web_quota_service as module

    def boom() -> object:
        raise AssertionError("chrome must not be probed on win32")

    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "find_qwen_chrome_executable", boom)
    created: list[str] = []
    _record_factories(monkeypatch, module, created)
    module._create_platform_web_session(
        tmp_path, None, language_manager=module.LanguageManager(module.ZH_CN)
    )
    assert created == ["native"]
