"""Diagnostic for AACC's packaged native Windows WebView backend."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebView import QtWebView, QWebView, QWebViewLoadingInfo
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

SMOKE_TIMEOUT_MS = 30_000
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXPECTED_JAVASCRIPT_RESULT = "aacc-native-webview-ok"
SMOKE_RESULT_PATH_ENV = "AACC_WEBVIEW_SMOKE_RESULT_PATH"
SMOKE_RESULT_CATEGORIES = frozenset(
    {
        "success",
        "timeout",
        "load-failed",
        "unexpected-javascript-result",
        "evidence-write-failed",
        "invalid-category",
    }
)
INLINE_HTML = f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>AACC native WebView smoke</title></head>
<body><script>window.aaccSmokeResult = {EXPECTED_JAVASCRIPT_RESULT!r};</script></body></html>"""


def _record_result(category: str) -> bool:
    result_path = os.environ.get(SMOKE_RESULT_PATH_ENV)
    if not result_path:
        return True
    safe_category = category if category in SMOKE_RESULT_CATEGORIES else "invalid-category"
    try:
        path = Path(result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"AACC_WEBVIEW_SMOKE category={safe_category}\n",
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


class NativeWebViewSmoke:
    """Run one bounded, deterministic WebView load and JavaScript round trip."""

    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._exit_code = EXIT_FAILURE
        self._finished = False
        self._dialog = QDialog()
        self._dialog.setWindowTitle("AACC native WebView smoke")
        self._dialog.resize(640, 360)
        layout = QVBoxLayout(self._dialog)
        self._view = QWebView()
        self._container = QWidget.createWindowContainer(self._view, self._dialog)
        layout.addWidget(self._container)
        self._timeout = QTimer(self._dialog)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._fail("timeout"))
        self._view.loadingChanged.connect(self._loading_changed)

    def run(self) -> int:
        self._dialog.show()
        self._timeout.start(SMOKE_TIMEOUT_MS)
        QTimer.singleShot(0, self._load_inline_html)
        self._app.exec()
        return self._exit_code

    def _load_inline_html(self) -> None:
        self._view.loadHtml(INLINE_HTML, QUrl("https://aacc.invalid/"))

    def _loading_changed(self, info: QWebViewLoadingInfo) -> None:
        status = info.status()
        if status is QWebViewLoadingInfo.LoadStatus.Failed:
            self._fail("load-failed")
        elif status is QWebViewLoadingInfo.LoadStatus.Succeeded:
            self._view.runJavaScript("window.aaccSmokeResult", self._javascript_finished)

    def _javascript_finished(self, result: object) -> None:
        if self._finished:
            return
        if result != EXPECTED_JAVASCRIPT_RESULT:
            self._fail("unexpected-javascript-result")
            return
        if not _record_result("success"):
            self._fail("evidence-write-failed")
            return
        self._exit_code = EXIT_SUCCESS
        self._finish()

    def _fail(self, category: str) -> None:
        if self._finished:
            return
        print(f"AACC_WEBVIEW_SMOKE category={category}", file=sys.stderr)
        _record_result(category)
        self._exit_code = EXIT_FAILURE
        self._finish()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._timeout.stop()
        self._dialog.close()
        self._app.quit()


def run_native_webview_smoke() -> int:
    """Initialize the native backend before QApplication, then run the diagnostic."""

    QtWebView.initialize()
    app = QApplication(sys.argv)
    return NativeWebViewSmoke(app).run()
