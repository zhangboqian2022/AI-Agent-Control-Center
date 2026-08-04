from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtWebView import QWebViewLoadingInfo
from PySide6.QtWidgets import QWidget

from aacc.file_security import FileProtectionError
from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.qwen_web_session import (
    BRIDGE_PREFIX,
    QwenWebSession,
    qwen_dom_extract_script,
    qwen_webview_user_data_path,
)

WORKSPACE_URL = (
    "https://bailian.console.aliyun.com/cn-beijing?tab=plan#/efm/subscription/token-plan/personal"
)


class FakeWebView(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._url = QUrl()
        self.scripts: list[str] = []
        self.script_result: object = None
        self.cookies_deleted = False
        self.deleted = False
        self.settings = lambda: _FakeSettings()

    def url(self) -> QUrl:
        return self._url

    def setUrl(self, url: QUrl) -> None:
        self._url = url
        self.scripts = []

    def runJavaScript(self, script: str, callback=None) -> None:
        self.scripts.append(script)
        if callback is not None:
            callback(self.script_result)

    def deleteAllCookies(self) -> None:
        self.cookies_deleted = True

    def deleteLater(self) -> None:
        self.deleted = True


class _FakeSettings:
    class WebAttribute:
        JavaScriptEnabled = 0
        LocalStorageEnabled = 1

    def setAttribute(self, attribute: int, enabled: bool) -> None:
        pass


class FakeLoadingInfo:
    def __init__(self, status: object = QWebViewLoadingInfo.LoadStatus.Succeeded) -> None:
        self._status = status

    def status(self) -> object:
        return self._status


def _bridge_title(payload: object) -> str:
    return BRIDGE_PREFIX + json.dumps(payload)


def make_session(tmp_path: Path) -> QwenWebSession:
    session = QwenWebSession(tmp_path)
    session.view = FakeWebView()  # type: ignore[assignment]
    session.set_workspace_url(WORKSPACE_URL)
    return session


def test_dom_extract_script_contains_text_extraction() -> None:
    script = qwen_dom_extract_script(WORKSPACE_URL, 7)
    assert "document.body.innerText" in script
    assert "extract5h" in script
    assert "extract7d" in script
    assert "AACC_QWEN_QUOTA:" in script
    assert qwen_dom_extract_script("https://bailian.console.aliyun.com/other", 1) == ""


def test_user_data_path_windows_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert qwen_webview_user_data_path(tmp_path) == (
        Path(tmp_path / "local") / "AACC" / "qwen-web-session"
    )


def test_user_data_path_windows_raises_without_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(FileProtectionError):
        qwen_webview_user_data_path(tmp_path)


def test_session_refresh_runs_fetch_script(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.refresh()
    assert session.view.url().toString() == WORKSPACE_URL
    session._on_loading_changed(FakeLoadingInfo())
    assert session.view.scripts
    assert "document.body.innerText" in session.view.scripts[-1]


def test_refresh_logs_only_scheme_and_host_never_query_params(caplog, qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.view._url = QUrl("https://signin.aliyun.com/login?token=SECRET&state=X")
    with caplog.at_level(logging.INFO, logger="aacc.qwen_web_session"):
        session.refresh()
    assert "SECRET" not in caplog.text
    assert "state=" not in caplog.text
    assert "signin.aliyun.com" in caplog.text
    assert session.view.url().toString() == WORKSPACE_URL


def test_session_without_workspace_url_is_inert(qapp, tmp_path: Path) -> None:
    del qapp
    session = QwenWebSession(tmp_path)
    session.view = FakeWebView()  # type: ignore[assignment]
    session.set_workspace_url("")
    assert session._is_bailian_origin() is False
    session.refresh()
    assert session.logout() is True
    session._load_workspace_url()
    assert session.view.scripts == []
    assert session.view.url().isEmpty()


def test_refresh_runs_fetch_script_without_reload_when_origin_matches(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.view._url = QUrl(WORKSPACE_URL)
    session.refresh()
    assert session.view.scripts
    assert "document.body.innerText" in session.view.scripts[-1]
    assert session.view.url().toString() == WORKSPACE_URL


def test_open_login_without_workspace_url_shows_message_box(
    monkeypatch, qapp, tmp_path: Path
) -> None:
    del qapp
    import aacc.qwen_web_session as module

    messages: list[str] = []
    monkeypatch.setattr(
        module.QMessageBox, "information", lambda *args: messages.append(str(args[-1]))
    )
    session = QwenWebSession(tmp_path)
    session.view = FakeWebView()  # type: ignore[assignment]
    session.set_workspace_url("")
    session.open_login()
    assert messages == []
    session.open_login(QWidget())
    assert messages == ["请先在 config.yaml 中配置 qwen_workspace_url"]


def test_open_login_builds_reusable_dialog_and_closes_after_quota(
    monkeypatch, qapp, tmp_path: Path
) -> None:
    del qapp
    import aacc.qwen_web_session as module

    container = QWidget()
    monkeypatch.setattr(module.QWidget, "createWindowContainer", lambda view, parent: container)
    session = make_session(tmp_path)
    login_states: list[bool] = []
    session.login_state_changed.connect(login_states.append)
    session.open_login()
    assert session._login_dialog is not None
    assert session._login_container is container
    assert session._login_dialog_open is True
    assert session.view.url().toString() == WORKSPACE_URL
    dialog = session._login_dialog
    session.open_login()
    assert session._login_dialog is dialog
    session.retranslate_ui()
    generation = session._active_refresh_generation
    assert generation is not None
    payload = {
        "kind": "quota",
        "generation": generation,
        "raw": {
            "fiveHour": {"percentage": 5, "resetSeconds": 3600},
            "sevenDay": {"percentage": 5, "resetSeconds": 3600},
        },
    }
    session._on_title_changed(_bridge_title(payload))
    assert login_states == [True]
    assert session._login_dialog_open is False
    session.close()
    assert session._login_dialog is None


def test_logout_cleanup_watchdog_timeout_resets_flag(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session._logout_after_load = True
    session._logout_cleanup_watchdog.timeout.emit()
    assert session._logout_after_load is False


def test_loading_changed_ignores_non_success_statuses(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))
    assert session.view.scripts == []


def test_loading_changed_ignores_foreign_origin(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.view._url = QUrl("https://example.com/other")
    session._on_loading_changed(FakeLoadingInfo())
    assert session.view.scripts == []


def test_title_without_bridge_prefix_ignored(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.refresh()
    session.view.scripts = []
    session._on_title_changed("plain page title")
    assert session.view.scripts == []


def test_bridge_unknown_kind_emits_refresh_failed(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    session._on_title_changed(_bridge_title({"kind": "mystery", "generation": generation}))
    assert errors == ["refresh_failed"]


def test_fetch_script_missing_token_plan_url_emits_refresh_failed(qapp, tmp_path: Path) -> None:
    del qapp
    session = QwenWebSession(tmp_path)
    session.view = FakeWebView()  # type: ignore[assignment]
    session.set_workspace_url("https://bailian.console.aliyun.com/home")
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session._run_fetch_script()
    assert errors == ["refresh_failed"]


def test_session_bridge_delivers_quota_payload(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    quotas: list[object] = []
    session.quota_received.connect(quotas.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    payload = {
        "kind": "quota",
        "generation": generation,
        "raw": {
            "fiveHour": {"percentage": 30, "resetSeconds": 18000},
            "sevenDay": {"percentage": 65, "resetSeconds": 604800},
        },
    }
    session._on_title_changed(_bridge_title(payload))
    assert len(quotas) == 1
    assert quotas[0]["fiveHour"]["percentage"] == 30


def test_session_bridge_unauthorized_emits_login_state(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    login_states: list[bool] = []
    errors: list[str] = []
    session.login_state_changed.connect(login_states.append)
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    payload = {"kind": "unauthorized", "generation": generation, "message": "UNAUTHORIZED:401"}
    session._on_title_changed(_bridge_title(payload))
    assert login_states == [False]
    assert errors == ["unauthorized"]


def test_session_bridge_stale_generation_ignored(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    session._on_title_changed(_bridge_title({"kind": "quota", "generation": 9999, "raw": {}}))
    assert errors == []


def test_session_refresh_timeout_emits_error(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    session._refresh_watchdog.timeout.emit()
    assert errors == ["refresh_timeout"]


def test_session_logout_clears_cookies(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.view._url = QUrl(WORKSPACE_URL)
    assert session.logout() is True
    session._on_loading_changed(FakeLoadingInfo())
    assert session.view.cookies_deleted is True
    assert "localStorage.clear" in session.view.scripts[-1]
    session.close()
    assert session.view.deleted is True


def test_logout_invalidates_in_flight_refresh(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    login_states: list[bool] = []
    quotas: list[object] = []
    session.login_state_changed.connect(login_states.append)
    session.quota_received.connect(quotas.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    session._on_loading_changed(FakeLoadingInfo())
    assert session._refresh_watchdog.isActive()
    session._login_dialog_open = True
    payload = {"kind": "quota", "generation": generation, "raw": {}}
    assert session.logout() is True
    assert session._active_refresh_generation is None
    assert not session._refresh_watchdog.isActive()
    session._on_title_changed(_bridge_title(payload))
    assert quotas == []
    assert login_states == [False]


def test_bridge_payload_generation_mismatch_ignored(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    quotas: list[object] = []
    errors: list[str] = []
    session.quota_received.connect(quotas.append)
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    payload = {"kind": "quota", "generation": generation + 5, "raw": {}}
    session._on_title_changed(_bridge_title(payload))
    assert quotas == []
    assert errors == []


def test_bridge_completion_clears_active_generation(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    quotas: list[object] = []
    session.quota_received.connect(quotas.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    payload = {"kind": "quota", "generation": generation, "raw": {}}
    session._on_title_changed(_bridge_title(payload))
    assert len(quotas) == 1
    assert session._active_refresh_generation is None
    session._on_title_changed(_bridge_title(payload))
    assert len(quotas) == 1


def test_refresh_navigation_path_arms_watchdog(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.refresh()
    assert session.view.url().toString() == WORKSPACE_URL
    assert session._refresh_watchdog.isActive()


def test_manual_login_dialog_dismissal_resets_state(monkeypatch, qapp, tmp_path: Path) -> None:
    del qapp
    import aacc.qwen_web_session as module

    container = QWidget()
    monkeypatch.setattr(module.QWidget, "createWindowContainer", lambda view, parent: container)
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.open_login()
    assert session._login_dialog_open is True
    assert session._refreshing is True
    assert session._refresh_watchdog.isActive()
    dialog = session._login_dialog
    assert dialog is not None
    dialog.close()
    assert session._login_dialog_open is False
    assert session._refreshing is False
    assert session._refresh_watchdog.isActive() is False
    assert session._active_refresh_generation is None
    assert errors == []
    session.view.scripts = []
    session.refresh()
    assert session.view.scripts
    assert "document.body.innerText" in session.view.scripts[-1]
    assert session.view.url().toString() == WORKSPACE_URL
    session.close()


def test_quota_success_close_does_not_double_handle(monkeypatch, qapp, tmp_path: Path) -> None:
    del qapp
    import aacc.qwen_web_session as module

    container = QWidget()
    monkeypatch.setattr(module.QWidget, "createWindowContainer", lambda view, parent: container)
    session = make_session(tmp_path)
    login_states: list[bool] = []
    errors: list[str] = []
    session.login_state_changed.connect(login_states.append)
    session.error_occurred.connect(errors.append)
    session.open_login()
    generation = session._active_refresh_generation
    assert generation is not None
    payload = {
        "kind": "quota",
        "generation": generation,
        "raw": {
            "fiveHour": {"percentage": 5, "resetSeconds": 3600},
            "sevenDay": {"percentage": 5, "resetSeconds": 3600},
        },
    }
    session._on_title_changed(_bridge_title(payload))
    assert login_states == [True]
    assert session._login_dialog_open is False
    assert session._refreshing is False
    assert errors == []
    session.close()


def test_open_login_arms_watchdog(monkeypatch, qapp, tmp_path: Path) -> None:
    del qapp
    import aacc.qwen_web_session as module

    container = QWidget()
    monkeypatch.setattr(module.QWidget, "createWindowContainer", lambda view, parent: container)
    session = make_session(tmp_path)
    session.open_login()
    assert session._refresh_watchdog.isActive()
    session.close()


def test_refresh_re_entry_is_inert_while_in_flight(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.view._url = QUrl(WORKSPACE_URL)
    session.refresh()
    assert session._refreshing is True
    assert session.view.scripts
    session.view.scripts = []
    session.refresh()
    assert session.view.scripts == []
    assert session.view.url().toString() == WORKSPACE_URL


def test_refresh_during_login_progress_does_not_navigate(monkeypatch, qapp, tmp_path: Path) -> None:
    del qapp
    import aacc.qwen_web_session as module

    container = QWidget()
    monkeypatch.setattr(module.QWidget, "createWindowContainer", lambda view, parent: container)
    session = make_session(tmp_path)
    session.open_login()
    assert session._refreshing is True
    assert session.view.url().toString() == WORKSPACE_URL
    session._close_login_dialog()
    session.view.scripts = []
    session.refresh()
    assert session.view.scripts == []
    assert session.view.url().toString() == WORKSPACE_URL
    session.close()


def test_language_switch_retranslates_login_dialog(monkeypatch, qapp, tmp_path: Path) -> None:
    del qapp
    import aacc.qwen_web_session as module

    container = QWidget()
    monkeypatch.setattr(module.QWidget, "createWindowContainer", lambda view, parent: container)
    language_manager = LanguageManager(ZH_CN)
    session = QwenWebSession(tmp_path, language_manager=language_manager)
    session.view = FakeWebView()  # type: ignore[assignment]
    session.set_workspace_url(WORKSPACE_URL)
    session.open_login()
    label = session._login_explanation_label
    assert label is not None
    assert label.text() == "正在启动百炼控制台登录页面，请稍候…"
    language_manager.set_language(EN_US)
    assert label.text() == "Starting the Bailian console login page. Please wait…"
    session.close()
    language_manager.set_language(ZH_CN)
    assert language_manager._subscribers == []
