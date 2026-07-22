# AACC Known Limitations

- This release is signed with a local self-signed certificate and is not Apple-notarized. Verify its SHA-256 before using **Open Anyway**.
- Kimi Desktop monitoring reads only the daimon catalog under `~/Library/Application Support/kimi-desktop` (WAL-aware `mode=ro`, deliberately not `immutable=1` so fresh WAL content stays visible). If a future Kimi Desktop version moves this data outside Application Support, disk-read (TCC) permission must be re-evaluated. The Chat tab is a kimi.com web shell whose conversations live in the cloud and cannot be monitored.
- Desktop automation defaults to a five-second osascript timeout, configurable from 2 to 15 seconds. A slow first activation may need a higher value.
- Accessibility permission is required for global hotkeys and keyboard/dictation injection. App focus without injection remains available.
- API credential rotation is local-GUI-only. The old token is invalid immediately; there is no grace period or remote rotation endpoint.
- `aacc-run` cleans up children after SIGINT/SIGTERM but cannot guarantee cleanup after SIGKILL, power loss, or an operating-system crash.
- Codex discovery targets metadata compatibility identifier `2026-07`. A future Codex metadata-format change may temporarily degrade discovery; AACC then preserves last-known states and shows a warning.
- The supported floor is macOS 13. Hardware/version rows not marked passed in the integration checklist are not claimed as tested.
