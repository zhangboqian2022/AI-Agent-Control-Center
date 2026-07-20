import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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


def test_send_control_c_uses_control_modifier() -> None:
    config = default_config()
    recorder = Recorder()
    MacAutomation(config, runner=recorder, sleeper=lambda _seconds: None).send_key(
        config.tasks[0], "CTRL_C"
    )
    assert "using control down" in recorder.calls[-1][2]


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


def test_missing_accessibility_permission_blocks_injection_without_subprocess() -> None:
    config = default_config()
    recorder = Recorder()
    automation = MacAutomation(
        config, runner=recorder, accessibility_trusted=lambda: False
    )

    with pytest.raises(AutomationError, match="Accessibility permission"):
        automation.send_key(config.tasks[0], "ENTER")

    assert recorder.calls == []


def test_failed_focus_stops_before_key_injection() -> None:
    config = default_config()

    def fail(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="window missing")

    automation = MacAutomation(config, runner=fail)
    with pytest.raises(AutomationError, match="window missing"):
        automation.send_key(config.tasks[0], "ENTER")


def test_focus_supports_iterm_title_and_plain_app_bundle() -> None:
    config = default_config()
    recorder = Recorder()
    automation = MacAutomation(config, runner=recorder)
    iterm = config.tasks[0].model_copy(deep=True)
    iterm.terminal.type = "iterm2"
    iterm.terminal.window_title = "Agent Window"
    automation.focus(iterm)
    assert 'tell application "iTerm2"' in recorder.calls[-1][2]
    assert "Agent Window" in recorder.calls[-1][2]

    app = config.tasks[0].model_copy(deep=True)
    app.terminal.type = "mac_app"
    app.terminal.app_bundle_id = "com.openai.codex"
    automation.focus(app)
    assert recorder.calls[-1] == ["/usr/bin/open", "-b", "com.openai.codex"]


def test_voice_focuses_and_triggers_double_fn() -> None:
    config = default_config()
    recorder = Recorder()
    delays: list[float] = []
    automation = MacAutomation(config, runner=recorder, sleeper=delays.append)

    assert automation.start_voice(config.tasks[0]) == "已触发系统听写"
    assert delays == [0.25, 0.2]
    assert "key code 63" in recorder.calls[-1][2]


def test_voice_rejects_unsupported_hotkey_after_focus() -> None:
    config = default_config()
    config.voice.hotkey = "F20"
    with pytest.raises(AutomationError, match="FN_FN"):
        MacAutomation(config, runner=Recorder(), sleeper=lambda _seconds: None).start_voice(
            config.tasks[0]
        )


def test_send_text_passes_unicode_payload_as_argv() -> None:
    config = default_config()
    recorder = Recorder()
    automation = MacAutomation(config, runner=recorder)
    payload = '中文🙂"; do shell script "false"\r\n\t\\'

    automation.send_text(config.tasks[0], payload)

    assert recorder.calls[-1][-1] == payload
    assert payload not in recorder.calls[-1][2]


def test_send_text_rejects_nul() -> None:
    config = default_config()
    with pytest.raises(AutomationError, match="NUL"):
        MacAutomation(config, runner=Recorder()).send_text(config.tasks[0], "bad\0text")


def test_timeout_becomes_sanitized_automation_error() -> None:
    config = default_config()

    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("osascript", 5, stderr="secret payload")

    with pytest.raises(AutomationError, match="timed out") as caught:
        MacAutomation(config, runner=timeout).focus(config.tasks[0])
    assert "secret payload" not in str(caught.value)


def test_os_error_becomes_sanitized_automation_error() -> None:
    config = default_config()

    def missing(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("private filesystem detail")

    with pytest.raises(AutomationError, match="unavailable") as caught:
        MacAutomation(config, runner=missing).focus(config.tasks[0])
    assert "private filesystem detail" not in str(caught.value)


def test_concurrent_send_text_keeps_focus_and_input_together() -> None:
    config = default_config()
    calls: list[list[str]] = []
    calls_lock = threading.Lock()

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        with calls_lock:
            calls.append(args)
        time.sleep(0.002)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    automation = MacAutomation(config, runner=runner, sleeper=lambda _seconds: None)
    tasks = []
    for index in range(10):
        task = config.tasks[0].model_copy(deep=True)
        task.terminal.window_title = f"task-{index}"
        tasks.append(task)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [
            pool.submit(automation.send_text, task, f"payload-{index}")
            for index, task in enumerate(tasks)
        ]
        assert [future.result(timeout=2) for future in futures] == ["文本已发送"] * 10

    assert len(calls) == 20
    for focus_call, input_call in zip(calls[::2], calls[1::2], strict=True):
        index = int(input_call[-1].removeprefix("payload-"))
        assert f"task-{index}" in focus_call[2]
