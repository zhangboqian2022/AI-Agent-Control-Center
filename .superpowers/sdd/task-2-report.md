# Task 2 report: live main-UI language switching

## Status

Complete on `codex/v1.4.2-ui-language-exec`.

Task 2 is limited to the header, persistent main panel, task cards, Kimi/Codex
quota bars and tray actions. Settings/task-selection/authorization dialogs,
Kimi Web internals and application assembly remain for later tasks.

## TDD evidence

The implementation was split into behavior-level RED/GREEN cycles:

- Header injection/persistence and compact-entry retention:
  - RED: 2 failures because `MainWindow` did not accept a language manager.
  - GREEN: 2 passed.
- Status names, no-message text and localized reset timestamps:
  - RED: missing `status_name`/language-aware APIs.
  - GREEN: 3 passed.
- Retained Kimi/Codex quota snapshots:
  - RED: 3 failures because quota bars did not accept a manager or retranslate.
  - GREEN: 3 passed.
- Persistent main-window state and tray lifecycle:
  - GREEN verification covers raw card/quota identity, zero refresh/service calls,
    unchanged compact/geometry/login state, one subscription, close-time
    unsubscription and the retained compact tray signal.
- Header style parity:
  - RED: the new `languageButton` was not covered by the existing header selector.
  - GREEN: 1 passed after extending the loaded selector without changing the QSS
    source file.
- Destination-language tooltip semantics:
  - RED: 1 failure showed the tooltip was rendered in the current language.
  - GREEN: 1 passed with the approved `Switch to English` / `切换到中文`
    destination-language wording.
- Chinese quota-tooltip compatibility found during self-review:
  - RED: 2 failures proved the original countdown wording had been replaced.
  - GREEN: 2 passed after retaining Chinese countdowns while English uses localized
    absolute reset text.

## Implementation

- Replaced the header `↕` compact action with destination labels `EN` / `中`.
- Injected one `LanguageManager`, subscribed exactly once after widget
  construction, persisted through the manager and unsubscribed on actual close.
- Kept compact mode in Settings and the tray; the tray action still invokes the
  existing `set_compact` path.
- Re-rendered `TaskCard`, `QuotaBar` and `CodexQuotaBar` from retained raw
  `TaskState`, `KimiQuota` and `CodexQuotaSnapshot` objects.
- Translated task status, elapsed/total time, no-message and activity/update text,
  card tooltips, dynamic context menus and remove-button accessibility text.
- Translated main summary/group/empty labels, header tooltips, quota states and
  tooltip prefixes, reset timestamps and retained tray actions.
- Preserved `5H`, `WEEK` and `MONTH` literally.
- Preserved established Chinese UI wording and quota countdown tooltips.
- Language switching directly retranslates retained widgets. It does not call
  `MainWindow.refresh`, discovery/quota services, task mutation, login/logout or
  compact setters. This intentionally follows the approved no-refresh invariant
  instead of the obsolete `self.refresh()` example in the plan.
- Existing UI tests now inject `ZH_CN` explicitly so host locale cannot alter
  legacy assertions.

## Verification

Fresh final results:

```text
python -m pytest -q tests/test_gui.py tests/test_codex_quota_bar.py tests/test_quota_bar.py
91 passed in 1.64s

python -m pytest -q
830 passed, 7 skipped in 10.63s

ruff format --check src tests
112 files already formatted

ruff check src tests
All checks passed!

mypy src/aacc
Success: no issues found in 48 source files

git diff --check
clean
```

## Self-review and concerns

- No Critical or Important issue remains in Task 2 scope.
- The default manager remains deterministic Chinese for direct legacy
  construction; Task 3/application assembly must inject the persisted/system
  manager to activate first-run locale detection in the product.
- Dialog localization is intentionally incomplete here and must be completed by
  Task 3 rather than expanded into this commit.

## Independent-review follow-up

Addressed the two Important findings without expanding into the Task 3
confirmation-dialog scope.

- RED:

  ```text
  /Users/zhangboqian/Desktop/codelight/.venv/bin/python -m pytest -q \\
    tests/test_gui.py::test_language_switch_preserves_geometry_after_queued_events \\
    tests/test_codex_quota_bar.py::test_codex_unknown_snapshot_retranslates_without_clearing_raw_state
  2 failed
  ```

- GREEN:

  ```text
  /Users/zhangboqian/Desktop/codelight/.venv/bin/python -m pytest -q \\
    tests/test_gui.py::test_language_switch_preserves_geometry_after_queued_events \\
    tests/test_codex_quota_bar.py::test_codex_unknown_snapshot_retranslates_without_clearing_raw_state
  2 passed
  ```

- Removed the language-retranslation call to `_schedule_adaptive_resize()`: a
  language change now retains its window geometry after queued Qt events are
  processed.
- `CodexQuotaBar.show_quota()` now retains an UNKNOWN raw snapshot; a pure
  `_render_unknown()` renders its state, so language retranslation neither
  clears the snapshot nor calls stateful `show_unknown()`.
- Final verification:

  ```text
  pytest tests/test_gui.py tests/test_codex_quota_bar.py tests/test_quota_bar.py
  93 passed
  ruff check: passed
  ruff format --check: 4 files already formatted
  mypy src/aacc: Success, 48 source files
  ```

- Commit command:

  ```text
  git commit -m "fix: preserve live UI state during language switch"
  ```
