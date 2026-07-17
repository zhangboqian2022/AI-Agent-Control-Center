import subprocess

import pytest

from aacc.automation import AutomationError, MacAutomation, applescript_quote
from aacc.config import default_config


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def test_applescript_quote_escapes_quotes_backslashes_and_newlines() -> None:
    quoted = applescript_quote('A "window"\\name\nnext')
    assert quoted == '"A \\"window\\"\\\\name\\nnext"'


def test_focus_uses_argument_array_and_escaped_window_title() -> None:
    config = default_config()
    task = config.tasks[0]
    task.terminal.window_title = 'AACC "TASK"'
    recorder = Recorder()
    automation = MacAutomation(config, runner=recorder)
    automation.focus(task)
    assert recorder.calls[0][0:2] == ["/usr/bin/osascript", "-e"]
    assert '\\"TASK\\"' in recorder.calls[0][2]


def test_send_key_focuses_before_injecting_whitelisted_key() -> None:
    config = default_config()
    recorder = Recorder()
    automation = MacAutomation(config, runner=recorder)
    automation.send_key(config.tasks[0], "ENTER")
    assert len(recorder.calls) == 2
    assert "activate" in recorder.calls[0][2]
    assert "key code 36" in recorder.calls[1][2]


def test_send_key_rejects_unlisted_key() -> None:
    config = default_config()
    automation = MacAutomation(config, runner=Recorder())
    with pytest.raises(AutomationError, match="not allowed"):
        automation.send_key(config.tasks[0], "CMD_Q")


def test_keyboard_injection_can_be_disabled() -> None:
    config = default_config()
    config.app.keyboard_injection = False
    automation = MacAutomation(config, runner=Recorder())
    with pytest.raises(AutomationError, match="disabled"):
        automation.send_text(config.tasks[0], "continue")


def test_failed_focus_stops_before_key_injection() -> None:
    config = default_config()

    def fail(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="window missing")

    automation = MacAutomation(config, runner=fail)
    with pytest.raises(AutomationError, match="window missing"):
        automation.send_key(config.tasks[0], "ENTER")

