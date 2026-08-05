"""AACC-owned Bailian (Qwen Code token-plan) session using the native web view.

The personal token-plan page is a qiankun micro-frontend
(``bailian-tokenplan``) that renders a 5-hour and a 7-day window quotas.
Unlike opencode.ai (a SolidStart same-origin RPC) and kimi.com (a public
Connect gateway), the Bailian console exposes no stable same-origin quota
endpoint: data is loaded by the micro-frontend into React state and rendered
as DOM. This module therefore reads the rendered text — a page-injected
script captures the text snippets around the two window labels and bridges
them back through ``document.title``; the Python parser derives the numbers.

Snippets without any rendered percentage are reported as ``unauthorized``:
the anonymous/login view repeats the window labels in marketing copy, which
must not be mistaken for quota data. On macOS the Aliyun login flow is too
complex for this native view (new-window requests are dropped); the Chrome
CDP session (``qwen_chrome_session``) owns login there and this module is
the fallback path (also the Windows native path).
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

BRIDGE_PREFIX = "AACC_QWEN_QUOTA:"
BRIDGE_PAYLOAD_KEY = "__AACC_QWEN_QUOTA_PAYLOAD__"
REFRESH_TIMEOUT_MS = 60_000
LOGOUT_CLEANUP_TIMEOUT_MS = 10_000
_TK_PLAN_PATTERN = re.compile(r"/efm/subscription/token-plan/personal", re.IGNORECASE)
_logger = logging.getLogger("aacc.qwen_web_session")

_DEFAULT_URL = (
    "https://bailian.console.aliyun.com/cn-beijing?tab=plan#/efm/subscription/token-plan/personal"
)


def qwen_webview_user_data_path(config_dir: Path) -> Path:
    """Return AACC's writable Qwen session directory."""

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise FileProtectionError("LOCALAPPDATA is unavailable")
        return Path(local_app_data) / "AACC" / "qwen-web-session"
    return config_dir / "qwen-web-session"


def _is_token_plan_url(url: str) -> bool:
    if not url:
        return False
    parsed = QUrl(url)
    if parsed.host() != "bailian.console.aliyun.com":
        return False
    return bool(_TK_PLAN_PATTERN.search(parsed.toString()))


def qwen_dom_extract_script(url: str, generation: int) -> str:
    """Return a script that captures rendered token-plan usage text from the DOM.

    Locates the two window labels ("5 小时"/"5h" and "7 天"/"7d") in
    ``document.body.innerText`` and emits the text snippet of each window
    (sliced up to the next window label) through the title bridge. Snippets
    without any percentage mean the anonymous/login view is showing, which is
    reported as ``unauthorized`` instead of fake quota data. The Python
    parser derives ``percentage`` and ``resetSeconds`` from the text.
    """

    if not _is_token_plan_url(url):
        return ""

    template = r"""
 (() => {
   const prefix = __PREFIX__;
   const generation = __GEN__;
   let attempts = 0;
   const emit = (payload) => {
     document.title = prefix + JSON.stringify(payload);
   };
   const retry = () => {
     if (++attempts < 50) setTimeout(extract, 1000);
     else emit({kind: 'error', generation, message: 'DOM_TIMEOUT'});
   };
   const FIVE = /5\s*小时|5\s*h|5h/i;
   const SEVEN = /7\s*天|7\s*d|7d/i;
   const PCT = /(\d{1,3}(?:\.\d+)?)\s*%/;
   const sliceWindow = (lines, idx, stop) => {
     const out = [lines[idx]];
     for (let i = idx + 1; i < lines.length && out.length < 12; i++) {
       if (stop.test(lines[i])) break;
       out.push(lines[i]);
     }
     return out.join('\n');
   };
   const extract = () => {
     const text = document.body ? document.body.innerText : '';
     if (!text) { retry(); return; }
     const lines = text.split('\n').map(l => l.trim()).filter(l => l);
     const fiveIdx = lines.findIndex(l => FIVE.test(l));
     const sevenIdx = lines.findIndex(l => SEVEN.test(l));
     if (fiveIdx < 0 && sevenIdx < 0) { retry(); return; }
     const fiveText = fiveIdx >= 0 ? sliceWindow(lines, fiveIdx, SEVEN) : null;
     const weeklyText = sevenIdx >= 0 ? sliceWindow(lines, sevenIdx, FIVE) : null;
     if (!PCT.test(fiveText || '') && !PCT.test(weeklyText || '')) {
       emit({kind: 'unauthorized', generation, message: 'NO_USAGE_DATA'});
       return;
     }
     emit({
       kind: 'quota', generation,
       raw: {fiveHourText: fiveText, weeklyText: weeklyText}
     });
   };
   setTimeout(extract, 2500);
 })();
"""
    return template.replace("__PREFIX__", json.dumps(BRIDGE_PREFIX)).replace(
        "__GEN__", str(generation)
    )


class QwenWebSession(QObject):
    """Keep Bailian cookies in the platform web view; never handle the password."""

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
        self.storage_path = qwen_webview_user_data_path(config_dir)
        protect_directory(self.storage_path)
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self.workspace_url = _DEFAULT_URL
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
        self._may_reuse = False
        self._login_status_key = "qwen.web_starting"
        self._unsubscribe_language = self.language_manager.subscribe(
            self.retranslate_ui,
            component="qwen_web_session",
        )

    def set_workspace_url(self, url: str) -> None:
        self.workspace_url = url.strip()

    def _is_bailian_origin(self) -> bool:
        if not self.workspace_url:
            return False
        expected = QUrl(self.workspace_url).host()
        return bool(expected) and self.view.url().host() == expected

    def refresh(self) -> None:
        if not self.workspace_url:
            return
        if self._refreshing:
            return
        if not self._login_dialog_open and not self._may_reuse:
            return
        self._refreshing = True
        self._start_refresh_generation()
        self._start_refresh_watchdog()
        self._reload_workspace_url()

    def open_login(self, parent: QWidget | None = None) -> None:
        if not self.workspace_url:
            if parent is not None:
                QMessageBox.information(
                    parent,
                    "AACC",
                    self.language_manager.text("qwen.web_need_config"),
                )
            return
        if self._login_dialog is None:
            dialog = QDialog(parent)
            dialog.resize(960, 720)
            layout = QVBoxLayout(dialog)
            explanation = QLabel(self.language_manager.text("qwen.web_starting"))
            explanation.setWordWrap(True)
            layout.addWidget(explanation)
            container = QWidget.createWindowContainer(self.view, dialog)
            layout.addWidget(container, 1)
            dialog.finished.connect(self._login_dialog_closed)
            self._login_dialog = dialog
            self._login_container = container
            self._login_explanation_label = explanation
        if self._login_explanation_label is not None:
            self._login_explanation_label.setText(self.language_manager.text("qwen.web_starting"))
        self._login_dialog_open = True
        self._refreshing = True
        self._login_dialog.show()
        self._login_dialog.raise_()
        self._login_dialog.activateWindow()
        self._start_refresh_generation()
        self._start_refresh_watchdog()
        self._reload_workspace_url()

    def logout(self) -> bool:
        if not self.workspace_url:
            return True
        self._invalidate_refresh()
        self._may_reuse = False
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

    def _reload_workspace_url(self) -> None:
        if not self.workspace_url:
            return
        target = QUrl(self.workspace_url)
        current = self.view.url()
        _logger.info("Qwen quota refresh origin=%s://%s", current.scheme(), current.host())
        if current == target:
            self.view.reload()
        else:
            self.view.setUrl(target)

    def _run_fetch_script(self) -> None:
        script = qwen_dom_extract_script(self.workspace_url, self._refresh_generation)
        if not script:
            _logger.warning("Qwen DOM extract script empty; workspace url invalid")
            self._finish_refresh_with_error("refresh_failed")
            return
        _logger.info("Qwen DOM extract script running generation=%s", self._refresh_generation)
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
        _logger.warning("Qwen quota refresh error category=%s", category)
        self.error_occurred.emit(category)

    def _on_loading_changed(self, info: QWebViewLoadingInfo) -> None:
        if info.status() != QWebViewLoadingInfo.LoadStatus.Succeeded:
            return
        if self._logout_after_load:
            self._logout_after_load = False
            self._logout_cleanup_watchdog.stop()
            self._run_logout_cleanup()
            return
        if not self._is_bailian_origin():
            return
        self._run_fetch_script()

    def _on_title_changed(self, title: str) -> None:
        if not title.startswith(BRIDGE_PREFIX):
            return
        encoded = title[len(BRIDGE_PREFIX) :]
        try:
            payload = json.loads(encoded)
        except ValueError:
            _logger.warning("Qwen bridge title is not json: %.120s", encoded)
            return
        if not isinstance(payload, dict):
            _logger.warning("Qwen bridge title payload not a dict")
            return
        _logger.info(
            "Qwen bridge title received kind=%s generation=%s active=%s",
            payload.get("kind"),
            payload.get("generation"),
            self._active_refresh_generation,
        )
        if payload.get("generation") != self._active_refresh_generation:
            return
        self._handle_bridge(payload)

    def _handle_bridge(self, payload_text: object) -> None:
        try:
            payload = json.loads(payload_text) if isinstance(payload_text, str) else payload_text
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            _logger.warning(
                "Qwen bridge payload not a dict: %r (len=%s)",
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
            self._may_reuse = True
            raw = payload.get("raw")
            _logger.info("Qwen quota raw=%s", str(raw)[:500])
            self.quota_received.emit(raw)
            if self._login_dialog_open:
                self._close_login_dialog()
                self.login_state_changed.emit(True)
            return
        if kind == "unauthorized":
            self._refreshing = False
            self._refresh_watchdog.stop()
            self._active_refresh_generation = None
            self._may_reuse = False
            self.login_state_changed.emit(False)
            self.error_occurred.emit("unauthorized")
            return
        _logger.warning("Qwen bridge error message=%s", str(payload.get("message"))[:200])
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
