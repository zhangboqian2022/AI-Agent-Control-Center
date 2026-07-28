# Task 2 lifecycle follow-up

## Scope

Fixed the Kimi native WebView login-dialog lifecycle review findings in
`src/aacc/kimi_web_session.py`, with regression coverage in
`tests/test_kimi_web_session.py`.

## RED

Added and ran the lifecycle regressions before changing production code:

```text
.venv/bin/python -m pytest -q tests/test_kimi_web_session.py -k 'login_dialog_close_retains_native_container or login_dialog_close_invalidates_attempt or stale_login_startup_timeout'
FFF
```

The failures proved that closing cleared the retained dialog/container,
late loading events could start a fetch after close, and startup timeouts
were not bound to a login attempt. A follow-up success-to-background-refresh
test was also run RED before its minimal state transition was added.

## Fix

- Keep one dialog and `createWindowContainer` for the session lifetime.
  Closing now hides the dialog while preserving the owned-widget references.
- Track explicit dialog visibility and a monotonically increasing login-attempt
  token. Closing invalidates the active refresh and prevents late successful
  loading events or bridge payloads from persisting a login or quota.
- Bind each startup-watchdog callback to its attempt token; loading, closing,
  and a stale callback cannot affect a later attempt.
- On a successful interactive login, mark the dialog closed before accepting
  it, while preserving normal background refreshes.

The tests include a real PySide6 `QWindow` / `QWidget.createWindowContainer`
ownership check without loading the native WebView backend, plus session-level
construction/reuse and late-callback regressions using the existing WebView
fake.

## GREEN and verification

```text
.venv/bin/python -m pytest -q tests/test_kimi_web_session.py
33 passed

.venv/bin/ruff check src/aacc/kimi_web_session.py tests/test_kimi_web_session.py
All checks passed!

.venv/bin/mypy src/aacc
Success: no issues found in 46 source files
```

The complete suite was also run:

```text
798 passed, 7 skipped, 2 failed
```

The two failures are pre-existing/unrelated Windows installer contract checks:
`tests/test_windows_installer_contract.py::{test_inno_setup_packages_only_the_reviewed_onedir_roots,test_inno_setup_uses_only_the_graceful_aacc_shutdown_command}`.
They assert against `installer/AACC.iss`, which is outside this task's changed
files and has not been modified here.

## Remaining concern

This validates Qt ownership with a generic `QWindow`; native WebView2 backend
startup still requires the existing real Windows smoke gate.
