from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from aacc.models import AppConfig, TaskConfig
from aacc.security import redact

if TYPE_CHECKING:
    from aacc.automation_windows import WindowsAutomation

Runner = Callable[..., subprocess.CompletedProcess[str]]


AutomationErrorCategory = Literal[
    "timeout",
    "unavailable",
    "cancelled",
    "unsupported_operation",
    "executor_closed",
    "queue_full",
    "window_not_found",
    "window_focus_failed",
    "app_unconfigured",
    "injection_disabled",
    "accessibility_required",
    "key_not_allowed",
    "text_invalid",
    "text_nul",
    "voice_hotkey_unsupported",
]
AUTOMATION_ERROR_CATEGORIES = frozenset(
    {
        "timeout",
        "unavailable",
        "cancelled",
        "unsupported_operation",
        "executor_closed",
        "queue_full",
        "window_not_found",
        "window_focus_failed",
        "app_unconfigured",
        "injection_disabled",
        "accessibility_required",
        "key_not_allowed",
        "text_invalid",
        "text_nul",
        "voice_hotkey_unsupported",
    }
)


class AutomationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: AutomationErrorCategory | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category


KEY_CODES = {
    "ENTER": 36,
    "ESC": 53,
    "UP": 126,
    "DOWN": 125,
    "LEFT": 123,
    "RIGHT": 124,
    "1": 18,
    "2": 19,
}

TEXT_SCRIPT = 'on run argv\ntell application "System Events" to keystroke (item 1 of argv)\nend run'


def applescript_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")
    )
    return f'"{escaped}"'


class MacAutomation:
    def __init__(
        self,
        config: AppConfig,
        *,
        runner: Runner = subprocess.run,
        sleeper: Callable[[float], Any] = time.sleep,
        accessibility_trusted: Callable[[], bool] = lambda: True,
    ) -> None:
        self.config = config
        self._runner = runner
        self._sleep = sleeper
        self._accessibility_trusted = accessibility_trusted
        self._lock = threading.RLock()

    def _run(self, args: list[str]) -> str:
        try:
            completed = self._runner(
                args,
                capture_output=True,
                text=True,
                timeout=self.config.app.automation_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AutomationError("Desktop automation timed out", category="timeout") from error
        except OSError as error:
            raise AutomationError(
                "Desktop automation is unavailable",
                category="unavailable",
            ) from error
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "automation failed"
            raise AutomationError(redact(detail)[:500])
        return completed.stdout.strip()

    def _terminal_script(self, task: TaskConfig) -> str:
        terminal = task.terminal
        title = terminal.window_title or terminal.tab_title
        if terminal.type == "iterm2":
            lines = ['tell application "iTerm2"', "activate"]
            if title:
                quoted = applescript_quote(title)
                lines.extend(
                    [
                        f"set matchingWindows to every window whose name contains {quoted}",
                        "if count of matchingWindows is not 1 then error number -128",
                        "set targetWindow to item 1 of matchingWindows",
                        "set index of targetWindow to 1",
                    ]
                )
            lines.append("end tell")
            return "\n".join(lines)
        bundle_id = terminal.app_bundle_id or "com.apple.Terminal"
        lines = [f"tell application id {applescript_quote(bundle_id)}", "activate"]
        lines.extend(
            [
                "try",
                "set miniaturized of windows to false",
                "end try",
            ]
        )
        if title:
            lines.extend(
                [
                    "set matchingWindows to every window whose name contains "
                    + applescript_quote(title),
                    "if count of matchingWindows is not 1 then error number -128",
                    "set targetWindow to item 1 of matchingWindows",
                    "set miniaturized of targetWindow to false",
                    "set index of targetWindow to 1",
                ]
            )
        lines.append("end tell")
        return "\n".join(lines)

    def _mac_app_script(self, bundle_id: str) -> str:
        return "\n".join(
            [
                f"tell application id {applescript_quote(bundle_id)}",
                "activate",
                "try",
                "set miniaturized of windows to false",
                "end try",
                "end tell",
            ]
        )

    @staticmethod
    def _target_bundle_id(task: TaskConfig) -> str | None:
        terminal = task.terminal
        if terminal.type == "iterm2":
            return "com.googlecode.iterm2"
        if terminal.type == "terminal_app":
            return terminal.app_bundle_id or "com.apple.Terminal"
        return terminal.app_bundle_id

    def _frontmost_check_script(self, task: TaskConfig) -> str:
        terminal = task.terminal
        bundle_id = self._target_bundle_id(task)
        if not bundle_id:
            raise AutomationError(
                "No app bundle identifier is configured",
                category="app_unconfigured",
            )
        lines = []
        title = terminal.window_title or terminal.tab_title
        if title and terminal.type in {"terminal_app", "iterm2"}:
            quoted_title = applescript_quote(title)
            lines.extend(
                [
                    f"tell application id {applescript_quote(bundle_id)}",
                    (
                        f"if (count of (every window whose name contains {quoted_title})) "
                        "is not 1 then error number -128"
                    ),
                    f"if (name of window 1 does not contain {quoted_title}) then error number -128",
                    "end tell",
                ]
            )
        lines.extend(
            [
                'tell application "System Events"',
                "set frontmostProcess to first application process whose frontmost is true",
                (
                    f"if (bundle identifier of frontmostProcess) is not "
                    f"{applescript_quote(bundle_id)} then error number -128"
                ),
                "end tell",
            ]
        )
        return "\n".join(lines)

    def _verify_frontmost_unlocked(self, task: TaskConfig) -> None:
        self._run(["/usr/bin/osascript", "-e", self._frontmost_check_script(task)])

    def _focus_unlocked(self, task: TaskConfig) -> str:
        terminal = task.terminal
        if terminal.type in {"terminal_app", "iterm2"}:
            self._run(["/usr/bin/osascript", "-e", self._terminal_script(task)])
        else:
            bundle_id = terminal.app_bundle_id
            if not bundle_id:
                raise AutomationError(
                    "No app bundle identifier is configured",
                    category="app_unconfigured",
                )
            self._run(["/usr/bin/osascript", "-e", self._mac_app_script(bundle_id)])
        return f"已聚焦 {task.name}"

    @staticmethod
    def _check_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AutomationError(
                "Desktop automation was cancelled",
                category="cancelled",
            )

    def focus(self, task: TaskConfig, *, cancel_event: threading.Event | None = None) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            return self._focus_unlocked(task)

    def _ensure_injection(self) -> None:
        if not self.config.app.keyboard_injection:
            raise AutomationError(
                "Keyboard injection is disabled in AACC settings",
                category="injection_disabled",
            )
        if not self._accessibility_trusted():
            raise AutomationError(
                "Accessibility permission is required",
                category="accessibility_required",
            )

    def send_key(
        self,
        task: TaskConfig,
        key: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            self._ensure_injection()
            normalized = key.upper()
            if normalized not in {*KEY_CODES, "CTRL_C"}:
                raise AutomationError(
                    f"Key {normalized} is not allowed",
                    category="key_not_allowed",
                )
            self._focus_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            self._verify_frontmost_unlocked(task)
            if normalized == "CTRL_C":
                statement = 'tell application "System Events" to keystroke "c" using control down'
            else:
                statement = f'tell application "System Events" to key code {KEY_CODES[normalized]}'
            self._run(["/usr/bin/osascript", "-e", statement])
            return f"已发送 {normalized}"

    def send_text(
        self,
        task: TaskConfig,
        text: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            self._ensure_injection()
            if not text or len(text) > 2000:
                raise AutomationError(
                    "Text must contain 1 to 2000 characters",
                    category="text_invalid",
                )
            if "\0" in text:
                raise AutomationError(
                    "Text must not contain NUL",
                    category="text_nul",
                )
            self._focus_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            self._verify_frontmost_unlocked(task)
            self._run(["/usr/bin/osascript", "-e", TEXT_SCRIPT, "--", text])
            return "文本已发送"

    def start_voice(self, task: TaskConfig, *, cancel_event: threading.Event | None = None) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            self._ensure_injection()
            self._focus_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._sleep(self.config.voice.voice_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            self._verify_frontmost_unlocked(task)
            if self.config.voice.hotkey.upper() != "FN_FN":
                raise AutomationError(
                    "V1.0 voice hotkey currently supports FN_FN",
                    category="voice_hotkey_unsupported",
                )
            statement = (
                'tell application "System Events" to key code 63\n'
                "delay 0.12\n"
                'tell application "System Events" to key code 63'
            )
            self._run(["/usr/bin/osascript", "-e", statement])
            return "已触发系统听写"


def create_automation(
    config: AppConfig,
    *,
    accessibility_trusted: Callable[[], bool] = lambda: True,
) -> MacAutomation | WindowsAutomation:
    """Platform dispatch for the desktop automation controller."""
    if sys.platform == "win32":
        from aacc.automation_windows import WindowsAutomation

        return WindowsAutomation(config, accessibility_trusted=accessibility_trusted)
    return MacAutomation(config, accessibility_trusted=accessibility_trusted)
