# Codex Active Auto-monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically show recent, verified running Codex tasks while allowing the user to suppress individual tasks and manually retain others.

**Architecture:** `CodexLocalDiscovery` will deduplicate sessions and expose active candidate IDs from safe local metadata. `CodexDiscoveryService` will merge manual, active-auto, and muted sets before registering tasks. `MainWindow` will persist manual/muted preferences and show auto-running state in the selector.

**Tech Stack:** Python 3.13, PySide6, psutil, pytest, QSettings.

## Global Constraints

- Read only local IDs, titles, timestamps, event labels, file metadata, and PID records.
- A stale `task_started` alone must never mean `RUNNING`.
- Auto-monitor no more than four active tasks; do not register inactive unselected tasks.
- Existing `codex_selected_tasks` values migrate to manual selections.

---

### Task 1: Make activity detection fresh and deduplicated

**Files:**
- Modify: `src/aacc/codex_discovery.py`
- Test: `tests/test_codex_discovery.py`

**Interfaces:**
- Produces: `active_session_ids() -> set[str]` and unique `catalog()` sessions.
- Consumes: session index, session event labels, file modification timestamps, and validated PIDs.

- [ ] Write failing tests for duplicate index IDs, stale `task_started`, and active-candidate limits.
- [ ] Run `uv run pytest -q tests/test_codex_discovery.py` and confirm the new tests fail.
- [ ] Deduplicate index records by most recent update, require fresh evidence for running events, and add active-candidate discovery.
- [ ] Re-run `uv run pytest -q tests/test_codex_discovery.py` and confirm all tests pass.

### Task 2: Merge auto, manual, and muted monitoring sets

**Files:**
- Modify: `src/aacc/discovery_service.py`
- Test: `tests/test_discovery_service.py`

**Interfaces:**
- Produces: `auto_active_ids() -> set[str]` and `set_monitoring_preferences(manual_ids, muted_ids)`.
- Consumes: `CodexLocalDiscovery.active_session_ids()`.

- [ ] Write failing tests proving active candidates are registered without manual selection and muted candidates are excluded.
- [ ] Run `uv run pytest -q tests/test_discovery_service.py` and confirm failure.
- [ ] Implement a locked snapshot of auto-active IDs and register only the merged monitored set.
- [ ] Re-run `uv run pytest -q tests/test_discovery_service.py` and confirm all tests pass.

### Task 3: Persist and explain automatic task choices

**Files:**
- Modify: `src/aacc/gui.py`, `src/aacc/app.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: service callbacks for monitor preferences and active auto IDs.
- Produces: selector labels for automatic tasks, a restore-automatic button, and persistent manual/muted preferences.

- [ ] Write failing GUI tests for an auto-running checked task, user suppression, and restore automatic detection.
- [ ] Run `uv run pytest -q tests/test_gui.py` and confirm failure.
- [ ] Implement the selector and preference callbacks without changing click-to-focus behavior.
- [ ] Re-run `uv run pytest -q tests/test_gui.py` and confirm all tests pass.

### Task 4: Update docs, verify, and package

**Files:**
- Modify: `README.md`, `README.zh-CN.md`, `docs/user-guide.md`, `docs/user-guide.en.md`, `CHANGELOG.md`, `CHANGELOG.zh-CN.md`

- [ ] Document automatic active-task monitoring, manual suppression, the four-task cap, and stale-event handling in both languages.
- [ ] Run full tests, Ruff, mypy, Markdown-link verification, app build, DMG verification, and an installed-app visual check.
- [ ] Commit the implementation and publish it to the existing public GitHub repository.
