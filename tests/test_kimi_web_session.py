from __future__ import annotations

import base64
import inspect
import json

from PySide6.QtCore import QUrl
from PySide6.QtWebView import QWebViewLoadingInfo

import aacc.kimi_web_session as web_session

KIMI_MEMBERSHIP_URL = web_session.KIMI_MEMBERSHIP_URL
KimiWebSession = web_session.KimiWebSession
membership_fetch_script = web_session.membership_fetch_script


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
        self.cookies_deleted = False
        self.deleted = False

    def settings(self):
        return self._settings

    def url(self):
        return self._url

    def setUrl(self, url):
        self._url = url

    def runJavaScript(self, script, callback):
        self.scripts.append(script)
        callback(None)

    def deleteAllCookies(self):
        self.cookies_deleted = True

    def deleteLater(self):
        self.deleted = True


class FakeLoadingInfo:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


def make_session(monkeypatch, tmp_path):
    monkeypatch.setattr(web_session, "_webview_initialized", True)
    monkeypatch.setattr(web_session, "QWebView", FakeView)
    return KimiWebSession(tmp_path)


def test_membership_script_uses_cached_web_token_for_both_connect_services():
    script = membership_fetch_script()

    assert "GetSubscriptionStats" in script
    assert "GetSubscription" in script
    assert "credentials: 'include'" in script
    assert "localStorage.getItem('access_token')" in script
    assert "'Authorization': 'Bearer ' + accessToken" in script
    assert "emit({kind: 'quota', stats, subscription})" in script
    assert KIMI_MEMBERSHIP_URL.startswith("https://www.kimi.com/")


def test_web_session_uses_native_system_webview_without_import_time_initialization():
    source = inspect.getsource(KimiWebSession)

    assert "QWebView()" in source
    assert "QWebEngine" not in source


def test_native_webview_initialization_is_once_and_must_precede_app(monkeypatch):
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

    web_session.initialize_native_webview()
    web_session.initialize_native_webview()

    assert calls == [True]

    monkeypatch.setattr(web_session, "_webview_initialized", False)

    class ExistingApplication:
        @staticmethod
        def instance():
            return object()

    monkeypatch.setattr(web_session, "QGuiApplication", ExistingApplication)
    try:
        web_session.initialize_native_webview()
    except RuntimeError as error:
        assert "before QApplication" in str(error)
    else:
        raise AssertionError("late native webview initialization must fail")


def test_web_session_refresh_bridge_logout_and_close(monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    login_states = []
    quotas = []
    errors = []
    session.login_state_changed.connect(login_states.append)
    session.quota_received.connect(lambda stats, subscription: quotas.append((stats, subscription)))
    session.error_occurred.connect(errors.append)

    assert session.storage_path.is_dir()
    assert len(session.view.settings().attributes) == 2

    session.refresh()
    assert session.view.url().toString() == KIMI_MEMBERSHIP_URL
    assert session._refresh_after_load is True

    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Succeeded))
    assert "GetSubscriptionStats" in session.view.scripts[-1]

    payload = {"kind": "quota", "stats": {"value": 1}, "subscription": {"value": 2}}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    session._on_title_changed(web_session.BRIDGE_PREFIX + encoded)
    assert login_states == [True]
    assert quotas == [({"value": 1}, {"value": 2})]

    session._handle_bridge({"kind": "unauthorized"})
    session._handle_bridge({"kind": "error", "message": "network"})
    session._handle_bridge("invalid")
    session._on_title_changed(web_session.BRIDGE_PREFIX + "not-base64")
    assert login_states[-1] is False
    assert errors == [
        "network",
        "Kimi 会员响应格式无效",
        "invalid membership response",
    ]

    session.logout()
    assert session.view.cookies_deleted is True
    assert "localStorage.clear" in session.view.scripts[-1]
    session.close()
    assert session.view.deleted is True


def test_web_session_loading_failure_and_login_dialog(monkeypatch, tmp_path):
    session = make_session(monkeypatch, tmp_path)
    errors = []
    session.error_occurred.connect(errors.append)

    session._refreshing = True
    session._refresh_after_load = True
    session._on_loading_changed(FakeLoadingInfo(QWebViewLoadingInfo.LoadStatus.Failed))
    assert errors == ["Kimi 官网加载失败"]
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
        def __init__(self, _text):
            pass

        def setWordWrap(self, _enabled):
            pass

    class FakeLayout:
        def __init__(self, _dialog):
            pass

        def addWidget(self, _widget, *_args):
            pass

    class FakeWidget:
        @staticmethod
        def createWindowContainer(view, dialog):
            return (view, dialog)

    monkeypatch.setattr(web_session, "QDialog", FakeDialog)
    monkeypatch.setattr(web_session, "QLabel", FakeLabel)
    monkeypatch.setattr(web_session, "QVBoxLayout", FakeLayout)
    monkeypatch.setattr(web_session, "QWidget", FakeWidget)

    session.open_login()
    dialog = session._login_dialog
    assert dialog is not None
    assert session.view.url().toString() == KIMI_MEMBERSHIP_URL

    session._handle_bridge({"kind": "quota", "stats": {}, "subscription": {}})
    assert dialog.accepted is True
    session._login_dialog_closed()
    assert session._login_dialog is None
