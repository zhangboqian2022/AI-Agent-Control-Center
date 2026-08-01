# AACC Known Limitations

- This release is signed with a local self-signed certificate and is not Apple-notarized. Verify its SHA-256 before using **Open Anyway**.
- Kimi Desktop monitoring reads only the daimon catalog under `~/Library/Application Support/kimi-desktop` (WAL-aware `mode=ro`, deliberately not `immutable=1` so fresh WAL content stays visible). If a future Kimi Desktop version moves this data outside Application Support, disk-read (TCC) permission must be re-evaluated. The Chat tab is a kimi.com web shell whose conversations live in the cloud and cannot be monitored.
- Desktop automation defaults to a five-second osascript timeout, configurable from 2 to 15 seconds. A slow first activation may need a higher value.
- Accessibility permission is required for global hotkeys and keyboard/dictation injection. App focus without injection remains available.
- API credential rotation is local-GUI-only. The old token is invalid immediately; there is no grace period or remote rotation endpoint.
- `aacc-run` cleans up children after SIGINT/SIGTERM but cannot guarantee cleanup after SIGKILL, power loss, or an operating-system crash.
- Codex discovery targets metadata compatibility identifier `2026-07`. A future Codex metadata-format change may temporarily degrade discovery; AACC then preserves last-known states and shows a warning.
- Codex quota is a read-only weekly indicator sourced from bounded local structured metadata. AACC accepts only a fresh 10080-minute window, ignores legacy shorter windows, and reports unavailable when that metadata is absent or changes; it does not call a private Codex quota API.
- The supported floor is macOS 13. Hardware/version rows not marked passed in the integration checklist are not claimed as tested.
- On Windows, terminal focus relies on window-title matching and can miss when the shell rewrites the title.
- `SetForegroundWindow` is subject to the Windows foreground lock; AACC degrades and logs when activation is denied.
- The Kimi Desktop daimon path on Windows is a best-effort candidate path and has not been verified on real hardware.
- The Windows build is unsigned; first launch shows a SmartScreen prompt.
- Windows CI runs on hosted Windows Server SKUs; consumer Windows 10/11 behaviors (SmartScreen, tray, window focus/hotkeys, long-running sessions) are covered by a manual verification checklist, not by automation.
- F13–F20 hotkeys require an Fn-layer mapping on most Windows keyboards.

- OpenCode usage (Go plan) is extracted from the rendered /go workspace page on opencode.ai; if opencode.ai changes that page layout, extraction may need updating. The opencode session cookie lives in the per-application web storage of the macOS web view.
- OpenCode task status is inferred from local part snapshots (official idle/busy is a runtime event, not persisted) with a 90-second activity window; status is approximate. OpenCode support is macOS first; Windows is a later iteration.
