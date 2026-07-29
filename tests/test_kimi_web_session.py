from __future__ import annotations

import inspect
import json
import logging
import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QWindow
from PySide6.QtWebView import QWebViewLoadingInfo
from PySide6.QtWidgets import QDialog, QWidget
from shiboken6 import isValid

import aacc.kimi_web_session as web_session
from aacc.file_security import FileProtectionError
from aacc.i18n import EN_US, ZH_CN, LanguageManager

KIMI_MEMBERSHIP_URL = web_session.KIMI_MEMBERSHIP_URL
KimiWebSession = web_session.KimiWebSession
membership_fetch_script = web_session.membership_fetch_script


def test_membership_expression_returns_payload_without_exporting_token() -> None:
    script = web_session.membership_fetch_expression(7)

    assert "return await Promise.all" in script
    assert "localStorage.getItem('access_token')" in script
    assert "Authorization" in script
    assert "console.log" not in script


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class FakeSettings:
    class WebAttribute:
        JavaScriptEnabled = 1
        LocalStorageEnabled = 2

    def __init__(self):
        self.attributes = []

    def setAttribute(self, attribute, enabled):
        self.attributes.append((attribute, enabled))


class FakeView:
    def __init__(self):
        self.loadingChanged = FakeSignal()
        self.titleChanged = FakeSignal()
        self._settings = FakeSettings()
        self._url = QUrl()
        self.scripts = []
        self.script_result = None
        self.respond_to_scripts = True
        self.pending_script_callbacks = []
        self.cookies_deleted = False
        self.deleted = False
        self.stopped = False

    def settings(self):
        return self._settings

    def url(self):
        return self._url

    def setUrl(self, url):
        self._url = url

    def runJavaScript(self, script, callback):
        self.scripts.append(script)
        if self.respond_to_scripts:
            callback(self.script_result)
        else:
            self.pending_script_callbacks.append(callback)

    def deleteAllCookies(self):
        self.cookies_deleted = True

    def deleteLater(self):
        self.deleted = True

    def stop(self):
        self.stopped = True


class FakeLoadingInfo:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


class ExistingFakeDialog:
    def __init__(self):
        self.accepted = False

    def show(self):
        pass

    def raise_(self):
        pass

    def activateWindow(self):
        pass

    def accept(self):
        self.accepted = True

    def close(self):
        pass


def make_session(monkeypatch, tmp_path, *, language_manager=None):
    monkeypatch.setattr(web_session, "_webview_initialized", True)
    monkeypatch.setattr(web_session, "QWebView", FakeView)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return KimiWebSession(tmp_path, language_manager=language_manager)


def test_membership_script_uses_cached_web_token_for_both_connect_services():
    script = membership_fetch_script()

    assert "GetSubscriptionStats" in script
    assert "GetSubscription" in script
    assert "credentials: 'include'" in script
    assert "localStorage.getItem('access_token')" in script
    assert "'Authorization': 'Bearer ' + accessToken" in script
    assert "emit({kind: 'quota', generation, stats, subscription})" in script
    assert "document.title = prefix + generation + ':ready:'" in script
    assert KIMI_MEMBERSHIP_URL.startswith("https://www.kimi.com/")


def test_membership_script_aborts_both_requests_after_fifteen_seconds():
    script = membership_fetch_script()

    assert "AbortController" in script
    assert "15000" in script
    catch_block = script.split("}).catch((error) => {", 1)[1]
    assert catch_block.index("controller.abort();") < catch_block.index("emit({")


def test_web_session_uses_native_system_webview_without_import_time_initialization():
    source = inspect.getsource(KimiWebSession)

    assert "QWebView()" in source
    assert "QWebEngine" not in source


def test_qt_window_container_survives_close_when_dialog_is_retained(qapp):
    window = QWindow()
    dialog = QDialog()
    container = QWidget.createWindowContainer(window, dialog)

    assert container.parentWidget() is dialog

    dialog.close()
    qapp.processEvents()

    assert isValid(dialog)
    assert isValid(container)
    assert isValid(window)


def test_native_webview_initialization_is_once_and_must_precede_app(monkeypatch, tmp_path):
    calls = []

    class FakeQtWebView:
        @staticmethod
        def initialize():
            calls.append(True)

    class NoApplication:
        @staticmethod
        def instance():
            return None

    monkeypatch.setattr(web_session, "_webview_initialized", False)
    monkeypatch.setattr(web_session, "QtWebView", FakeQtWebView)
    monkeypatch.setattr(web_session, "QGuiApplication", NoApplication)

    web_session.initialize_native_webview(tmp_path)
    web_session.initialize_native_webview(tmp_path)

    assert calls == [True]

    monkeypatch.setattr(web_session, "_webview_initialized", False)

    class ExistingApplication:
        @staticmethod
        def instance():
            return object()

    monkeypatch.setattr(web_session, "QGuiApplication", ExistingApplication)
    try:
        web_session.initialize_native_webview(tmp_path)
    except RuntimeError as error:
        assert "before QApplication" in str(error)
    else:
        raise AssertionError("late native webview initialization must fail")


def test_windows_native_webview_uses_writable_aacc_user_data_before_initialize(
    monkeypatch, tmp_path
):
    calls = []
    local_app_data = tmp_path / "Local App Data"
    expected = local_app_data / "AACC" / "kimi-web-session"
    monkeypatch.setattr(
        web_session,
        "sys",
        SimpleNamespace(platform="win32"),
        raising=False,
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("WEBVIEW2_USER_DATA_FOLDER", str(tmp_path / "stale-override"))
    monkeypatch.setattr(web_session, "_webview_initialized", False)
    monkeypatch.setattr(
        web_session,
        "protect_directory",
        lambda path: calls.append(("protect", path)),
    )

    class FakeQtWebView:
        @staticmethod
        def initialize():
            calls.append(
                (
                    "initialize",
                    os.environ.get("WEBVIEW2_USER_DATA_FOLDER"),
                )
            )

    class NoApplication:
        @staticmethod
        def instance():
            return None

    monkeypatch.setattr(web_session, "QtWebView", FakeQtWebView)
    monkeypatch.setattr(web_session, "QGuiApplication", NoApplication)

    web_session.initialize_native_webview(tmp_path / "roaming")

    assert calls == [
        ("protect", expected),
        ("initialize", str(expected)),
    ]


def test_windows_native_webview_wraps_user_data_directory_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(
        web_session,
        "sys",
        SimpleNamespace(platform="win32"),
        raising=False,
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(web_session, "_webview_initialized", False)
    monkeypatch.setattr(
        web_session,
        "protect_directory",
        lambda _path: (_ for _ in ()).throw(PermissionError("private path")),
    )

    class FakeQtWebView:
        @staticmethod
        def initialize():
            raise AssertionError("backend must not initialize after storage failure")

    class NoApplication:
        @staticmethod
        def instance():
            return None

    monkeypatch.setattr(web_session, "QtWebView", FakeQtWebView)
    monkeypatch.setattr(web_session, "QGuiApplication", NoApplication)

    with pytest.raises(FileProtectionError, match="native web view storage"):
        web_session.initialize_native_webview(tmp_path / "roaming")


def test_windows_native_webview_rejects_file_at_user_data_path(monkeypatch, tmp_path):
    local_app_data = tmp_path / "local"
    storage_path = local_app_data / "AACC" / "kimi-web-session"
    storage_path.parent.mkdir(parents=True)
    storage_path.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        web_session,
        "sys",
        SimpleNamespace(platform="win32"),
        raising=False,
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(web_session, "_webview_initialized", False)

    class NoApplication:
        @staticmethod
        def instance():
            return None

    monkeypatch.setattr(web_session, "QGuiApplication", NoApplication)

    with pytest.raises(FileProtectionError, match="native web view storage"):
        web_session.initialize_native_webview(tmp_path / "roaming")


def test_windows_native_webview_requires_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setattr(
        web_session,
        "sys",
        SimpleNamespace(platform="win32"),
        raising=False,
    )
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(FileProtectionError, match="LOCALAPPDATA"):
        web_session.native_webview_user_data_path(tmp_path)


def test_web_session_refresh_bridge_logout_and_close(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    login_states = []
    quotas = []
    errors = []
    session.login_state_changed.connect(
        lambda value: login_states.append((value, session.login_state.may_reuse()))
    )
    session.quota_received.connect(lambda stats, subscription: quotas.append((stats, subscription)))
    session.error_occurred.connect(errors.append)

    assert session.storage_path.is_dir()
    assert len(session.view.settings().attributes) == 2

    session.refresh()
    assert session.view.url().isEmpty()
    assert session.view.scripts == []

    session.login_state.set_may_reuse(True)
    session.refresh()
    assert session.view.url().toString() == KIMI_MEMBERSHIP_URL
    assert session._refresh_after_load is True

    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))
    assert "GetSubscriptionStats" in session.view.scripts[-1]
    generation = session._active_refresh_generation
    assert generation is not None

    payload = {
        "kind": "quota",
        "generation": generation,
        "stats": {"value": 1, "large": "x" * 100_000},
        "subscription": {"value": 2},
    }
    session.login_state.set_may_reuse(False)
    session.view.script_result = json.dumps(payload)
    session._on_title_changed(f"{web_session.BRIDGE_PREFIX}{generation}:ready:result")
    assert login_states == [(True, True)]
    assert session.login_state.may_reuse() is True
    assert quotas == [({"value": 1, "large": "x" * 100_000}, {"value": 2})]
    assert web_session.BRIDGE_PAYLOAD_KEY in session.view.scripts[-1]

    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    session._handle_bridge({"kind": "unauthorized", "generation": generation})
    assert session.login_state.may_reuse() is False
    session._login_dialog = ExistingFakeDialog()  # type: ignore[assignment]
    session.open_login()
    generation = session._active_refresh_generation
    assert generation is not None
    session._handle_bridge({"kind": "error", "generation": generation, "message": "network"})
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))
    generation = session._active_refresh_generation
    assert generation is not None
    session.view.script_result = "{"
    session._on_title_changed(f"{web_session.BRIDGE_PREFIX}{generation}:ready:malformed")
    assert login_states[-1] == (False, False)
    assert errors == ["refresh_failed", "refresh_failed"]

    session.logout()
    assert session.login_state.may_reuse() is False
    assert session.view.cookies_deleted is True
    assert "localStorage.clear" in session.view.scripts[-1]
    session.close()
    assert session.view.deleted is True


def test_logout_disables_reuse_before_same_origin_webview_cleanup(monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    calls = []
    original_set_may_reuse = session.login_state.set_may_reuse
    original_run_javascript = session.view.runJavaScript

    def set_may_reuse(value):
        calls.append(("gate", value))
        original_set_may_reuse(value)

    def run_javascript(script, callback):
        calls.append(("javascript", script))
        original_run_javascript(script, callback)

    monkeypatch.setattr(session.login_state, "set_may_reuse", set_may_reuse)
    monkeypatch.setattr(session.view, "runJavaScript", run_javascript)

    session.logout()

    assert calls[0] == ("gate", False)
    assert calls[1][0] == "javascript"
    assert session.login_state.may_reuse() is False


def test_logout_waits_for_kimi_origin_before_clearing_webview_data(monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl("https://example.com/account")

    session.logout()

    assert session.view.url().toString() == KIMI_MEMBERSHIP_URL
    assert session.view.scripts == []
    assert session.view.cookies_deleted is False
    assert session.login_state.may_reuse() is False

    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))

    assert "localStorage.clear" in session.view.scripts[-1]
    assert "sessionStorage.clear" in session.view.scripts[-1]
    assert session.view.cookies_deleted is True
    assert session.login_state.may_reuse() is False


def test_logout_navigation_timeout_ends_cleanup_and_ignores_late_load(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl("https://example.com/account")
    errors = []
    session.error_occurred.connect(errors.append)

    session.logout()

    generation = session._logout_cleanup_generation
    assert generation is not None
    assert session._logout_after_load is True
    assert session._logout_cleanup_watchdog.isActive() is True

    session._logout_cleanup_watchdog_fired(generation)

    assert session._logout_cleanup_generation is None
    assert session._logout_after_load is False
    assert session._logout_cleanup_watchdog.isActive() is False
    assert session.login_state.may_reuse() is False
    assert session.view.scripts == []
    assert session.view.cookies_deleted is False

    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))

    assert session.view.scripts == []
    assert session.view.cookies_deleted is False
    assert session.login_state.may_reuse() is False
    assert errors == []


def test_logout_cleanup_callback_loss_keeps_reuse_gate_closed(monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    session.view.respond_to_scripts = False

    session.logout()

    assert session.login_state.may_reuse() is False
    assert session.view.cookies_deleted is True
    generation = session._logout_cleanup_generation
    assert generation is not None
    assert session._logout_cleanup_watchdog.isSingleShot() is True
    assert session._logout_cleanup_watchdog.interval() == web_session.LOGOUT_CLEANUP_TIMEOUT_MS

    session._logout_cleanup_watchdog_fired(generation)

    assert session._logout_cleanup_generation is None
    assert session._logout_cleanup_watchdog.isActive() is False
    assert session.login_state.may_reuse() is False


def test_logout_javascript_timeout_ignores_late_failed_load(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    session.view.respond_to_scripts = False
    errors = []
    session.error_occurred.connect(errors.append)

    session.logout()
    generation = session._logout_cleanup_generation
    assert generation is not None
    assert session._logout_after_load is False

    session._logout_cleanup_watchdog_fired(generation)
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))

    assert session._logout_cleanup_generation is None
    assert session.login_state.may_reuse() is False
    assert errors == []


def test_open_login_cancels_pending_logout_before_starting_new_refresh(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl("https://example.com/account")
    session._login_dialog = ExistingFakeDialog()  # type: ignore[assignment]

    session.logout()
    logout_generation = session._logout_cleanup_generation
    assert logout_generation is not None
    assert session._logout_after_load is True

    session.open_login()
    refresh_generation = session._active_refresh_generation
    assert refresh_generation is not None
    assert refresh_generation > logout_generation
    assert session._logout_cleanup_generation is None
    assert session._logout_after_load is False
    assert session._logout_cleanup_watchdog.isActive() is False

    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))

    assert "GetSubscriptionStats" in session.view.scripts[-1]
    assert all("localStorage.clear" not in script for script in session.view.scripts)
    session._logout_cleanup_watchdog_fired(logout_generation)
    assert session._active_refresh_generation == refresh_generation
    assert session._refreshing is True


def test_stale_logout_watchdog_cannot_finish_newer_cleanup(monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    session.view.respond_to_scripts = False

    session.logout()
    old_generation = session._logout_cleanup_generation
    assert old_generation is not None
    session.logout()
    active_generation = session._logout_cleanup_generation
    assert active_generation is not None
    assert active_generation > old_generation

    session._logout_cleanup_watchdog_fired(old_generation)

    assert session._logout_cleanup_generation == active_generation
    assert session._logout_cleanup_watchdog.isActive() is True


def test_close_cancels_logout_cleanup_and_ignores_all_late_callbacks(monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    session.view.respond_to_scripts = False
    login_states = []
    session.login_state_changed.connect(login_states.append)

    session.logout()
    generation = session._logout_cleanup_generation
    assert generation is not None
    pending_callback = session.view.pending_script_callbacks[-1]
    script_count = len(session.view.scripts)
    login_states.clear()

    session.close()
    pending_callback(True)
    session._logout_cleanup_watchdog_fired(generation)
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))
    session._on_title_changed(f"{web_session.BRIDGE_PREFIX}{generation}:ready:late")

    assert session._closed is True
    assert session._logout_cleanup_generation is None
    assert session._logout_after_load is False
    assert session._logout_cleanup_watchdog.isActive() is False
    assert len(session.view.scripts) == script_count
    assert session.login_state.may_reuse() is False
    assert login_states == []
    assert session.view.deleted is True


def test_refresh_logging_never_records_webview_query_or_fragment(caplog, monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(
        f"{KIMI_MEMBERSHIP_URL}?code=oauth-code&state=oauth-state"
        "#access_token=fragment-token&password=fragment-password"
    )

    with caplog.at_level(logging.INFO, logger="aacc.kimi_web_session"):
        session.refresh()

    logs = caplog.text
    for secret in (
        "oauth-code",
        "oauth-state",
        "fragment-token",
        "fragment-password",
    ):
        assert secret not in logs
    assert "Authorization" not in logs


def test_bridge_error_logging_redacts_remote_secrets(caplog, monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    errors = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    message = "password=remote-password token=remote-token Authorization: Bearer remote-bearer"

    with caplog.at_level(logging.WARNING, logger="aacc.kimi_web_session"):
        session._handle_bridge({"kind": "error", "generation": generation, "message": message})

    assert errors == ["refresh_failed"]
    logs = caplog.text
    assert "remote-password" not in logs
    assert "remote-token" not in logs
    assert "remote-bearer" not in logs
    assert "Authorization" not in logs


def test_stale_generation_logging_never_formats_remote_value(caplog, monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    remote_generation = "Authorization: Bearer remote-generation-secret"

    with caplog.at_level(logging.INFO, logger="aacc.kimi_web_session"):
        session._handle_bridge({"kind": "error", "generation": remote_generation})

    assert "remote-generation-secret" not in caplog.text
    assert "Authorization" not in caplog.text


def test_login_dialog_retries_after_initial_unauthorized_page(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    dialog = ExistingFakeDialog()
    session._login_dialog = dialog  # type: ignore[assignment]

    session.open_login()
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))
    first_generation = session._active_refresh_generation
    assert first_generation is not None
    first_script_count = len(session.view.scripts)
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))
    assert len(session.view.scripts) == first_script_count
    session._handle_bridge({"kind": "unauthorized", "generation": first_generation})
    assert session._active_refresh_generation is None
    assert session.login_state.may_reuse() is False

    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))

    retry_generation = session._active_refresh_generation
    assert retry_generation is not None
    assert retry_generation > first_generation
    assert "GetSubscriptionStats" in session.view.scripts[-1]
    session._handle_bridge(
        {
            "kind": "quota",
            "generation": retry_generation,
            "stats": {},
            "subscription": {},
        }
    )
    assert session.login_state.may_reuse() is True
    assert dialog.accepted is True


def test_quota_success_fails_closed_when_reuse_gate_write_fails(
    caplog, qapp, monkeypatch, tmp_path
):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    login_states = []
    quotas = []
    errors = []
    session.login_state_changed.connect(login_states.append)
    session.quota_received.connect(lambda stats, subscription: quotas.append((stats, subscription)))
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None

    def fail_gate_write(_value):
        raise FileProtectionError("/Users/private/account-state.json password=gate-secret")

    monkeypatch.setattr(session.login_state, "set_may_reuse", fail_gate_write)
    with caplog.at_level(logging.ERROR, logger="aacc.kimi_web_session"):
        session._handle_bridge(
            {
                "kind": "quota",
                "generation": generation,
                "stats": {"value": 1},
                "subscription": {"value": 2},
            }
        )

    assert session._reuse_blocked is True
    assert login_states == []
    assert quotas == []
    assert errors == ["state_save_failed"]
    assert "account-state.json" not in caplog.text
    assert "gate-secret" not in caplog.text


def test_unauthorized_fails_closed_when_reuse_gate_write_fails(caplog, qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    login_states = []
    errors = []
    session.login_state_changed.connect(login_states.append)
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None

    def fail_gate_write(_value):
        raise FileProtectionError("C:\\private\\state.json token=gate-secret")

    monkeypatch.setattr(session.login_state, "set_may_reuse", fail_gate_write)
    with caplog.at_level(logging.ERROR, logger="aacc.kimi_web_session"):
        session._handle_bridge({"kind": "unauthorized", "generation": generation})

    script_count = len(session.view.scripts)
    session.refresh()

    assert session._reuse_blocked is True
    assert session._active_refresh_generation is None
    assert login_states == [False]
    assert errors == ["state_save_failed"]
    assert len(session.view.scripts) == script_count
    assert "state.json" not in caplog.text
    assert "gate-secret" not in caplog.text


def test_logout_gate_write_failure_still_invalidates_and_cleans_up(
    caplog, qapp, monkeypatch, tmp_path
):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    login_states = []
    quotas = []
    errors = []
    session.login_state_changed.connect(login_states.append)
    session.quota_received.connect(lambda stats, subscription: quotas.append((stats, subscription)))
    session.error_occurred.connect(errors.append)
    session.refresh()
    old_generation = session._active_refresh_generation
    assert old_generation is not None

    def fail_gate_write(_value):
        raise FileProtectionError("/private/logout-state.json Authorization: Bearer gate-secret")

    monkeypatch.setattr(session.login_state, "set_may_reuse", fail_gate_write)
    with caplog.at_level(logging.ERROR, logger="aacc.kimi_web_session"):
        logout_succeeded = session.logout()
        session._handle_bridge(
            {
                "kind": "quota",
                "generation": old_generation,
                "stats": {"late": True},
                "subscription": {},
            }
        )

    assert session._reuse_blocked is True
    assert logout_succeeded is False
    assert session._active_refresh_generation is None
    assert login_states == [False]
    assert quotas == []
    assert errors == ["state_save_failed"]
    assert "localStorage.clear" in session.view.scripts[-1]
    assert session.view.cookies_deleted is True
    assert "logout-state.json" not in caplog.text
    assert "gate-secret" not in caplog.text


def test_refresh_watchdog_releases_request_and_allows_new_generation(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    errors = []
    session.error_occurred.connect(errors.append)

    session.refresh()
    first_generation = session._active_refresh_generation
    assert first_generation is not None

    session._refresh_watchdog_fired(first_generation)

    assert session._refreshing is False
    assert errors == ["refresh_timeout"]

    session.refresh()

    assert session._active_refresh_generation is not None
    assert session._active_refresh_generation > first_generation


def test_bridge_payload_from_older_generation_is_ignored(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    quotas = []
    session.quota_received.connect(lambda stats, subscription: quotas.append((stats, subscription)))

    session.refresh()
    old_generation = session._active_refresh_generation
    assert old_generation is not None
    session._refresh_watchdog_fired(old_generation)
    session.refresh()
    active_generation = session._active_refresh_generation
    assert active_generation is not None

    session._handle_bridge(
        {
            "kind": "quota",
            "generation": old_generation,
            "stats": {"old": True},
            "subscription": {},
        }
    )

    assert active_generation > old_generation
    assert session._refreshing is True
    assert quotas == []


def test_malformed_bridge_from_older_title_generation_is_ignored(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    errors = []
    session.error_occurred.connect(errors.append)

    session.refresh()
    old_generation = session._active_refresh_generation
    assert old_generation is not None
    session._refresh_watchdog_fired(old_generation)
    errors.clear()
    session.refresh()
    active_generation = session._active_refresh_generation
    assert active_generation is not None
    script_count = len(session.view.scripts)

    for malformed in ("", "{"):
        session.view.script_result = malformed
        session._on_title_changed(f"{web_session.BRIDGE_PREFIX}{old_generation}:ready:late-result")

    assert active_generation > old_generation
    assert session._active_refresh_generation == active_generation
    assert session._refreshing is True
    assert errors == []
    assert len(session.view.scripts) == script_count


def test_close_invalidates_refresh_without_changing_gate_or_emitting_late_results(
    qapp, monkeypatch, tmp_path
):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    session.login_state.set_may_reuse(True)
    errors = []
    quotas = []
    session.error_occurred.connect(errors.append)
    session.quota_received.connect(lambda stats, subscription: quotas.append((stats, subscription)))

    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None

    session.close()
    session._refresh_watchdog_fired(generation)
    session._handle_bridge(
        {
            "kind": "quota",
            "generation": generation,
            "stats": {"late": True},
            "subscription": {},
        }
    )

    assert session.login_state.may_reuse() is True
    assert session._refreshing is False
    assert session._active_refresh_generation is None
    assert session._refresh_watchdog.isActive() is False
    assert errors == []
    assert quotas == []
    assert session.view.deleted is True


def test_web_session_loading_failure_and_login_dialog(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    errors = []
    session.error_occurred.connect(errors.append)

    session._refreshing = True
    session._refresh_after_load = True
    session._background_navigation_pending = True
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))
    assert errors == ["load_failed"]
    assert session._refreshing is False
    assert session._refresh_after_load is False

    class FakeDialog:
        def __init__(self, parent):
            self.parent = parent
            self.finished = FakeSignal()
            self.accepted = False
            self.closed = False

        def setWindowTitle(self, _title):
            pass

        def resize(self, _width, _height):
            pass

        def show(self):
            pass

        def raise_(self):
            pass

        def activateWindow(self):
            pass

        def accept(self):
            self.accepted = True

        def close(self):
            self.closed = True

    class FakeLabel:
        def __init__(self, text):
            self.text = text
            self.visible = True

        def setWordWrap(self, _enabled):
            pass

        def setText(self, text):
            self.text = text

        def setVisible(self, visible):
            self.visible = visible

    class FakeLayout:
        def __init__(self, _dialog):
            pass

        def addWidget(self, _widget, *_args):
            pass

    class FakeWidget:
        @staticmethod
        def createWindowContainer(view, dialog):
            class FakeContainer:
                def setVisible(self, _visible):
                    pass

            return FakeContainer()

    monkeypatch.setattr(web_session, "QDialog", FakeDialog)
    monkeypatch.setattr(web_session, "QLabel", FakeLabel)
    monkeypatch.setattr(web_session, "QVBoxLayout", FakeLayout)
    monkeypatch.setattr(web_session, "QWidget", FakeWidget)

    session.open_login()
    dialog = session._login_dialog
    assert dialog is not None
    assert session.login_state.may_reuse() is False
    assert session.view.url().toString() == KIMI_MEMBERSHIP_URL

    generation = session._active_refresh_generation
    assert generation is not None
    session._handle_bridge(
        {"kind": "quota", "generation": generation, "stats": {}, "subscription": {}}
    )
    assert dialog.accepted is True
    session._login_dialog_closed()
    assert session._login_dialog is dialog


def test_login_dialog_starts_startup_watchdog_and_restores_native_container(
    qapp, monkeypatch, tmp_path
):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    widgets = _install_login_dialog_fakes(monkeypatch)
    watchdog_states = []
    original_set_url = session.view.setUrl

    def set_url(url):
        watchdog_states.append(session._webview_startup_watchdog.isActive())
        original_set_url(url)

    monkeypatch.setattr(session.view, "setUrl", set_url)

    session.open_login()

    assert web_session.WEBVIEW_STARTUP_TIMEOUT_MS == 15_000
    assert session._webview_startup_watchdog.isSingleShot() is True
    assert session._webview_startup_watchdog.interval() == web_session.WEBVIEW_STARTUP_TIMEOUT_MS
    assert session._webview_startup_watchdog.isActive() is True
    assert watchdog_states == [True]
    assert "正在" in widgets["status"].text
    assert "Starting" not in widgets["status"].text
    assert widgets["container"].visible is True
    assert widgets["repair"].visible is False

    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Started))

    assert session._webview_startup_watchdog.isActive() is False
    assert widgets["container"].visible is True
    assert widgets["status"].visible is False
    assert widgets["repair"].visible is False


def test_open_kimi_login_dialog_retranslates_live(qapp, monkeypatch, tmp_path):
    del qapp
    language_manager = LanguageManager(ZH_CN)
    session = make_session(
        monkeypatch,
        tmp_path,
        language_manager=language_manager,
    )
    widgets = _install_login_dialog_fakes(monkeypatch)

    session.open_login()
    language_manager.set_language(EN_US)

    assert session._login_dialog is not None
    assert session._login_dialog.windowTitle() == "Kimi membership login"
    assert widgets["explanation"].text.startswith("Sign in directly")
    assert widgets["status"].text.startswith("Starting")
    assert widgets["repair"].text == "Repair Microsoft Edge WebView2"

    session._show_login_diagnostic()
    language_manager.set_language(ZH_CN)

    assert widgets["status"].text.startswith("无法启动 Kimi 登录页面")
    assert widgets["repair"].text == "修复 Microsoft Edge WebView2"


def test_repeated_language_switch_and_session_close_do_not_duplicate_callbacks(
    qapp, monkeypatch, tmp_path
):
    del qapp
    language_manager = LanguageManager(ZH_CN)
    session = make_session(
        monkeypatch,
        tmp_path,
        language_manager=language_manager,
    )
    widgets = _install_login_dialog_fakes(monkeypatch)

    session.open_login()
    dialog = widgets["dialog"]
    initial_updates = len(dialog.titles)
    language_manager.set_language(EN_US)
    language_manager.set_language(ZH_CN)
    session.close()
    language_manager.set_language(EN_US)

    assert len(dialog.titles) - initial_updates == 2
    assert language_manager._subscribers == []


def test_webview_failure_translation_never_contains_url_fragment_or_remote_body(
    caplog, qapp, monkeypatch, tmp_path
):
    del qapp
    language_manager = LanguageManager(EN_US)
    session = make_session(
        monkeypatch,
        tmp_path,
        language_manager=language_manager,
    )
    widgets = _install_login_dialog_fakes(monkeypatch)
    errors = []
    session.error_occurred.connect(errors.append)
    session.open_login()
    session.view._url = QUrl(
        "https://www.kimi.com/login?code=remote-code#access_token=remote-token"
    )

    with caplog.at_level(logging.DEBUG, logger="aacc.kimi_web_session"):
        session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))
        displayed_failure = widgets["status"].text
        session.open_login()
        generation = session._active_refresh_generation
        assert generation is not None
        session._handle_bridge(
            {
                "kind": "error",
                "generation": generation,
                "message": "remote body password=remote-password",
            }
        )

    combined = displayed_failure + "\n" + "\n".join(errors) + "\n" + caplog.text
    assert displayed_failure.startswith("Kimi login could not start")
    for secret in ("remote-code", "remote-token", "remote-password", "remote body"):
        assert secret not in combined


def test_login_dialog_close_retains_native_container_and_reuses_it_on_reopen(
    qapp, monkeypatch, tmp_path
):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    _install_login_dialog_fakes(monkeypatch)

    session.open_login()
    dialog = session._login_dialog
    container = session._login_container

    assert dialog is not None
    assert container is not None

    session._login_dialog_closed()

    assert session._login_dialog is dialog
    assert session._login_container is container

    session.open_login()

    assert session._login_dialog is dialog
    assert session._login_container is container


def test_login_dialog_close_invalidates_attempt_and_ignores_late_webview_events(
    qapp, monkeypatch, tmp_path
):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    widgets = _install_login_dialog_fakes(monkeypatch)
    quotas = []
    errors = []
    session.quota_received.connect(lambda stats, subscription: quotas.append((stats, subscription)))
    session.error_occurred.connect(errors.append)

    session.open_login()
    generation = session._active_refresh_generation

    assert generation is not None

    session._login_dialog_closed()
    session.login_state.set_may_reuse(True)
    session.refresh()
    script_count = len(session.view.scripts)
    background_generation = session._active_refresh_generation
    assert background_generation is not None
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))
    session._handle_bridge(
        {
            "kind": "quota",
            "generation": generation,
            "stats": {"late": True},
            "subscription": {},
        }
    )

    assert session._active_refresh_generation == background_generation
    assert session._refreshing is True
    assert len(session.view.scripts) == script_count
    assert session.view.stopped is True
    assert session.login_state.may_reuse() is True
    assert quotas == []
    assert errors == []
    assert widgets["repair"].visible is False


def test_login_success_marks_retained_dialog_closed_before_background_refresh(
    qapp, monkeypatch, tmp_path
):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    _install_login_dialog_fakes(monkeypatch)

    session.open_login()
    generation = session._active_refresh_generation

    assert generation is not None

    session._handle_bridge(
        {"kind": "quota", "generation": generation, "stats": {}, "subscription": {}}
    )

    assert session._login_dialog_open is False

    session.refresh()

    assert session._active_refresh_generation is not None


def test_stale_login_startup_timeout_cannot_affect_a_reopened_attempt(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    widgets = _install_login_dialog_fakes(monkeypatch)
    errors = []
    session.error_occurred.connect(errors.append)

    session.open_login()
    first_attempt = session._active_login_attempt
    session._login_dialog_closed()
    session.open_login()
    second_generation = session._active_refresh_generation

    assert first_attempt is not None
    assert second_generation is not None

    session._webview_startup_watchdog_timeout(first_attempt)

    assert session._active_refresh_generation == second_generation
    assert session._webview_startup_watchdog.isActive() is True
    assert widgets["container"].visible is True
    assert errors == []


def test_logout_navigation_completion_ignores_late_loading_events(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    _install_login_dialog_fakes(monkeypatch)
    errors = []
    session.error_occurred.connect(errors.append)
    session.view._url = QUrl("https://example.invalid/")

    session.logout()
    session.view._url = QUrl(KIMI_MEMBERSHIP_URL)
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))

    assert session._logout_cleanup_generation is None
    assert session._background_navigation_pending is False

    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))

    assert errors == []


def test_login_dialog_startup_timeout_shows_repair_and_reopens_cleanly(qapp, monkeypatch, tmp_path):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    widgets = _install_login_dialog_fakes(monkeypatch)
    opened_urls = []

    class FakeDesktopServices:
        @staticmethod
        def openUrl(url):
            opened_urls.append(url.toString())
            return True

    monkeypatch.setattr(web_session, "QDesktopServices", FakeDesktopServices)
    errors = []
    session.error_occurred.connect(errors.append)
    session.open_login()

    session._webview_startup_watchdog_timeout()

    assert session._webview_startup_watchdog.isActive() is False
    assert session._active_refresh_generation is None
    assert widgets["container"].visible is False
    assert widgets["status"].visible is True
    assert "WebView2" in widgets["status"].text
    assert "网络" in widgets["status"].text
    assert widgets["repair"].visible is True
    assert errors == ["load_failed"]
    assert KIMI_MEMBERSHIP_URL not in errors[0]

    widgets["repair"].clicked.emit()

    assert opened_urls == [web_session.WEBVIEW2_HELP_URL]
    assert web_session.WEBVIEW2_HELP_URL.startswith("https://")
    assert "microsoft.com" in web_session.WEBVIEW2_HELP_URL

    session._login_dialog_closed()
    assert session._webview_startup_watchdog.isActive() is False
    dialog = session._login_dialog
    assert dialog is not None

    session.open_login()
    assert session._webview_startup_watchdog.isActive() is True
    assert session._login_dialog is dialog


def test_login_dialog_loading_failure_shows_sanitized_webview_diagnostic(
    caplog, qapp, monkeypatch, tmp_path
):
    del qapp
    session = make_session(monkeypatch, tmp_path)
    widgets = _install_login_dialog_fakes(monkeypatch)
    errors = []
    session.error_occurred.connect(errors.append)
    session.open_login()
    session.view._url = QUrl(
        "https://www.kimi.com/login?code=remote-code#access_token=remote-token"
    )

    with caplog.at_level(logging.DEBUG, logger="aacc.kimi_web_session"):
        session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))

    assert session._webview_startup_watchdog.isActive() is False
    assert widgets["container"].visible is False
    assert widgets["status"].visible is True
    assert "WebView2" in widgets["status"].text
    assert "网络" in widgets["status"].text
    assert widgets["repair"].visible is True
    assert errors == ["load_failed"]
    assert KIMI_MEMBERSHIP_URL not in errors[0]
    assert "remote-code" not in caplog.text
    assert "remote-token" not in caplog.text


def _install_login_dialog_fakes(monkeypatch):
    widgets = {}

    class FakeDialog:
        def __init__(self, _parent):
            self.finished = FakeSignal()
            self.title = ""
            self.titles = []
            widgets["dialog"] = self

        def setWindowTitle(self, title):
            self.title = title
            self.titles.append(title)

        def windowTitle(self):
            return self.title

        def resize(self, _width, _height):
            pass

        def show(self):
            pass

        def raise_(self):
            pass

        def activateWindow(self):
            pass

        def accept(self):
            pass

        def close(self):
            pass

    class FakeLabel:
        def __init__(self, text):
            self.text = text
            self.visible = True

        def setWordWrap(self, _enabled):
            pass

        def setText(self, text):
            self.text = text

        def setVisible(self, visible):
            self.visible = visible

    class FakeButton(FakeLabel):
        def __init__(self, text):
            super().__init__(text)
            self.clicked = FakeSignal()

    class FakeContainer:
        def __init__(self):
            self.visible = True

        def setVisible(self, visible):
            self.visible = visible

    class FakeWidget:
        @staticmethod
        def createWindowContainer(_view, _dialog):
            container = FakeContainer()
            widgets["container"] = container
            return container

    class FakeLayout:
        def __init__(self, _dialog):
            self._label_count = 0

        def addWidget(self, widget, *_args):
            if isinstance(widget, FakeButton):
                widgets["repair"] = widget
            elif isinstance(widget, FakeLabel):
                key = "explanation" if self._label_count == 0 else "status"
                widgets[key] = widget
                self._label_count += 1

    monkeypatch.setattr(web_session, "QDialog", FakeDialog)
    monkeypatch.setattr(web_session, "QLabel", FakeLabel)
    monkeypatch.setattr(web_session, "QPushButton", FakeButton)
    monkeypatch.setattr(web_session, "QVBoxLayout", FakeLayout)
    monkeypatch.setattr(web_session, "QWidget", FakeWidget)
    return widgets
