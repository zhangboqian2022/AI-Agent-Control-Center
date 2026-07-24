from __future__ import annotations

import httpx
import pytest

from aacc.quota_service import STATE_AUTHORIZED, STATE_UNAUTHORIZED, QuotaService

pytest.importorskip("pytestqt")


def make_window(qtbot, tmp_path, handler=None, with_service=True):
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
