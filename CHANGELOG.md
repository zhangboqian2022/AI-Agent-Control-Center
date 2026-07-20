# Changelog

[中文版本](CHANGELOG.zh-CN.md)

## 1.3.0-rc.2 — 2026-07-20

- [UX] Rebuilt task cards as a compact horizontal hierarchy with a large left status light, small agent badge, prominent task name, raised whole-run timer, and one-line activity summary.
- [UX] Made the floating window grow and shrink with visible tasks, capped it at 80% of the current screen's available height, and enabled internal scrolling only beyond that cap.
- [Stability] Changed Codex discovery to a five-second cadence and added fixed privacy-safe activity labels for analysis, code edits, tests, builds, inspection, searches, command execution, and completion.
- [Stability] Preserved one timer across short active/waiting turns, froze terminal total duration, and reset timing only when a terminal task starts a new run.
- [Stability] Recognized current Codex input, command, patch, and permission approval events; recovered cold-start run times beyond the activity tail with cached incremental metadata scans; and prioritized waiting tasks with other active work.
- [Privacy] Activity classification never copies prompt, response, command, credential, or file-content payloads into task messages, logs, or the panel.

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
