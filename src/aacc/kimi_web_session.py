"""AACC-owned Kimi session using the operating system's native web view."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebView import QtWebView, QWebView, QWebViewLoadingInfo
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from aacc.file_security import protect_directory

KIMI_MEMBERSHIP_URL = "https://www.kimi.com/membership/subscription"
KIMI_ORIGIN_HOST = "www.kimi.com"
BRIDGE_PREFIX = "AACC_KIMI_QUOTA:"
_webview_initialized = False


def initialize_native_webview() -> None:
    """Initialize Qt's native backend before QApplication is constructed."""

    global _webview_initialized
    if _webview_initialized:
        return
    if QGuiApplication.instance() is not None:
        raise RuntimeError("native web view must be initialized before QApplication")
    QtWebView.initialize()
    _webview_initialized = True


def membership_fetch_script() -> str:
    """Return the same-origin metadata request used by Kimi's native web view."""

    base = "/apiv2/kimi.gateway.membership.v2.MembershipService/"
    return f"""
(() => {{
  const prefix = {json.dumps(BRIDGE_PREFIX)};
  const emit = (payload) => {{
    const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
    document.title = prefix + encoded;
  }};
  const request = async (method) => {{
    const response = await fetch({json.dumps(base)} + method, {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/json',
        'Connect-Protocol-Version': '1'
      }},
      credentials: 'include',
      body: '{{}}'
    }});
    if (response.status === 401 || response.status === 403) {{
      throw new Error('UNAUTHORIZED:' + response.status);
    }}
    if (!response.ok) {{
      throw new Error('HTTP:' + response.status);
    }}
    return await response.json();
  }};
  Promise.all([
    request('GetSubscriptionStats'),
    request('GetSubscription')
  ]).then(([stats, subscription]) => {{
    emit({{kind: 'quota', stats, subscription}});
  }}).catch((error) => {{
    const message = String(error && error.message || error);
    emit({{
      kind: message.startsWith('UNAUTHORIZED:') ? 'unauthorized' : 'error',
      message: message.slice(0, 120)
    }});
  }});
}})();
"""


class KimiWebSession(QObject):
    """Keep Kimi cookies in the platform web view; never handle the password."""

    login_state_changed = Signal(bool)
    quota_received = Signal(object, object)
    error_occurred = Signal(str)

    def __init__(self, config_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if not _webview_initialized:
            raise RuntimeError("native web view is not initialized")
        self.storage_path = config_dir / "kimi-web-session"
        protect_directory(self.storage_path)
        self.view = QWebView()
        self.view.settings().setAttribute(self.view.settings().WebAttribute.JavaScriptEnabled, True)
        self.view.settings().setAttribute(
            self.view.settings().WebAttribute.LocalStorageEnabled, True
        )
        self.view.loadingChanged.connect(self._on_loading_changed)
        self.view.titleChanged.connect(self._on_title_changed)
        self._refreshing = False
        self._refresh_after_load = False
        self._login_dialog: QDialog | None = None

    def open_login(self, parent: QWidget | None = None) -> None:
        if self._login_dialog is None:
            dialog = QDialog(parent)
            dialog.setWindowTitle("Kimi 会员网页登录")
            dialog.resize(960, 720)
            layout = QVBoxLayout(dialog)
            explanation = QLabel(
                "请直接在 Kimi 官网完成登录。AACC 只复用系统 WebView 会话，"
                "不保存账号密码；登录成功后会自动同步 5H、WEEK 和 MONTH。"
            )
            explanation.setWordWrap(True)
            layout.addWidget(explanation)
            container = QWidget.createWindowContainer(self.view, dialog)
            layout.addWidget(container, 1)
            dialog.finished.connect(self._login_dialog_closed)
            self._login_dialog = dialog
        self._login_dialog.show()
        self._login_dialog.raise_()
        self._login_dialog.activateWindow()
        self.view.setUrl(QUrl(KIMI_MEMBERSHIP_URL))

    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        url = self.view.url()
        if url.scheme() == "https" and url.host() == KIMI_ORIGIN_HOST:
            self._run_fetch()
            return
        self._refresh_after_load = True
        self.view.setUrl(QUrl(KIMI_MEMBERSHIP_URL))

    def logout(self) -> None:
        self.view.runJavaScript(
            "try { localStorage.clear(); sessionStorage.clear(); } catch (_) {}",
            lambda _result: None,
        )
        self.view.deleteAllCookies()
        self.login_state_changed.emit(False)

    def close(self) -> None:
        if self._login_dialog is not None:
            self._login_dialog.close()
        self.view.deleteLater()

    def _run_fetch(self) -> None:
        self.view.runJavaScript(membership_fetch_script(), lambda _result: None)

    def _on_loading_changed(self, info: QWebViewLoadingInfo) -> None:
        status = info.status()
        if status is QWebViewLoadingInfo.LoadStatus.Failed:
            self._refreshing = False
            self._refresh_after_load = False
            self.error_occurred.emit("Kimi 官网加载失败")
            return
        if status is not QWebViewLoadingInfo.LoadStatus.Succeeded:
            return
        if self.view.url().host() != KIMI_ORIGIN_HOST:
            return
        if self._refresh_after_load or self._login_dialog is not None:
            self._refresh_after_load = False
            self._refreshing = True
            self._run_fetch()

    def _on_title_changed(self, title: str) -> None:
        if not title.startswith(BRIDGE_PREFIX):
            return
        encoded = title.removeprefix(BRIDGE_PREFIX)
        try:
            raw = base64.b64decode(encoded, validate=True).decode("utf-8")
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._handle_bridge({"kind": "error", "message": "invalid membership response"})
            return
        self._handle_bridge(payload)

    def _handle_bridge(self, payload: object) -> None:
        self._refreshing = False
        if not isinstance(payload, dict):
            self.error_occurred.emit("Kimi 会员响应格式无效")
            return
        kind = payload.get("kind")
        if kind == "quota":
            self.login_state_changed.emit(True)
            self.quota_received.emit(payload.get("stats"), payload.get("subscription"))
            if self._login_dialog is not None:
                self._login_dialog.accept()
            return
        if kind == "unauthorized":
            self.login_state_changed.emit(False)
            return
        message = payload.get("message")
        self.error_occurred.emit(
            message if isinstance(message, str) and message else "Kimi 会员额度刷新失败"
        )

    def _login_dialog_closed(self) -> None:
        self._login_dialog = None
