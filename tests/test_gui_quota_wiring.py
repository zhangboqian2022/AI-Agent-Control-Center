from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from PySide6.QtCore import QObject, Signal

from aacc.quota_service import STATE_AUTHORIZED, STATE_UNAUTHORIZED, QuotaService

pytest.importorskip("pytestqt")


class FakeWebQuotaService(QObject):
    quota_updated = Signal(object)
    login_state_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.refreshes = 0
        self.logins = 0
        self.logouts = 0

    def refresh_now(self) -> None:
        self.refreshes += 1

    def open_login(self, parent=None) -> None:
        del parent
        self.logins += 1

    def logout(self) -> None:
        self.logouts += 1


def make_window(
    qtbot,
    tmp_path,
    handler=None,
    with_service=True,
    codex_quota_service=None,
    web_quota_service=None,
):
    from aacc.automation import MacAutomation
    from aacc.automation_executor import AutomationExecutor
    from aacc.config import default_config
    from aacc.gui import MainWindow
    from aacc.persistence import StateStore
    from aacc.task_manager import TaskManager

    config = default_config()
    store = StateStore(tmp_path / "state.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    automation = MacAutomation(config, accessibility_trusted=lambda: True)
    service = None
    if with_service:
        transport = httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json={"usage": {}}))
        )
        service = QuotaService(
            tmp_path / "cfg",
            version="test",
            client_factory=lambda: httpx.Client(transport=transport),
        )
    opened: list[str] = []
    window = MainWindow(
        manager,
        AutomationExecutor(automation),
        enable_tray=False,
        quota_service=service,
        kimi_web_quota_service=web_quota_service,
        codex_quota_service=codex_quota_service,
        open_url=opened.append,
    )
    qtbot.addWidget(window)
    return window, service, opened


def test_quota_bar_absent_without_service(qtbot, tmp_path):
    window, _, _ = make_window(qtbot, tmp_path, with_service=False)
    assert window.quota_bar is None


def test_quota_bar_present_and_click_triggers_refresh(qtbot, tmp_path):
    window, service, _ = make_window(qtbot, tmp_path)
    assert window.quota_bar is not None
    calls: list[bool] = []
    service.refresh_now = lambda: calls.append(True)  # type: ignore[method-assign]
    window._on_quota_bar_clicked()
    assert calls == []  # unauthorized state starts OAuth instead
    service._state = STATE_AUTHORIZED
    window._on_quota_bar_clicked()
    assert calls == [True]


def test_click_unauthorized_starts_oauth(qtbot, tmp_path):
    window, service, _ = make_window(qtbot, tmp_path)
    began: list[bool] = []
    service.begin_oauth = lambda: began.append(True)  # type: ignore[method-assign]
    assert service.state() == STATE_UNAUTHORIZED
    window._on_quota_bar_clicked()
    assert began == [True]


def test_oauth_code_ready_opens_dialog_and_url(qtbot, tmp_path):
    window, _, opened = make_window(qtbot, tmp_path)
    window._on_oauth_code_ready("ABCD-EFGH", "https://auth.kimi.com/device")
    assert opened == ["https://auth.kimi.com/device"]
    assert window._oauth_dialog is not None
    assert "ABCD-EFGH" in window._oauth_dialog.code_label.text()
    window._on_oauth_finished(True, "")
    assert window._oauth_dialog is None


def test_save_api_key_and_logout_delegate(qtbot, tmp_path):
    window, service, _ = make_window(qtbot, tmp_path)
    saved: list[str] = []
    service.set_api_key = saved.append  # type: ignore[method-assign]
    window.save_kimi_api_key(" sk-kimi-x ")
    assert saved == [" sk-kimi-x "]
    logged_out: list[bool] = []
    service.logout = lambda: logged_out.append(True)  # type: ignore[method-assign]
    window.kimi_logout()
    assert logged_out == [True]


def test_web_quota_login_and_unified_logout_delegate_to_both_services(qtbot, tmp_path):
    web = FakeWebQuotaService()
    window, code, _ = make_window(qtbot, tmp_path, web_quota_service=web)
    code_logouts: list[bool] = []
    code.logout = lambda: code_logouts.append(True)  # type: ignore[method-assign]

    window._on_quota_bar_clicked()
    window.kimi_logout()

    assert web.logins == 1
    assert web.logouts == 1
    assert code_logouts == [True]


def test_manual_web_refresh_uses_only_the_coordinated_web_cycle(qtbot, tmp_path):
    web = FakeWebQuotaService()
    window, code, _ = make_window(qtbot, tmp_path, web_quota_service=web)
    code_refreshes: list[bool] = []
    code.refresh_now = lambda: code_refreshes.append(True)  # type: ignore[method-assign]
    code._state = STATE_AUTHORIZED
    window._kimi_web_authorized = True

    window._on_quota_bar_clicked()

    assert web.refreshes == 1
    assert code_refreshes == []


def test_web_quota_controls_all_rows_and_code_only_fills_missing_week(qtbot, tmp_path):
    from aacc.kimi_quota import KimiQuota, QuotaDetail, QuotaStatus

    web_service = FakeWebQuotaService()
    window, _, _ = make_window(qtbot, tmp_path, web_quota_service=web_service)
    reset = datetime(2026, 8, 20, tzinfo=UTC)
    fetched_at = datetime.now(UTC)

    def item(percent: int) -> QuotaDetail:
        return QuotaDetail(percent, 100, 100 - percent, reset, percent)

    window._on_quota_updated(
        KimiQuota(
            five_hour=item(2),
            weekly=item(70),
            monthly=None,
            membership_level=None,
            booster=None,
            status=QuotaStatus.PARTIAL,
            fetched_at=fetched_at,
        )
    )
    web_service.quota_updated.emit(
        KimiQuota(
            five_hour=item(1),
            weekly=None,
            monthly=item(31),
            membership_level="ALLEGRO",
            booster=None,
            status=QuotaStatus.PARTIAL,
            fetched_at=fetched_at,
        )
    )

    assert window.quota_bar is not None
    assert window.quota_bar.percent_labels() == ["1%", "70%", "31%"]


def test_oauth_dialog_x_cancels_once(qtbot):
    from aacc.gui import KimiOAuthDialog

    dialog = KimiOAuthDialog()
    qtbot.addWidget(dialog)
    cancelled: list[bool] = []
    dialog.cancelled.connect(lambda: cancelled.append(True))
    dialog.show()

    dialog.close()
    dialog.close()

    assert cancelled == [True]


def test_oauth_dialog_escape_cancels_once(qtbot):
    from PySide6.QtCore import Qt

    from aacc.gui import KimiOAuthDialog

    dialog = KimiOAuthDialog()
    qtbot.addWidget(dialog)
    cancelled: list[bool] = []
    dialog.cancelled.connect(lambda: cancelled.append(True))
    dialog.show()

    qtbot.keyClick(dialog, Qt.Key.Key_Escape)

    assert cancelled == [True]


def test_oauth_dialog_success_close_does_not_cancel(qtbot):
    from aacc.gui import KimiOAuthDialog

    dialog = KimiOAuthDialog()
    qtbot.addWidget(dialog)
    cancelled: list[bool] = []
    dialog.cancelled.connect(lambda: cancelled.append(True))

    dialog.finish_and_close()

    assert cancelled == []


def test_codex_quota_bar_click_triggers_local_refresh(qtbot, tmp_path):
    from aacc.codex_quota import CodexQuotaSnapshot
    from aacc.codex_quota_service import CodexQuotaService

    class EmptyReader:
        def read_latest(self) -> CodexQuotaSnapshot:
            raise AssertionError("click test replaces refresh_now")

    codex_service = CodexQuotaService(EmptyReader())
    window, _, _ = make_window(
        qtbot,
        tmp_path,
        codex_quota_service=codex_service,
    )
    refreshed: list[bool] = []
    codex_service.refresh_now = lambda: refreshed.append(True)  # type: ignore[method-assign]

    assert window.codex_quota_bar is not None
    window.codex_quota_bar.clicked.emit()

    assert refreshed == [True]


def test_quota_rows_fit_inside_exact_420_pixel_main_window(qtbot, tmp_path):
    from aacc.codex_quota import (
        CodexQuotaSnapshot,
        CodexQuotaStatus,
        CodexQuotaWindow,
    )
    from aacc.codex_quota_service import CodexQuotaService
    from aacc.kimi_quota import KimiQuota, QuotaDetail

    reset_at = datetime(2026, 12, 31, 15, 59, tzinfo=UTC)

    class Reader:
        def read_latest(self) -> CodexQuotaSnapshot:
            return CodexQuotaSnapshot(
                weekly=CodexQuotaWindow(88, 10_080, reset_at),
                observed_at=reset_at,
                status=CodexQuotaStatus.OK,
            )

    window, _, _ = make_window(
        qtbot,
        tmp_path,
        codex_quota_service=CodexQuotaService(Reader()),
    )
    assert window.quota_bar is not None
    assert window.codex_quota_bar is not None
    detail = QuotaDetail(
        used=88,
        limit=100,
        remaining=12,
        reset_at=reset_at,
        percentage=88,
    )
    window.quota_bar.show_quota(
        KimiQuota(
            weekly=detail,
            five_hour=detail,
            monthly=detail,
            membership_level=None,
            booster=None,
        )
    )
    window.codex_quota_bar.show_quota(Reader().read_latest())
    window.resize(420, window.height())
    window.show()
    qtbot.wait(20)

    assert window.width() == 420
    for bar in (window.codex_quota_bar, window.quota_bar):
        assert bar.width() <= 396
        for percent_label, reset_label in bar.metric_label_pairs():
            percent_right = percent_label.mapTo(bar, percent_label.rect().topRight()).x()
            reset_left = reset_label.mapTo(bar, reset_label.rect().topLeft()).x()
            assert percent_right < reset_left
            assert (
                reset_label.fontMetrics().horizontalAdvance(reset_label.text())
                <= reset_label.contentsRect().width()
            )
