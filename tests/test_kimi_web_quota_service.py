from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from aacc.kimi_web_quota_service import WEB_QUOTA_INTERVAL_MS, KimiWebQuotaService


class FakeSession(QObject):
    login_state_changed = Signal(bool)
    quota_received = Signal(object, object)
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.refreshes = 0
        self.logins = 0
        self.logouts = 0
        self.closed = 0

    def refresh(self) -> None:
        self.refreshes += 1

    def open_login(self, parent=None) -> None:
        del parent
        self.logins += 1

    def logout(self) -> None:
        self.logouts += 1

    def close(self) -> None:
        self.closed += 1


def test_web_quota_service_starts_one_five_minute_timer(qapp, tmp_path: Path):
    session = FakeSession()
    refresh_order: list[str] = []
    original_refresh = session.refresh

    def track_web_refresh() -> None:
        refresh_order.append("web")
        original_refresh()

    session.refresh = track_web_refresh  # type: ignore[method-assign]
    service = KimiWebQuotaService(tmp_path, session=session)
    fallback_refreshes: list[bool] = []

    def refresh_fallback() -> None:
        refresh_order.append("code")
        fallback_refreshes.append(True)

    service.set_fallback_refresh(refresh_fallback)

    service.start()

    assert WEB_QUOTA_INTERVAL_MS == 300_000
    assert service.timer.interval() == 300_000
    assert service.timer.isActive()
    assert session.refreshes == 1
    assert fallback_refreshes == [True]
    assert refresh_order == ["code", "web"]

    service.timer.timeout.emit()

    assert session.refreshes == 2
    assert fallback_refreshes == [True, True]
    assert refresh_order == ["code", "web", "code", "web"]
    service.stop()


def test_web_quota_service_parses_snapshot_and_preserves_it_on_error(qapp, tmp_path: Path):
    session = FakeSession()
    service = KimiWebQuotaService(
        tmp_path,
        session=session,
        now=lambda: datetime(2026, 7, 27, 5, 0, tzinfo=UTC),
    )
    updates = []
    errors = []
    service.quota_updated.connect(updates.append)
    service.error_occurred.connect(errors.append)

    session.quota_received.emit(
        {
            "subscriptionBalance": {"amountUsedRatio": 0.31},
            "ratelimitCode5h": 0,
            "ratelimitCode7d": 0.72,
        },
        {},
    )
    session.error_occurred.emit("temporary")

    assert len(updates) == 1
    assert updates[0].monthly.percentage == 31
    assert service.last_quota is updates[0]
    assert errors == ["temporary"]


def test_web_quota_service_delegates_login_logout_and_close(qapp, tmp_path: Path):
    session = FakeSession()
    service = KimiWebQuotaService(tmp_path, session=session)

    service.open_login()
    service.logout()
    service.stop()

    assert session.logins == 1
    assert session.logouts == 1
    assert session.closed == 1
