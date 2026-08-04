"""AACC-owned Bailian (Qwen Code token-plan) session using the native web view.

The personal token-plan page is a qiankun micro-frontend
(``bailian-tokenplan``) that renders a 5-hour and a 7-day window quotas.
Unlike opencode.ai (a SolidStart same-origin RPC) and kimi.com (a public
Connect gateway), the Bailian console exposes no stable same-origin quota
endpoint: data is loaded by the micro-frontend into React state and rendered
as DOM. This module therefore reads the rendered text the way
``opencode_web_session`` does — a page-injected script parses
``document.body.innerText`` for the two labeled windows and bridges the
captured values back through ``document.title``.
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
    """Return a script that extracts rendered token-plan usage from the DOM.

    Reads the rendered ``document.body.innerText`` and locates the two labeled
    windows ("5 小时"/"5h" and "7 天"/"7d"). It is intentionally loose — the
    skeleton accepts either Chinese or English labels and emits the matched
    text segment so the Python parser can derive ``percentage`` and
    ``resetSeconds``. The regex is tuned iteratively against the live page
    (opencode-dom strategy).
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
   const parseResetSeconds = (text) => {
     let s = 0;
     const d = text.match(/(\d+)\s*(天|days?|day)/);
     const h = text.match(/(\d+)\s*(小时|hours?|hour)/);
     const m = text.match(/(\d+)\s*(分钟|minutes?|minute|min)/);
     if (d) s += parseInt(d[1]) * 86400;
     if (h) s += parseInt(h[1]) * 3600;
     if (m) s += parseInt(m[1]) * 60;
     return s > 0 ? s : null;
   };
   const extract5h = () => {
     const text = document.body ? document.body.innerText : '';
     if (!text) return null;
     const lines = text.split('\n').map(l => l.trim());
     const idx = lines.findIndex(l => /5\s*小时|5\s*h|5h/i.test(l));
     if (idx < 0) return null;
     const snippet = lines.slice(idx, idx + 6).join('\n');
     const match = snippet.match(/(\d{1,3})\s*%/);
     return {
       percentage: match ? parseInt(match[1]) : null,
       resetSeconds: parseResetSeconds(snippet),
     };
   };
   const extract7d = () => {
     const text = document.body ? document.body.innerText : '';
     if (!text) return null;
     const lines = text.split('\n').map(l => l.trim());
     const idx = lines.findIndex(l => /7\s*天|7\s*d|7d/i.test(l));
     if (idx < 0) return null;
     const snippet = lines.slice(idx, idx + 6).join('\n');
     const match = snippet.match(/(\d{1,3})\s*%/);
     return {
       percentage: match ? parseInt(match[1]) : null,
       resetSeconds: parseResetSeconds(snippet),
     };
   };
   const extract = () => {
     const fiveHour = extract5h();
     const sevenDay = extract7d();
     if (!fiveHour && !sevenDay) {
       if (++attempts < 50) setTimeout(extract, 1000);
       else emit({kind: 'error', generation, message: 'DOM_TIMEOUT'});
       return;
     }
     emit({
       kind: 'quota', generation, raw: {
         fiveHour: fiveHour || null,
         sevenDay: sevenDay || null
       }
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
        if self._login_dialog_open or self.view.url().isEmpty() or not self._is_bailian_origin():
            url = self.view.url()
            _logger.info(
                "Qwen quota refresh navigating to bailian origin=%s://%s",
                url.scheme(),
                url.host(),
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
        self._load_workspace_url()

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

    def _load_workspace_url(self) -> None:
        if not self.workspace_url:
            return
        self.view.setUrl(QUrl(self.workspace_url))

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
            _logger.info("Qwen quota raw=%s", str(raw)[:300])
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
