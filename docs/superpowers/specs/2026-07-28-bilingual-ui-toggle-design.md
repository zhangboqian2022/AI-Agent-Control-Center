# AACC Live Chinese/English UI Toggle Design

**Date:** 2026-07-28  
**Target:** AACC 1.4.2 candidate  
**Status:** Approved direction; implementation pending

## Goal

Replace the first header button (`↕`) with a clear Chinese/English language
action and let the user switch the application UI immediately on both macOS
and Windows. Preserve compact/expanded mode in Settings and the tray menu,
because compact mode still hides task-card detail rows and reduces the adaptive
window height.

## User experience

- On first launch, use Chinese when the operating-system language is Chinese;
  otherwise use English.
- Persist the explicit selection in `QSettings` under `ui_language`. Only
  `zh_CN` and `en_US` are valid stored values; invalid values fall back to the
  system-language rule.
- In Chinese mode, the first header button reads `EN` and its tooltip says
  `Switch to English`. In English mode it reads `中` and its tooltip says
  `切换到中文`. The label describes the action, not the current state.
- Clicking the button updates the visible application immediately without a
  restart. Window position, size, quota data, monitored tasks, login state and
  compact-mode state do not change.
- The main panel, task cards, quota summaries and tooltips, status names,
  reset-time text, header tooltips, context menus, tray menu, Settings,
  task-selection dialogs, confirmations, accessibility guidance, About dialog,
  Kimi authorization dialogs and Kimi website-login diagnostics use the
  selected language.
- Product and protocol labels such as `Codex`, `Kimi`, `5H`, `WEEK`, `MONTH`,
  API, WebView2 and filesystem paths stay unchanged.
- Startup security failures remain bilingual. They can appear before the
  normal UI is available and must remain supportable regardless of a damaged
  preference.

## Approaches considered

### Selected: Python catalog plus explicit retranslation

Add a small typed localization module with stable message keys, Chinese and
English catalogs, placeholder validation, system-language detection and a
`LanguageManager` that notifies subscribers. Persistent widgets implement a
bounded `retranslate_ui()` method; menus and modal dialogs read the active
catalog when they are created.

This fits the current Python/PySide application, has no new build tool, works
inside PyInstaller on both platforms and supports immediate switching.

### Rejected: translate only the main panel

This is faster but leaves Settings, tray actions and authorization dialogs in
Chinese, producing an inconsistent and misleading language switch.

### Deferred: Qt `.ts`/`.qm` translation pipeline

Qt Linguist is appropriate for a larger translator workflow, but it adds
`lrelease`, packaged resource generation and source-string extraction for only
two maintained languages. The catalog interface must remain replaceable so a
future `.qm` migration does not change UI call sites.

## Architecture

### `aacc.i18n`

The new module owns:

- language constants `zh_CN` and `en_US`;
- strict normalization and `QLocale`-based first-run detection;
- a complete two-language catalog keyed by semantic identifiers;
- `tr(key, **values)` formatting with the same placeholder set in both
  languages;
- `LanguageManager`, which stores the active language and offers subscribe /
  unsubscribe callbacks.

Unknown message keys are programming errors in tests and development. At
runtime, formatting failure returns the English catalog value without
including sensitive values in logs or dialogs.

### Application assembly

After `QApplication` is created, the app loads `ui_language` from the existing
AACC `QSettings`, creates one `LanguageManager`, and passes it to the main
window and Kimi web quota/session path. Changing the language writes the
preference synchronously and notifies active subscribers.

The manager is an application dependency rather than ambient mutable global
state. Tests can inject a manager with a deterministic language, and shutdown
can unsubscribe without leaking callbacks.

### Main window and child widgets

`MainWindow` owns the header language button and coordinates retranslating
persistent UI:

- quota bars;
- summary and group labels;
- header buttons and tooltips;
- existing task cards;
- tray menu actions.

`TaskCard`, `QuotaBar` and `CodexQuotaBar` retain raw model state, not rendered
Chinese text, so `retranslate_ui()` can reproduce current text without a new
poll or state transition. Dynamic context menus and dialogs resolve messages
from the manager each time they open.

The old header compact button and its signal are removed. The existing
Settings button and tray action continue to call `set_compact`, and the
`compact_mode` configuration/QSettings key remains unchanged.

### Kimi native web login

`KimiWebSession` receives the same manager. It retains its login explanation,
startup status, repair action and diagnostic widgets, so an open login dialog
can update immediately. Error signals remain fixed internal categories; the
selected language is applied at the presentation boundary and no failing URL,
query, fragment, cookie or token is introduced into translation data.

## State and event flow

1. App creates `QApplication`.
2. `ui_language` is read and normalized, or the system language is detected.
3. One `LanguageManager` is passed to UI components.
4. The header language button calls `set_language(other_language)`.
5. The manager persists the selection, then notifies subscribers on the Qt UI
   thread.
6. Persistent widgets re-render from their retained model state; newly opened
   menus/dialogs use the new language automatically.

Language switching never triggers discovery, quota refresh, login/logout,
configuration token rotation or task-state mutation.

## Layout behavior

English strings are generally wider. Persistent labels that can grow use
word-wrap or elision, while small header buttons keep fixed bounds. After a
language change, `MainWindow` schedules the existing adaptive-height
recalculation rather than storing a new authoritative height. Quota percentage,
progress-bar and reset-time columns must remain non-overlapping in both
languages.

The documentation screenshot is regenerated with fake/non-account data after
the feature is implemented. It must show the new language button and the final
quota layout without exposing a real account or token.

## Error handling

- Invalid stored language: fall back to system detection and overwrite the
  normalized value after the first explicit switch.
- Missing catalog key or mismatched placeholders: fail tests; runtime fallback
  is a fixed English message and logs only the semantic key.
- Subscriber failure: log its component category and continue notifying the
  remaining UI; never log rendered secrets or arbitrary remote text.
- Translation does not change the existing bilingual startup-security
  fallback, WebView2 repair behavior or authorization privacy boundary.

## Verification

Use TDD and cover:

- system-language detection and strict persisted-value normalization;
- catalog parity and placeholder parity;
- header button text/tooltip in each language;
- immediate switch and persistence across a new `MainWindow`;
- compact mode still available in Settings/tray and no longer in the header;
- task status, elapsed text, quota summaries/reset dates and error states in
  both languages;
- open Settings/task-selection/Kimi login dialogs use or adopt the active
  language;
- tray/context menus rebuild in the selected language;
- repeated switching does not duplicate subscribers, dialogs or signal
  connections;
- English layout does not overlap quota percentages/reset times and adaptive
  height remains bounded;
- full pytest, Ruff, formatting, mypy, macOS build and Windows frozen/Setup
  product smoke.

The existing Windows 10/11 manual gates remain mandatory. Add a manual item to
switch languages repeatedly while Kimi login and live quota/task data are
present on each platform.

## Release boundary

This is part of the unreleased 1.4.2 candidate. Update both READMEs, both user
guides, both changelogs, 1.4.2 release notes and the simulated product
screenshot. Do not create `v1.4.2` or a formal Release until the existing real
Windows 10/11, cross-account ACL, native-session and new bilingual-UI manual
gates are signed.
