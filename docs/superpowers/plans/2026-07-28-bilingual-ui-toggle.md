# Live Chinese/English UI Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the header compact button with a persistent, immediate Chinese/English UI switch while preserving compact mode in Settings/tray and keeping the complete 1.4.2 Windows/macOS candidate pipeline releasable.

**Architecture:** Add one injected `LanguageManager` backed by strict two-language catalogs and `QSettings`. Persistent Qt widgets re-render from retained model state when the language changes; transient menus and dialogs read the active language when created. The same manager reaches the Kimi native web login through application assembly, without global mutable locale state.

**Tech Stack:** Python 3.12+, PySide6 6.11, QSettings/QLocale, pytest/pytest-qt, Ruff, mypy strict, PyInstaller, Inno Setup 6.7.1, GitHub Actions.

## Global Constraints

- Supported values are exactly `zh_CN` and `en_US`.
- First launch uses Chinese only for a Chinese system locale; every other locale defaults to English.
- Explicit selection is persisted under QSettings key `ui_language`.
- The header button displays the destination language: `EN` in Chinese mode and `中` in English mode.
- Language switching is immediate and must not refresh quotas, mutate tasks, log out, rotate credentials or alter compact/window state.
- Compact mode remains available in Settings and the tray menu; its existing persistence key remains `compact_mode`.
- Translate application UI in `gui.py` and Kimi website-login UI. CLI/API/log protocols and product labels (`Codex`, `Kimi`, `5H`, `WEEK`, `MONTH`, `API`, `WebView2`) stay unchanged.
- Startup security failures remain bilingual.
- Do not introduce Qt Linguist, `.qm` files or a new runtime dependency.
- Do not log or translate cookies, tokens, passwords, remote URLs/query strings/fragments or remote response bodies.
- Regenerate the documentation screenshot only from fixed synthetic data.
- Do not create `v1.4.2` or a formal Release while Windows 10/11/manual gates remain open.

---

### Task 0: Repair the Hosted Windows Inno Compile Gate

**Files:**
- Modify: `installer/AACC.iss:318-360`
- Modify: `tests/test_packaging.py:340-360`

**Interfaces:**
- Consumes: `IsUsableWebView2RuntimeVersion(const RuntimeVersion: String): Boolean`
- Produces: the same function with Inno-compatible character comparisons and unchanged strict four-component semantics.

- [ ] **Step 1: Record the failure evidence**

Save the hosted evidence in the task report:

```text
GitHub Actions run 30327307060
windows-package-2025
installer/AACC.iss line 344 column 44
Closing square bracket (']') expected
```

The unsupported expression is:

```pascal
Component[DigitIndex] in ['0'..'9']
```

- [ ] **Step 2: Strengthen the static regression test**

Replace the current range-syntax assertion in
`test_windows_installer_rejects_malformed_or_zero_webview2_runtime_versions`
with:

```python
assert "in ['0'..'9']" not in version_validator
assert "(Component[DigitIndex] < '0') or" in version_validator
assert "(Component[DigitIndex] > '9')" in version_validator
assert "ComponentCount <> 4" in version_validator
assert "HasNonZeroDigit" in version_validator
```

- [ ] **Step 3: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_packaging.py::test_windows_installer_rejects_malformed_or_zero_webview2_runtime_versions
```

Expected: FAIL because the source still contains the unsupported set range.

- [ ] **Step 4: Replace the Inno expression**

Use only comparisons supported by Inno Pascal Script:

```pascal
if (Component[DigitIndex] < '0') or
   (Component[DigitIndex] > '9') then
  Exit;
```

Do not change the exact-four-components or non-zero-digit rules.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_packaging.py tests/test_windows_installer_contract.py
.venv/bin/ruff check tests/test_packaging.py
git diff --check
```

Commit:

```bash
git add installer/AACC.iss tests/test_packaging.py
git commit -m "fix: use Inno-compatible WebView2 validation"
```

---

### Task 1: Typed Language Catalog and Persistence

**Files:**
- Create: `src/aacc/i18n.py`
- Create: `tests/test_i18n.py`

**Interfaces:**
- Produces:
  - `Language = Literal["zh_CN", "en_US"]`
  - `ZH_CN`, `EN_US`, `SUPPORTED_LANGUAGES`
  - `detect_system_language(locale_name: str | None = None) -> Language`
  - `load_language(settings: QSettings, locale_name: str | None = None) -> Language`
  - `other_language(language: Language) -> Language`
  - `LanguageManager(language: Language, settings: QSettings | None = None)`
  - `LanguageManager.language: Language`
  - `LanguageManager.text(key: str, **values: object) -> str`
  - `LanguageManager.set_language(language: Language) -> None`
  - `LanguageManager.subscribe(callback: Callable[[], None]) -> Callable[[], None]`

- [ ] **Step 1: Write failing core tests**

Create `tests/test_i18n.py` with:

```python
from PySide6.QtCore import QSettings

from aacc.i18n import (
    EN_US,
    ZH_CN,
    LanguageManager,
    catalog_keys,
    detect_system_language,
    load_language,
    placeholder_names,
)


def test_system_language_is_chinese_only_for_chinese_locales() -> None:
    assert detect_system_language("zh_CN") == ZH_CN
    assert detect_system_language("zh-Hant-TW") == ZH_CN
    assert detect_system_language("en_US") == EN_US
    assert detect_system_language("ja_JP") == EN_US


def test_persisted_language_is_strict_and_invalid_value_falls_back(
    tmp_path,
) -> None:
    settings = QSettings(str(tmp_path / "i18n.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui_language", "en_US")
    assert load_language(settings, "zh_CN") == EN_US
    settings.setValue("ui_language", "EN")
    assert load_language(settings, "zh_CN") == ZH_CN


def test_catalogs_have_identical_keys_and_placeholders() -> None:
    assert catalog_keys(ZH_CN) == catalog_keys(EN_US)
    for key in catalog_keys(ZH_CN):
        assert placeholder_names(ZH_CN, key) == placeholder_names(EN_US, key)


def test_manager_persists_once_and_unsubscribe_stops_notifications(tmp_path) -> None:
    settings = QSettings(str(tmp_path / "i18n.ini"), QSettings.Format.IniFormat)
    manager = LanguageManager(ZH_CN, settings)
    calls: list[str] = []
    unsubscribe = manager.subscribe(lambda: calls.append(manager.language))
    manager.set_language(EN_US)
    manager.set_language(EN_US)
    unsubscribe()
    manager.set_language(ZH_CN)
    assert calls == [EN_US]
    assert settings.value("ui_language") == ZH_CN
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_i18n.py
```

Expected: collection FAIL because `aacc.i18n` does not exist.

- [ ] **Step 3: Implement the typed manager**

Create the module with strict normalization, callback isolation and catalog
formatting:

```python
from __future__ import annotations

import logging
import string
from collections.abc import Callable
from typing import Literal, cast

from PySide6.QtCore import QLocale, QSettings

Language = Literal["zh_CN", "en_US"]
ZH_CN: Language = "zh_CN"
EN_US: Language = "en_US"
SUPPORTED_LANGUAGES = frozenset({ZH_CN, EN_US})
_logger = logging.getLogger("aacc.i18n")

CATALOGS: dict[Language, dict[str, str]] = {
    ZH_CN: {
        "language.switch_to_english": "切换到英文界面",
        "language.switch_to_chinese": "切换到中文界面",
        "status.unconfigured": "未配置",
        "status.idle": "空闲",
        "status.starting": "启动中",
        "status.thinking": "思考中",
        "status.running": "执行中",
        "status.waiting_input": "等待输入",
        "status.waiting_approval": "等待批准",
        "status.completed": "已完成",
        "status.warning": "警告",
        "status.error": "失败",
        "status.paused": "已暂停",
        "status.cancelled": "已取消",
        "status.stopped": "已停止",
        "status.unknown": "状态未知",
        "time.total": "总用时 {elapsed}",
        "quota.reset": "{month}月{day}日 {hour:02d}:{minute:02d} 重置",
    },
    EN_US: {
        "language.switch_to_english": "Switch to English",
        "language.switch_to_chinese": "Switch to Chinese",
        "status.unconfigured": "Not configured",
        "status.idle": "Idle",
        "status.starting": "Starting",
        "status.thinking": "Thinking",
        "status.running": "Running",
        "status.waiting_input": "Waiting for input",
        "status.waiting_approval": "Waiting for approval",
        "status.completed": "Completed",
        "status.warning": "Warning",
        "status.error": "Failed",
        "status.paused": "Paused",
        "status.cancelled": "Cancelled",
        "status.stopped": "Stopped",
        "status.unknown": "Unknown",
        "time.total": "Total {elapsed}",
        "quota.reset": "Resets {month}/{day} {hour:02d}:{minute:02d}",
    },
}


def detect_system_language(locale_name: str | None = None) -> Language:
    name = locale_name or QLocale.system().name()
    return ZH_CN if name.casefold().replace("-", "_").startswith("zh_") else EN_US


def load_language(settings: QSettings, locale_name: str | None = None) -> Language:
    value = settings.value("ui_language")
    if isinstance(value, str) and value in SUPPORTED_LANGUAGES:
        return cast(Language, value)
    return detect_system_language(locale_name)


def other_language(language: Language) -> Language:
    return EN_US if language == ZH_CN else ZH_CN
```

Implement `placeholder_names` with `string.Formatter().parse`, and implement
`LanguageManager` so subscriber exceptions log only the callback category and
do not stop remaining subscribers. `text()` must never log formatted values.

- [ ] **Step 4: Expand the shared catalog**

Add the complete shared key families used by later tasks:

```text
header.about, header.settings, header.hide
summary.tasks
group.running, group.completed, group.clear_all
empty.no_tasks
quota.kimi, quota.codex, quota.authorize, quota.authorizing
quota.unavailable, quota.partial, quota.stale, quota.refresh
quota.five_hour, quota.week, quota.month
quota.membership, quota.booster, quota.last_update
task.switch, task.manual_status, task.copy, task.rename, task.remove
task.no_message, task.last_activity, task.updated
compact.toggle, topmost.toggle, dock.top_right
tray.show_hide, tray.quit
common.cancel, common.done, common.apply
```

For every key, add natural Chinese and English text with identical format
placeholders. Preserve `5H`, `WEEK` and `MONTH` literally.

- [ ] **Step 5: Verify and commit**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_i18n.py
.venv/bin/ruff check src/aacc/i18n.py tests/test_i18n.py
.venv/bin/ruff format --check src/aacc/i18n.py tests/test_i18n.py
.venv/bin/mypy src/aacc
```

Commit:

```bash
git add src/aacc/i18n.py tests/test_i18n.py
git commit -m "feat: add typed bilingual UI catalog"
```

---

### Task 2: Header Toggle, Main Panel, Cards, Quotas and Tray

**Files:**
- Modify: `src/aacc/gui.py:90-730`
- Modify: `src/aacc/gui.py:951-1720`
- Modify: `tests/test_gui.py`
- Modify: `tests/test_codex_quota_bar.py`
- Modify: `tests/test_quota_bar.py`

**Interfaces:**
- Consumes: `LanguageManager`, `Language`, `other_language`
- Produces:
  - `status_name(status: TaskStatus, language: LanguageManager) -> str`
  - `format_quota_reset(..., language: LanguageManager) -> str`
  - `MainWindow(..., language_manager: LanguageManager | None = None)`
  - `MainWindow.toggle_language() -> None`
  - `MainWindow.retranslate_ui() -> None`
  - `TaskCard.retranslate_ui() -> None`
  - `QuotaBar.retranslate_ui() -> None`
  - `CodexQuotaBar.retranslate_ui() -> None`

- [ ] **Step 1: Write failing header/persistence tests**

Extend the `build_window` helper to accept an injected manager and add:

```python
def test_header_language_button_switches_live_and_persists(
    tmp_path, qtbot
) -> None:
    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    manager = LanguageManager(ZH_CN, settings)
    window, task_manager = build_window(
        tmp_path, qtbot, settings=settings, language_manager=manager
    )
    assert window.language_button.text() == "EN"
    assert window.running_group_label.text() == "运行中"

    window.language_button.click()

    assert manager.language == EN_US
    assert settings.value("ui_language") == EN_US
    assert window.language_button.text() == "中"
    assert window.running_group_label.text() == "Running"
    task_manager.close()


def test_header_replaces_compact_button_but_settings_and_tray_keep_compact(
    tmp_path, qtbot
) -> None:
    window, task_manager = build_window(tmp_path, qtbot)
    header_buttons = {
        button.objectName(): button for button in window.findChildren(QPushButton)
    }
    assert "languageButton" in header_buttons
    assert all(button.text() != "↕" for button in header_buttons.values())
    dialog = SettingsDialog(window)
    assert "切换紧凑 / 展开模式" in {
        button.text() for button in dialog.findChildren(QPushButton)
    }
    task_manager.close()
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_gui.py -k "language or compact_button"
```

Expected: FAIL because `MainWindow` has no language manager/button.

- [ ] **Step 3: Replace the header button**

Store the manager and retained widget references in `MainWindow.__init__`.
Replace:

```python
compact_button = QPushButton("↕")
compact_button.clicked.connect(lambda: self.set_compact(not self.compact_mode))
```

with:

```python
self.language_button = QPushButton()
self.language_button.setObjectName("languageButton")
self.language_button.clicked.connect(self.toggle_language)
```

Implement:

```python
def toggle_language(self) -> None:
    self.language_manager.set_language(other_language(self.language_manager.language))


def retranslate_ui(self) -> None:
    english_target = self.language_manager.language == ZH_CN
    self.language_button.setText("EN" if english_target else "中")
    self.language_button.setToolTip(
        self.language_manager.text(
            "language.switch_to_english"
            if english_target
            else "language.switch_to_chinese"
        )
    )
    self.refresh()
    self._schedule_adaptive_resize()
```

Subscribe once during initialization and unsubscribe in `closeEvent`.

- [ ] **Step 4: Make status/time/reset formatting language-aware**

Replace `STATUS_NAMES` with a key map:

```python
STATUS_NAME_KEYS = {
    TaskStatus.UNCONFIGURED: "status.unconfigured",
    TaskStatus.IDLE: "status.idle",
    TaskStatus.STARTING: "status.starting",
    TaskStatus.THINKING: "status.thinking",
    TaskStatus.RUNNING: "status.running",
    TaskStatus.WAITING_INPUT: "status.waiting_input",
    TaskStatus.WAITING_APPROVAL: "status.waiting_approval",
    TaskStatus.COMPLETED: "status.completed",
    TaskStatus.WARNING: "status.warning",
    TaskStatus.ERROR: "status.error",
    TaskStatus.PAUSED: "status.paused",
    TaskStatus.CANCELLED: "status.cancelled",
    TaskStatus.STOPPED: "status.stopped",
    TaskStatus.UNKNOWN: "status.unknown",
}


def status_name(status: TaskStatus, language: LanguageManager) -> str:
    return language.text(STATUS_NAME_KEYS[status])
```

Change `_elapsed_label` and `format_quota_reset` to accept the manager. English
reset text is `Resets M/D HH:MM`; Chinese remains
`M月D日 HH:MM 重置`. Keep `--` unchanged.

- [ ] **Step 5: Retain raw quota/card state and retranslate**

`QuotaBar`, `CodexQuotaBar` and `TaskCard` receive the manager. Store their last
render inputs:

```python
self._last_quota: KimiQuota | None = None
self._last_codex_quota: CodexQuotaSnapshot | None = None
self.state = state
```

Their existing update methods store raw objects before rendering.
`retranslate_ui()` replays only the render method and must not emit `clicked`,
call a quota service or modify task state.

Translate:

```text
Kimi/Codex quota summaries and every tooltip prefix
unknown/partial/stale/error/authorize states
task status, no-message text, last activity/update and total duration
context-menu actions and manual-status submenu
remove button tooltip/accessibility name
task summary, running/completed headings and empty state
```

- [ ] **Step 6: Translate and retain tray actions**

Store tray actions on `MainWindow`:

```python
self.tray_show_action = menu.addAction("")
self.tray_compact_action = menu.addAction("")
self.tray_quit_action = menu.addAction("")
```

Update their text in `retranslate_ui()`. Keep
`self.tray_compact_action.triggered -> set_compact(...)` unchanged.

- [ ] **Step 7: Add language rendering tests**

Cover both languages without polling:

```python
def test_existing_task_and_quota_widgets_retranslate_without_refreshing_services(...):
    # render a WAITING_APPROVAL card and quota snapshots
    manager.set_language(EN_US)
    assert card.status_label.text() == "Waiting for approval"
    assert "Resets " in window.quota_bar.reset_labels()[2]
    assert quota_refresh_calls == []


def test_english_quota_percent_and_reset_labels_do_not_overlap(...):
    manager.set_language(EN_US)
    percent_right = percent_label.mapTo(bar, percent_label.rect().topRight()).x()
    reset_left = reset_label.mapTo(bar, reset_label.rect().topLeft()).x()
    assert percent_right < reset_left
```

Update pre-existing Chinese assertions to inject `ZH_CN` explicitly rather
than relying on the developer machine locale.

- [ ] **Step 8: Verify and commit**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_gui.py tests/test_codex_quota_bar.py tests/test_quota_bar.py
.venv/bin/ruff check src/aacc/gui.py tests/test_gui.py \
  tests/test_codex_quota_bar.py tests/test_quota_bar.py
.venv/bin/mypy src/aacc
```

Commit:

```bash
git add src/aacc/gui.py tests/test_gui.py \
  tests/test_codex_quota_bar.py tests/test_quota_bar.py
git commit -m "feat: switch the live main UI language"
```

---

### Task 3: Settings, Selectors, Confirmations and Kimi Web Login

**Files:**
- Modify: `src/aacc/gui.py:480-950`
- Modify: `src/aacc/gui.py:1660-2160`
- Modify: `src/aacc/kimi_web_session.py`
- Modify: `src/aacc/kimi_web_quota_service.py`
- Modify: `src/aacc/app.py`
- Modify: `tests/test_gui.py`
- Modify: `tests/test_kimi_web_session.py`
- Modify: `tests/test_kimi_web_quota_service.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: the Task 1 manager and Task 2 `MainWindow(... language_manager=...)`
- Produces:
  - `KimiWebQuotaService(..., language_manager: LanguageManager | None = None)`
  - `KimiWebSession(..., language_manager: LanguageManager | None = None)`
  - `KimiWebSession.retranslate_ui() -> None`
  - fully localized Settings/selector/confirmation/authorization UI

- [ ] **Step 1: Write failing transient-dialog tests**

Add tests that create dialogs under both languages:

```python
def test_settings_and_selector_use_current_language(tmp_path, qtbot) -> None:
    manager = LanguageManager(EN_US)
    window, task_manager = build_window(
        tmp_path, qtbot, language_manager=manager
    )
    settings = SettingsDialog(window)
    selector = CodexTaskSelectionDialog([], set(), set(), window)
    assert settings.windowTitle() == "AACC Settings"
    assert selector.windowTitle() == "Select Codex tasks to monitor"
    assert "Compact / expanded mode" in settings.visible_button_texts()
    task_manager.close()


def test_open_kimi_login_dialog_retranslates_live(qapp, monkeypatch, tmp_path):
    manager = LanguageManager(ZH_CN)
    session = make_session(monkeypatch, tmp_path, language_manager=manager)
    session.open_login()
    manager.set_language(EN_US)
    assert session._login_dialog.windowTitle() == "Kimi membership login"
    assert session._login_status_label.text().startswith("Starting")
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_gui.py tests/test_kimi_web_session.py \
  -k "settings_and_selector or retranslates_live"
```

Expected: FAIL because dialogs still use hard-coded Chinese.

- [ ] **Step 3: Expand the dialog catalog**

Add exact key families with Chinese/English values:

```text
settings.title, settings.opacity, settings.config_file
settings.accessibility, settings.compact, settings.topmost, settings.dock
settings.select_codex, settings.select_kimi_code, settings.select_kimi_desktop
settings.selected_counts, settings.rotate_api, settings.kimi_fallback
settings.save_kimi_key, settings.kimi_web_login, settings.kimi_logout
settings.visible_agents
selector.running_hint, selector.auto_running
selector.select_all, selector.clear_all, selector.restore_auto
selector.start_monitoring
rename.title, rename.prompt
clear_completed.title, clear_completed.prompt
credentials.reset_title, credentials.reset_prompt
credentials.reset_done_title, credentials.reset_done_text, credentials.copy
accessibility.enabled, accessibility.disabled
accessibility.title, accessibility.prompt, accessibility.do_not_remind
about.title, about.body
kimi.device_title, kimi.device_opened, kimi.device_finished, kimi.device_cancel
kimi.web_title, kimi.web_explanation, kimi.web_starting
kimi.web_diagnostic, kimi.web_repair
kimi.web_load_failed, kimi.web_refresh_timeout, kimi.web_refresh_failed
```

Use literal protocol labels in both translations.

- [ ] **Step 4: Translate GUI dialogs at creation time**

Pass/obtain `window.language_manager` in `SettingsDialog`,
`TaskSelectionDialog` subclasses and `KimiAuthorizationDialog`. Replace every
hard-coded visible string in the specified `gui.py` ranges with
`manager.text(...)`.

Confirmations invoked from `MainWindow` read the manager at invocation time,
so a previous language switch is reflected without retaining a dialog.

- [ ] **Step 5: Inject language into the Kimi web path**

Extend the `_WebSessionLike` protocol with:

```python
def retranslate_ui(self) -> None: ...
```

Store the manager in `KimiWebQuotaService` and pass it when lazily creating:

```python
self._session = KimiWebSession(
    self._config_dir,
    self,
    language_manager=self.language_manager,
)
```

In `KimiWebSession`, subscribe once, retain the explanation label as
`self._login_explanation_label`, and update the existing dialog title,
explanation, status text and repair button in `retranslate_ui()`. Unsubscribe
in `close()`. Do not alter the WebView container or navigation/watchdog state.

- [ ] **Step 6: Assemble one manager in `app.py`**

After `_create_qapplication()`:

```python
settings = QSettings()
language_manager = LanguageManager(load_language(settings), settings)
```

Pass it into `build_runtime`/the default Kimi web factory and `MainWindow`.
Tests must prove exactly the same object reaches both paths.

Keep `--shutdown-for-update` and `--smoke-native-webview` dispatches before
QSettings/path/instance-guard work.

- [ ] **Step 7: Verify no stale subscriptions or sensitive translation data**

Add:

```python
def test_repeated_language_switch_and_session_close_do_not_duplicate_callbacks(...):
    session.open_login()
    manager.set_language(EN_US)
    manager.set_language(ZH_CN)
    session.close()
    manager.set_language(EN_US)
    assert update_call_count == 2


def test_webview_failure_translation_never_contains_url_or_fragment(...):
    # use ?code=remote-code#access_token=remote-token
    assert "remote-code" not in displayed_text
    assert "remote-token" not in displayed_text
```

- [ ] **Step 8: Verify and commit**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_gui.py tests/test_kimi_web_session.py \
  tests/test_kimi_web_quota_service.py tests/test_app.py
.venv/bin/ruff check src/aacc/gui.py src/aacc/kimi_web_session.py \
  src/aacc/kimi_web_quota_service.py src/aacc/app.py tests
.venv/bin/mypy src/aacc
git diff --check
```

Commit:

```bash
git add src/aacc/gui.py src/aacc/kimi_web_session.py \
  src/aacc/kimi_web_quota_service.py src/aacc/app.py \
  tests/test_gui.py tests/test_kimi_web_session.py \
  tests/test_kimi_web_quota_service.py tests/test_app.py
git commit -m "feat: localize dialogs and Kimi login"
```

---

### Task 4: Documentation, Synthetic Screenshot and Candidate Gates

**Files:**
- Modify: `scripts/capture_panel_screenshot.py`
- Modify: `docs/images/panel-overview.png`
- Modify: `tests/test_release_docs.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/windows-verification-checklist.en.md`
- Modify: `docs/windows-verification-checklist.zh-CN.md`
- Modify: `docs/release-notes-1.4.2.md`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.zh-CN.md`

**Interfaces:**
- Consumes: final `LanguageManager` and localized `MainWindow`
- Produces: privacy-safe screenshot and bilingual release/user documentation

- [ ] **Step 1: Write failing documentation contracts**

Extend `tests/test_release_docs.py`:

```python
def test_docs_describe_live_language_toggle_and_preserved_compact_mode() -> None:
    english = "\n".join(_read(name) for name in (
        "README.md", "docs/user-guide.en.md", "CHANGELOG.md",
    ))
    chinese = "\n".join(_read(name) for name in (
        "README.zh-CN.md", "docs/user-guide.md", "CHANGELOG.zh-CN.md",
    ))
    assert "live Chinese/English" in english
    assert "compact mode remains in Settings and the tray menu" in english
    assert "中英文即时切换" in chinese
    assert "紧凑模式保留在设置和托盘菜单" in chinese


def test_screenshot_uses_explicit_synthetic_chinese_locale() -> None:
    script = _read("scripts/capture_panel_screenshot.py")
    assert "LanguageManager(ZH_CN" in script
    assert "language_manager=language_manager" in script
    assert 'findChild(QPushButton, "languageButton")' in script
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_release_docs.py
```

Expected: FAIL because the documentation and capture script do not describe or
render the language button.

- [ ] **Step 3: Update the deterministic screenshot**

In the capture script, inject:

```python
language_manager = LanguageManager(ZH_CN, settings)
window = MainWindow(..., settings=settings, language_manager=language_manager)
language_button = window.findChild(QPushButton, "languageButton")
assert language_button is not None and language_button.text() == "EN"
```

Keep all fixed percentages, fake paths and `DEMO_NOW`. Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python \
  scripts/capture_panel_screenshot.py docs/images/panel-overview.png
```

Expected: a `420x577` PNG with only `IHDR`, optional `pHYs`, `IDAT`, `IEND`
chunks and no real home path/account/token.

- [ ] **Step 4: Update bilingual docs**

Document:

- header `EN`/`中` switches the complete UI immediately;
- first launch follows system language and explicit selection persists;
- compact mode moved from the header to Settings/tray, not removed;
- switching does not refresh quotas or change tasks/logins;
- both macOS and Windows behavior;
- 1.4.2 remains a candidate.

Add one unchecked manual item to each Windows checklist for repeated switching
with real tasks, quota data and an open Kimi login dialog. Add the corresponding
macOS manual sign-off reminder to release notes.

- [ ] **Step 5: Run full local verification**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts/capture_panel_screenshot.py \
  scripts/smoke_windows_webview.py
.venv/bin/ruff format --check src tests scripts/capture_panel_screenshot.py \
  scripts/smoke_windows_webview.py
.venv/bin/mypy src/aacc
git diff --check
```

- [ ] **Step 6: Build and verify the macOS candidate**

Run:

```bash
scripts/build_app.sh
scripts/build_dmg.sh
codesign --verify --deep --strict dist/AACC.app
```

Confirm the DMG and `.sha256` are version 1.4.2 candidate artifacts. Do not tag
or publish.

- [ ] **Step 7: Commit**

```bash
git add scripts/capture_panel_screenshot.py docs/images/panel-overview.png \
  tests/test_release_docs.py README.md README.zh-CN.md docs \
  CHANGELOG.md CHANGELOG.zh-CN.md
git commit -m "docs: present the bilingual 1.4.2 interface"
```

---

### Task 5: Final Review, Main Integration, Hosted Windows and Desktop Artifacts

**Files:**
- No planned source files; fixes discovered by review/CI receive their own TDD commit.

**Interfaces:**
- Consumes: Tasks 0-4
- Produces: pushed `main`, green hosted CI, verified desktop DMG/Setup candidates

- [ ] **Step 1: Request final independent review**

Review the entire feature range against:

```text
docs/superpowers/specs/2026-07-28-bilingual-ui-toggle-design.md
docs/superpowers/plans/2026-07-28-bilingual-ui-toggle.md
```

Block integration on every Critical/Important issue. Fix with a failing
regression test and re-review.

- [ ] **Step 2: Re-run fresh verification**

Run the exact Task 4 full verification commands after the last review fix.
Record exact pass/skip counts.

- [ ] **Step 3: Merge to `main` and verify again**

Fast-forward only when possible, then repeat pytest/Ruff/format/mypy on the
merged `main`.

- [ ] **Step 4: Push and monitor GitHub Actions**

Push `main` and require:

```text
quality (macos-latest)
quality (windows-2022)
quality (windows-2025-vs2026)
windows-frozen-2022
windows-package-2025
```

The Windows 2025 job must compile Inno Setup, install the Setup, run the
installed `AACC.exe --smoke-native-webview`, verify its evidence, and upload
`AACC-Windows-Setup`.

- [ ] **Step 5: Download and verify Windows artifacts**

Download only from the final green `main` run:

```bash
gh run download <run-id> -n AACC-Windows-Setup -D <temporary-directory>
```

Verify:

```text
AACC-1.4.2-Setup.exe
AACC-1.4.2-Setup.exe.sha256
AACC-1.4.2-windows-x64-windows-2025-vs2026.zip
```

Recompute SHA-256 and require exact companion-file agreement. Copy the verified
Setup and checksum to the Desktop.

- [ ] **Step 6: Copy verified macOS artifacts**

Copy the newly built `AACC-1.4.2.dmg` and checksum to the Desktop and recompute
SHA-256 after copying.

- [ ] **Step 7: Preserve release gates**

Report hosted evidence separately from manual gates. Do not create a tag or
Release. Explicitly leave open:

```text
real Windows 10/11 standard-user checklist
separate unprivileged-account ACL denial
real Kimi/Codex, SmartScreen, tray, focus/hotkey, long-run checks
macOS/Windows native-session persistence and logout sign-off
macOS/Windows live bilingual-switch sign-off
```
