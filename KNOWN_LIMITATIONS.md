# AACC Known Limitations

- This GitHub prerelease is ad-hoc signed and not Apple-notarized. Verify its SHA-256 before using **Open Anyway**. Stable `v1.3.0` is not released under this condition.
- Desktop automation defaults to a five-second osascript timeout, configurable from 2 to 15 seconds. A slow first activation may need a higher value.
- Accessibility permission is required for global hotkeys and keyboard/dictation injection. App focus without injection remains available.
- API credential rotation is local-GUI-only. The old token is invalid immediately; there is no grace period or remote rotation endpoint.
- `aacc-run` cleans up children after SIGINT/SIGTERM but cannot guarantee cleanup after SIGKILL, power loss, or an operating-system crash.
- Codex discovery targets metadata compatibility identifier `2026-07`. A future Codex metadata-format change may temporarily degrade discovery; AACC then preserves last-known states and shows a warning.
- The supported floor is macOS 13. Hardware/version rows not marked passed in the integration checklist are not claimed as tested.
