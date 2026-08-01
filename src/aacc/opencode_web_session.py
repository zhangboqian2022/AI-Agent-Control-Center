"""AACC-owned opencode.ai session using the operating system's native web view.

The workspace page renders Go-plan usage through the same-origin ``/_server``
RPC ``subscription.get``. Refreshes run a fetch script inside the page so the
session cookie authenticates the request; results arrive through the
title-bridge used by the Kimi session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebView import QWebView, QWebViewLoadingInfo
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QVBoxLayout, QWidget

from aacc.file_security import FileProtectionError, protect_directory
from aacc.i18n import ZH_CN, LanguageManager

BRIDGE_PREFIX = "AACC_OPENCODE_QUOTA:"
BRIDGE_PAYLOAD_KEY = "__AACC_OPENCODE_QUOTA_PAYLOAD__"
SERVER_FN_HASH = "7abeebee372f304e050aaaf92be863f4a86490e382f8c79db68fd94040d691b4"
REFRESH_TIMEOUT_MS = 60_000
LOGOUT_CLEANUP_TIMEOUT_MS = 10_000
_workspace_id_pattern = re.compile(r"/workspace/([A-Za-z0-9_-]+)")
_logger = logging.getLogger("aacc.opencode_web_session")


def opencode_webview_user_data_path(config_dir: Path) -> Path:
    """Return AACC's writable opencode session directory."""

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise FileProtectionError("LOCALAPPDATA is unavailable")
        return Path(local_app_data) / "AACC" / "opencode-web-session"
    return config_dir / "opencode-web-session"


def workspace_id_from_url(url: str) -> str | None:
    match = _workspace_id_pattern.search(url)
    return match.group(1) if match else None


def opencode_usage_fetch_script(url: str, generation: int) -> str:
    """Return the same-origin usage request used by the native web view."""

    workspace_id = workspace_id_from_url(url)
    if workspace_id is None:
        return ""
    body = json.dumps(
        {
            "t": {"t": 15, "l": 1, "c": "Array", "a": [{"t": 2, "s": workspace_id}]},
            "f": 31,
            "m": [],
        },
        separators=(",", ":"),
    )
    return f"""
(() => {{
  const prefix = {json.dumps(BRIDGE_PREFIX)};
  const payloadKey = {json.dumps(BRIDGE_PAYLOAD_KEY)};
  const generation = {generation};
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 15000);
  const emit = (payload) => {{
    window[payloadKey] = JSON.stringify(payload);
    document.title = prefix + generation + ':' + (payload.kind || 'unknown') + ':' + Date.now() + ':' + Math.random();
  }};
  const findSubscription = (node, depth) => {{
    if (!node || typeof node !== 'object' || depth > 6) return null;
    if (node.rollingUsage || node.weeklyUsage || node.monthlyUsage) return node;
    for (const key in node) {{
      if (Object.prototype.hasOwnProperty.call(node, key)) {{
        const found = findSubscription(node[key], depth + 1);
        if (found) return found;
      }}
    }}
    return null;
  }};
  const parse = (text) => {{
    const trimmed = String(text || '').trim();
    if (!trimmed) return null;
    if (trimmed.charAt(0) === '{{') {{
      try {{ return JSON.parse(trimmed); }} catch (_) {{ return null; }}
    }}
    const equals = trimmed.indexOf('=');
    const expression = equals === -1 ? trimmed : trimmed.slice(equals + 1);
    try {{ return eval(expression); }} catch (_) {{ return null; }}
  }};
  fetch('/_server', {{
    method: 'POST',
    headers: {{
      'Content-Type': 'application/json',
      'X-Server-Id': {json.dumps(SERVER_FN_HASH)},
      'X-Server-Instance': 'server-fn:1'
    }},
    credentials: 'include',
    signal: controller.signal,
    body: {json.dumps(body)}
  }}).then(async (response) => {{
    if (response.status === 401 || response.status === 403) {{
      throw new Error('UNAUTHORIZED:' + response.status);
    }}
    if (!response.ok) {{
      throw new Error('HTTP:' + response.status);
    }}
    const subscription = findSubscription(parse(await response.text()), 0);
    if (!subscription) {{
      throw new Error('PARSE_FAILED');
    }}
    emit({{kind: 'quota', generation, raw: {{subscription}}}});
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


class OpenCodeWebSession(QObject):
    """Keep opencode.ai cookies in the platform web view; never handle the password."""

    login_state_changed = Signal(bool)
    quota_received = Signal(object)
    error_occurred = Signal(str)

    def __init__(
        self,
        config_dir: Path,
        parent: QObject | None = None,
        *,
        language_manager: LanguageManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage_path = opencode_webview_user_data_path(config_dir)
        protect_directory(self.storage_path)
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self.workspace_url = ""
        self.view = QWebView()
        self.view.settings().setAttribute(self.view.settings().WebAttribute.JavaScriptEnabled, True)
        self.view.settings().setAttribute(
            self.view.settings().WebAttribute.LocalStorageEnabled, True
        )
        self.view.loadingChanged.connect(self._on_loading_changed)
        self.view.titleChanged.connect(self._on_title_changed)
        self._refreshing = False
        self._refresh_generation = 0
        self._active_refresh_generation: int | None = None
        self._refresh_watchdog = QTimer(self)
        self._refresh_watchdog.setSingleShot(True)
        self._refresh_watchdog.timeout.connect(self._refresh_watchdog_timeout)
        self._logout_after_load = False
        self._logout_cleanup_watchdog = QTimer(self)
        self._logout_cleanup_watchdog.setSingleShot(True)
        self._logout_cleanup_watchdog.timeout.connect(self._logout_cleanup_watchdog_timeout)
        self._login_dialog: QDialog | None = None
        self._login_container: QWidget | None = None
        self._login_explanation_label: QLabel | None = None
        self._login_dialog_open = False
        self._login_status_key = "opencode.web_starting"
        self._unsubscribe_language = self.language_manager.subscribe(
            self.retranslate_ui,
            component="opencode_web_session",
        )

    def set_workspace_url(self, url: str) -> None:
        self.workspace_url = url.strip()

    def _is_opencode_origin(self) -> bool:
        if not self.workspace_url:
            return False
        expected = QUrl(self.workspace_url).host()
        return bool(expected) and self.view.url().host() == expected

    def refresh(self) -> None:
        if not self.workspace_url:
            return
        if self._refreshing:
            return
        self._refreshing = True
        self._start_refresh_generation()
        self._start_refresh_watchdog()
        if self._login_dialog_open or self.view.url().isEmpty() or not self._is_opencode_origin():
            _logger.info(
                "OpenCode quota refresh navigating to workspace url=%s",
                self.view.url().toString(),
            )
            self._load_workspace_url()
            return
        self._run_fetch_script()

    def open_login(self, parent: QWidget | None = None) -> None:
        if not self.workspace_url:
            if parent is not None:
                QMessageBox.information(
                    parent,
                    "AACC",
                    self.language_manager.text("opencode.web_need_config"),
                )
            return
        if self._login_dialog is None:
            dialog = QDialog(parent)
            dialog.resize(960, 720)
            layout = QVBoxLayout(dialog)
            explanation = QLabel(self.language_manager.text("opencode.web_starting"))
            explanation.setWordWrap(True)
            layout.addWidget(explanation)
            container = QWidget.createWindowContainer(self.view, dialog)
            layout.addWidget(container, 1)
            dialog.finished.connect(self._login_dialog_closed)
            self._login_dialog = dialog
            self._login_container = container
            self._login_explanation_label = explanation
        if self._login_explanation_label is not None:
            self._login_explanation_label.setText(
                self.language_manager.text("opencode.web_starting")
            )
        self._login_dialog_open = True
        self._refreshing = True
        self._login_dialog.show()
        self._login_dialog.raise_()
        self._login_dialog.activateWindow()
        self._start_refresh_generation()
        self._start_refresh_watchdog()
        self._load_workspace_url()

    def logout(self) -> bool:
        if not self.workspace_url:
            return True
        self._invalidate_refresh()
        self._logout_after_load = True
        self._logout_cleanup_watchdog.start(LOGOUT_CLEANUP_TIMEOUT_MS)
        self.view.setUrl(QUrl(self.workspace_url))
        self.login_state_changed.emit(False)
        return True

    def close(self) -> None:
        self._refreshing = False
        self._refresh_watchdog.stop()
        self._logout_cleanup_watchdog.stop()
        self._login_dialog_open = False
        if self._login_dialog is not None:
            self._login_dialog.close()
            self._login_dialog.deleteLater()
            self._login_dialog = None
        self._login_container = None
        self._login_explanation_label = None
        self._unsubscribe_language()
        self.view.deleteLater()

    def retranslate_ui(self) -> None:
        if self._login_explanation_label is not None:
            self._login_explanation_label.setText(
                self.language_manager.text(self._login_status_key)
            )

    def _load_workspace_url(self) -> None:
        if not self.workspace_url:
            return
        self.view.setUrl(QUrl(self.workspace_url))

    def _run_fetch_script(self) -> None:
        script = opencode_usage_fetch_script(self.workspace_url, self._refresh_generation)
        if not script:
            _logger.warning("OpenCode quota fetch script empty; workspace id missing")
            self._finish_refresh_with_error("refresh_failed")
            return
        _logger.info("OpenCode quota fetch script running generation=%s", self._refresh_generation)
        self._start_refresh_watchdog()
        self.view.runJavaScript(script, lambda _result: None)

    def _start_refresh_generation(self) -> None:
        self._refresh_generation += 1
        self._active_refresh_generation = self._refresh_generation

    def _start_refresh_watchdog(self) -> None:
        self._refresh_watchdog.start(REFRESH_TIMEOUT_MS)

    def _invalidate_refresh(self) -> None:
        self._refresh_generation += 1
        self._active_refresh_generation = None
        self._refreshing = False
        self._refresh_watchdog.stop()

    def _refresh_watchdog_timeout(self) -> None:
        self._finish_refresh_with_error("refresh_timeout")

    def _logout_cleanup_watchdog_timeout(self) -> None:
        self._logout_after_load = False
        self._logout_cleanup_watchdog.stop()

    def _finish_refresh_with_error(self, category: str) -> None:
        self._refreshing = False
        self._refresh_watchdog.stop()
        _logger.warning("OpenCode quota refresh error category=%s", category)
        self.error_occurred.emit(category)

    def _on_loading_changed(self, info: QWebViewLoadingInfo) -> None:
        if info.status() != QWebViewLoadingInfo.LoadStatus.Succeeded:
            return
        if self._logout_after_load:
            self._logout_after_load = False
            self._logout_cleanup_watchdog.stop()
            self._run_logout_cleanup()
            return
        if not self._is_opencode_origin():
            return
        self._run_fetch_script()

    def _on_title_changed(self, title: str) -> None:
        if not title.startswith(BRIDGE_PREFIX):
            return
        try:
            generation = int(title[len(BRIDGE_PREFIX) :].split(":", 1)[0])
        except ValueError:
            return
        _logger.info(
            "OpenCode bridge title received generation=%s active=%s",
            generation,
            self._active_refresh_generation,
        )
        if generation != self._active_refresh_generation:
            return

        def dispatch(payload_text: object) -> None:
            self._handle_bridge(payload_text)

        script = (
            "(() => {"
            f"const key = {json.dumps(BRIDGE_PAYLOAD_KEY)};"
            "const value = window[key] || '';"
            "delete window[key];"
            "return value;"
            "})()"
        )
        self.view.runJavaScript(script, dispatch)

    def _handle_bridge(self, payload_text: object) -> None:
        try:
            payload = json.loads(payload_text) if isinstance(payload_text, str) else payload_text
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            _logger.warning(
                "OpenCode bridge payload not a dict: %r (len=%s)",
                str(payload_text)[:200],
                len(str(payload_text)) if payload_text is not None else -1,
            )
            self._finish_refresh_with_error("refresh_failed")
            return
        if payload.get("generation") != self._active_refresh_generation:
            return
        kind = payload.get("kind")
        if kind == "quota":
            self._refreshing = False
            self._refresh_watchdog.stop()
            self._active_refresh_generation = None
            self.quota_received.emit(payload.get("raw"))
            if self._login_dialog_open:
                self._close_login_dialog()
                self.login_state_changed.emit(True)
            return
        if kind == "unauthorized":
            self._refreshing = False
            self._refresh_watchdog.stop()
            self._active_refresh_generation = None
            self.login_state_changed.emit(False)
            self.error_occurred.emit("unauthorized")
            return
        self._finish_refresh_with_error("refresh_failed")

    def _close_login_dialog(self) -> None:
        self._login_dialog_open = False
        if self._login_dialog is not None:
            self._login_dialog.close()

    def _login_dialog_closed(self, _result: object) -> None:
        if not self._login_dialog_open:
            return
        self._login_dialog_open = False
        self._invalidate_refresh()

    def _run_logout_cleanup(self) -> None:
        self.view.runJavaScript(
            "try { localStorage.clear(); sessionStorage.clear(); return true; } "
            "catch (_) { return false; }",
            lambda _result: None,
        )
        self.view.deleteAllCookies()
