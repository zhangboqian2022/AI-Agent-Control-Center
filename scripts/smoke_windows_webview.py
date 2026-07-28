"""Run AACC's native WebView diagnostic from a Windows source checkout."""

from __future__ import annotations

import sys
from importlib import import_module

if sys.platform != "win32":
    print("AACC_WEBVIEW_SMOKE category=unsupported-platform", file=sys.stderr)
    raise SystemExit(2)

webview_smoke = import_module("aacc.webview_smoke")
raise SystemExit(webview_smoke.run_native_webview_smoke())
