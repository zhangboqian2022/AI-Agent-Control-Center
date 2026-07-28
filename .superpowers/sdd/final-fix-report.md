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
