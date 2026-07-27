"""Collect only the operating system's native Qt WebView backend.

PySide6 ships both the native backend (WKWebView on macOS, WebView2 on
Windows) and a QtWebEngine fallback. AACC deliberately excludes the fallback
because it increases the application bundle by hundreds of megabytes.
"""

from pathlib import Path

from PyInstaller.utils.hooks.qt import pyside6_library_info

hiddenimports, binaries, datas = pyside6_library_info.collect_module("PySide6.QtWebView")
binaries = [
    (source, destination)
    for source, destination in binaries
    if "webengine" not in Path(source).name.lower()
]
