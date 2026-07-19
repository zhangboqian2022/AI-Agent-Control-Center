# Changelog

[中文版本](CHANGELOG.zh-CN.md)

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
