# Task 3 packaged WebView smoke review fix

## Scope

Address the review finding that the Windows native WebView smoke ran from the
source virtual environment rather than the installed frozen product, that its
Qt timer was not a process-level deadline, and that a late JavaScript callback
could overwrite an earlier failure.

## TDD evidence

The new packaging contract tests were run before implementation and failed
because `src/aacc/webview_smoke.py` did not exist and CI still invoked
`uv run python scripts/smoke_windows_webview.py`.

After implementation, the focused regression tests passed:

```text
tests/test_app.py -k native_webview_smoke: 2 passed
tests/test_packaging.py -k windows_webview_smoke or windows_2025_ci_contractually: 2 passed
tests/test_webview_smoke.py: 1 passed
```

## Implementation

- Moved the native diagnostic into `aacc.webview_smoke`; its static import from
  `aacc.app` makes the module part of the frozen product.
- Added the exact Windows-only `AACC.exe --smoke-native-webview` dispatch before
  configuration paths and `InstanceGuard`; extra arguments remain normal startup.
- Kept the 30-second internal Qt watchdog and added a `_finished` check at the
  beginning of the JavaScript callback.
- Replaced CI's source-venv native smoke with the installed executable invoked
  by `test_windows_package.ps1` immediately after fresh install/hash validation.
  `Invoke-ExternalDeadline` provides a 40-second process-level deadline, the
  harness restores `QT_QPA_PLATFORM`, captures process evidence, and asserts the
  product-process baseline even on diagnostic failure.

## Remaining environment limit

The hosted Windows Setup/product smoke has not been run from this macOS
worktree. Its contract tests and all cross-platform checks are recorded in the
task handoff.
