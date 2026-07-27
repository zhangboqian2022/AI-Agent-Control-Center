"""AACC-owned Kimi session using the operating system's native web view."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebView import QtWebView, QWebView, QWebViewLoadingInfo
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from aacc.file_security import protect_directory
from aacc.kimi_web_login_state import KimiWebLoginStateStore

KIMI_MEMBERSHIP_URL = "https://www.kimi.com/membership/subscription"
KIMI_ORIGIN_HOST = "www.kimi.com"
BRIDGE_PREFIX = "AACC_KIMI_QUOTA:"
BRIDGE_PAYLOAD_KEY = "__AACC_KIMI_QUOTA_PAYLOAD__"
LOGOUT_CLEANUP_TIMEOUT_MS = 10_000
LOGIN_STATE_SAVE_ERROR = "Kimi 网页登录状态保存失败"
_webview_initialized = False
_logger = logging.getLogger("aacc.kimi_web_session")


def initialize_native_webview() -> None:
    """Initialize Qt's native backend before QApplication is constructed."""

    global _webview_initialized
    if _webview_initialized:
        return
    if QGuiApplication.instance() is not None:
        raise RuntimeError("native web view must be initialized before QApplication")
    QtWebView.initialize()
    _webview_initialized = True


def membership_fetch_script(generation: int = 0) -> str:
    """Return the same-origin metadata request used by Kimi's native web view."""

    base = "/apiv2/kimi.gateway.membership.v2.MembershipService/"
    return f"""
(() => {{
  const prefix = {json.dumps(BRIDGE_PREFIX)};
  const payloadKey = {json.dumps(BRIDGE_PAYLOAD_KEY)};
  const generation = {generation};
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 15000);
  const emit = (payload) => {{
    window[payloadKey] = JSON.stringify(payload);
    document.title = prefix + generation + ':ready:' + Date.now() + ':' + Math.random();
  }};
  const request = async (method) => {{
    let accessToken = localStorage.getItem('access_token');
    if (accessToken) {{
      try {{
        const parsed = JSON.parse(accessToken);
        if (typeof parsed === 'string') accessToken = parsed;
      }} catch (_) {{}}
    }}
    if (!accessToken) {{
      throw new Error('UNAUTHORIZED:NO_TOKEN');
    }}
    const response = await fetch({json.dumps(base)} + method, {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/json',
        'Connect-Protocol-Version': '1',
        'Authorization': 'Bearer ' + accessToken
      }},
      credentials: 'include',
      signal: controller.signal,
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
    emit({{kind: 'quota', generation, stats, subscription}});
  }}).catch((error) => {{
    controller.abort();
    const message = String(error && error.message || error);
    emit({{
      kind: message.startsWith('UNAUTHORIZED:') ? 'unauthorized' : 'error',
      generation,
      message: message.slice(0, 120)
    }});
  }}).finally(() => clearTimeout(deadline));
}})();
"""


class KimiWebSession(QObject):
    """Keep Kimi cookies in the platform web view; never handle the password."""

    login_state_changed = Signal(bool)
    quota_received = Signal(object, object)
    error_occurred = Signal(str)

    def __init__(
        self,
        config_dir: Path,
        parent: QObject | None = None,
        login_state: KimiWebLoginStateStore | None = None,
    ) -> None:
        super().__init__(parent)
        if not _webview_initialized:
            raise RuntimeError("native web view is not initialized")
        self.storage_path = config_dir / "kimi-web-session"
        protect_directory(self.storage_path)
        self.login_state = login_state or KimiWebLoginStateStore(config_dir)
        self.view = QWebView()
        self.view.settings().setAttribute(self.view.settings().WebAttribute.JavaScriptEnabled, True)
        self.view.settings().setAttribute(
            self.view.settings().WebAttribute.LocalStorageEnabled, True
        )
        self.view.loadingChanged.connect(self._on_loading_changed)
        self.view.titleChanged.connect(self._on_title_changed)
        self._refreshing = False
        self._refresh_after_load = False
        self._refresh_generation = 0
        self._active_refresh_generation: int | None = None
        self._refresh_watchdog_generation: int | None = None
        self._refresh_watchdog = QTimer(self)
        self._refresh_watchdog.setSingleShot(True)
        self._refresh_watchdog.timeout.connect(self._refresh_watchdog_timeout)
        self._logout_after_load = False
        self._logout_cleanup_generation: int | None = None
        self._logout_cleanup_watchdog = QTimer(self)
        self._logout_cleanup_watchdog.setSingleShot(True)
        self._logout_cleanup_watchdog.timeout.connect(self._logout_cleanup_watchdog_timeout)
        self._ignore_expired_logout_loads = False
        self._login_dialog: QDialog | None = None
        self._reuse_blocked = False
        self._closed = False

    def open_login(self, parent: QWidget | None = None) -> None:
        if self._closed:
            return
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
        self._begin_refresh()
        self._refresh_after_load = True
        self.view.setUrl(QUrl(KIMI_MEMBERSHIP_URL))

    def refresh(self) -> None:
        if (
            self._closed
            or self._refreshing
            or self._reuse_blocked
            or not self.login_state.may_reuse()
        ):
            return
        generation = self._begin_refresh()
        url = self.view.url()
        if url.scheme() == "https" and url.host() == KIMI_ORIGIN_HOST:
            self._run_fetch(generation)
            return
        self._refresh_after_load = True
        self.view.setUrl(QUrl(KIMI_MEMBERSHIP_URL))

    def logout(self) -> bool:
        self._reuse_blocked = True
        self._invalidate_refresh()
        persisted = self._persist_reuse_state(False)
        if self._closed:
            self.login_state_changed.emit(False)
            return persisted
        self._cancel_logout_cleanup()
        self._ignore_expired_logout_loads = False
        self._logout_after_load = True
        self._logout_cleanup_generation = self._refresh_generation
        self._logout_cleanup_watchdog.start(LOGOUT_CLEANUP_TIMEOUT_MS)
        if self._is_kimi_origin():
            self._run_logout_cleanup()
        else:
            self.view.setUrl(QUrl(KIMI_MEMBERSHIP_URL))
        self.login_state_changed.emit(False)
        return persisted

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._invalidate_refresh()
        self._cancel_logout_cleanup()
        if self._login_dialog is not None:
            self._login_dialog.close()
        self.view.deleteLater()

    def _begin_refresh(self) -> int:
        self._cancel_logout_cleanup()
        self._ignore_expired_logout_loads = False
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._active_refresh_generation = generation
        self._refresh_watchdog_generation = generation
        self._refreshing = True
        self._refresh_watchdog.start(25_000)
        return generation

    def _invalidate_refresh(self) -> None:
        self._refresh_generation += 1
        self._active_refresh_generation = None
        self._refresh_watchdog_generation = None
        self._refreshing = False
        self._refresh_after_load = False
        self._refresh_watchdog.stop()

    def _run_fetch(self, generation: int) -> None:
        if self._closed:
            return
        _logger.info("Kimi web quota refresh started")
        self.view.runJavaScript(membership_fetch_script(generation), lambda _result: None)

    def _persist_reuse_state(self, value: bool) -> bool:
        try:
            self.login_state.set_may_reuse(value)
        except Exception:  # noqa: BLE001 - Qt callbacks must fail closed
            _logger.error("Kimi web reuse gate update failed")
            self.error_occurred.emit(LOGIN_STATE_SAVE_ERROR)
            return False
        return True

    def _run_logout_cleanup(self) -> None:
        if self._closed:
            return
        generation = self._logout_cleanup_generation
        if generation is None:
            return
        self._logout_after_load = False
        self.view.runJavaScript(
            "try { localStorage.clear(); sessionStorage.clear(); return true; } "
            "catch (_) { return false; }",
            lambda _result: self._finish_logout_cleanup(generation),
        )
        self.view.deleteAllCookies()

    def _finish_logout_cleanup(self, generation: int) -> None:
        if self._closed or generation != self._logout_cleanup_generation:
            return
        self._cancel_logout_cleanup()

    def _cancel_logout_cleanup(self) -> None:
        self._logout_after_load = False
        self._logout_cleanup_generation = None
        self._logout_cleanup_watchdog.stop()

    def _logout_cleanup_watchdog_timeout(self) -> None:
        generation = self._logout_cleanup_generation
        if generation is not None:
            self._logout_cleanup_watchdog_fired(generation)

    def _logout_cleanup_watchdog_fired(self, generation: int) -> None:
        if self._closed or generation != self._logout_cleanup_generation:
            return
        self._ignore_expired_logout_loads = True
        self._finish_logout_cleanup(generation)

    def _refresh_watchdog_timeout(self) -> None:
        if self._closed:
            return
        generation = self._refresh_watchdog_generation
        if generation is not None:
            self._refresh_watchdog_fired(generation)

    def _refresh_watchdog_fired(self, generation: int) -> None:
        if self._closed or generation != self._active_refresh_generation:
            return
        self._complete_refresh(generation)
        self.error_occurred.emit("Kimi 会员额度刷新超时")

    def _complete_refresh(self, generation: int) -> bool:
        if self._closed or generation != self._active_refresh_generation:
            return False
        self._refreshing = False
        self._refresh_after_load = False
        self._active_refresh_generation = None
        if generation == self._refresh_watchdog_generation:
            self._refresh_watchdog_generation = None
            self._refresh_watchdog.stop()
        return True

    def _is_kimi_origin(self) -> bool:
        url = self.view.url()
        return url.scheme() == "https" and url.host() == KIMI_ORIGIN_HOST

    def _on_loading_changed(self, info: QWebViewLoadingInfo) -> None:
        if self._closed or self._ignore_expired_logout_loads:
            return
        status = info.status()
        if status is QWebViewLoadingInfo.LoadStatus.Failed:
            logout_generation = self._logout_cleanup_generation
            if logout_generation is not None:
                self._finish_logout_cleanup(logout_generation)
            generation = self._active_refresh_generation
            if generation is not None:
                self._complete_refresh(generation)
            else:
                self._refreshing = False
                self._refresh_after_load = False
                self._refresh_watchdog.stop()
            self.error_occurred.emit("Kimi 官网加载失败")
            return
        if status is not QWebViewLoadingInfo.LoadStatus.Succeeded:
            return
        if not self._is_kimi_origin():
            return
        if self._logout_after_load:
            self._run_logout_cleanup()
            return
        should_fetch = self._refresh_after_load
        if should_fetch:
            self._refresh_after_load = False
        generation = self._active_refresh_generation
        if generation is None and self._login_dialog is not None:
            generation = self._begin_refresh()
            should_fetch = True
        if should_fetch and generation is not None:
            self._run_fetch(generation)

    def _on_title_changed(self, title: str) -> None:
        if self._closed or not title.startswith(BRIDGE_PREFIX):
            return
        generation_text, separator, suffix = title[len(BRIDGE_PREFIX) :].partition(":")
        if not separator or not suffix.startswith("ready:"):
            return
        try:
            generation = int(generation_text)
        except ValueError:
            return
        if generation != self._active_refresh_generation:
            return
        script = (
            "(() => {"
            f"const key = {json.dumps(BRIDGE_PAYLOAD_KEY)};"
            "const value = window[key] || '';"
            "delete window[key];"
            "return value;"
            "})()"
        )
        self.view.runJavaScript(
            script,
            lambda raw: self._on_bridge_result(raw, generation),
        )

    def _on_bridge_result(self, raw: object, generation: int) -> None:
        if self._closed or generation != self._active_refresh_generation:
            return
        try:
            if not isinstance(raw, str):
                raise ValueError
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            self._handle_bridge(
                {
                    "kind": "error",
                    "generation": generation,
                    "message": "invalid membership response",
                }
            )
            return
        self._handle_bridge(payload)

    def _handle_bridge(self, payload: object) -> None:
        if self._closed or not isinstance(payload, dict):
            return
        generation = payload.get("generation")
        if type(generation) is not int or not self._complete_refresh(generation):
            _logger.info("Kimi web quota bridge ignored for stale generation")
            return
        kind = payload.get("kind")
        if kind == "quota":
            stats = payload.get("stats")
            subscription = payload.get("subscription")
            self._reuse_blocked = True
            if not self._persist_reuse_state(True):
                return
            self._reuse_blocked = False
            _logger.info("Kimi web quota refresh completed")
            self.login_state_changed.emit(True)
            self.quota_received.emit(stats, subscription)
            if self._login_dialog is not None:
                self._login_dialog.accept()
            return
        if kind == "unauthorized":
            _logger.warning("Kimi web quota refresh unauthorized")
            self._reuse_blocked = True
            self._persist_reuse_state(False)
            self.login_state_changed.emit(False)
            return
        message = payload.get("message")
        _logger.warning("Kimi web quota refresh failed")
        self.error_occurred.emit(
            message if isinstance(message, str) and message else "Kimi 会员额度刷新失败"
        )

    def _login_dialog_closed(self) -> None:
        self._login_dialog = None
