from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Any

from aacc.models import AppConfig, TaskConfig

Runner = Callable[..., subprocess.CompletedProcess[str]]


class AutomationError(RuntimeError):
    pass


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


def applescript_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


class MacAutomation:
    def __init__(
        self,
        config: AppConfig,
        *,
        runner: Runner = subprocess.run,
        sleeper: Callable[[float], Any] = time.sleep,
    ) -> None:
        self.config = config
        self._runner = runner
        self._sleep = sleeper

    def _run(self, args: list[str]) -> str:
        completed = self._runner(
            args,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "automation failed"
            raise AutomationError(detail)
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
                        "repeat with targetWindow in windows",
                        f"if name of targetWindow contains {quoted} then",
                        "set index of targetWindow to 1",
                        "exit repeat",
                        "end if",
                        "end repeat",
                    ]
                )
            lines.append("end tell")
            return "\n".join(lines)
        bundle_id = terminal.app_bundle_id or "com.apple.Terminal"
        lines = [f"tell application id {applescript_quote(bundle_id)}", "activate"]
        if title:
            lines.extend(
                [
                    f"set targetWindow to first window whose name contains {applescript_quote(title)}",
                    "set index of targetWindow to 1",
                ]
            )
        lines.append("end tell")
        return "\n".join(lines)

    def focus(self, task: TaskConfig) -> str:
        terminal = task.terminal
        if terminal.type in {"terminal_app", "iterm2"}:
            self._run(["/usr/bin/osascript", "-e", self._terminal_script(task)])
        else:
            bundle_id = terminal.app_bundle_id
            if not bundle_id:
                raise AutomationError("No app bundle identifier is configured")
            self._run(["/usr/bin/open", "-b", bundle_id])
        return f"已聚焦 {task.name}"

    def _ensure_injection(self) -> None:
        if not self.config.app.keyboard_injection:
            raise AutomationError("Keyboard injection is disabled in AACC settings")

    def send_key(self, task: TaskConfig, key: str) -> str:
        self._ensure_injection()
        normalized = key.upper()
        if normalized not in {*KEY_CODES, "CTRL_C"}:
            raise AutomationError(f"Key {normalized} is not allowed")
        self.focus(task)
        self._sleep(self.config.voice.focus_delay_ms / 1000)
        if normalized == "CTRL_C":
            statement = 'tell application "System Events" to keystroke "c" using control down'
        else:
            statement = f'tell application "System Events" to key code {KEY_CODES[normalized]}'
        self._run(["/usr/bin/osascript", "-e", statement])
        return f"已发送 {normalized}"

    def send_text(self, task: TaskConfig, text: str) -> str:
        self._ensure_injection()
        if not text or len(text) > 2000:
            raise AutomationError("Text must contain 1 to 2000 characters")
        self.focus(task)
        self._sleep(self.config.voice.focus_delay_ms / 1000)
        statement = (
            'tell application "System Events" to keystroke '
            f"{applescript_quote(text)}"
        )
        self._run(["/usr/bin/osascript", "-e", statement])
        return "文本已发送"

    def start_voice(self, task: TaskConfig) -> str:
        self._ensure_injection()
        self.focus(task)
        self._sleep(self.config.voice.focus_delay_ms / 1000)
        self._sleep(self.config.voice.voice_delay_ms / 1000)
        if self.config.voice.hotkey.upper() != "FN_FN":
            raise AutomationError("V1.0 voice hotkey currently supports FN_FN")
        statement = (
            'tell application "System Events" to key code 63\n'
            "delay 0.12\n"
            'tell application "System Events" to key code 63'
        )
        self._run(["/usr/bin/osascript", "-e", statement])
        return "已触发系统听写"

