"""Run AACC's native WebView diagnostic from a Windows source checkout."""

from __future__ import annotations

import sys

if sys.platform != "win32":
    print("AACC_WEBVIEW_SMOKE category=unsupported-platform", file=sys.stderr)
    raise SystemExit(2)

from aacc.webview_smoke import run_native_webview_smoke


raise SystemExit(run_native_webview_smoke())
