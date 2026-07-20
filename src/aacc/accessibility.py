from __future__ import annotations

import subprocess
from typing import Any


def _load_quartz() -> Any:
    import Quartz  # type: ignore[import-untyped]

    return Quartz


def is_accessibility_trusted(prompt: bool = False) -> bool:
    try:
        quartz = _load_quartz()
        options = {quartz.kAXTrustedCheckOptionPrompt: prompt}
        return bool(quartz.AXIsProcessTrustedWithOptions(options))
    except (AttributeError, ImportError, OSError):
        return False


def open_accessibility_settings() -> None:
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
