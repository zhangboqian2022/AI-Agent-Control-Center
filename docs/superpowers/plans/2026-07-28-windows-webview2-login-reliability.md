# Windows WebView2 Login Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a blank Kimi login dialog on Windows by guaranteeing the
Microsoft WebView2 Runtime during Setup and exposing bounded runtime/navigation
failures in the dialog.

**Architecture:** The pinned Setup build embeds Microsoft's signed Evergreen
bootstrapper and Inno Setup enforces the documented registry precondition
before mutating AACC. `KimiWebSession` owns a 15-second first-event watchdog and
switches its dialog from the native surface to a safe repair message when the
backend never initializes. A hosted Windows smoke creates the real native
controller, loads inline HTML, and executes JavaScript.

**Tech Stack:** Python 3.12+, PySide6 6.11 Qt WebView/WebView2, PowerShell 5,
Inno Setup 6.7.1, pytest/pytest-qt, GitHub Actions.

## Global Constraints

- Setup remains per-user, non-elevated, and defaults to
  `%LocalAppData%\Programs\AACC`.
- The bootstrapper URL and SHA-256 are immutable/pinned; Authenticode must be
  `Valid` before packaging.
- Never commit the Microsoft executable or any build/cache directory.
- Do not log passwords, tokens, cookies, query strings, fragments, or remote
  response bodies.
- Keep Qt WebEngine excluded.
- Follow red-green-refactor and keep all existing 788 tests green.
- Do not create `v1.4.2` or a formal Release before the existing manual gates.

---

### Task 1: WebView2 Runtime Setup Gate

**Files:**
- Modify: `scripts/build_windows_installer.ps1`
- Modify: `installer/AACC.iss`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: Microsoft's x64 Evergreen bootstrapper at the immutable July 22,
  2026 delivery URL.
- Produces: `build/installer/MicrosoftEdgeWebview2Setup.exe`, verified before
  ISCC; Inno functions `WebView2RuntimeInstalled` and
  `EnsureWebView2Runtime(var ErrorMessage: String): Boolean`.

- [ ] **Step 1: Write failing packaging tests**

Add assertions that the PowerShell build script contains the immutable URL,
SHA-256 `0223fa1e8d5bd5e4344fb8734e60d088e79f262c0a24444d01f240bc996f04e5`,
`Get-AuthenticodeSignature`, and stages exactly
`MicrosoftEdgeWebview2Setup.exe`. Assert `AACC.iss` embeds it with
`dontcopy`, reads both documented `{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`
`pv` keys, runs `/silent /install`, rechecks the Runtime, and returns a
bilingual `PrepareToInstall` error on failure.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_packaging.py -k webview
```

Expected: FAIL because the bootstrapper and Runtime gate are absent.

- [ ] **Step 3: Implement the pinned build input**

In `build_windows_installer.ps1`, define the URL, digest, leaf name, minimum
size, and a validation function that checks a regular non-reparse file, exact
SHA-256, minimum size, and valid Authenticode. Download to a `.download` sibling,
validate before atomic move, validate the final cache again, and leave the
verified file in `build\installer` for ISCC.

- [ ] **Step 4: Implement the Inno precondition**

Embed the bootstrapper with:

```text
Source: "..\build\installer\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy noencryption
```

Implement registry reads against `HKLM32` and `HKCU32`; accept only a non-empty
value other than `0.0.0.0`. When absent, extract the temporary file, call
`Exec(..., '/silent /install', ..., ewWaitUntilTerminated, ResultCode)`, and
require both exit code zero and a successful registry recheck. Call this after
the existing lock/reparse preflight but before returning success from
`PrepareToInstall`.

- [ ] **Step 5: Run targeted tests and commit**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_packaging.py -k webview
.venv/bin/ruff check tests/test_packaging.py
git diff --check
```

Commit:

```bash
git add scripts/build_windows_installer.ps1 installer/AACC.iss tests/test_packaging.py
git commit -m "fix: provision WebView2 for Windows login"
```

### Task 2: Visible Login Backend Diagnostics

**Files:**
- Modify: `src/aacc/kimi_web_session.py`
- Modify: `tests/test_kimi_web_session.py`

**Interfaces:**
- Produces: `WEBVIEW_STARTUP_TIMEOUT_MS = 15_000`,
  `WEBVIEW2_HELP_URL`, `_webview_startup_watchdog`, and dialog state transitions
  for waiting, failed, and loaded states.

- [ ] **Step 1: Write failing dialog-state tests**

Extend the existing fake dialog/widget setup. Verify `open_login()` starts a
15-second single-shot timer and shows a bilingual startup message. Verify the
timeout hides the native container and shows the WebView2 repair action. Verify
`LoadStatus.Failed` produces the network/WebView2 message. Verify the first
non-failed loading event stops the timer, hides the status/action, and keeps the
native container visible. Verify the repair action opens only the fixed
Microsoft HTTPS help URL.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_kimi_web_session.py -k "dialog or startup"
```

Expected: FAIL because no startup watchdog or repair UI exists.

- [ ] **Step 3: Implement minimal UI state**

Create and retain the status label, repair button, and window-container widget
with the dialog. Start the watchdog immediately before `setUrl`. On timeout or
`Failed`, stop the active refresh, hide the native container, show a sanitized
bilingual message/button, emit the existing generic error, and never include
the failing URL. On any real loading event, stop the startup timer; on
non-failure restore the container and hide the diagnostic controls.

- [ ] **Step 4: Verify behavior and commit**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_kimi_web_session.py
.venv/bin/ruff check src/aacc/kimi_web_session.py tests/test_kimi_web_session.py
.venv/bin/mypy src/aacc
```

Commit:

```bash
git add src/aacc/kimi_web_session.py tests/test_kimi_web_session.py
git commit -m "fix: expose Windows WebView login failures"
```

### Task 3: Native Windows Smoke, Documentation, and Candidate

**Files:**
- Create: `scripts/smoke_windows_webview.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_packaging.py`
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
- Produces: a Windows-only script that exits zero only after a native
  `QWebView` loads deterministic inline HTML and returns the expected JavaScript
  result.

- [ ] **Step 1: Write failing CI/script contract tests**

Assert the smoke script initializes Qt WebView before QApplication, rejects
non-Windows execution, uses a 30-second hard deadline, creates a visible
window-container, loads inline HTML, waits for `Succeeded`, runs JavaScript, and
exits nonzero on timeout/failure/result mismatch. Assert the Windows 2025 job
runs it after Setup installation with `QT_QPA_PLATFORM=windows`.

- [ ] **Step 2: Verify contract tests fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_packaging.py -k webview
```

Expected: FAIL because the script and CI step do not exist.

- [ ] **Step 3: Implement the native smoke and CI step**

Use `QTimer`, `QDialog`, `QWidget.createWindowContainer`, and `QWebView.loadHtml`.
Record only fixed diagnostic categories. In CI, clear the job-level offscreen
override for this step and run:

```powershell
$env:QT_QPA_PLATFORM = "windows"
uv run python scripts/smoke_windows_webview.py
```

- [ ] **Step 4: Update bilingual user/release documentation**

Explain that Setup provisions Microsoft's Evergreen WebView2 Runtime as the
current user, requires network access only when the Runtime is absent, and
shows a repair diagnostic instead of a blank dialog. Add an explicit Windows
10/11 verification item for first login on a machine without the Runtime and
for an already-installed Runtime.

- [ ] **Step 5: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/aacc
git diff --check
```

Expected: all tests and checks pass.

- [ ] **Step 6: Commit, push, and verify hosted Windows**

Commit:

```bash
git add .github/workflows/ci.yml scripts/smoke_windows_webview.py tests/test_packaging.py README.md README.zh-CN.md docs CHANGELOG.md CHANGELOG.zh-CN.md
git commit -m "test: exercise native Windows WebView login"
git push origin codex/v1.4.2-webview2-runtime
```

Require all quality jobs, Windows Server 2022 frozen smoke, and Windows Server
2025 Setup/native-WebView/product/artifact jobs to pass. Then fast-forward
`main`, repeat final CI, download that run's `AACC-Windows-Setup`, verify its
SHA-256, and replace the desktop candidate. Do not tag or publish.
