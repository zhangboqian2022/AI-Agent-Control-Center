# Task 3 Report: Dialogs, Kimi Login and Application Language Assembly

## Delivered

- Expanded the typed Chinese/English catalog for Settings, all three task
  selectors, rename/clear/reset confirmations, credential results,
  accessibility guidance, About, Kimi device authorization, Kimi membership
  login/diagnostics, and transient task-action feedback.
- Localized transient `gui.py` surfaces at creation/invocation time. Product
  and protocol labels (`AACC`, `Codex`, `Kimi`, API, OAuth, WebView2, `5H`,
  `WEEK`, `MONTH`) remain literal.
- Resolved the Task 2 review minor: selection, manual-status state/message,
  copy confirmation, queued automation, unknown-operation, and empty API Key
  feedback now consistently follow the selected language.
- Injected `LanguageManager` into `KimiWebQuotaService` and its lazy
  `KimiWebSession`. An open Kimi login dialog retranslates its title,
  explanation, waiting/diagnostic status, and WebView2 repair action live.
- Kimi web sessions subscribe once and unsubscribe on idempotent `close()`.
  Navigation, retained native container, watchdog generations, logout cleanup,
  and the five-minute refresh cadence are unchanged.
- Remote OAuth/bridge error bodies and WebView URL query/fragment data are no
  longer presented or logged. Only fixed localized error categories cross the
  UI boundary.
- `_run_application` now creates one manager after `QApplication` from the
  existing `QSettings`, then passes that exact object to `build_runtime`,
  `MainWindow`, the default Kimi web service, and the lazy session.
  `--shutdown-for-update` and `--smoke-native-webview` remain dispatched before
  path, settings, and instance-guard work. Startup security dialogs remain
  deliberately bilingual.

## TDD Evidence

- RED: Settings/selector/Kimi live-retranslation target produced `2 failed`
  because the dialogs were hard-coded and `KimiWebSession` did not accept a
  manager. GREEN: `2 passed`.
- RED: confirmation/accessibility/About/credential/feedback target produced
  `3 failed, 1 passed`. GREEN: `4 passed`.
- RED: session privacy/service injection/application assembly target produced
  `3 failed, 1 passed`. GREEN: `5 passed`.
- RED: queued automation and empty API Key target produced `1 failed`.
  GREEN: `1 passed`.
- RED: OAuth failure privacy target produced `1 failed`. GREEN: `1 passed`.
- Final focused target, including catalog parity:
  `155 passed`.

## Hard-coded Visible-Text Audit

`kimi_web_session.py` contains no hard-coded visible Chinese text.
The remaining Chinese literals found in `gui.py` are:

- explicit `ZH_CN` branches paired with English rendering;
- constructor placeholders that are retranslated before the window is shown;
- the required `中` language-switch action.

The product/protocol literals and bilingual startup-security fallback are
intentionally unchanged. No URL, query, fragment, cookie, token, or remote
response body is formatted into translations or logs.

## Verification

```text
python -m pytest -q
845 passed, 7 skipped

focused dialog/Kimi/app/catalog tests
155 passed

ruff check
All checks passed!

mypy src/aacc
Success: no issues found in 48 source files
```

The final fresh format, lint, mypy, and diff checks are run immediately before
the Task 3 commit.

## Concern

Qt standard `Yes`/`Cancel`/`Close` buttons continue to use the platform Qt
translation, while every AACC-owned confirmation title, prompt, checkbox, and
custom action uses the selected AACC language. This preserves native dialog
behavior and the existing confirmation API.
