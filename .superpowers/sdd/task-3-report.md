# Task 3 Report: Native Windows WebView Smoke and Documentation

## Delivered

- Added `scripts/smoke_windows_webview.py`, a Windows-only native Qt WebView
  smoke. It initializes Qt WebView before `QApplication`, displays a `QDialog`
  with a native window container, loads deterministic inline HTML, waits for a
  successful load, and requires the exact JavaScript result within 30 seconds.
  Its failure output contains fixed categories only.
- Added the native smoke to the Windows Server 2025 packaging job immediately
  after the frozen/installed product smoke, with `QT_QPA_PLATFORM=windows`.
- Updated the English and Chinese README, user guides, verification checklists,
  candidate notes, and changelogs for current-user Evergreen provisioning,
  network only when the Runtime is absent, the 15-second diagnostic, and the
  separate real Windows 10/11 missing-Runtime/already-present-Runtime gates.
- Updated installer contracts for the intentional temporary WebView2 payload
  and scoped the `Exec`/`ShellExec` prohibition to the graceful AACC shutdown
  path, where it remains enforced.

## TDD Evidence

- RED: `.venv/bin/python -m pytest -q tests/test_packaging.py -k webview`
  produced `2 failed, 4 passed, 38 deselected` because the smoke script and CI
  step did not exist.
- RED: after adding the documentation contract, the same target produced
  `1 failed, 6 passed, 38 deselected` because the runtime provisioning and
  Windows 10/11 coverage text was absent.
- GREEN: the target produced `7 passed, 38 deselected` after implementation.
- The two pre-existing installer-contract failures were reproduced first, then
  the scoped assertions produced `20 passed`.

## Final Verification

```text
.venv/bin/python -m pytest -q
804 passed, 7 skipped

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/ruff format --check src tests
108 files already formatted

.venv/bin/mypy src/aacc
Success: no issues found in 46 source files

git diff --check
exit 0
```

`tests/test_kimi_web_session.py` received only the formatter's current line
wrapping so the mandated full format check is green; its behavior is unchanged.

## Concern

This macOS worktree cannot execute the Windows-native controller. The new CI
step supplies hosted Windows Server evidence, while the documented consumer
Windows 10/11 Runtime-absent and Runtime-present checks remain release gates.
