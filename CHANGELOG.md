# Changelog

[中文版本](CHANGELOG.zh-CN.md)

## 1.3.3-rc.1 — 2026-07-22

- [Security] `save_config` now rejects a symlinked configuration directory (defense in depth alongside the existing config-file symlink check).
- [Stability] Card removal goes through a single dispatch funnel that logs an ERROR for task ids with an unknown brand prefix instead of silently ignoring them.
- [Performance] Process-liveness probes (Kimi Code / Kimi Desktop discovery) cache the matching PID and only rescan the process tree after it dies or changes identity, instead of walking the whole tree every poll.
- [Delivery] CI now runs a non-blocking `pip-audit` dependency vulnerability scan.
- [Docs] Clarified that the Kimi Desktop catalog is opened `mode=ro` deliberately, not `immutable=1` (WAL freshness), updated the discovery-service count and the signing wording in the docs, and added a TCC contingency note for future daimon data-path changes.

## 1.3.2 — 2026-07-22

- [Security] Placeholder-shaped API tokens are now rejected by prefix (`change-me`, `replace-`, `your-token`, `placeholder`), and the shipped example config leaves the token empty — loading it always rotates to a fresh random credential instead of running with a public constant.
- [UX] Hiding an agent brand now persists across restarts: the new-brand visibility seeding runs once via a migration key instead of force re-adding Kimi brands on every launch.
- [API] `/api/v1/reload-config` now returns 501 Not Implemented instead of a misleading 200.
- [Stability] `aacc doctor` and the app resolve the runtime database path through one shared helper, so diagnostics check the file the app actually mounts.
- [UX] Rotating the API token no longer writes the clipboard automatically; the new token is shown in a dialog and copied only when the user clicks Copy.
- [Performance] Expired state-history cleanup is throttled to once per hour with an index on `created_at`, and the panel skips its per-second card-layout rebuild when grouping and order are unchanged.
- [Stability] Task-state subscriber failures are logged instead of silently swallowed.
- [Docs] Fixed the README claim that the installer runs tests (they are opt-in via `AACC_RUN_TESTS=1`) and added the 1.3.1 test report.

## 1.3.1 — 2026-07-22

- [UX] Switching to a task now restores its minimized windows (both terminal and macOS app targets), so the focused window actually appears on screen ready for typing.
- [UX] Removed the voice-input and key-injection actions from the card context menu; the underlying automation remains available through global hotkeys.

## 1.3.0 — 2026-07-22

- [UX] Kimi Code cards now show the session's working-directory name next to the status (full path in the tooltip), making it easy to tell concurrent projects apart.
- [UX] Fixed the panel failing to reopen after being minimized: clicking the tray icon now restores the minimized window instead of hiding it, and clicking the Dock icon (or Cmd-Tabbing back to the app) brings back a hidden panel like other Mac apps.
- [UX] Accessibility permission changes take effect within seconds while the app is running — global hotkeys start as soon as permission is granted (no restart needed) and stop when it is revoked — and the permission guidance dialog now offers a "do not show again" checkbox.
- [Delivery] The build signs with the stable self-signed "AACC Local Development" identity when present in the keychain, so macOS accessibility grants survive rebuilds and upgrades; it falls back to ad-hoc signing otherwise.

## 1.3.0-rc.6 — 2026-07-21

- [Feature] Added Kimi Desktop (Kimi.app) monitoring: conversations are discovered from the daimon runtime's local read-only catalog, agent conversations reuse the Kimi Code turn analysis for full running/waiting/completed status, chats show simplified generating/idle states, and cards focus Kimi.app the same way Codex cards focus Codex.app.
- [UX] Added Kimi Desktop task selection, retention, and muting to the panel and settings, and merged discovery-health reporting across all three monitored brands.
- [Stability] Read the Kimi Desktop catalog through the sqlite WAL so conversations created while the app is running are discovered immediately, and dropped an ambiguous process-name fallback in the Kimi.app liveness check.

## 1.3.0-rc.5 — 2026-07-21

- [Stability] Fixed Kimi sessions dropping to idle during long in-turn silences (slow LLM responses, long tool calls, or context summarization): a turn still in progress now keeps the running status within a bounded active-turn window (default 30 minutes) instead of going idle after 90 seconds without file activity; sessions past the window still fall back to idle so crashed sessions cannot show running forever.
- [UX] Added an About dialog (ⓘ in the panel header) showing the running version and the matching DMG installer name.

## 1.3.0-rc.4 — 2026-07-20

- [Stability] Changed Kimi wire completion scanning to a bounded full-line reverse scan, and updated privacy wording to state that sensitive prompt and response bodies are never stored, displayed, or logged.
- [UX] Restored a running task removed with `×` automatically when it starts running again, so it returns to the monitored panel.
- [UX] Added custom task-card renaming, persisted per task id.
- [Delivery] Fixed the installer: it no longer depends on `python3`, unconditionally quits the old instance before replacement, skips tests by default (enable with `AACC_RUN_TESTS=1`), and the uninstaller is aware of `AACC_INSTALL_ROOT`.
- [UX] The panel now surfaces Kimi discovery health alongside Codex discovery health.
- [Docs] Corrected the security-model wording in SECURITY.md.
- [Delivery] Added a CI workflow running lint, type checks, and tests on macOS.

## 1.3.0-rc.3 — 2026-07-20

- [Stability] Added Kimi Code local session discovery with running/idle/completed status lights and wire-tail turn-completion detection that inspects event types only, never prompt or response content.
- [UX] Added Kimi task selection and monitoring preferences to the panel, mirroring the Codex auto-monitoring, retention, and muting behavior.
- [Delivery] Version housekeeping for the 1.3.0-rc.3 prerelease across the package, build scripts, and packaging tests.

## 1.3.0-rc.2 — 2026-07-20

- [UX] Rebuilt task cards as a compact horizontal hierarchy with a large left status light, small agent badge, prominent task name, raised whole-run timer, and one-line activity summary.
- [UX] Made the floating window grow and shrink with visible tasks, capped it at 80% of the current screen's available height, and enabled internal scrolling only beyond that cap.
- [Stability] Changed Codex discovery to a five-second cadence and added fixed privacy-safe activity labels for analysis, code edits, tests, builds, inspection, searches, command execution, and completion.
- [Stability] Preserved one timer across short active/waiting turns, froze terminal total duration, and reset timing only when a terminal task starts a new run.
- [Stability] Recognized current Codex input, command, patch, and permission approval events; recovered cold-start run times beyond the activity tail with cached incremental metadata scans; and prioritized waiting tasks with other active work.
- [Privacy] Activity classification never copies prompt, response, command, credential, or file-content payloads into task messages, logs, or the panel.
- [Stability] Stopped GUI refresh timers before shutdown and made task-manager closure idempotent, preventing a queued Qt refresh from touching a closed SQLite connection.

## 1.3.0-rc.1 — 2026-07-20

- [Security] Made configuration writes atomic, repaired invalid API tokens, rejected whitespace-bearing credentials, enforced private config/database permissions, added local credential rotation, strengthened log redaction, and removed AppleScript text interpolation.
- [Stability] Serialized complete desktop automation transactions in a bounded worker, cancelled timed-out queued/input operations before delayed injection, kept Qt responsive, preserved task run timestamps, suppressed duplicate history, bounded SQLite retention, and exposed recoverable Codex discovery health.
- [Stability] Added verified PID identity, single-instance locking, cooperative wrapper process cleanup, Accessibility guidance, event-tap recovery, and immediate adapter disconnect wake-up.
- [Breaking] The source installer now places command-line tools in a runtime-only environment under Application Support instead of linking the repository `.venv`.
- [Delivery] Added packaged QSS, lockfile-reproducible production installs, sanitized current-format Codex fixtures, build reuse, paired Developer ID/notarization validation, and explicit ad-hoc prerelease labeling.

## 1.2.0 — 2026-07-19

- Retained terminal Codex cards until explicit removal, so completed green lights no longer disappear automatically.
- Added per-card `×` removal, a matching context-menu action, and confirmation-backed bulk cleanup for retained terminal tasks.
- Added running/retained grouping, compact task counts, and last-activity time to improve scanning.
- Restored a removed task automatically when a later verified Codex run is detected.

## 1.1.0 — 2026-07-19

- Added automatic monitoring for up to four recent verified Codex tasks, persistent task muting, stale-start protection, and duplicate-index cleanup.
- Added a larger, 5× status light for faster desktop scanning.
- Added selected-task-only local Codex discovery and completion-event precedence.
- Added English-first and Chinese documentation, open-source governance, and public repository metadata.

## 1.0.0 — 2026-07-17

- Added a frameless, transparent, draggable and resizable macOS floating panel with optional always-on-top behavior.
- Added a menu-bar entry, compact mode, remembered position and opacity, state animation, and completion/error notifications.
- Added a unified state machine, SQLite state history, YAML configuration, and secret redaction.
- Added a random-token localhost API, `aacc` CLI, and `aacc-run` lifecycle wrapper.
- Added Terminal.app, iTerm2, Codex App, and generic bundle-ID focus targets.
- Added F13–F20 global shortcuts, allowlisted keyboard injection, and macOS dictation triggering.
- Added Generic CLI, Codex CLI, Claude Code, Kimi Code, and Codex App adapter presets.
- Added automated tests, installation, recoverable uninstall, PyInstaller `.app` builds, a DMG release artifact, and documentation.
