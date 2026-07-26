# Windows 移植实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在同一代码库内实现与 macOS 版功能完全一致的 Windows 版 AACC，运行时按 `sys.platform` 选择实现，macOS 零回归。

**Architecture:** 平台差异收敛到少数模块：`win32.py`（ctypes 薄封装，全部可 fake）、`automation_windows.py`（实现既有 `AutomationController` Protocol）、`hotkeys_windows.py`（RegisterHotKey + Qt 原生事件过滤），其余为小型平台分支（路径、进程正则、单实例锁、权限 stub）。设计见 `docs/superpowers/specs/2026-07-26-windows-port-design.md`。

**Tech Stack:** Python 3.12+ / PySide6 / ctypes（无新第三方依赖）/ psutil / PyInstaller。

## Global Constraints

- 不新增第三方运行时依赖；Windows 能力只用 ctypes + 既有依赖。
- TDD：每个实现先写失败测试并看它失败，再实现。Windows 代码全部通过
  fake `aacc.win32` 模块在 macOS 上测试（禁止依赖真实 Windows API）。
- 每个 Task 完成后必须全绿：
  `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
  `.venv/bin/ruff check src tests`
  `.venv/bin/mypy src/aacc`
- CI 有 changed-line 覆盖率 ≥90% 门槛：所有新增代码必须有测试覆盖，
  包括平台分支（用 `monkeypatch.setattr(sys, "platform", "win32")` 或注入
  fake 模块覆盖）。
- 提交信息格式 `feat: ...` / `fix: ...` / `docs: ...`，英文。
  本工作在 feature 分支 `feat/windows-port` 上逐 Task 提交（仅本地，不 push）。
- 文档中英双语（README.md / README.zh-CN.md、KNOWN_LIMITATIONS 两个版本）。
- macOS 行为零回归：现有 418 个测试在任何时刻不得被破坏。
- `sys.platform` 判断集中出现，禁止散落无关注释的平台 hack。

---

### Task 1: 平台化基础路径与 OAuth 设备头

**Files:**
- Modify: `src/aacc/constants.py`（全文 13 行）
- Modify: `src/aacc/kimi_oauth.py:118-127`（`device_headers`）
- Modify: `src/aacc/gui.py:903`（默认日志路径硬编码 `~/Library/Application Support/AACC/logs/app.log`）
- Test: `tests/test_constants.py`（新建）、`tests/test_kimi_oauth.py`（追加）

**Interfaces:**
- Produces:
  - `aacc.constants.APP_SUPPORT_DIR: Path`（保持模块级常量，值平台感知；
    所有既有 import 不变）
  - `aacc.constants.default_app_support_dir(platform_name: str, appdata: str | None) -> Path`
    （纯函数，便于测试）
  - `aacc.kimi_oauth.device_headers(version, device_id)` 行为在 Windows 下返回
    Windows 型号/版本字符串（签名不变）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_constants.py`：

```python
import sys
from pathlib import Path

from aacc.constants import default_app_support_dir


def test_app_support_dir_macos() -> None:
    assert default_app_support_dir("darwin", None) == (
        Path.home() / "Library" / "Application Support" / "AACC"
    )


def test_app_support_dir_windows_prefers_appdata() -> None:
    assert default_app_support_dir("win32", r"C:\Users\u\AppData\Roaming") == (
        Path(r"C:\Users\u\AppData\Roaming") / "AACC"
    )


def test_app_support_dir_windows_falls_back_to_home() -> None:
    assert default_app_support_dir("win32", None) == (
        Path.home() / "AppData" / "Roaming" / "AACC"
    )
```

`tests/test_kimi_oauth.py` 追加：

```python
import platform
import sys


def test_device_headers_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "version", lambda: "10.0.22631")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    from aacc.kimi_oauth import device_headers

    headers = device_headers("1.0.0", "dev-1")
    assert headers["X-Msh-Device-Model"] == "Windows 10.0.22631 AMD64"
    assert headers["X-Msh-Os-Version"] == "10.0.22631"


def test_device_headers_macos_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "mac_ver", lambda: ("15.5", ("", "", ""), "arm64"))
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    from aacc.kimi_oauth import device_headers

    headers = device_headers("1.0.0", "dev-1")
    assert headers["X-Msh-Device-Model"] == "macOS 15.5 arm64"
    assert headers["X-Msh-Os-Version"] == "15.5"
```

- [ ] **Step 2: 运行确认失败**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_constants.py -q`
Expected: FAIL（`ImportError: cannot import name 'default_app_support_dir'`）

- [ ] **Step 3: 实现**

`src/aacc/constants.py` 完整替换为：

```python
import os
import sys
from pathlib import Path

APP_NAME = "AACC"


def default_app_support_dir(platform_name: str, appdata: str | None) -> Path:
    """Per-platform application support directory (pure, testable)."""
    if platform_name == "win32":
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    return Path.home() / "Library" / "Application Support" / APP_NAME


APP_SUPPORT_DIR = default_app_support_dir(sys.platform, os.environ.get("APPDATA"))
DEFAULT_CONFIG_PATH = APP_SUPPORT_DIR / "config.yaml"
DEFAULT_DATABASE_PATH = APP_SUPPORT_DIR / "aacc.db"
DEFAULT_PORT = 17650


def resolve_database_path() -> Path:
    """Single source for the runtime database path (app, CLI, doctor)."""
    return Path(os.environ.get("AACC_DATABASE_PATH", DEFAULT_DATABASE_PATH))
```

`src/aacc/kimi_oauth.py` 的 `device_headers` 改为：

```python
def device_headers(version: str, device_id: str) -> dict[str, str]:
    if sys.platform == "win32":
        os_version = platform.version() or "unknown"
        device_model = f"Windows {os_version} {platform.machine()}"
    else:
        os_version = platform.mac_ver()[0] or "unknown"
        device_model = f"macOS {os_version} {platform.machine()}"
    return {
        "X-Msh-Platform": "kimi_code_cli",
        "X-Msh-Version": version,
        "X-Msh-Device-Id": device_id,
        "X-Msh-Device-Name": platform.node() or "unknown",
        "X-Msh-Device-Model": device_model,
        "X-Msh-Os-Version": os_version,
    }
```

`src/aacc/gui.py:903`：把默认日志路径字符串改为
`str(APP_SUPPORT_DIR / "logs" / "app.log")`（从 `aacc.constants` import
`APP_SUPPORT_DIR`；若 gui.py 已有该 import 则复用）。

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: 全部通过（420 passed 左右）

---

### Task 2: 单实例锁与实例激活跨平台

**Files:**
- Modify: `src/aacc/instance_guard.py`（全文 47 行）
- Test: `tests/test_instance_guard.py`（追加；既有测试不得修改）

**Interfaces:**
- Consumes: 无新依赖。
- Produces:
  - `InstanceGuard.acquire() -> bool` / `close() -> None`（签名不变，
    Windows 用 `msvcrt.locking` 实现同等语义）
  - `activate_existing_instance()`（签名不变；Windows 下尝试把标题含
    "AACC" 的窗口置前，找不到则静默返回）

- [ ] **Step 1: 写失败测试**

`tests/test_instance_guard.py` 追加（先读该文件既有 fake 模式再落笔，
保持风格一致）：

```python
import sys


def test_acquire_uses_msvcrt_on_windows(tmp_path, monkeypatch) -> None:
    import aacc.instance_guard as guard_mod

    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[tuple[int, int, int]] = []

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 0

        @staticmethod
        def locking(fd: int, mode: int, nbytes: int) -> None:
            calls.append((fd, mode, nbytes))

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    guard = guard_mod.InstanceGuard(tmp_path / "aacc.lock")
    assert guard.acquire() is True
    assert calls and calls[0][1] == FakeMsvcrt.LK_NBLCK
    guard.close()
    assert calls[-1][1] == FakeMsvcrt.LK_UNLCK


def test_acquire_windows_conflict_returns_false(tmp_path, monkeypatch) -> None:
    import aacc.instance_guard as guard_mod

    monkeypatch.setattr(sys, "platform", "win32")

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 0

        @staticmethod
        def locking(fd: int, mode: int, nbytes: int) -> None:
            raise OSError("locked")

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    assert guard_mod.InstanceGuard(tmp_path / "aacc.lock").acquire() is False


def test_activate_existing_instance_windows(tmp_path, monkeypatch) -> None:
    import aacc.instance_guard as guard_mod

    monkeypatch.setattr(sys, "platform", "win32")
    focused: list[int] = []
    fake_win32 = types.SimpleNamespace(
        find_window_by_title=lambda title: 42 if title == "AACC" else None,
        focus_window=lambda hwnd: focused.append(hwnd) or True,
    )
    monkeypatch.setitem(sys.modules, "aacc.win32", fake_win32)
    guard_mod.activate_existing_instance()
    assert focused == [42]
```

（文件顶部补 `import types`。）

- [ ] **Step 2: 运行确认失败**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_instance_guard.py -q`
Expected: 新测试 FAIL（Windows 分支不存在）

- [ ] **Step 3: 实现**

`src/aacc/instance_guard.py` 完整替换为：

```python
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import IO


class InstanceGuard:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = self.path.open("a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        if sys.platform == "win32":
            if not self._lock_windows(handle):
                handle.close()
                return False
        else:
            if not self._lock_posix(handle):
                handle.close()
                return False
        self._handle = handle
        return True

    @staticmethod
    def _lock_posix(handle: IO[str]) -> bool:
        import fcntl

        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    @staticmethod
    def _lock_windows(handle: IO[str]) -> bool:
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle, fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def activate_existing_instance() -> None:
    if sys.platform == "win32":
        try:
            from aacc import win32

            hwnd = win32.find_window_by_title("AACC")
            if hwnd is not None:
                win32.focus_window(hwnd)
        except Exception:
            return
        return
    try:
        subprocess.run(
            ["/usr/bin/open", "-b", "com.aacc.controlcenter"],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
```

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: 全部通过（注意既有 `import fcntl` 顶层 import 必须移除，否则
Windows 上 import 即崩；已改为函数内延迟 import）

---

### Task 3: 辅助功能权限平台分支

**Files:**
- Modify: `src/aacc/accessibility.py`（全文 33 行）
- Test: `tests/test_accessibility.py`（追加；既有 FakeQuartz 测试不得修改）

**Interfaces:**
- Produces:
  - `is_accessibility_trusted(prompt: bool = False) -> bool`：Windows 恒 True
    （Windows 注入无需辅助功能授权）
  - `open_accessibility_settings() -> None`：Windows no-op

- [ ] **Step 1: 写失败测试**

`tests/test_accessibility.py` 追加：

```python
import sys


def test_accessibility_always_trusted_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    from aacc.accessibility import is_accessibility_trusted

    assert is_accessibility_trusted() is True
    assert is_accessibility_trusted(prompt=True) is True


def test_open_settings_noop_on_windows(monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr(sys, "platform", "win32")
    called: list[object] = []
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: called.append(a) or None
    )
    from aacc.accessibility import open_accessibility_settings

    open_accessibility_settings()
    assert called == []
```

- [ ] **Step 2: 运行确认失败**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_accessibility.py -q`
Expected: 新测试 FAIL

- [ ] **Step 3: 实现**

`src/aacc/accessibility.py` 两个函数体开头各加平台分支：

```python
def is_accessibility_trusted(prompt: bool = False) -> bool:
    if sys.platform == "win32":
        # Windows 的 SendInput 注入不需要辅助功能授权。
        return True
    try:
        quartz = _load_quartz()
        ...


def open_accessibility_settings() -> None:
    if sys.platform == "win32":
        return
    try:
        subprocess.run(...)
```

（文件顶部加 `import sys`。）

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: 全部通过

---

### Task 4: win32 薄封装 + WindowsAutomation + 自动化工厂

**Files:**
- Create: `src/aacc/win32.py`
- Create: `src/aacc/automation_windows.py`
- Modify: `src/aacc/automation.py`（追加 `create_automation` 工厂；既有
  `MacAutomation` 一行不动）
- Modify: `src/aacc/app.py:17,47,94`（装配处改调工厂）
- Test: `tests/test_automation_windows.py`（新建）

**Interfaces:**
- Consumes: `AutomationController` Protocol
  （`src/aacc/automation_executor.py:23-41`）：`focus / send_key / send_text /
  start_voice`，均带 `cancel_event` 关键字参数并返回 str。
- Produces:
  - `aacc.win32`（仅 Windows 可 import；非 Windows import 即 ImportError）：
    - `find_window_by_title(substring: str) -> int | None`
    - `focus_window(hwnd: int) -> bool`
    - `send_key_combo(vk: int, modifiers: tuple[int, ...] = ()) -> None`
    - `send_unicode_text(text: str) -> None`
    - 常量 `VK_CONTROL = 0x11`、`VK_LWIN = 0x5B`
  - `aacc.automation_windows.WindowsAutomation(config, *, win32_module=None,
    sleeper=time.sleep, accessibility_trusted=lambda: True)`，实现
    `AutomationController` 四方法；校验规则与 `MacAutomation` 完全一致
    （按键白名单 `{*WIN_KEY_CODES, "CTRL_C"}`、文本 1–2000 字符、禁 NUL、
    `keyboard_injection` 开关、cancel_event 检查、focus_delay/voice_delay）
  - `aacc.automation_windows.WIN_KEY_CODES: dict[str, int]`
  - `aacc.automation.create_automation(config, *, accessibility_trusted)`
    按 `sys.platform` 返回 `MacAutomation | WindowsAutomation`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_automation_windows.py`：

```python
from __future__ import annotations

import threading
import types

import pytest

from aacc.automation import AutomationError
from aacc.automation_windows import WIN_KEY_CODES, WindowsAutomation
from aacc.models import AppConfig, TaskConfig, TerminalConfig


class FakeWin32:
    VK_CONTROL = 0x11
    VK_LWIN = 0x5B

    def __init__(self) -> None:
        self.windows: dict[str, int] = {}
        self.focused: list[int] = []
        self.combos: list[tuple[int, tuple[int, ...]]] = []
        self.texts: list[str] = []

    def find_window_by_title(self, substring: str) -> int | None:
        for title, hwnd in self.windows.items():
            if substring.lower() in title.lower():
                return hwnd
        return None

    def focus_window(self, hwnd: int) -> bool:
        self.focused.append(hwnd)
        return True

    def send_key_combo(self, vk: int, modifiers: tuple[int, ...] = ()) -> None:
        self.combos.append((vk, modifiers))

    def send_unicode_text(self, text: str) -> None:
        self.texts.append(text)


def _task(title: str | None = "myproject") -> TaskConfig:
    return TaskConfig(
        id="t1",
        name="myproject",
        terminal=TerminalConfig(type="windows_terminal", window_title=title),
    )


def _automation(win32: FakeWin32) -> WindowsAutomation:
    return WindowsAutomation(AppConfig(), win32_module=win32, sleeper=lambda _: None)


def test_focus_uses_window_title() -> None:
    win32 = FakeWin32()
    win32.windows["user@host: C:\\dev\\myproject"] = 7
    assert _automation(win32).focus(_task()) == "已聚焦 myproject"
    assert win32.focused == [7]


def test_focus_falls_back_to_task_name() -> None:
    win32 = FakeWin32()
    win32.windows["myproject - Windows Terminal"] = 9
    assert _automation(win32).focus(_task(title=None)) == "已聚焦 myproject"
    assert win32.focused == [9]


def test_focus_missing_window_raises() -> None:
    win32 = FakeWin32()
    with pytest.raises(AutomationError):
        _automation(win32).focus(_task())


def test_send_key_enter_focuses_then_sends() -> None:
    win32 = FakeWin32()
    win32.windows["myproject"] = 3
    assert _automation(win32).send_key(_task(), "ENTER") == "已发送 ENTER"
    assert win32.focused == [3]
    assert win32.combos == [(WIN_KEY_CODES["ENTER"], ())]


def test_send_key_ctrl_c_uses_control_modifier() -> None:
    win32 = FakeWin32()
    win32.windows["myproject"] = 3
    _automation(win32).send_key(_task(), "CTRL_C")
    assert win32.combos == [(0x43, (0x11,))]


def test_send_key_rejects_unknown_key() -> None:
    win32 = FakeWin32()
    with pytest.raises(AutomationError):
        _automation(win32).send_key(_task(), "F24")


def test_send_text_unicode() -> None:
    win32 = FakeWin32()
    win32.windows["myproject"] = 3
    assert _automation(win32).send_text(_task(), "你好 world") == "文本已发送"
    assert win32.texts == ["你好 world"]


def test_send_text_validates_length_and_nul() -> None:
    win32 = FakeWin32()
    automation = _automation(win32)
    with pytest.raises(AutomationError):
        automation.send_text(_task(), "")
    with pytest.raises(AutomationError):
        automation.send_text(_task(), "x" * 2001)
    with pytest.raises(AutomationError):
        automation.send_text(_task(), "a\0b")


def test_injection_disabled_blocks_send() -> None:
    win32 = FakeWin32()
    config = AppConfig()
    config.app.keyboard_injection = False
    automation = WindowsAutomation(config, win32_module=win32, sleeper=lambda _: None)
    with pytest.raises(AutomationError):
        automation.send_key(_task(), "ENTER")


def test_start_voice_triggers_win_h() -> None:
    win32 = FakeWin32()
    win32.windows["myproject"] = 3
    assert _automation(win32).start_voice(_task()) == "已触发语音输入"
    assert win32.combos == [(0x48, (0x5B,))]


def test_cancel_event_aborts_before_focus() -> None:
    win32 = FakeWin32()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(AutomationError):
        _automation(win32).focus(_task(), cancel_event=cancel)
    assert win32.focused == []


def test_factory_returns_windows_automation_on_win32(monkeypatch) -> None:
    import sys

    import aacc.automation as automation_mod

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules, "aacc.win32", types.SimpleNamespace()
    )
    controller = automation_mod.create_automation(AppConfig())
    assert isinstance(controller, WindowsAutomation)


def test_factory_returns_mac_automation_on_darwin(monkeypatch) -> None:
    import sys

    import aacc.automation as automation_mod

    monkeypatch.setattr(sys, "platform", "darwin")
    controller = automation_mod.create_automation(AppConfig())
    assert isinstance(controller, automation_mod.MacAutomation)
```

- [ ] **Step 2: 运行确认失败**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_automation_windows.py -q`
Expected: FAIL（`ModuleNotFoundError: aacc.automation_windows`）

- [ ] **Step 3: 实现**

新建 `src/aacc/win32.py`（完整内容）：

```python
"""Thin ctypes wrappers over the Win32 APIs used by the Windows port.

All Windows-specific calls live behind this module so tests can substitute a
fake (``win32_module`` parameter / ``sys.modules`` stub) without a Windows
machine. Importing on non-Windows raises ImportError; consumers import it
lazily so the rest of the package stays importable everywhere.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - import guard
    raise ImportError("aacc.win32 is only importable on Windows")

user32 = ctypes.windll.user32

SW_RESTORE = 9
VK_CONTROL = 0x11
VK_LWIN = 0x5B
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_window_by_title(substring: str) -> int | None:
    """First visible top-level window whose title contains the substring."""
    needle = substring.casefold()
    found: list[int] = []

    @_WNDENUMPROC
    def _callback(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if needle in buffer.value.casefold():
                    found.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    return found[0] if found else None


def focus_window(hwnd: int) -> bool:
    """Restore a minimized window and bring it to the foreground."""
    user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


def _vk_input(vk: int, *, key_up: bool = False) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if key_up else 0, 0, 0)
    return event


def _char_input(char: str, *, key_up: bool = False) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    event.union.ki = KEYBDINPUT(0, ord(char), flags, 0, 0)
    return event


def _send_input(events: list[INPUT]) -> None:
    array = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(array), array, ctypes.sizeof(INPUT))
    if sent != len(array):
        raise OSError("SendInput failed")


def send_key_combo(vk: int, modifiers: tuple[int, ...] = ()) -> None:
    """Press modifiers+vk and release them in reverse order."""
    events = [_vk_input(modifier) for modifier in modifiers]
    events += [_vk_input(vk), _vk_input(vk, key_up=True)]
    events += [_vk_input(modifier, key_up=True) for modifier in reversed(modifiers)]
    _send_input(events)


def send_unicode_text(text: str) -> None:
    """Type text as Unicode keystrokes (layout-independent)."""
    events: list[INPUT] = []
    for char in text:
        events.append(_char_input(char))
        events.append(_char_input(char, key_up=True))
    _send_input(events)
```

新建 `src/aacc/automation_windows.py`（完整内容）：

```python
"""Windows desktop automation: window focus, key/text injection, voice typing.

Mirrors :class:`aacc.automation.MacAutomation` through the shared
``AutomationController`` protocol. All Win32 calls go through ``aacc.win32``
(injectable as ``win32_module`` for tests). Window focus matches window
titles, since Windows has no bundle-identifier concept.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from aacc.automation import AutomationError
from aacc.models import AppConfig, TaskConfig

WIN_KEY_CODES = {
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "1": 0x31,
    "2": 0x32,
}


class WindowsAutomation:
    def __init__(
        self,
        config: AppConfig,
        *,
        win32_module: Any | None = None,
        sleeper: Callable[[float], Any] = time.sleep,
        accessibility_trusted: Callable[[], bool] = lambda: True,
    ) -> None:
        self.config = config
        if win32_module is not None:
            self._win32 = win32_module
        else:
            from aacc import win32

            self._win32 = win32
        self._sleep = sleeper
        self._accessibility_trusted = accessibility_trusted
        self._lock = threading.RLock()

    @staticmethod
    def _check_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AutomationError("Desktop automation was cancelled")

    def _focus_unlocked(self, task: TaskConfig) -> str:
        title = task.terminal.window_title or task.terminal.tab_title or task.name
        hwnd = self._win32.find_window_by_title(title)
        if hwnd is None:
            raise AutomationError(f"未找到标题包含 {title!r} 的窗口")
        if not self._win32.focus_window(hwnd):
            raise AutomationError("无法将目标窗口置前")
        return f"已聚焦 {task.name}"

    def focus(self, task: TaskConfig, *, cancel_event: threading.Event | None = None) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            return self._focus_unlocked(task)

    def _ensure_injection(self) -> None:
        if not self.config.app.keyboard_injection:
            raise AutomationError("Keyboard injection is disabled in AACC settings")
        if not self._accessibility_trusted():
            raise AutomationError("Accessibility permission is required")

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
            if normalized not in {*WIN_KEY_CODES, "CTRL_C"}:
                raise AutomationError(f"Key {normalized} is not allowed")
            self._focus_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            if normalized == "CTRL_C":
                self._win32.send_key_combo(0x43, (self._win32.VK_CONTROL,))
            else:
                self._win32.send_key_combo(WIN_KEY_CODES[normalized])
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
                raise AutomationError("Text must contain 1 to 2000 characters")
            if "\0" in text:
                raise AutomationError("Text must not contain NUL")
            self._focus_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            self._win32.send_unicode_text(text)
            return "文本已发送"

    def start_voice(self, task: TaskConfig, *, cancel_event: threading.Event | None = None) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            self._ensure_injection()
            self._focus_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._sleep(self.config.voice.voice_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            # Windows 语音输入（Win+H），对应 macOS 的系统听写（双击 Fn）。
            self._win32.send_key_combo(0x48, (self._win32.VK_LWIN,))
            return "已触发语音输入"
```

`src/aacc/automation.py` 追加（顶部加 `import sys`、`from typing import
TYPE_CHECKING`，`TYPE_CHECKING` 块内 `from aacc.automation_windows import
WindowsAutomation`）：

```python
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
```

`src/aacc/app.py`：
- 第 17 行 `from aacc.automation import MacAutomation` →
  `from aacc.automation import create_automation`
- 第 47 行 `automation: MacAutomation` →
  `automation: AutomationController`（`AutomationController` 已从
  `aacc.automation_executor` import 处补充导入）
- 第 94 行 `automation = MacAutomation(config, accessibility_trusted=...)` →
  `automation = create_automation(config, accessibility_trusted=accessibility_trusted)`

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: 全部通过

---

### Task 5: Windows 全局热键

**Files:**
- Create: `src/aacc/hotkeys_windows.py`
- Modify: `src/aacc/app.py:215`（按平台选择热键实现）
- Test: `tests/test_hotkeys_windows.py`（新建）

**Interfaces:**
- Consumes: `aacc.win32`（Task 4）；既有
  `AccessibilityHotkeySync`（`src/aacc/hotkeys.py:125-145`）——只要
  Windows 实现暴露同样的 `start() -> bool` / `stop() -> None` /
  `available` / `error` 接口即可复用，无需改动。
- Produces:
  - `aacc.hotkeys_windows.WINDOWS_HOTKEY_VK: dict[str, int]`
    （F13–F20 → 0x7C–0x87）
  - `aacc.hotkeys_windows.windows_hotkey_vk(name: str) -> int`
    （语义对齐 `hotkeys.hotkey_keycode`，非法名抛 ValueError）
  - `aacc.hotkeys_windows.WindowsGlobalHotkeys(bindings, actions, *, hwnd=0,
    win32_module=None, qt_app=None)`：公开接口与 `GlobalHotkeys` 相同
    （`start()` / `stop()` / `available` / `error`）
  - `aacc.win32.register_hotkey(hwnd, hotkey_id, vk) -> bool` /
    `unregister_hotkey(hwnd, hotkey_id) -> None`（本任务向 win32.py 追加，
    附 `WM_HOTKEY = 0x0312` 常量与 `MOD_NOREPEAT = 0x4000`）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_hotkeys_windows.py`：

```python
from __future__ import annotations

import ctypes
import types

import pytest

from aacc.hotkeys_windows import WINDOWS_HOTKEY_VK, WindowsGlobalHotkeys, windows_hotkey_vk


class FakeWin32:
    WM_HOTKEY = 0x0312

    def __init__(self) -> None:
        self.registered: list[tuple[int, int, int]] = []
        self.unregistered: list[tuple[int, int]] = []

    def register_hotkey(self, hwnd: int, hotkey_id: int, vk: int) -> bool:
        self.registered.append((hwnd, hotkey_id, vk))
        return True

    def unregister_hotkey(self, hwnd: int, hotkey_id: int) -> None:
        self.unregistered.append((hwnd, hotkey_id))


def test_vk_table_matches_function_keys() -> None:
    assert windows_hotkey_vk("F13") == 0x7C
    assert windows_hotkey_vk(" f20 ") == 0x87
    with pytest.raises(ValueError):
        windows_hotkey_vk("F1")


def test_start_registers_hotkeys_and_dispatches(qapp) -> None:
    win32 = FakeWin32()
    fired: list[str] = []
    hotkeys = WindowsGlobalHotkeys(
        {"toggle": "F13"},
        {"toggle": lambda: fired.append("toggle")},
        hwnd=99,
        win32_module=win32,
        qt_app=qapp,
    )
    assert hotkeys.start() is True
    assert hotkeys.available
    assert win32.registered == [(99, 1, 0x7C)]

    # 模拟一条 WM_HOTKEY 消息送达过滤器
    msg = hotkeys.make_hotkey_message(hwnd=99, hotkey_id=1)
    hotkeys.dispatch_message(msg)
    assert fired == ["toggle"]

    hotkeys.stop()
    assert win32.unregistered == [(99, 1)]
    assert not hotkeys.available


def test_start_failure_sets_error(qapp) -> None:
    win32 = FakeWin32()
    win32.register_hotkey = lambda hwnd, hotkey_id, vk: False  # type: ignore[method-assign]
    hotkeys = WindowsGlobalHotkeys(
        {"toggle": "F13"}, {"toggle": lambda: None}, win32_module=win32, qt_app=qapp
    )
    assert hotkeys.start() is False
    assert hotkeys.error is not None
    assert not hotkeys.available
```

（`qapp` fixture 来自 tests/conftest.py；先读它确认 fixture 名，
若不存在则用 `pytest.fixture` 自建 offscreen QApplication，参照
tests/test_gui.py 的做法。`make_hotkey_message`/`dispatch_message` 是为
可测试性暴露的两个小方法：前者构造一个 MSG 结构，后者是过滤器内部
分发入口。）

- [ ] **Step 2: 运行确认失败**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_hotkeys_windows.py -q`
Expected: FAIL（`ModuleNotFoundError: aacc.hotkeys_windows`）

- [ ] **Step 3: 实现**

`src/aacc/win32.py` 追加：

```python
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000


def register_hotkey(hwnd: int, hotkey_id: int, vk: int) -> bool:
    return bool(user32.RegisterHotKey(hwnd, hotkey_id, MOD_NOREPEAT, vk))


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    user32.UnregisterHotKey(hwnd, hotkey_id)
```

新建 `src/aacc/hotkeys_windows.py`（完整内容）：

```python
"""Windows global hotkeys via RegisterHotKey + a Qt native event filter.

Public surface mirrors :class:`aacc.hotkeys.GlobalHotkeys`
(``start``/``stop``/``available``/``error``) so the existing
``AccessibilityHotkeySync`` drives it unchanged. Win32 calls are injectable
for tests; the Qt message filter needs a running QApplication.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter

WINDOWS_HOTKEY_VK = {
    "F13": 0x7C,
    "F14": 0x7D,
    "F15": 0x7E,
    "F16": 0x7F,
    "F17": 0x80,
    "F18": 0x81,
    "F19": 0x82,
    "F20": 0x83,
}


def windows_hotkey_vk(name: str) -> int:
    normalized = name.strip().upper()
    try:
        return WINDOWS_HOTKEY_VK[normalized]
    except KeyError as error:
        raise ValueError(f"Unsupported global hotkey: {name}") from error


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: WindowsGlobalHotkeys) -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, _event_type: bytes, message: int) -> tuple[bool, int]:
        msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        if msg.message == self._owner._win32.WM_HOTKEY:
            self._owner.dispatch_message(msg)
        return False, 0


class WindowsGlobalHotkeys:
    def __init__(
        self,
        bindings: dict[str, str],
        actions: dict[str, Callable[[], None]],
        *,
        hwnd: int = 0,
        win32_module: Any | None = None,
        qt_app: Any | None = None,
    ) -> None:
        if win32_module is not None:
            self._win32 = win32_module
        else:
            from aacc import win32

            self._win32 = win32
        self._hwnd = hwnd
        self._qt_app = qt_app
        self._actions_by_id = {
            index: actions[action]
            for index, (action, hotkey) in enumerate(
                ((a, h) for a, h in bindings.items() if a in actions), start=1
            )
        }
        self._vk_by_id = {
            index: windows_hotkey_vk(hotkey)
            for index, (action, hotkey) in enumerate(
                ((a, h) for a, h in bindings.items() if a in actions), start=1
            )
        }
        self._filter: _HotkeyEventFilter | None = None
        self.error: str | None = None
        self._running = False

    @property
    def available(self) -> bool:
        return self.error is None and self._running

    def start(self) -> bool:
        self.error = None
        app = self._qt_app
        if app is None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
        if app is None:
            self.error = "QApplication is required for global hotkeys"
            return False
        for hotkey_id, vk in self._vk_by_id.items():
            if not self._win32.register_hotkey(self._hwnd, hotkey_id, vk):
                self.error = f"RegisterHotKey failed for hotkey id {hotkey_id}"
                logging.getLogger("aacc.hotkeys").warning(
                    "Global hotkeys unavailable: %s", self.error
                )
                return False
        self._filter = _HotkeyEventFilter(self)
        app.installNativeEventFilter(self._filter)
        self._running = True
        return True

    def make_hotkey_message(self, *, hwnd: int, hotkey_id: int) -> _MSG:
        """Build the MSG a WM_HOTKEY delivery would carry (test hook)."""
        return _MSG(hwnd, self._win32.WM_HOTKEY, hotkey_id, 0, 0, wintypes.POINT(0, 0))

    def dispatch_message(self, msg: _MSG) -> None:
        action = self._actions_by_id.get(msg.wParam)
        if action is not None:
            action()

    def stop(self) -> None:
        app = self._qt_app
        if app is None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
        if self._filter is not None and app is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None
        for hotkey_id in self._vk_by_id:
            self._win32.unregister_hotkey(self._hwnd, hotkey_id)
        self._running = False
```

`src/aacc/app.py:215` 改为按平台装配（window 已在作用域内）：

```python
    if sys.platform == "win32":
        from aacc.hotkeys_windows import WindowsGlobalHotkeys

        hotkeys = WindowsGlobalHotkeys(
            runtime.config.hotkeys,
            _hotkey_actions(window),
            hwnd=int(window.winId()),
        )
    else:
        hotkeys = GlobalHotkeys(runtime.config.hotkeys, _hotkey_actions(window))  # type: ignore[arg-type]
    hotkey_sync = AccessibilityHotkeySync(hotkeys)
```

（`AccessibilityHotkeySync` 只依赖 `start/stop`，mypy 若对联合类型报错，
给 `hotkeys` 标注 `GlobalHotkeys | WindowsGlobalHotkeys` 并视情况调整
`AccessibilityHotkeySync.__init__` 参数注解为 `Any` 或 Protocol——以
mypy strict 通过为准，改动最小化。）

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: 全部通过

---

### Task 6: 发现源与进程匹配平台化

**Files:**
- Modify: `src/aacc/kimi_discovery.py:21,216-218,278-280`（进程正则、发现任务的终端配置）
- Modify: `src/aacc/adapters.py:44-52,118-147`（codex 等进程匹配模式）
- Modify: `src/aacc/kimi_desktop_discovery.py:19-21,72-74,137-140`（daimon 路径、进程匹配、终端配置）
- Modify: `src/aacc/codex_discovery.py:55-61,477-485,577-583,144`（st_ino 缓存、终端配置）
- Test: `tests/test_kimi_discovery.py`、`tests/test_adapters.py`、
  `tests/test_kimi_desktop_discovery.py`、`tests/test_codex_discovery.py`（各追加，不改既有断言）

**Interfaces:**
- Consumes: 无（独立任务）。
- Produces:
  - `aacc.kimi_discovery._default_process_pattern() -> re.Pattern[str]`：
    darwin 返回现有 `(?:^|/)kimi(?:\s|$)`；win32 返回 `^kimi(?:\.exe)?$`
    （IGNORECASE）
  - `aacc.kimi_discovery._default_terminal_config(work_dir) -> TerminalConfig`：
    darwin 返回现有 `terminal_app/com.apple.Terminal`；win32 返回
    `TerminalConfig(type="windows_terminal", window_title=<work_dir 基名或 None>)`
  - `aacc.adapters` 的 codex 默认进程模式按平台生成（win32:
    `^codex(?:\.exe)?$` IGNORECASE；darwin 保持现状）
  - `aacc.kimi_desktop_discovery._default_daimon_roots() -> list[Path]`：
    darwin 返回现有单路径；win32 返回 `%LOCALAPPDATA%` 与 `%APPDATA%`
    下的 `kimi-desktop/daimon-share/daimon` 候选（环境变量缺失则跳过）
  - kimi_desktop 进程存活匹配按平台：darwin 保持 `"/Kimi.app/" in exe`；
    win32 匹配 `exe.lower().endswith("\\kimi.exe")`
  - `aacc.codex_discovery` 文件身份指纹：`(st_dev, st_ino)` 中 `st_ino == 0`
    时降级为 `(str(path), st_mtime_ns, st_size)`（Windows 上 st_ino 常为 0）
  - codex 发现任务的 TerminalConfig：darwin 保持
    `mac_app/com.openai.codex`；win32 改
    `TerminalConfig(type="windows_terminal", window_title=<work_dir 基名>)`

- [ ] **Step 1: 写失败测试**

各测试文件追加（monkeypatch `sys.platform` 为 `"win32"` 走 Windows 分支，
darwin 分支由既有测试覆盖）：

`tests/test_kimi_discovery.py`：

```python
import re
import sys


def test_process_pattern_windows_matches_kimi_exe(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    from aacc.kimi_discovery import _default_process_pattern

    pattern = _default_process_pattern()
    assert pattern.search("kimi.exe")
    assert pattern.search("KIMI.EXE")
    assert not pattern.search("notkimi.exe")


def test_discovered_task_terminal_windows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    from aacc.kimi_discovery import _default_terminal_config

    terminal = _default_terminal_config("/home/user/myproject")
    assert terminal.type == "windows_terminal"
    assert terminal.window_title == "myproject"
    assert terminal.app_bundle_id is None
```

`tests/test_adapters.py`：对 codex 默认模式做同款断言（先读
`src/aacc/adapters.py:118-147` 的默认 pattern 常量，按实际结构追加平台
分支与测试）。

`tests/test_kimi_desktop_discovery.py`：

```python
def test_daimon_roots_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\u\AppData\Roaming")
    from aacc.kimi_desktop_discovery import _default_daimon_roots

    roots = _default_daimon_roots()
    assert roots == [
        Path(r"C:\Users\u\AppData\Local") / "kimi-desktop" / "daimon-share" / "daimon",
        Path(r"C:\Users\u\AppData\Roaming") / "kimi-desktop" / "daimon-share" / "daimon",
    ]


def test_daimon_roots_windows_missing_env(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    from aacc.kimi_desktop_discovery import _default_daimon_roots

    assert _default_daimon_roots() == []
```

`tests/test_codex_discovery.py`：构造 st_ino 为 0 的 fake stat，断言指纹
降级为 path+mtime+size（先读 `src/aacc/codex_discovery.py:55-61` 的指纹
实现，按其真实函数名写测试）。

- [ ] **Step 2: 运行确认失败**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_kimi_discovery.py tests/test_adapters.py tests/test_kimi_desktop_discovery.py tests/test_codex_discovery.py -q`
Expected: 新增测试 FAIL

- [ ] **Step 3: 实现**

按 Produces 清单逐文件加平台分支。要点：
- 平台分支一律用模块级私有函数（`_default_*`），`sys.platform` 判断集中
  在函数内，调用处在 `__init__` 默认值或任务构造点。
- `kimi_discovery.KimiLocalDiscovery.__init__` 的 `agent_process_alive`
  默认值改为 `CachedProcessAlive("name", lambda value: bool(_default_process_pattern().search(value)))`
  ——注意保持 lambda 内调用以便测试 monkeypatch 生效（或改成
  `functools.partial` 语义等价的写法，以测试通过为准）。
- daimon 多根目录：发现服务对**每个存在的根**分别建 sqlite 连接，或
  取第一个存在的根——先读 `kimi_desktop_discovery.py` 现状，选改动
  最小的方案（找不到任何根时该源静默产出空列表）。
- codex/kimi_desktop 发现任务在 win32 下同样改走
  `windows_terminal` + 标题（Kimi Desktop 窗口标题用 `"Kimi"`）。

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: 全部通过

---

### Task 7: 配置默认值与示例配置平台化

**Files:**
- Modify: `src/aacc/config.py:39-79`（默认任务终端配置、默认热键区）
- Modify: `examples/config.example.yaml:38-79`（补 Windows 示例）
- Test: `tests/test_config.py`（追加）

**Interfaces:**
- Consumes: Task 6 的终端类型约定（`windows_terminal`）。
- Produces:
  - `aacc.config` 默认任务在 win32 下生成
    `TerminalConfig(type="windows_terminal")`（无 bundle id）；darwin 保持
    `com.apple.Terminal` 不变
  - 默认热键 F13–F20、voice FN_FN 两平台保持一致（Windows 侧
    start_voice 忽略 hotkey 值固定触发 Win+H，见 Task 4）

- [ ] **Step 1: 写失败测试**

`tests/test_config.py` 追加（先读 `src/aacc/config.py` 默认配置的生成
方式——是模块常量还是函数——再决定 monkeypatch 点）：

```python
def test_default_terminal_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    # 按 config.py 实际结构调用默认配置生成入口，断言：
    # terminal.type == "windows_terminal" 且 app_bundle_id is None
```

- [ ] **Step 2: 运行确认失败**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_config.py -q`
Expected: 新测试 FAIL

- [ ] **Step 3: 实现**

`config.py` 默认终端配置按 `sys.platform` 分支（集中在一个
`_default_terminal_config()` 私有函数）。**同时处理 POSIX-only 的目录
fsync**：`config.py:124-126` 保存配置时对目录做 `os.open` + `fsync`，
Windows 上对目录 `os.open` 会直接抛 `OSError`——加平台守卫（win32 跳过
目录 fsync，文件本身的 flush+fsync 保留），并配一个
`monkeypatch sys.platform="win32"` 调用 `save_config` 不抛异常的测试。
`os.chmod(..., 0o600)` 在 Windows 上无害（仅影响只读位），保持原样。

`examples/config.example.yaml`
在既有 macOS 示例后追加注释清晰的 Windows 示例块：

```yaml
# Windows 示例：终端类型 windows_terminal 按窗口标题聚焦（无 bundle id 概念）
# tasks:
#   - id: "task-1"
#     name: "我的项目"
#     agent:
#       type: "kimi_code"
#     terminal:
#       type: "windows_terminal"
#       window_title: "我的项目"
```

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: 全部通过

---

### Task 8: Windows 打包、CI 矩阵与文档

**Files:**
- Create: `AACC-windows.spec`
- Create: `scripts/build_windows.ps1`
- Modify: `.github/workflows/ci.yml`（测试矩阵加 windows-latest）
- Modify: `pyproject.toml:8`（描述不再限定 macOS）
- Modify: `README.md`、`README.zh-CN.md`（Windows 构建/运行章节）
- Modify: `KNOWN_LIMITATIONS.md`、`KNOWN_LIMITATIONS.zh-CN.md`（Windows 差异）
- Create: `docs/windows-verification-checklist.zh-CN.md`、
  `docs/windows-verification-checklist.en.md`（真机冒烟清单）
- Modify: `AGENTS.md`（架构要点 + 当前进度）
- Test: `tests/test_packaging.py`（追加）

**Interfaces:**
- Consumes: 之前所有任务。
- Produces:
  - `AACC-windows.spec`：相对路径 Analysis，`console=False`，无 BUNDLE，
    `excludes` 含 `Quartz`，datas 含 `styles.qss`
  - `scripts/build_windows.ps1`：`uv sync --locked` 后
    `uv run pyinstaller --noconfirm --clean AACC-windows.spec`

- [ ] **Step 1: 写失败测试**

`tests/test_packaging.py` 追加（先读该文件既有断言风格）：

```python
def test_windows_spec_exists_and_excludes_quartz() -> None:
    spec = (ROOT / "AACC-windows.spec").read_text(encoding="utf-8")
    assert "console=False" in spec
    assert "Quartz" in spec  # 出现在 excludes
    assert "BUNDLE" not in spec
    assert "styles.qss" in spec


def test_windows_build_script_invokes_pyinstaller() -> None:
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "AACC-windows.spec" in script
    assert "pyinstaller" in script.lower()
```

（`ROOT` 按该文件既有的路径常量写法。）

- [ ] **Step 2: 运行确认失败**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: 新测试 FAIL（文件不存在）

- [ ] **Step 3: 实现**

`AACC-windows.spec`（完整内容）：

```python
# -*- mode: python ; coding: utf-8 -*-
import os

ROOT = os.path.abspath(os.getcwd())

a = Analysis(
    [os.path.join(ROOT, 'src', 'aacc', '__main__.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[],
    datas=[(os.path.join(ROOT, 'src', 'aacc', 'styles.qss'), 'aacc')],
    hiddenimports=['aacc.adapters'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['mypy', 'pytest', 'Quartz'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AACC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AACC',
)
```

`scripts/build_windows.ps1`（完整内容）：

```powershell
#requires -Version 5
# 在 Windows 上构建 AACC（PyInstaller，windowed 单目录产物 dist/AACC/AACC.exe）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
uv sync --locked
uv run pyinstaller --noconfirm --clean AACC-windows.spec
Write-Host "Built dist/AACC/AACC.exe"
```

`.github/workflows/ci.yml`：把 `test` job 改为 matrix：

```yaml
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
```

Lint/Format/Type check/Test 步骤不变（offscreen pytest 在 Windows 同样
可跑）；`Changed-line coverage` 与 `Dependency vulnerability scan` 及
Upload 三个步骤加 `if: matrix.os == 'macos-latest'`（diff-cover 需要
origin/main 比较基线与 pip-audit 产物，保持在 mac 单点执行）。

`pyproject.toml:8` 描述改为跨平台措辞（如 "menu-bar/tray panel to
monitor local agent CLI tasks (macOS & Windows)"）。

README 双语：新增 "Windows" 小节——前置条件（Windows 10+、Python 3.12+、
uv）、构建（`scripts\build_windows.ps1`）、运行（`dist\AACC\AACC.exe`）、
与 macOS 版的能力对照表（聚焦=窗口标题匹配；语音=Win+H；无辅助功能
授权步骤；SmartScreen 未签名警告说明）。

KNOWN_LIMITATIONS 双语追加：
- Windows 终端聚焦依赖窗口标题匹配，标题被 shell 改写时可能失准；
- SetForegroundWindow 受 Windows 前景锁限制，必要时降级并记日志；
- Kimi Desktop daimon 的 Windows 路径为候选路径 best-effort，未真机验证；
- 无代码签名，首次运行有 SmartScreen 提示；
- F13–F20 热键在多数 Windows 键盘需 Fn 层映射。

冒烟清单 `docs/windows-verification-checklist.zh-CN.md`（.en.md 同构）：
构建 → 启动 → 发现 kimi/codex 会话 → 卡片聚焦 → 按键注入 → 文本注入 →
语音（Win+H）→ 热键 → 托盘驻留/恢复 → 额度条 → 设置页，逐项勾选。

`AGENTS.md`：架构要点补一段"平台抽象（win32.py / automation_windows /
hotkeys_windows，工厂分发）"；当前进度补 Windows 移植条目。

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: 全部通过

- [ ] **Step 5: 终检**

通读 `git diff --stat`，确认无 macOS 逻辑被意外改动；确认
`git status` 中无计划外文件。
