# Windows Codex Quota Synchronization and Exit Design

**Status:** Approved on 2026-07-29

## Goal

Make AACC recover live Codex quota synchronization when the Windows ChatGPT/Codex
desktop application is opened or restarted after AACC, and provide two reliable,
obvious ways to quit AACC completely.

## Confirmed behavior

### Codex quota

- AACC continues to poll Codex quota every 60 seconds.
- Clicking the Codex quota bar continues to request an immediate refresh.
- The live quota source is rediscovered on every refresh instead of being fixed
  once during AACC startup.
- Windows discovery supports the existing override, `PATH`, and npm locations,
  plus a running official ChatGPT/Codex `codex.exe` and bounded known desktop
  installation resource locations.
- A process-derived executable is accepted only when it is an absolute regular
  `codex.exe` below a path whose components identify an OpenAI or ChatGPT
  installation. Arbitrary same-named processes are rejected.
- Every successful refresh calls the read-only Codex app-server
  `account/rateLimits/read`; AACC does not read ChatGPT cookies or browser cache
  and does not submit a model request.
- If no safe live executable is available, the existing bounded local session
  metadata reader remains the fallback. A later refresh can recover live mode
  without restarting AACC.
- Failures remain non-fatal and do not expose executable paths, account data, or
  credentials in logs.

### Quit behavior

- A tray `Trigger` activation (normal left click on Windows) toggles the AACC
  window.
- Context-menu activation is never treated as a toggle, so right-click reliably
  leaves the tray menu open.
- The tray context menu retains the localized `Quit AACC` / `退出 AACC` action.
- A localized power button is added to the main-window header and quits AACC
  directly. The existing minus button continues to hide AACC to the tray.
- Quit marks the window as quitting, hides the tray icon, closes the window, and
  requests Qt application shutdown. Runtime cleanup remains owned by the existing
  application `finally` path, which stops quota services, discovery, automation,
  and SQLite.

## Compatibility and security constraints

- macOS behavior and executable discovery order remain compatible.
- No new dependency is added; `psutil`, PySide6, and the existing Windows broker
  are reused.
- Frozen Windows builds must continue launching Codex only through
  `aacc-spawn.exe`.
- Existing language switching updates the new quit tooltip.
- The window close event still hides to tray unless an explicit quit is in
  progress.

## Verification

- Unit tests cover late executable appearance, executable replacement, rejection
  of untrusted running processes, and live-to-local fallback.
- GUI tests cover tray trigger filtering, right-click/context activation, the
  header quit button, localization, and explicit close semantics.
- Existing full pytest, Ruff, and strict mypy checks must pass.
- The Windows package test must exercise the frozen application and broker
  contract; the generated Setup remains a candidate until Windows 10/11 manual
  release gates are completed.
