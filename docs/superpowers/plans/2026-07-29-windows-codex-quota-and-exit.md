# Windows Codex Quota Synchronization and Exit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover live Windows Codex quota synchronization without restarting AACC and provide reliable tray and header quit controls.

**Architecture:** Add a rediscovering live quota reader that safely resolves the current Codex executable for every poll and constructs the existing one-shot app-server reader on demand. Keep the local JSONL reader as fallback. Filter tray activation reasons and route both localized quit entry points through the existing application shutdown path.

**Tech Stack:** Python 3.12, PySide6, psutil, pytest/pytest-qt, Ruff, mypy, PyInstaller, existing native Windows spawn broker.

## Global Constraints

- Poll automatically every 60 seconds and keep click-to-refresh.
- Never read ChatGPT cookies/browser cache or send a model request.
- Accept process-derived `codex.exe` only from an OpenAI/ChatGPT installation path.
- Frozen Windows app-server launches must continue through `aacc-spawn.exe`.
- Preserve macOS behavior and the window-close-to-tray behavior.
- Add no dependency and log no credentials, account data, or executable paths.

---

### Task 1: Safe late Codex executable discovery

**Files:**
- Modify: `src/aacc/codex_app_server.py`
- Test: `tests/test_codex_app_server.py`

**Interfaces:**
- Consumes: `find_codex_executable(...) -> Path | None`
- Produces: `find_running_desktop_codex(...) -> Path | None` and bounded Windows desktop candidates used by `find_codex_executable`

- [ ] **Step 1: Write failing discovery tests**

Add tests with an injected process iterator proving that an absolute regular
`C:\Users\u\AppData\Local\Programs\ChatGPT\resources\codex.exe` is accepted,
an unrelated `C:\Temp\codex.exe` is rejected, access failures are ignored, and
the known desktop resource locations follow override/PATH/npm precedence.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:
`../.venv/bin/python -m pytest tests/test_codex_app_server.py -q`

Expected: new tests fail because running desktop discovery and resource
candidates do not exist.

- [ ] **Step 3: Implement bounded discovery**

Use `psutil.process_iter(("name", "exe"))`, catch `psutil.Error` and `OSError`,
require the basename `codex.exe`, require an absolute regular file, and require
an `openai` or `chatgpt` path component. Add only explicit
`%LOCALAPPDATA%`/`%PROGRAMFILES%` ChatGPT resource candidates; do not recursively
scan user or system directories.

- [ ] **Step 4: Run focused tests**

Run:
`../.venv/bin/python -m pytest tests/test_codex_app_server.py -q`

Expected: all Codex app-server tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aacc/codex_app_server.py tests/test_codex_app_server.py
git commit -m "fix: discover Windows desktop Codex safely"
```

### Task 2: Rediscover live quota on every refresh

**Files:**
- Modify: `src/aacc/codex_app_server.py`
- Modify: `src/aacc/app.py`
- Test: `tests/test_codex_app_server.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `CodexAppServerReader`, `find_codex_executable`, and a callable that builds the platform-specific one-shot reader
- Produces: `RediscoveringCodexQuotaReader.read_latest() -> CodexQuotaSnapshot`

- [ ] **Step 1: Write failing reader and factory tests**

Cover a locator returning `None` on the first read and a valid executable on the
second, a changed executable path, an invalid broker command returning UNKNOWN,
and the default app factory installing the rediscovering reader even when no
executable exists at startup.

- [ ] **Step 2: Verify focused failure**

Run:
`../.venv/bin/python -m pytest tests/test_codex_app_server.py tests/test_app.py -q`

Expected: tests fail because the default factory permanently stores `None`.

- [ ] **Step 3: Implement the rediscovering reader**

Add a small reader with injected locator and reader factory. Each
`read_latest()` resolves the executable, returns UNKNOWN if absent, builds the
one-shot reader, and delegates. Refactor `_default_codex_quota_service_factory`
so broker validation is prepared once but executable lookup happens per poll.
Keep `CompositeCodexQuotaReader(rediscovering_live, local)` unchanged in
fallback semantics.

- [ ] **Step 4: Run focused tests**

Run:
`../.venv/bin/python -m pytest tests/test_codex_app_server.py tests/test_app.py tests/test_codex_quota_service.py -q`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aacc/codex_app_server.py src/aacc/app.py tests/test_codex_app_server.py tests/test_app.py
git commit -m "fix: rediscover live Codex quota source"
```

### Task 3: Reliable tray and header quit controls

**Files:**
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/i18n.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: `QSystemTrayIcon.ActivationReason` and `MainWindow.quit_application()`
- Produces: `MainWindow._on_tray_activated(reason)` and `MainWindow.quit_button`

- [ ] **Step 1: Write failing GUI tests**

Add tests proving only `ActivationReason.Trigger` toggles visibility, context
activation does not toggle, the power button invokes explicit quit, the tray is
hidden during quit, and Chinese/English tooltip text changes with the language.

- [ ] **Step 2: Verify GUI test failure**

Run:
`QT_QPA_PLATFORM=offscreen ../.venv/bin/python -m pytest tests/test_gui.py -q`

Expected: new tests fail because every activation toggles and no power button
exists.

- [ ] **Step 3: Implement the controls**

Add a fixed-size header power button beside the minus button, add localized
`header.quit` strings, connect it to `quit_application`, replace the tray lambda
with `_on_tray_activated`, and accept only `Trigger`. In
`quit_application`, set `_quitting`, hide the tray, close the window, and call
`QGuiApplication.quit()`.

- [ ] **Step 4: Run focused GUI and shutdown tests**

Run:
`QT_QPA_PLATFORM=offscreen ../.venv/bin/python -m pytest tests/test_gui.py tests/test_shutdown_windows.py -q`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aacc/gui.py src/aacc/i18n.py tests/test_gui.py
git commit -m "fix: make Windows quit controls reliable"
```

### Task 4: Documentation, regression verification, and Windows candidate

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/windows-verification-checklist.md`
- Modify: `docs/windows-verification-checklist.en.md`
- Modify: `docs/release-notes-1.4.2.md`
- Modify: `AGENTS.md`
- Test: `tests/test_release_docs.py`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: implemented UI and quota behavior
- Produces: bilingual user/release documentation and the next Windows Setup candidate

- [ ] **Step 1: Add failing documentation assertions**

Require the bilingual guide/release notes to state that Codex refreshes every
60 seconds, click refreshes immediately, late ChatGPT/Codex startup is recovered,
right-click exposes Quit, and the header power button exits completely.

- [ ] **Step 2: Verify documentation test failure**

Run:
`../.venv/bin/python -m pytest tests/test_release_docs.py tests/test_packaging.py -q`

Expected: new assertions fail before documentation is updated.

- [ ] **Step 3: Update bilingual documentation**

Document the behavior without claiming browser-cache access or completed
Windows 10/11 manual release gates. Record the candidate commit, CI run, Setup
name, and checksum only after those values exist.

- [ ] **Step 4: Run all local quality gates**

Run:

```bash
../.venv/bin/python -m pytest -q
../.venv/bin/ruff check src tests
../.venv/bin/ruff format --check src tests
../.venv/bin/mypy src/aacc
```

Expected: 0 failures and 0 diagnostics.

- [ ] **Step 5: Build and validate the Windows candidate**

Push the branch, run the repository’s established Windows CI/package workflow,
download the resulting `AACC-1.4.2-Setup.exe`, verify its SHA-256 and strict
artifact contents, and copy the Setup plus `.sha256` to the Desktop. Do not tag
or publish a formal release before the Windows 10/11 manual gate.

- [ ] **Step 6: Commit documentation evidence**

```bash
git add README.md README.zh-CN.md docs AGENTS.md tests/test_release_docs.py tests/test_packaging.py
git commit -m "docs: describe Codex refresh and exit controls"
```
