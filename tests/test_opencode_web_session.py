from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtWebView import QWebViewLoadingInfo

from aacc.opencode_web_session import (
    BRIDGE_PAYLOAD_KEY,
    BRIDGE_PREFIX,
    SERVER_FN_HASH,
    OpenCodeWebSession,
    opencode_usage_fetch_script,
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
