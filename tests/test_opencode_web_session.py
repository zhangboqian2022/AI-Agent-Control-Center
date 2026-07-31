from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtWebView import QWebViewLoadingInfo
from PySide6.QtWidgets import QWidget

from aacc.file_security import FileProtectionError
from aacc.opencode_web_session import (
    BRIDGE_PAYLOAD_KEY,
    BRIDGE_PREFIX,
    SERVER_FN_HASH,
    OpenCodeWebSession,
    opencode_usage_fetch_script,
    opencode_webview_user_data_path,
    workspace_id_from_url,
)

WORKSPACE_URL = "https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go"


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


def make_session(tmp_path: Path) -> OpenCodeWebSession:
    session = OpenCodeWebSession(tmp_path)
    session.view = FakeWebView()  # type: ignore[assignment]
    session.set_workspace_url(WORKSPACE_URL)
    return session


def test_workspace_id_from_url() -> None:
    assert workspace_id_from_url(WORKSPACE_URL) == "wrk_01KYVH7EJDHAAE4TZ51J3TX5CS"
    assert workspace_id_from_url("https://opencode.ai/zen") is None


def test_user_data_path_windows_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert opencode_webview_user_data_path(tmp_path) == (
        Path(tmp_path / "local") / "AACC" / "opencode-web-session"
    )


def test_user_data_path_windows_raises_without_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(FileProtectionError):
        opencode_webview_user_data_path(tmp_path)


def test_fetch_script_embeds_workspace_id_and_server_hash() -> None:
    script = opencode_usage_fetch_script(WORKSPACE_URL, 7)
    assert "wrk_01KYVH7EJDHAAE4TZ51J3TX5CS" in script
    assert SERVER_FN_HASH in script
    assert "X-Server-Id" in script
    assert "X-Server-Instance" in script
    assert "X-Server-Id" in script and "server-fn:1" in script
    assert "subscription" in script
    assert "rollingUsage" in script
    assert BRIDGE_PAYLOAD_KEY in script
    assert "AACC_OPENCODE_QUOTA:" in script
    assert opencode_usage_fetch_script("https://opencode.ai/zen", 1) == ""


def test_fetch_script_uses_json_content_type() -> None:
    script = opencode_usage_fetch_script(WORKSPACE_URL, 1)
    assert "Content-Type" in script and "application/json" in script


def test_session_refresh_runs_fetch_script(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    login_states: list[bool] = []
    quotas: list[object] = []
    errors: list[str] = []
    session.login_state_changed.connect(login_states.append)
    session.quota_received.connect(quotas.append)
    session.error_occurred.connect(errors.append)

    session.refresh()
    assert session.view.url().toString() == WORKSPACE_URL
    session._on_loading_changed(FakeLoadingInfo())
    assert session.view.scripts
    assert "_server" in session.view.scripts[-1]


def test_session_without_workspace_url_is_inert(qapp, tmp_path: Path) -> None:
    del qapp
    session = OpenCodeWebSession(tmp_path)
    session.view = FakeWebView()  # type: ignore[assignment]
    assert session._is_opencode_origin() is False
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
    assert "_server" in session.view.scripts[-1]
    assert session.view.url().toString() == WORKSPACE_URL


def test_open_login_without_workspace_url_shows_message_box(
    monkeypatch, qapp, tmp_path: Path
) -> None:
    del qapp
    import aacc.opencode_web_session as module

    messages: list[str] = []
    monkeypatch.setattr(
        module.QMessageBox, "information", lambda *args: messages.append(str(args[-1]))
    )
    session = OpenCodeWebSession(tmp_path)
    session.view = FakeWebView()  # type: ignore[assignment]
    session.open_login()
    assert messages == []
    session.open_login(QWidget())
    assert messages == ["请先在 config.yaml 中配置 opencode_workspace_url"]


def test_open_login_builds_reusable_dialog_and_closes_after_quota(
    monkeypatch, qapp, tmp_path: Path
) -> None:
    del qapp
    import aacc.opencode_web_session as module

    container = QWidget()
    monkeypatch.setattr(module.QWidget, "createWindowContainer", lambda view, parent: container)
    session = make_session(tmp_path)
    login_states: list[bool] = []
    session.login_state_changed.connect(login_states.append)

    session.open_login()
    assert session._login_dialog is not None
    assert session._login_container is container
    assert session._login_explanation_label is not None
    assert session._login_dialog_open is True
    assert session.view.url().toString() == WORKSPACE_URL

    dialog = session._login_dialog
    session.open_login()
    assert session._login_dialog is dialog

    session.retranslate_ui()

    generation = session._active_refresh_generation
    assert generation is not None
    session.view.script_result = json.dumps(
        {
            "kind": "quota",
            "generation": generation,
            "raw": {
                "subscription": {
                    "rollingUsage": {"usagePercent": 5, "resetInSec": 3600},
                    "weeklyUsage": {"usagePercent": 5, "resetInSec": 3600},
                    "monthlyUsage": {"usagePercent": 5, "resetInSec": 3600},
                }
            },
        }
    )
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert login_states == [True]
    assert session._login_dialog_open is False

    session.close()
    assert session._login_dialog is None
    assert session._login_container is None
    assert session._login_explanation_label is None
    assert session.view.deleted is True


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


def test_title_with_invalid_generation_ignored(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    session.view.scripts = []
    session._on_title_changed(f"{BRIDGE_PREFIX}not-a-number:ready:result")
    assert session.view.scripts == []
    assert errors == []


def test_bridge_invalid_json_emits_refresh_failed(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    session.view.script_result = "{not valid json"
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert errors == ["refresh_failed"]


def test_bridge_unknown_kind_emits_refresh_failed(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    session.view.script_result = json.dumps({"kind": "mystery", "generation": generation})
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert errors == ["refresh_failed"]


def test_fetch_script_missing_workspace_id_emits_refresh_failed(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.set_workspace_url("https://opencode.ai/zen")
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
            "subscription": {
                "rollingUsage": {"usagePercent": 0, "resetInSec": 17760},
                "weeklyUsage": {"usagePercent": 42, "resetInSec": 226800},
                "monthlyUsage": {"usagePercent": 100, "resetInSec": 2674800},
            }
        },
    }
    session.view.script_result = json.dumps(payload)
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert len(quotas) == 1
    assert quotas[0]["subscription"]["rollingUsage"]["usagePercent"] == 0


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
    session.view.script_result = json.dumps(
        {"kind": "unauthorized", "generation": generation, "message": "UNAUTHORIZED:401"}
    )
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert login_states == [False]
    assert errors == ["unauthorized"]


def test_session_bridge_stale_generation_ignored(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    session._on_title_changed(f"{BRIDGE_PREFIX}9999:ready:result")
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
    session.view.script_result = json.dumps(
        {"kind": "quota", "generation": generation, "raw": {"subscription": {}}}
    )
    assert session.logout() is True
    assert session._active_refresh_generation is None
    assert not session._refresh_watchdog.isActive()
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
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
    session.view.script_result = json.dumps(
        {"kind": "quota", "generation": generation + 5, "raw": {"subscription": {}}}
    )
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
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
    session.view.script_result = json.dumps(
        {"kind": "quota", "generation": generation, "raw": {"subscription": {}}}
    )
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert len(quotas) == 1
    assert session._active_refresh_generation is None
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert len(quotas) == 1


def test_bridge_read_deletes_payload_key(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert session.view.scripts
    read_script = session.view.scripts[-1]
    assert "delete window[" in read_script
