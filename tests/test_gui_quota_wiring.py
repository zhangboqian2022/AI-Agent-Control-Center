from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from PySide6.QtCore import QObject, Signal

from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.kimi_web_quota_service import KimiWebQuotaService
from aacc.quota_service import STATE_AUTHORIZED, STATE_UNAUTHORIZED, QuotaService

pytest.importorskip("pytestqt")


class FakeWebQuotaService(QObject):
    quota_updated = Signal(object)
    login_state_changed = Signal(bool)
    web_error_occurred = Signal(str)
    code_error_occurred = Signal(str)

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


class PersistFailureSession(QObject):
    login_state_changed = Signal(bool)
    quota_received = Signal(object, object)
    error_occurred = Signal(str)

    def refresh(self) -> None:
        pass

    def open_login(self, parent=None) -> None:
        del parent

    def logout(self) -> bool:
        self.error_occurred.emit("password=web-session-secret")
        self.login_state_changed.emit(False)
        return False

    def close(self) -> None:
        pass


def make_window(
    qtbot,
    tmp_path,
    handler=None,
    with_service=True,
    codex_quota_service=None,
    web_quota_service=None,
    opencode_web_quota_service=None,
    language_manager=None,
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
        opencode_web_quota_service=opencode_web_quota_service,
        language_manager=language_manager,
        open_url=opened.append,
    )
    qtbot.addWidget(window)
    return window, service, opened


def test_web_error_category_retranslates_full_prompt_both_directions(qtbot, tmp_path):
    web = FakeWebQuotaService()
    language_manager = LanguageManager(ZH_CN)
    window, _, _ = make_window(
        qtbot,
        tmp_path,
        with_service=False,
        web_quota_service=web,
        language_manager=language_manager,
    )
    assert window.quota_bar is not None

    web.web_error_occurred.emit("web_load_failed")
    assert "Kimi 官网加载失败" in window.quota_bar.toolTip()

    language_manager.set_language(EN_US)
    assert "The Kimi website failed to load" in window.quota_bar.toolTip()
    assert "官网加载失败" not in window.quota_bar.toolTip()

    web.web_error_occurred.emit("web_refresh_timeout")
    assert "Kimi membership quota refresh timed out" in window.quota_bar.toolTip()

    language_manager.set_language(ZH_CN)
    assert "Kimi 会员额度刷新超时" in window.quota_bar.toolTip()
    assert "membership quota refresh timed out" not in window.quota_bar.toolTip()


def test_quota_bar_absent_without_service(qtbot, tmp_path):
    window, _, _ = make_window(qtbot, tmp_path, with_service=False)
    assert window.quota_bar is None


def test_code_and_web_quota_errors_keep_separate_safe_slots_and_retranslate(qtbot, tmp_path):
    web = FakeWebQuotaService()
    language_manager = LanguageManager(ZH_CN)
    window, code, _ = make_window(
        qtbot,
        tmp_path,
        web_quota_service=web,
        language_manager=language_manager,
    )
    assert code is not None
    assert window.quota_bar is not None

    code.error_occurred.emit("token=private-code-secret")
    web.web_error_occurred.emit("access_token=private-web-secret")

    tooltip = window.quota_bar.toolTip()
    assert "Kimi Code 额度刷新失败" in tooltip
    assert "Kimi 会员额度刷新失败" in tooltip
    assert "private-code-secret" not in tooltip
    assert "private-web-secret" not in tooltip

    language_manager.set_language(EN_US)

    tooltip = window.quota_bar.toolTip()
    assert "Kimi Code quota refresh failed" in tooltip
    assert "Kimi membership quota refresh failed" in tooltip
    assert "额度刷新失败" not in tooltip


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


def test_logout_disables_web_reuse_before_code_logout_failure(qtbot, tmp_path):
    web = FakeWebQuotaService()
    window, code, _ = make_window(qtbot, tmp_path, web_quota_service=web)
    calls: list[str] = []

    def web_logout() -> None:
        calls.append("web")

    def code_logout() -> None:
        calls.append("code")
        raise PermissionError("password=do-not-log")

    web.logout = web_logout  # type: ignore[method-assign]
    code.logout = code_logout  # type: ignore[method-assign]

    window.kimi_logout()

    assert calls == ["web", "code"]
    assert window.quota_bar is not None
    assert "do-not-log" not in window.quota_bar.toolTip()
    assert "退出登录未完全完成" in window.quota_bar.toolTip()


def test_logout_attempts_code_cleanup_when_web_logout_fails(qtbot, tmp_path):
    web = FakeWebQuotaService()
    window, code, _ = make_window(qtbot, tmp_path, web_quota_service=web)
    calls: list[str] = []

    def web_logout() -> None:
        calls.append("web")
        raise RuntimeError("access_token=do-not-log")

    def code_logout() -> None:
        calls.append("code")

    web.logout = web_logout  # type: ignore[method-assign]
    code.logout = code_logout  # type: ignore[method-assign]

    window.kimi_logout()

    assert calls == ["web", "code"]
    assert window.quota_bar is not None
    assert "do-not-log" not in window.quota_bar.toolTip()


def test_logout_reports_one_fixed_error_after_both_cleanup_paths_fail(caplog, qtbot, tmp_path):
    web = FakeWebQuotaService()
    window, code, _ = make_window(qtbot, tmp_path, web_quota_service=web)
    calls: list[str] = []

    def web_logout() -> None:
        calls.append("web")
        raise RuntimeError("Authorization: Bearer web-secret")

    def code_logout() -> None:
        calls.append("code")
        raise PermissionError("token=code-secret")

    web.logout = web_logout  # type: ignore[method-assign]
    code.logout = code_logout  # type: ignore[method-assign]
    window._latest_kimi_code_quota = object()  # type: ignore[assignment]
    window._latest_kimi_web_quota = object()  # type: ignore[assignment]
    window._kimi_web_authorized = True

    with caplog.at_level(logging.ERROR, logger="aacc.gui"):
        window.kimi_logout()

    assert calls == ["web", "code"]
    assert window._latest_kimi_code_quota is None
    assert window._latest_kimi_web_quota is None
    assert window._kimi_web_authorized is False
    assert window.quota_bar is not None
    tooltip = window.quota_bar.toolTip()
    assert tooltip.count("退出登录未完全完成") == 1
    assert "web-secret" not in tooltip
    assert "code-secret" not in tooltip
    assert "web-secret" not in caplog.text
    assert "code-secret" not in caplog.text


def test_persist_failure_signal_chain_keeps_final_logout_warning(qtbot, tmp_path):
    session = PersistFailureSession()
    web = KimiWebQuotaService(tmp_path / "web", session=session)
    window, code, _ = make_window(qtbot, tmp_path, web_quota_service=web)
    code_logouts: list[bool] = []
    code.logout = lambda: code_logouts.append(True)  # type: ignore[method-assign]
    propagated_errors: list[str] = []
    web.web_error_occurred.connect(propagated_errors.append)

    window.kimi_logout()

    assert code_logouts == [True]
    assert web.last_quota is None
    assert propagated_errors == ["web_refresh_failed"]
    assert window.quota_bar is not None
    tooltip = window.quota_bar.toolTip()
    assert "Kimi 退出登录未完全完成" in tooltip
    assert "web-session-secret" not in tooltip


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


def test_window_restore_triggers_throttled_web_quota_refresh(qtbot, tmp_path):
    web = FakeWebQuotaService()
    window, _, _ = make_window(qtbot, tmp_path, with_service=False, web_quota_service=web)

    window.show()
    assert web.refreshes == 1

    window.hide()
    window.show()
    assert web.refreshes == 1  # throttled within the restore interval

    window._last_restore_quota_refresh -= 61.0
    window.hide()
    window.show()
    assert web.refreshes == 2


def test_window_unminimize_triggers_web_quota_refresh(qtbot, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QWindowStateChangeEvent

    web = FakeWebQuotaService()
    window, _, _ = make_window(qtbot, tmp_path, with_service=False, web_quota_service=web)
    window.show()
    assert web.refreshes == 1

    window._last_restore_quota_refresh -= 61.0
    window.changeEvent(QWindowStateChangeEvent(Qt.WindowState.WindowMinimized))

    assert web.refreshes == 2


def test_window_restore_without_web_service_does_not_refresh(qtbot, tmp_path):
    window, _, _ = make_window(qtbot, tmp_path, with_service=False)

    window.show()
    window.hide()
    window.show()  # must not raise without a web quota service


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


def test_web_unauthorized_discards_web_snapshot_and_renders_code_fallback(qtbot, tmp_path):
    from aacc.kimi_quota import KimiQuota, QuotaDetail, QuotaStatus

    web_service = FakeWebQuotaService()
    window, _, _ = make_window(qtbot, tmp_path, web_quota_service=web_service)
    reset = datetime(2026, 8, 20, tzinfo=UTC)
    fetched_at = datetime.now(UTC)

    def item(percent: int) -> QuotaDetail:
        return QuotaDetail(percent, 100, 100 - percent, reset, percent)

    window._on_quota_updated(
        KimiQuota(
            five_hour=item(4),
            weekly=item(73),
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
            weekly=item(70),
            monthly=item(31),
            membership_level="ALLEGRO",
            booster=None,
            status=QuotaStatus.OK,
            fetched_at=fetched_at,
        )
    )

    web_service.login_state_changed.emit(False)

    assert window._latest_kimi_web_quota is None
    assert window.quota_bar is not None
    assert window.quota_bar.percent_labels() == ["4%", "73%", "--"]


@pytest.mark.parametrize("timestamp_kind", ["stale", "missing", "future"])
def test_web_unauthorized_prompts_login_without_verifiably_fresh_code_fallback(
    qtbot,
    tmp_path,
    timestamp_kind,
):
    from aacc.kimi_quota import KimiQuota, QuotaDetail, QuotaStatus

    web_service = FakeWebQuotaService()
    window, _, _ = make_window(qtbot, tmp_path, web_quota_service=web_service)
    now = datetime.now(UTC)
    fetched_at = {
        "stale": now - timedelta(seconds=331),
        "missing": None,
        "future": now + timedelta(seconds=30),
    }[timestamp_kind]
    detail = QuotaDetail(4, 100, 96, now + timedelta(days=1), 4)
    window._on_quota_updated(
        KimiQuota(
            five_hour=detail,
            weekly=detail,
            monthly=None,
            membership_level=None,
            booster=None,
            status=QuotaStatus.PARTIAL,
            fetched_at=fetched_at,
        )
    )
    web_service.quota_updated.emit(
        KimiQuota(
            five_hour=detail,
            weekly=detail,
            monthly=detail,
            membership_level="ALLEGRO",
            booster=None,
            status=QuotaStatus.OK,
            fetched_at=now,
        )
    )

    web_service.login_state_changed.emit(False)

    assert window._latest_kimi_web_quota is None
    assert window.quota_bar is not None
    assert window.quota_bar.summary_label.text() == "Kimi 额度\n点击授权"


def test_web_unauthorized_prompts_login_without_any_code_fallback(qtbot, tmp_path):
    from aacc.kimi_quota import KimiQuota, QuotaDetail, QuotaStatus

    web_service = FakeWebQuotaService()
    window, _, _ = make_window(qtbot, tmp_path, web_quota_service=web_service)
    now = datetime.now(UTC)
    detail = QuotaDetail(4, 100, 96, now + timedelta(days=1), 4)
    web_service.quota_updated.emit(
        KimiQuota(
            five_hour=detail,
            weekly=detail,
            monthly=detail,
            membership_level="ALLEGRO",
            booster=None,
            status=QuotaStatus.OK,
            fetched_at=now,
        )
    )

    web_service.login_state_changed.emit(False)

    assert window._latest_kimi_web_quota is None
    assert window.quota_bar is not None
    assert window.quota_bar.summary_label.text() == "Kimi 额度\n点击授权"


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


class FakeOpenCodeWebQuotaService(QObject):
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


def test_opencode_quota_bar_wired_and_signals_flow(qtbot, tmp_path):
    from aacc.opencode_web_quota import OpenCodeQuota, OpenCodeUsage

    service = FakeOpenCodeWebQuotaService()
    window, _, _ = make_window(
        qtbot,
        tmp_path,
        with_service=False,
        opencode_web_quota_service=service,
    )
    assert window.opencode_quota_bar is not None
    assert window.opencode_quota_bar.metric_row_count() == 3

    window.opencode_quota_bar.clicked.emit()
    assert service.logins == 1

    now = datetime.now(UTC)
    quota = OpenCodeQuota(
        rolling=OpenCodeUsage(10, 3600, now),
        weekly=OpenCodeUsage(20, 3600, now),
        monthly=OpenCodeUsage(30, 3600, now),
        status=__import__("aacc.kimi_quota", fromlist=["QuotaStatus"]).QuotaStatus.OK,
        fetched_at=now,
    )
    service.quota_updated.emit(quota)
    assert window.opencode_quota_bar.rolling_label.text() == "10%"

    window.opencode_quota_bar.clicked.emit()
    assert service.refreshes == 1

    service.error_occurred.emit("refresh_timeout")
    assert "点击重试" in window.opencode_quota_bar.toolTip()

    window.opencode_logout()
    assert service.logouts == 1
    assert window.opencode_quota_bar.rolling_label.text() == "--"
