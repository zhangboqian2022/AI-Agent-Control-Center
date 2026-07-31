# Final Branch Review Fix Report

## Scope

Resolved all three Important and both Minor findings from the final review of
the bilingual UI branch. The fixes are limited to presentation semantics,
safe error categories, dialog localization, i18n contracts, and language
preference persistence. No quota refresh cadence, network request, native
WebView ownership/lifecycle, release artifact, tag, push, or publication
behavior was changed.

## Important 1: Dynamic usage, manual state, and automation text

- Kimi usage formatting now receives the localized cache label from
  `LanguageManager`; retained usage metadata re-renders in both directions.
- AACC-created manual-state and automation-error messages now persist stable
  semantic categories instead of the currently selected language.
- Automation success subtitles retain trusted action semantics (`focus`,
  `voice`, and `key:*`) and re-render from those semantics after a language
  switch. Unknown controller/agent text remains external text and is never
  translated.
- AACC-owned automation exceptions carry an allowlisted category while their
  existing exception strings and controller return strings remain compatible
  with API/CLI consumers.

Focused GREEN evidence:

```text
Kimi metrics: 2 passed, 9 deselected
GUI usage/manual/automation: 12 passed, 71 deselected
macOS/Windows automation: 40 passed
```

Strict mypy subsequently exposed a `key` placeholder/parameter collision in
the key-action path. A GUI regression reproduced it as `1 failed, 1 error`;
renaming the presentation/i18n semantic-key parameters made the focused
regression and i18n suite pass (`15 passed`).

## Important 2: Source-separated, safe Kimi quota errors

- Kimi Code/OAuth/API and Kimi membership WebView errors now use distinct
  public category enums and distinct Qt signals.
- Unknown or exception-derived values fold into a source-specific safe
  category; exception bodies, credentials, HTTP bodies, URLs, and paths are
  neither retained for presentation nor logged by the quota worker.
- `QuotaBar` caches Code and Web errors independently, translates each only at
  the UI boundary, and preserves the other source's warning when one source
  updates successfully.
- OAuth completion now returns stable Code categories on failure while the
  existing success payload remains empty.

Focused GREEN evidence:

```text
quota bar / GUI wiring / web quota service / code quota service:
84 passed
```

The regression coverage includes unknown sensitive-looking values and verifies
that neither tooltips nor logs expose them.

## Important 3: Dialogs and native action-button language

- The Kimi device-authorization dialog now subscribes to the shared language
  manager, retranslates its open title/labels/cancel action, and unsubscribes
  idempotently on close.
- Rename, clear-completed, credential-reset/result, Accessibility guidance,
  and About dialogs now use instances whose action labels are explicitly set
  from the AACC catalog.
- A single `_localize_standard_buttons` helper overwrites Qt-provided
  `OK`/`Yes`/`Cancel`/`Close` labels after standard buttons are installed;
  behavior therefore does not depend on the OS/Qt locale.

RED evidence:

```text
2 failed
```

The failures showed a Chinese OAuth dialog after switching to English and use
of static Qt dialogs. The focused tests then passed (`2 passed`), including a
fake Qt environment that deliberately supplied the opposite language for
standard buttons. The complete GUI file passed:

```text
86 passed
```

## Minor 1: i18n failure contracts and quota literals

- Unknown translation keys return the fixed English fallback
  `Interface text unavailable` and log only the semantic key.
- Formatting failures return the corresponding raw English catalog template
  without formatting or logging supplied values.
- Subscriber records contain an allowlisted component category; one failing
  subscriber logs only that safe category and cannot stop later subscribers.
- Chinese and English catalogs have matching keys/placeholders, and both
  preserve `5H`, `WEEK`, and `MONTH` exactly.

## Minor 2: Durable language preference

- `set_language` now writes `ui_language`, calls `QSettings.sync()`, checks
  `QSettings.status()`, and only then notifies subscribers.
- A persistence failure emits a fixed log message without path or value
  details and does not prevent live UI retranslation.
- Selecting the current language remains a no-op.
- A regression opens a second `QSettings` instance from inside a subscriber
  and proves that it observes the new language immediately.

M1/M2 RED and GREEN evidence:

```text
RED:   7 failed, 7 passed
GREEN: 14 passed
Affected GUI / Kimi Web / app assembly: 154 passed
```

## Final re-review: External controller success text

The last Important re-review finding identified that the unknown-action
controller fallback still called `.upper()`, even though external/controller
text must remain opaque. A regression passed `Build id AbC/path` through an
unknown controller completion and failed RED with:

```text
expected: Build id AbC/path
actual:   BUILD ID ABC/PATH
1 failed
```

The minimal production fix removes only that fallback case conversion.
Trusted internal `focus`, `voice`, and `key:*` mappings remain semantic and
retranslatable. Fresh verification:

```text
focused external/trusted automation success paths:
4 passed, 84 deselected

complete tests/test_gui.py:
88 passed
```

## Final verification

```text
QT_QPA_PLATFORM=offscreen pytest --cov=src/aacc --cov-report=xml -q
873 passed, 7 skipped, 14 warnings

ruff check
All checks passed!

ruff format --check
115 files already formatted

mypy src/aacc
Success: no issues found in 49 source files

diff-cover coverage.xml --compare-branch=origin/main --fail-under=90
95% (705 changed lines, 34 missing)

git diff --check
clean
```

The 14 warnings are existing `ResourceWarning` reports for test SQLite
connections and do not fail the suite. No release artifact was built or
modified, and the existing real macOS/Windows manual gates remain open.

---

# OpenCode Quota Branch Review Fix Report (2026-07-31)

Branch `feat/opencode-quota`, review package `9aff648..e9a3cd9` (8 commits).
Commit: `2b8e89b fix: start opencode polling, guard refresh re-entry, and wire session i18n`

## Fix 1 (CRITICAL): wire opencode service start in app.py

`src/aacc/app.py` added `start_opencode_web_quota()` hook mirroring
`start_kimi_web_quota` exactly (try/except containment, `_logger.error`,
rollback stop, `stop_after_shutdown`), skipping when
`runtime.opencode_web_quota_service.workspace_url` is empty (avoids creating a
QWebView session on empty config). Wired via `QTimer.singleShot(0, ...)` right
after the kimi hook in the startup sequence.

RED (`tests/test_app.py`, before the fix):

```text
FAILED tests/test_app.py::test_deferred_opencode_web_start_runs_after_event_loop
```

GREEN (after the fix):

```text
46 passed
```

Coverage tests added for all hook branches: start runs after event loop;
empty URL skips; start-then-shutdown calls `stop_after_shutdown` (stop raising
exercises the post-shutdown error log); shutdown before the deferred hook
skips start; start failure rolls back; start failure after shutdown stops the
partial service.

## Fix 2 (IMPORTANT): arm the refresh watchdog on the navigation path

`src/aacc/opencode_web_session.py`: `refresh()` now calls
`self._start_refresh_watchdog()` immediately after `_start_refresh_generation()`
(QTimer.start on an active single-shot restarts safely; the arm in
`_run_fetch_script` stays); `open_login()` arms it after its generation start.

RED:

```text
FAILED tests/test_opencode_web_session.py::test_refresh_navigation_path_arms_watchdog
FAILED tests/test_opencode_web_session.py::test_open_login_arms_watchdog
```

GREEN:

```text
tests/test_opencode_web_session.py: 40 passed
```

## Fix 3 (IMPORTANT): refresh re-entry guard + login-in-progress protection

`refresh()` gains `if self._refreshing: return` at the top (kimi-identical,
`kimi_web_session.py:230-237`) so the 300s tick cannot setUrl over an in-flight
OAuth page; `open_login()` sets `self._refreshing = True`. Reset paths verified
complete: quota / unauthorized / `_finish_refresh_with_error` / logout
`_invalidate_refresh` / `close` all reset it.

RED:

```text
FAILED tests/test_opencode_web_session.py::test_refresh_re_entry_is_inert_while_in_flight
FAILED tests/test_opencode_web_session.py::test_refresh_during_login_progress_does_not_navigate
```

GREEN: `40 passed` (same run as Fix 2).

## Fix 4 (IMPORTANT): wire i18n subscriber for the session

`__init__` subscribes `self.retranslate_ui` with
`component="opencode_web_session"` (already allowlisted in
`LANGUAGE_SUBSCRIBER_COMPONENTS`); `close()` calls `self._unsubscribe_language()`
before `view.deleteLater()`, kimi-identical. Test mirrors
`test_repeated_language_switch_and_session_close_do_not_duplicate_callbacks`:
switching the shared `LanguageManager` retranslates the live login dialog
label, and `close()` leaves `_subscribers == []`.

**Deviation (documented):** the gui.py part of this finding
(`opencode_web_quota_service.retranslate_ui()` in `MainWindow.retranslate_ui`)
was NOT added. `OpenCodeWebQuotaService` has no `retranslate_ui` method
(`opencode_web_quota_service.py` has none, and the file is outside the
contract's touch list), and gui.py never calls
`kimi_web_quota_service.retranslate_ui` either — kimi's session retranslates
solely through its own subscription to the one shared `LanguageManager`
instance that app.py passes to both the window and the services (asserted by
`test_run_application_assembles_one_language_manager_for_runtime_and_window`).
Adding the call would raise `AttributeError` at runtime on every retranslate.

RED:

```text
FAILED tests/test_opencode_web_session.py::test_language_switch_retranslates_login_dialog_via_subscription
```

GREEN: `40 passed`.

## Fix 5 (MINOR): UNKNOWN quota must not flip authorized

`gui.py::_on_opencode_quota_updated` now sets
`self._opencode_authorized = quota.status is not QuotaStatus.UNKNOWN`, so an
UNKNOWN snapshot keeps the bar in login-click mode instead of refresh mode.

RED:

```text
FAILED tests/test_gui_quota_wiring.py::test_opencode_unknown_quota_keeps_login_click_behavior
```

GREEN:

```text
tests/test_gui_quota_wiring.py: 35 passed
```

## Fix 6 (MINOR): align JS subscription matcher with parser tolerance

Fetch script `findSubscription` now matches when any of
rollingUsage/weeklyUsage/monthlyUsage exists
(`node.rollingUsage || node.weeklyUsage || node.monthlyUsage`), aligned with
the parser's PARTIAL semantics; script-string test updated.

RED:

```text
FAILED tests/test_opencode_web_session.py::test_fetch_script_embeds_workspace_id_and_server_hash
```

GREEN: `40 passed`.

## Full gate

```text
pytest -q:                       1052 passed, 7 skipped
ruff check src tests:            All checks passed!
ruff format --check src tests:   129 files already formatted
mypy src/aacc:                   Success: no issues found in 57 source files
```

## diff-cover

```text
pytest --cov=src/aacc --cov-report=xml -q
diff-cover coverage.xml --compare-branch=origin/main --fail-under=90
Total:   655 lines
Missing: 0 lines
Coverage: 100%
```

Files changed (6, per contract): `src/aacc/app.py`, `src/aacc/gui.py`,
`src/aacc/opencode_web_session.py`, `tests/test_app.py`,
`tests/test_gui_quota_wiring.py`, `tests/test_opencode_web_session.py`.

---

# OpenCode Quota Fix 7 (2026-07-31): reset login state on manual dialog dismissal

Commit: `fix: reset opencode login state when the login dialog is dismissed`

`src/aacc/opencode_web_session.py`: `open_login()` now connects
`dialog.finished.connect(self._login_dialog_closed)` once when the dialog is
created (kimi-identical, `kimi_web_session.py:209`). New
`_login_dialog_closed(self, _result)` mirrors kimi semantics: guarded by
`self._login_dialog_open` (the quota-success path sets it False inside
`_close_login_dialog()` before `close()`, so that path is a no-op — idempotent),
and on a genuine user dismissal it sets `_login_dialog_open = False` and calls
`_invalidate_refresh()` (bumps generation, clears `_active_refresh_generation`,
`_refreshing = False`, stops the watchdog). No error is emitted — user-initiated
close is not a failure.

RED:

```text
FAILED tests/test_opencode_web_session.py::test_manual_login_dialog_dismissal_resets_state
1 failed, 1 passed
```

GREEN:

```text
tests/test_opencode_web_session.py: 34 passed
```

New tests:
- `test_manual_login_dialog_dismissal_resets_state`: open_login → `dialog.close()`
  (real QDialog `finished` signal) → asserts `_login_dialog_open is False`,
  `_refreshing is False`, watchdog inactive, `_active_refresh_generation is None`,
  no errors; subsequent `refresh()` takes the fetch path without navigation
  (scripts grow, URL unchanged).
- `test_quota_success_close_does_not_double_handle`: quota bridge success →
  `_close_login_dialog` → `dialog.close()` → `finished` fires
  `_login_dialog_closed` idempotently; no exception, `login_state_changed`
  emitted exactly once (`[True]`), no errors.

## Full gate

```text
pytest -q:                       1054 passed, 7 skipped
ruff check src tests:            All checks passed!
ruff format --check src tests:   129 files already formatted
mypy src/aacc:                   Success: no issues found in 57 source files
```

## diff-cover

```text
pytest --cov=src/aacc --cov-report=xml -q
diff-cover coverage.xml --compare-branch=origin/main --fail-under=90
Total:   663 lines
Missing: 0 lines
Coverage: 100%
```

Files changed (2, per contract): `src/aacc/opencode_web_session.py`,
`tests/test_opencode_web_session.py`.
