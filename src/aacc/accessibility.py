from __future__ import annotations

import subprocess
import sys
from typing import Any


def _load_quartz() -> Any:
    import Quartz  # type: ignore  # import-not-found off-mac, import-untyped with pyobjc

    return Quartz


def is_accessibility_trusted(prompt: bool = False) -> bool:
    if sys.platform == "win32":
        # Windows 的 SendInput 注入不需要辅助功能授权。
        return True
    try:
        quartz = _load_quartz()
        options = {quartz.kAXTrustedCheckOptionPrompt: prompt}
        return bool(quartz.AXIsProcessTrustedWithOptions(options))
    except (AttributeError, ImportError, OSError):
        return False


def open_accessibility_settings() -> None:
    if sys.platform == "win32":
        return
    try:
        subprocess.run(
            [
                "/usr/bin/open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
